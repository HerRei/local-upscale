package local.sr

import android.content.Context
import android.util.Log
import com.google.ai.edge.litert.Accelerator
import com.google.ai.edge.litert.BuiltinNpuAcceleratorProvider
import com.google.ai.edge.litert.CompiledModel
import com.google.ai.edge.litert.Environment
import com.google.ai.edge.litert.TensorBuffer
import local.srcore.ComputeUnit
import local.srcore.ModelKey

/** A model compiled for exactly one accelerator (no silent fallback: NPU-only, GPU-only or CPU-only). */
class LiteRtEngine private constructor(
    val model: ModelKey,
    val unit: ComputeUnit,
    val loadMs: Double,
    private val env: Environment?,
    private val compiled: CompiledModel,
    private val inputs: List<TensorBuffer>,
    private val outputs: List<TensorBuffer>,
) : TileRunner, AutoCloseable {

    /** Not thread-safe; the caller serialises runs. Allocates one 1536*1536*3 float array per call (LiteRT's Kotlin API has no read-into). */
    override fun run(input: FloatArray): FloatArray {
        invoke(input)
        return readOutput()
    }

    fun readOutput(): FloatArray {
        // readFloat allocates in JNI; avoid entering JNI if the Java heap cannot hold the result.
        val runtime = Runtime.getRuntime()
        val needed = PixelKernels.TILE_OUT.toLong() * PixelKernels.TILE_OUT * 3 * 4
        fun available() = runtime.maxMemory() - (runtime.totalMemory() - runtime.freeMemory())
        if (available() < needed + 4 * 1024 * 1024) System.gc()
        if (available() < needed + 4 * 1024 * 1024) throw OutOfMemoryError("not enough memory for model output")
        return outputs[0].readFloat()
    }

    /** Run without copying the output to the Java heap. */
    fun invoke(input: FloatArray) {
        inputs[0].writeFloat(input)
        compiled.run(inputs, outputs)
    }

    override fun close() {
        for (b in inputs) runCatching { b.close() }
        for (b in outputs) runCatching { b.close() }
        runCatching { compiled.close() }
        runCatching { env?.close() }
    }

    companion object {
        private const val TAG = "LocalSR"

        /** Asset name of a model inside the APK. */
        fun assetName(model: ModelKey): String = model.fileName + ".tflite"

        /** Throws when the accelerator is missing, fails to compile or has no matching vendor runtime. */
        fun load(context: Context, model: ModelKey, unit: ComputeUnit): LiteRtEngine {
            val start = System.nanoTime()
            var env: Environment? = null
            try {
                val accelerator = when (unit) {
                    ComputeUnit.npu -> Accelerator.NPU
                    ComputeUnit.gpu -> Accelerator.GPU
                    ComputeUnit.cpu -> Accelerator.CPU
                }
                val options = CompiledModel.Options(accelerator)
                if (unit == ComputeUnit.cpu) {
                    options.cpuOptions = CompiledModel.CpuOptions(
                        numThreads = Runtime.getRuntime().availableProcessors().coerceIn(1, 8),
                    )
                }
                if (unit == ComputeUnit.npu) {
                    // The NPU needs a vendor runtime. Only use it when it is already on the device (no download: the app has no network use).
                    val provider = BuiltinNpuAcceleratorProvider(context)
                    if (!provider.isDeviceSupported()) throw UpscaleException("No supported NPU on this device")
                    if (!provider.isLibraryReady()) throw UpscaleException("NPU runtime is not installed (and the app does not download it)")
                    env = Environment.create(context, provider)
                }
                val compiled = if (env != null) {
                    CompiledModel.create(context.assets, assetName(model), options, env)
                } else {
                    CompiledModel.create(context.assets, assetName(model), options)
                }
                try {
                    val inputs = compiled.createInputBuffers()
                    val outputs = compiled.createOutputBuffers()
                    val ms = (System.nanoTime() - start) / 1e6
                    Log.i(TAG, "loaded ${model.rawValue} on ${unit.rawValue} in ${"%.0f".format(ms)} ms")
                    return LiteRtEngine(model, unit, ms, env, compiled, inputs, outputs)
                } catch (t: Throwable) {
                    runCatching { compiled.close() }
                    throw t
                }
            } catch (t: Throwable) {
                runCatching { env?.close() }
                throw t
            }
        }
    }
}

/** User-facing failure of the engine or the pipeline. */
class UpscaleException(message: String, cause: Throwable? = null) : Exception(message, cause)
