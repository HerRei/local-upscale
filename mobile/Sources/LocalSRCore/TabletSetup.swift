import Foundation

public enum TabletTaskKind: String, Codable, CaseIterable, Sendable {
    case upscale
    case restore

    public var title: String {
        switch self {
        case .upscale: return "Upscale"
        case .restore: return "Restore"
        }
    }

    public var caption: String {
        switch self {
        case .upscale: return "Enlarges photos and artwork 4\u{00D7}."
        case .restore: return "Removes noise and JPEG artifacts at the same size."
        }
    }
}

public enum TabletContentKind: String, Codable, CaseIterable, Sendable {
    case photo
    case illustration

    public var title: String {
        switch self {
        case .photo: return "Photo"
        case .illustration: return "Illustration"
        }
    }
}

public enum TabletQualityPreset: String, Codable, CaseIterable, Sendable {
    case fast
    case best
    case finest
    case anime
    case general

    public var title: String {
        switch self {
        case .fast: return "Fast"
        case .best: return "Best"
        case .finest: return "Finest"
        case .anime: return "Anime"
        case .general: return "General"
        }
    }

    public var subtitle: String {
        switch self {
        case .fast: return "Quickest"
        case .best: return "More detail"
        case .finest: return "Most detail, slower"
        case .anime: return "Line art, flat colour"
        case .general: return "Painted or mixed artwork"
        }
    }

    public var contentKind: TabletContentKind {
        switch self {
        case .fast, .best, .finest: return .photo
        case .anime, .general: return .illustration
        }
    }

    public var modelID: TabletModelID? {
        switch self {
        case .fast: return .fast
        case .best: return .best
        case .finest: return .finest
        case .anime: return .anime
        case .general: return nil
        }
    }

    public static func presets(for content: TabletContentKind) -> [TabletQualityPreset] {
        switch content {
        case .photo: return [.fast, .best, .finest]
        case .illustration: return [.anime, .general]
        }
    }

    public static func matching(model: TabletModelID, content: TabletContentKind) -> TabletQualityPreset? {
        for preset in presets(for: content) {
            if preset.modelID == model { return preset }
        }
        return nil
    }
}

public enum TabletJpegStrength: String, Codable, CaseIterable, Sendable {
    case auto

    public var title: String { "Auto" }

    public init(from decoder: Decoder) throws {
        _ = try? decoder.singleValueContainer().decode(String.self)
        self = .auto
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        try c.encode(rawValue)
    }
}

public enum TabletOutputSizeChoice: Int, Codable, CaseIterable, Sendable {
    case x4 = 4
    case x2 = 2

    public var label: String { "\(rawValue)\u{00D7}" }
}

public enum TabletOutputFormat: String, Codable, CaseIterable, Sendable {
    case heic
    case jpeg
    case png

    public var displayName: String {
        switch self {
        case .heic: return "HEIC"
        case .jpeg: return "JPEG"
        case .png: return "PNG"
        }
    }

    public var fileExtension: String {
        switch self {
        case .heic: return "heic"
        case .jpeg: return "jpg"
        case .png: return "png"
        }
    }
}

public enum TabletSaveDestination: String, Codable, CaseIterable, Sendable {
    case photos
    case folder
}

public enum TabletLaneChoice: String, Codable, CaseIterable, Sendable {
    case both
    case neuralEngineOnly
    case gpuOnly

    public var shortTitle: String {
        shortTitle(isAndroid: false)
    }

    public func shortTitle(isAndroid: Bool) -> String {
        switch self {
        case .both: return "Both"
        case .neuralEngineOnly: return isAndroid ? "NPU" : "Neural Engine"
        case .gpuOnly: return "GPU"
        }
    }

    public var menuTitle: String {
        menuTitle(isAndroid: false)
    }

    public func menuTitle(isAndroid: Bool) -> String {
        switch self {
        case .both: return "Both Chips"
        case .neuralEngineOnly: return isAndroid ? "NPU Only" : "Neural Engine Only"
        case .gpuOnly: return "GPU Only"
        }
    }
}

public enum TabletWorkspaceLayoutMode: String, Equatable, Sendable {
    case threePane
    case twoColumnTabs
    case oneColumnTabs
}

