#if !SKIP
import Foundation
import LocalSRCore
#if os(iOS)
import UIKit
import BackgroundTasks
#endif

private final class TabletBackgroundState: @unchecked Sendable {
    private let lock = NSLock()
    private var background: Bool
    private let supportsGPU: Bool
    #if os(iOS)
    private var observers: [NSObjectProtocol] = []
    #endif
    init(background: Bool, supportsGPU: Bool) {
        self.background = background; self.supportsGPU = supportsGPU
        #if os(iOS)
        let center = NotificationCenter.default
        observers.append(center.addObserver(forName: UIApplication.didEnterBackgroundNotification, object: nil, queue: nil) { [weak self] _ in self?.set(true) })
        observers.append(center.addObserver(forName: UIApplication.willEnterForegroundNotification, object: nil, queue: nil) { [weak self] _ in self?.set(false) })
        #endif
    }
    deinit {
        #if os(iOS)
        for observer in observers { NotificationCenter.default.removeObserver(observer) }
        #endif
    }
    private func set(_ value: Bool) { lock.withLock { background = value } }
    var restricted: Bool { lock.withLock { background && !supportsGPU } }
}

private final class TabletWorkQueue: @unchecked Sendable {
    private let lock = NSLock()
    private var next = 0
    private let count: Int
    init(count: Int) { self.count = count }
    func take() -> Int? { lock.withLock { guard next < count else { return nil }; defer { next += 1 }; return next } }
}

/// Memory promised to a Full 4x output that is not allocated yet, so two workers can't both pass the check.
final class TabletMemoryBudget: @unchecked Sendable {
    private let lock = NSLock()
    private var reserved: Double = 0
    private let available: @Sendable () -> UInt64

    init(available: @escaping @Sendable () -> UInt64 = { AppleUpscaleService.availableMemory() }) {
        self.available = available
    }

    /// Reserves `bytes` when the free memory, less what other workers reserved, covers it.
    func reserve(_ bytes: Double) -> Bool {
        lock.withLock {
            guard Double(available()) - reserved >= bytes else { return false }
            reserved += bytes
            return true
        }
    }

    func release(_ bytes: Double) {
        lock.withLock { reserved = max(0, reserved - bytes) }
    }
}

public final class AppleTabletUpscaleService: TabletUpscaleService, @unchecked Sendable {
    public enum ServiceError: LocalizedError {
        case alreadyRunning, invalidPlan
        public var errorDescription: String? {
            switch self {
            case .alreadyRunning: return "Another batch is already running."
            case .invalidPlan: return "These steps can't run on this photo."
            }
        }
    }
    private let checker: TabletDeviceCheck
    private let registry = TabletModelRegistry()
    private let cacheRoot: URL
    private let lock = NSLock()
    private var cancellation: CancelFlag?
    public let laneActivity: AsyncStream<TabletLaneActivity>
    private let laneContinuation: AsyncStream<TabletLaneActivity>.Continuation
    private var selectedOverride: TabletLaneOverride = .both
    public var laneOverride: TabletLaneOverride {
        get { lock.withLock { selectedOverride } }
        set { lock.withLock { selectedOverride = newValue } }
    }
    /// Optional injection for testing background restrictions; nil observes the real iOS state.
    private let restriction: (@Sendable () -> Bool)?
    private let backgroundGPUGranted: Bool
    private let physicalMemory: Int64
    private let memoryBudget: TabletMemoryBudget
    /// Photos removed from the queue while their batch runs. Kept until the run that holds them ends.
    private var skippedIDs: Set<String> = []

    public var supportsBackgroundGPU: Bool {
        #if os(iOS)
        return BatchBackgroundTask.canRequestBackgroundGPU
        #else
        return true
        #endif
    }

