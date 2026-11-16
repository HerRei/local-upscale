public enum TabletLibraryGroup: String, Codable, CaseIterable, Sendable {
    case upscalePhotos
    case illustration
    case fixNoiseAndJpeg

    public var title: String {
        switch self {
        case .upscalePhotos: return "Upscale photos"
        case .illustration: return "Illustration"
        case .fixNoiseAndJpeg: return "Fix noise and JPEG"
        }
    }
}

/// Identifiers are persisted in recipes; they match the converted artifact names.
public enum TabletModelID: String, Codable, CaseIterable, Sendable {
    case fast, best
    case finest = "nomos_realplksr"
    case noiseFix = "denoise_realplksr"
    case jpegFix = "fbcnn"
    case anime = "anime_esrgan"
    case nomosSpan = "nomos_span"
    case span48 = "span48"
}

public enum ModelLane: String, Codable, Sendable {
    case neuralEngine, gpu, cpu
}

public struct ModelDescriptor: Sendable {
    public let id: TabletModelID
    public let displayName: String
    public let fullName: String
    public let summary: String
    public let scale: Int
    public let window: Int
    public let halo: Int
    public let preferredLane: ModelLane
    public let iosFileName: String
    public let androidFileName: String
    public let licenceNoticeKey: String
    public let qualityRating: Int
    public let speedRating: Int
    public let author: String
    public let licence: String
    public let sourceURL: String
    public let libraryGroup: TabletLibraryGroup
    public let fallbackBytes: Int64

    public init(
        id: TabletModelID,
        displayName: String,
        fullName: String? = nil,
        summary: String = "",
        scale: Int,
        window: Int,
        halo: Int,
        preferredLane: ModelLane,
        licenceNoticeKey: String,
        qualityRating: Int = 3,
        speedRating: Int = 3,
        author: String = "LocalSR",
        licence: String = "MIT",
        sourceURL: String = "https://github.com/HerRei/LocalSR",
        libraryGroup: TabletLibraryGroup = .upscalePhotos,
        fallbackBytes: Int64 = 0
    ) {
        self.id = id
        self.displayName = displayName
        self.fullName = fullName ?? displayName
        self.summary = summary
        self.scale = scale
        self.window = window
        self.halo = halo
        self.preferredLane = preferredLane
        self.iosFileName = id.rawValue + ".mlmodelc"
        self.androidFileName = id.rawValue + ".tflite"
        self.licenceNoticeKey = licenceNoticeKey
        self.qualityRating = qualityRating
        self.speedRating = speedRating
        self.author = author
        self.licence = licence
        self.sourceURL = sourceURL
        self.libraryGroup = libraryGroup
        self.fallbackBytes = fallbackBytes
    }
}

