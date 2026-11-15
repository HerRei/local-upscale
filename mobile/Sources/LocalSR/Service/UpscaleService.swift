import Foundation
import LocalSRCore

/// One finished upscale. Files live in the app's caches directory until saved or discarded.
public struct UpscaleResult: Identifiable {
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

    public init(id: String, inputURL: URL, outputURL: URL, previewURL: URL, width: Int, height: Int, model: ModelKey, unit: ComputeUnit, seconds: Double) {
        self.id = id
        self.inputURL = inputURL
        self.outputURL = outputURL
        self.previewURL = previewURL
        self.width = width
        self.height = height
        self.model = model
        self.unit = unit
        self.seconds = seconds
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
    func upscale(input: URL, model: ModelKey, progress: @escaping (Double) -> Void) async throws -> UpscaleResult
    /// Saves the output to the system photo library, keeping date and location (and the HDR gain map on iOS).
    func saveToLibrary(_ result: UpscaleResult) async throws
}

public func makeUpscaleService() -> UpscaleService {
    #if SKIP
    return AndroidUpscaleService()
    #else
    return AppleUpscaleService()
    #endif
}
