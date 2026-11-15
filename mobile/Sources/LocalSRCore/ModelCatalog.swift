/// Identifiers are persisted in recipes; they match the converted artifact names.
public enum TabletModelID: String, Codable, Sendable {
    case fast, best
    case finest = "nomos_realplksr"
    case noiseFix = "denoise_realplksr"
    case jpegFix = "fbcnn"
    case anime = "anime_esrgan"
    case nomosSpan = "nomos_span"
    case span48 = "span48"
}

public enum ModelLane: String, Codable, Sendable {
    case neuralEngine, gpu
}

public struct ModelDescriptor: Sendable {
    public let id: TabletModelID
    public let displayName: String
    public let scale: Int
    public let window: Int
    public let halo: Int
    public let preferredLane: ModelLane
    public let iosFileName: String
    public let androidFileName: String
    public let licenceNoticeKey: String

    public init(id: TabletModelID, displayName: String, scale: Int, window: Int, halo: Int, preferredLane: ModelLane, licenceNoticeKey: String) {
        self.id = id
        self.displayName = displayName
        self.scale = scale
        self.window = window
        self.halo = halo
        self.preferredLane = preferredLane
        self.iosFileName = id.rawValue + ".mlmodelc"
        self.androidFileName = id.rawValue + ".tflite"
        self.licenceNoticeKey = licenceNoticeKey
    }
}

public enum ModelCatalog {
    public static let all: [ModelDescriptor] = [
        ModelDescriptor(id: .fast, displayName: "Fast", scale: 4, window: 384, halo: 16, preferredLane: .neuralEngine, licenceNoticeKey: "span"),
        ModelDescriptor(id: .best, displayName: "Best", scale: 4, window: 384, halo: 16, preferredLane: .neuralEngine, licenceNoticeKey: "pulse"),
        ModelDescriptor(id: .finest, displayName: "Finest", scale: 4, window: 256, halo: 16, preferredLane: .gpu, licenceNoticeKey: "realplksr"),
        ModelDescriptor(id: .noiseFix, displayName: "Noise fix", scale: 1, window: 256, halo: 16, preferredLane: .gpu, licenceNoticeKey: "realplksr"),
        ModelDescriptor(id: .jpegFix, displayName: "JPEG fix", scale: 1, window: 256, halo: 16, preferredLane: .neuralEngine, licenceNoticeKey: "fbcnn"),
        ModelDescriptor(id: .anime, displayName: "Anime", scale: 4, window: 384, halo: 16, preferredLane: .neuralEngine, licenceNoticeKey: "realesrgan"),
        ModelDescriptor(id: .nomosSpan, displayName: "SPAN ×4 NomosUni", scale: 4, window: 384, halo: 16, preferredLane: .neuralEngine, licenceNoticeKey: "span"),
        ModelDescriptor(id: .span48, displayName: "SPAN ×4 ch48", scale: 4, window: 384, halo: 16, preferredLane: .neuralEngine, licenceNoticeKey: "span")
    ]

    public static func model(_ id: TabletModelID) -> ModelDescriptor {
        for model in all { if model.id == id { return model } }
        preconditionFailure("Unknown model")
    }
}
