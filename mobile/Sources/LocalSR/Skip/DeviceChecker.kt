package local.sr

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.os.Build
import android.util.Log
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import local.srcore.ComputeUnit
import local.srcore.DeviceCheck
import local.srcore.DeviceMeasurement
import local.srcore.DeviceVerdict
import local.srcore.ModelKey
import local.srcore.TilePlanner

/** What the device check measured, for logs and the About screen. */
class DeviceCheckReport(val verdict: DeviceVerdict, val lines: List<String>, val cacheable: Boolean) {
    override fun toString(): String = "${verdict.unit.rawValue}: ${verdict.reason}\n" + lines.joinToString("\n")
}

/**
 * DESIGN.md "Device check": the probe tile runs on the CPU (reference), the NPU and the GPU, each on its
 * own so a failure is explicit. A unit passes with PSNR >= 45 dB vs CPU, no NaN/Inf, not constant, and
 * the 4x box-downsampled output >= 26 dB vs the input. NPU wins unless the GPU passes and is >= 1.3x faster.
 * The verdict is cached in SharedPreferences under (device, OS, app build, model).
 * A unit that crashed or hung the app before ([UnitGuard]) is skipped, and a cached verdict naming it is ignored.
 */
class DeviceChecker(private val context: Context, private val gpuTimeoutMs: Long = GPU_TIMEOUT_MS) {
    private val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    /** Marks NPU/GPU use around native calls; shared by every checker and upscaler in the process. */
    val guard: UnitGuard = UnitGuard.shared(context)
    private val build: String by lazy { appBuild() }

    fun cacheKey(model: ModelKey): String {
        val soc = if (Build.VERSION.SDK_INT >= 31) "${Build.SOC_MANUFACTURER} ${Build.SOC_MODEL}" else Build.HARDWARE
        val os = "Android ${Build.VERSION.RELEASE} (${Build.VERSION.SDK_INT}) ${Build.FINGERPRINT.hashCode()}"
        return DeviceCheck.cacheKey(
            deviceModel = "${Build.MANUFACTURER} ${Build.MODEL} $soc",
            osVersion = os,
            appBuild = build,
            modelKey = model,
        )
    }

    private fun appBuild(): String = try {
        val info = context.packageManager.getPackageInfo(context.packageName, 0)
        if (Build.VERSION.SDK_INT >= 28) info.longVersionCode.toString() else @Suppress("DEPRECATION") info.versionCode.toString()
    } catch (e: Exception) {
        "0"
    }

    fun cached(model: ModelKey): DeviceVerdict? {
        val raw = prefs.getString(cacheKey(model), null) ?: return null
        if (raw.contains("its output did not pass validation")) return null
        val bar = raw.indexOf('|')
        if (bar < 0) return null
        val unit = ComputeUnit.init(rawValue = raw.substring(0, bar)) ?: return null
        // The unit took the app down (or hung) since this verdict was stored: measure again without it.
        if (unit != ComputeUnit.cpu && failedUnits(model).contains(unit)) return null
        return DeviceVerdict(unit = unit, reason = raw.substring(bar + 1))
    }

    /** NPU/GPU units that crashed or hung the app for [model] on this device, OS and app build. */
    fun failedUnits(model: ModelKey): Set<ComputeUnit> =
        guard.failedUnits(cacheKey(model)).mapNotNull { ComputeUnit.init(rawValue = it) }.toSet()

    /** Records that [unit] hung for [model]: the check skips it from now on (until the cache key changes). */
    fun markFailed(model: ModelKey, unit: ComputeUnit, why: String) {
        Log.w(TAG, "${unit.rawValue} $why for ${model.rawValue}; not using it again for this model")
        guard.markFailed(cacheKey(model), unit.rawValue)
    }

    fun store(model: ModelKey, verdict: DeviceVerdict) {
        prefs.edit().putString(cacheKey(model), "${verdict.unit.rawValue}|${verdict.reason}").apply()
    }

    fun forget(model: ModelKey) {
        prefs.edit().remove(cacheKey(model)).apply()
    }

    /** Loads the bundled probe tile (384x384) as NHWC floats. */
    private fun probeInput(): FloatArray {
        val bmp = context.assets.open("probe.png").use { BitmapFactory.decodeStream(it, null, BitmapFactory.Options().apply { inPreferredConfig = Bitmap.Config.ARGB_8888 }) }
            ?: throw UpscaleException("probe.png cannot be decoded")
        if (bmp.width != PixelKernels.WINDOW || bmp.height != PixelKernels.WINDOW) throw UpscaleException("probe.png must be 384x384, is ${bmp.width}x${bmp.height}")
        val px = IntArray(bmp.width * bmp.height)
        bmp.getPixels(px, 0, bmp.width, 0, 0, bmp.width, bmp.height)
        bmp.recycle()
        val tile = TilePlanner.plan(width = PixelKernels.WINDOW, height = PixelKernels.WINDOW)[0]
        val out = FloatArray(PixelKernels.WINDOW * PixelKernels.WINDOW * 3)
        PixelKernels.extractWindow(px, PixelKernels.WINDOW, PixelKernels.WINDOW, tile, out)
        return out
    }