public enum ModelCatalog {
    public static let all: [ModelDescriptor] = [
        ModelDescriptor(
            id: .fast,
            displayName: "Fast",
            fullName: "SPAN-28",
            summary: "Ultra-fast photo upscaler trained from scratch for everyday photos.",
            scale: 4,
            window: 384,
            halo: 16,
            preferredLane: .neuralEngine,
            licenceNoticeKey: "span",
            qualityRating: 2,
            speedRating: 4,
            author: "LocalSR",
            licence: "MIT",
            sourceURL: "https://github.com/HerRei/LocalSR",
            libraryGroup: .upscalePhotos,
            fallbackBytes: 365_000
        ),
        ModelDescriptor(
            id: .best,
            displayName: "Best",
            fullName: "PULSE-48",
            summary: "Balanced photo upscaler with natural detail and textures.",
            scale: 4,
            window: 384,
            halo: 16,
            preferredLane: .neuralEngine,
            licenceNoticeKey: "pulse",
            qualityRating: 3,
            speedRating: 3,
            author: "LocalSR / Philip Hofmann",
            licence: "MIT",
            sourceURL: "https://huggingface.co/Phips/PULSE",
            libraryGroup: .upscalePhotos,
            fallbackBytes: 758_000
        ),
        ModelDescriptor(
            id: .finest,
            displayName: "Finest",
            fullName: "RealPLKSR \u{00D7}4 NomosWebPhoto",
            summary: "High-fidelity photo upscaler trained on real-world web compression.",
            scale: 4,
            window: 256,
            halo: 16,
            preferredLane: .gpu,
            licenceNoticeKey: "realplksr",
            qualityRating: 4,
            speedRating: 2,
            author: "Philip Hofmann",
            licence: "CC BY 4.0",
            sourceURL: "https://huggingface.co/Phips/4xNomosWebPhoto_RealPLKSR",
            libraryGroup: .upscalePhotos,
            fallbackBytes: 15_700_000
        ),
        ModelDescriptor(
            id: .noiseFix,
            displayName: "Noise fix",
            fullName: "RealPLKSR Denoise 1\u{00D7}",
            summary: "Fast 1\u{00D7} denoising model to clean up camera and high-ISO noise.",
            scale: 1,
            window: 256,
            halo: 16,
            preferredLane: .gpu,
            licenceNoticeKey: "realplksr",
            qualityRating: 3,
            speedRating: 4,
            author: "Philip Hofmann",
            licence: "CC BY 4.0",
            sourceURL: "https://github.com/Phhofm/models",
            libraryGroup: .fixNoiseAndJpeg,
            fallbackBytes: 15_700_000
        ),
        ModelDescriptor(
            id: .jpegFix,
            displayName: "JPEG fix",
            fullName: "FBCNN",
            summary: "Blind JPEG artifact removal before upscaling or as 1\u{00D7} restore.",
            scale: 1,
            window: 256,
            halo: 16,
            preferredLane: .neuralEngine,
            licenceNoticeKey: "fbcnn",
            qualityRating: 3,
            speedRating: 3,
            author: "Jiaxi Jiang et al.",
            licence: "Apache-2.0",
            sourceURL: "https://github.com/jiaxi-jiang/FBCNN",
            libraryGroup: .fixNoiseAndJpeg,
            fallbackBytes: 144_000_000
        ),
        ModelDescriptor(
            id: .anime,
            displayName: "Anime",
            fullName: "Real-ESRGAN \u{00D7}4 Anime",
            summary: "Compact upscaler keeping anime line art and flat colour crisp.",
            scale: 4,
            window: 384,
            halo: 16,
            preferredLane: .neuralEngine,
            licenceNoticeKey: "realesrgan",
            qualityRating: 3,
            speedRating: 4,
            author: "Xintao Wang et al.",
            licence: "BSD-3-Clause",
            sourceURL: "https://github.com/xinntao/Real-ESRGAN",
            libraryGroup: .illustration,
            fallbackBytes: 9_200_000
        ),
        ModelDescriptor(
            id: .nomosSpan,
            displayName: "SPAN \u{00D7}4 NomosUni",
            fullName: "SPAN \u{00D7}4 NomosUni",
            summary: "Lightweight SPAN photo upscaler trained on multi-JPEG degradations.",
            scale: 4,
            window: 384,
            halo: 16,
            preferredLane: .neuralEngine,
            licenceNoticeKey: "span",
            qualityRating: 2,
            speedRating: 4,
            author: "Philip Hofmann",
            licence: "CC BY 4.0",
            sourceURL: "https://github.com/Phhofm/models",
            libraryGroup: .upscalePhotos,
            fallbackBytes: 917_000
        ),
        ModelDescriptor(
            id: .span48,
            displayName: "SPAN \u{00D7}4 ch48",
            fullName: "SPAN \u{00D7}4 ch48",
            summary: "Compact 48-channel SPAN photo upscaler with higher detail.",
            scale: 4,
            window: 384,
            halo: 16,
            preferredLane: .neuralEngine,
            licenceNoticeKey: "span",
            qualityRating: 3,
            speedRating: 4,
            author: "LocalSR",
            licence: "MIT",
            sourceURL: "https://github.com/HerRei/LocalSR",
            libraryGroup: .upscalePhotos,
            fallbackBytes: 917_000
        )
    ]

    public static func model(_ id: TabletModelID) -> ModelDescriptor {
        for model in all { if model.id == id { return model } }
        preconditionFailure("Unknown model")
    }

    public static func models(in group: TabletLibraryGroup) -> [ModelDescriptor] {
        all.filter { $0.libraryGroup == group }
    }
}
