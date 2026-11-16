import Foundation

extension ModelKey {
    public var displayName: String {
        switch self {
        case .best: return "Best"
        case .fast: return "Fast"
        }
    }
}

extension ComputeUnit {
    public var displayName: String {
        switch self {
        case .npu: return "NPU"
        case .gpu: return "GPU"
        case .cpu: return "CPU"
        }
    }

    public var platformDisplayName: String {
        switch self {
        case .npu:
            #if SKIP
            return "NPU"
            #else
            return "Neural Engine"
            #endif
        case .gpu: return "GPU"
        case .cpu: return "CPU"
        }
    }
}

public enum UIFormatting {
    /// The enlargement as shown next to the size: "4", "2", or one decimal when it is not a whole number
    /// (a 24 MP photo is reduced to 12 MP and enlarged 2x, so it grows 1.4x; a 200 MP photo comes out at 0.5x).
    public static func scaleFactorText(inputWidth: Int, inputHeight: Int, outputWidth: Int, outputHeight: Int) -> String {
        let inLong = max(inputWidth, inputHeight)
        if inLong <= 0 { return "1" }
        let ratio = Double(max(outputWidth, outputHeight)) / Double(inLong)
        let tenths = max(1, Int((ratio * 10.0).rounded()))
        return tenths % 10 == 0 ? "\(tenths / 10)" : "\(tenths / 10).\(tenths % 10)"
    }

    /// The small line under the result caption; empty when the photo got the full 4x.
    /// `fullScaleAvailable` is false where Full 4x can't be turned on (Android phones), so the note doesn't point to it.
    public static func scaleNote(inputWidth: Int, inputHeight: Int, outputScale: Int, fullScaleFallback: Bool, fullScaleAvailable: Bool = true) -> String {
        let reduced = Double(inputWidth) * Double(inputHeight) > Double(SizePolicy.maxInputPixels)
        if reduced {
            if fullScaleFallback {
                return "Large photo, reduced to 12 MP first. Not enough memory for Full 4\u{00D7}."
            }
            if outputScale >= 4 {
                return "Large photo, reduced to 12 MP before upscaling."
            }
            return fullScaleAvailable ? "Large photo, reduced to 12 MP first. Full 4\u{00D7} in Settings." : "Large photo, reduced to 12 MP first."
        }
        if outputScale >= 4 { return "" }
        if fullScaleFallback {
            return "2\u{00D7} \u{2013} not enough memory for Full 4\u{00D7}."
        }
        return fullScaleAvailable ? "2\u{00D7} \u{2013} large photo. Full 4\u{00D7} in Settings." : "2\u{00D7} \u{2013} large photo."
    }

    public static func formatSeconds(_ seconds: Double) -> String {
        let clamped = max(0.0, seconds.isFinite ? seconds : 0.0)
        let tenths = Int((clamped * 10.0).rounded())
        let whole = tenths / 10
        let frac = tenths % 10
        return "\(whole).\(frac) s"
    }

    public static func formatMegapixels(width: Int, height: Int) -> String {
        let mp = max(0.0, Double(width) * Double(height) / 1_000_000.0)
        let tenths = Int((mp * 10.0).rounded())
        if tenths < 100 {
            return "\(tenths / 10).\(tenths % 10) MP"
        }
        return "\(Int(mp.rounded())) MP"
    }

    public static func resultDimensionsLine(width: Int, height: Int) -> String {
        return "\(width) \u{00D7} \(height) \u{00B7} \(formatMegapixels(width: width, height: height))"
    }

    public static func resultDetailsLine(model: ModelKey, unit: ComputeUnit, seconds: Double) -> String {
        return "\(model.displayName) \u{00B7} \(unit.platformDisplayName) \u{00B7} \(formatSeconds(seconds))"
    }

    public static func resultAccessibilityCaption(width: Int, height: Int, model: ModelKey, unit: ComputeUnit, seconds: Double) -> String {
        let mp = max(0.0, Double(width) * Double(height) / 1_000_000.0)
        let tenths = Int((mp * 10.0).rounded())
        let mpText = tenths < 100 ? "\(tenths / 10).\(tenths % 10) megapixels" : "\(Int(mp.rounded())) megapixels"
        let clamped = max(0.0, seconds.isFinite ? seconds : 0.0)
        let secTenths = Int((clamped * 10.0).rounded())
        let secText = "\(secTenths / 10).\(secTenths % 10) seconds"
        return "\(width) by \(height), \(mpText), \(model.displayName), \(unit.platformDisplayName), \(secText)"
    }

    public static func resultSummary(width: Int, height: Int, model: ModelKey, unit: ComputeUnit, seconds: Double) -> String {
        return "\(width) \u{00D7} \(height) \u{00B7} \(model.displayName) \u{00B7} \(unit.displayName) \u{00B7} \(formatSeconds(seconds))"
    }

    public static func formatETA(_ seconds: Double?) -> String? {
        guard let seconds, seconds.isFinite, seconds > 0.0 else { return nil }
        let total = max(1, Int(seconds.rounded()))
        if total < 60 {
            return "\(total) s remaining"
        }
        let mins = total / 60
        let secs = total % 60
        if secs == 0 {
            return "\(mins) min remaining"
        }
        return "\(mins) min \(secs) s remaining"
    }
}
