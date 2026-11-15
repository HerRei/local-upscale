package local.sr

import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import local.srcore.TilePlanner

/** Image pixels as ARGB ints. Backed by a Bitmap on device, by an IntArray in unit tests. */
interface PixelSource {
    val width: Int
    val height: Int
    /** Fills [out] (row-major, stride w) with the rectangle (x, y, w, h). */
    fun readRegion(x: Int, y: Int, w: Int, h: Int, out: IntArray)
}

interface PixelSink {
    /** Writes the ARGB rectangle (x, y, w, h) taken row-major with stride w from [pixels]. */
    fun writeRegion(x: Int, y: Int, w: Int, h: Int, pixels: IntArray)
}

/** One 384x384 -> 1536x1536 inference. Input and output are NHWC float32 (0...1). The returned array may be reused only after the next call. */
fun interface TileRunner {
    fun run(input: FloatArray): FloatArray
}

/** The tiling loop: plan with the core's TilePlanner, extract windows, run, stitch core regions. */
object TilePipeline {
    /** Number of tiles for an image of this size (core's TilePlanner). */
    fun tileCount(width: Int, height: Int): Int = TilePlanner.plan(width = width, height = height).count

    /**
     * Upscales [source] 4x into [sink] (which must be (4*width) x (4*height)). Checks for coroutine
     * cancellation before every tile and calls [onTile] (done, total) after each one.
     */
    suspend fun process(source: PixelSource, sink: PixelSink, runner: TileRunner, outputScale: Int = 4, onTile: (Int, Int) -> Unit = { _, _ -> }) {
        val plan = TilePlanner.plan(width = source.width, height = source.height, scale = outputScale)
        val total = plan.count
        val region = IntArray(PixelKernels.WINDOW * PixelKernels.WINDOW)
        val window = FloatArray(PixelKernels.WINDOW * PixelKernels.WINDOW * 3)
        val core = IntArray(PixelKernels.CORE * PixelKernels.SCALE * PixelKernels.CORE * PixelKernels.SCALE)
        for (i in 0 until total) {
            currentCoroutineContext().ensureActive()
            val tile = plan[i]
            val rw = PixelKernels.regionWidth(source.width, tile)
            val rh = PixelKernels.regionHeight(source.height, tile)
            source.readRegion(tile.windowX, tile.windowY, rw, rh, region)
            PixelKernels.extractWindow(region, source.width, source.height, tile, window)
            val out = runner.run(window)
            PixelKernels.stitchRegion(out, tile, core, outputScale = outputScale)
            sink.writeRegion(tile.outputX, tile.outputY, tile.outputWidth, tile.outputHeight, core)
            onTile(i + 1, total)
        }
    }
}
