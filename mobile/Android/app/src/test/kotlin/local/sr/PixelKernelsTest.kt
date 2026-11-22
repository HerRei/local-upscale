package local.sr

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.runBlocking
import local.srcore.PixelMath
import local.srcore.Tile
import local.srcore.TilePlanner
import local.srcore.TileStitcher
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import skip.lib.Array
import skip.lib.InOut
import java.util.Random

/**
 * The Android tiling glue (PixelKernels, TilePipeline: primitive-array Kotlin) against the shared core
 * (TilePlanner, PixelMath, TileStitcher: the transpiled Swift reference). Pure JVM, no device.
 */
class PixelKernelsTest {
    private fun randomImage(w: Int, h: Int, seed: Long): IntArray {
        val r = Random(seed)
        return IntArray(w * h) { (0xFF shl 24) or (r.nextInt(256) shl 16) or (r.nextInt(256) shl 8) or r.nextInt(256) }
    }

    /** Region of the image the tile's window reads, like PixelSource.readRegion. */
    private fun region(img: IntArray, w: Int, h: Int, tile: Tile, window: Int): IntArray {
        val rw = PixelKernels.regionWidth(w, tile, window)
        val rh = PixelKernels.regionHeight(h, tile, window)
        val out = IntArray(rw * rh)
        for (y in 0 until rh) for (x in 0 until rw) out[y * rw + x] = img[(tile.windowY + y) * w + tile.windowX + x]
        return out
    }

    private fun rgba(img: IntArray): Array<UByte> {
        var a = Array<UByte>(repeating = 0.toUByte(), count = img.size * 4)
        for (i in img.indices) {
            a[i * 4] = ((img[i] shr 16) and 0xFF).toUByte()
            a[i * 4 + 1] = ((img[i] shr 8) and 0xFF).toUByte()
            a[i * 4 + 2] = (img[i] and 0xFF).toUByte()
            a[i * 4 + 3] = 255.toUByte()
        }
        return a
    }

    @Test
    fun planMatchesCoreForRealSizes() {
        // The Kotlin pipeline uses the core's plan directly; pin the shape the glue relies on.
        for ((w, h) in listOf(100 to 100, 384 to 384, 385 to 700, 1000 to 750, 4032 to 3024)) {
            val plan = TilePlanner.plan(width = w, height = h)
            assertEquals(TilePipeline.tileCount(w, h), plan.count)
            var area = 0L
            for (i in 0 until plan.count) {
                val t = plan[i]
                assertTrue(PixelKernels.regionWidth(w, t) in 1..PixelKernels.WINDOW)
                assertTrue(t.windowX + PixelKernels.regionWidth(w, t) <= w)
                assertTrue(t.sourceX + t.outputWidth <= PixelKernels.TILE_OUT)
                area += t.outputWidth.toLong() * t.outputHeight
            }
            assertEquals(w.toLong() * h * 16, area)
        }
        assertEquals(12 * 9, TilePipeline.tileCount(4032, 3024))
    }

    @Test
    fun extractWindowEqualsPixelMath() {
        // (width, height, window, halo): the real 384/16 window and small windows that exercise padding and shifted edge windows.
        val cases = listOf(listOf(100, 100, 384, 16), listOf(384, 384, 384, 16), listOf(385, 700, 384, 16), listOf(1000, 750, 384, 16),
            listOf(7, 5, 24, 2), listOf(24, 24, 24, 2), listOf(25, 61, 24, 2), listOf(100, 37, 24, 2))
        for ((idx, c) in cases.withIndex()) {
            val (w, h, window, halo) = c
            val img = randomImage(w, h, 100L + idx)
            val core = rgba(img)
            val plan = TilePlanner.plan(width = w, height = h, window = window, halo = halo)
            val mine = FloatArray(window * window * 3)
            for (i in 0 until plan.count) {
                val tile = plan[i]
                PixelKernels.extractWindow(region(img, w, h, tile, window), w, h, tile, mine, window)
                val ref = PixelMath.extractWindow(input = core, width = w, height = h, tile = tile, window = window)
                val plane = window * window
                for (p in 0 until plane) for (ch in 0 until 3) {
                    assertEquals("case $idx tile $i px $p ch $ch", ref[ch * plane + p], mine[p * 3 + ch], 0f)
                }
            }
        }
    }

