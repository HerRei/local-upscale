import Foundation
import Observation
import SwiftUI
import LocalSRCore

#if !SKIP
private final class TabletServiceBox: @unchecked Sendable {
    var service: TabletUpscaleService

    init(_ service: TabletUpscaleService) {
        self.service = service
    }

    func run(
        plan: Plan,
        inputs: [TabletInput],
        fullScale: Bool,
        events: @escaping @Sendable (TabletUpscaleEvent) -> Void
    ) async throws -> [TabletUpscaleResult] {
        try await service.run(plan: plan, inputs: inputs, fullScale: fullScale, events: events)
    }

    func cancel() {
        service.cancel()
    }
}
#else
private final class TabletServiceBox: @unchecked Sendable {
    var service: TabletUpscaleService

    init(_ service: TabletUpscaleService) {
        self.service = service
    }

    func run(
        plan: Plan,
        inputs: [TabletInput],
        fullScale: Bool,
        events: @escaping @Sendable (TabletUpscaleEvent) -> Void
    ) async throws -> [TabletUpscaleResult] {
        try await service.run(plan: plan, inputs: inputs, fullScale: fullScale, events: events)
    }

    func cancel() {
        service.cancel()
    }
}
#endif

enum TabletMediaSegment: String, CaseIterable, Sendable {
    case queue
    case results
}

enum TabletWorkspaceTab: String, CaseIterable, Sendable {
    case media
    case viewer
    case enhance
}

enum TabletSelection: Equatable, Hashable, Sendable {
    case queue(String)
    case result(String)
}

enum TabletQueueItemStatus: Equatable, Sendable {
    case waiting
    case running
    case done
    case failed
}

struct TabletQueueItem: Identifiable, Equatable, Sendable {
    let id: String
    let inputURL: URL
    var displayName: String
    var pixelWidth: Int?
    var pixelHeight: Int?
    var isPreparingPreview: Bool
    var status: TabletQueueItemStatus
    var stageIndex: Int
    var stageCount: Int
    var stageName: String?
    var chipName: String?
    var progress: Double
    var resultID: String?
    var outputWidth: Int?
    var outputHeight: Int?
    var errorMessage: String?

    var dimensionsText: String? {
        guard let w = pixelWidth, let h = pixelHeight, w > 0, h > 0 else { return nil }
        return UIFormatting.resultDimensionsLine(width: w, height: h)
    }
}

struct TabletResultItem: Identifiable, Codable, Equatable, Sendable {
    let id: String
    let displayName: String
    let inputURL: URL
    let outputURL: URL
    let previewURL: URL
    let inputWidth: Int?
    let inputHeight: Int?
    let width: Int
    let height: Int
    let outputScale: Int
    let seconds: Double
    let stages: [TabletModelID]
    let chipsLabel: String
    let createdAt: Date
}

enum TabletBatchCompletion: Equatable, Sendable {
    case complete(count: Int, seconds: Double)
    case partialFailure(succeeded: Int, total: Int, failed: Int)
}

@MainActor
@Observable
final class TabletModel {
    private let serviceBox: TabletServiceBox
    private let storageDirectory: URL

    var queueItems: [TabletQueueItem] = []
    var results: [TabletResultItem] = []
    var savedResultIDs: Set<String> = []

    var mediaSegment: TabletMediaSegment = .queue
    var selection: TabletSelection? = nil
    var activeTab: TabletWorkspaceTab = .viewer
    var isMediaPaneCollapsed: Bool = false

    // Preserved viewer & enhance state across layout breakpoints
    var zoomScale: CGFloat = 1.0
    var panOffset: CGSize = .zero
    var dividerFraction: CGFloat = 0.5
    var isFullResolutionActive: Bool = false
    var revealResultID: String? = nil
    var enhanceScrollOffset: CGFloat = 0.0

    // Batch & status state
    var isCheckingDevice: Bool = false
    var isBatchRunning: Bool = false
    var isLoadingInputs: Bool = false
    var isDropTargeted: Bool = false
    var transientStatusMessage: String? = nil

    var batchTargetIDs: [String] = []
    var batchStartedAt: Date? = nil
    var batchCompletedInRun: Int = 0
    var batchTotalInRun: Int = 0
    var batchFailedInRun: Int = 0
    var batchLastRunSummary: TabletBatchCompletion? = nil

