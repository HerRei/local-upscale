#if SKIP
import Foundation
import LocalSRCore

/// Runs the tablet models in order on the existing Kotlin pixel pipeline.
public final class AndroidTabletUpscaleService: TabletUpscaleService {
    private let engine: AndroidTabletEngine
    private let activityStream: AndroidTabletActivityStream
    public var laneActivity: AsyncStream<TabletLaneActivity> { activityStream.stream }
    public var laneOverride: TabletLaneOverride = .both
    public init() {
        activityStream = AndroidTabletActivityStream()
        engine = AndroidTabletEngine(ProcessInfo.processInfo.androidContext)
    }
    public func cancel() { engine.cancel() }

    public func run(plan: Plan, inputs: [TabletInput], fullScale: Bool, events: @escaping @Sendable (TabletUpscaleEvent) -> Void) async throws -> [TabletUpscaleResult] {
        guard plan.isValid && inputs.allSatisfy({ ($0.plan ?? plan).isValid }) else { throw NSError(domain: "LocalSR", code: 1) }
        guard laneOverride != .neuralEngineOnly else { throw NSError(domain: "LocalSR", code: 3) }
        guard engine.start() else { throw NSError(domain: "LocalSR", code: 2) }
        defer { engine.finish() }
        try await UpscaleForegroundService.acquire(ProcessInfo.processInfo.androidContext)
        defer { UpscaleForegroundService.release(ProcessInfo.processInfo.androidContext) }
        var results: [TabletUpscaleResult] = []
        for input in inputs {
            let selected = input.plan ?? plan
            let raw = try await engine.process(input.url.absoluteString, selected, fullScale, laneOverride == .gpuOnly, { stage, fraction in
                events(.progress(photoID: input.id, stage: stage, fraction: fraction))
            }, { stage, gpu, start, end in
                events(.prediction(photoID: input.id, stage: stage, model: selected.stages[stage], lane: gpu ? .gpu : .cpu, start: start, end: end))
            }, { stage, gpu, busy in
                let active = stage >= 0
                let activity = TabletLaneActivity(lane: gpu ? .gpu : .cpu, photoID: active ? input.id : nil, stage: active ? stage : nil, model: active ? selected.stages[stage] : nil, unit: active ? (gpu ? .gpu : .cpu) : nil, busyFraction: busy)
                activityStream.emit(activity)
                events(.lane(activity))
            })
            let result = TabletUpscaleResult(id: input.id, inputURL: input.url, outputURL: URL(fileURLWithPath: raw.outputPath), previewURL: URL(fileURLWithPath: raw.previewPath), width: raw.width, height: raw.height, outputScale: raw.scale, seconds: raw.seconds, plan: selected)
            results.append(result)
            events(.completed(result))
        }
        return results
    }
}
#endif
