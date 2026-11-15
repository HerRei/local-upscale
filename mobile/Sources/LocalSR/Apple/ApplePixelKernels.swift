#if !SKIP
import Foundation
import Dispatch
import LocalSRCore

/// Where the model's FP16 output tensor lives and how it is laid out (in elements, not bytes).
/// Valid until the next prediction of the engine that produced it.
struct TileOutputView: @unchecked Sendable {
    let base: UnsafePointer<Float16>
    let channelStride: Int
    let rowStride: Int
    let columnStride: Int
}

/// Pointer-loop equivalents of `PixelMath.extractWindow` and `TileStitcher.stitch` that read and write the
/// model's FP16 tensors directly. Same semantics as the core versions (the equivalence is tested in
/// AppleUpscaleTests): edge-replicated window, non-finite -> 0, clamp to [0, 1], round half away from zero,
/// alpha 255. They exist because the core's [Float] version moves 28 MB per tile through Swift arrays.
enum ApplePixelKernels {
    /// 256 FP16 values k / 255, computed in Float exactly like `PixelMath.extractWindow`.
    nonisolated(unsafe) static let byteToHalf: UnsafeMutablePointer<Float16> = {
        let p = UnsafeMutablePointer<Float16>.allocate(capacity: 256)
        for i in 0..<256 { p[i] = Float16(Float(i) / Float(255)) }
        return p
    }()

    /// Fills `dst` (planar RGB, `window` x `window`, FP16) from an RGBA8 image for one tile.
    static func fillWindow(source: UnsafePointer<UInt8>, width: Int, height: Int, tile: Tile, window: Int, dst: UnsafeMutablePointer<Float16>) {
        let plane = window * window
        let lut = byteToHalf
        let r = dst, g = dst + plane, b = dst + 2 * plane
        for y in 0..<window {
            let sy = max(0, min(height - 1, tile.windowY + y - tile.padTop))
            let row = source + sy * width * 4
            let out = y * window
            for x in 0..<window {
                let sx = max(0, min(width - 1, tile.windowX + x - tile.padLeft))
                let p = row + sx * 4
                r[out + x] = lut[Int(p[0])]
                g[out + x] = lut[Int(p[1])]
                b[out + x] = lut[Int(p[2])]
            }
        }
    }

    @inline(__always)
    static func byte(_ v: Float16) -> UInt8 {
        let f = Float(v)
        if !f.isFinite { return 0 }
        return UInt8(Int((PixelMath.clamp01(f) * Float(255)).rounded()))
    }

    /// FP16 row -> clamped 8-bit row, 8 lanes at a time.
    @inline(__always)
    static func convertRow(_ src: UnsafePointer<Float16>, _ dst: UnsafeMutablePointer<UInt8>, count: Int) {
        var i = 0
        let raw = UnsafeRawPointer(src)
        let zero = SIMD8<Float>(repeating: 0), one = SIMD8<Float>(repeating: 1), k = SIMD8<Float>(repeating: 255)
        while i + 8 <= count {
            let h = raw.loadUnaligned(fromByteOffset: i * 2, as: SIMD8<Float16>.self)
            var f = SIMD8<Float>(h)
            let finite = (f .< SIMD8<Float>(repeating: .infinity)) .& (f .> SIMD8<Float>(repeating: -.infinity))
            f.replace(with: zero, where: .!finite)
            f = pointwiseMin(pointwiseMax(f, zero), one) * k
            let u = SIMD8<UInt8>(f, rounding: .toNearestOrAwayFromZero)
            UnsafeMutableRawPointer(dst).storeBytes(of: u, toByteOffset: i, as: SIMD8<UInt8>.self)
            i += 8
        }
        while i < count { dst[i] = byte(src[i]); i += 1 }
    }

    /// Copies the tile's core region from the FP16 output into the RGBA8 output image (stride `outputWidth * 4`).
    static func stitch(tile: Tile, output: TileOutputView, image: UnsafeMutablePointer<UInt8>, outputWidth: Int, outputScale: Int = 4, modelScale: Int = 4) {
        precondition((modelScale == 1 && outputScale == 1) || (modelScale == 4 && (outputScale == 2 || outputScale == 4)))
        let reduction = modelScale / outputScale
        let rowsPerChunk = 32
        let chunks = (tile.outputHeight + rowsPerChunk - 1) / rowsPerChunk
        let w = tile.outputWidth
        let outputHeight = tile.outputHeight, sourceX = tile.sourceX, sourceY = tile.sourceY, outputX = tile.outputX, outputY = tile.outputY
        let base = output.base, channelStride = output.channelStride, rowStride = output.rowStride, columnStride = output.columnStride
        nonisolated(unsafe) let basePointer = base
        // `nonisolated(unsafe)` pointers are fine here: chunks write disjoint rows.
        nonisolated(unsafe) let image = image
        DispatchQueue.concurrentPerform(iterations: chunks) { chunk in
            let scratch = UnsafeMutablePointer<UInt8>.allocate(capacity: 3 * w)
            defer { scratch.deallocate() }
            let y0 = chunk * rowsPerChunk
            let y1 = min(outputHeight, y0 + rowsPerChunk)
            for y in y0..<y1 {
                let srcRow = (sourceY + y) * reduction * rowStride + sourceX * reduction * columnStride
                for c in 0..<3 {
                    let p = basePointer + c * channelStride + srcRow
                    if reduction == 2 {
                        for x in 0..<w {
                            let q = p + x * 2 * columnStride
                            let v = (Float(q[0]) + Float(q[columnStride]) + Float(q[rowStride]) + Float(q[rowStride + columnStride])) / 4
                            scratch[c * w + x] = v.isFinite ? UInt8(Int((PixelMath.clamp01(v) * 255).rounded())) : 0
                        }
                    } else if columnStride == 1 {
                        convertRow(p, scratch + c * w, count: w)
                    } else {
                        for x in 0..<w { scratch[c * w + x] = byte(p[x * columnStride]) }
                    }
                }
                let dst = UnsafeMutableRawPointer(image + ((outputY + y) * outputWidth + outputX) * 4)
                let r = scratch, g = scratch + w, b = scratch + 2 * w
                for x in 0..<w {
                    let v = UInt32(r[x]) | (UInt32(g[x]) << 8) | (UInt32(b[x]) << 16) | 0xFF00_0000
                    dst.storeBytes(of: v, toByteOffset: x * 4, as: UInt32.self)
                }
            }
        }
    }
}
#endif
