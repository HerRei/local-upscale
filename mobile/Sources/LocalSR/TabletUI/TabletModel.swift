import Foundation
import Observation
import SwiftUI
import LocalSRCore

private final class TabletServiceBox: @unchecked Sendable {
    var service: TabletUpscaleService

    init(_ service: TabletUpscaleService) {
        self.service = service
    }

    var supportsBackgroundGPU: Bool {
        service.supportsBackgroundGPU
    }

    var availableLanes: [ModelLane] {
        service.availableLanes
    }

    var runsDeviceCheck: Bool {
        service.runsDeviceCheck
    }

    func skip(photoID: String) {
        service.skip(photoID: photoID)
    }

    func updateForegroundNotification(title: String, detail: String?, fraction: Double) {
        service.updateForegroundNotification(title: title, detail: detail, fraction: fraction)
    }

    func setLaneOverride(_ override: TabletLaneOverride) {
        service.laneOverride = override
    }

    func cachedVerdict(for model: TabletModelID) -> TabletChipVerdict? {
        service.cachedVerdict(for: model)
    }

    func isModelBundled(_ model: TabletModelID) -> Bool {
        service.isModelBundled(model)
    }

    func hasModelFailedCheck(_ model: TabletModelID) -> Bool {
        service.hasModelFailedCheck(model)
    }

    func bundledModelByteSize(_ model: TabletModelID) -> Int64? {
        service.bundledModelByteSize(model)
    }

    func run(
        plan: Plan,
        inputs: [TabletInput],
        fullScale: Bool,
        targetScale: Int,
        outputFormat: TabletOutputFormat,
        events: @escaping @Sendable (TabletUpscaleEvent) -> Void
    ) async throws -> [TabletUpscaleResult] {
        try await service.run(
            plan: plan,
            inputs: inputs,
            fullScale: fullScale,
            targetScale: targetScale,
            outputFormat: outputFormat,
            events: events
        )
    }

    func cancel() {
        service.cancel()
    }
}

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
    /// The task of the photo's last run, for "Couldn't upscale" vs "Couldn't restore".
    var lastTask: TabletTaskKind

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
    let fullScaleFallback: Bool
    let seconds: Double
    let stages: [TabletModelID]
    let chipsLabel: String
    let createdAt: Date

    init(
        id: String,
        displayName: String,
        inputURL: URL,
        outputURL: URL,
        previewURL: URL,
        inputWidth: Int?,
        inputHeight: Int?,
        width: Int,
        height: Int,
        outputScale: Int,
        fullScaleFallback: Bool = false,
        seconds: Double,
        stages: [TabletModelID],
        chipsLabel: String,
        createdAt: Date
    ) {
        self.id = id
        self.displayName = displayName
        self.inputURL = inputURL
        self.outputURL = outputURL
        self.previewURL = previewURL
        self.inputWidth = inputWidth
        self.inputHeight = inputHeight
        self.width = width
        self.height = height
        self.outputScale = outputScale
        self.fullScaleFallback = fullScaleFallback
        self.seconds = seconds
        self.stages = stages
        self.chipsLabel = chipsLabel
        self.createdAt = createdAt
    }

    private enum CodingKeys: String, CodingKey {
        case id, displayName, inputURL, outputURL, previewURL, inputWidth, inputHeight
        case width, height, outputScale, fullScaleFallback, seconds, stages, chipsLabel, createdAt
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        displayName = try c.decode(String.self, forKey: .displayName)
        inputURL = try c.decode(URL.self, forKey: .inputURL)
        outputURL = try c.decode(URL.self, forKey: .outputURL)
        previewURL = try c.decode(URL.self, forKey: .previewURL)
        inputWidth = try c.decodeIfPresent(Int.self, forKey: .inputWidth)
        inputHeight = try c.decodeIfPresent(Int.self, forKey: .inputHeight)
        width = try c.decode(Int.self, forKey: .width)
        height = try c.decode(Int.self, forKey: .height)
        outputScale = try c.decode(Int.self, forKey: .outputScale)
        fullScaleFallback = try c.decodeIfPresent(Bool.self, forKey: .fullScaleFallback) ?? false
        seconds = try c.decode(Double.self, forKey: .seconds)
        stages = try c.decode([TabletModelID].self, forKey: .stages)
        chipsLabel = try c.decode(String.self, forKey: .chipsLabel)
        createdAt = try c.decode(Date.self, forKey: .createdAt)
    }
}

private struct TabletPersistedQueueItem: Codable, Sendable {
    let id: String
    let inputURL: URL
    let displayName: String
    let pixelWidth: Int?
    let pixelHeight: Int?
}

private struct TabletPersistedBatchState: Codable, Sendable {
    let items: [TabletPersistedQueueItem]
    let totalInRun: Int
    let completedInRun: Int
    let pausedBySystem: Bool
    let setup: TabletEnhanceSetup
}

@MainActor
@Observable
final class TabletModel {
    /// One model per process, for the same reason as AppModel.shared (Activity recreation on Android).
    /// Created on first use (see AppModel.shared).
    static var shared: TabletModel {
        if let existing = sharedInstance { return existing }
        let created = TabletModel()
        sharedInstance = created
        return created
    }
    private static var sharedInstance: TabletModel? = nil

    private let serviceBox: TabletServiceBox
    private let storageDirectory: URL
    private let defaults: UserDefaults

    var queueItems: [TabletQueueItem] = []
    var results: [TabletResultItem] = []
    var savedResultIDs: Set<String> = []

    var mediaSegment: TabletMediaSegment = .queue
    var selection: TabletSelection? = nil
    var activeTab: TabletWorkspaceTab = .viewer
    var isMediaPaneCollapsed: Bool = false
    /// The pane the user last worked in, for the bare keys (arrows, Delete, M) in three-pane mode.
    var keyboardPane: TabletWorkspaceTab = .media
    /// A recipe name is being typed: the bare keys are not shortcuts then.
    var isTextEntryActive: Bool = false

    // Preserved viewer & enhance state across layout breakpoints
    var zoomScale: CGFloat = 1.0
    var panOffset: CGSize = .zero
    var dividerFraction: CGFloat = 0.5
    var isFullResolutionActive: Bool = false
    var revealResultID: String? = nil
    var enhanceScrollOffset: CGFloat = 0.0
    var scrollToChipsTrigger: Int = 0

    // Enhance setup, recipes & verdicts
    var setup: TabletEnhanceSetup = TabletEnhanceSetup()
    var recipes: [TabletRecipe] = []
    var laneChoice: TabletLaneChoice = .both
    var verdictsByModel: [TabletModelID: TabletChipVerdict] = [:]
    var bundledModels: Set<TabletModelID> = []
    var failedModels: Set<TabletModelID> = []
    var modelByteSizes: [TabletModelID: Int64] = [:]
    var measuredSecondsByModel: [TabletModelID: Double] = [:]

    // Output & auto-save settings
    var fullScale: Bool = false
    var autoSave: Bool = false
    var saveDestination: TabletSaveDestination = .photos
    var saveFolderURL: URL? = nil
    var saveFolderDisplayName: String? = nil

