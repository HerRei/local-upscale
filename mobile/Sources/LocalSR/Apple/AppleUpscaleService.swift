#if !SKIP
import Foundation
import Dispatch
import CoreGraphics
import LocalSRCore

/// iOS (and macOS-host) implementation of `UpscaleService`: Core ML engine, ImageIO codec, PhotoKit.
///
/// One engine is kept loaded (the model/unit of the last `prepare`); `prepare` and `upscale` are serialized,
/// so jobs never share the engine's buffers concurrently.
public final class AppleUpscaleService: UpscaleService, @unchecked Sendable {
    public enum ServiceError: Error, CustomStringConvertible {
        case cancelled
        public var description: String { "Cancelled" }
    }

    /// Details of the last `upscale` for logs and tests.
    public struct JobStats {
        public var tiles: Int
        public var decodeSeconds: Double
        public var tilesSeconds: Double
        public var encodeSeconds: Double
        public var gainMapNote: String
    }

    private let deviceCheck: AppleDeviceCheck
    private let cacheRoot: URL
    private let mutex = AsyncMutex()
    private let state = NSLock()
    private var engine = CoreMLEngine()
    private var verdicts: [ModelKey: DeviceVerdict] = [:]
    private var reports: [ModelKey: AppleDeviceCheck.Report] = [:]
    private var stats: JobStats?