    var laneActivities: [ModelLane: TabletLaneActivity] = [:]
    var lastKnownChipByModel: [TabletModelID: String] = [:]
    var fullScale: Bool = false
    var isSaving: Bool = false
    var isSavingAll: Bool = false
    var saveAllDone: Int = 0
    var saveAllTotal: Int = 0
    var saveErrorMessage: String? = nil
    var saveAccessDenied: Bool = false

    @ObservationIgnored private var batchTask: Task<Void, Never>? = nil
    @ObservationIgnored private var transientTimerTask: Task<Void, Never>? = nil
    @ObservationIgnored private var pendingFollowUpIDs: [String] = []
    @ObservationIgnored private var itemChipsUsed: [String: [String]] = [:]

    /// Default plan for chunk 1: Best 4x.
    let activePlan = Plan(stages: [.best])

    init(service: TabletUpscaleService = makeTabletUpscaleService(), cachesDirectory: URL? = nil) {
        self.serviceBox = TabletServiceBox(service)
        #if SKIP
        let cachesBase = cachesDirectory ?? URL(fileURLWithPath: ProcessInfo.processInfo.androidContext.cacheDir.absolutePath, isDirectory: true)
        #else
        let cachesBase = cachesDirectory ?? FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
        #endif
        self.storageDirectory = cachesBase.appendingPathComponent("LocalSR", isDirectory: true)

        if let data = try? Data(contentsOf: storageDirectory.appendingPathComponent("tablet_results.json")),
           let decoded = try? JSONDecoder().decode([TabletResultItem].self, from: data) {
            results = decoded.filter {
                FileManager.default.fileExists(atPath: $0.outputURL.path)
                    && FileManager.default.fileExists(atPath: $0.previewURL.path)
            }
            persistResults()
        }
        let validIDs = Set(results.map(\.id))
        if let savedData = try? Data(contentsOf: storageDirectory.appendingPathComponent("tablet_saved.json")),
           let savedList = try? JSONDecoder().decode([String].self, from: savedData) {
            savedResultIDs = Set(savedList).intersection(validIDs)
            persistSavedIDs()
        }
        if let firstResult = results.first {
            selection = .result(firstResult.id)
        }
    }

    // MARK: - Computed Properties

    var waitingQueueItems: [TabletQueueItem] {
        queueItems.filter { $0.status == .waiting || $0.status == .failed }
    }

    var runnableQueueCount: Int {
        queueItems.filter { $0.status == .waiting }.count
    }

    var hasRunnableSelectedItem: Bool {
        guard case .queue(let id) = selection,
              let item = queueItems.first(where: { $0.id == id }) else {
            return false
        }
        return item.status == .waiting || item.status == .failed
    }

    var canAddSelectedToRunningBatch: Bool {
        guard isBatchRunning,
              case .queue(let id) = selection,
              let item = queueItems.first(where: { $0.id == id }) else {
            return false
        }
        return item.status == .waiting
            && !batchTargetIDs.contains(id)
            && !pendingFollowUpIDs.contains(id)
    }

    var selectedQueueItem: TabletQueueItem? {
        guard case .queue(let id) = selection else { return nil }
        return queueItems.first { $0.id == id }
    }

    var selectedResultItem: TabletResultItem? {
        switch selection {
        case .result(let id):
            return results.first { $0.id == id }
        case .queue(let qid):
            if let q = queueItems.first(where: { $0.id == qid }),
               let rid = q.resultID {
                return results.first { $0.id == rid }
            }
            return nil
        case nil:
            return nil
        }
    }

    var unsavedResultsCount: Int {
        results.filter { !savedResultIDs.contains($0.id) }.count
    }

    var wholeQueueProgress: Double {
        let ids = batchTargetIDs
        guard !ids.isEmpty else { return 0.0 }
        var sum = 0.0
        for id in ids {
            if let item = queueItems.first(where: { $0.id == id }) {
                switch item.status {
                case .waiting: sum += 0.0
                case .running: sum += min(max(0.0, item.progress), 1.0)
                case .done, .failed: sum += 1.0
                }
            } else {
                sum += 1.0
            }
        }
        return min(max(0.0, sum / Double(ids.count)), 1.0)
    }

