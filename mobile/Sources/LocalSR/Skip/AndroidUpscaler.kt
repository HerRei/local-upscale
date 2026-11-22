package local.sr

import android.app.ActivityManager
import android.graphics.BitmapFactory
import android.os.StatFs
import local.srcore.SizePolicy
import android.content.ContentValues
import android.content.Context
import android.graphics.Bitmap
import android.net.Uri
import android.os.Build
import android.provider.MediaStore
import android.util.Log
import androidx.exifinterface.media.ExifInterface
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
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
    val outputScale: Int,
    val fullScaleFallback: Boolean = false,
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
    /** The thread all native calls of an NPU/GPU [engine] run on (null for the CPU). */
    private var engineThread: EngineThread? = null
    /**
     * Verdicts that must not be cached: the NPU load timed out ("use the GPU for this session"), or an accelerator
     * failed during a job, which can be transient (GPU busy, a driver hiccup), so it only holds for this process.
     */
    private val sessionVerdicts = HashMap<ModelKey, DeviceVerdict>()

    /** Last device check report per model (human readable), for the About screen and logs. */
    val reports = HashMap<ModelKey, String>()

    // MARK: prepare

    suspend fun prepare(model: ModelKey): DeviceVerdict = lock.withLock { prepareLocked(model) }

    private suspend fun prepareLocked(model: ModelKey): DeviceVerdict {
        var verdict = sessionVerdicts[model] ?: checker.cached(model) ?: runCheck(model)
        val current = engine
        if (current != null && current.model == model && current.unit == verdict.unit) return verdict
        closeEngine()
        try {
            loadEngine(model, verdict.unit)
        } catch (c: CancellationException) {
            throw c
        } catch (t: Throwable) {
            if (verdict.unit == ComputeUnit.cpu) throw UpscaleException("Cannot load the ${model.rawValue} model on the CPU: ${t.message}", t)
            // The cached unit no longer works (driver update, ...): forget it and measure again. A load that hangs
            // is recorded, so the new check skips that unit.
            Log.w(TAG, "cached ${verdict.unit.rawValue} verdict for ${model.rawValue} failed to load: ${t.message}")
            if (t is UnitHangException) checker.markFailed(model, verdict.unit, "took more than ${LOAD_TIMEOUT_MS / 1000} s to load")
            checker.forget(model)
            sessionVerdicts.remove(model)
            verdict = runCheck(model)
            loadEngine(model, verdict.unit)
        }
        return verdict
    }

    private suspend fun runCheck(model: ModelKey): DeviceVerdict {
        val report = checker.run(model)
        reports[model] = report.toString()
        if (report.cacheable) checker.store(model, report.verdict) else sessionVerdicts[model] = report.verdict
        return report.verdict
    }

    /** Loads [unit] into [engine]. An NPU/GPU engine gets its own thread, and its load a time limit. */
    private suspend fun loadEngine(model: ModelKey, unit: ComputeUnit) {
        if (unit == ComputeUnit.cpu) {
            engine = withContext(Dispatchers.Default) { LiteRtEngine.load(context, model, unit) }
            return
        }
        val thread = EngineThread("localsr-${unit.rawValue}")
        val key = checker.cacheKey(model)
        // Kept outside the cancellable withContext: if Cancel wins after the load finished but before withContext
        // returns, the loaded engine would otherwise be dropped without being closed.
        var loaded: LiteRtEngine? = null
        try {
            withContext(Dispatchers.Default) {
                val job = coroutineContext[Job]
                loaded = thread.call(LOAD_TIMEOUT_MS, isCancelled = { job?.isActive == false }, onDiscard = { late: LiteRtEngine -> late.close() }) {
                    checker.guard.inUse(key, unit.rawValue) { LiteRtEngine.load(context, model, unit) }
                }
            }
            engine = loaded
            engineThread = thread
        } catch (t: Throwable) {
            val orphan = loaded
            thread.retire { orphan?.close() }
            throw t
        }
    }

    /** Closes the engine. An NPU/GPU engine is closed on its own thread, after a call that may still hang there. */
    private fun closeEngine() {
        val e = engine ?: return
        val thread = engineThread
        engine = null
        engineThread = null
        if (thread == null) e.close() else thread.retire { e.close() }
    }

    /** Frees the loaded model (call from onLowMemory / when leaving the app). */
    suspend fun release() = lock.withLock { closeEngine() }

    private fun dimensions(uri: Uri): Pair<Int, Int>? = try {
        val options = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        val stream = if (uri.scheme == null || uri.scheme == "file") File(uri.path ?: "").inputStream() else context.contentResolver.openInputStream(uri)
        stream?.use { BitmapFactory.decodeStream(it, null, options) }
        if (options.outWidth > 0 && options.outHeight > 0) Pair(options.outWidth, options.outHeight) else null
    } catch (_: Exception) { null }

    /**
     * Whether a Full 4x run of this photo fits in memory now. The big buffers (decoded input, output and preview
     * Bitmaps) are native memory since API 26, so they are checked against the system's available memory; the Java
     * heap only holds per-tile arrays, whose size does not depend on the output size.
     */
    fun canUseFullScale(inputUri: String): Boolean {
        val (width, height) = dimensions(Uri.parse(inputUri)) ?: return false
        val policy = SizePolicy.target(width = width, height = height)
        if (policy.outputScale != 2) return false
        val manager = context.getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
        val info = ActivityManager.MemoryInfo()
        manager.getMemoryInfo(info)
        val runtime = Runtime.getRuntime()
        val heapFree = runtime.maxMemory() - (runtime.totalMemory() - runtime.freeMemory())
        val outputPixels = policy.width.toLong() * policy.height * 16
        return FullScaleBudget.fits(outputPixels, availMem = info.availMem, lowMemoryThreshold = info.threshold, lowMemory = info.lowMemory, javaHeapFree = heapFree)
    }

    private fun freeCacheBytes(): Long = try {
        StatFs(context.cacheDir.absolutePath).availableBytes
    } catch (_: Throwable) {
        Long.MAX_VALUE
    }

    // MARK: upscale

    /**
     * Upscales the photo at [inputUri] (content://, file:// or a plain path) 4x.
     * [progress] gets 0...1 after every tile (from the compute thread). Cancelling the coroutine stops between tiles
     * and removes the partial files. One photo at a time: concurrent calls queue.
     */
    suspend fun upscale(inputUri: String, model: ModelKey, fullScale: Boolean, progress: (Double) -> Unit): RawUpscaleResult = lock.withLock {
        val started = System.nanoTime()
        val uri = Uri.parse(inputUri)
        val verdict = prepareLocked(model)
        val id = UUID.randomUUID().toString()
        val dir = File(context.cacheDir, "LocalSR/$id")
        var finished = false
        try {
            val result = withContext(Dispatchers.Default) {
                dir.mkdirs()
                val bounds = dimensions(uri) ?: throw UpscaleException("Cannot read image dimensions")
                val policy = SizePolicy.target(width = bounds.first, height = bounds.second)
                val scale = if (fullScale && canUseFullScale(inputUri)) 4 else policy.outputScale
                val fullScaleFallback = fullScale && policy.outputScale == 2 && scale < 4
                // Fail before minutes of tiling, not at the JPEG write.
                val storageNeeded = StorageBudget.bytesNeeded(policy.width.toLong() * policy.height * scale * scale)
                if (freeCacheBytes() < storageNeeded) throw UpscaleException(StorageBudget.notEnoughMessage(storageNeeded))
                var input: Bitmap? = AndroidCodec.decode(context, uri)
                val w = input!!.width
                val h = input.height
                val ow = w * scale
                val oh = h * scale
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
                    val job = coroutineContext[Job]
                    val guardKey = checker.cacheKey(model)
                    // The unit marked "in use" for the whole job; cleared as soon as the job falls back to the CPU,
                    // so a later stop during CPU work does not blame the accelerator.
                    var guardedUnit: ComputeUnit? = null
                    val runner = TileRunner { tile ->
                        val e = engine ?: throw UpscaleException("Model is not loaded")
                        val thread = engineThread
                        if (e.unit == ComputeUnit.cpu || thread == null) return@TileRunner e.run(tile)
                        try {
                            // On the engine's own thread with a time limit: a driver that hangs fails this tile
                            // instead of blocking the job, Cancel and every later photo.
                            thread.call(TILE_TIMEOUT_MS, isCancelled = { job?.isActive == false }) {
                                checker.guard.inUse(guardKey, e.unit.rawValue) { e.run(tile) }
                            }
                        } catch (c: CancellationException) {
                            if (thread.isAbandoned) closeEngine() // its tile is still running; closed when it returns
                            throw c
                        } catch (oom: OutOfMemoryError) {
                            // The Java heap (the tile's output array), not the accelerator: the CPU would need the same memory.
                            throw UpscaleException("Not enough memory to upscale this photo. Close other apps and try again.", oom)
                        } catch (t: Throwable) {
                            // The accelerator failed mid-job: finish on the CPU. A failed tile can be transient, so the
                            // CPU only holds for this session; a hang is recorded, so later checks skip the unit.
                            Log.w(TAG, "${e.unit.rawValue} failed during upscale (${t.message}); continuing on the CPU for this session")
                            if (t is UnitHangException) checker.markFailed(model, e.unit, "did not finish a tile within ${TILE_TIMEOUT_MS / 1000} s")
                            sessionVerdicts[model] = DeviceVerdict(unit = ComputeUnit.cpu, reason = "${e.unit.rawValue.uppercase()} failed while upscaling; using the CPU for now.")
                            guardedUnit?.let { checker.guard.end(guardKey, it.rawValue) }
                            guardedUnit = null
                            closeEngine()
                            val cpu = LiteRtEngine.load(context, model, ComputeUnit.cpu)
                            engine = cpu
                            usedUnit = ComputeUnit.cpu
                            cpu.run(tile)
                        }
                    }
                    // While real tiles run on an NPU/GPU, keep it marked as in use: if its driver takes the process
                    // down, the next start skips it instead of crashing on every batch.
                    guardedUnit = engine?.unit?.takeIf { it != ComputeUnit.cpu && engineThread != null }
                    guardedUnit?.let { checker.guard.begin(guardKey, it.rawValue) }
                    try {
                        TilePipeline.process(BitmapSource(input), BitmapSink(output), runner, outputScale = scale) { done, total ->
                            val f = done.toDouble() / total * 0.97
                            progress(f)
                            UpscaleForegroundService.updateProgress(context, f)
                        }
                    } finally {
                        guardedUnit?.let { checker.guard.end(guardKey, it.rawValue) }
                        guardedUnit = null
                    }
                    input.recycle()
                    input = null
                    val outFile = File(dir, "output.jpg")
                    try {
                        AndroidCodec.writeJpeg(output, outFile)
                        AndroidCodec.copyExif(context, uri, outFile, ow, oh)
                        val prev = AndroidCodec.preview(output)
                        try { AndroidCodec.writeJpeg(prev, File(dir, "preview.jpg")) } finally { if (prev !== output) prev.recycle() }
                    } catch (t: Exception) {
                        // A full disk shows up as "JPEG encoding failed" (Bitmap.compress swallows the IOException) or ENOSPC.
                        if (t !is CancellationException && StorageBudget.isOutOfSpace(t, freeCacheBytes())) {
                            throw UpscaleException("Not enough free storage to save the result. Free up some space and try again.", t)
                        }
                        throw t
                    }
                    currentCoroutineContext().ensureActive()
                    progress(1.0)
                    val seconds = (System.nanoTime() - started) / 1e9
                    RawUpscaleResult(id, outFile.absolutePath, File(dir, "preview.jpg").absolutePath, ow, oh, usedUnit, seconds, scale, fullScaleFallback)
                } finally {
                    input?.recycle()
                    output?.recycle()
                }
            }
            currentCoroutineContext().ensureActive()
            finished = true
            result
        } finally {
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
        /** An NPU/GPU load that takes longer is treated as hung (the device check allows 60 s for load + warm-up + timing). */
        const val LOAD_TIMEOUT_MS = DeviceChecker.GPU_TIMEOUT_MS
        /** A tile takes well under a few seconds on an accelerator that passed the check; one without an answer for this long has hung. */
        const val TILE_TIMEOUT_MS = 90_000L
    }
}

