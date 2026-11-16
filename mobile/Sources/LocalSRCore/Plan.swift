/// A recipe stores model IDs in execution order. FBCNN estimates its own JPEG strength.
public struct Plan: Codable, Sendable {
    public let stages: [TabletModelID]

    public init(stages: [TabletModelID]) { self.stages = stages }

    /// A plan has at most one of each fix, in order, optionally followed by one 4x model.
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
        return true
    }

    /// Upscales use the common input cap. Restore-only plans retain the original dimensions.
    public func stageSizes(width: Int, height: Int, fullScale: Bool = false) -> [StageSize] {
        precondition(isValid && width > 0 && height > 0)
        let target = SizePolicy.target(width: width, height: height)
        let upscale = stages.contains { ModelCatalog.model($0).scale == 4 }
        var w = upscale ? target.width : width
        var h = upscale ? target.height : height
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
