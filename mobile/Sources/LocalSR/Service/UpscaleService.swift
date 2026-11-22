import Foundation
import LocalSRCore

/// One finished upscale. Files live in the app's caches directory until saved or discarded.
public struct UpscaleResult: Identifiable, Codable, Sendable {
    public let id: String
    /// Original input file as picked (kept for the before/after view).
    public let inputURL: URL
    /// Full-size output (HEIC on iOS, JPEG on Android).
    public let outputURL: URL
    /// Downscaled copy (long side <= 4096 px) for the compare view.
    public let previewURL: URL
    public let width: Int
    public let height: Int
    public let model: ModelKey
    public let unit: ComputeUnit
    public let seconds: Double
    public let outputScale: Int
    public let fullScaleFallback: Bool

    public init(id: String, inputURL: URL, outputURL: URL, previewURL: URL, width: Int, height: Int, model: ModelKey, unit: ComputeUnit, seconds: Double, outputScale: Int = 4, fullScaleFallback: Bool = false) {
        self.id = id
        self.inputURL = inputURL
        self.outputURL = outputURL
        self.previewURL = previewURL
        self.width = width
        self.height = height
        self.model = model
        self.unit = unit
        self.seconds = seconds
        self.outputScale = outputScale
        self.fullScaleFallback = fullScaleFallback
    }
    private enum CodingKeys: String, CodingKey { case id, inputURL, outputURL, previewURL, width, height, model, unit, seconds, outputScale, fullScaleFallback }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        inputURL = try c.decode(URL.self, forKey: .inputURL)
        outputURL = try c.decode(URL.self, forKey: .outputURL)
        previewURL = try c.decode(URL.self, forKey: .previewURL)
        width = try c.decode(Int.self, forKey: .width)
        height = try c.decode(Int.self, forKey: .height)
        model = try c.decode(ModelKey.self, forKey: .model)
        unit = try c.decode(ComputeUnit.self, forKey: .unit)
        seconds = try c.decode(Double.self, forKey: .seconds)
        outputScale = try c.decodeIfPresent(Int.self, forKey: .outputScale) ?? 4
        fullScaleFallback = try c.decodeIfPresent(Bool.self, forKey: .fullScaleFallback) ?? false
    }

}

/// The platform boundary. The UI only talks to this protocol.
/// iOS: `AppleUpscaleService` (Sources/LocalSR/Apple, `#if !SKIP`).
/// Android: `AndroidUpscaleService` (Sources/LocalSR/Android, `#if SKIP`).
public protocol UpscaleService {
    /// Runs the device check for `model` if no cached verdict exists, loads the model, returns the verdict.
    func prepare(model: ModelKey) async throws -> DeviceVerdict
    /// Upscales one picked photo file 4x (prescaled so the output stays <= 48 MP).
    /// `progress` is called with 0...1 from any thread.
    func upscale(input: URL, model: ModelKey, fullScale: Bool, progress: @escaping (Double) -> Void) async throws -> UpscaleResult
    func canUseFullScale(forInput: URL) -> Bool
    /// Saves the output to the system photo library, keeping date and location (and the HDR gain map on iOS).
    func saveToLibrary(_ result: UpscaleResult) async throws
}

public extension UpscaleService {
    func upscale(input: URL, model: ModelKey, progress: @escaping (Double) -> Void) async throws -> UpscaleResult {
        try await upscale(input: input, model: model, fullScale: false, progress: progress)
    }
}

public func makeUpscaleService() -> UpscaleService {
    #if SKIP
    return AndroidUpscaleService()
    #else
    return AppleUpscaleService()
    #endif
}
