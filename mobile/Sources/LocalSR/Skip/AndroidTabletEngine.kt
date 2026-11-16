package local.sr

import android.content.Context
import android.graphics.Bitmap
import android.net.Uri
import android.os.StatFs
import com.google.ai.edge.litert.Accelerator
import com.google.ai.edge.litert.BuiltinNpuAcceleratorProvider
import com.google.ai.edge.litert.CompiledModel
import com.google.ai.edge.litert.Environment
import com.google.ai.edge.litert.TensorBuffer
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import local.srcore.ModelCatalog
import local.srcore.Plan
import local.srcore.SizePolicy
import java.io.File
import java.util.UUID
import java.util.Timer
import java.util.TimerTask
import java.util.concurrent.atomic.AtomicBoolean
import skip.lib.AsyncStream
import skip.lib.makeStream

/** Typed continuation for the shared Swift service's lane stream. */
class AndroidTabletActivityStream {
    val stream: AsyncStream<TabletLaneActivity>
    private val continuation: AsyncStream.Continuation<TabletLaneActivity>
    init {
        val (stream, continuation) = AsyncStream.makeStream(of = TabletLaneActivity::class,
            bufferingPolicy = AsyncStream.Continuation.BufferingPolicy.bufferingNewest(4))
        this.stream = stream
        this.continuation = continuation
    }
    fun emit(activity: TabletLaneActivity) { continuation.yield(activity) }
}

/** One compiled tablet artifact. The Kotlin tile pipeline owns all pixel buffers. */
private class TabletInterpreter private constructor(
    private val env: Environment?,
    private val compiled: CompiledModel,
    private val inputs: List<TensorBuffer>,
    private val outputs: List<TensorBuffer>,
) : AutoCloseable {
    fun run(input: FloatArray): FloatArray {
        inputs[0].writeFloat(input)
        compiled.run(inputs, outputs)
        return outputs[0].readFloat()
    }
    override fun close() {
        inputs.forEach { runCatching { it.close() } }
        outputs.forEach { runCatching { it.close() } }
        runCatching { compiled.close() }
        runCatching { env?.close() }
    }
    companion object {
        const val UNIT_CPU = 0
        const val UNIT_GPU = 1
        const val UNIT_NPU = 2

        fun load(context: Context, file: String, unitCode: Int): TabletInterpreter {
            var env: Environment? = null
            try {
                val accelerator = when (unitCode) {
                    UNIT_NPU -> Accelerator.NPU
                    UNIT_GPU -> Accelerator.GPU
                    else -> Accelerator.CPU
                }
                val options = CompiledModel.Options(accelerator)
                if (unitCode == UNIT_CPU) {
                    options.cpuOptions = CompiledModel.CpuOptions(numThreads = Runtime.getRuntime().availableProcessors().coerceIn(1, 8))
                }
                if (unitCode == UNIT_NPU) {
                    val provider = BuiltinNpuAcceleratorProvider(context)
                    if (!provider.isDeviceSupported() || !provider.isLibraryReady()) {
                        throw UpscaleException("NPU unavailable on this tablet")
                    }
                    env = Environment.create(context, provider)
                }
                val compiled = if (env != null) {
                    CompiledModel.create(context.assets, "tablet/$file", options, env)
                } else {
                    CompiledModel.create(context.assets, "tablet/$file", options)
                }
                try { return TabletInterpreter(env, compiled, compiled.createInputBuffers(), compiled.createOutputBuffers()) }
                catch (t: Throwable) { runCatching { compiled.close() }; throw t }
            } catch (t: Throwable) {
                runCatching { env?.close() }
                throw t
            }
        }
    }
}

class AndroidTabletRawResult(
    val outputPath: String,
    val previewPath: String,
    val width: Int,
    val height: Int,
    val scale: Int,
    val fullScaleFallback: Boolean,
    val seconds: Double,
)

/** Sequential stages and photos on one LiteRT lane. */
class AndroidTabletEngine(context: Context) {
    private val context = context.applicationContext
    private val cancelled = AtomicBoolean(false)
    private val running = AtomicBoolean(false)
    private val memory = AndroidUpscaler(context)
    fun start(): Boolean {
        if (!running.compareAndSet(false, true)) return false
        cancelled.set(false)
        return true
    }
    fun finish() { running.set(false) }
    fun cancel() { cancelled.set(true) }
    private fun checkCancelled() { if (cancelled.get()) throw CancellationException("Tablet batch cancelled") }