    @Test
    fun stitchRegionEqualsTileStitcher() {
        val cases = listOf(listOf(7, 5, 24, 2), listOf(24, 24, 24, 2), listOf(25, 61, 24, 2), listOf(100, 37, 24, 2), listOf(60, 60, 24, 2))
        for (scale in listOf(2, 4)) for ((idx, c) in cases.withIndex()) {
            val (w, h, window, halo) = c
            val tileSize = window * 4
            val plan = TilePlanner.plan(width = w, height = h, window = window, halo = halo, scale = scale)
            val r = Random(7L + idx)
            val ow = w * scale
            val oh = h * scale
            var refOut = Array<UByte>(repeating = 0.toUByte(), count = ow * oh * 4)
            val myOut = IntArray(ow * oh)
            // No exact x.5 values here: the transpiled core's .rounded() is round-half-even (Skip) while Swift's is half-away; see roundingIsHalfUpLikeSwift.
            val special = floatArrayOf(Float.NaN, Float.POSITIVE_INFINITY, Float.NEGATIVE_INFINITY, -0.5f, 1.5f, 0f, 1f)
            for (i in 0 until plan.count) {
                val tile = plan[i]
                val nhwc = FloatArray(tileSize * tileSize * 3) { if (r.nextInt(20) == 0) special[r.nextInt(special.size)] else (r.nextInt(256) + (r.nextInt(9) - 4) / 10f) / 255f }
                // core wants planar
                val plane = tileSize * tileSize
                var planar = Array<Float>(repeating = 0f, count = 3 * plane)
                for (p in 0 until plane) for (ch in 0 until 3) planar[ch * plane + p] = nhwc[p * 3 + ch]
                val io = InOut<Array<UByte>>({ refOut }, { refOut = it })
                TileStitcher.stitch(tile = tile, tileOutput = planar, output = io, outputWidth = ow, tileSize = tileSize, outputScale = scale)

                val dest = IntArray(tile.outputWidth * tile.outputHeight)
                PixelKernels.stitchRegion(nhwc, tile, dest, tileSize, scale)
                for (y in 0 until tile.outputHeight) for (x in 0 until tile.outputWidth) myOut[(tile.outputY + y) * ow + tile.outputX + x] = dest[y * tile.outputWidth + x]
            }
            for (p in 0 until ow * oh) {
                val argb = myOut[p]
                assertEquals("case $idx px $p r", refOut[p * 4].toInt(), (argb shr 16) and 0xFF)
                assertEquals("case $idx px $p g", refOut[p * 4 + 1].toInt(), (argb shr 8) and 0xFF)
                assertEquals("case $idx px $p b", refOut[p * 4 + 2].toInt(), argb and 0xFF)
                assertEquals(255, (argb ushr 24))
            }
        }
    }

    @Test
    fun roundingIsHalfUpLikeSwift() {
        // Swift's FloatingPoint.rounded() (iOS reference) rounds .5 away from zero; so does the Android stitcher.
        val tile = TilePlanner.plan(width = 1, height = 1, window = 24, halo = 2)[0]
        val out = FloatArray(96 * 96 * 3) // zeros
        out[0] = 0.5f / 255f; out[1] = 127.5f / 255f; out[2] = 254.5f / 255f
        val dest = IntArray(tile.outputWidth * tile.outputHeight)
        PixelKernels.stitchRegion(out, tile, dest, 96)
        val expectR = Math.round(((0.5f / 255f) * 255f)) // 0.5 (or 0.49999997 if the division is inexact)
        assertEquals(expectR, (dest[0] shr 16) and 0xFF)
        assertEquals(Math.round(((127.5f / 255f) * 255f)), (dest[0] shr 8) and 0xFF)
        assertEquals(255, dest[0] ushr 24)
    }

