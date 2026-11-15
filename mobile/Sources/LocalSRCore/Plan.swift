/// A recipe stores model IDs in execution order. FBCNN estimates its own JPEG strength.
public struct Plan: Codable, Sendable {
    public let stages: [TabletModelID]

    public init(stages: [TabletModelID]) { self.stages = stages }

    /// A valid upscale has at most one of each fix, in order, followed by exactly one 4x model.
    public var isValid: Bool {
        if stages.isEmpty || stages.count > 3 { return false }
        var lastRank = -1
        for stage in stages {
            let rank: Int
            if stage == .jpegFix { rank = 0 }
            else if stage == .noiseFix { rank = 1 }
            else { rank = 2 }
            if rank <= lastRank { return false }
            lastRank = rank
        }
        return lastRank == 2
    }

    /// Dimensions after each stage, after the common SizePolicy input cap. The caller must
    /// check memory availability before passing `fullScale: true`.
    public func stageSizes(width: Int, height: Int, fullScale: Bool = false) -> [StageSize] {
        precondition(isValid && width > 0 && height > 0)
        let target = SizePolicy.target(width: width, height: height)
        var w = target.width
        var h = target.height
        var sizes: [StageSize] = []
        for stage in stages {
            let scale = ModelCatalog.model(stage).scale == 1 ? 1 : (fullScale ? 4 : target.outputScale)
            w *= scale
            h *= scale
            sizes.append(StageSize(width: w, height: h, scale: scale))
        }
        return sizes
    }
}

public struct StageSize: Sendable {
    public let width: Int
    public let height: Int
    public let scale: Int
}