    var nextQueuedItemName: String? {
        for id in batchTargetIDs {
            if let item = queueItems.first(where: { $0.id == id }), item.status == .waiting {
                return item.displayName
            }
        }
        return nil
    }

    func queuePosition(for id: String) -> Int? {
        var pos = 1
        for tid in batchTargetIDs {
            guard let item = queueItems.first(where: { $0.id == tid }) else { continue }
            if item.id == id {
                return item.status == .waiting ? pos : nil
            }
            if item.status == .waiting {
                pos += 1
            }
        }
        return nil
    }

    func isSaved(_ resultID: String) -> Bool {
        savedResultIDs.contains(resultID)
    }

    // MARK: - Importing

    func importBatch(_ batch: TabletImportBatch) {
        if batch.hasVideo {
            showTransientStatus("Videos aren't supported yet.")
        } else if batch.hasUnreadable && batch.inputs.isEmpty {
            showTransientStatus("LocalSR can't open this file.")
        } else if batch.hasUnreadable {
            showTransientStatus("LocalSR can't open this file.")
        }

        guard !batch.inputs.isEmpty else { return }
        batchLastRunSummary = nil

        var newlyAdded: [TabletQueueItem] = []
        let baseIndex = queueItems.count
        for (idx, input) in batch.inputs.enumerated() {
            let fallbackName = "Photo \(baseIndex + idx + 1).jpg"
            let rawName = (input.displayName?.isEmpty == false) ? input.displayName! : input.url.lastPathComponent
            let name = rawName.isEmpty ? fallbackName : rawName
            let item = TabletQueueItem(
                id: UUID().uuidString,
                inputURL: input.url,
                displayName: name,
                pixelWidth: nil,
                pixelHeight: nil,
                isPreparingPreview: true,
                status: .waiting,
                stageIndex: 0,
                stageCount: activePlan.stages.count,
                stageName: nil,
                chipName: nil,
                progress: 0.0,
                resultID: nil,
                outputWidth: nil,
                outputHeight: nil,
                errorMessage: nil
            )
            newlyAdded.append(item)
        }

        queueItems.append(contentsOf: newlyAdded)
        mediaSegment = .queue
        if selection == nil || !isCurrentSelectionValid {
            if let first = newlyAdded.first {
                selection = .queue(first.id)
            }
        }

        for item in newlyAdded {
            let itemID = item.id
            let url = item.inputURL
            Task { @MainActor in
                let dims = await PlatformSupport.imagePixelSize(url: url)
                if let idx = self.queueItems.firstIndex(where: { $0.id == itemID }) {
                    self.queueItems[idx].pixelWidth = dims?.width
                    self.queueItems[idx].pixelHeight = dims?.height
                    self.queueItems[idx].isPreparingPreview = false
                }
            }
        }
    }

    private var isCurrentSelectionValid: Bool {
        switch selection {
        case .queue(let id):
            return queueItems.contains { $0.id == id }
        case .result(let id):
            return results.contains { $0.id == id }
        case nil:
            return false
        }
    }

    func showTransientStatus(_ message: String) {
        transientStatusMessage = message
        transientTimerTask?.cancel()
        transientTimerTask = Task { @MainActor in
            try? await Task.sleep(nanoseconds: 4_000_000_000)
            if !Task.isCancelled {
                self.transientStatusMessage = nil
            }
        }
    }

    // MARK: - Queue & Result Mutations

    func removeQueueItem(id: String) {
        guard let idx = queueItems.firstIndex(where: { $0.id == id }) else { return }
        if queueItems[idx].status == .running { return }
        let removedURL = queueItems[idx].inputURL
        queueItems.remove(at: idx)
        pendingFollowUpIDs.removeAll { $0 == id }
        batchTargetIDs.removeAll { $0 == id }

        if selection == .queue(id) {
            if idx < queueItems.count {
                selection = .queue(queueItems[idx].id)
            } else if let last = queueItems.last {
                selection = .queue(last.id)
            } else if let firstRes = results.first {
                selection = .result(firstRes.id)
            } else {
                selection = nil
            }
        }
        deleteInputFileIfUnreferenced(removedURL)
    }