public struct TabletLayoutMetrics: Equatable, Sendable {
    public static let foldWidth: Double = 1100.0
    public static let twoColumnWidth: Double = 700.0
    public static let mediaGridWidth: Double = 600.0

    public let mode: TabletWorkspaceLayoutMode
    public let isThreePane: Bool
    public let isTwoColumnEnhance: Bool
    public let isMediaGrid: Bool
    public let isCompactStatusBar: Bool

    public init(
        mode: TabletWorkspaceLayoutMode,
        isThreePane: Bool,
        isTwoColumnEnhance: Bool,
        isMediaGrid: Bool,
        isCompactStatusBar: Bool
    ) {
        self.mode = mode
        self.isThreePane = isThreePane
        self.isTwoColumnEnhance = isTwoColumnEnhance
        self.isMediaGrid = isMediaGrid
        self.isCompactStatusBar = isCompactStatusBar
    }

    public static func forWidth(_ width: Double) -> TabletLayoutMetrics {
        if width >= foldWidth {
            return TabletLayoutMetrics(
                mode: .threePane,
                isThreePane: true,
                isTwoColumnEnhance: false,
                isMediaGrid: false,
                isCompactStatusBar: false
            )
        }
        if width >= twoColumnWidth {
            return TabletLayoutMetrics(
                mode: .twoColumnTabs,
                isThreePane: false,
                isTwoColumnEnhance: true,
                isMediaGrid: true,
                isCompactStatusBar: false
            )
        }
        return TabletLayoutMetrics(
            mode: .oneColumnTabs,
            isThreePane: false,
            isTwoColumnEnhance: false,
            isMediaGrid: width >= mediaGridWidth,
            isCompactStatusBar: true
        )
    }
}

public struct TabletChipVerdict: Codable, Equatable, Sendable {
    public let preferred: ComputeUnit
    public let cpu: Bool
    public let gpu: Bool
    public let neuralEngine: Bool
    public let neuralEngineChecked: Bool

    public init(
        preferred: ComputeUnit,
        cpu: Bool,
        gpu: Bool,
        neuralEngine: Bool,
        neuralEngineChecked: Bool = false
    ) {
        self.preferred = preferred
        self.cpu = cpu
        self.gpu = gpu
        self.neuralEngine = neuralEngine
        self.neuralEngineChecked = neuralEngineChecked
    }

    private enum CodingKeys: String, CodingKey {
        case preferred, cpu, gpu, neuralEngine, neuralEngineChecked
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        preferred = try c.decode(ComputeUnit.self, forKey: .preferred)
        cpu = try c.decode(Bool.self, forKey: .cpu)
        gpu = try c.decode(Bool.self, forKey: .gpu)
        neuralEngine = try c.decode(Bool.self, forKey: .neuralEngine)
        neuralEngineChecked = try c.decodeIfPresent(Bool.self, forKey: .neuralEngineChecked) ?? false
    }

    public func supports(_ unit: ComputeUnit) -> Bool {
        switch unit {
        case .cpu: return cpu
        case .gpu: return gpu
        case .npu: return neuralEngine
        }
    }
}

public struct TabletRecipe: Identifiable, Codable, Equatable, Sendable {
    public let id: String
    public var name: String
    public var task: TabletTaskKind
    public var content: TabletContentKind
    public var upscaleModel: TabletModelID
    public var fixNoise: Bool
    public var fixJpeg: Bool
    public var jpegStrength: TabletJpegStrength
    public var outputSize: TabletOutputSizeChoice
    public var outputFormat: TabletOutputFormat

    public init(
        id: String,
        name: String,
        task: TabletTaskKind,
        content: TabletContentKind,
        upscaleModel: TabletModelID,
        fixNoise: Bool,
        fixJpeg: Bool,
        jpegStrength: TabletJpegStrength,
        outputSize: TabletOutputSizeChoice,
        outputFormat: TabletOutputFormat
    ) {
        self.id = id
        self.name = name
        self.task = task
        self.content = content
        self.upscaleModel = upscaleModel
        self.fixNoise = fixNoise
        self.fixJpeg = fixJpeg
        self.jpegStrength = jpegStrength
        self.outputSize = outputSize
        self.outputFormat = outputFormat
    }

