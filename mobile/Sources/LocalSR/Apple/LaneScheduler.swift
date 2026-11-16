#if !SKIP
import Foundation
import LocalSRCore

/// Serializes each lane independently. Callers may schedule different photos concurrently.
/// CPU fallbacks use the Neural Engine lane, so they cannot race its engine or misreport the GPU as busy.
public final class LaneScheduler: @unchecked Sendable {
    public enum ScheduleError: Error { case unavailable, cancelled }
    private struct State {
        var photo: String?
        var stage: Int?
        var model: TabletModelID?
        var unit: ComputeUnit?
        var predictionStart: TimeInterval?
        var intervals: [(TimeInterval, TimeInterval)] = []
    }
    private let lock = NSLock()
    private let ane = DispatchSemaphore(value: 1)
    private let gpu = DispatchSemaphore(value: 1)
    private var states: [ModelLane: State] = [.neuralEngine: State(), .gpu: State()]
    private let timer: DispatchSourceTimer
    private let onActivity: @Sendable (TabletLaneActivity) -> Void
    private let onPrediction: @Sendable (String, Int, TabletModelID, ModelLane, Double, Double) -> Void
    /// True when background execution cannot use the GPU. The caller supplies current application state.
    private let gpuRestricted: @Sendable () -> Bool

    public init(gpuRestricted: @escaping @Sendable () -> Bool = { false }, onPrediction: @escaping @Sendable (String, Int, TabletModelID, ModelLane, Double, Double) -> Void = { _, _, _, _, _, _ in }, onActivity: @escaping @Sendable (TabletLaneActivity) -> Void = { _ in }) {
        self.gpuRestricted = gpuRestricted
        self.onActivity = onActivity
        self.onPrediction = onPrediction
        timer = DispatchSource.makeTimerSource(queue: DispatchQueue(label: "localsr.lane.activity"))
        timer.schedule(deadline: .now(), repeating: .milliseconds(500))
        timer.setEventHandler { [weak self] in self?.publish() }
        timer.resume()
    }
    deinit { timer.cancel() }

    private func candidates(model: TabletModelID, verdict: TabletDeviceVerdict, override: TabletLaneOverride) throws -> [ComputeUnit] {
        let restricted = gpuRestricted()
        var list: [ComputeUnit] = []
        switch override {
        case .gpuOnly:
            if !restricted && verdict.gpu { list.append(.gpu) }
            else if restricted && verdict.neuralEngine { list.append(.npu) }
            else if restricted && verdict.cpu { list.append(.cpu) }
        case .neuralEngineOnly:
            if verdict.neuralEngine { list.append(.npu) }
        case .both:
            if verdict.preferred == .gpu && !restricted && verdict.gpu { list.append(.gpu) }
            if verdict.preferred == .npu && verdict.neuralEngine { list.append(.npu) }
            if !restricted && verdict.gpu && !list.contains(.gpu) { list.append(.gpu) }
            if verdict.neuralEngine && !list.contains(.npu) { list.append(.npu) }
            if list.isEmpty && verdict.cpu { list.append(.cpu) }
        }
        if list.isEmpty { throw ScheduleError.unavailable }
        return list
    }

    func validate(model: TabletModelID, verdict: TabletDeviceVerdict, override: TabletLaneOverride) throws {
        _ = try candidates(model: model, verdict: verdict, override: override)
    }

    /// Acquires a lane for a stage. The synchronous body executes on the caller's worker thread.
    func run<T>(photoID: String, stage: Int, model: TabletModelID, verdict: TabletDeviceVerdict,
                override: TabletLaneOverride, isCancelled: () -> Bool,
                body: (ComputeUnit, ModelLane) throws -> T) throws -> T {
        while true {
            if isCancelled() { throw ScheduleError.cancelled }
            let options = try candidates(model: model, verdict: verdict, override: override)
            var acquired: (ComputeUnit, ModelLane, DispatchSemaphore)?
            for option in options {
                let lane: ModelLane = option == .gpu ? .gpu : .neuralEngine
                let semaphore = lane == .gpu ? gpu : ane
                if semaphore.wait(timeout: .now()) == .success {
                    if lane == .gpu && gpuRestricted() {
                        semaphore.signal()
                        continue
                    }
                    acquired = (option, lane, semaphore)
                    break
                }
            }
            if acquired == nil, let primary = options.first {
                let lane: ModelLane = primary == .gpu ? .gpu : .neuralEngine
                let semaphore = lane == .gpu ? gpu : ane
                if semaphore.wait(timeout: .now() + .milliseconds(50)) == .success {
                    if lane == .gpu && gpuRestricted() {
                        semaphore.signal()
                    } else {
                        acquired = (primary, lane, semaphore)
                    }
                }
            }
            guard let (unit, lane, semaphore) = acquired else { continue }
            lock.withLock {
                states[lane]?.photo = photoID
                states[lane]?.stage = stage
                states[lane]?.model = model
                states[lane]?.unit = unit
            }
            publish()
            defer {
                lock.withLock { states[lane]?.photo = nil; states[lane]?.stage = nil; states[lane]?.model = nil; states[lane]?.unit = nil }
                semaphore.signal()
                publish()
            }
            if isCancelled() { throw ScheduleError.cancelled }
            return try body(unit, lane)
        }
    }

    func predictionStarted(_ lane: ModelLane) {
        lock.withLock { states[lane]?.predictionStart = ProcessInfo.processInfo.systemUptime }
    }
    func predictionEnded(_ lane: ModelLane) {
        let now = ProcessInfo.processInfo.systemUptime
        let finished: (String, Int, TabletModelID, Double)? = lock.withLock {
            let state = states[lane]!
            if let start = state.predictionStart { states[lane]?.intervals.append((start, now)) }
            states[lane]?.predictionStart = nil
            guard let photo = state.photo, let stage = state.stage, let model = state.model, let start = state.predictionStart else { return nil }
            return (photo, stage, model, start)
        }
        if let (photo, stage, model, start) = finished { onPrediction(photo, stage, model, lane, start, now) }
    }
    private func publish() {
        let now = ProcessInfo.processInfo.systemUptime
        let snapshots: [TabletLaneActivity] = lock.withLock {
            [ModelLane.neuralEngine, .gpu].map { lane in
                var state = states[lane]!
                state.intervals.removeAll { $0.1 <= now - 1 }
                let active = state.intervals.reduce(0.0) { $0 + max(0, min(now, $1.1) - max(now - 1, $1.0)) }
                    + (state.predictionStart.map { max(0, now - max(now - 1, $0)) } ?? 0)
                states[lane] = state
                return TabletLaneActivity(lane: lane, photoID: state.photo, stage: state.stage, model: state.model, unit: state.unit, busyFraction: min(1, active))
            }
        }
        snapshots.forEach(onActivity)
    }
}
#endif
