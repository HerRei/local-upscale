import Foundation

public enum PixelMath {
    public static func extractWindow(input: [UInt8], width: Int, height: Int, tile: Tile, window: Int = 384) -> [Float] {
        precondition(width > 0 && height > 0 && input.count == width * height * 4 && window > 0)
        let plane = window * window
        var result = [Float](repeating: Float(0), count: 3 * plane)
        for y in 0..<window {
            let sy = max(0, min(height - 1, tile.windowY + y - tile.padTop))
            for x in 0..<window {
                let sx = max(0, min(width - 1, tile.windowX + x - tile.padLeft))
                let source = (sy * width + sx) * 4
                let dest = y * window + x
                for c in 0..<3 { result[c * plane + dest] = Float(Int(input[source + c])) / Float(255) }
            }
        }
        return result
    }

    /// Clamps to [0, 1]. Written with explicit Float so it transpiles (no Int literals in min/max).
    public static func clamp01(_ v: Float) -> Float {
        if v < Float(0) { return Float(0) }
        if v > Float(1) { return Float(1) }
        return v
    }

    public static func psnr(_ a: [Float], _ b: [Float]) -> Double {
        precondition(!a.isEmpty && a.count == b.count)
        var sum = 0.0
        for i in 0..<a.count {
            if !a[i].isFinite || !b[i].isFinite { return 0 }
            let d = Double(clamp01(a[i]) - clamp01(b[i]))
            sum += d * d
        }
        if sum == 0.0 { return 99.0 }
        let value: Double = -10.0 * log10(sum / Double(a.count))
        return value < 99.0 ? value : 99.0
    }

    public static func downsample4x(_ input: [Float], width: Int, height: Int) -> [Float] {
        precondition(width > 0 && height > 0 && width % 4 == 0 && height % 4 == 0 && input.count == 3 * width * height)
        let outWidth = width / 4
        let outHeight = height / 4
        var result = [Float](repeating: Float(0), count: 3 * outWidth * outHeight)
        for c in 0..<3 {
            for y in 0..<outHeight {
                for x in 0..<outWidth {
                    var sum: Float = Float(0)
                    for dy in 0..<4 {
                        for dx in 0..<4 { sum += input[c * width * height + (y * 4 + dy) * width + x * 4 + dx] }
                    }
                    result[c * outWidth * outHeight + y * outWidth + x] = sum / Float(16)
                }
            }
        }
        return result
    }

    public static func hasNonFinite(_ values: [Float]) -> Bool {
        for value in values { if !value.isFinite { return true } }
        return false
    }

    public static func isConstant(_ values: [Float]) -> Bool {
        if values.isEmpty { return true }
        for value in values { if value != values[0] { return false } }
        return true
    }
}