    /// - Parameters:
    ///   - defaults: where the device-check verdict is cached.
    ///   - cachesDirectory: parent of `LocalSR/<id>/` (the app's Caches directory by default).
    public init(defaults: UserDefaults = .standard, cachesDirectory: URL? = nil) {
        deviceCheck = AppleDeviceCheck(defaults: defaults)
        cacheRoot = (cachesDirectory ?? FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]).appendingPathComponent("LocalSR", isDirectory: true)
    }

    /// The verdict chosen by the last `prepare` for `model` (for the About sheet).
    public func verdict(for model: ModelKey) -> DeviceVerdict? {
        state.withLock { verdicts[model] }
    }

    /// The last full device-check report (nil when the verdict came from the cache).
    public func report(for model: ModelKey) -> AppleDeviceCheck.Report? {
        state.withLock { reports[model] }
    }

    public var lastJobStats: JobStats? {
        state.withLock { stats }
    }

    // MARK: UpscaleService

    public func prepare(model: ModelKey) async throws -> DeviceVerdict {
        await mutex.lock()
        do {
            let verdict = try await prepareLocked(model: model)
            await mutex.unlock()
            return verdict
        } catch {
            await mutex.unlock()
            throw error
        }
    }

    public func upscale(input: URL, model: ModelKey, progress: @escaping (Double) -> Void) async throws -> UpscaleResult {
        await mutex.lock()
        do {
            let verdict = try await prepareLocked(model: model)
            let id = UUID().uuidString
            let directory = cacheRoot.appendingPathComponent(id, isDirectory: true)
            let engine = self.engine
            let cancel = CancelFlag()
            let progress = SendableProgress(progress)
            let job = Box((model: model, unit: verdict.unit))
            let started = Date()
            let boxed: Box<(UpscaleResult, JobStats)> = try await withTaskCancellationHandler {
                try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Box<(UpscaleResult, JobStats)>, Error>) in
                    DispatchQueue.global(qos: .userInitiated).async {
                        do {
                            let r = try Self.runPipeline(input: input, model: job.value.model, unit: job.value.unit, engine: engine, directory: directory, id: id, started: started, cancel: cancel, progress: progress)
                            continuation.resume(returning: Box(r))
                        } catch {
                            try? FileManager.default.removeItem(at: directory)
                            continuation.resume(throwing: error)
                        }
                    }
                }
            } onCancel: {
                cancel.cancel()
            }
            let outcome = boxed.value
            state.withLock { stats = outcome.1 }
            await mutex.unlock()
            return outcome.0
        } catch {
            await mutex.unlock()
            throw error
        }
    }

    public func saveToLibrary(_ result: UpscaleResult) async throws {
        let stem = result.inputURL.deletingPathExtension().lastPathComponent
        try await PhotoLibrarySaver.save(fileURL: result.outputURL, originalFilename: "\(stem)-LocalSR-4x.heic")
    }

    // MARK: Preparation

    private func prepareLocked(model: ModelKey) async throws -> DeviceVerdict {
        if let current = engine.loadedModel, current == model, let unit = engine.loadedUnit, let verdict = verdict(for: model), verdict.unit == unit {
            return verdict
        }
        var verdict: DeviceVerdict
        if let known = self.verdict(for: model) {
            verdict = known
        } else {
            let report = try await deviceCheck.verdict(for: model)
            verdict = report.verdict
            state.withLock { reports[model] = report }
        }
        do {
            try await engine.loadAsync(model: model, unit: verdict.unit)
        } catch {
            // The cached unit no longer loads (OS update, driver): forget the cache and use the CPU this session.
            if verdict.unit == .cpu { throw error }
            deviceCheck.clearCache(model: model)
            verdict = DeviceVerdict(unit: .cpu, reason: "Loading on \(verdict.unit.rawValue) failed (\(error)); using CPU.")
            try await engine.loadAsync(model: model, unit: .cpu)
        }
        let chosen = verdict
        state.withLock { verdicts[model] = chosen }
        return verdict
    }

    // MARK: Pipeline (runs on a background queue)

    private static func runPipeline(input: URL, model: ModelKey, unit: ComputeUnit, engine: CoreMLEngine, directory: URL, id: String, started: Date, cancel: CancelFlag, progress: SendableProgress) throws -> (UpscaleResult, JobStats) {
        let t0 = Date()
        progress.report(0)
        let decoded = try ImageIOCodec.decode(input: input)
        let source = decoded.pixels
        let scale = 4
        let outWidth = source.width * scale, outHeight = source.height * scale
        let tDecoded = Date()
        if cancel.isCancelled { throw ServiceError.cancelled }

        let output = PixelBuffer(width: outWidth, height: outHeight)
        let tiles = TilePlanner.plan(width: source.width, height: source.height)
        let window = CoreMLEngine.window
        // Stitching tile n (CPU) overlaps with predicting tile n + 1 (NPU/GPU). The engine alternates two output
        // buffers; at most one stitch is in flight, which is what keeps tile n's buffer intact until it is done.
        let stitchQueue = DispatchQueue(label: "localsr.stitch", qos: .userInitiated)
        let inFlight = DispatchGroup()
        defer { inFlight.wait() }
        nonisolated(unsafe) let outputPointer = output.pointer
        for (index, tile) in tiles.enumerated() {
            if cancel.isCancelled { throw ServiceError.cancelled }
            let view = try engine.predict { dst in
                ApplePixelKernels.fillWindow(source: source.pointer, width: source.width, height: source.height, tile: tile, window: window, dst: dst)
            }
            inFlight.wait()
            let planned = Box(tile)
            inFlight.enter()
            stitchQueue.async {
                ApplePixelKernels.stitch(tile: planned.value, output: view, image: outputPointer, outputWidth: outWidth)
                inFlight.leave()
            }
            progress.report(0.95 * Double(index + 1) / Double(tiles.count))
        }
        inFlight.wait()
        let tTiles = Date()
        if cancel.isCancelled { throw ServiceError.cancelled }

        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let outputURL = directory.appendingPathComponent("output.heic")
        let previewURL = directory.appendingPathComponent("preview.jpg")
        let encoded = try ImageIOCodec.encodeHEIC(pixels: output, colorSpace: decoded.colorSpace, decoded: decoded, to: outputURL)
        _ = try ImageIOCodec.writePreviewJPEG(pixels: output, colorSpace: decoded.colorSpace, to: previewURL)
        let tDone = Date()
        progress.report(1)

        let result = UpscaleResult(id: id, inputURL: input, outputURL: outputURL, previewURL: previewURL, width: outWidth, height: outHeight, model: model, unit: unit, seconds: tDone.timeIntervalSince(t0))
        let stats = JobStats(tiles: tiles.count, decodeSeconds: tDecoded.timeIntervalSince(t0), tilesSeconds: tTiles.timeIntervalSince(tDecoded), encodeSeconds: tDone.timeIntervalSince(tTiles), gainMapNote: encoded.note)
        return (result, stats)
    }
}

extension ImageIOCodec {
    static func decode(input: URL) throws -> DecodedImage { try decode(url: input) }
}

struct Box<T>: @unchecked Sendable { let value: T; init(_ value: T) { self.value = value } }

final class CancelFlag: @unchecked Sendable {
    private let lock = NSLock()
    private var flag = false
    func cancel() { lock.lock(); flag = true; lock.unlock() }
    var isCancelled: Bool { lock.lock(); defer { lock.unlock() }; return flag }
}

final class SendableProgress: @unchecked Sendable {
    private let callback: (Double) -> Void
    init(_ callback: @escaping (Double) -> Void) { self.callback = callback }
    func report(_ value: Double) { callback(value) }
}

/// FIFO async mutex.
actor AsyncMutex {
    private var locked = false
    private var waiters: [CheckedContinuation<Void, Never>] = []
    func lock() async {
        if !locked { locked = true; return }
        await withCheckedContinuation { waiters.append($0) }
    }
    func unlock() {
        if waiters.isEmpty { locked = false } else { waiters.removeFirst().resume() }
    }
}
#endif
