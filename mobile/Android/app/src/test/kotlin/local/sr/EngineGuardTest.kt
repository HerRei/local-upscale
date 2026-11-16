package local.sr

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.IOException
import java.util.Collections
import java.util.concurrent.CancellationException
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/**
 * The Android engine's guards without a device: the in-use markers that catch NPU/GPU driver crashes, the
 * engine thread that keeps a hung driver from blocking the app, the launch-intent check, and the memory and
 * storage budgets. Pure JVM.
 */
class EngineGuardTest {
    private class MemoryStore : GuardStore {
        val map = LinkedHashMap<String, String>()
        var writes = 0
        override fun get(key: String): String? = map[key]
        override fun keys(): Collection<String> = map.keys.toList()
        override fun write(put: Map<String, String>, remove: Collection<String>) {
            writes++
            for (k in remove) map.remove(k)
            map.putAll(put)
        }
    }

    private val key = "cache-key-best"
    private val marker = UnitGuard.inFlightKey("gpu", key)

    private fun waitUntil(timeoutMs: Long = 3_000, condition: () -> Boolean) {
        val end = System.currentTimeMillis() + timeoutMs
        while (!condition()) {
            if (System.currentTimeMillis() > end) fail("condition not reached within $timeoutMs ms")
            Thread.sleep(10)
        }
    }

    // MARK: UnitGuard

    @Test
    fun beginWritesTheMarkerAndEndRemovesIt() {
        val store = MemoryStore()
        val guard = UnitGuard(store, pid = 100) { null }
        guard.begin(key, "gpu")
        assertEquals("100", store.map[marker])
        guard.end(key, "gpu")
        assertNull(store.map[marker])
        assertEquals(2, store.writes)
    }

    @Test
    fun nestedUseOnlyWritesOnTheFirstBeginAndTheLastEnd() {
        val store = MemoryStore()
        val guard = UnitGuard(store, pid = 100) { null }
        guard.begin(key, "gpu") // the job
        guard.inUse(key, "gpu") { } // a tile
        guard.inUse(key, "gpu") { } // another tile
        assertEquals("job still holds the marker", "100", store.map[marker])
        assertEquals(1, store.writes)
        guard.end(key, "gpu")
        assertNull(store.map[marker])
        assertEquals(2, store.writes)
    }

    @Test
    fun aMarkerLeftByANativeCrashMarksThatUnitFailedForThatKeyOnly() {
        val store = MemoryStore()
        store.map[marker] = "42"
        val guard = UnitGuard(store, pid = 100) { pid -> if (pid == 42) UnitGuard.REASON_CRASH_NATIVE else null }
        assertEquals(setOf("gpu"), guard.failedUnits(key))
        assertTrue(guard.failedUnits("other-model").isEmpty())
        assertNull("leftover marker must be cleared", store.map[marker])
        assertEquals(1, guard.recoveredReport.size)
    }

    @Test
    fun aMarkerLeftByALowMemoryKillOrTheUserIsNotBlamed() {
        for (reason in listOf(UnitGuard.REASON_LOW_MEMORY, UnitGuard.REASON_USER_REQUESTED, UnitGuard.REASON_CRASH)) {
            val store = MemoryStore()
            store.map[marker] = "42"
            val guard = UnitGuard(store, pid = 100) { reason }
            assertTrue("reason $reason", guard.failedUnits(key).isEmpty())
            assertNull(store.map[marker])
        }
    }

    @Test
    fun anUnknownExitReasonBlamesTheUnit() {
        // API 29 has no exit reasons; a crash loop is worse than a slower CPU run.
        val store = MemoryStore()
        store.map[marker] = "42"
        val guard = UnitGuard(store, pid = 100) { null }
        assertEquals(setOf("gpu"), guard.failedUnits(key))
    }

    @Test
    fun theNextProcessBlamesAUnitStillInUseWhenThisOneDied() {
        val store = MemoryStore()
        val first = UnitGuard(store, pid = 100) { null }
        first.begin(key, "npu")
        // This process recovered before writing, so its own marker is not a leftover for it.
        assertTrue(first.failedUnits(key).isEmpty())
        // The process dies here; the next one starts with the same storage.
        val second = UnitGuard(store, pid = 200) { pid -> if (pid == 100) UnitGuard.REASON_CRASH_NATIVE else null }
        assertEquals(setOf("npu"), second.failedUnits(key))
        assertNull(store.map[UnitGuard.inFlightKey("npu", key)])
    }

    @Test
    fun markFailedAddsToTheUnitsAlreadyFailed() {
        val store = MemoryStore()
        val guard = UnitGuard(store, pid = 100) { null }
        guard.markFailed(key, "npu")
        guard.markFailed(key, "gpu")
        guard.markFailed(key, "gpu")
        assertEquals(setOf("gpu", "npu"), guard.failedUnits(key))
        assertEquals("gpu,npu", store.map[UnitGuard.failedKey(key)])
        // Survives into the next process.
        assertEquals(setOf("gpu", "npu"), UnitGuard(store, pid = 200) { null }.failedUnits(key))
    }

