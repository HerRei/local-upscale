#if SKIP
import Foundation
import LocalSRCore

/// Android implementation of `UpscaleService`. A thin wrapper: everything heavy is the hand-written Kotlin
/// in `Sources/LocalSR/Skip/` (`AndroidUpscaler`, LiteRT engine, tiling, codec, MediaStore).
/// Transpiled Swift stays out of every per-pixel and per-tile loop on purpose.
public final class AndroidUpscaleService: UpscaleService {
    private let upscaler: AndroidUpscaler

    public init() {
        self.upscaler = AndroidUpscaler(ProcessInfo.processInfo.androidContext)
    }

    public func prepare(model: ModelKey) async throws -> DeviceVerdict {
        return try await upscaler.prepare(model)
    }

    public func upscale(input: URL, model: ModelKey, progress: @escaping (Double) -> Void) async throws -> UpscaleResult {
        let raw = try await upscaler.upscale(input.absoluteString, model, progress)
        return UpscaleResult(id: raw.id, inputURL: input, outputURL: URL(fileURLWithPath: raw.outputPath), previewURL: URL(fileURLWithPath: raw.previewPath), width: raw.width, height: raw.height, model: model, unit: raw.unit, seconds: raw.seconds)
    }

    public func saveToLibrary(_ result: UpscaleResult) async throws {
        _ = try await upscaler.saveToLibrary(result.outputURL.path)
    }

    /// Frees the loaded model (call on low memory or when the app goes to the background without work).
    public func releaseModel() async {
        await upscaler.release()
    }

    public func beginBatch() async throws {
        try await UpscaleForegroundService.acquire(ProcessInfo.processInfo.androidContext)
    }

    public func endBatch() {
        UpscaleForegroundService.release(ProcessInfo.processInfo.androidContext)
    }

    /// Optional: lets the foreground notification say "Photo 2 of 5". Not part of `UpscaleService`.
    public func setBatch(done: Int, total: Int) {
        UpscaleForegroundService.setBatch(ProcessInfo.processInfo.androidContext, done, total)
    }
}
#endif