    @Test
    fun deviceCheckMathEqualsPixelMath() {
        val r = Random(5)
        val a = FloatArray(4 * 4 * 3) { r.nextFloat() }
        val b = FloatArray(a.size) { a[it] + (r.nextFloat() - 0.5f) * 0.02f }
        assertEquals(PixelMath.psnr(arrayOfFloats(a), arrayOfFloats(b)), PixelKernels.psnr(a, b), 1e-9)
        assertEquals(99.0, PixelKernels.psnr(a, a), 0.0)
        val bad = a.copyOf(); bad[3] = Float.NaN
        assertEquals(0.0, PixelKernels.psnr(a, bad), 0.0)
        assertTrue(PixelKernels.hasNonFinite(bad)); assertTrue(!PixelKernels.hasNonFinite(a))
        assertTrue(PixelKernels.isConstant(FloatArray(10) { 0.25f })); assertTrue(!PixelKernels.isConstant(a))

        // 4x box downsample, NHWC vs the core's planar version
        val w = 8; val h = 12
        val nhwc = FloatArray(w * h * 3) { r.nextFloat() }
        var planar = Array<Float>(repeating = 0f, count = nhwc.size)
        for (p in 0 until w * h) for (ch in 0 until 3) planar[ch * w * h + p] = nhwc[p * 3 + ch]
        val ref = PixelMath.downsample4x(planar, width = w, height = h)
        val mine = PixelKernels.downsample4x(nhwc, w, h)
        val ow = w / 4; val oh = h / 4
        for (p in 0 until ow * oh) for (ch in 0 until 3) assertEquals(ref[ch * ow * oh + p], mine[p * 3 + ch], 0f)
    }

    @Test
    fun deviceCheckSampledPsnrMatchesFullPsnrForNoise() {
        // Accelerator differences are mostly pixel-level noise (FP16 rounding). A box average would hide it;
        // the point-sampled reference must give the same PSNR as the full comparison.
        val w = 64; val h = 64
        val rnd = java.util.Random(7)
        val cpu = FloatArray(w * h * 3) { 0.2f + 0.6f * rnd.nextFloat() }
        val candidate = FloatArray(cpu.size) { cpu[it] + (rnd.nextGaussian() * 0.01).toFloat() }
        val full = PixelKernels.psnr(cpu, candidate)
        val sampled = PixelKernels.psnr(PixelKernels.sampleGrid(cpu, w, h), PixelKernels.sampleGrid(candidate, w, h))
        assertEquals(full, sampled, 0.5)
        assertEquals(32 * 32 * 3, PixelKernels.sampleGrid(cpu, w, h).size)
        // Noise at 0.01 (about 40 dB) must fail the 45 dB threshold on the samples too; the box average would pass it.
        assertTrue(sampled < 45.0)
        assertTrue(PixelKernels.psnr(PixelKernels.downsample4x(cpu, w, h), PixelKernels.downsample4x(candidate, w, h)) > 45.0)
        // Small noise passes.
        val good = FloatArray(cpu.size) { cpu[it] + (rnd.nextGaussian() * 0.002).toFloat() }
        assertTrue(PixelKernels.psnr(PixelKernels.sampleGrid(cpu, w, h), PixelKernels.sampleGrid(good, w, h)) >= 45.0)
    }

    private fun arrayOfFloats(a: FloatArray): Array<Float> {
        var out = Array<Float>(repeating = 0f, count = a.size)
        for (i in a.indices) out[i] = a[i]
        return out
    }

    /** Pixel source/sink over plain int arrays. */
    private class ArraySource(override val width: Int, override val height: Int, val px: IntArray) : PixelSource {
        override fun readRegion(x: Int, y: Int, w: Int, h: Int, out: IntArray) {
            for (yy in 0 until h) for (xx in 0 until w) out[yy * w + xx] = px[(y + yy) * width + x + xx]
        }
    }