    func clearFinishedQueueItems() {
        let finished = queueItems.filter { $0.status == .done }
        queueItems.removeAll { $0.status == .done }
        for item in finished {
            if selection == .queue(item.id) {
                if let rid = item.resultID, results.contains(where: { $0.id == rid }) {
                    selection = .result(rid)
                } else if let first = queueItems.first {
                    selection = .queue(first.id)
                } else if let firstRes = results.first {
                    selection = .result(firstRes.id)
                } else {
                    selection = nil
                }
            }
        }
    }

    func deleteResult(id: String) {
        guard let idx = results.firstIndex(where: { $0.id == id }) else { return }
        let target = results[idx]
        results.remove(at: idx)
        savedResultIDs.remove(id)
        for qIdx in queueItems.indices where queueItems[qIdx].resultID == id {
            queueItems[qIdx].resultID = nil
            queueItems[qIdx].status = .waiting
            queueItems[qIdx].progress = 0.0
        }
        persistResults()
        persistSavedIDs()
        deleteResultFiles(target)

        if selection == .result(id) {
            if idx < results.count {
                selection = .result(results[idx].id)
            } else if let last = results.last {
                selection = .result(last.id)
            } else if let firstQ = queueItems.first {
                selection = .queue(firstQ.id)
            } else {
                selection = nil
            }
        }
    }

    func deleteAllResults() {
        let existing = results
        results = []
        savedResultIDs = []
        for qIdx in queueItems.indices where queueItems[qIdx].resultID != nil {
            queueItems[qIdx].resultID = nil
            if queueItems[qIdx].status == .done {
                queueItems[qIdx].status = .waiting
                queueItems[qIdx].progress = 0.0
            }
        }
        persistResults()
        persistSavedIDs()
        for item in existing {
            deleteResultFiles(item)
        }
        if case .result = selection {
            if let first = queueItems.first {
                selection = TabletSelection.queue(first.id)
            } else {
                selection = nil
            }
        }
    }

    // MARK: - Batch Execution

    func startAll(switchToViewer: Bool = false) {
        let targets = queueItems.filter { $0.status == .waiting || $0.status == .failed }
        guard !targets.isEmpty, !isBatchRunning else { return }
        if switchToViewer { activeTab = .viewer }
        runQueueBatch(items: targets)
    }

    func upscaleSelected(switchToViewer: Bool = false) {
        guard !isBatchRunning else { return }
        if let selected = selectedQueueItem, selected.status == .waiting || selected.status == .failed {
            if switchToViewer { activeTab = .viewer }
            runQueueBatch(items: [selected])
            return
        }
        if let firstWaiting = queueItems.first(where: { $0.status == .waiting || $0.status == .failed }) {
            selection = .queue(firstWaiting.id)
            if switchToViewer { activeTab = .viewer }
            runQueueBatch(items: [firstWaiting])
        }
    }

    func resetViewerTransform(divider: CGFloat = 0.5) {
        zoomScale = 1.0
        panOffset = .zero
        dividerFraction = divider
        isFullResolutionActive = false
    }

    func canEnhanceAgain(_ result: TabletResultItem) -> Bool {
        guard !isBatchRunning else { return false }
        if result.inputURL.isFileURL {
            return FileManager.default.fileExists(atPath: result.inputURL.path)
        }
        return true
    }

    func enhanceAgain(result: TabletResultItem) {
        guard !isBatchRunning else { return }
        if let idx = queueItems.firstIndex(where: { $0.resultID == result.id }) {
            queueItems[idx].resultID = nil
            queueItems[idx].status = .waiting
            queueItems[idx].progress = 0.0
            queueItems[idx].errorMessage = nil
            upscaleSingleQueueItem(id: queueItems[idx].id, switchToViewer: false)
            return
        }
        guard canEnhanceAgain(result) else {
            showTransientStatus("LocalSR can't open this file.")
            return
        }
        let item = TabletQueueItem(
            id: UUID().uuidString,
            inputURL: result.inputURL,
            displayName: result.displayName,
            pixelWidth: result.inputWidth,
            pixelHeight: result.inputHeight,
            isPreparingPreview: false,
            status: .waiting,
            stageIndex: 0,
            stageCount: activePlan.stages.count,
            stageName: nil,
            chipName: nil,
            progress: 0.0,
            resultID: nil,
            outputWidth: nil,
            outputHeight: nil,
            errorMessage: nil
        )
        queueItems.append(item)
        selection = .queue(item.id)
        runQueueBatch(items: [item])
    }

