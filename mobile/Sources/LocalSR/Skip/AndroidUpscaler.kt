package local.sr

import android.content.ContentValues
import android.content.Context
import android.graphics.Bitmap
import android.net.Uri
import android.os.Build
import android.provider.MediaStore
import android.util.Log
import androidx.exifinterface.media.ExifInterface
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import local.srcore.ComputeUnit
import local.srcore.DeviceVerdict
import local.srcore.ModelKey
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import java.util.UUID

/** What the Swift wrapper turns into an UpscaleResult. */
class RawUpscaleResult(
    val id: String,
    val outputPath: String,
    val previewPath: String,
    val width: Int,
    val height: Int,
    val unit: ComputeUnit,
    val seconds: Double,
)

/**
 * The Android engine behind AndroidUpscaleService: device check, model loading, tiling pipeline, JPEG +
 * EXIF output and the MediaStore save. All heavy work is real Kotlin on primitive arrays and Bitmaps.
 */
class AndroidUpscaler(context: Context) {
    private val context: Context = context.applicationContext
    private val checker = DeviceChecker(this.context)
    private val lock = Mutex()
    private var engine: LiteRtEngine? = null
    /** Verdicts that must not be cached (NPU load timed out: "use the GPU for this session"). */
    private val sessionVerdicts = HashMap<ModelKey, DeviceVerdict>()

    /** Last device check report per model (human readable), for the About screen and logs. */
    val reports = HashMap<ModelKey, String>()

    // MARK: prepare

    suspend fun prepare(model: ModelKey): DeviceVerdict = lock.withLock { prepareLocked(model) }

    private suspend fun prepareLocked(model: ModelKey): DeviceVerdict {
        var verdict = sessionVerdicts[model] ?: checker.cached(model)
        if (verdict == null) {
            val report = checker.run(model)
            reports[model] = report.toString()
            verdict = report.verdict
            if (report.cacheable) checker.store(model, verdict) else sessionVerdicts[model] = verdict
        }
        val current = engine
        if (current != null && current.model == model && current.unit == verdict.unit) return verdict
        closeEngine()
        try {
            engine = withContext(Dispatchers.Default) { LiteRtEngine.load(context, model, verdict.unit) }
        } catch (t: Throwable) {
            if (verdict.unit == ComputeUnit.cpu) throw UpscaleException("Cannot load the ${model.rawValue} model on the CPU: ${t.message}", t)
            // The cached unit no longer works (driver update, ...): forget it and measure again.
            Log.w(TAG, "cached ${verdict.unit.rawValue} verdict for ${model.rawValue} failed to load: ${t.message}")
            checker.forget(model)
            sessionVerdicts.remove(model)
            val report = checker.run(model)
            reports[model] = report.toString()
            verdict = report.verdict
            if (report.cacheable) checker.store(model, verdict) else sessionVerdicts[model] = verdict
            engine = withContext(Dispatchers.Default) { LiteRtEngine.load(context, model, verdict.unit) }
        }
        return verdict
    }

    private fun closeEngine() {
        engine?.close()
        engine = null
    }

    /** Frees the loaded model (call from onLowMemory / when leaving the app). */
    suspend fun release() = lock.withLock { closeEngine() }

    // MARK: upscale