    /// Set backgroundGPUGranted only when the continued-processing request actually acquired .gpu.
    /// `physicalMemory` and `availableMemory` exist for tests; nil uses the device's values.
    public init(defaults: UserDefaults = .standard, cachesDirectory: URL? = nil, backgroundGPUGranted: Bool = false, gpuRestricted: (@Sendable () -> Bool)? = nil, physicalMemory: Int64? = nil, availableMemory: (@Sendable () -> UInt64)? = nil) {
        let (stream, continuation) = AsyncStream<TabletLaneActivity>.makeStream(bufferingPolicy: .bufferingNewest(4))
        laneActivity = stream
        laneContinuation = continuation
        checker = TabletDeviceCheck(defaults: defaults)
        cacheRoot = (cachesDirectory ?? FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]).appendingPathComponent("LocalSR", isDirectory: true)
        restriction = gpuRestricted
        self.backgroundGPUGranted = backgroundGPUGranted
        self.physicalMemory = physicalMemory ?? Int64(clamping: ProcessInfo.processInfo.physicalMemory)
        memoryBudget = availableMemory.map { TabletMemoryBudget(available: $0) } ?? TabletMemoryBudget()
    }
    deinit { laneContinuation.finish() }
    public func cancel() { lock.withLock { cancellation?.cancel() } }

    public func skip(photoID: String) {
        _ = lock.withLock { skippedIDs.insert(photoID) }
    }

    private func isSkipped(_ photoID: String) -> Bool {
        lock.withLock { skippedIDs.contains(photoID) }
    }

    public func cachedVerdict(for model: TabletModelID) -> TabletChipVerdict? {
        guard let v = checker.cachedVerdict(for: model) else { return nil }
        return TabletChipVerdict(preferred: v.preferred, cpu: v.cpu, gpu: v.gpu, neuralEngine: v.neuralEngine, neuralEngineChecked: v.neuralEngineChecked)
    }

    public func isModelBundled(_ model: TabletModelID) -> Bool {
        (try? AppleResources.tabletModelURL(model)) != nil
    }

    public func hasModelFailedCheck(_ model: TabletModelID) -> Bool {
        checker.hasFailedCheck(for: model)
    }

    public func bundledModelByteSize(_ model: TabletModelID) -> Int64? {
        AppleResources.tabletModelByteSize(model)
    }

    public func run(
        plan: Plan,
        inputs: [TabletInput],
        fullScale: Bool,
        targetScale: Int,
        outputFormat: TabletOutputFormat,
        events: @escaping @Sendable (TabletUpscaleEvent) -> Void
    ) async throws -> [TabletUpscaleResult] {
        guard plan.isValid && inputs.allSatisfy({ ($0.plan ?? plan).isValid }) else { throw ServiceError.invalidPlan }
        for input in inputs {
            for model in (input.plan ?? plan).stages where !isModelBundled(model) {
                throw TabletServiceError.modelNotIncluded(model)
            }
        }
        let cancel = CancelFlag()
        let accepted = lock.withLock { () -> Bool in
            if cancellation != nil { return false }
            cancellation = cancel
            return true
        }
        guard accepted else { throw ServiceError.alreadyRunning }
        let inputIDs = Set(inputs.map(\.id))
        defer { lock.withLock { cancellation = nil; skippedIDs.subtract(inputIDs) } }
        return try await withTaskCancellationHandler {
            var verdicts: [TabletModelID: TabletDeviceVerdict] = [:]
            for input in inputs {
                for model in (input.plan ?? plan).stages where verdicts[model] == nil {
                    if cancel.isCancelled { throw CancellationError() }
                    do {
                        let v = try await checker.verdict(for: model)
                        verdicts[model] = v
                        events(.verdict(
                            model: model,
                            verdict: TabletChipVerdict(preferred: v.preferred, cpu: v.cpu, gpu: v.gpu, neuralEngine: v.neuralEngine, neuralEngineChecked: v.neuralEngineChecked)
                        ))
                    } catch {
                        let ns = error as NSError
                        if ns.domain == "LocalSR" && ns.code == 1 {
                            events(.verdictFailed(model: model))
                        }
                        throw error
                    }
                }
            }
            #if os(iOS)
            let background = await MainActor.run { UIApplication.shared.applicationState == .background }
            let bgGPUAllowed = (backgroundGPUGranted || BatchBackgroundTask.shared.hasGrantedGPU) && BGTaskScheduler.supportedResources.contains(.gpu)
            let backgroundState = TabletBackgroundState(background: background, supportsGPU: bgGPUAllowed)
            #else
            let backgroundState = TabletBackgroundState(background: false, supportsGPU: true)
            #endif
            let restricted = restriction ?? { backgroundState.restricted }
            let scheduler = LaneScheduler(gpuRestricted: restricted, onPrediction: { photo, stage, model, lane, start, end in
                events(.prediction(photoID: photo, stage: stage, model: model, lane: lane, start: start, end: end))
            }) { activity in
                self.laneContinuation.yield(activity)
                events(.lane(activity))
            }
            let queue = TabletWorkQueue(count: inputs.count)
            let selected = laneOverride
            let checked = verdicts
            for (model, verdict) in checked { try scheduler.validate(model: model, verdict: verdict, override: selected) }
            // One photo at a time on iPads with less than 6 GB, so two large photos can't exhaust memory.
            let workers = TabletMemoryPolicy.workerCount(physicalMemory: physicalMemory, photoCount: inputs.count)
            return try await withThrowingTaskGroup(of: [(Int, TabletUpscaleResult)].self) { group in
                for _ in 0..<workers {
                    group.addTask {
                        var results: [(Int, TabletUpscaleResult)] = []
                        while !cancel.isCancelled, let index = queue.take() {
                            let input = inputs[index]
                            if self.isSkipped(input.id) { continue }
                            do {
                                let result = try self.process(
                                    input: input,
                                    plan: input.plan ?? plan,
                                    fullScale: fullScale,
                                    targetScale: targetScale,
                                    outputFormat: outputFormat,
                                    verdicts: checked,
                                    scheduler: scheduler,
                                    override: selected,
                                    restricted: restricted,
                                    cancel: cancel,
                                    events: events
                                )
                                results.append((index, result))
                                events(.completed(result))
                            } catch {
                                if cancel.isCancelled || error is CancellationError {
                                    cancel.cancel()
                                    throw CancellationError()
                                }
                                // Only this photo failed: report it and go on with the next one.
                                events(.failed(TabletPhotoFailure(
                                    photoID: input.id,
                                    message: TabletErrorText.message(for: error),
                                    isOutOfSpace: TabletPlatform.isOutOfSpaceError(error)
                                )))
                            }
                        }
                        if cancel.isCancelled { throw CancellationError() }
                        return results
                    }
                }
                var results: [(Int, TabletUpscaleResult)] = []
                for try await batch in group { results.append(contentsOf: batch) }
                return results.sorted { $0.0 < $1.0 }.map { $0.1 }
            }
        } onCancel: { cancel.cancel() }
    }

    private func process(
        input: TabletInput,
        plan: Plan,
        fullScale: Bool,
        targetScale: Int,
        outputFormat: TabletOutputFormat,
        verdicts: [TabletModelID: TabletDeviceVerdict],
        scheduler: LaneScheduler,
        override: TabletLaneOverride,
        restricted: @escaping @Sendable () -> Bool,
        cancel: CancelFlag,
        events: @escaping @Sendable (TabletUpscaleEvent) -> Void
    ) throws -> TabletUpscaleResult {
        let started = Date()
        let directory = cacheRoot.appendingPathComponent(UUID().uuidString, isDirectory: true)
        var reservedBytes = 0.0
        defer { if reservedBytes > 0 { memoryBudget.release(reservedBytes) } }
        do {
            let upscale = plan.stages.contains { ModelCatalog.model($0).scale == 4 }
            if !upscale, let size = ImageIOCodec.uprightPixelSize(url: input.url),
               TabletMemoryPolicy.isRestoreInputTooLarge(width: size.width, height: size.height) {
                throw TabletServiceError.restoreTooLarge
            }
            let decoded = try ImageIOCodec.decode(url: input.url, preserveDimensions: !upscale)
            let policy = SizePolicy.target(width: decoded.originalWidth, height: decoded.originalHeight)
            let needed = Double(decoded.pixels.width) * Double(decoded.pixels.height) * 16 * 4 * 2.5
            // The reservation makes the check and the allocation one step for both workers.
            let wantsFull = upscale && targetScale != 2 && fullScale && policy.outputScale == 2
            let canUpgradeFull = wantsFull && memoryBudget.reserve(needed)
            if canUpgradeFull { reservedBytes = needed }
            let scale: Int
            let fullScaleFallback: Bool
            if !upscale {
                scale = 1
                fullScaleFallback = false
            } else if targetScale == 2 {
                scale = 2
                fullScaleFallback = false
            } else {
                scale = canUpgradeFull ? 4 : policy.outputScale
                fullScaleFallback = fullScale && policy.outputScale == 2 && !canUpgradeFull
            }
            var pixels = decoded.pixels
            for (index, model) in plan.stages.enumerated() {
                let stageScale = ModelCatalog.model(model).scale == 1 ? 1 : scale
                while true {
                    do {
                        let stageStart = Date()
                        var usedLane: ModelLane = .neuralEngine
                        pixels = try scheduler.run(photoID: input.id, stage: index, model: model, verdict: verdicts[model]!, override: override, isCancelled: { cancel.isCancelled }) { unit, lane in
                            usedLane = lane
                            return try AppleTabletStageRunner.run(source: pixels, model: model, unit: unit, outputScale: stageScale, registry: registry, isCancelled: { cancel.isCancelled }, gpuRestricted: restricted, predictionStarted: { scheduler.predictionStarted(lane) }, predictionEnded: { scheduler.predictionEnded(lane) }) { done, total in
                                events(.progress(photoID: input.id, stage: index, fraction: Double(done) / Double(total)))
                            }
                        }
                        events(.stageTiming(photoID: input.id, stage: index, model: model, lane: usedLane, seconds: Date().timeIntervalSince(stageStart)))
                        break
                    } catch AppleTabletStageRunner.StageError.gpuRestricted { continue }
                }
            }
            if cancel.isCancelled { throw CancellationError() }
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let output = directory.appendingPathComponent("output.\(outputFormat.fileExtension)")
            let preview = directory.appendingPathComponent("preview.jpg")
            _ = try ImageIOCodec.encodeOutput(pixels: pixels, colorSpace: decoded.colorSpace, decoded: decoded, format: outputFormat, to: output)
            if cancel.isCancelled { throw CancellationError() }
            _ = try ImageIOCodec.writePreviewJPEG(pixels: pixels, colorSpace: decoded.colorSpace, to: preview)
            if cancel.isCancelled { throw CancellationError() }
            return TabletUpscaleResult(
                id: input.id,
                inputURL: input.url,
                outputURL: output,
                previewURL: preview,
                width: pixels.width,
                height: pixels.height,
                outputScale: scale,
                fullScaleFallback: fullScaleFallback,
                seconds: Date().timeIntervalSince(started),
                plan: plan
            )
        } catch {
            try? FileManager.default.removeItem(at: directory)
            throw error
        }
    }
}
#endif