    public var summaryText: String {
        var parts: [String] = [task.title]
        if task == .upscale {
            let desc = ModelCatalog.model(upscaleModel)
            parts.append(desc.displayName)
        }
        if fixJpeg && fixNoise {
            parts.append("Noise + JPEG fix")
        } else if fixJpeg {
            parts.append("JPEG fix")
        } else if fixNoise {
            parts.append("Noise fix")
        }
        if task == .upscale && outputSize == .x2 {
            parts.append("2\u{00D7}")
        }
        return parts.joined(separator: " \u{00B7} ")
    }
}

public struct TabletEnhanceSetup: Codable, Equatable, Sendable {
    public var task: TabletTaskKind
    public var content: TabletContentKind
    public var upscaleModel: TabletModelID
    public var isManualModelChoice: Bool
    public var fixNoise: Bool
    public var fixJpeg: Bool
    public var jpegStrength: TabletJpegStrength
    public var outputSize: TabletOutputSizeChoice
    public var outputFormat: TabletOutputFormat

    public init(
        task: TabletTaskKind = .upscale,
        content: TabletContentKind = .photo,
        upscaleModel: TabletModelID = .best,
        isManualModelChoice: Bool = false,
        fixNoise: Bool = false,
        fixJpeg: Bool = false,
        jpegStrength: TabletJpegStrength = .auto,
        outputSize: TabletOutputSizeChoice = .x4,
        outputFormat: TabletOutputFormat = .heic
    ) {
        self.task = task
        self.content = content
        self.upscaleModel = upscaleModel
        self.isManualModelChoice = isManualModelChoice
        self.fixNoise = fixNoise
        self.fixJpeg = fixJpeg
        self.jpegStrength = jpegStrength
        self.outputSize = outputSize
        self.outputFormat = outputFormat
    }

    public func buildPlan() -> Plan {
        var stages: [TabletModelID] = []
        if fixJpeg { stages.append(.jpegFix) }
        if fixNoise { stages.append(.noiseFix) }
        if task == .upscale { stages.append(upscaleModel) }
        if stages.isEmpty {
            stages.append(.noiseFix)
        }
        return Plan(stages: stages)
    }

    public func matches(recipe: TabletRecipe) -> Bool {
        if task != recipe.task { return false }
        if fixNoise != recipe.fixNoise || fixJpeg != recipe.fixJpeg { return false }
        if fixJpeg && jpegStrength != recipe.jpegStrength { return false }
        if outputFormat != recipe.outputFormat { return false }
        if task == .upscale {
            if content != recipe.content || upscaleModel != recipe.upscaleModel || outputSize != recipe.outputSize {
                return false
            }
        }
        return true
    }

    public func applying(recipe: TabletRecipe) -> TabletEnhanceSetup {
        let isPreset = TabletQualityPreset.matching(model: recipe.upscaleModel, content: recipe.content) != nil
        return TabletEnhanceSetup(
            task: recipe.task,
            content: recipe.content,
            upscaleModel: recipe.upscaleModel,
            isManualModelChoice: !isPreset,
            fixNoise: recipe.fixNoise || (recipe.task == .restore && !recipe.fixJpeg),
            fixJpeg: recipe.fixJpeg,
            jpegStrength: recipe.jpegStrength,
            outputSize: recipe.outputSize,
            outputFormat: recipe.outputFormat
        )
    }

    public func makeRecipe(id: String = UUID().uuidString, name: String) -> TabletRecipe {
        TabletRecipe(
            id: id,
            name: name,
            task: task,
            content: content,
            upscaleModel: upscaleModel,
            fixNoise: fixNoise,
            fixJpeg: fixJpeg,
            jpegStrength: jpegStrength,
            outputSize: outputSize,
            outputFormat: outputFormat
        )
    }

    public func headingSummary(matchingRecipe: TabletRecipe?) -> String {
        if let matchingRecipe {
            return matchingRecipe.name
        }
        if task == .restore {
            return "Restore"
        }
        if isManualModelChoice && TabletQualityPreset.matching(model: upscaleModel, content: content) == nil {
            return "Custom"
        }
        let desc = ModelCatalog.model(upscaleModel)
        return "\(desc.displayName) \u{00B7} \(content.title)"
    }

