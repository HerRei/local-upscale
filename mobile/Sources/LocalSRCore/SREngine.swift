public enum ComputeUnit: String, Codable, Sendable {
    case npu, gpu, cpu
}

public enum ModelKey: String, Codable, Sendable {
    case best, fast
    public var fileName: String {
        switch self {
        case .best: return "localsr_best"
        case .fast: return "localsr_fast"
        }
    }
}

public protocol SREngine {
    func load(model: ModelKey, unit: ComputeUnit) throws
    func run(tile: [Float]) throws -> [Float]
}