    // Batch & status state
    var isCheckingDevice: Bool = false
    var isBatchRunning: Bool = false
    var isLoadingInputs: Bool = false
    var isDropTargeted: Bool = false
    var transientStatusMessage: String? = nil
    var storageErrorMessage: String? = nil

    var isPausedBySystem: Bool = false
    /// The paused batch was found at launch without a system pause: the app was quit or crashed mid-batch.
    var wasBatchInterrupted: Bool = false
    var pausedCompletedCount: Int = 0
    var pausedTotalCount: Int = 0
    /// True only while a batch runs whose background task actually got the GPU from iPadOS.
    var isBackgroundGPUGranted: Bool = false

    var viewerActualZoom: CGFloat = 1.0
    var viewerMinZoom: CGFloat = 1.0
    var viewerMaxZoom: CGFloat = 8.0
    @ObservationIgnored private var savedDividerBeforeCompare: CGFloat? = nil
    @ObservationIgnored private var pauseRequestedBySystem: Bool = false

    var batchTargetIDs: [String] = []
    var batchStartedAt: Date? = nil
    var batchCompletedInRun: Int = 0
    var batchTotalInRun: Int = 0
    var batchFailedInRun: Int = 0
    var batchLastRunSummary: TabletBatchCompletion? = nil
    var lastBatchLaneSummary: String? = nil

    var laneActivities: [ModelLane: TabletLaneActivity] = [:]
    var lastKnownChipByModel: [TabletModelID: String] = [:]
    var isThermalSlowed: Bool = false
    var isAppInBackground: Bool = false

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
    @ObservationIgnored private var batchStageRecords: [String: [Int: (model: TabletModelID, lane: ModelLane)]] = [:]
    @ObservationIgnored private var batchItemCompletedIDs: Set<String> = []
    /// Photos removed while their batch ran: the service skips them and late events for them are dropped.
    @ObservationIgnored private var removedDuringBatchIDs: Set<String> = []
    /// Inputs of removed photos; deleted when the batch ends so the service never reads a missing file.
    @ObservationIgnored private var deferredInputDeletions: [URL] = []
    /// Results waiting for "Save Automatically", saved one after another.
    @ObservationIgnored private var autoSaveQueue: [TabletResultItem] = []
    @ObservationIgnored private var isDrainingAutoSave: Bool = false
    @ObservationIgnored private var isImportingShareInbox: Bool = false
    @ObservationIgnored private var pendingShareInboxRecheck: Bool = false

    private static let recipesDefaultsKey = "tabletRecipesJSON"
    private static let setupDefaultsKey = "tabletEnhanceSetupJSON"
    private static let laneChoiceDefaultsKey = "tabletLaneChoice"
    private static let measuredSecondsDefaultsKey = "tabletMeasuredSecondsJSON"
    private static let autoSaveDefaultsKey = "tabletAutoSave"
    private static let saveDestinationDefaultsKey = "tabletSaveDestination"
    private static let saveFolderPathDefaultsKey = "tabletSaveFolderPath"
    private static let saveFolderBookmarkDefaultsKey = "tabletSaveFolderBookmark"

