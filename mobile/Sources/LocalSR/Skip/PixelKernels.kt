package local.sr

import local.srcore.Tile
import kotlin.math.log10

/**
 * Pixel kernels for the Android hot path. Real Kotlin on primitive arrays: the transpiled core
 * (PixelMath, TileStitcher) works on boxed skip.lib.Array and is far too slow for 48 MP.
 * Semantics mirror the core exactly (the unit test compares them): same clamp, same rounding,
 * same edge replication. Layout is NHWC (the LiteRT models), pixels are ARGB_8888 ints.
 */
object PixelKernels {
    const val WINDOW = 384
    const val SCALE = 4
    const val TILE_OUT = WINDOW * SCALE
    const val HALO = 16
    const val CORE = WINDOW - 2 * HALO

    /** Width of the image region a tile's window reads (the window is shifted inside the image, so it only exceeds the image when the image is smaller than the window). */
    fun regionWidth(imageWidth: Int, tile: Tile, window: Int = WINDOW): Int = minOf(window, imageWidth - tile.windowX)
    fun regionHeight(imageHeight: Int, tile: Tile, window: Int = WINDOW): Int = minOf(window, imageHeight - tile.windowY)

    /**
     * Fills [out] (window*window*3 floats, NHWC, 0...1) for [tile]. [region] holds the ARGB pixels of the
     * image rectangle (tile.windowX, tile.windowY, regionWidth, regionHeight), row-major with stride regionWidth.
     * Out-of-image coordinates (images smaller than the window) replicate the edge pixel, like PixelMath.extractWindow.
     */
    fun extractWindow(region: IntArray, imageWidth: Int, imageHeight: Int, tile: Tile, out: FloatArray, window: Int = WINDOW) {
        require(out.size == window * window * 3) { "out has ${out.size} floats, expected ${window * window * 3}" }
        val regionX = tile.windowX
        val regionY = tile.windowY
        val regionW = regionWidth(imageWidth, tile, window)
        val regionH = regionHeight(imageHeight, tile, window)
        require(region.size >= regionW * regionH) { "region too small" }
        var o = 0
        for (y in 0 until window) {
            val sy = (tile.windowY + y - tile.padTop).coerceIn(0, imageHeight - 1) - regionY
            require(sy in 0 until regionH) { "row outside region" }
            val rowBase = sy * regionW
            for (x in 0 until window) {
                val sx = (tile.windowX + x - tile.padLeft).coerceIn(0, imageWidth - 1) - regionX
                require(sx in 0 until regionW) { "column outside region" }
                val p = region[rowBase + sx]
                out[o] = ((p shr 16) and 0xFF) / 255f
                out[o + 1] = ((p shr 8) and 0xFF) / 255f
                out[o + 2] = (p and 0xFF) / 255f
                o += 3
            }
        }
    }

    /** Converts one NHWC float to 0...255: NaN/Inf -> 0, clamp, round half up (== Swift .rounded() for non-negative values). */
    private fun toByte(v: Float): Int {
        if (v.isNaN() || v.isInfinite()) return 0
        val c = if (v < 0f) 0f else if (v > 1f) 1f else v
        return Math.round(c * 255f)
    }

    /**
     * Copies the core region of [tile] from the model output [tileOut] (NHWC, tileSize x tileSize x 3) into
     * [dest] (ARGB, row-major, stride tile.outputWidth, size >= outputWidth*outputHeight).
     */
    fun stitchRegion(tileOut: FloatArray, tile: Tile, dest: IntArray, tileSize: Int = TILE_OUT, outputScale: Int = 4, modelScale: Int = 4) {
        require(tileOut.size == tileSize * tileSize * 3) { "tile output has ${tileOut.size} floats" }
        require(dest.size >= tile.outputWidth * tile.outputHeight) { "dest too small" }
        require((modelScale == 1 && outputScale == 1) || (modelScale == 4 && (outputScale == 2 || outputScale == 4)))
        val reduction = modelScale / outputScale
        require((tile.sourceX + tile.outputWidth) * reduction <= tileSize && (tile.sourceY + tile.outputHeight) * reduction <= tileSize) { "core outside tile output" }
        var d = 0
        for (y in 0 until tile.outputHeight) {
            var s = ((tile.sourceY + y) * reduction * tileSize + tile.sourceX * reduction) * 3
            for (x in 0 until tile.outputWidth) {
                fun sample(c: Int): Float = if (reduction == 1) tileOut[s + c] else
                    (tileOut[s + c] + tileOut[s + 3 + c] + tileOut[s + tileSize * 3 + c] + tileOut[s + tileSize * 3 + 3 + c]) / 4f
                dest[d++] = (0xFF shl 24) or (toByte(sample(0)) shl 16) or (toByte(sample(1)) shl 8) or toByte(sample(2))
                s += 3 * reduction
            }
        }
    }

    // MARK: Device-check math (same formulas as PixelMath.psnr / downsample4x / hasNonFinite / isConstant)

    private fun clamp01(v: Float): Float = if (v < 0f) 0f else if (v > 1f) 1f else v

    fun psnr(a: FloatArray, b: FloatArray): Double {
        require(a.isNotEmpty() && a.size == b.size)
        var sum = 0.0
        for (i in a.indices) {
            if (a[i].isNaN() || a[i].isInfinite() || b[i].isNaN() || b[i].isInfinite()) return 0.0
            val d = (clamp01(a[i]) - clamp01(b[i])).toDouble()
            sum += d * d
        }
        if (sum == 0.0) return 99.0
        val value = -10.0 * log10(sum / a.size)
        return if (value < 99.0) value else 99.0
    }

    /** 4x box downsample of an NHWC image. */
    fun downsample4x(input: FloatArray, width: Int, height: Int): FloatArray {
        require(width % 4 == 0 && height % 4 == 0 && input.size == width * height * 3)
        val ow = width / 4
        val oh = height / 4
        val out = FloatArray(ow * oh * 3)
        for (y in 0 until oh) {
            for (x in 0 until ow) {
                for (c in 0 until 3) {
                    var sum = 0f
                    for (dy in 0 until 4) {
                        val row = ((y * 4 + dy) * width + x * 4) * 3 + c
                        for (dx in 0 until 4) sum += input[row + dx * 3]
                    }
                    out[(y * ow + x) * 3 + c] = sum / 16f
                }
            }
        }
        return out
    }

    /**
     * Every [stride]-th pixel of an NHWC image in both directions, without averaging. The device check keeps
     * this instead of the full 28 MB CPU output: point samples keep the error statistics, so the PSNR over the
     * samples estimates the full-image PSNR (a box average would smooth noise away and read up to ~12 dB high).
     */
    fun sampleGrid(input: FloatArray, width: Int, height: Int, stride: Int = 2): FloatArray {
        require(stride >= 1 && input.size == width * height * 3)
        val ow = (width + stride - 1) / stride
        val oh = (height + stride - 1) / stride
        val out = FloatArray(ow * oh * 3)
        var i = 0
        for (y in 0 until oh) {
            for (x in 0 until ow) {
                val src = ((y * stride) * width + x * stride) * 3
                out[i] = input[src]; out[i + 1] = input[src + 1]; out[i + 2] = input[src + 2]
                i += 3
            }
        }
        return out
    }

    fun hasNonFinite(v: FloatArray): Boolean {
        for (x in v) if (x.isNaN() || x.isInfinite()) return true
        return false
    }

    fun isConstant(v: FloatArray): Boolean {
        if (v.isEmpty()) return true
        val first = v[0]
        for (x in v) if (x != first) return false
        return true
    }
}
