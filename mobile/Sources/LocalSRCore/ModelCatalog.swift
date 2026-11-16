import Foundation

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
    /// The "(key)" in this model's section title in Models/NOTICE.txt (see `ModelNotice`).
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

public extension ModelDescriptor {
    /// A licence whose full text is a section of its own in NOTICE.txt, shared by several models.
    var sharedLicenceNoticeKey: String? {
        licence == "Apache-2.0" ? "apache-2.0" : nil
    }
}

/// Finds a model's part of Models/NOTICE.txt, the text the Licences screen shows.
/// A section starts with a title line that contains "(key)" and is underlined with "-" or "=";
/// it runs to the next underlined title or the end of the text.
public enum ModelNotice {
    public static func section(key: String, in text: String) -> String? {
        let lines = text.components(separatedBy: "\n")
        var start: Int? = nil
        var end = lines.count
        var index = 0
        while index + 1 < lines.count {
            if isTitle(lines, at: index) {
                if start != nil {
                    end = index
                    break
                }
                if lines[index].contains("(\(key))") {
                    start = index
                }
            }
            index += 1
        }
        guard let first = start else { return nil }
        var body: [String] = []
        for line in first..<end {
            body.append(lines[line])
        }
        return body.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// The model's own section, followed by the full text of its licence when that is shared.
    public static func text(for model: ModelDescriptor, in notice: String) -> String? {
        guard let own = section(key: model.licenceNoticeKey, in: notice) else { return nil }
        if let sharedKey = model.sharedLicenceNoticeKey, let shared = section(key: sharedKey, in: notice) {
            return own + "\n\n" + shared
        }
        return own
    }

    private static func isTitle(_ lines: [String], at index: Int) -> Bool {
        let title = lines[index].trimmingCharacters(in: .whitespacesAndNewlines)
        let rule = lines[index + 1].trimmingCharacters(in: .whitespacesAndNewlines)
        let ruleMarks = rule.replacingOccurrences(of: "-", with: "").replacingOccurrences(of: "=", with: "")
        return !title.isEmpty && rule.count >= 3 && ruleMarks.isEmpty
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
            licenceNoticeKey: "localsr_photo_span28_v1_x4",
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
            licenceNoticeKey: "localsr_photo_pulse48_v1_x4",
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
            licenceNoticeKey: "nomos_realplksr",
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
            licenceNoticeKey: "denoise_realplksr",
            qualityRating: 3,
            speedRating: 4,
            author: "Philip Hofmann",
            licence: "CC BY 4.0",
            sourceURL: "https://github.com/Phhofm/models/releases/tag/1xDeNoise_realplksr_otf",
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
            author: "Jiaxi Jiang, Kai Zhang, Radu Timofte",
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
            licenceNoticeKey: "anime_esrgan",
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
            licenceNoticeKey: "nomos_span",
            qualityRating: 2,
            speedRating: 4,
            author: "Philip Hofmann",
            licence: "CC BY 4.0",
            sourceURL: "https://github.com/Phhofm/models/releases/tag/4xNomosUni_span_multijpg",
            libraryGroup: .upscalePhotos,
            fallbackBytes: 917_000
        ),
        ModelDescriptor(
            id: .span48,
            displayName: "SPAN \u{00D7}4 ch48",
            fullName: "SPAN \u{00D7}4 ch48",
            summary: "The SPAN authors' official 48-channel \u{00D7}4 checkpoint for clean photos.",
            scale: 4,
            window: 384,
            halo: 16,
            preferredLane: .neuralEngine,
            licenceNoticeKey: "span48",
            qualityRating: 3,
            speedRating: 4,
            author: "Cheng Wan, Hongyuan Yu et al.",
            licence: "Apache-2.0",
            sourceURL: "https://github.com/hongyuanyu/SPAN",
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