    fun hasNPULane(): Boolean {
        return try {
            val provider = BuiltinNpuAcceleratorProvider(context)
            provider.isDeviceSupported() && provider.isLibraryReady()
        } catch (_: Throwable) {
            false
        }
    }

    fun availableStorageBytes(): Long {
        return try {
            val stat = StatFs(context.cacheDir.absolutePath)
            stat.availableBytes
        } catch (_: Throwable) {
            Long.MAX_VALUE
        }
    }

    fun isModelBundled(file: String): Boolean {
        return try {
            context.assets.list("tablet")?.contains(file) == true
        } catch (_: Throwable) {
            false
        }
    }

    fun bundledModelByteSize(file: String): Long? {
        return try {
            context.assets.openFd("tablet/$file").use { fd ->
                if (fd.length > 0) fd.length else null
            }
        } catch (_: Throwable) {
            try {
                context.assets.open("tablet/$file").use { stream ->
                    var total = 0L
                    val buf = ByteArray(8192)
                    while (true) {
                        val read = stream.read(buf)
                        if (read <= 0) break
                        total += read
                    }
                    if (total > 0) total else null
                }
            } catch (_: Throwable) {
                null
            }
        }
    }

    suspend fun process(inputUri: String, plan: Plan, fullScale: Boolean, targetScale: Int = 4, outputFormat: String = "jpeg", laneMode: Int = 0,
                        progress: (Int, Double) -> Unit,
                        prediction: (Int, Int, Double, Double) -> Unit,
                        activity: (Int, Int, Double) -> Unit,
                        stageDone: (Int, Int, Double) -> Unit = { _, _, _ -> }): AndroidTabletRawResult = withContext(Dispatchers.Default) {
        val start = System.nanoTime()
        val uri = Uri.parse(inputUri)
        val directory = File(context.cacheDir, "LocalSR/${UUID.randomUUID()}")
        var finished = false
        var pixels: Bitmap? = null
        val activityLock = Any()
        var activeStage = -1
        var activeUnit = TabletInterpreter.UNIT_GPU
        var activeSince: Double? = null
        val intervals = mutableListOf<Pair<Double, Double>>()
        val timer = Timer("localsr-tablet-activity", true)
        timer.scheduleAtFixedRate(object : TimerTask() {
            override fun run() {
                val now = System.nanoTime() / 1e9
                val snapshot = synchronized(activityLock) {
                    intervals.removeAll { it.second <= now - 1 }
                    var busy = 0.0
                    for ((start, end) in intervals) {
                        busy += maxOf(0.0, minOf(now, end) - maxOf(now - 1.0, start))
                    }
                    busy += activeSince?.let { maxOf(0.0, now - maxOf(now - 1.0, it)) } ?: 0.0
                    Triple(activeStage, activeUnit, busy.coerceIn(0.0, 1.0))
                }
                activity(snapshot.first, snapshot.second, snapshot.third)
            }
        }, 0, 500)
        try {
            checkCancelled()
            val upscale = plan.stages.any { ModelCatalog.model(it).scale == 4 }
            pixels = AndroidCodec.decode(context, uri, preserveDimensions = !upscale)
            val policy = SizePolicy.target(width = pixels.width, height = pixels.height)
            val canUpgradeFull = fullScale && policy.outputScale == 2 && memory.canUseFullScale(inputUri)
            val scale = if (!upscale) 1 else if (targetScale == 2) 2 else if (canUpgradeFull) 4 else policy.outputScale
            val fullScaleFallback = upscale && targetScale == 4 && fullScale && policy.outputScale == 2 && !canUpgradeFull
            val npuSupported = hasNPULane()
            for ((index, model) in plan.stages.withIndex()) {
                val stageStart = System.nanoTime()
                checkCancelled()
                currentCoroutineContext().ensureActive()
                val descriptor = ModelCatalog.model(model)
                val factor = if (descriptor.scale == 1) 1 else scale
                val output = Bitmap.createBitmap(pixels!!.width * factor, pixels!!.height * factor, Bitmap.Config.ARGB_8888)
                output.setHasAlpha(false)
                // laneMode: 0 = both, 1 = NPU only, 2 = GPU only
                val candidates: List<Int> = when (laneMode) {
                    1 -> if (npuSupported) listOf(TabletInterpreter.UNIT_NPU) else emptyList()
                    2 -> listOf(TabletInterpreter.UNIT_GPU)
                    else -> {
                        val list = ArrayList<Int>()
                        if (npuSupported && descriptor.preferredLane == local.srcore.ModelLane.neuralEngine) {
                            list.add(TabletInterpreter.UNIT_NPU)
                        }
                        list.add(TabletInterpreter.UNIT_GPU)
                        if (npuSupported && !list.contains(TabletInterpreter.UNIT_NPU)) {
                            list.add(TabletInterpreter.UNIT_NPU)
                        }
                        list.add(TabletInterpreter.UNIT_CPU)
                        list
                    }
                }
                if (candidates.isEmpty()) {
                    output.recycle()
                    throw UpscaleException("No compatible compute chip available on this tablet.")
                }
                var candidateIdx = 0
                var unit = candidates[0]
                var interpreter: TabletInterpreter? = null
                while (candidateIdx < candidates.size) {
                    try {
                        unit = candidates[candidateIdx]
                        interpreter = TabletInterpreter.load(context, descriptor.androidFileName, unit)
                        break
                    } catch (error: Throwable) {
                        candidateIdx++
                        if (candidateIdx >= candidates.size) {
                            output.recycle()
                            throw error
                        }
                    }
                }
                var activeInterp = interpreter!!
                synchronized(activityLock) { activeStage = index; activeUnit = unit }
                try {
                    val runner = TileRunner { tile ->
                        checkCancelled()
                        fun predict(u: Int): FloatArray {
                            val t = System.nanoTime() / 1e9
                            synchronized(activityLock) { activeSince = t }
                            try { return activeInterp.run(tile) }
                            finally {
                                val end = System.nanoTime() / 1e9
                                synchronized(activityLock) { intervals.add(Pair(t, end)); activeSince = null }
                                prediction(index, u, t, end)
                            }
                        }
                        try { predict(unit) } catch (error: Exception) {
                            if (candidateIdx + 1 >= candidates.size) throw error
                            activeInterp.close()
                            activity(-1, unit, 0.0)
                            var nextLoaded: TabletInterpreter? = null
                            var nextUnit = unit
                            while (candidateIdx + 1 < candidates.size) {
                                candidateIdx++
                                nextUnit = candidates[candidateIdx]
                                try {
                                    nextLoaded = TabletInterpreter.load(context, descriptor.androidFileName, nextUnit)
                                    break
                                } catch (_: Throwable) {
                                }
                            }
                            if (nextLoaded == null) throw error
                            activeInterp = nextLoaded
                            unit = nextUnit
                            synchronized(activityLock) { activeUnit = unit }
                            predict(unit)
                        }
                    }
                    TilePipeline.process(BitmapSource(pixels!!), BitmapSink(output), runner, outputScale = factor,
                        windowSize = descriptor.window, halo = descriptor.halo, modelScale = descriptor.scale) { done, total ->
                        checkCancelled()
                        progress(index, done.toDouble() / total)
                    }
                } catch (t: Throwable) { output.recycle(); throw t }
                finally { runCatching { activeInterp.close() } }
                synchronized(activityLock) { activeStage = -1 }
                stageDone(index, unit, (System.nanoTime() - stageStart) / 1e9)
                pixels!!.recycle()
                pixels = output
            }
            checkCancelled()
            currentCoroutineContext().ensureActive()
            val isPng = outputFormat == "png"
            val output = File(directory, if (isPng) "output.png" else "output.jpg")
            if (isPng) {
                AndroidCodec.writePng(pixels!!, output)
            } else {
                AndroidCodec.writeJpeg(pixels!!, output)
                AndroidCodec.copyExif(context, uri, output, pixels!!.width, pixels!!.height)
            }
            checkCancelled()
            val preview = AndroidCodec.preview(pixels!!)
            try { AndroidCodec.writeJpeg(preview, File(directory, "preview.jpg")) }
            finally { if (preview !== pixels) preview.recycle() }
            checkCancelled()
            currentCoroutineContext().ensureActive()
            finished = true
            AndroidTabletRawResult(output.absolutePath, File(directory, "preview.jpg").absolutePath, pixels!!.width, pixels!!.height, scale, fullScaleFallback, (System.nanoTime() - start) / 1e9)
        } finally {
            timer.cancel()
            activity(-1, synchronized(activityLock) { activeUnit }, 0.0)
            pixels?.recycle()
            if (!finished) directory.deleteRecursively()
        }
    }
}
