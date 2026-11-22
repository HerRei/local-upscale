package local.sr

import android.content.Intent
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.util.Log
import androidx.core.content.FileProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.By
import androidx.test.uiautomator.UiDevice
import androidx.test.uiautomator.Until
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeFalse
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File

@RunWith(AndroidJUnit4::class)
class ViewerDecodeInstrumentedTest {
    @Test fun viewerSwitchesDecodedPixelSizes() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        // This drives the phone result viewer. Tablets open the tablet workspace instead, which has its own viewer.
        assumeFalse("phone viewer test; not applicable on a tablet", AndroidUISupport.isTablet(context))
        val device = UiDevice.getInstance(instrumentation)
        // The batch's foreground notification asks for this; the dialog would cover the viewer.
        if (android.os.Build.VERSION.SDK_INT >= 33) {
            instrumentation.uiAutomation.grantRuntimePermission(context.packageName, android.Manifest.permission.POST_NOTIFICATIONS)
        }
        val file = File(context.cacheDir, "LocalSR/viewer-detailed.jpg")
        file.parentFile!!.mkdirs()
        val bitmap = Bitmap.createBitmap(1600, 1200, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(bitmap)
        canvas.drawColor(Color.rgb(54, 93, 120))
        val paint = Paint(Paint.ANTI_ALIAS_FLAG)
        for (y in 0 until 1200 step 5) for (x in 0 until 1600 step 7) {
            val value = (x * 17 + y * 31) % 255
            paint.color = Color.rgb(value, 255 - value, 90)
            canvas.drawRect(x.toFloat(), y.toFloat(), x + 2f, y + 3f, paint)
        }
        paint.color = Color.WHITE; paint.textSize = 44f
        canvas.drawText("LocalSR native detail 1600 x 1200", 60f, 1050f, paint)
        file.outputStream().use { bitmap.compress(Bitmap.CompressFormat.JPEG, 95, it) }
        bitmap.recycle()
        val uri = FileProvider.getUriForFile(context, "${context.packageName}.fileprovider", file)
        val intent = Intent(context, MainActivity::class.java).apply {
            action = Intent.ACTION_SEND
            type = "image/jpeg"
            putExtra(Intent.EXTRA_STREAM, uri)
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        try {
            val activity = instrumentation.startActivitySync(intent)
            try {
                fun waitDescription(prefix: String, expected: String? = null, timeout: Long = 150_000): String {
                    val deadline = System.currentTimeMillis() + timeout
                    do {
                        val description = try {
                            device.findObject(By.descStartsWith(prefix))?.contentDescription
                        } catch (_: androidx.test.uiautomator.StaleObjectException) {
                            null
                        }
                        if (description != null && (expected == null || description == "$prefix $expected")) return description
                        Thread.sleep(500)
                    } while (System.currentTimeMillis() < deadline)
                    val dump = java.io.ByteArrayOutputStream().also { device.dumpWindowHierarchy(it) }.toString()
                    dump.chunked(3000).forEach { Log.i("LocalSRViewerTest", "hierarchy: $it") }
                    throw AssertionError("Timed out waiting for $prefix $expected")
                }
                val up = "viewerUpscaledImage"
                val original = "viewerOriginalImage"
                val preview = waitDescription(up).substringAfter(' ')
                assertTrue("Preview size $preview", preview.split('x').map { it.toInt() }.max() <= 2048)
                waitDescription(original, "1600x1200")
                Log.i("LocalSRViewerTest", "1x upscaled=$preview original=1600x1200")
                val image = device.wait(Until.findObject(By.descStartsWith(up)), 5000)
                assertNotNull(image)
                image.pinchOpen(0.9f, 1000)
                image.pinchOpen(0.9f, 1000)
                val (fw, fh) = AndroidUISupport.displayDecodeSize(6400, 4800, 0)
                waitDescription(up, "${fw}x${fh}")
                waitDescription(original, "1600x1200")
                Log.i("LocalSRViewerTest", "4x upscaled=${fw}x${fh} original=1600x1200")
                device.findObject(By.descStartsWith(up)).pinchClose(0.95f, 1000)
                device.findObject(By.descStartsWith(up)).pinchClose(0.95f, 1000)
                assertEquals("$up $preview", waitDescription(up, preview, 60_000))
                Log.i("LocalSRViewerTest", "1x return upscaled=$preview original=${waitDescription(original).substringAfter(' ')}")
            } finally { activity.finish() }
        } finally { file.delete() }
    }
}
