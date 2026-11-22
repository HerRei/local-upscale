package local.sr

import android.graphics.Bitmap
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File

@RunWith(AndroidJUnit4::class)
class DisplayDecodeInstrumentedTest {
    @Test fun previewDecodeHonorsPixelCap() {
        val ctx = InstrumentationRegistry.getInstrumentation().targetContext
        val file = File(ctx.cacheDir, "display-cap.png")
        val source = Bitmap.createBitmap(6000, 1000, Bitmap.Config.ARGB_8888)
        try {
            file.outputStream().use { source.compress(Bitmap.CompressFormat.PNG, 100, it) }
            val decoded = AndroidUISupport.decodeDisplayBitmap(ctx, file.toURI().toString(), 2048)
            assertNotNull(decoded)
            try {
                assertTrue("Preview exceeded 2048 pixels: ${decoded!!.width}x${decoded.height}",
                    maxOf(decoded.width, decoded.height) <= 2048)
            } finally { decoded?.recycle() }
            val full = AndroidUISupport.decodeDisplayBitmap(ctx, file.toURI().toString(), 0)
            assertNotNull(full)
            try {
                assertEquals(6000, full!!.width)
                assertEquals(1000, full.height)
            } finally { full?.recycle() }
        } finally { source.recycle(); file.delete() }
    }

    @Test fun fullDecodeStaysDrawable() {
        // A full 4x result of a 1600x1200 photo is 123 MB in ARGB_8888, above the 100 MiB draw limit.
        val (w, h) = AndroidUISupport.displayDecodeSize(6400, 4800, 0)
        assertTrue("$w x $h", w.toLong() * h <= AndroidUISupport.MAX_DRAW_PIXELS)
        assertTrue("$w x $h", w > 5000 && Math.abs(w * 3 - h * 4) <= 4)
        assertEquals(Pair(8192, 2048), AndroidUISupport.displayDecodeSize(16384, 4096, 0))
        assertEquals(Pair(1600, 1200), AndroidUISupport.displayDecodeSize(1600, 1200, 0))
        assertEquals(Pair(2048, 1536), AndroidUISupport.displayDecodeSize(6400, 4800, 2048))
    }
}