    @Test
    fun recoveryMergesWithUnitsThatFailedEarlier() {
        val store = MemoryStore()
        store.map[UnitGuard.failedKey(key)] = "npu"
        store.map[marker] = "42"
        val guard = UnitGuard(store, pid = 100) { UnitGuard.REASON_CRASH_NATIVE }
        assertEquals(setOf("gpu", "npu"), guard.failedUnits(key))
    }

    @Test
    fun blamesOnlyReasonsAUnitCanCause() {
        assertTrue(UnitGuard.blames(null))
        assertTrue(UnitGuard.blames(UnitGuard.REASON_UNKNOWN))
        assertTrue(UnitGuard.blames(UnitGuard.REASON_CRASH_NATIVE))
        assertTrue(UnitGuard.blames(UnitGuard.REASON_ANR))
        assertTrue(UnitGuard.blames(UnitGuard.REASON_SIGNALED))
        assertFalse(UnitGuard.blames(UnitGuard.REASON_CRASH))
        assertFalse(UnitGuard.blames(UnitGuard.REASON_LOW_MEMORY))
        assertFalse(UnitGuard.blames(UnitGuard.REASON_USER_REQUESTED))
        assertFalse(UnitGuard.blames(16)) // REASON_PACKAGE_UPDATED
        assertEquals(setOf("gpu", "npu"), UnitGuard.parseUnits(" gpu, npu ,,"))
        assertTrue(UnitGuard.parseUnits(null).isEmpty())
    }

    // MARK: EngineThread

    @Test
    fun callReturnsTheValueAndRethrowsFailuresOnTheCaller() {
        val thread = EngineThread("test-engine")
        try {
            assertEquals(42, thread.call(5_000, isCancelled = { false }) { 42 })
            try {
                thread.call<Int>(5_000, isCancelled = { false }) { throw OutOfMemoryError("heap") }
                fail("OutOfMemoryError expected")
            } catch (e: OutOfMemoryError) {
                assertEquals("heap", e.message)
            }
            try {
                thread.call<Int>(5_000, isCancelled = { false }) { throw IllegalStateException("driver") }
                fail("exception expected")
            } catch (e: IllegalStateException) {
                assertEquals("driver", e.message)
            }
            assertFalse(thread.isAbandoned)
        } finally {
            thread.retire {}
        }
    }

    @Test
    fun aHungCallTimesOutAndItsLateResultIsDiscarded() {
        val thread = EngineThread("test-engine")
        val unblock = CountDownLatch(1)
        val discarded = CountDownLatch(1)
        val started = System.nanoTime()
        try {
            thread.call(300, isCancelled = { false }, onDiscard = { _: String -> discarded.countDown() }) {
                unblock.await()
                "late"
            }
            fail("UnitHangException expected")
        } catch (e: UnitHangException) {
            // expected
        }
        val ms = (System.nanoTime() - started) / 1_000_000
        assertTrue("gave up after $ms ms", ms in 300L..2_000L)
        assertTrue(thread.isAbandoned)
        // The engine is not used again while its call may still be running.
        try {
            thread.call(5_000, isCancelled = { false }) { "again" }
            fail("an abandoned engine must refuse new calls")
        } catch (e: UnitHangException) {
            // expected
        }
        unblock.countDown()
        assertTrue("late result goes to onDiscard", discarded.await(3, TimeUnit.SECONDS))
        thread.retire {}
    }

    @Test
    fun retireClosesOnlyAfterTheHungCallReturns() {
        val thread = EngineThread("test-engine")
        val unblock = CountDownLatch(1)
        val closed = CountDownLatch(1)
        val order = Collections.synchronizedList(ArrayList<String>())
        try {
            thread.call(200, isCancelled = { false }) { unblock.await(); order.add("call") }
            fail("UnitHangException expected")
        } catch (e: UnitHangException) {
            // expected
        }
        thread.retire { order.add("close"); closed.countDown() }
        Thread.sleep(200)
        assertTrue("must not close while the call runs", order.isEmpty())
        unblock.countDown()
        assertTrue(closed.await(3, TimeUnit.SECONDS))
        assertEquals(listOf("call", "close"), order.toList())
    }

    @Test
    fun cancelReturnsQuicklyWhileTheNativeCallIsStuck() {
        val thread = EngineThread("test-engine")
        val unblock = CountDownLatch(1)
        val cancelled = AtomicBoolean(false)
        Thread { Thread.sleep(150); cancelled.set(true) }.start()
        val started = System.nanoTime()
        try {
            thread.call(60_000, isCancelled = { cancelled.get() }, cancelGraceMs = 100) { unblock.await(); 1 }
            fail("CancellationException expected")
        } catch (e: CancellationException) {
            // expected
        }
        val ms = (System.nanoTime() - started) / 1_000_000
        assertTrue("cancel took $ms ms", ms < 2_000)
        assertTrue(thread.isAbandoned)
        unblock.countDown()
        thread.retire {}
    }

