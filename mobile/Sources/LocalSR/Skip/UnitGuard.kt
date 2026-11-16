package local.sr

import android.app.ActivityManager
import android.content.Context
import android.content.SharedPreferences
import android.os.Build
import android.util.Log
import kotlinx.coroutines.CancellationException
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference

/** Storage for [UnitGuard]. [write] must be on disk when it returns: the process may die right after it. */
interface GuardStore {
    fun get(key: String): String?
    fun keys(): Collection<String>
    fun write(put: Map<String, String>, remove: Collection<String>)
}

/**
 * Notices an NPU or GPU driver that takes the whole process down. A native crash kills the app before any catch
 * runs, so without this the device check (or the cached verdict) would pick the same unit again on every start.
 *
 * Before native code runs on an NPU/GPU, [begin] writes an "in use" entry synchronously; [end] removes it.
 * The first use in a new process finds the entries a dead process left behind, asks Android why that process
 * ended (API 30+) and records the unit as failed for that cache key (device, OS, app build, model) unless the
 * process was clearly stopped for another reason (low memory, the user, an update). The device check and the
 * cached verdict then skip the unit until the cache key changes. [markFailed] records a hang the same way.
 *
 * Nested begin/end pairs for the same unit and key only write on the first begin and the last end, so a job can
 * hold one entry while every tile also takes one (a tile that never returns keeps the entry alive).
 */
class UnitGuard(private val store: GuardStore, private val pid: Int, private val exitReason: (Int) -> Int?) {
    private val lock = Any()
    private val active = HashMap<String, Int>()
    private var recovered = false

    /** What recovery found at the first use in this process, for logs and tests. */
    var recoveredReport: List<String> = emptyList()
        private set

    fun begin(cacheKey: String, unit: String) {
        synchronized(lock) {
            recoverLocked()
            val key = inFlightKey(unit, cacheKey)
            val n = active[key] ?: 0
            active[key] = n + 1
            if (n == 0) store.write(mapOf(key to pid.toString()), emptyList())
        }
    }

    fun end(cacheKey: String, unit: String) {
        synchronized(lock) {
            val key = inFlightKey(unit, cacheKey)
            val n = active[key] ?: return
            if (n > 1) {
                active[key] = n - 1
                return
            }
            active.remove(key)
            store.write(emptyMap(), listOf(key))
        }
    }

    inline fun <T> inUse(cacheKey: String, unit: String, block: () -> T): T {
        begin(cacheKey, unit)
        try {
            return block()
        } finally {
            end(cacheKey, unit)
        }
    }

    /** Records [unit] as unusable for [cacheKey] (it hung or crashed). */
    fun markFailed(cacheKey: String, unit: String) {
        synchronized(lock) {
            recoverLocked()
            val units = parseUnits(store.get(failedKey(cacheKey))) + unit
            store.write(mapOf(failedKey(cacheKey) to units.sorted().joinToString(",")), emptyList())
        }
    }

    fun failedUnits(cacheKey: String): Set<String> = synchronized(lock) {
        recoverLocked()
        parseUnits(store.get(failedKey(cacheKey)))
    }

    private fun recoverLocked() {
        if (recovered) return
        recovered = true
        val leftovers = store.keys().filter { it.startsWith(IN_FLIGHT_PREFIX) }
        if (leftovers.isEmpty()) return
        val failed = HashMap<String, MutableSet<String>>()
        val report = ArrayList<String>()
        for (key in leftovers) {
            val rest = key.substring(IN_FLIGHT_PREFIX.length)
            val bar = rest.indexOf('|')
            if (bar <= 0) continue
            val unit = rest.substring(0, bar)
            val cacheKey = rest.substring(bar + 1)
            val deadPid = store.get(key)?.toIntOrNull()
            val reason = if (deadPid == null) null else exitReason(deadPid)
            val blamed = blames(reason)
            if (blamed) failed.getOrPut(cacheKey) { parseUnits(store.get(failedKey(cacheKey))).toMutableSet() }.add(unit)
            val line = "${unit.uppercase()} was in use when the app last stopped (exit reason ${reason ?: "unknown"}): " +
                if (blamed) "not used again for this model until the app or system is updated" else "not its fault, keeping it"
            report.add(line)
            Log.w(TAG, line)
        }
        store.write(failed.entries.associate { failedKey(it.key) to it.value.sorted().joinToString(",") }, leftovers)
        recoveredReport = report
    }