    init(
        service: TabletUpscaleService = makeTabletUpscaleService(),
        cachesDirectory: URL? = nil,
        defaults: UserDefaults = .standard
    ) {
        self.serviceBox = TabletServiceBox(service)
        self.defaults = defaults
        #if SKIP
        let cachesBase = cachesDirectory ?? URL(fileURLWithPath: ProcessInfo.processInfo.androidContext.cacheDir.absolutePath, isDirectory: true)
        #else
        let cachesBase = cachesDirectory ?? FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
        #endif
        self.storageDirectory = cachesBase.appendingPathComponent("LocalSR", isDirectory: true)

        if TabletPlatform.isAndroid {
            self.setup.outputFormat = .jpeg
        }

        loadPersistedState()
        refreshCatalogState()

        #if !SKIP
        updateThermalState()
        #endif

        if let data = try? Data(contentsOf: storageDirectory.appendingPathComponent("tablet_results.json")),
           let decoded = try? JSONDecoder().decode([TabletResultItem].self, from: data) {
            // Keep the record so the viewer can explain when its file is gone.
            results = decoded
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
        restorePausedBatchIfNeeded()
    }

    // MARK: - Computed Properties

    var visibleLanes: [ModelLane] {
        if TabletPlatform.isAndroid {
            return serviceBox.availableLanes
        }
        return [.neuralEngine, .gpu]
    }

    var isNeuralEngineAvailable: Bool {
        if TabletPlatform.isAndroid {
            return visibleLanes.contains(ModelLane.neuralEngine)
        }
        return TabletPlatform.hasNeuralEngine
    }

    var activePlan: Plan {
        setup.buildPlan()
    }

    var matchingRecipe: TabletRecipe? {
        recipes.first { setup.matches(recipe: $0) }
    }

    var headingSummary: String {
        setup.headingSummary(matchingRecipe: matchingRecipe)
    }

    /// This build and device can keep the GPU in the background (iOS: entitlement + M3 or newer).
    var supportsBackgroundGPU: Bool {
        serviceBox.supportsBackgroundGPU
    }

    /// The GPU lane stops while the app is in the background during this batch.
    var isGPUPausedInBackground: Bool {
        guard isBatchRunning && isAppInBackground && !TabletPlatform.isAndroid else { return false }
        return !isBackgroundGPUGranted
    }

    /// False on Android tablets: the engine takes the first chip that loads, without a check.
    var runsDeviceCheck: Bool {
        serviceBox.runsDeviceCheck
    }

    var checkingModelDisplayName: String {
        if setup.task == .restore {
            return "the models"
        }
        return ModelCatalog.model(setup.upscaleModel).displayName
    }

    var laneValidationError: (title: String, detail: String, allFailed: Bool)? {
        TabletEnhanceSetup.laneValidationFailureMessage(
            plan: activePlan,
            verdicts: verdictsByModel,
            failedModels: failedModels,
            laneChoice: laneChoice,
            deviceNoun: TabletPlatform.deviceNoun,
            bundledModels: bundledModels
        )
    }

    var waitingQueueItems: [TabletQueueItem] {
        queueItems.filter { $0.status == .waiting || $0.status == .failed }
    }

    var runnableQueueCount: Int {
        queueItems.filter { $0.status == .waiting }.count
    }

    var failedQueueCount: Int {
        queueItems.filter { $0.status == .failed }.count
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
                case .waiting: sum += batchItemCompletedIDs.contains(id) ? 1.0 : 0.0
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
        for id in batchTargetIDs where !batchItemCompletedIDs.contains(id) {
            if let item = queueItems.first(where: { $0.id == id }), item.status == .waiting {
                return item.displayName
            }
        }
        return nil
    }

    func queuePosition(for id: String) -> Int? {
        // A photo whose result was deleted mid-run is waiting again but won't run in this batch.
        var pos = 1
        for tid in batchTargetIDs {
            guard let item = queueItems.first(where: { $0.id == tid }) else { continue }
            let willRun = item.status == .waiting && !batchItemCompletedIDs.contains(tid)
            if item.id == id {
                return willRun ? pos : nil
            }
            if willRun {
                pos += 1
            }
        }
        return nil
    }

    func isSaved(_ resultID: String) -> Bool {
        savedResultIDs.contains(resultID)
    }

    // MARK: - Model Catalog & Verdicts

    func refreshCatalogState() {
        var bundled: Set<TabletModelID> = []
        var failed: Set<TabletModelID> = []
        var verdicts: [TabletModelID: TabletChipVerdict] = verdictsByModel
        var sizes: [TabletModelID: Int64] = [:]

        for modelID in TabletModelID.allCases {
            if serviceBox.isModelBundled(modelID) {
                bundled.insert(modelID)
            }
            if serviceBox.hasModelFailedCheck(modelID) {
                failed.insert(modelID)
            }
            if let v = serviceBox.cachedVerdict(for: modelID) {
                verdicts[modelID] = v
            }
            if let bytes = serviceBox.bundledModelByteSize(modelID) {
                sizes[modelID] = bytes
            } else {
                sizes[modelID] = ModelCatalog.model(modelID).fallbackBytes
            }
        }

        bundledModels = bundled
        failedModels = failed
        verdictsByModel = verdicts
        modelByteSizes = sizes
    }

    func isModelAvailable(_ id: TabletModelID) -> Bool {
        bundledModels.contains(id) && !failedModels.contains(id)
    }

    func availablePresets(for content: TabletContentKind) -> [TabletQualityPreset] {
        TabletQualityPreset.presets(for: content).filter { preset in
            guard let id = preset.modelID else { return false }
            return isModelAvailable(id)
        }
    }

    func availableLibraryModels(in group: TabletLibraryGroup) -> [ModelDescriptor] {
        ModelCatalog.models(in: group).filter { isModelAvailable($0.id) }
    }

    func chipLabel(for modelID: TabletModelID) -> String {
        TabletEnhanceSetup.chipLabel(
            for: modelID,
            verdict: verdictsByModel[modelID],
            laneChoice: laneChoice,
            isAndroid: TabletPlatform.isAndroid
        )
    }

    func measuredSeconds(for modelID: TabletModelID) -> Double? {
        measuredSecondsByModel[modelID]
    }

    func estimatedSecondsPerPhotoForActivePlan() -> Double? {
        let stages = activePlan.stages
        guard !stages.isEmpty else { return nil }
        var total = 0.0
        for stage in stages {
            guard let s = measuredSecondsByModel[stage] else { return nil }
            total += s
        }
        return total
    }

    // MARK: - Enhance Setup Actions

    func setTask(_ task: TabletTaskKind) {
        keyboardPane = .enhance
        guard !isBatchRunning else { return }
        setup.task = task
        if task == .restore && !setup.fixNoise && !setup.fixJpeg {
            setup.fixNoise = true
        }
        persistSetup()
    }

    func setContent(_ content: TabletContentKind) {
        keyboardPane = .enhance
        guard !isBatchRunning else { return }
        setup.content = content
        let presets = availablePresets(for: content)
        if !presets.contains(where: { $0.modelID == setup.upscaleModel }) {
            if let preferred = presets.first(where: { $0 == .best }) ?? presets.first,
               let modelID = preferred.modelID {
                setup.upscaleModel = modelID
                setup.isManualModelChoice = false
            }
        }
        persistSetup()
    }

    func selectPreset(_ preset: TabletQualityPreset) {
        keyboardPane = .enhance
        guard !isBatchRunning, let modelID = preset.modelID, isModelAvailable(modelID) else { return }
        setup.task = .upscale
        setup.content = preset.contentKind
        setup.upscaleModel = modelID
        setup.isManualModelChoice = false
        persistSetup()
    }

    func selectModelFromLibrary(_ modelID: TabletModelID) {
        guard !isBatchRunning, isModelAvailable(modelID) else { return }
        if modelID == .jpegFix {
            setup.fixJpeg = true
        } else if modelID == .noiseFix {
            setup.fixNoise = true
        } else {
            setup.task = .upscale
            if modelID == .anime {
                setup.content = .illustration
            } else {
                setup.content = .photo
            }
            setup.upscaleModel = modelID
            setup.isManualModelChoice = (modelID != .best)
        }
        persistSetup()
    }

    func resetToBestModel() {
        guard !isBatchRunning else { return }
        setup.task = .upscale
        setup.content = .photo
        setup.upscaleModel = .best
        setup.isManualModelChoice = false
        persistSetup()
    }

    func toggleFixNoise() {
        keyboardPane = .enhance
        guard !isBatchRunning else { return }
        if setup.task == .restore && setup.fixNoise && !setup.fixJpeg {
            return
        }
        setup.fixNoise.toggle()
        persistSetup()
    }

    func toggleFixJpeg() {
        keyboardPane = .enhance
        guard !isBatchRunning else { return }
        if setup.task == .restore && setup.fixJpeg && !setup.fixNoise {
            return
        }
        setup.fixJpeg.toggle()
        persistSetup()
    }

    func setOutputSize(_ size: TabletOutputSizeChoice) {
        keyboardPane = .enhance
        guard !isBatchRunning else { return }
        setup.outputSize = size
        persistSetup()
    }

    func setOutputFormat(_ format: TabletOutputFormat) {
        keyboardPane = .enhance
        guard !isBatchRunning else { return }
        setup.outputFormat = TabletPlatform.isAndroid && format == .heic ? .jpeg : format
        persistSetup()
    }

    func updateLaneChoice(_ choice: TabletLaneChoice) {
        keyboardPane = .enhance
        guard !isBatchRunning else { return }
        laneChoice = choice
        applyLaneChoiceToService()
        defaults.set(choice.rawValue, forKey: Self.laneChoiceDefaultsKey)
    }

    func updateAutoSave(_ enabled: Bool) {
        guard !isBatchRunning else { return }
        autoSave = enabled
        defaults.set(enabled, forKey: Self.autoSaveDefaultsKey)
    }

    func updateSaveDestination(_ dest: TabletSaveDestination) {
        guard !isBatchRunning else { return }
        saveDestination = dest
        defaults.set(dest.rawValue, forKey: Self.saveDestinationDefaultsKey)
    }

    func setSaveFolder(_ url: URL) {
        #if !SKIP
        let scoped = url.startAccessingSecurityScopedResource()
        defer {
            if scoped { url.stopAccessingSecurityScopedResource() }
        }
        if let bookmark = try? url.bookmarkData(options: [], includingResourceValuesForKeys: nil, relativeTo: nil) {
            defaults.set(bookmark, forKey: Self.saveFolderBookmarkDefaultsKey)
        }
        #endif
        saveFolderURL = url
        saveFolderDisplayName = url.lastPathComponent.isEmpty ? "Folder" : url.lastPathComponent
        defaults.set(url.path, forKey: Self.saveFolderPathDefaultsKey)
        updateSaveDestination(.folder)
    }

    // MARK: - Recipes

    func saveRecipe(name rawName: String) {
        let trimmed = rawName.trimmingCharacters(in: .whitespacesAndNewlines)
        let name = trimmed.isEmpty ? "Recipe \(recipes.count + 1)" : trimmed
        let recipe = setup.makeRecipe(name: name)
        recipes.append(recipe)
        persistRecipes()
    }

    func applyRecipe(_ recipe: TabletRecipe) {
        keyboardPane = .enhance
        guard !isBatchRunning else { return }
        setup = setup.applying(recipe: recipe)
        if TabletPlatform.isAndroid && setup.outputFormat == .heic {
            setup.outputFormat = .jpeg
        }
        persistSetup()
    }

    func renameRecipe(id: String, name rawName: String) {
        let trimmed = rawName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty,
              let idx = recipes.firstIndex(where: { $0.id == id }) else { return }
        recipes[idx].name = trimmed
        persistRecipes()
    }

    func deleteRecipe(id: String) {
        recipes.removeAll { $0.id == id }
        persistRecipes()
    }

    // MARK: - Importing

    /// Adds the photos to the queue and returns their queue IDs.
    @discardableResult
    func importBatch(_ batch: TabletImportBatch) -> [String] {
        if batch.hasVideo {
            showTransientStatus("Videos aren't supported yet.")
        } else if batch.hasUnreadable && batch.inputs.isEmpty {
            showTransientStatus("LocalSR can't open this file.")
        } else if batch.hasUnreadable {
            showTransientStatus("LocalSR can't open this file.")
        }

        guard !batch.inputs.isEmpty else { return [] }
        batchLastRunSummary = nil
        if isPausedBySystem {
            clearPausedBatchState()
        }

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
                errorMessage: nil,
                lastTask: setup.task
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
        return newlyAdded.map(\.id)
    }

    // MARK: - Share Menu

    /// Imports photos shared to LocalSR: on iPad the share extension's App Group inbox, on Android the
    /// copies the share intent made. With "Start Right Away" they start at once, or join a running batch.
    func importSharedInbox() async {
        if isImportingShareInbox {
            pendingShareInboxRecheck = true
            return
        }
        isImportingShareInbox = true
        repeat {
            pendingShareInboxRecheck = false
            let imported = await Self.drainShareInbox()
            if !imported.isEmpty {
                let ids = importBatch(TabletImportBatch(inputs: imported, hasVideo: false, hasUnreadable: false))
                if shareStartsImmediately() {
                    startSharedPhotos(ids: ids)
                }
            }
        } while pendingShareInboxRecheck
        isImportingShareInbox = false
    }

    private func shareStartsImmediately() -> Bool {
        if defaults.object(forKey: "shareStartsImmediately") != nil {
            return defaults.bool(forKey: "shareStartsImmediately")
        }
        return true
    }

    private func startSharedPhotos(ids: [String]) {
        guard !ids.isEmpty else { return }
        if isBatchRunning {
            var added = 0
            for id in ids where !batchTargetIDs.contains(id) && !pendingFollowUpIDs.contains(id) {
                pendingFollowUpIDs.append(id)
                batchTargetIDs.append(id)
                added += 1
            }
            batchTotalInRun += added
            return
        }
        guard laneValidationError == nil else { return }
        if let first = ids.first {
            selection = .queue(first)
        }
        runQueueBatch(items: queueItems.filter { ids.contains($0.id) })
    }

    private static func drainShareInbox() async -> [PickedPhotoInput] {
        #if SKIP
        let rawItems = await AndroidShareInbox.awaitAndDrainPendingInputs()
        var mapped: [PickedPhotoInput] = []
        for item in rawItems {
            guard let url = URL(string: item.urlString) else { continue }
            // Move the copy into the tablet inputs folder, so removing the photo also deletes it.
            if url.isFileURL, let moved = TabletPlatform.copyFileToInputs(url, preferredName: item.displayName) {
                try? FileManager.default.removeItem(at: url)
                mapped.append(moved)
            } else {
                mapped.append(PickedPhotoInput(url: url, displayName: item.displayName))
            }
        }
        return mapped
        #else
        guard let inbox = ShareInboxImporter.defaultInboxDirectory() else { return [] }
        let destination = TabletPlatform.tabletInputsDirectory()
        return await Task.detached(priority: .userInitiated) {
            ShareInboxImporter.importAll(from: inbox, to: destination)
        }.value
        #endif
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
        let removedStatus = queueItems[idx].status
        // The running service still holds this photo: tell it to skip the photo, and keep the file
        // until the batch ends in case a worker is opening it right now.
        let inRunningBatch = isBatchRunning && (batchTargetIDs.contains(id) || pendingFollowUpIDs.contains(id))
        queueItems.remove(at: idx)
        if inRunningBatch {
            serviceBox.skip(photoID: id)
            removedDuringBatchIDs.insert(id)
            if removedStatus == .waiting && !batchItemCompletedIDs.contains(id) {
                batchTotalInRun = max(batchCompletedInRun + batchFailedInRun, batchTotalInRun - 1)
            }
        }
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
        if inRunningBatch {
            deferredInputDeletions.append(removedURL)
        } else {
            deleteInputFileIfUnreferenced(removedURL)
        }
        if isBatchRunning {
            persistBatchState(pausedBySystem: pauseRequestedBySystem)
        }
        if isPausedBySystem && waitingQueueItems.isEmpty {
            clearPausedBatchState()
        }
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
        guard !targets.isEmpty, !isBatchRunning, laneValidationError == nil else { return }
        if switchToViewer { activeTab = .viewer }
        runQueueBatch(items: targets)
    }

    func resumePausedQueue(switchToViewer: Bool = false) {
        guard !isBatchRunning, laneValidationError == nil else { return }
        let waiting = queueItems.filter { $0.status == .waiting || $0.status == .failed }
        guard !waiting.isEmpty else {
            clearPausedBatchState()
            return
        }
        let carryCompleted = pausedCompletedCount
        let carryTotal = max(pausedTotalCount, carryCompleted + waiting.count)
        if switchToViewer { activeTab = .viewer }
        runQueueBatch(items: waiting, carryCompleted: carryCompleted, carryTotal: carryTotal)
    }

    func upscaleSelected(switchToViewer: Bool = false) {
        guard !isBatchRunning, laneValidationError == nil else { return }
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
        guard !isBatchRunning, laneValidationError == nil else { return }
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
            errorMessage: nil,
            lastTask: setup.task
        )
        queueItems.append(item)
        selection = .queue(item.id)
        runQueueBatch(items: [item])
    }