    @Test
    fun cancelWaitsForATileThatFinishesWithinTheGraceTime() {
        val thread = EngineThread("test-engine")
        try {
            val value = thread.call(60_000, isCancelled = { true }, cancelGraceMs = 2_000) { Thread.sleep(300); "tile" }
            assertEquals("tile", value)
            assertFalse("the engine stays usable", thread.isAbandoned)
        } finally {
            thread.retire {}
        }
    }

    @Test
    fun aTileThatHangsKeepsTheUnitMarkedAfterTheJobEnds() {
        val store = MemoryStore()
        val guard = UnitGuard(store, pid = 100) { null }
        val thread = EngineThread("test-engine")
        val unblock = CountDownLatch(1)
        guard.begin(key, "gpu") // the job
        try {
            thread.call(200, isCancelled = { false }) { guard.inUse(key, "gpu") { unblock.await() } }
            fail("UnitHangException expected")
        } catch (e: UnitHangException) {
            // expected
        }
        guard.end(key, "gpu") // the job gives up
        assertEquals("the hung tile still holds the marker", "100", store.map[marker])
        unblock.countDown()
        waitUntil { store.map[marker] == null }
        thread.retire {}
    }

    // MARK: launch intent

    @Test
    fun onlyAFreshLaunchHandlesItsShareIntent() {
        val launchedFromHistory = 0x00100000 // Intent.FLAG_ACTIVITY_LAUNCHED_FROM_HISTORY
        val newTaskAndRead = 0x10000000 or 0x00000001 // FLAG_ACTIVITY_NEW_TASK | FLAG_GRANT_READ_URI_PERMISSION
        assertTrue(ShareLaunchPolicy.isNewShare(hasSavedState = false, intentFlags = 0))
        assertTrue(ShareLaunchPolicy.isNewShare(hasSavedState = false, intentFlags = newTaskAndRead))
        assertFalse("restored after process death", ShareLaunchPolicy.isNewShare(hasSavedState = true, intentFlags = newTaskAndRead))
        assertFalse("relaunched from Recents", ShareLaunchPolicy.isNewShare(hasSavedState = false, intentFlags = launchedFromHistory or newTaskAndRead))
    }

    // MARK: budgets

    @Test
    fun fullScaleIsMeasuredAgainstSystemMemoryNotTheJavaHeap() {
        val mb = 1024L * 1024
        val output = 4000L * 3000 * 16 // 12 MP at 4x = 192 MP
        assertEquals(1_920_000_000L, FullScaleBudget.nativeBytesNeeded(output))
        // A phone with 4 GB available and a 512 MB Java heap: possible.
        assertTrue(FullScaleBudget.fits(output, availMem = 4096 * mb, lowMemoryThreshold = 300 * mb, lowMemory = false, javaHeapFree = 400 * mb))
        // 2 GB available minus the low-memory threshold is less than 2.5x the 768 MB output.
        assertFalse(FullScaleBudget.fits(output, availMem = 2048 * mb, lowMemoryThreshold = 300 * mb, lowMemory = false, javaHeapFree = 400 * mb))
        assertFalse(FullScaleBudget.fits(output, availMem = 8192 * mb, lowMemoryThreshold = 300 * mb, lowMemory = true, javaHeapFree = 400 * mb))
        assertFalse("tile arrays need Java heap", FullScaleBudget.fits(output, availMem = 8192 * mb, lowMemoryThreshold = 300 * mb, lowMemory = false, javaHeapFree = 32 * mb))
    }

    @Test
    fun storageBudgetAndFullDiskDetection() {
        val mb = 1024L * 1024
        assertEquals(48_771_072L + 16 * mb, StorageBudget.bytesNeeded(48_771_072L))
        assertTrue(StorageBudget.notEnoughMessage(65 * mb).contains("65 MB"))
        assertTrue(StorageBudget.isOutOfSpace(IOException("write failed: ENOSPC (No space left on device)"), freeBytes = Long.MAX_VALUE))
        assertTrue(StorageBudget.isOutOfSpace(UpscaleException("save failed", IOException("No space left on device")), freeBytes = Long.MAX_VALUE))
        // Bitmap.compress hides the IOException: decide by the free space left.
        assertTrue(StorageBudget.isOutOfSpace(UpscaleException("JPEG encoding failed"), freeBytes = 1 * mb))
        assertFalse(StorageBudget.isOutOfSpace(UpscaleException("JPEG encoding failed"), freeBytes = 10_000 * mb))
    }
}