    func upscaleSingleQueueItem(id: String, switchToViewer: Bool = false) {
        guard !isBatchRunning,
              let item = queueItems.first(where: { $0.id == id }) else { return }
        selection = .queue(id)
        if switchToViewer { activeTab = .viewer }
        runQueueBatch(items: [item])
    }

    func addSelectedToRunningBatch() {
        guard canAddSelectedToRunningBatch,
              case .queue(let id) = selection else { return }
        pendingFollowUpIDs.append(id)
        batchTargetIDs.append(id)
        batchTotalInRun = batchTargetIDs.count
    }

    func retryFailed(switchToViewer: Bool = false) {
        let failed = queueItems.filter { $0.status == .failed }
        guard !failed.isEmpty, !isBatchRunning else { return }
        if switchToViewer { activeTab = .viewer }
        runQueueBatch(items: failed)
    }

    func cancelQueue() {
        guard isBatchRunning else { return }
        pendingFollowUpIDs.removeAll()
        serviceBox.cancel()
        batchTask?.cancel()
    }

    private func runQueueBatch(items: [TabletQueueItem]) {
        guard !items.isEmpty else { return }
        let plan = activePlan
        let ids = items.map(\.id)

        for id in ids {
            if let idx = queueItems.firstIndex(where: { $0.id == id }) {
                queueItems[idx].status = .waiting
                queueItems[idx].progress = 0.0
                queueItems[idx].stageIndex = 0
                queueItems[idx].stageCount = plan.stages.count
                queueItems[idx].stageName = nil
                queueItems[idx].chipName = nil
                queueItems[idx].resultID = nil
                queueItems[idx].errorMessage = nil
            }
            if selection == .queue(id) {
                resetViewerTransform()
            }
        }

        isBatchRunning = true
        isCheckingDevice = true
        batchLastRunSummary = nil
        batchStartedAt = Date()
        batchTargetIDs = ids
        batchCompletedInRun = 0
        batchFailedInRun = 0
        batchTotalInRun = ids.count
        pendingFollowUpIDs = []
        itemChipsUsed = [:]
        laneActivities = [:]

        #if !SKIP && os(iOS)
        let title = ids.count == 1 ? "Upscaling 1 Photo" : "Upscaling \(ids.count) Photos"
        BatchBackgroundTask.shared.begin(
            title: title,
            subtitle: "0 of \(ids.count)",
            totalUnits: Int64(ids.count),
            requestGPU: true,
            onExpire: { [weak self] in
                Task { @MainActor in
                    self?.cancelQueue()
                }
            }
        )
        #endif

        batchTask?.cancel()
        batchTask = Task { @MainActor in
            var remainingIDs = ids
            var wasCancelled = false

            while !remainingIDs.isEmpty && !Task.isCancelled {
                let batchInputs: [TabletInput] = remainingIDs.compactMap { id in
                    guard let item = self.queueItems.first(where: { $0.id == id }) else { return nil }
                    return TabletInput(id: item.id, url: item.inputURL, plan: plan)
                }
                remainingIDs = []
                if batchInputs.isEmpty { break }

                do {
                    _ = try await self.serviceBox.run(plan: plan, inputs: batchInputs, fullScale: self.fullScale) { event in
                        Task { @MainActor in
                            self.handleServiceEvent(event, plan: plan)
                        }
                    }
                } catch {
                    if Task.isCancelled || error is CancellationError {
                        wasCancelled = true
                        break
                    }
                    #if !SKIP
                    if let schedErr = error as? LaneScheduler.ScheduleError, case .cancelled = schedErr {
                        wasCancelled = true
                        break
                    }
                    #endif
                    self.handleBatchRunError(error, targetIDs: batchInputs.map(\.id))
                }

                if !wasCancelled && !self.pendingFollowUpIDs.isEmpty {
                    remainingIDs = self.pendingFollowUpIDs
                    self.pendingFollowUpIDs = []
                }
            }

            self.finalizeBatchRun(wasCancelled: wasCancelled)
        }
    }

