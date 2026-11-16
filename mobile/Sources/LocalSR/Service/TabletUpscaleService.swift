import Foundation
import LocalSRCore

public enum TabletLaneOverride: Sendable { case both, neuralEngineOnly, gpuOnly }

public struct TabletLaneActivity: Sendable {
    public let lane: ModelLane
    public let photoID: String?
    public let stage: Int?
    public let model: TabletModelID?
    public let unit: ComputeUnit?
    /// Fraction of the preceding second spent inside predictions.
    public let busyFraction: Double
    public init(lane: ModelLane, photoID: String?, stage: Int?, model: TabletModelID?, unit: ComputeUnit?, busyFraction: Double) {
        self.lane = lane; self.photoID = photoID; self.stage = stage; self.model = model; self.unit = unit; self.busyFraction = busyFraction
    }
}

public struct TabletInput: Sendable {
    public let id: String
    public let url: URL
    /// A per-photo plan allows mixed batches; nil uses the batch plan.
    public let plan: Plan?
    public init(id: String, url: URL, plan: Plan? = nil) { self.id = id; self.url = url; self.plan = plan }
}

public struct TabletUpscaleResult: Sendable {
    public let id: String
    public let inputURL: URL
    public let outputURL: URL
    public let previewURL: URL
    public let width: Int
    public let height: Int
    public let outputScale: Int
    public let seconds: Double
    public let plan: Plan
}

public enum TabletUpscaleEvent: Sendable {
    case progress(photoID: String, stage: Int, fraction: Double)
    case completed(TabletUpscaleResult)
    case lane(TabletLaneActivity)
    case prediction(photoID: String, stage: Int, model: TabletModelID, lane: ModelLane, start: Double, end: Double)
}

public protocol TabletUpscaleService {
    var laneOverride: TabletLaneOverride { get set }
    /// Latest activity for both lanes; events are also delivered to the batch callback.
    var laneActivity: AsyncStream<TabletLaneActivity> { get }
    func run(plan: Plan, inputs: [TabletInput], fullScale: Bool, events: @escaping @Sendable (TabletUpscaleEvent) -> Void) async throws -> [TabletUpscaleResult]
    func cancel()
}

public func makeTabletUpscaleService() -> TabletUpscaleService {
    #if SKIP
    return AndroidTabletUpscaleService()
    #else
    return AppleTabletUpscaleService()
    #endif
}