    private class Run(val engineMs: Double, val loadMs: Double, val output: FloatArray)

    /** Loads [unit], warms up, then times [timedRuns] runs (median). Returns the last timed output. */
    private fun runUnit(model: ModelKey, unit: ComputeUnit, input: FloatArray, timedRuns: Int, engine: LiteRtEngine = LiteRtEngine.load(context, model, unit)): Run {
        try {
            engine.invoke(input) // warm-up (GPU compiles shaders, XNNPACK packs weights)
            val times = ArrayList<Double>()
            for (i in 0 until timedRuns) {
                val t0 = System.nanoTime()
                engine.invoke(input)
                times.add((System.nanoTime() - t0) / 1e6)
            }
            times.sort()
            return Run(times[times.size / 2], engine.loadMs, engine.readOutput())
        } finally {
            engine.close()
        }
    }

    private fun measure(run: Run, reference: FloatArray?, input: FloatArray): DeviceMeasurement {
        val out = run.output
        val finite = !PixelKernels.hasNonFinite(out)
        val down = PixelKernels.downsample4x(out, PixelKernels.TILE_OUT, PixelKernels.TILE_OUT)
        val psnrCpu = if (reference == null) 99.0 else PixelKernels.psnr(reference, down)
        val psnrDown = PixelKernels.psnr(down, input)
        return DeviceMeasurement(
            loadMs = run.loadMs,
            tileMs = run.engineMs,
            psnrVsCPU = psnrCpu,
            psnrDownsampleVsInput = psnrDown,
            hasNaN = !finite,
            isConstant = PixelKernels.isConstant(out),
            placementAllNPU = true, // Android has no placement report; the quality + speed probe decides
            timedOut = false,
        )
    }

    private fun failureReason(t: Throwable): String =
        if (t is OutOfMemoryError) "not enough memory" else t.message ?: t.javaClass.simpleName

    /** Same thresholds as the core's DeviceCheck (kept private there). */
    private fun passes(m: DeviceMeasurement?): Boolean =
        m != null && !m.timedOut && !m.hasNaN && !m.isConstant && m.psnrVsCPU >= 45 && m.psnrDownsampleVsInput >= 26 && m.tileMs > 0 && m.tileMs.isFinite()

    private fun describe(name: String, m: DeviceMeasurement): String =
        "$name: load %.0f ms, %.1f ms/tile, PSNR vs CPU (4x box-downsampled) %.1f dB, downsample vs input %.1f dB, nan=%s constant=%s".format(m.loadMs, m.tileMs, m.psnrVsCPU, m.psnrDownsampleVsInput, m.hasNaN, m.isConstant)