    private func handleServiceEvent(_ event: TabletUpscaleEvent, plan: Plan) {
        switch event {
        case .lane(let activity):
            laneActivities[activity.lane] = activity
            if let pid = activity.photoID {
                isCheckingDevice = false
                if let idx = queueItems.firstIndex(where: { $0.id == pid }) {
                    let stageIdx = activity.stage ?? queueItems[idx].stageIndex
                    let model = activity.model ?? plan.stages[min(stageIdx, max(0, plan.stages.count - 1))]
                    let chip = (activity.unit ?? unitForLane(activity.lane)).platformDisplayName
                    lastKnownChipByModel[model] = chip
                    queueItems[idx].status = .running
                    queueItems[idx].stageIndex = stageIdx
                    queueItems[idx].stageName = stageDisplayName(for: model)
                    queueItems[idx].chipName = chip
                    var chips = itemChipsUsed[pid] ?? []
                    if !chips.contains(chip) {
                        chips.append(chip)
                        itemChipsUsed[pid] = chips
                    }
                }
            }

        case .progress(let photoID, let stage, let fraction):
            isCheckingDevice = false
            if let idx = queueItems.firstIndex(where: { $0.id == photoID }) {
                let combined = combinedProgress(for: queueItems[idx], plan: plan, stage: stage, fraction: fraction)
                queueItems[idx].status = .running
                queueItems[idx].stageIndex = stage
                if queueItems[idx].stageName == nil, stage < plan.stages.count {
                    queueItems[idx].stageName = stageDisplayName(for: plan.stages[stage])
                }
                queueItems[idx].progress = combined
            }

        case .prediction(let photoID, _, let model, let lane, _, _):
            isCheckingDevice = false
            let chip = unitForLane(lane).platformDisplayName
            lastKnownChipByModel[model] = chip
            var chips = itemChipsUsed[photoID] ?? []
            if !chips.contains(chip) {
                chips.append(chip)
                itemChipsUsed[photoID] = chips
            }

        case .completed(let result):
            isCheckingDevice = false
            let qIdx = queueItems.firstIndex(where: { $0.id == result.id })
            let displayName = qIdx.map { queueItems[$0].displayName } ?? result.inputURL.lastPathComponent
            let inW = qIdx.flatMap { queueItems[$0].pixelWidth }
            let inH = qIdx.flatMap { queueItems[$0].pixelHeight }
            let chips = itemChipsUsed[result.id] ?? []
            let chipsLabel = chips.isEmpty ? ComputeUnit.npu.platformDisplayName : chips.joined(separator: " + ")

            let record = TabletResultItem(
                id: UUID().uuidString,
                displayName: displayName,
                inputURL: result.inputURL,
                outputURL: result.outputURL,
                previewURL: result.previewURL,
                inputWidth: inW,
                inputHeight: inH,
                width: result.width,
                height: result.height,
                outputScale: result.outputScale,
                seconds: result.seconds,
                stages: result.plan.stages,
                chipsLabel: chipsLabel,
                createdAt: Date()
            )
            results.insert(record, at: 0)
            persistResults()

            if selection == .queue(result.id) {
                revealResultID = record.id
                resetViewerTransform(divider: 1.0)
            }

            if let qIdx {
                queueItems[qIdx].status = .done
                queueItems[qIdx].progress = 1.0
                queueItems[qIdx].resultID = record.id
                queueItems[qIdx].outputWidth = result.width
                queueItems[qIdx].outputHeight = result.height
            }

            batchCompletedInRun += 1
            #if !SKIP && os(iOS)
            BatchBackgroundTask.shared.update(
                completedUnits: Int64(batchCompletedInRun),
                subtitle: "\(batchCompletedInRun) of \(batchTotalInRun)"
            )
            #endif
        }
    }

    private func handleBatchRunError(_ error: Error, targetIDs: [String]) {
        isCheckingDevice = false
        let message = error.localizedDescription.isEmpty ? String(describing: error) : error.localizedDescription
        for id in targetIDs {
            if let idx = queueItems.firstIndex(where: { $0.id == id }),
               queueItems[idx].status == .running || queueItems[idx].status == .waiting {
                queueItems[idx].status = .failed
                queueItems[idx].errorMessage = message
                batchFailedInRun += 1
            }
        }
    }

