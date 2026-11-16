#if SKIP
import Foundation
import LocalSRCore

/// Runs the tablet models in order on the existing Kotlin pixel pipeline.
public final class AndroidTabletUpscaleService: TabletUpscaleService {
    private let engine: AndroidTabletEngine
    private let activityStream: AndroidTabletActivityStream
    private var observedVerdicts: [TabletModelID: TabletChipVerdict] = [:]
    private var observedCPUOnly: Bool = false
    private var observedGPU: Bool = false
    public var laneActivity: AsyncStream<TabletLaneActivity> { activityStream.stream }
    public var laneOverride: TabletLaneOverride = .both
    public var supportsBackgroundGPU: Bool { true }
    /// The engine takes the first accelerator that loads; there is no device check yet (TABLET-DESIGN.md 7).
    public var runsDeviceCheck: Bool { false }
    /// Photos removed from the queue while their batch runs. Read and written on the main thread only.
    private var skippedIDs: Set<String> = []
    private var cancelRequested = false

    public var availableLanes: [ModelLane] {
        let hasNPU = engine.hasNPULane()
        if hasNPU {
            return [.neuralEngine, .gpu]
        }
        if observedCPUOnly && !observedGPU {
            return [.cpu]
        }
        return [.gpu]
    }

    public init() {
        activityStream = AndroidTabletActivityStream()
        engine = AndroidTabletEngine(ProcessInfo.processInfo.androidContext)
    }
    public func cancel() {
        cancelRequested = true
        engine.cancel()
    }

    public func skip(photoID: String) {
        skippedIDs.insert(photoID)
    }

    public func updateForegroundNotification(title: String, detail: String?, fraction: Double) {
        UpscaleForegroundService.setStatus(ProcessInfo.processInfo.androidContext, title, detail, fraction)
    }

    public func cachedVerdict(for model: TabletModelID) -> TabletChipVerdict? {
        observedVerdicts[model]
    }

    public func isModelBundled(_ model: TabletModelID) -> Bool {
        engine.isModelBundled(ModelCatalog.model(model).androidFileName)
    }

    public func hasModelFailedCheck(_ model: TabletModelID) -> Bool {
        false
    }

    public func bundledModelByteSize(_ model: TabletModelID) -> Int64? {
        engine.bundledModelByteSize(ModelCatalog.model(model).androidFileName)
    }

    private func laneForUnitCode(_ code: Int) -> ModelLane {
        switch code {
        case 2: return .neuralEngine
        case 1: return .gpu
        default: return .cpu
        }
    }

    private func unitForUnitCode(_ code: Int) -> ComputeUnit {
        switch code {
        case 2: return .npu
        case 1: return .gpu
        default: return .cpu
        }
    }

    public func run(plan: Plan, inputs: [TabletInput], fullScale: Bool, targetScale: Int, outputFormat: TabletOutputFormat, events: @escaping @Sendable (TabletUpscaleEvent) -> Void) async throws -> [TabletUpscaleResult] {
        guard plan.isValid && inputs.allSatisfy({ ($0.plan ?? plan).isValid }) else { throw TabletServiceError.invalidPlan }
        for input in inputs {
            for model in (input.plan ?? plan).stages where !isModelBundled(model) {
                throw TabletServiceError.modelNotIncluded(model)
            }
        }
        if laneOverride == .neuralEngineOnly && !engine.hasNPULane() {
            throw TabletServiceError.noNPU
        }
        guard engine.start() else { throw TabletServiceError.alreadyRunning }
        cancelRequested = false
        defer {
            engine.finish()
            for input in inputs {
                skippedIDs.remove(input.id)
            }
        }
        try await UpscaleForegroundService.acquire(ProcessInfo.processInfo.androidContext)
        defer { UpscaleForegroundService.release(ProcessInfo.processInfo.androidContext) }
        let laneMode: Int = {
            switch laneOverride {
            case .both: return 0
            case .neuralEngineOnly: return 1
            case .gpuOnly: return 2
            }
        }()
        var results: [TabletUpscaleResult] = []
        for input in inputs {
            if skippedIDs.contains(input.id) { continue }
            if cancelRequested || Task.isCancelled { throw CancellationError() }
            let selected = input.plan ?? plan
            do {
                let raw = try await engine.process(input.url.absoluteString, selected, fullScale, targetScale, outputFormat.rawValue, laneMode, { stage, fraction in
                    events(.progress(photoID: input.id, stage: stage, fraction: fraction))
                }, { stage, unitCode, start, end in
                    let lane = self.laneForUnitCode(unitCode)
                    events(.prediction(photoID: input.id, stage: stage, model: selected.stages[stage], lane: lane, start: start, end: end))
                }, { stage, unitCode, busy in
                    let active = stage >= 0
                    let lane = self.laneForUnitCode(unitCode)
                    let unit = self.unitForUnitCode(unitCode)
                    let activity = TabletLaneActivity(lane: lane, photoID: active ? input.id : nil, stage: active ? stage : nil, model: active ? selected.stages[stage] : nil, unit: active ? unit : nil, busyFraction: busy)
                    activityStream.emit(activity)
                    events(.lane(activity))
                }, { stage, unitCode, seconds in
                    let modelID = selected.stages[stage]
                    let lane = self.laneForUnitCode(unitCode)
                    let unit = self.unitForUnitCode(unitCode)
                    if unit == .gpu {
                        self.observedGPU = true
                    } else if unit == .cpu {
                        self.observedCPUOnly = true
                    }
                    let verdict = TabletChipVerdict(
                        preferred: unit,
                        cpu: true,
                        gpu: unit == .gpu,
                        neuralEngine: unit == .npu,
                        neuralEngineChecked: unit == .npu
                    )
                    self.observedVerdicts[modelID] = verdict
                    events(.verdict(model: modelID, verdict: verdict))
                    events(.stageTiming(photoID: input.id, stage: stage, model: modelID, lane: lane, seconds: seconds))
                })
                let result = TabletUpscaleResult(
                    id: input.id,
                    inputURL: input.url,
                    outputURL: URL(fileURLWithPath: raw.outputPath),
                    previewURL: URL(fileURLWithPath: raw.previewPath),
                    width: raw.width,
                    height: raw.height,
                    outputScale: raw.scale,
                    fullScaleFallback: raw.fullScaleFallback,
                    seconds: raw.seconds,
                    plan: selected
                )
                results.append(result)
                events(.completed(result))
            } catch {
                if cancelRequested || Task.isCancelled { throw error }
                // Only this photo failed: report it and go on with the next one.
                events(.failed(TabletPhotoFailure(
                    photoID: input.id,
                    message: TabletErrorText.message(for: error),
                    isOutOfSpace: TabletPlatform.isOutOfSpaceError(error)
                )))
            }
        }
        return results
    }
}
#endif
