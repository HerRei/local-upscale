import Foundation

public struct PrescaleSize {
    public let factor: Double
    public let width: Int
    public let height: Int
}

public enum SizePolicy {
    /// 4x output must stay <= 48 MP (192 MB RGBA8 buffer), so inputs are capped at 3 MP.
    public static let maxInputPixels = 3_000_000

    public static func target(width: Int, height: Int) -> PrescaleSize {
        precondition(width > 0 && height > 0)
        let pixels = Double(width) * Double(height)
        if pixels <= Double(maxInputPixels) { return PrescaleSize(factor: 1, width: width, height: height) }
        let factor = sqrt(Double(maxInputPixels) / pixels)
        let w = min(maxInputPixels, max(1, Int((Double(width) * factor).rounded(.down))))
        let h = min(maxInputPixels / w, max(1, Int((Double(height) * factor).rounded(.down))))
        return PrescaleSize(factor: factor, width: w, height: h)
    }
}

public enum ETA {
    public static func remaining(completed: Int, total: Int, elapsedSeconds: Double) -> Double? {
        if total <= 0 || completed < 0 || completed > total || elapsedSeconds < 0 || !elapsedSeconds.isFinite { return nil }
        if completed == total { return 0 }
        if completed == 0 { return nil }
        return elapsedSeconds * Double(total - completed) / Double(completed)
    }
}