    private func finalizeBatchRun(wasCancelled: Bool) {
        isBatchRunning = false
        isCheckingDevice = false
        laneActivities = [:]

        if wasCancelled {
            for idx in queueItems.indices where queueItems[idx].status == .running {
                queueItems[idx].status = .waiting
                queueItems[idx].progress = 0.0
                queueItems[idx].stageName = nil
                queueItems[idx].chipName = nil
            }
        }

        #if !SKIP && os(iOS)
        BatchBackgroundTask.shared.end(success: !wasCancelled && batchFailedInRun == 0)
        #endif

        if wasCancelled {
            batchLastRunSummary = nil
            return
        }

        let elapsed = batchStartedAt.map { Date().timeIntervalSince($0) } ?? 0.0
        if batchFailedInRun > 0 {
            batchLastRunSummary = .partialFailure(
                succeeded: batchCompletedInRun,
                total: batchTotalInRun,
                failed: batchFailedInRun
            )
            Haptics.warning()
        } else if batchCompletedInRun > 0 {
            batchLastRunSummary = .complete(count: batchCompletedInRun, seconds: elapsed)
            Haptics.success()
        }
    }

    // MARK: - Saving

    func saveResult(_ result: TabletResultItem) async {
        guard !isSaving, !isSavingAll, !savedResultIDs.contains(result.id) else { return }
        isSaving = true
        saveErrorMessage = nil
        saveAccessDenied = false
        do {
            let ext = result.outputURL.pathExtension.isEmpty ? "heic" : result.outputURL.pathExtension
            let base = URL(fileURLWithPath: result.displayName).deletingPathExtension().lastPathComponent
            let filename = base.isEmpty ? "LocalSR.\(ext)" : "\(base).\(ext)"
            try await TabletPlatform.saveOutputToLibrary(outputURL: result.outputURL, originalFilename: filename)
            savedResultIDs.insert(result.id)
            persistSavedIDs()
            Haptics.success()
        } catch {
            let msg = String(describing: error)
            saveAccessDenied = msg.contains("not granted") || msg.contains("notAuthorized")
            saveErrorMessage = saveAccessDenied ? "Allow LocalSR to add photos in Settings." : error.localizedDescription
            Haptics.error()
        }
        isSaving = false
    }

    func saveAllResults() async {
        guard !isSaving, !isSavingAll else { return }
        let targets = results.filter { !savedResultIDs.contains($0.id) }
        guard !targets.isEmpty else { return }

        isSavingAll = true
        saveAllDone = 0
        saveAllTotal = targets.count
        saveErrorMessage = nil
        saveAccessDenied = false

        for item in targets {
            do {
                let ext = item.outputURL.pathExtension.isEmpty ? "heic" : item.outputURL.pathExtension
                let base = URL(fileURLWithPath: item.displayName).deletingPathExtension().lastPathComponent
                let filename = base.isEmpty ? "LocalSR.\(ext)" : "\(base).\(ext)"
                try await TabletPlatform.saveOutputToLibrary(outputURL: item.outputURL, originalFilename: filename)
                savedResultIDs.insert(item.id)
                persistSavedIDs()
                saveAllDone += 1
            } catch {
                let msg = String(describing: error)
                saveAccessDenied = msg.contains("not granted") || msg.contains("notAuthorized")
                saveErrorMessage = saveAccessDenied ? "Allow LocalSR to add photos in Settings." : error.localizedDescription
                Haptics.error()
                isSavingAll = false
                return
            }
        }
        isSavingAll = false
        Haptics.success()
    }

    // MARK: - Formatting & Persistence Helpers