    func upscaleSingleQueueItem(id: String, switchToViewer: Bool = false) {
        guard !isBatchRunning, laneValidationError == nil,
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
        guard !failed.isEmpty, !isBatchRunning, laneValidationError == nil else { return }
        if switchToViewer { activeTab = .viewer }
        runQueueBatch(items: failed)
    }

    func cancelQueue() {
        guard isBatchRunning else { return }
        pauseRequestedBySystem = false
        pendingFollowUpIDs.removeAll()
        serviceBox.cancel()
        batchTask?.cancel()
    }

    func pauseBySystem() {
        guard isBatchRunning else { return }
        pauseRequestedBySystem = true
        pausedCompletedCount = batchCompletedInRun
        pausedTotalCount = max(batchTotalInRun, batchCompletedInRun)
        isPausedBySystem = true
        persistBatchState(pausedBySystem: true)
        pendingFollowUpIDs.removeAll()
        serviceBox.cancel()
        batchTask?.cancel()
    }

    private func runQueueBatch(items: [TabletQueueItem], carryCompleted: Int = 0, carryTotal: Int? = nil) {
        guard !items.isEmpty else { return }
        if let freeBytes = TabletPlatform.availableStorageBytes(), freeBytes < TabletPlatform.minimumRequiredStorageBytes {
            storageErrorMessage = "Free up 120 MB and try again."
            return
        }
        let plan = activePlan
        let targetScale = setup.task == .restore ? 1 : setup.outputSize.rawValue
        let outputFormat = setup.outputFormat
        let ids = items.map(\.id)

        applyLaneChoiceToService()

        let task = setup.task
        for id in ids {
            if let idx = queueItems.firstIndex(where: { $0.id == id }) {
                queueItems[idx].lastTask = task
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

        isPausedBySystem = false
        wasBatchInterrupted = false
        pauseRequestedBySystem = false
        pausedCompletedCount = 0
        pausedTotalCount = 0
        removedDuringBatchIDs = []
        isBackgroundGPUGranted = false
        isBatchRunning = true
        let allChecked = plan.stages.allSatisfy { verdictsByModel[$0] != nil }
        isCheckingDevice = !allChecked
        batchLastRunSummary = nil
        lastBatchLaneSummary = nil
        batchStartedAt = Date()
        batchTargetIDs = ids
        batchCompletedInRun = carryCompleted
        batchFailedInRun = 0
        batchTotalInRun = carryTotal ?? ids.count
        pendingFollowUpIDs = []
        itemChipsUsed = [:]
        batchStageRecords = [:]
        batchItemCompletedIDs = []
        laneActivities = [:]
        persistBatchState(pausedBySystem: false)

        #if !SKIP && os(iOS)
        let verb = setup.task == .restore ? "Restoring" : "Upscaling"
        let totalForTitle = batchTotalInRun
        let title = totalForTitle == 1 ? "\(verb) 1 Photo" : "\(verb) \(totalForTitle) Photos"
        BatchBackgroundTask.shared.begin(
            title: title,
            subtitle: "\(batchCompletedInRun) of \(totalForTitle)",
            totalUnits: Int64(totalForTitle),
            requestGPU: laneChoice != .neuralEngineOnly,
            onExpire: { [weak self] in
                Task { @MainActor in
                    self?.pauseBySystem()
                }
            }
        )
        isBackgroundGPUGranted = BatchBackgroundTask.shared.hasGrantedGPU
        #elseif SKIP
        updateAndroidForegroundNotification()
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
                    _ = try await self.serviceBox.run(
                        plan: plan,
                        inputs: batchInputs,
                        fullScale: self.fullScale,
                        targetScale: targetScale,
                        outputFormat: outputFormat
                    ) { event in
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

            self.finalizeBatchRun(wasCancelled: wasCancelled, plan: plan)
        }
    }

    private func handleServiceEvent(_ event: TabletUpscaleEvent, plan: Plan) {
        switch event {
        case .verdict(let model, let verdict):
            verdictsByModel[model] = verdict
            failedModels.remove(model)

        case .verdictFailed(let model):
            failedModels.insert(model)
            isCheckingDevice = false

        case .stageTiming(let photoID, let stage, let model, let lane, let seconds):
            let chip = unitForLane(lane).platformDisplayName
            lastKnownChipByModel[model] = chip
            var chips = itemChipsUsed[photoID] ?? []
            if !chips.contains(chip) {
                chips.append(chip)
                itemChipsUsed[photoID] = chips
            }
            var rec = batchStageRecords[photoID] ?? [:]
            if rec[stage] == nil {
                rec[stage] = (model, lane)
                batchStageRecords[photoID] = rec
            }
            if seconds.isFinite && seconds > 0.0 {
                if let prev = measuredSecondsByModel[model] {
                    measuredSecondsByModel[model] = prev * 0.5 + seconds * 0.5
                } else {
                    measuredSecondsByModel[model] = seconds
                }
                persistMeasuredSeconds()
            }

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
                    var rec = batchStageRecords[pid] ?? [:]
                    rec[stageIdx] = (model, activity.lane)
                    batchStageRecords[pid] = rec
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
            #if SKIP
            updateAndroidForegroundNotification()
            #endif

        case .prediction(let photoID, let stage, let model, let lane, _, _):
            isCheckingDevice = false
            let chip = unitForLane(lane).platformDisplayName
            lastKnownChipByModel[model] = chip
            var chips = itemChipsUsed[photoID] ?? []
            if !chips.contains(chip) {
                chips.append(chip)
                itemChipsUsed[photoID] = chips
            }
            var rec = batchStageRecords[photoID] ?? [:]
            rec[stage] = (model, lane)
            batchStageRecords[photoID] = rec

        case .failed(let failure):
            handlePhotoFailure(failure)

        case .completed(let result):
            isCheckingDevice = false
            if removedDuringBatchIDs.contains(result.id) {
                // Removed while a worker had already started on it: drop the result.
                let outputDir = result.outputURL.deletingLastPathComponent()
                if outputDir.deletingLastPathComponent().standardizedFileURL.path == storageDirectory.standardizedFileURL.path {
                    try? FileManager.default.removeItem(at: outputDir)
                }
                return
            }
            batchItemCompletedIDs.insert(result.id)
            let qIdx = queueItems.firstIndex(where: { $0.id == result.id })
            let displayName = qIdx.map { queueItems[$0].displayName } ?? result.inputURL.lastPathComponent
            let inW = qIdx.flatMap { queueItems[$0].pixelWidth }
            let inH = qIdx.flatMap { queueItems[$0].pixelHeight }
            let chips = itemChipsUsed[result.id] ?? []
            let chipsLabel = chips.isEmpty ? ComputeUnit.npu.platformDisplayName : chips.joined(separator: " + ")

            if result.plan.stages.count == 1, let onlyModel = result.plan.stages.first, result.seconds.isFinite, result.seconds > 0.0 {
                if measuredSecondsByModel[onlyModel] == nil {
                    measuredSecondsByModel[onlyModel] = result.seconds
                    persistMeasuredSeconds()
                }
            }

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
                fullScaleFallback: result.fullScaleFallback,
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
            if isPausedBySystem || pauseRequestedBySystem {
                pausedCompletedCount = batchCompletedInRun
                persistBatchState(pausedBySystem: true)
            } else {
                persistBatchState(pausedBySystem: false)
            }
            #if !SKIP && os(iOS)
            var bgSubtitle = "\(batchCompletedInRun) of \(batchTotalInRun)"
            if let eta = estimatedRemainingSeconds() {
                bgSubtitle += " \u{00B7} about \(Self.formatDurationCompact(eta)) left"
            }
            BatchBackgroundTask.shared.update(
                completedUnits: Int64(batchCompletedInRun),
                subtitle: bgSubtitle
            )
            #elseif SKIP
            updateAndroidForegroundNotification()
            #endif

            if autoSave {
                enqueueAutoSave(record)
            }
        }
    }

    /// One photo failed: only it is marked failed, with its own reason; the batch goes on.
    private func handlePhotoFailure(_ failure: TabletPhotoFailure) {
        isCheckingDevice = false
        if removedDuringBatchIDs.contains(failure.photoID) { return }
        if failure.isOutOfSpace {
            storageErrorMessage = "Free up 120 MB and try again."
        }
        guard let idx = queueItems.firstIndex(where: { $0.id == failure.photoID }) else { return }
        queueItems[idx].status = .failed
        queueItems[idx].errorMessage = failure.message
        queueItems[idx].progress = 0.0
        queueItems[idx].stageName = nil
        queueItems[idx].chipName = nil
        batchFailedInRun += 1
        persistBatchState(pausedBySystem: isPausedBySystem || pauseRequestedBySystem)
        #if SKIP
        updateAndroidForegroundNotification()
        #endif
    }

    /// A batch-level error (device check, missing model, chip setting): it applies to every photo of the batch.
    private func handleBatchRunError(_ error: Error, targetIDs: [String]) {
        isCheckingDevice = false
        if TabletPlatform.isOutOfSpaceError(error) {
            storageErrorMessage = "Free up 120 MB and try again."
        }
        let message: String
        #if !SKIP
        if let schedErr = error as? LaneScheduler.ScheduleError, case .unavailable = schedErr {
            message = laneValidationError?.detail ?? "No chip available for this setup."
        } else {
            message = TabletErrorText.message(for: error)
        }
        #else
        message = TabletErrorText.message(for: error)
        #endif
        for id in targetIDs {
            if let idx = queueItems.firstIndex(where: { $0.id == id }),
               queueItems[idx].status == .running || queueItems[idx].status == .waiting {
                queueItems[idx].status = .failed
                queueItems[idx].errorMessage = message
                batchFailedInRun += 1
            }
        }
    }

    private func finalizeBatchRun(wasCancelled: Bool, plan: Plan) {
        isBatchRunning = false
        isCheckingDevice = false
        isBackgroundGPUGranted = false
        laneActivities = [:]
        refreshCatalogState()
        let deletions = deferredInputDeletions
        deferredInputDeletions = []
        for url in deletions {
            deleteInputFileIfUnreferenced(url)
        }

        if wasCancelled {
            for idx in queueItems.indices where queueItems[idx].status == .running {
                queueItems[idx].status = .waiting
                queueItems[idx].progress = 0.0
                queueItems[idx].stageName = nil
                queueItems[idx].chipName = nil
            }
        }

        let paused = pauseRequestedBySystem
        pauseRequestedBySystem = false

        #if !SKIP && os(iOS)
        BatchBackgroundTask.shared.end(success: !wasCancelled && !paused && batchFailedInRun == 0)
        #endif

        if paused {
            isPausedBySystem = true
            pausedCompletedCount = batchCompletedInRun
            pausedTotalCount = max(batchTotalInRun, batchCompletedInRun)
            persistBatchState(pausedBySystem: true)
            batchLastRunSummary = nil
            lastBatchLaneSummary = nil
            return
        }

        clearPersistedBatchFile()

        if wasCancelled {
            batchLastRunSummary = nil
            lastBatchLaneSummary = nil
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
            lastBatchLaneSummary = buildLastBatchLaneSummary(
                completedCount: batchCompletedInRun,
                elapsed: elapsed,
                plan: plan
            )
            Haptics.success()
        }
    }

    private func buildLastBatchLaneSummary(completedCount: Int, elapsed: Double, plan: Plan) -> String {
        let photoWord = completedCount == 1 ? "photo" : "photos"
        let duration = Self.formatDurationCompact(elapsed)

        var neCount = 0
        var gpuCount = 0
        var cpuCount = 0
        var neFixed = 0
        var gpuFixed = 0
        var neUpscaled = 0
        var gpuUpscaled = 0

        for pid in batchItemCompletedIDs {
            guard let stageMap = batchStageRecords[pid] else { continue }
            if plan.stages.count > 1 {
                for (_, entry) in stageMap {
                    let isFix = entry.model == .jpegFix || entry.model == .noiseFix
                    if isFix {
                        if entry.lane == .neuralEngine { neFixed += 1 }
                        else if entry.lane == .gpu { gpuFixed += 1 }
                    } else {
                        if entry.lane == .neuralEngine { neUpscaled += 1 }
                        else if entry.lane == .gpu { gpuUpscaled += 1 }
                    }
                }
            } else if let firstStage = stageMap[0] {
                switch firstStage.lane {
                case .neuralEngine: neCount += 1
                case .gpu: gpuCount += 1
                case .cpu: cpuCount += 1
                }
            }
        }

        let laneText: String
        if plan.stages.count > 1 {
            var parts: [String] = []
            if neFixed > 0 { parts.append("\(TabletPlatform.npuName) fixed \(neFixed)") }
            if gpuFixed > 0 { parts.append("GPU fixed \(gpuFixed)") }
            if neUpscaled > 0 { parts.append("\(TabletPlatform.npuName) upscaled \(neUpscaled)") }
            if gpuUpscaled > 0 { parts.append("GPU upscaled \(gpuUpscaled)") }
            laneText = parts.isEmpty ? "\(TabletPlatform.npuName) \(neCount), GPU \(gpuCount)" : parts.joined(separator: ", ")
        } else if !TabletPlatform.isAndroid && laneChoice == .both {
            laneText = "Neural Engine \(neCount), GPU \(gpuCount)"
        } else {
            var parts: [String] = []
            if neCount > 0 { parts.append("\(TabletPlatform.npuName) \(neCount)") }
            if gpuCount > 0 { parts.append("GPU \(gpuCount)") }
            if cpuCount > 0 { parts.append("CPU \(cpuCount)") }
            if parts.isEmpty {
                parts.append(laneChoice == .gpuOnly ? "GPU \(completedCount)" : "\(TabletPlatform.npuName) \(completedCount)")
            }
            laneText = parts.joined(separator: ", ")
        }
        return "Last batch: \(completedCount) \(photoWord) in \(duration). \(laneText)."
    }

    // MARK: - Saving

    /// Two photos can finish within one save; each is queued and saved in turn so none is skipped.
    private func enqueueAutoSave(_ result: TabletResultItem) {
        autoSaveQueue.append(result)
        guard !isDrainingAutoSave else { return }
        isDrainingAutoSave = true
        Task { @MainActor in
            while !self.autoSaveQueue.isEmpty {
                // A manual Save or Save All runs first; this one waits for it instead of being dropped.
                while self.isSaving || self.isSavingAll {
                    try? await Task.sleep(nanoseconds: 150_000_000)
                }
                let next = self.autoSaveQueue.removeFirst()
                if self.results.contains(where: { $0.id == next.id }) && !self.savedResultIDs.contains(next.id) {
                    await self.autoSaveResult(next)
                }
            }
            self.isDrainingAutoSave = false
        }
    }

    private func autoSaveResult(_ result: TabletResultItem) async {
        if saveDestination == .folder, let folderURL = saveFolderURL {
            #if !SKIP
            let scoped = folderURL.startAccessingSecurityScopedResource()
            defer {
                if scoped { folderURL.stopAccessingSecurityScopedResource() }
            }
            let ext = result.outputURL.pathExtension.isEmpty ? "heic" : result.outputURL.pathExtension
            let base = URL(fileURLWithPath: result.displayName).deletingPathExtension().lastPathComponent
            let cleanBase = base.isEmpty ? "LocalSR" : base
            var dest = folderURL.appendingPathComponent("\(cleanBase)-enhanced.\(ext)")
            var suffix = 2
            while FileManager.default.fileExists(atPath: dest.path) {
                dest = folderURL.appendingPathComponent("\(cleanBase)-enhanced-\(suffix).\(ext)")
                suffix += 1
            }
            do {
                try FileManager.default.copyItem(at: result.outputURL, to: dest)
                savedResultIDs.insert(result.id)
                persistSavedIDs()
            } catch {
                saveErrorMessage = error.localizedDescription
            }
            #else
            await saveResult(result)
            #endif
        } else {
            await saveResult(result)
        }
    }

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
            if TabletPlatform.isOutOfSpaceError(error) {
                storageErrorMessage = "Free up 120 MB and try again."
            } else {
                let msg = String(describing: error)
                saveAccessDenied = msg.contains("not granted") || msg.contains("notAuthorized")
                saveErrorMessage = saveAccessDenied ? "Allow LocalSR to add photos in Settings." : error.localizedDescription
            }
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
                if TabletPlatform.isOutOfSpaceError(error) {
                    storageErrorMessage = "Free up 120 MB and try again."
                } else {
                    let msg = String(describing: error)
                    saveAccessDenied = msg.contains("not granted") || msg.contains("notAuthorized")
                    saveErrorMessage = saveAccessDenied ? "Allow LocalSR to add photos in Settings." : error.localizedDescription
                }
                Haptics.error()
                isSavingAll = false
                return
            }
        }
        isSavingAll = false
        Haptics.success()
    }

    // MARK: - Formatting & Persistence Helpers

    private func applyLaneChoiceToService() {
        switch laneChoice {
        case .both: serviceBox.setLaneOverride(.both)
        case .neuralEngineOnly: serviceBox.setLaneOverride(.neuralEngineOnly)
        case .gpuOnly: serviceBox.setLaneOverride(.gpuOnly)
        }
    }

    private func loadPersistedState() {
        if let json = defaults.string(forKey: Self.recipesDefaultsKey) {
            recipes = TabletRecipeStore.decode(json)
        }
        if let setupData = defaults.data(forKey: Self.setupDefaultsKey),
           let decoded = try? JSONDecoder().decode(TabletEnhanceSetup.self, from: setupData) {
            setup = decoded
            if TabletPlatform.isAndroid && setup.outputFormat == .heic {
                setup.outputFormat = .jpeg
            }
        }
        if let rawLane = defaults.string(forKey: Self.laneChoiceDefaultsKey),
           let decodedLane = TabletLaneChoice(rawValue: rawLane) {
            laneChoice = decodedLane
        }
        applyLaneChoiceToService()

        if let secData = defaults.data(forKey: Self.measuredSecondsDefaultsKey),
           let decodedSec = try? JSONDecoder().decode([String: Double].self, from: secData) {
            var mapped: [TabletModelID: Double] = [:]
            for (k, v) in decodedSec {
                if let id = TabletModelID(rawValue: k) {
                    mapped[id] = v
                }
            }
            measuredSecondsByModel = mapped
        }

        fullScale = defaults.bool(forKey: "fullScale")
        autoSave = defaults.bool(forKey: Self.autoSaveDefaultsKey)
        if let rawDest = defaults.string(forKey: Self.saveDestinationDefaultsKey),
           let dest = TabletSaveDestination(rawValue: rawDest) {
            saveDestination = dest
        }

        #if !SKIP
        if let bookmark = defaults.data(forKey: Self.saveFolderBookmarkDefaultsKey) {
            var isStale = false
            if let url = try? URL(resolvingBookmarkData: bookmark, options: [], relativeTo: nil, bookmarkDataIsStale: &isStale) {
                saveFolderURL = url
                saveFolderDisplayName = url.lastPathComponent.isEmpty ? "Folder" : url.lastPathComponent
            }
        }
        #endif
        if saveFolderURL == nil, let path = defaults.string(forKey: Self.saveFolderPathDefaultsKey), !path.isEmpty {
            let url = URL(fileURLWithPath: path, isDirectory: true)
            saveFolderURL = url
            saveFolderDisplayName = url.lastPathComponent.isEmpty ? "Folder" : url.lastPathComponent
        }
    }

    private func persistRecipes() {
        defaults.set(TabletRecipeStore.encode(recipes), forKey: Self.recipesDefaultsKey)
    }

    private func persistSetup() {
        if let data = try? JSONEncoder().encode(setup) {
            defaults.set(data, forKey: Self.setupDefaultsKey)
        }
    }

    private func persistMeasuredSeconds() {
        var dict: [String: Double] = [:]
        for (k, v) in measuredSecondsByModel {
            dict[k.rawValue] = v
        }
        if let data = try? JSONEncoder().encode(dict) {
            defaults.set(data, forKey: Self.measuredSecondsDefaultsKey)
        }
    }

    #if !SKIP
    func updateThermalState() {
        let state = ProcessInfo.processInfo.thermalState
        isThermalSlowed = (state == .serious || state == .critical)
    }
    #endif

    private func combinedProgress(for item: TabletQueueItem, plan: Plan, stage: Int, fraction: Double) -> Double {
        let clamped = min(max(0.0, fraction), 1.0)
        let stages = plan.stages
        guard !stages.isEmpty else { return clamped }
        if let pw = item.pixelWidth, let ph = item.pixelHeight, pw > 0, ph > 0 {
            let stageSizes = plan.stageSizes(
                width: pw,
                height: ph,
                fullScale: fullScale,
                requestedScale: setup.task == .restore ? 1 : setup.outputSize.rawValue
            )
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

    static func formatBytesCompact(_ bytes: Int64) -> String {
        if bytes < 1_000_000 {
            let kb = max(1, Int((Double(bytes) / 1000.0).rounded()))
            return "\(kb) KB"
        }
        let mb = Double(bytes) / 1_000_000.0
        if mb < 10.0 {
            let tenths = Int((mb * 10.0).rounded())
            return "\(tenths / 10).\(tenths % 10) MB"
        }
        return "\(Int(mb.rounded())) MB"
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

    // MARK: - Batch Recovery & Foreground Notification

    private var batchStateFileURL: URL {
        storageDirectory.appendingPathComponent("tablet_batch_state.json")
    }

    private func persistBatchState(pausedBySystem: Bool) {
        let remaining = queueItems.filter { item in
            batchTargetIDs.contains(item.id) && (item.status == .waiting || item.status == .running)
        }.map { item in
            TabletPersistedQueueItem(
                id: item.id,
                inputURL: item.inputURL,
                displayName: item.displayName,
                pixelWidth: item.pixelWidth,
                pixelHeight: item.pixelHeight
            )
        }
        guard !remaining.isEmpty else {
            clearPersistedBatchFile()
            return
        }
        let state = TabletPersistedBatchState(
            items: remaining,
            totalInRun: max(batchTotalInRun, batchCompletedInRun + remaining.count),
            completedInRun: batchCompletedInRun,
            pausedBySystem: pausedBySystem,
            setup: setup
        )
        try? FileManager.default.createDirectory(at: storageDirectory, withIntermediateDirectories: true)
        if let data = try? JSONEncoder().encode(state) {
            try? data.write(to: batchStateFileURL, options: Data.WritingOptions.atomic)
        }
    }

    private func clearPersistedBatchFile() {
        try? FileManager.default.removeItem(at: batchStateFileURL)
    }

    func clearPausedBatchState() {
        isPausedBySystem = false
        wasBatchInterrupted = false
        pausedCompletedCount = 0
        pausedTotalCount = 0
        clearPersistedBatchFile()
    }

    private func restorePausedBatchIfNeeded() {
        guard let data = try? Data(contentsOf: batchStateFileURL),
              let state = try? JSONDecoder().decode(TabletPersistedBatchState.self, from: data) else {
            clearPersistedBatchFile()
            return
        }
        var restored: [TabletQueueItem] = []
        for entry in state.items {
            if entry.inputURL.isFileURL && !FileManager.default.fileExists(atPath: entry.inputURL.path) {
                continue
            }
            restored.append(TabletQueueItem(
                id: entry.id,
                inputURL: entry.inputURL,
                displayName: entry.displayName,
                pixelWidth: entry.pixelWidth,
                pixelHeight: entry.pixelHeight,
                isPreparingPreview: false,
                status: .waiting,
                stageIndex: 0,
                stageCount: state.setup.buildPlan().stages.count,
                stageName: nil,
                chipName: nil,
                progress: 0.0,
                resultID: nil,
                outputWidth: nil,
                outputHeight: nil,
                errorMessage: nil,
                lastTask: state.setup.task
            ))
        }
        guard !restored.isEmpty else {
            clearPersistedBatchFile()
            return
        }
        setup = state.setup
        if TabletPlatform.isAndroid && setup.outputFormat == .heic {
            setup.outputFormat = .jpeg
        }
        queueItems.append(contentsOf: restored)
        isPausedBySystem = true
        // Without a system pause on record the app was quit or crashed during the batch.
        wasBatchInterrupted = !state.pausedBySystem
        pausedCompletedCount = state.completedInRun
        pausedTotalCount = max(state.totalInRun, state.completedInRun + restored.count)
        if selection == nil, let first = restored.first {
            selection = .queue(first.id)
        }
    }

    private func updateAndroidForegroundNotification() {
        guard TabletPlatform.isAndroid else { return }
        let total = max(1, batchTotalInRun)
        let current = min(batchCompletedInRun + 1, total)
        let pct = Int((wholeQueueProgress * 100.0).rounded())
        let verb = setup.task == .restore ? "Restoring" : "Upscaling"
        let title = isCheckingDevice ? "Getting Ready\u{2026}" : "\(verb) \(current) of \(total) \u{00B7} \(pct) %"
        var parts: [String] = []
        if let eta = estimatedRemainingSeconds() {
            parts.append("About \(Self.formatDurationCompact(eta)) left")
        }
        if let nextName = nextQueuedItemName {
            parts.append("next \(nextName)")
        }
        let detail = parts.isEmpty ? nil : parts.joined(separator: " \u{00B7} ")
        serviceBox.updateForegroundNotification(title: title, detail: detail, fraction: isCheckingDevice ? 0.0 : wholeQueueProgress)
    }

    // MARK: - Keyboard Navigation & Viewer Controls

    func selectPreviousPhoto() {
        if mediaSegment == .queue && !queueItems.isEmpty {
            if case .queue(let id) = selection, let idx = queueItems.firstIndex(where: { $0.id == id }) {
                let prev = max(0, idx - 1)
                selection = .queue(queueItems[prev].id)
            } else if let first = queueItems.first {
                selection = .queue(first.id)
            }
        } else if !results.isEmpty {
            if case .result(let id) = selection, let idx = results.firstIndex(where: { $0.id == id }) {
                let prev = max(0, idx - 1)
                selection = .result(results[prev].id)
            } else if let first = results.first {
                selection = .result(first.id)
            }
        }
    }

    func selectNextPhoto() {
        if mediaSegment == .queue && !queueItems.isEmpty {
            if case .queue(let id) = selection, let idx = queueItems.firstIndex(where: { $0.id == id }) {
                let next = min(queueItems.count - 1, idx + 1)
                selection = .queue(queueItems[next].id)
            } else if let first = queueItems.first {
                selection = .queue(first.id)
            }
        } else if !results.isEmpty {
            if case .result(let id) = selection, let idx = results.firstIndex(where: { $0.id == id }) {
                let next = min(results.count - 1, idx + 1)
                selection = .result(results[next].id)
            } else if let first = results.first {
                selection = .result(first.id)
            }
        }
    }

    /// The pane the bare keys act on: the visible one in tab mode, the last one used in three-pane mode.
    func keyboardTargetPane(isThreePane: Bool) -> TabletWorkspaceTab {
        isThreePane ? keyboardPane : activeTab
    }

    var canRemoveSelectedQueueItem: Bool {
        guard case .queue(let id) = selection,
              let item = queueItems.first(where: { $0.id == id }) else { return false }
        return item.status != .running
    }

    func removeSelectedQueueItem() {
        guard canRemoveSelectedQueueItem, case .queue(let id) = selection else { return }
        removeQueueItem(id: id)
    }

    func zoomIn() {
        setZoomScale(zoomScale * 1.25)
    }

    func zoomOut() {
        setZoomScale(zoomScale / 1.25)
    }

    func zoomFit() {
        setZoomScale(1.0)
    }

    func zoomActual() {
        setZoomScale(viewerActualZoom)
    }

    private func setZoomScale(_ rawTarget: CGFloat) {
        let clamped = min(max(rawTarget, viewerMinZoom), viewerMaxZoom)
        let snapped: CGFloat
        if (viewerMinZoom >= 1.0 && clamped <= 1.01) || abs(clamped - 1.0) <= 0.015 {
            snapped = 1.0
        } else {
            snapped = clamped
        }
        zoomScale = snapped
        if snapped <= 1.01 {
            panOffset = .zero
            isFullResolutionActive = false
        } else if snapped > 1.2 {
            isFullResolutionActive = true
        }
    }

    func nudgeDivider(delta: CGFloat) {
        guard selectedResultItem != nil else { return }
        savedDividerBeforeCompare = nil
        dividerFraction = min(max(dividerFraction + delta, 0.0), 1.0)
    }

    func moveDivider(to value: CGFloat) {
        guard selectedResultItem != nil else { return }
        savedDividerBeforeCompare = nil
        dividerFraction = min(max(value, 0.0), 1.0)
    }

    func toggleCompareOriginal() {
        guard selectedResultItem != nil else { return }
        if dividerFraction >= 0.99, let saved = savedDividerBeforeCompare {
            dividerFraction = saved
            savedDividerBeforeCompare = nil
        } else if dividerFraction >= 0.99 {
            dividerFraction = 0.5
            savedDividerBeforeCompare = nil
        } else {
            savedDividerBeforeCompare = dividerFraction
            dividerFraction = 1.0
        }
    }
}
