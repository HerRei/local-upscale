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