    private func combinedProgress(for item: TabletQueueItem, plan: Plan, stage: Int, fraction: Double) -> Double {
        let clamped = min(max(0.0, fraction), 1.0)
        let stages = plan.stages
        guard !stages.isEmpty else { return clamped }
        if let pw = item.pixelWidth, let ph = item.pixelHeight, pw > 0, ph > 0 {
            let stageSizes = plan.stageSizes(width: pw, height: ph, fullScale: fullScale)
            let upscale = stages.contains { ModelCatalog.model($0).scale == 4 }
            let target = SizePolicy.target(width: pw, height: ph)
            var inW = upscale ? target.width : pw
            var inH = upscale ? target.height : ph
            var counts: [Double] = []
            for (idx, modelID) in stages.enumerated() {
                let desc = ModelCatalog.model(modelID)
                let tiles = TilePlanner.plan(width: inW, height: inH, window: desc.window, halo: desc.halo, scale: desc.scale)
                counts.append(Double(max(1, tiles.count)))
                if idx < stageSizes.count {
                    inW = stageSizes[idx].width
                    inH = stageSizes[idx].height
                }
            }
            var total = 0.0
            for c in counts { total += c }
            if total > 0.0 {
                let clampedStage = min(max(0, stage), counts.count - 1)
                var before = 0.0
                for i in 0..<clampedStage {
                    before += counts[i]
                }
                let current = counts[clampedStage] * clamped
                return min(max(0.0, (before + current) / total), 1.0)
            }
        }
        let totalStages = max(1, stages.count)
        return min(max(0.0, (Double(stage) + clamped) / Double(totalStages)), 1.0)
    }

    private func unitForLane(_ lane: ModelLane) -> ComputeUnit {
        switch lane {
        case .neuralEngine: return .npu
        case .gpu: return .gpu
        case .cpu: return .cpu
        }
    }

    private func stageDisplayName(for model: TabletModelID) -> String {
        switch model {
        case .jpegFix: return "Fixing JPEG"
        case .noiseFix: return "Denoising"
        default: return "Upscaling"
        }
    }

    func estimatedRemainingSeconds() -> Double? {
        guard isBatchRunning, !isCheckingDevice, batchCompletedInRun > 0, let started = batchStartedAt else {
            return nil
        }
        let elapsed = Date().timeIntervalSince(started)
        return ETA.remaining(completed: batchCompletedInRun, total: max(batchTotalInRun, 1), elapsedSeconds: elapsed)
    }

    static func formatDurationCompact(_ seconds: Double) -> String {
        let total = max(1, Int(seconds.rounded()))
        if total < 60 {
            return "\(total) s"
        }
        let mins = total / 60
        let secs = total % 60
        if secs == 0 {
            return "\(mins) min"
        }
        return "\(mins) min \(secs) s"
    }

    private func persistResults() {
        try? FileManager.default.createDirectory(at: storageDirectory, withIntermediateDirectories: true)
        if let data = try? JSONEncoder().encode(results) {
            try? data.write(to: storageDirectory.appendingPathComponent("tablet_results.json"), options: Data.WritingOptions.atomic)
        }
    }

    private func persistSavedIDs() {
        try? FileManager.default.createDirectory(at: storageDirectory, withIntermediateDirectories: true)
        let sorted = Array(savedResultIDs).sorted()
        if let data = try? JSONEncoder().encode(sorted) {
            try? data.write(to: storageDirectory.appendingPathComponent("tablet_saved.json"), options: Data.WritingOptions.atomic)
        }
    }

    private func deleteResultFiles(_ result: TabletResultItem) {
        let fm = FileManager.default
        let outputDir = result.outputURL.deletingLastPathComponent()
        if outputDir.deletingLastPathComponent().standardizedFileURL.path == storageDirectory.standardizedFileURL.path {
            try? fm.removeItem(at: outputDir)
        }
        deleteInputFileIfUnreferenced(result.inputURL)
    }

    private func deleteInputFileIfUnreferenced(_ url: URL) {
        guard url.isFileURL else { return }
        let inputsDir = TabletPlatform.tabletInputsDirectory().standardizedFileURL.resolvingSymlinksInPath().path
        let targetPath = url.standardizedFileURL.resolvingSymlinksInPath().path
        guard targetPath.hasPrefix(inputsDir + "/") else { return }
        let referencedByQueue = queueItems.contains {
            $0.inputURL.standardizedFileURL.resolvingSymlinksInPath().path == targetPath
        }
        let referencedByResults = results.contains {
            $0.inputURL.standardizedFileURL.resolvingSymlinksInPath().path == targetPath
        }
        if !referencedByQueue && !referencedByResults {
            try? FileManager.default.removeItem(at: url)
        }
    }
}
