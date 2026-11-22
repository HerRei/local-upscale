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
    public let fullScaleFallback: Bool
    public let seconds: Double
    public let plan: Plan

    public init(
        id: String,
        inputURL: URL,
        outputURL: URL,
        previewURL: URL,
        width: Int,
        height: Int,
        outputScale: Int,
        fullScaleFallback: Bool = false,
        seconds: Double,
        plan: Plan
    ) {
        self.id = id
        self.inputURL = inputURL
        self.outputURL = outputURL
        self.previewURL = previewURL
        self.width = width
        self.height = height
        self.outputScale = outputScale
        self.fullScaleFallback = fullScaleFallback
        self.seconds = seconds
        self.plan = plan
    }
}

/// One photo the service could not finish. The rest of the batch carries on.
public struct TabletPhotoFailure: Sendable {
    public let photoID: String
    /// Text for the photo's row and the viewer, already readable.
    public let message: String
    public let isOutOfSpace: Bool
    public init(photoID: String, message: String, isOutOfSpace: Bool = false) {
        self.photoID = photoID; self.message = message; self.isOutOfSpace = isOutOfSpace
    }
}

/// Errors the tablet services raise themselves, with the text the app shows.
public enum TabletServiceError: Error, Sendable {
    /// The build has no file for this model (a fresh clone without Models/ios-tablet or android-tablet).
    case modelNotIncluded(TabletModelID)
    /// Restore keeps the size, so very large photos would not fit in memory.
    case restoreTooLarge
    case invalidPlan
    case alreadyRunning
    /// "NPU Only" on a tablet without a usable NPU.
    case noNPU

    public var message: String {
        switch self {
        case .modelNotIncluded:
            return "This model isn't included in this build."
        case .restoreTooLarge:
            return "This photo is too large to restore. Restore works on photos up to \(TabletMemoryPolicy.maxRestorePixels / 1_000_000) MP."
        case .invalidPlan:
            return "These steps can't run on this photo."
        case .alreadyRunning:
            return "Another batch is already running."
        case .noNPU:
            return "This tablet has no NPU LocalSR can use. Choose Both Chips or GPU Only."
        }
    }
}

#if !SKIP
extension TabletServiceError: LocalizedError {
    public var errorDescription: String? { message }
}
#endif

public enum TabletErrorText {
    /// Readable text for an error from a tablet service, for a photo's row and the viewer.
    public static func message(for error: Error) -> String {
        if let own = error as? TabletServiceError {
            return own.message
        }
        #if SKIP
        // Kotlin exceptions reach Swift wrapped; their own message is the readable part.
        if let text = AndroidTabletErrors.message(error) {
            return text
        }
        #endif
        let localized = error.localizedDescription
        return localized.isEmpty ? String(describing: error) : localized
    }
}

public enum TabletUpscaleEvent: Sendable {
    case progress(photoID: String, stage: Int, fraction: Double)
    case completed(TabletUpscaleResult)
    /// The photo failed; only this photo is marked failed.
    case failed(TabletPhotoFailure)
    case lane(TabletLaneActivity)
    case prediction(photoID: String, stage: Int, model: TabletModelID, lane: ModelLane, start: Double, end: Double)
    case verdict(model: TabletModelID, verdict: TabletChipVerdict)
    case verdictFailed(model: TabletModelID)
    case stageTiming(photoID: String, stage: Int, model: TabletModelID, lane: ModelLane, seconds: Double)
}

public protocol TabletUpscaleService {
    var laneOverride: TabletLaneOverride { get set }
    /// Latest activity for both lanes; events are also delivered to the batch callback.
    var laneActivity: AsyncStream<TabletLaneActivity> { get }
    /// True when this build can keep the GPU in the background (iOS: the continued-processing GPU
    /// entitlement is provisioned and the iPad supports it). Whether a batch actually got it is
    /// `BatchBackgroundTask.shared.hasGrantedGPU`.
    var supportsBackgroundGPU: Bool { get }
    var availableLanes: [ModelLane] { get }
    /// False when the service picks a chip without a device check (Android tablets for now).
    var runsDeviceCheck: Bool { get }
    func run(plan: Plan, inputs: [TabletInput], fullScale: Bool, targetScale: Int, outputFormat: TabletOutputFormat, events: @escaping @Sendable (TabletUpscaleEvent) -> Void) async throws -> [TabletUpscaleResult]
    func cachedVerdict(for model: TabletModelID) -> TabletChipVerdict?
    func isModelBundled(_ model: TabletModelID) -> Bool
    func hasModelFailedCheck(_ model: TabletModelID) -> Bool
    func bundledModelByteSize(_ model: TabletModelID) -> Int64?
    func updateForegroundNotification(title: String, detail: String?, fraction: Double)
    /// Leaves this photo out of the running batch (removed from the queue while it waited).
    func skip(photoID: String)
    func cancel()
}

public extension TabletUpscaleService {
    var availableLanes: [ModelLane] { [.neuralEngine, .gpu] }
    var runsDeviceCheck: Bool { true }
    func updateForegroundNotification(title: String, detail: String?, fraction: Double) {}
    func run(plan: Plan, inputs: [TabletInput], fullScale: Bool, events: @escaping @Sendable (TabletUpscaleEvent) -> Void) async throws -> [TabletUpscaleResult] {
        try await run(plan: plan, inputs: inputs, fullScale: fullScale, targetScale: 4, outputFormat: .heic, events: events)
    }
}

public func makeTabletUpscaleService() -> TabletUpscaleService {
    #if SKIP
    return AndroidTabletUpscaleService()
    #else
    return AppleTabletUpscaleService()
    #endif
}