    /**
     * Runs the full check for [model]. Blocking work happens on Dispatchers.Default. Does not touch the cache
     * (the caller stores the verdict if [DeviceCheckReport.cacheable]).
     */
    suspend fun run(model: ModelKey): DeviceCheckReport = withContext(Dispatchers.Default) {
        val lines = ArrayList<String>()
        val key = cacheKey(model)
        val failed = failedUnits(model)
        val input = try { probeInput() } catch (e: OutOfMemoryError) {
            throw UpscaleException("Device check failed: not enough memory", e)
        }

        // CPU reference.
        var cpuMeasure: DeviceMeasurement? = null
        var reference: FloatArray? = null
        var cpuFailure: String? = null
        try {
            val r = runUnit(model, ComputeUnit.cpu, input, timedRuns = 1)
            cpuMeasure = measure(r, null, input)
            reference = PixelKernels.downsample4x(r.output, PixelKernels.TILE_OUT, PixelKernels.TILE_OUT)
            lines.add(describe("CPU", cpuMeasure))
        } catch (t: Throwable) {
            Log.e(TAG, "CPU probe failed", t)
            cpuFailure = failureReason(t)
            lines.add("CPU: failed: $cpuFailure")
        }
        currentCoroutineContext().ensureActive()

        // NPU alone, with the 15 s load watchdog.
        var npuMeasure: DeviceMeasurement? = null
        var cacheable = true
        if (reference != null && ComputeUnit.npu in failed) {
            lines.add("NPU: skipped (it crashed or hung the app before)")
        } else if (reference != null) {
            try {
                val loaded = withLoadWatchdog(WATCHDOG_MS, key, ComputeUnit.npu, onDiscard = { it.close() }) { LiteRtEngine.load(context, model, ComputeUnit.npu) }
                val r = loaded?.let { engine ->
                    withLoadWatchdog(gpuTimeoutMs, key, ComputeUnit.npu) { runUnit(model, ComputeUnit.npu, input, timedRuns = 3, engine = engine) }
                        ?: throw UpscaleException("warm-up + timing exceeded $gpuTimeoutMs ms")
                }
                if (r == null) {
                    npuMeasure = DeviceMeasurement(loadMs = WATCHDOG_MS.toDouble(), tileMs = 0.0, psnrVsCPU = 0.0, psnrDownsampleVsInput = 0.0, timedOut = true)
                    cacheable = false // "use the GPU for this session and retry later"
                    lines.add("NPU: load exceeded $WATCHDOG_MS ms (will retry on a later start)")
                } else {
                    npuMeasure = measure(r, reference, input)
                    lines.add(describe("NPU", npuMeasure))
                }
            } catch (t: Throwable) {
                Log.i(TAG, "NPU probe failed: ${failureReason(t)}")
                lines.add("NPU: failed: ${failureReason(t)}")
                if (t is OutOfMemoryError) cacheable = false // memory is short right now; the NPU may be fine later
            }
        } else {
            lines.add("NPU: skipped (no CPU reference)")
        }

        // GPU alone.
        var gpuMeasure: DeviceMeasurement? = null
        if (reference != null && ComputeUnit.gpu in failed) {
            lines.add("GPU: skipped (it crashed or hung the app before)")
        } else if (reference != null) {
            try {
                val r = withLoadWatchdog(gpuTimeoutMs, key, ComputeUnit.gpu) { runUnit(model, ComputeUnit.gpu, input, timedRuns = 3) }
                if (r == null) {
                    lines.add("GPU: load + warm-up + timing exceeded $gpuTimeoutMs ms (treated as failed)")
                } else {
                    gpuMeasure = measure(r, reference, input)
                    lines.add(describe("GPU", gpuMeasure))
                }
            } catch (t: Throwable) {
                Log.w(TAG, "GPU probe failed: ${failureReason(t)}")
                lines.add("GPU: failed: ${failureReason(t)}")
                if (t is OutOfMemoryError) cacheable = false // memory is short right now; the GPU may be fine later
            }
        } else {
            lines.add("GPU: skipped (no CPU reference)")
        }
        currentCoroutineContext().ensureActive()

        var verdict = DeviceCheck.decide(cpu = cpuMeasure, gpu = gpuMeasure, npu = npuMeasure)
            ?: throw UpscaleException(if (cpuFailure == "not enough memory") "Device check failed: not enough memory" else "This device produced invalid output; upscaling is disabled.")
        // An accelerator that passes but is not faster than the CPU is pointless (software GL on an emulator, a slow iGPU).
        val cpuOk = passes(cpuMeasure)
        val chosen = when (verdict.unit) { ComputeUnit.npu -> npuMeasure; ComputeUnit.gpu -> gpuMeasure; ComputeUnit.cpu -> null }
        if (cpuOk && chosen != null && chosen.tileMs >= cpuMeasure!!.tileMs) {
            verdict = DeviceVerdict(unit = ComputeUnit.cpu, reason = "${verdict.unit.rawValue.uppercase()} passed but is not faster than the CPU (%.0f vs %.0f ms/tile); using CPU.".format(chosen.tileMs, cpuMeasure.tileMs))
        }
        val report = DeviceCheckReport(verdict, lines, cacheable)
        Log.i(TAG, "device check for ${model.rawValue}: $report")
        report
    }

    /**
     * Runs [block] on a worker thread and gives up waiting after [timeoutMs] (a native call cannot be interrupted;
     * its result is discarded). [unit] stays marked as in use until [block] really returns, also after the watchdog
     * gave up, so a probe that later takes the process down is still blamed on the next start.
     */
    private suspend fun <T> withLoadWatchdog(timeoutMs: Long, guardKey: String, unit: ComputeUnit, onDiscard: (T) -> Unit = {}, block: () -> T): T? {
        val result = CompletableDeferred<Result<T>>()
        val thread = Thread({
            // Background priority: if this probe hangs (software GL on an emulator) it must not starve the real work.
            android.os.Process.setThreadPriority(android.os.Process.THREAD_PRIORITY_BACKGROUND)
            val value = guard.inUse(guardKey, unit.rawValue) { runCatching(block) }
            if (!result.complete(value)) value.getOrNull()?.let(onDiscard)
        }, "localsr-probe")
        thread.start()
        val r = withTimeoutOrNull(timeoutMs) { result.await() } ?: run { result.cancel(); return null }
        return r.getOrThrow()
    }

    companion object {
        private const val TAG = "LocalSR"
        const val PREFS_NAME = "localsr_device_check"
        /** DESIGN.md: an NPU that needs more than 15 s to load is not used this session. */
        const val WATCHDOG_MS = 15_000L
        /** First GPU run compiles shaders (can take many seconds on mid-range GPUs); a hang beyond this counts as a failure. */
        const val GPU_TIMEOUT_MS = 60_000L
    }
}
