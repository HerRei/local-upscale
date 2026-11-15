public struct DeviceMeasurement {
    public var loadMs: Double
    public var tileMs: Double
    public var psnrVsCPU: Double
    public var psnrDownsampleVsInput: Double
    public var hasNaN: Bool
    public var isConstant: Bool
    public var placementAllNPU: Bool
    public var timedOut: Bool

    public init(loadMs: Double, tileMs: Double, psnrVsCPU: Double, psnrDownsampleVsInput: Double, hasNaN: Bool = false, isConstant: Bool = false, placementAllNPU: Bool = true, timedOut: Bool = false) {
        self.loadMs = loadMs
        self.tileMs = tileMs
        self.psnrVsCPU = psnrVsCPU
        self.psnrDownsampleVsInput = psnrDownsampleVsInput
        self.hasNaN = hasNaN
        self.isConstant = isConstant
        self.placementAllNPU = placementAllNPU
        self.timedOut = timedOut
    }
}

public struct DeviceVerdict: Codable, Sendable {
    public let unit: ComputeUnit
    public let reason: String
    public init(unit: ComputeUnit, reason: String) { self.unit = unit; self.reason = reason }
}

public enum DeviceCheck {
    private static func passes(_ m: DeviceMeasurement?) -> Bool {
        guard let m = m else { return false }
        return !m.timedOut && !m.hasNaN && !m.isConstant && m.psnrVsCPU >= 45 && m.psnrDownsampleVsInput >= 26 && m.tileMs > 0 && m.tileMs.isFinite
    }
    public static func decide(cpu: DeviceMeasurement?, gpu: DeviceMeasurement?, npu: DeviceMeasurement?) -> DeviceVerdict {
        let gpuPass = passes(gpu)
        if let n = npu {
            if n.timedOut || n.loadMs > 15000 {
                if gpuPass { return DeviceVerdict(unit: .gpu, reason: "NPU load timed out; using GPU for this session and retrying later.") }
                return DeviceVerdict(unit: .cpu, reason: "NPU load timed out and GPU failed; using CPU for this session and retrying later.")
            }
            if n.placementAllNPU && passes(n) {
                if let g = gpu, gpuPass && n.tileMs >= 1.3 * g.tileMs {
                    return DeviceVerdict(unit: .gpu, reason: "GPU passed and is at least 1.3 times faster than NPU.")
                }
                return DeviceVerdict(unit: .npu, reason: "NPU placement and image quality checks passed.")
            }
        }
        if gpuPass { return DeviceVerdict(unit: .gpu, reason: "NPU unavailable or failed; GPU passed.") }
        if passes(cpu) { return DeviceVerdict(unit: .cpu, reason: "NPU and GPU unavailable or failed; using CPU.") }
        return DeviceVerdict(unit: .cpu, reason: "All units failed or are unavailable; CPU is the fallback and its output did not pass validation.")
    }
    public static func cacheKey(deviceModel: String, osVersion: String, appBuild: String, modelKey: ModelKey) -> String {
        return "\(deviceModel.count):\(deviceModel)\(osVersion.count):\(osVersion)\(appBuild.count):\(appBuild)\(modelKey.rawValue.count):\(modelKey.rawValue)"
    }
}
