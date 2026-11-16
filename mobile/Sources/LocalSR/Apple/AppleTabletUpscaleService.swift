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

public final class AppleTabletUpscaleService: TabletUpscaleService, @unchecked Sendable {
    public enum ServiceError: Error { case alreadyRunning, invalidPlan }
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

    public var supportsBackgroundGPU: Bool {
        #if os(iOS)
        return BGTaskScheduler.supportedResources.contains(.gpu)
        #else
        return true
        #endif
    }

    /// Set backgroundGPUGranted only when the continued-processing request actually acquired .gpu.
    public init(defaults: UserDefaults = .standard, cachesDirectory: URL? = nil, backgroundGPUGranted: Bool = false, gpuRestricted: (@Sendable () -> Bool)? = nil) {
        let (stream, continuation) = AsyncStream<TabletLaneActivity>.makeStream(bufferingPolicy: .bufferingNewest(4))
        laneActivity = stream
        laneContinuation = continuation
        checker = TabletDeviceCheck(defaults: defaults)
        cacheRoot = (cachesDirectory ?? FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]).appendingPathComponent("LocalSR", isDirectory: true)
        restriction = gpuRestricted
        self.backgroundGPUGranted = backgroundGPUGranted
    }
    deinit { laneContinuation.finish() }
    public func cancel() { lock.withLock { cancellation?.cancel() } }

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
        let cancel = CancelFlag()
        let accepted = lock.withLock { () -> Bool in
            if cancellation != nil { return false }
            cancellation = cancel
            return true
        }
        guard accepted else { throw ServiceError.alreadyRunning }
        defer { lock.withLock { cancellation = nil } }
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
            return try await withThrowingTaskGroup(of: [(Int, TabletUpscaleResult)].self) { group in
                for _ in 0..<min(2, inputs.count) {
                    group.addTask {
                        var results: [(Int, TabletUpscaleResult)] = []
                        while !cancel.isCancelled, let index = queue.take() {
                            do {
                                let input = inputs[index]
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
                            } catch { cancel.cancel(); throw error }
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
        do {
            let upscale = plan.stages.contains { ModelCatalog.model($0).scale == 4 }
            let decoded = try ImageIOCodec.decode(url: input.url, preserveDimensions: !upscale)
            let policy = SizePolicy.target(width: decoded.originalWidth, height: decoded.originalHeight)
            let needed = Double(decoded.pixels.width) * Double(decoded.pixels.height) * 16 * 4 * 2.5
            let canUpgradeFull = fullScale && policy.outputScale == 2 && Double(AppleUpscaleService.availableMemory()) >= needed
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