    public func predictedOutputSize(inputWidth: Int, inputHeight: Int, fullScale: Bool) -> StageSize {
        let plan = buildPlan()
        let sizes = plan.stageSizes(width: inputWidth, height: inputHeight, fullScale: fullScale, requestedScale: outputSize.rawValue)
        return sizes.last ?? StageSize(width: inputWidth, height: inputHeight, scale: 1)
    }

    public static func stageTitle(for model: TabletModelID, outputSize: TabletOutputSizeChoice) -> String {
        switch model {
        case .jpegFix: return "Fix JPEG"
        case .noiseFix: return "Denoise"
        default: return "Upscale \u{00D7}\(outputSize.rawValue)"
        }
    }

    public static func stageModelDetail(for model: TabletModelID) -> String {
        switch model {
        case .jpegFix: return "FBCNN \u{00B7} strength Auto"
        default: return ModelCatalog.model(model).fullName
        }
    }

    public static func chipLabel(
        for model: TabletModelID,
        verdict: TabletChipVerdict?,
        laneChoice: TabletLaneChoice,
        isAndroid: Bool
    ) -> String {
        guard let verdict else { return "Checked on first use" }
        let npuName = isAndroid ? "NPU" : "Neural Engine"
        switch laneChoice {
        case .both:
            if verdict.neuralEngine && verdict.gpu { return "Both chips" }
            if verdict.gpu { return "GPU" }
            if verdict.neuralEngine { return npuName }
            if verdict.cpu { return "CPU" }
            return "Unavailable"
        case .neuralEngineOnly:
            return verdict.neuralEngine ? npuName : "Unavailable"
        case .gpuOnly:
            return verdict.gpu ? "GPU" : "Unavailable"
        }
    }

    public static func isStageRunnable(
        model: TabletModelID,
        verdict: TabletChipVerdict?,
        laneChoice: TabletLaneChoice
    ) -> Bool {
        guard let verdict else { return true }
        switch laneChoice {
        case .both: return verdict.gpu || verdict.neuralEngine || verdict.cpu
        case .neuralEngineOnly: return verdict.neuralEngine
        case .gpuOnly: return verdict.gpu
        }
    }

    public static func laneValidationFailureMessage(
        plan: Plan,
        verdicts: [TabletModelID: TabletChipVerdict],
        failedModels: Set<TabletModelID>,
        laneChoice: TabletLaneChoice,
        deviceNoun: String
    ) -> (title: String, detail: String, allFailed: Bool)? {
        for model in plan.stages {
            if failedModels.contains(model) {
                let title = deviceNoun == "this tablet" ? "Can't Upscale on This Tablet" : "Can't Upscale on This iPad"
                return (title, "The device check found no chip that runs the models correctly.", true)
            }
            guard let verdict = verdicts[model] else { continue }
            if !isStageRunnable(model: model, verdict: verdict, laneChoice: laneChoice) {
                let name = ModelCatalog.model(model).displayName
                let npuName = deviceNoun == "this tablet" ? "NPU" : "Neural Engine"
                switch laneChoice {
                case .neuralEngineOnly:
                    if verdict.gpu {
                        return ("Ready", "\(name) needs the GPU.", false)
                    }
                    return ("Ready", "\(name) cannot run on the \(npuName).", false)
                case .gpuOnly:
                    if verdict.neuralEngine {
                        return ("Ready", "\(name) needs the \(npuName).", false)
                    }
                    return ("Ready", "\(name) cannot run on the GPU.", false)
                case .both:
                    let title = deviceNoun == "this tablet" ? "Can't Upscale on This Tablet" : "Can't Upscale on This iPad"
                    return (title, "The device check found no chip that runs the models correctly.", true)
                }
            }
        }
        return nil
    }
}

public enum TabletRecipeStore {
    public static func decode(_ json: String) -> [TabletRecipe] {
        guard !json.isEmpty, let data = json.data(using: .utf8) else { return [] }
        return (try? JSONDecoder().decode([TabletRecipe].self, from: data)) ?? []
    }

    public static func encode(_ recipes: [TabletRecipe]) -> String {
        guard let data = try? JSONEncoder().encode(recipes),
              let str = String(data: data, encoding: .utf8) else {
            return "[]"
        }
        return str
    }
}
