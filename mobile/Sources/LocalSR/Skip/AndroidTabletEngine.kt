package local.sr

import android.content.Context
import android.graphics.Bitmap
import android.net.Uri
import com.google.ai.edge.litert.Accelerator
import com.google.ai.edge.litert.CompiledModel
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
        compiled.close()
    }
    companion object {
        fun load(context: Context, file: String, gpu: Boolean): TabletInterpreter {
            val options = CompiledModel.Options(if (gpu) Accelerator.GPU else Accelerator.CPU)
            if (!gpu) options.cpuOptions = CompiledModel.CpuOptions(numThreads = Runtime.getRuntime().availableProcessors().coerceIn(1, 8))
            val compiled = CompiledModel.create(context.assets, "tablet/$file", options)
            try { return TabletInterpreter(compiled, compiled.createInputBuffers(), compiled.createOutputBuffers()) }
            catch (t: Throwable) { compiled.close(); throw t }
        }
    }
}

class AndroidTabletRawResult(val outputPath: String, val previewPath: String, val width: Int, val height: Int, val scale: Int, val seconds: Double)

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

    suspend fun process(inputUri: String, plan: Plan, fullScale: Boolean, gpuOnly: Boolean,
                        progress: (Int, Double) -> Unit,
                        prediction: (Int, Boolean, Double, Double) -> Unit,
                        activity: (Int, Boolean, Double) -> Unit): AndroidTabletRawResult = withContext(Dispatchers.Default) {
        val start = System.nanoTime()
        val uri = Uri.parse(inputUri)
        val directory = File(context.cacheDir, "LocalSR/${UUID.randomUUID()}")
        var finished = false
        var pixels: Bitmap? = null
        val activityLock = Any()
        var activeStage = -1
        var activeGPU = false
        var activeSince: Double? = null
        val intervals = mutableListOf<Pair<Double, Double>>()
        val timer = Timer("localsr-tablet-activity", true)
        timer.scheduleAtFixedRate(object : TimerTask() {
            override fun run() {
                val now = System.nanoTime() / 1e9
                val snapshot = synchronized(activityLock) {
                    intervals.removeAll { it.second <= now - 1 }
                    val busy = intervals.sumOf { maxOf(0.0, minOf(now, it.second) - maxOf(now - 1, it.first)) } +
                        (activeSince?.let { maxOf(0.0, now - maxOf(now - 1, it)) } ?: 0.0)
                    Triple(activeStage, activeGPU, busy.coerceIn(0.0, 1.0))
                }
                activity(snapshot.first, snapshot.second, snapshot.third)
            }
        }, 0, 500)
        try {
            checkCancelled()
            val upscale = plan.stages.any { ModelCatalog.model(it).scale == 4 }
            pixels = AndroidCodec.decode(context, uri, preserveDimensions = !upscale)
            val policy = SizePolicy.target(width = pixels.width, height = pixels.height)
            val scale = if (!upscale) 1 else if (fullScale && memory.canUseFullScale(inputUri)) 4 else policy.outputScale
            for ((index, model) in plan.stages.withIndex()) {
                checkCancelled()
                currentCoroutineContext().ensureActive()
                val descriptor = ModelCatalog.model(model)
                val factor = if (descriptor.scale == 1) 1 else scale
                val output = Bitmap.createBitmap(pixels!!.width * factor, pixels!!.height * factor, Bitmap.Config.ARGB_8888)
                output.setHasAlpha(false)
                var gpu = true
                var interpreter = try {
                    try { TabletInterpreter.load(context, descriptor.androidFileName, true) }
                    catch (error: Exception) { if (gpuOnly) throw error; gpu = false; TabletInterpreter.load(context, descriptor.androidFileName, false) }
                } catch (error: Throwable) { output.recycle(); throw error }
                synchronized(activityLock) { activeStage = index; activeGPU = gpu }
                try {
                    val runner = TileRunner { tile ->
                        checkCancelled()
                        fun predict(onGPU: Boolean): FloatArray {
                            val t = System.nanoTime() / 1e9
                            synchronized(activityLock) { activeSince = t }
                            try { return interpreter.run(tile) }
                            finally {
                                val end = System.nanoTime() / 1e9
                                synchronized(activityLock) { intervals.add(Pair(t, end)); activeSince = null }
                                prediction(index, onGPU, t, end)
                            }
                        }
                        try { predict(gpu) } catch (error: Exception) {
                            if (!gpu || gpuOnly) throw error
                            interpreter.close()
                            gpu = false
                            synchronized(activityLock) { activeGPU = false }
                            activity(-1, true, 0.0)
                            interpreter = TabletInterpreter.load(context, descriptor.androidFileName, false)
                            predict(false)
                        }
                    }
                    TilePipeline.process(BitmapSource(pixels!!), BitmapSink(output), runner, outputScale = factor,
                        windowSize = descriptor.window, halo = descriptor.halo, modelScale = descriptor.scale) { done, total ->
                        checkCancelled()
                        progress(index, done.toDouble() / total)
                    }
                } catch (t: Throwable) { output.recycle(); throw t }
                finally { interpreter.close() }
                synchronized(activityLock) { activeStage = -1 }
                pixels!!.recycle()
                pixels = output
            }
            checkCancelled()
            currentCoroutineContext().ensureActive()
            val output = File(directory, "output.jpg")
            AndroidCodec.writeJpeg(pixels!!, output)
            AndroidCodec.copyExif(context, uri, output, pixels!!.width, pixels!!.height)
            checkCancelled()
            val preview = AndroidCodec.preview(pixels!!)
            try { AndroidCodec.writeJpeg(preview, File(directory, "preview.jpg")) }
            finally { if (preview !== pixels) preview.recycle() }
            checkCancelled()
            currentCoroutineContext().ensureActive()
            finished = true
            AndroidTabletRawResult(output.absolutePath, File(directory, "preview.jpg").absolutePath, pixels!!.width, pixels!!.height, scale, (System.nanoTime() - start) / 1e9)
        } finally {
            timer.cancel()
            activity(-1, synchronized(activityLock) { activeGPU }, 0.0)
            pixels?.recycle()
            if (!finished) directory.deleteRecursively()
        }
    }
}
