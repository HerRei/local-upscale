package local.sr

import android.app.ActivityManager
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.Shader
import android.provider.MediaStore
import android.util.Log
import androidx.exifinterface.media.ExifInterface
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.async
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import local.srcore.ComputeUnit
import local.srcore.DeviceVerdict
import local.srcore.ModelKey
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import kotlin.math.log10

/**
 * On-device checks of the Android engine: device check, end-to-end upscale with EXIF, orientation,
 * cancellation, the transpiled Swift wrapper, the foreground service and the MediaStore save.
 * Prints verdicts and timings to logcat (tag LocalSRTest).
 */
@RunWith(AndroidJUnit4::class)
class UpscaleInstrumentedTest {
    private val ctx: Context = InstrumentationRegistry.getInstrumentation().targetContext
    private val dir = File(ctx.cacheDir, "test-inputs")

    // A visible activity keeps the app in the foreground state, which Android requires to start a foreground service.
    // Started directly: ActivityScenario's helper activities need androidx.test:monitor in the test APK, which AGP strips because Skip ships it in the app.
    private var activity: android.app.Activity? = null

    @Before fun setUp() {
        dir.deleteRecursively(); dir.mkdirs()
        activity = InstrumentationRegistry.getInstrumentation().startActivitySync(Intent(ctx, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }
    // All tests share one process; loaded models must not pile up across tests.
    private val upscalers = ArrayList<AndroidUpscaler>()
    private fun newUpscaler() = AndroidUpscaler(ctx).also { upscalers.add(it) }

    @After fun tearDown() {
        runBlocking { for (u in upscalers) u.release() }
        activity?.finish(); dir.deleteRecursively()
    }

    private fun log(msg: String) { Log.i(TAG, msg) }

    /** A synthetic photo-ish JPEG with gradients, shapes, fine lines and text, plus EXIF date, GPS and camera fields. */
    private fun makeInput(name: String, w: Int, h: Int, orientation: Int = ExifInterface.ORIENTATION_NORMAL): File {
        val bmp = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888)
        val c = Canvas(bmp)
        val p = Paint(Paint.ANTI_ALIAS_FLAG)
        p.shader = LinearGradient(0f, 0f, w.toFloat(), h.toFloat(), intArrayOf(Color.rgb(20, 60, 140), Color.rgb(240, 180, 90), Color.rgb(40, 140, 80)), null, Shader.TileMode.CLAMP)
        c.drawRect(0f, 0f, w.toFloat(), h.toFloat(), p)
        p.shader = null
        p.color = Color.WHITE; p.style = Paint.Style.STROKE; p.strokeWidth = 1.5f
        for (i in 0 until 40) c.drawLine(0f, h * i / 40f, w * (1f - i / 40f), h.toFloat(), p)
        p.style = Paint.Style.FILL
        p.color = Color.rgb(200, 30, 50); c.drawCircle(w * 0.3f, h * 0.4f, h * 0.18f, p)
        p.color = Color.rgb(250, 240, 220); c.drawCircle(w * 0.7f, h * 0.55f, h * 0.12f, p)
        p.color = Color.BLACK; p.textSize = h * 0.12f; c.drawText("LocalSR 4x", w * 0.08f, h * 0.9f, p)
        val file = File(dir, name)
        file.outputStream().use { bmp.compress(Bitmap.CompressFormat.JPEG, 95, it) }
        bmp.recycle()
        val e = ExifInterface(file)
        e.setAttribute(ExifInterface.TAG_DATETIME_ORIGINAL, "2024:07:14 18:30:05")
        e.setAttribute(ExifInterface.TAG_DATETIME, "2024:07:14 18:30:05")
        e.setAttribute(ExifInterface.TAG_OFFSET_TIME_ORIGINAL, "+02:00")
        e.setAttribute(ExifInterface.TAG_MAKE, "TestMaker")
        e.setAttribute(ExifInterface.TAG_MODEL, "Synthetic 1")
        e.setAttribute(ExifInterface.TAG_F_NUMBER, "18/10")
        e.setAttribute(ExifInterface.TAG_EXPOSURE_TIME, "1/250")
        e.setAttribute(ExifInterface.TAG_PHOTOGRAPHIC_SENSITIVITY, "100")
        e.setAttribute(ExifInterface.TAG_FOCAL_LENGTH, "5700/1000")
        e.setAttribute(ExifInterface.TAG_ORIENTATION, orientation.toString())
        e.setLatLong(47.5596, 7.5886)
        e.setAltitude(260.0)
        e.saveAttributes()
        return file
    }

    private fun psnr(a: Bitmap, b: Bitmap): Double {
        assertEquals(a.width, b.width); assertEquals(a.height, b.height)
        val pa = IntArray(a.width * a.height); val pb = IntArray(pa.size)
        a.getPixels(pa, 0, a.width, 0, 0, a.width, a.height); b.getPixels(pb, 0, b.width, 0, 0, b.width, b.height)
        var sum = 0.0
        for (i in pa.indices) for (s in intArrayOf(16, 8, 0)) { val d = ((pa[i] shr s) and 255) - ((pb[i] shr s) and 255); sum += d * d }
        return if (sum == 0.0) 99.0 else -10 * log10(sum / (pa.size * 3.0) / (255.0 * 255.0))
    }

    private fun bounds(f: File): Pair<Int, Int> {
        val o = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(f.path, o)
        return o.outWidth to o.outHeight
    }

    @Test
    fun deviceCheckBothModels() = runBlocking<Unit> {
        val checker = DeviceChecker(ctx)
        for (model in listOf(ModelKey.fast, ModelKey.best)) {
            checker.forget(model)
            val report = checker.run(model)
            log("DEVICE CHECK ${model.rawValue}: unit=${report.verdict.unit.rawValue} reason=\"${report.verdict.reason}\"")
            for (l in report.lines) log("  $l")
            assertTrue("CPU must at least pass validation: ${report}", !report.verdict.reason.contains("did not pass validation"))
            assertTrue(report.lines.any { it.startsWith("CPU: load") })
            assertTrue("NPU line missing", report.lines.any { it.startsWith("NPU:") })
            assertTrue("GPU line missing", report.lines.any { it.startsWith("GPU:") })
            // The cache round-trips
            checker.store(model, report.verdict)
            val cached = checker.cached(model)
            assertNotNull(cached); assertEquals(report.verdict.unit, cached!!.unit); assertEquals(report.verdict.reason, cached.reason)
            checker.forget(model)
        }
    }

    @Test
    fun deviceCheckSkipsAUnitThatCrashedTheAppBefore() = runBlocking<Unit> {
        val checker = DeviceChecker(ctx)
        val model = ModelKey.fast
        val prefs = ctx.getSharedPreferences(DeviceChecker.PREFS_NAME, Context.MODE_PRIVATE)
        val failedKey = UnitGuard.failedKey(checker.cacheKey(model))
        val before = prefs.getString(failedKey, null)
        try {
            checker.markFailed(model, ComputeUnit.gpu, "hung in a test")
            assertTrue(ComputeUnit.gpu in checker.failedUnits(model))
            checker.store(model, DeviceVerdict(unit = ComputeUnit.gpu, reason = "stored before the crash"))
            assertNull("a cached verdict naming a failed unit must be ignored", checker.cached(model))
            val report = checker.run(model)
            log("CRASH GUARD: ${report.verdict.unit.rawValue}; ${report.lines}")
            assertTrue(report.lines.any { it.startsWith("GPU: skipped") })
            assertTrue(report.verdict.unit != ComputeUnit.gpu)
        } finally {
            prefs.edit().apply { if (before == null) remove(failedKey) else putString(failedKey, before) }.commit()
            checker.forget(model)
        }
    }

    private fun checkOutput(input: File, res: RawUpscaleResult, expectW: Int, expectH: Int) {
        val out = File(res.outputPath)
        assertTrue(out.isFile && out.length() > 10_000)
        assertEquals(expectW to expectH, bounds(out))
        assertEquals(expectW, res.width); assertEquals(expectH, res.height)
        val prev = File(res.previewPath)
        assertTrue(prev.isFile)
        val (pw, ph) = bounds(prev)
        assertTrue("preview ${pw}x$ph", maxOf(pw, ph) <= 4096)

        // EXIF preserved
        val src = ExifInterface(input)
        val e = ExifInterface(out)
        assertEquals(src.getAttribute(ExifInterface.TAG_DATETIME_ORIGINAL), e.getAttribute(ExifInterface.TAG_DATETIME_ORIGINAL))
        assertEquals("+02:00", e.getAttribute(ExifInterface.TAG_OFFSET_TIME_ORIGINAL))
        assertEquals("TestMaker", e.getAttribute(ExifInterface.TAG_MAKE))
        assertEquals("Synthetic 1", e.getAttribute(ExifInterface.TAG_MODEL))
        assertEquals(src.getAttribute(ExifInterface.TAG_F_NUMBER), e.getAttribute(ExifInterface.TAG_F_NUMBER))
        assertEquals(src.getAttribute(ExifInterface.TAG_EXPOSURE_TIME), e.getAttribute(ExifInterface.TAG_EXPOSURE_TIME))
        assertEquals(src.getAttribute(ExifInterface.TAG_FOCAL_LENGTH), e.getAttribute(ExifInterface.TAG_FOCAL_LENGTH))
        val ll = e.latLong
        assertNotNull("GPS lost", ll)
        assertEquals(47.5596, ll!![0], 1e-4); assertEquals(7.5886, ll[1], 1e-4)
        assertEquals(260.0, e.getAltitude(0.0), 0.5)
        assertEquals(ExifInterface.ORIENTATION_NORMAL, e.getAttributeInt(ExifInterface.TAG_ORIENTATION, -1))
        assertEquals(expectW, e.getAttributeInt(ExifInterface.TAG_PIXEL_X_DIMENSION, -1))
        assertEquals(expectH, e.getAttributeInt(ExifInterface.TAG_PIXEL_Y_DIMENSION, -1))
    }

    @Test
    fun endToEndBothModels() = runBlocking<Unit> {
        val upscaler = newUpscaler()
        val input = makeInput("synthetic_1000x750.jpg", 1000, 750)
        val original = BitmapFactory.decodeFile(input.path)
        for (model in listOf(ModelKey.fast, ModelKey.best)) {
            val verdict = upscaler.prepare(model)
            log("E2E ${model.rawValue}: verdict ${verdict.unit.rawValue} (${verdict.reason})")
            val fractions = ArrayList<Double>()
            val t0 = System.nanoTime()
            val res = upscaler.upscale("file://${input.absolutePath}", model, false) { fractions.add(it) }
            val ms = (System.nanoTime() - t0) / 1e6
            val tiles = TilePipeline.tileCount(1000, 750) // 3 x 3 core tiles
            log("E2E ${model.rawValue}: ${res.width}x${res.height} on ${res.unit.rawValue} in %.1f s (%.0f ms/tile incl. decode/stitch/encode), result in ${res.outputPath}".format(ms / 1000, ms / tiles))
            log("E2E ${model.rawValue}: output ${File(res.outputPath).length() / 1024} KB, preview ${File(res.previewPath).length() / 1024} KB, progress steps ${fractions.size}")
            checkOutput(input, res, 4000, 3000)
            assertEquals(tiles + 2, fractions.size) // 0.0, one per tile, 1.0
            assertEquals(0.0, fractions.first(), 0.0); assertEquals(1.0, fractions.last(), 0.0)
            assertTrue(fractions.zipWithNext().all { it.first <= it.second })

            // Quality: downsampled output close to the input, output not flat, and real detail added
            val out = BitmapFactory.decodeFile(res.outputPath)
            val small = Bitmap.createScaledBitmap(out, 1000, 750, true)
            val q = psnr(small, original)
            log("E2E ${model.rawValue}: PSNR of 4x-downsampled output vs input = %.1f dB".format(q))
            assertTrue("downsample-vs-input PSNR $q", q >= 26.0)
            val seen = HashSet<Int>()
            for (y in 0 until 3000 step 37) for (x in 0 until 4000 step 41) seen.add(out.getPixel(x, y))
            log("E2E ${model.rawValue}: ${seen.size} distinct colours in a coarse sample")
            assertTrue("output looks constant (${seen.size} colours)", seen.size > 200)
            out.recycle(); small.recycle()
            File(res.outputPath).parentFile!!.deleteRecursively()
        }
    }

    @Test
    fun orientationIsAppliedAndReset() = runBlocking<Unit> {
        val upscaler = newUpscaler()
        val input = makeInput("rotated_400x300.jpg", 400, 300, ExifInterface.ORIENTATION_ROTATE_90)
        val res = upscaler.upscale(input.absolutePath, ModelKey.fast, false) { }
        log("ORIENTATION: 400x300 with EXIF orientation 6 -> ${res.width}x${res.height}")
        checkOutput(input, res, 1200, 1600) // portrait after rotation
        File(res.outputPath).parentFile!!.deleteRecursively()
    }

    @Test
    fun tinyImageIsPadded() = runBlocking<Unit> {
        val upscaler = newUpscaler()
        val input = makeInput("tiny_100x60.jpg", 100, 60)
        val res = upscaler.upscale(input.absolutePath, ModelKey.fast, false) { }
        checkOutput(input, res, 400, 240)
        File(res.outputPath).parentFile!!.deleteRecursively()
    }

    @Test
    fun cancellationStopsAndCleansUp() = runBlocking<Unit> {
        val upscaler = newUpscaler()
        val input = makeInput("cancel_1000x750.jpg", 1000, 750)
        val root = File(ctx.cacheDir, "LocalSR")
        val before = root.list()?.toSet() ?: emptySet()
        var steps = 0
        val job = async { upscaler.upscale(input.absolutePath, ModelKey.fast, false) { steps++ } }
        while (steps < 3) delay(20)
        job.cancel()
        try { job.await(); fail("should be cancelled") } catch (e: CancellationException) { /* expected */ }
        log("CANCEL: stopped after $steps progress steps")
        assertTrue("stopped early", steps < 11)
        assertEquals("partial files must be removed", before, root.list()?.toSet() ?: emptySet<String>())
        // The engine still works afterwards.
        val res = upscaler.upscale(input.absolutePath, ModelKey.fast, false) { }
        assertTrue(File(res.outputPath).isFile)
        File(res.outputPath).parentFile!!.deleteRecursively()
    }

    @Suppress("DEPRECATION")
    private fun serviceRunning(): Boolean {
        val am = ctx.getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
        return am.getRunningServices(100).any { it.service.className == UpscaleForegroundService::class.java.name && it.foreground }
    }

    @Test
    fun foregroundServiceRunsDuringUpscaleAndStopsAfter() = runBlocking<Unit> {
        val upscaler = newUpscaler()
        val input = makeInput("fg_1000x750.jpg", 1000, 750)
        assertFalse(serviceRunning())
        var seen = false
        // The batch (not each photo) holds the foreground service: AppModel calls beginBatch/endBatch.
        UpscaleForegroundService.acquire(ctx)
        val res = try {
            upscaler.upscale(input.absolutePath, ModelKey.fast, false) { if (it > 0.1 && !seen) seen = serviceRunning() }
        } finally {
            UpscaleForegroundService.release(ctx)
        }
        log("FOREGROUND SERVICE: foreground while upscaling = $seen")
        assertTrue("foreground service was not running during the upscale", seen)
        delay(500)
        assertFalse("foreground service must stop afterwards", serviceRunning())
        File(res.outputPath).parentFile!!.deleteRecursively()
    }

    @Test
    fun swiftWrapperEndToEndAndSaveToLibrary() = runBlocking<Unit> {
        val service = AndroidUpscaleService() // the transpiled Swift class
        val input = makeInput("wrapper_500x400.jpg", 500, 400)
        val verdict = service.prepare(ModelKey.fast)
        log("WRAPPER: verdict ${verdict.unit.rawValue}")
        val result = service.upscale(skip.foundation.URL(fileURLWithPath = input.absolutePath), ModelKey.fast) { }
        assertEquals(2000, result.width); assertEquals(1600, result.height)
        assertEquals(ModelKey.fast, result.model)
        assertTrue(File(result.outputURL.path).isFile && File(result.previewURL.path).isFile)
        assertEquals(input.absolutePath, result.inputURL.path)

        service.saveToLibrary(result)
        // Find it in MediaStore and check the pattern.
        val projection = arrayOf(MediaStore.Images.Media._ID, MediaStore.Images.Media.RELATIVE_PATH, MediaStore.Images.Media.IS_PENDING, MediaStore.Images.Media.DATE_TAKEN, MediaStore.Images.Media.DISPLAY_NAME, MediaStore.Images.Media.SIZE, MediaStore.Images.Media.WIDTH)
        var found = false
        ctx.contentResolver.query(MediaStore.Images.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY), projection, "${MediaStore.Images.Media.DISPLAY_NAME} LIKE ?", arrayOf("LocalSR_%"), "${MediaStore.Images.Media._ID} DESC")?.use { c ->
            if (c.moveToFirst()) {
                found = true
                val id = c.getLong(0)
                log("MEDIASTORE: id=$id path=${c.getString(1)} pending=${c.getInt(2)} taken=${c.getLong(3)} name=${c.getString(4)} size=${c.getLong(5)} width=${c.getInt(6)}")
                assertEquals("Pictures/LocalSR/", c.getString(1))
                assertEquals(0, c.getInt(2))
                // 2024-07-14 18:30:05 +02:00 = 16:30:05 UTC
                assertEquals(1720974605000L, c.getLong(3))
                assertEquals(2000, c.getInt(6))
                val uri = android.content.ContentUris.withAppendedId(MediaStore.Images.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY), id)
                ctx.contentResolver.openInputStream(uri)!!.use { s ->
                    val e = ExifInterface(s)
                    assertNotNull("GPS lost in the saved copy", e.latLong)
                    assertEquals("TestMaker", e.getAttribute(ExifInterface.TAG_MAKE))
                }
                assertEquals(1, ctx.contentResolver.delete(uri, null, null))
            }
        }
        assertTrue("saved picture not found in MediaStore", found)
        File(result.outputURL.path).parentFile!!.deleteRecursively()
    }

    companion object { const val TAG = "LocalSRTest" }
}