    /**
     * Upscales the photo at [inputUri] (content://, file:// or a plain path) 4x.
     * [progress] gets 0...1 after every tile (from the compute thread). Cancelling the coroutine stops between tiles
     * and removes the partial files. One photo at a time: concurrent calls queue.
     */
    suspend fun upscale(inputUri: String, model: ModelKey, progress: (Double) -> Unit): RawUpscaleResult = lock.withLock {
        val started = System.nanoTime()
        val uri = Uri.parse(inputUri)
        val verdict = prepareLocked(model)
        val id = UUID.randomUUID().toString()
        val dir = File(context.cacheDir, "LocalSR/$id")
        var finished = false
        UpscaleForegroundService.acquire(context)
        try {
            withContext(Dispatchers.Default) {
                dir.mkdirs()
                var input: Bitmap? = AndroidCodec.decode(context, uri)
                val w = input!!.width
                val h = input.height
                val ow = w * PixelKernels.SCALE
                val oh = h * PixelKernels.SCALE
                var output: Bitmap? = null
                try {
                    output = try {
                        Bitmap.createBitmap(ow, oh, Bitmap.Config.ARGB_8888).also { it.setHasAlpha(false) }
                    } catch (e: OutOfMemoryError) {
                        throw UpscaleException("Not enough memory for a ${ow}x$oh result", e)
                    }
                    var usedUnit = verdict.unit
                    progress(0.0)
                    UpscaleForegroundService.updateProgress(context, 0.0)
                    val runner = TileRunner { tile ->
                        val e = engine ?: throw UpscaleException("Model is not loaded")
                        try {
                            e.run(tile)
                        } catch (t: Throwable) {
                            if (e.unit == ComputeUnit.cpu) throw t
                            // The chosen accelerator failed mid-job: continue on the CPU and remember that.
                            Log.w(TAG, "${e.unit.rawValue} failed during upscale (${t.message}); falling back to the CPU")
                            checker.store(model, DeviceVerdict(unit = ComputeUnit.cpu, reason = "${e.unit.rawValue.uppercase()} failed while upscaling; using CPU."))
                            e.close()
                            val cpu = LiteRtEngine.load(context, model, ComputeUnit.cpu)
                            engine = cpu
                            usedUnit = ComputeUnit.cpu
                            cpu.run(tile)
                        }
                    }
                    TilePipeline.process(BitmapSource(input), BitmapSink(output), runner) { done, total ->
                        val f = done.toDouble() / total * 0.97
                        progress(f)
                        UpscaleForegroundService.updateProgress(context, f)
                    }
                    input.recycle()
                    input = null
                    val outFile = File(dir, "output.jpg")
                    AndroidCodec.writeJpeg(output, outFile)
                    AndroidCodec.copyExif(context, uri, outFile, ow, oh)
                    val prev = AndroidCodec.preview(output)
                    AndroidCodec.writeJpeg(prev, File(dir, "preview.jpg"))
                    if (prev !== output) prev.recycle()
                    progress(1.0)
                    val seconds = (System.nanoTime() - started) / 1e9
                    finished = true
                    RawUpscaleResult(id, outFile.absolutePath, File(dir, "preview.jpg").absolutePath, ow, oh, usedUnit, seconds)
                } finally {
                    input?.recycle()
                    output?.recycle()
                }
            }
        } finally {
            UpscaleForegroundService.release(context)
            if (!finished) dir.deleteRecursively()
        }
    }

    // MARK: save

    /** Inserts the JPEG into MediaStore (Pictures/LocalSR) with date taken from its EXIF. Returns the content URI. */
    suspend fun saveToLibrary(outputPath: String): String = withContext(Dispatchers.IO) {
        MediaStoreLibrary.save(context, File(outputPath)).toString()
    }

    companion object {
        private const val TAG = "LocalSR"
    }
}

/** Saves finished photos with the IS_PENDING pattern; no storage permission needed (scoped storage, own files). */
object MediaStoreLibrary {
    const val RELATIVE_PATH = "Pictures/LocalSR"

    fun save(context: Context, file: File): Uri {
        if (Build.VERSION.SDK_INT < 29) throw UpscaleException("Saving needs Android 10 or newer")
        if (!file.isFile) throw UpscaleException("Result file is missing: ${file.name}")
        val resolver = context.contentResolver
        val takenMs = dateTakenMillis(file)
        val stamp = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(Date())
        val values = ContentValues().apply {
            put(MediaStore.Images.Media.DISPLAY_NAME, "LocalSR_$stamp.jpg")
            put(MediaStore.Images.Media.MIME_TYPE, "image/jpeg")
            put(MediaStore.Images.Media.RELATIVE_PATH, RELATIVE_PATH)
            put(MediaStore.Images.Media.IS_PENDING, 1)
            if (takenMs != null) put(MediaStore.Images.Media.DATE_TAKEN, takenMs)
        }
        val collection = MediaStore.Images.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)
        val uri = resolver.insert(collection, values) ?: throw UpscaleException("MediaStore refused the new picture")
        try {
            resolver.openOutputStream(uri, "w")?.use { out -> file.inputStream().use { it.copyTo(out, 1 shl 16) } }
                ?: throw UpscaleException("Cannot open the new MediaStore entry for writing")
            val done = ContentValues().apply { put(MediaStore.Images.Media.IS_PENDING, 0) }
            resolver.update(uri, done, null, null)
        } catch (t: Throwable) {
            runCatching { resolver.delete(uri, null, null) }
            throw t
        }
        return uri
    }

    /** DateTimeOriginal (+ OffsetTimeOriginal when present) as epoch millis, else null. */
    fun dateTakenMillis(file: File): Long? {
        return try {
            val exif = ExifInterface(file)
            val raw = exif.getAttribute(ExifInterface.TAG_DATETIME_ORIGINAL) ?: exif.getAttribute(ExifInterface.TAG_DATETIME) ?: return null
            val offset = exif.getAttribute(ExifInterface.TAG_OFFSET_TIME_ORIGINAL) ?: exif.getAttribute(ExifInterface.TAG_OFFSET_TIME)
            val fmt = SimpleDateFormat("yyyy:MM:dd HH:mm:ss", Locale.US)
            fmt.timeZone = if (offset != null) TimeZone.getTimeZone("GMT$offset") else TimeZone.getDefault()
            fmt.parse(raw)?.time
        } catch (e: Exception) {
            null
        }
    }
}