/** DESIGN.md: Full 4x needs available memory of at least 2.5 times the output buffer. */
object FullScaleBudget {
    /** Java heap a run needs besides the native buffers: the 28 MB per-tile model output plus stitching arrays, with room. */
    const val JAVA_HEAP_BYTES = 64L * 1024 * 1024

    /** 2.5x the ARGB_8888 output Bitmap: covers the output, the decoded input, the preview steps and the engine. */
    fun nativeBytesNeeded(outputPixels: Long): Long = outputPixels * 4 * 5 / 2

    /**
     * [availMem], [lowMemoryThreshold] and [lowMemory] come from ActivityManager.MemoryInfo. The need is counted
     * above Android's low-memory threshold, so the run does not push the system into killing apps.
     */
    fun fits(outputPixels: Long, availMem: Long, lowMemoryThreshold: Long, lowMemory: Boolean, javaHeapFree: Long): Boolean =
        !lowMemory && availMem - lowMemoryThreshold >= nativeBytesNeeded(outputPixels) && javaHeapFree >= JAVA_HEAP_BYTES
}

/** Free space a result needs in the cache folder, and how a full disk shows up when writing it. */
object StorageBudget {
    /** Below this much free space after a failed write, the disk was full. */
    const val FULL_DISK_BYTES = 16L * 1024 * 1024

    /** A quality-95 JPEG of upscaled content, per pixel, rounded up (0.2 to 0.4 is typical). */
    private const val JPEG_BYTES_PER_PIXEL = 0.5

    /** The output JPEG twice (the EXIF rewrite briefly keeps a copy) plus room for the preview. */
    fun bytesNeeded(outputPixels: Long): Long = (outputPixels * JPEG_BYTES_PER_PIXEL * 2).toLong() + 16L * 1024 * 1024

    fun notEnoughMessage(needed: Long): String {
        val mb = (needed + 1024 * 1024 - 1) / (1024 * 1024)
        return "Not enough free storage: this photo needs about $mb MB. Free up some space and try again."
    }

    fun isOutOfSpace(error: Throwable, freeBytes: Long): Boolean {
        var t: Throwable? = error
        var depth = 0
        while (t != null && depth < 8) {
            val msg = t.message ?: ""
            if (msg.contains("ENOSPC") || msg.contains("No space left", ignoreCase = true)) return true
            t = t.cause
            depth++
        }
        return freeBytes < FULL_DISK_BYTES
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