    private class ArraySink(val width: Int, val height: Int) : PixelSink {
        val px = IntArray(width * height)
        override fun writeRegion(x: Int, y: Int, w: Int, h: Int, pixels: IntArray) {
            for (yy in 0 until h) for (xx in 0 until w) px[(y + yy) * width + x + xx] = pixels[yy * w + xx]
        }
    }

    /** Fake model: nearest-neighbour 4x on the window. The stitched image must equal the nearest-neighbour upscale of the whole input. */
    private val nearest = TileRunner { input ->
        val n = PixelKernels.WINDOW
        val o = PixelKernels.TILE_OUT
        val out = FloatArray(o * o * 3)
        for (y in 0 until o) for (x in 0 until o) for (ch in 0 until 3) out[(y * o + x) * 3 + ch] = input[((y / 4) * n + x / 4) * 3 + ch]
        out
    }

    @Test
    fun pipelineStitchesNearestNeighbourExactly() = runBlocking<Unit> {
        for ((idx, size) in listOf(100 to 100, 300 to 20, 384 to 384, 385 to 400, 700 to 380, 800 to 750).withIndex()) {
            val (w, h) = size
            val img = randomImage(w, h, 42L + idx)
            val sink = ArraySink(w * 4, h * 4)
            val calls = ArrayList<Pair<Int, Int>>()
            TilePipeline.process(ArraySource(w, h, img), sink, nearest) { d, t -> calls.add(d to t) }
            assertEquals(TilePipeline.tileCount(w, h), calls.size)
            assertEquals(calls.size to calls.size, calls.last())
            for (y in 0 until h * 4) for (x in 0 until w * 4) {
                assertEquals("size ${w}x$h at $x,$y", img[(y / 4) * w + x / 4], sink.px[y * w * 4 + x])
            }
        }
    }

    @Test
    fun restorationWindow256StitchesExactly() = runBlocking<Unit> {
        for ((w, h) in listOf(17 to 19, 256 to 256, 480 to 257)) {
            val image = randomImage(w, h, 71)
            val sink = ArraySink(w, h)
            TilePipeline.process(ArraySource(w, h, image), sink, TileRunner { it },
                outputScale = 1, windowSize = 256, halo = 16, modelScale = 1)
            org.junit.Assert.assertArrayEquals(image, sink.px)
        }
    }

    @Test
    fun pipelineStopsBetweenTilesWhenCancelled() {
        val w = 1000; val h = 750
        val sink = ArraySink(w * 4, h * 4)
        var ran = 0
        val job = Job()
        val total = TilePipeline.tileCount(w, h)
        try {
            runBlocking(job) {
                TilePipeline.process(ArraySource(w, h, randomImage(w, h, 1)), sink, { input -> ran++; nearest.run(input) }) { done, _ ->
                    if (done == 2) job.cancel()
                }
            }
            fail("expected cancellation")
        } catch (e: CancellationException) {
            // expected
        }
        assertEquals(2, ran)
        assertTrue(ran < total)
    }

    @Test
    fun decodeSizeKeepsOutputWithin48MP() {
        for ((w, h) in listOf(1000 to 750, 4000 to 3000, 8000 to 6000, 12000 to 1000, 20000 to 20000)) {
            val (dw, dh) = AndroidCodec.decodeSize(w, h)
            assertTrue("${dw}x$dh from ${w}x$h", dw.toLong() * dh * (if (w.toLong() * h <= 3_000_000) 16 else 4) <= AndroidCodec.MAX_OUTPUT_PIXELS)
            assertTrue(dw <= w && dh <= h && dw >= 1 && dh >= 1)
            // aspect ratio kept within a pixel of rounding
            assertEquals(w.toDouble() / h, dw.toDouble() / dh, w.toDouble() / h * (2.0 / minOf(dw, dh)))
        }
        assertEquals(1000 to 750, AndroidCodec.decodeSize(1000, 750))
    }
}