    companion object {
        private const val TAG = "LocalSR"
        const val IN_FLIGHT_PREFIX = "inflight|"
        const val FAILED_PREFIX = "failed|"

        // android.app.ApplicationExitInfo reasons (API 30). Copied so that API 29 never loads that class.
        const val REASON_UNKNOWN = 0
        const val REASON_SIGNALED = 2
        const val REASON_LOW_MEMORY = 3
        const val REASON_CRASH = 4
        const val REASON_CRASH_NATIVE = 5
        const val REASON_ANR = 6
        const val REASON_USER_REQUESTED = 10

        fun inFlightKey(unit: String, cacheKey: String): String = "$IN_FLIGHT_PREFIX$unit|$cacheKey"
        fun failedKey(cacheKey: String): String = "$FAILED_PREFIX$cacheKey"

        fun parseUnits(raw: String?): Set<String> =
            raw?.split(',')?.map { it.trim() }?.filter { it.isNotEmpty() }?.toSet() ?: emptySet()

        /**
         * Whether a process that died with this ApplicationExitInfo reason while a unit was in use counts against
         * that unit. Unknown (no record, or API 29) counts: a crash loop is far worse than a slower CPU run.
         * A Java crash does not: our code catches every Throwable from the engine, so it came from elsewhere.
         */
        fun blames(reason: Int?): Boolean = when (reason) {
            null, REASON_UNKNOWN, REASON_CRASH_NATIVE, REASON_ANR, REASON_SIGNALED -> true
            else -> false
        }

        @Volatile private var instance: UnitGuard? = null

        /** One guard per process (its in-memory counts must see every engine of the app). */
        fun shared(context: Context): UnitGuard {
            instance?.let { return it }
            synchronized(this) {
                instance?.let { return it }
                val app = context.applicationContext
                val prefs = app.getSharedPreferences(DeviceChecker.PREFS_NAME, Context.MODE_PRIVATE)
                val guard = UnitGuard(PrefsGuardStore(prefs), android.os.Process.myPid()) { deadPid -> lastExitReason(app, deadPid) }
                instance = guard
                return guard
            }
        }

        private fun lastExitReason(context: Context, pid: Int): Int? {
            if (Build.VERSION.SDK_INT < 30) return null
            return try {
                val am = context.getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
                am.getHistoricalProcessExitReasons(context.packageName, pid, 1).firstOrNull()?.reason
            } catch (_: Throwable) {
                null
            }
        }
    }
}

private class PrefsGuardStore(private val prefs: SharedPreferences) : GuardStore {
    override fun get(key: String): String? = try { prefs.getString(key, null) } catch (_: ClassCastException) { null }
    override fun keys(): Collection<String> = prefs.all.keys.toList()
    override fun write(put: Map<String, String>, remove: Collection<String>) {
        val editor = prefs.edit()
        for (key in remove) editor.remove(key)
        for ((key, value) in put) editor.putString(key, value)
        // commit(), not apply(): the entry must be on disk before native code that may kill the process runs.
        if (!editor.commit()) Log.w("LocalSR", "cannot write the accelerator guard")
    }
}

/** A native NPU/GPU call that did not return in time. It keeps running on its [EngineThread], which is retired. */
class UnitHangException(message: String) : Exception(message)

/**
 * The one thread every native call of one NPU/GPU engine runs on: load, tiles and close (GPU drivers may also
 * expect a single thread). The caller waits with a time limit and watches for cancellation, so a driver that hangs
 * can no longer block the job, the upscaler's lock or Cancel. A call that is given up on keeps running; the engine
 * must then not be used again ([isAbandoned]), and [retire] closes it only after that call returns, if ever.
 */
class EngineThread(private val name: String) {
    private val executor = Executors.newSingleThreadExecutor { r -> Thread(r, name).apply { isDaemon = true } }
    private val abandoned = AtomicBoolean(false)
    private val retired = AtomicBoolean(false)

    /** True after a call was given up on while it was still running. */
    val isAbandoned: Boolean get() = abandoned.get()

    /**
     * Runs [block] on this thread and waits for it. Throws [UnitHangException] after [timeoutMs], or
     * CancellationException when [isCancelled] turns true and the call does not finish within [cancelGraceMs].
     * A result that arrives after the caller gave up is passed to [onDiscard] (on this thread).
     */
    fun <T> call(timeoutMs: Long, isCancelled: () -> Boolean, cancelGraceMs: Long = CANCEL_GRACE_MS, onDiscard: (T) -> Unit = {}, block: () -> T): T {
        if (abandoned.get()) throw UnitHangException("$name: an earlier call never returned")
        val state = AtomicInteger(RUNNING)
        val result = AtomicReference<Result<T>?>(null)
        val done = CountDownLatch(1)
        executor.execute {
            val r = runCatching(block)
            result.set(r)
            if (state.compareAndSet(RUNNING, DONE)) {
                done.countDown()
            } else {
                r.getOrNull()?.let { value -> runCatching { onDiscard(value) } }
            }
        }
        val started = System.nanoTime()
        while (true) {
            if (done.await(POLL_MS, TimeUnit.MILLISECONDS)) return result.get()!!.getOrThrow()
            if (isCancelled()) {
                if (done.await(cancelGraceMs, TimeUnit.MILLISECONDS)) return result.get()!!.getOrThrow()
                giveUp(state, result)?.let { return it.getOrThrow() }
                throw CancellationException("$name: cancelled while a native call was still running")
            }
            if ((System.nanoTime() - started) / 1_000_000 >= timeoutMs) {
                giveUp(state, result)?.let { return it.getOrThrow() }
                throw UnitHangException("$name: no answer after ${timeoutMs / 1000} s")
            }
        }
    }

    /** Marks the running call as abandoned, or returns its result when it finished in the meantime. */
    private fun <T> giveUp(state: AtomicInteger, result: AtomicReference<Result<T>?>): Result<T>? {
        if (state.compareAndSet(RUNNING, ABANDONED)) {
            abandoned.set(true)
            return null
        }
        return result.get()
    }

    /** Runs [last] (closing the engine) after every queued call, then ends the thread. */
    fun retire(last: () -> Unit) {
        if (!retired.compareAndSet(false, true)) return
        try {
            executor.execute { runCatching(last) }
        } catch (_: Throwable) {
        }
        executor.shutdown()
    }

    companion object {
        private const val RUNNING = 0
        private const val DONE = 1
        private const val ABANDONED = 2
        private const val POLL_MS = 100L
        /** Cancel waits this long for the running tile, so a normal cancel keeps the engine usable. */
        const val CANCEL_GRACE_MS = 2_000L
    }
}
