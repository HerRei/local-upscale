import Foundation
import Observation
import LocalSRCore

public enum BatchItemStatus: Equatable, Sendable {
    case waiting
    case upscaling
    case done
    case failed
    case cancelled

    public var label: String {
        switch self {
        case .waiting: return "Waiting"
        case .upscaling: return "Upscaling"
        case .done: return "Done"
        case .failed: return "Failed"
        case .cancelled: return "Cancelled"
        }
    }
}

public struct BatchItem: Identifiable, Sendable {
    public let id: String
    public let inputURL: URL
    public let displayName: String
    public var status: BatchItemStatus
    public var progress: Double
    public var errorMessage: String?
    public var resultID: String?

    public init(
        id: String = UUID().uuidString,
        inputURL: URL,
        displayName: String,
        status: BatchItemStatus = .waiting,
        progress: Double = 0.0,
        errorMessage: String? = nil,
        resultID: String? = nil
    ) {
        self.id = id
        self.inputURL = inputURL
        self.displayName = displayName
        self.status = status
        self.progress = progress
        self.errorMessage = errorMessage
        self.resultID = resultID
    }
}

public struct BatchFailureItem: Identifiable, Equatable, Sendable {
    public let id: String
    public let inputURL: URL
    public let displayName: String
    public let errorMessage: String

    public init(id: String = UUID().uuidString, inputURL: URL, displayName: String, errorMessage: String) {
        self.id = id
        self.inputURL = inputURL
        self.displayName = displayName
        self.errorMessage = errorMessage
    }
}

public struct WaitingPhotoItem: Identifiable, Codable, Equatable, Sendable {
    public let id: String
    public let url: URL
    public let displayName: String

    public init(id: String = UUID().uuidString, url: URL, displayName: String) {
        self.id = id
        self.url = url
        self.displayName = displayName
    }
}

public enum BatchSummaryKind: Equatable, Sendable {
    case succeeded(count: Int, resultIDs: [String])
    case partialFailure(succeededCount: Int, totalCount: Int, resultIDs: [String], failures: [BatchFailureItem])
    case allFailed(totalCount: Int, failures: [BatchFailureItem], message: String, noWorkingChip: Bool)
    case interrupted
}

public struct BatchSummary: Identifiable, Equatable, Sendable {
    public let id: String
    public var kind: BatchSummaryKind

    public init(id: String = UUID().uuidString, kind: BatchSummaryKind) {
        self.id = id
        self.kind = kind
    }
}

private final class ServiceBox: @unchecked Sendable {
    private let service: UpscaleService

    init(_ service: UpscaleService) {
        self.service = service
    }

    func prepare(model: ModelKey) async throws -> DeviceVerdict {
        try await service.prepare(model: model)
    }

    func upscale(input: URL, model: ModelKey, fullScale: Bool, progress: @escaping @Sendable (Double) -> Void) async throws -> UpscaleResult {
        try await service.upscale(input: input, model: model, fullScale: fullScale, progress: progress)
    }

    func canUseFullScale(forInput url: URL) -> Bool {
        service.canUseFullScale(forInput: url)
    }

    func saveToLibrary(_ result: UpscaleResult) async throws {
        try await service.saveToLibrary(result)
    }

    #if SKIP
    func beginBatch() async throws {
        if let androidService = service as? AndroidUpscaleService { try await androidService.beginBatch() }
    }

    func endBatch() {
        if let androidService = service as? AndroidUpscaleService { androidService.endBatch() }
    }

    func setBatch(done: Int, total: Int) {
        if let androidService = service as? AndroidUpscaleService {
            androidService.setBatch(done: done, total: total)
        }
    }

    func releaseModel() async {
        if let androidService = service as? AndroidUpscaleService {
            await androidService.releaseModel()
        }
    }
    #endif
}

@MainActor
@Observable
public class AppModel {
    /// The app's one model. Views use this instead of creating their own: on Android the Activity (and its views) can be
    /// recreated while a batch keeps running in the foreground service, and a second model would orphan that batch,
    /// report it as interrupted and overwrite its results.json.
    /// Created on first use, not when the class loads: Skip turns a `static let` into an eager Kotlin field, which
    /// would build the model (and its engine) as soon as anything touches AppModel, also in unit tests.
    public static var shared: AppModel {
        if let existing = sharedInstance { return existing }
        let created = AppModel()
        sharedInstance = created
        return created
    }
    private static var sharedInstance: AppModel? = nil

    private let serviceBox: ServiceBox

    public var results: [UpscaleResult] = []
    public var waitingItems: [WaitingPhotoItem] = []
    public var lastBatchInterrupted = false
    public var batchSummary: BatchSummary? = nil
    private let storageDirectory: URL
    private let inputsDirectory: URL
    private let defaults: UserDefaults
    private let customInboxDirectory: URL?
    public var verdicts: [ModelKey: DeviceVerdict] = [:]
    public var savedResultIDs: Set<String> = []
    public var isSaving: Bool = false
    public var isSavingAll: Bool = false
    public var saveAllDoneCount: Int = 0
    public var saveAllTotalCount: Int = 0
    public var saveErrorMessage: String? = nil
    public var saveAccessDenied: Bool = false

    public var isBatchRunning: Bool = false
    public var preparingModel: ModelKey? = nil
    public var preparingElapsedLong: Bool = false
    public var batchModel: ModelKey = .best
    public var batchFullScale: Bool = false
    public var isFullScaleAvailable: Bool = true
    public var batchItems: [BatchItem] = []
    public var batchCompletedCount: Int = 0
    public var batchETAText: String? = nil

    public var revealResultID: String? = nil
    public var pendingAutoOpenResultID: String? = nil
    public var stageTransitionResultID: String? = nil
    public var isLoadingPickedPhotos: Bool = false

    public var isRerunningResultID: String? = nil
    public var rerunProgress: Double = 0.0
    public var rerunErrorMessage: String? = nil

    @ObservationIgnored private var batchTask: Task<Void, Never>? = nil
    @ObservationIgnored private var preparingTimerTask: Task<Void, Never>? = nil
    @ObservationIgnored private var didRequestNotifications: Bool = false
    @ObservationIgnored private var isImportingInbox: Bool = false
    @ObservationIgnored private var pendingInboxRecheck: Bool = false
    /// Set when iOS ends the background task (not the user): the unfinished photos go back to the waiting list.
    @ObservationIgnored private var batchStoppedBySystem: Bool = false

    public init(
        service: UpscaleService = makeUpscaleService(),
        cachesDirectory: URL? = nil,
        defaults: UserDefaults = .standard,
        inboxDirectory: URL? = nil
    ) {
        self.serviceBox = ServiceBox(service)
        self.defaults = defaults
        self.customInboxDirectory = inboxDirectory
        let cachesBase = cachesDirectory ?? URL.cachesDirectory
        self.storageDirectory = cachesBase.appendingPathComponent("LocalSR", isDirectory: true)
        self.inputsDirectory = cachesBase.appendingPathComponent("LocalSRInputs", isDirectory: true)
        let marker = storageDirectory.appendingPathComponent("batch-in-progress")
        lastBatchInterrupted = FileManager.default.fileExists(atPath: marker.path)
        if lastBatchInterrupted {
            try? FileManager.default.removeItem(at: marker)
            batchSummary = BatchSummary(id: "interrupted", kind: .interrupted)
        }
        if let data = try? Data(contentsOf: storageDirectory.appendingPathComponent("results.json")),
           let stored = try? JSONDecoder().decode([UpscaleResult].self, from: data) {
            let moved = stored.map { r in
                UpscaleResult(
                    id: r.id,
                    inputURL: Self.rebased(r.inputURL, cachesBase: cachesBase),
                    outputURL: Self.rebased(r.outputURL, cachesBase: cachesBase),
                    previewURL: Self.rebased(r.previewURL, cachesBase: cachesBase),
                    width: r.width, height: r.height, model: r.model, unit: r.unit, seconds: r.seconds,
                    outputScale: r.outputScale, fullScaleFallback: r.fullScaleFallback
                )
            }
            results = moved.filter { FileManager.default.fileExists(atPath: $0.outputURL.path) && FileManager.default.fileExists(atPath: $0.previewURL.path) }
            persistResults()
        }
        if let waitingData = try? Data(contentsOf: storageDirectory.appendingPathComponent("waiting.json")),
           let storedWaiting = try? JSONDecoder().decode([WaitingPhotoItem].self, from: waitingData) {
            waitingItems = storedWaiting
                .map { WaitingPhotoItem(id: $0.id, url: Self.rebased($0.url, cachesBase: cachesBase), displayName: $0.displayName) }
                .filter { FileManager.default.fileExists(atPath: $0.url.path) }
            persistWaitingItems()
        }
        let validIDs = Set(results.map { $0.id })
        if let savedData = try? Data(contentsOf: storageDirectory.appendingPathComponent("saved.json")),
           let savedList = try? JSONDecoder().decode([String].self, from: savedData) {
            savedResultIDs = Set(savedList).intersection(validIDs)
            persistSavedResultIDs()
        }
        sweepOrphanedFiles()
        _ = refreshFullScaleAvailability()
    }

    /// Removes result folders and input copies that no result or waiting photo refers to: what cancelled, failed or
    /// killed runs leave behind. Runs once per process at startup, before any batch of this process can start, and
    /// only touches entries older than an hour (a batch of another process could still be writing newer ones).
    private func sweepOrphanedFiles() {
        // Tablets keep their own results in the same Caches/LocalSR folder, which this model knows nothing about.
        guard !TabletPlatform.isTablet else { return }
        let fm = FileManager.default
        let keepIDs = Set(results.map { $0.id })
        var keepInputs = Set<String>()
        for r in results where r.inputURL.isFileURL { keepInputs.insert(r.inputURL.lastPathComponent) }
        for w in waitingItems where w.url.isFileURL { keepInputs.insert(w.url.lastPathComponent) }
        let cutoff = Date().addingTimeInterval(-3600.0)
        func isOld(_ path: String) -> Bool {
            guard let attrs = try? fm.attributesOfItem(atPath: path),
                  let date = attrs[FileAttributeKey.modificationDate] as? Date else { return false }
            return date < cutoff
        }
        // The tablet UI keeps its results in the same folder, and a foldable or a display-size change can switch the
        // same device between the phone and the tablet UI: never touch a folder the tablet's files mention.
        var tabletManifests = ""
        for manifest in ["tablet_results.json", "tablet_batch_state.json", "tablet_saved.json"] {
            if let data = try? Data(contentsOf: storageDirectory.appendingPathComponent(manifest)),
               let text = String(data: data, encoding: .utf8) {
                tabletManifests += text
            }
        }
        // Result folders are named by the result's UUID (36 characters); files such as results.json stay.
        if let names = try? fm.contentsOfDirectory(atPath: storageDirectory.path) {
            for name in names where name.count == 36 && !keepIDs.contains(name) && !tabletManifests.contains(name) {
                let path = storageDirectory.appendingPathComponent(name).path
                if isOld(path) { try? fm.removeItem(atPath: path) }
            }
        }
        // Input copies are flat files named <UUID>.<ext>.
        if let names = try? fm.contentsOfDirectory(atPath: inputsDirectory.path) {
            for name in names where !keepInputs.contains(name) {
                let path = inputsDirectory.appendingPathComponent(name).path
                if isOld(path) { try? fm.removeItem(atPath: path) }
            }
        }
    }

    /// iOS moves the app's data container to a new path when the app is updated, so a file URL stored in
    /// results.json or waiting.json can point to the old container. Re-root it under today's caches folder
    /// when the old path is gone and the file exists there.
    static func rebased(_ url: URL, cachesBase: URL) -> URL {
        #if SKIP
        return url
        #else
        guard url.isFileURL, !FileManager.default.fileExists(atPath: url.path) else { return url }
        let path = url.path
        guard let range = path.range(of: "/Library/Caches/", options: .backwards) else { return url }
        let candidate = cachesBase.appendingPathComponent(String(path[range.upperBound...]))
        return FileManager.default.fileExists(atPath: candidate.path) ? candidate : url
        #endif
    }

    // Minimal valid 4000x3000 (12 MP) 1-bit grayscale PNG header+payload for querying service.canUseFullScale(forInput:).
    private static let twelveMPProbePNGBase64 = "iVBORw0KGgoAAAANSUhEUgAAD6AAAAu4AQAAAADdznpKAAAFx0lEQVR42u3BMQEAAADCoPVPbQ0PoAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAB4NPBiAAHZAhuiAAAAAElFTkSuQmCC"

    @discardableResult
    public func refreshFullScaleAvailability() -> Bool {
        let probeURL = FileManager.default.temporaryDirectory.appendingPathComponent("localsr-probe-12mp.png")
        if !FileManager.default.fileExists(atPath: probeURL.path),
           let data = Data(base64Encoded: Self.twelveMPProbePNGBase64) {
            try? data.write(to: probeURL, options: Data.WritingOptions.atomic)
        }
        let available = serviceBox.canUseFullScale(forInput: probeURL)
        isFullScaleAvailable = available
        return available
    }

    private func persistResults() {
        try? FileManager.default.createDirectory(at: storageDirectory, withIntermediateDirectories: true)
        if let data = try? JSONEncoder().encode(results) {
            try? data.write(to: storageDirectory.appendingPathComponent("results.json"), options: Data.WritingOptions.atomic)
        }
    }

    private func persistSavedResultIDs() {
        try? FileManager.default.createDirectory(at: storageDirectory, withIntermediateDirectories: true)
        let list = Array(savedResultIDs).sorted()
        if let data = try? JSONEncoder().encode(list) {
            try? data.write(to: storageDirectory.appendingPathComponent("saved.json"), options: Data.WritingOptions.atomic)
        }
    }

    private func persistWaitingItems() {
        let waitingURL = storageDirectory.appendingPathComponent("waiting.json")
        if waitingItems.isEmpty {
            try? FileManager.default.removeItem(at: waitingURL)
            return
        }
        try? FileManager.default.createDirectory(at: storageDirectory, withIntermediateDirectories: true)
        if let data = try? JSONEncoder().encode(waitingItems) {
            try? data.write(to: waitingURL, options: Data.WritingOptions.atomic)
        }
    }

    private func currentModelSetting() -> ModelKey {
        if let raw = defaults.string(forKey: "selectedModel"), let key = ModelKey(rawValue: raw) {
            return key
        }
        return .best
    }

    private func currentFullScaleSetting() -> Bool {
        defaults.bool(forKey: "fullScale")
    }

    private func shareStartsImmediatelySetting() -> Bool {
        if defaults.object(forKey: "shareStartsImmediately") != nil {
            return defaults.bool(forKey: "shareStartsImmediately")
        }
        return true
    }

    public func importSharedInboxIfNeeded(
        model: ModelKey? = nil,
        fullScale: Bool? = nil,
        startImmediately: Bool? = nil
    ) async {
        if isImportingInbox {
            pendingInboxRecheck = true
            return
        }
        isImportingInbox = true
        defer { isImportingInbox = false }

        repeat {
            pendingInboxRecheck = false
            let imported: [PickedPhotoInput]
            #if SKIP
            let rawItems = await AndroidShareInbox.awaitAndDrainPendingInputs()
            var mapped: [PickedPhotoInput] = []
            for item in rawItems {
                if let url = URL(string: item.urlString) {
                    mapped.append(PickedPhotoInput(url: url, displayName: item.displayName))
                }
            }
            imported = mapped
            #else
            let inbox = customInboxDirectory ?? ShareInboxImporter.defaultInboxDirectory()
            if let inbox {
                let destDir = inputsDirectory
                imported = await Task.detached(priority: .userInitiated) {
                    ShareInboxImporter.importAll(from: inbox, to: destDir)
                }.value
            } else {
                imported = []
            }
            #endif
            if !imported.isEmpty {
                handleIncomingInputs(
                    imported,
                    model: model,
                    fullScale: fullScale,
                    startImmediately: startImmediately
                )
            }
        } while pendingInboxRecheck
    }

    public func handleIncomingInputs(
        _ inputs: [PickedPhotoInput],
        model: ModelKey? = nil,
        fullScale: Bool? = nil,
        startImmediately: Bool? = nil
    ) {
        guard !inputs.isEmpty else { return }
        let resolvedModel = model ?? currentModelSetting()
        let resolvedFullScale = fullScale ?? currentFullScaleSetting()
        let shouldStart = startImmediately ?? shareStartsImmediatelySetting()

        if shouldStart && !isBatchRunning {
            var combined: [PickedPhotoInput] = waitingItems.map { PickedPhotoInput(url: $0.url, displayName: $0.displayName) }
            combined.append(contentsOf: inputs)
            if !waitingItems.isEmpty {
                waitingItems = []
                persistWaitingItems()
            }
            _ = refreshFullScaleAvailability()
            startBatch(inputs: combined, model: resolvedModel, fullScale: resolvedFullScale)
        } else {
            var next = waitingItems
            for i in 0..<inputs.count {
                let input = inputs[i]
                let name = (input.displayName?.isEmpty == false) ? input.displayName! : "Photo \(next.count + 1)"
                next.append(WaitingPhotoItem(url: input.url, displayName: name))
            }
            waitingItems = next
            persistWaitingItems()
        }
    }

    public func startWaitingBatch(model: ModelKey, fullScale: Bool) {
        guard !waitingItems.isEmpty, !isBatchRunning else { return }
        let inputs = waitingItems.map { PickedPhotoInput(url: $0.url, displayName: $0.displayName) }
        waitingItems = []
        persistWaitingItems()
        _ = refreshFullScaleAvailability()
        startBatch(inputs: inputs, model: model, fullScale: fullScale)
    }

    public func clearWaitingItems() {
        let existing = waitingItems
        waitingItems = []
        persistWaitingItems()
        for item in existing {
            deleteInputFileIfUnreferenced(item.url)
        }
    }

    public func result(for id: String) -> UpscaleResult? {
        for r in results {
            if r.id == id { return r }
        }
        return nil
    }

    public func isSaved(_ resultID: String) -> Bool {
        return savedResultIDs.contains(resultID)
    }

    public var unsavedResultsCount: Int {
        var count = 0
        for r in results {
            if !savedResultIDs.contains(r.id) {
                count += 1
            }
        }
        return count
    }

    public var overallBatchProgress: Double {
        guard !batchItems.isEmpty else { return 0.0 }
        var sum = 0.0
        for item in batchItems {
            switch item.status {
            case .waiting:
                sum += 0.0
            case .upscaling:
                sum += min(max(0.0, item.progress), 1.0)
            case .done, .failed, .cancelled:
                sum += 1.0
            }
        }
        return min(max(0.0, sum / Double(batchItems.count)), 1.0)
    }

    public var currentBatchItem: BatchItem? {
        for item in batchItems {
            if item.status == .upscaling { return item }
        }
        for item in batchItems {
            if item.status == .waiting { return item }
        }
        return batchItems.first
    }

    public var currentBatchItemNumber: Int {
        guard !batchItems.isEmpty else { return 0 }
        return min(batchCompletedCount + 1, batchItems.count)
    }

    public func startBatch(inputs: [PickedPhotoInput], model: ModelKey, fullScale: Bool = false) {
        guard !inputs.isEmpty, !isBatchRunning else { return }

        var items: [BatchItem] = []
        for i in 0..<inputs.count {
            let input = inputs[i]
            let name = (input.displayName?.isEmpty == false) ? input.displayName! : "Photo \(i + 1)"
            items.append(BatchItem(inputURL: input.url, displayName: name))
        }

        try? FileManager.default.createDirectory(at: storageDirectory, withIntermediateDirectories: true)
        try? Data().write(to: storageDirectory.appendingPathComponent("batch-in-progress"), options: Data.WritingOptions.atomic)
        lastBatchInterrupted = false
        batchSummary = nil
        pendingAutoOpenResultID = nil
        stageTransitionResultID = nil
        batchModel = model
        batchFullScale = fullScale
        batchItems = items
        batchCompletedCount = 0
        batchETAText = nil
        preparingModel = model
        preparingElapsedLong = false
        isBatchRunning = true

        batchTask?.cancel()
        batchTask = Task { @MainActor in
            await self.runBatch(model: model, fullScale: fullScale)
        }
    }

    public func retryFailed() {
        guard !isBatchRunning, let summary = batchSummary else { return }
        let failures: [BatchFailureItem]
        switch summary.kind {
        case .partialFailure(_, _, _, let f):
            failures = f
        case .allFailed(_, let f, _, _):
            failures = f
        case .succeeded, .interrupted:
            return
        }
        guard !failures.isEmpty else { return }
        let inputs = failures.map { PickedPhotoInput(url: $0.inputURL, displayName: $0.displayName) }
        startBatch(inputs: inputs, model: batchModel, fullScale: batchFullScale)
    }

    public func cancelBatch(dismiss: Bool = true) {
        batchTask?.cancel()
        preparingTimerTask?.cancel()
        preparingTimerTask = nil
    }

    public func dismissBatchSummary() {
        batchSummary = nil
    }

    public func completeAutoOpenTransition() {
        stageTransitionResultID = nil
        pendingAutoOpenResultID = nil
    }

    public func saveToLibrary(_ result: UpscaleResult) async {
        guard !isSaving, !isSavingAll, !savedResultIDs.contains(result.id) else { return }
        isSaving = true
        saveErrorMessage = nil
        saveAccessDenied = false
        do {
            try await serviceBox.saveToLibrary(result)
            var next = savedResultIDs
            next.insert(result.id)
            savedResultIDs = next
            persistSavedResultIDs()
            Haptics.success()
        } catch {
            saveAccessDenied = isNotAuthorizedError(error)
            saveErrorMessage = saveAccessDenied ? "Allow LocalSR to add photos in Settings." : describeError(error)
            Haptics.error()
        }
        isSaving = false
    }

    public func saveAll(resultIDs: [String]? = nil) async {
        guard !isSaving, !isSavingAll else { return }
        let targets: [UpscaleResult]
        if let resultIDs {
            let wanted = Set(resultIDs)
            targets = results.filter { wanted.contains($0.id) && !savedResultIDs.contains($0.id) }
        } else {
            targets = results.filter { !savedResultIDs.contains($0.id) }
        }
        guard !targets.isEmpty else { return }

        isSavingAll = true
        saveAllDoneCount = 0
        saveAllTotalCount = targets.count
        saveErrorMessage = nil
        saveAccessDenied = false

        var nextSaved = savedResultIDs
        for item in targets {
            do {
                try await serviceBox.saveToLibrary(item)
                nextSaved.insert(item.id)
                savedResultIDs = nextSaved
                persistSavedResultIDs()
                saveAllDoneCount += 1
            } catch {
                saveAccessDenied = isNotAuthorizedError(error)
                saveErrorMessage = saveAccessDenied ? "Allow LocalSR to add photos in Settings." : describeError(error)
                Haptics.error()
                isSavingAll = false
                return
            }
        }
        isSavingAll = false
        Haptics.success()
    }

    public func delete(_ result: UpscaleResult) {
        results.removeAll { $0.id == result.id }
        if savedResultIDs.remove(result.id) != nil {
            persistSavedResultIDs()
        }
        persistResults()
        deleteFiles(for: result, keepInput: false)
    }

    public func deleteAll() {
        let existing = results
        results = []
        savedResultIDs = []
        batchSummary = nil
        persistResults()
        persistSavedResultIDs()
        for item in existing {
            deleteFiles(for: item, keepInput: false)
        }
    }

    public func rerun(result: UpscaleResult, model: ModelKey, fullScale: Bool = false) async -> UpscaleResult? {
        guard isRerunningResultID == nil, !isBatchRunning else { return nil }
        isRerunningResultID = result.id
        rerunProgress = 0.0
        rerunErrorMessage = nil
        preparingModel = model
        #if SKIP
        // A rerun is a one-photo batch: the foreground service (and its wake lock) keeps it going when the user
        // leaves the app or the screen turns off. If Android refuses it, run anyway: the user is in the app.
        var holdsForegroundService = false
        #endif
        defer {
            isRerunningResultID = nil
            preparingModel = nil
            #if SKIP
            if holdsForegroundService {
                serviceBox.endBatch()
            }
            #endif
        }
        #if SKIP
        do {
            try await serviceBox.beginBatch()
            holdsForegroundService = true
        } catch {
            holdsForegroundService = false
        }
        #endif

        do {
            let verdict = try await serviceBox.prepare(model: model)
            verdicts[model] = verdict
            preparingModel = nil
            let updated = try await serviceBox.upscale(input: result.inputURL, model: model, fullScale: fullScale) { @Sendable p in
                Task { @MainActor in
                    self.rerunProgress = min(max(0.0, p), 1.0)
                }
            }
            if let idx = results.firstIndex(where: { $0.id == result.id }) {
                results[idx] = updated
            } else {
                results.insert(updated, at: 0)
            }
            if savedResultIDs.remove(result.id) != nil {
                persistSavedResultIDs()
            }
            persistResults()
            if result.id != updated.id {
                deleteFiles(for: result, keepInput: true)
            }
            revealResultID = updated.id
            Haptics.success()
            return updated
        } catch {
            if !isCancellation(error) {
                rerunErrorMessage = describeError(error)
                Haptics.error()
            }
            return nil
        }
    }

    public func releaseIdleModel() async {
        guard !isBatchRunning, isRerunningResultID == nil else { return }
        #if SKIP
        await serviceBox.releaseModel()
        #endif
    }

    private func runBatch(model: ModelKey, fullScale: Bool) async {
        let total = batchItems.count
        let startedAt = Date()
        var wasCancelled = false
        var failedCount = 0
        var succeededIDs: [String] = []
        var failures: [BatchFailureItem] = []

        #if SKIP
        do {
            try await serviceBox.beginBatch()
        } catch {
            let msg = describeError(error)
            for i in 0..<batchItems.count {
                batchItems[i].status = .failed
                batchItems[i].errorMessage = msg
                failures.append(BatchFailureItem(inputURL: batchItems[i].inputURL, displayName: batchItems[i].displayName, errorMessage: msg))
            }
            finishBatch(wasCancelled: false, failedCount: total, total: total, succeededIDs: [], failures: failures)
            return
        }
        serviceBox.setBatch(done: 0, total: total)
        if !didRequestNotifications {
            didRequestNotifications = true
            await PlatformSupport.requestNotificationPermissionIfNeeded()
        }
        #else
        BatchBackgroundTask.shared.begin(
            title: "Upscaling photos",
            subtitle: "0 of \(total)",
            // 100 units per photo, so the progress also moves while one large photo runs (iOS may end a task whose progress stalls).
            totalUnits: Int64(total * 100),
            onExpire: { [weak self] in
                Task { @MainActor in
                    self?.batchStoppedBySystem = true
                    self?.cancelBatch(dismiss: false)
                }
            }
        )
        #endif

        preparingModel = model
        preparingElapsedLong = false
        preparingTimerTask?.cancel()
        preparingTimerTask = Task { @MainActor in
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            if !Task.isCancelled && self.preparingModel != nil {
                self.preparingElapsedLong = true
            }
        }

        do {
            let verdict = try await serviceBox.prepare(model: model)
            verdicts[model] = verdict
            preparingTimerTask?.cancel()
            preparingModel = nil
            preparingElapsedLong = false
        } catch {
            preparingTimerTask?.cancel()
            preparingModel = nil
            preparingElapsedLong = false
            if isCancellation(error) || Task.isCancelled {
                wasCancelled = true
                markRemainingCancelled(from: 0)
            } else {
                failedCount = total
                let msg = describeError(error)
                for i in 0..<batchItems.count {
                    batchItems[i].status = .failed
                    batchItems[i].errorMessage = msg
                    failures.append(BatchFailureItem(inputURL: batchItems[i].inputURL, displayName: batchItems[i].displayName, errorMessage: msg))
                }
            }
            finishBatch(wasCancelled: wasCancelled, failedCount: failedCount, total: total, succeededIDs: [], failures: failures)
            return
        }

        for i in 0..<total {
            if Task.isCancelled {
                wasCancelled = true
                markRemainingCancelled(from: i)
                break
            }

            let itemID = batchItems[i].id
            let inputURL = batchItems[i].inputURL
            let displayName = batchItems[i].displayName
            batchItems[i].status = .upscaling
            batchItems[i].progress = 0.0

            #if SKIP
            serviceBox.setBatch(done: i, total: total)
            #endif

            do {
                let res = try await serviceBox.upscale(input: inputURL, model: model, fullScale: fullScale) { @Sendable p in
                    Task { @MainActor in
                        self.updateBatchItemProgress(id: itemID, progress: p)
                    }
                }
                batchItems[i].status = .done
                batchItems[i].progress = 1.0
                batchItems[i].resultID = res.id
                succeededIDs.append(res.id)
                results.insert(res, at: 0)
                persistResults()
            } catch {
                if isCancellation(error) || Task.isCancelled {
                    wasCancelled = true
                    markRemainingCancelled(from: i)
                    break
                } else {
                    failedCount += 1
                    let msg = describeError(error)
                    batchItems[i].status = .failed
                    batchItems[i].errorMessage = msg
                    failures.append(BatchFailureItem(inputURL: inputURL, displayName: displayName, errorMessage: msg))
                }
            }

            batchCompletedCount = i + 1
            let elapsed = Date().timeIntervalSince(startedAt)
            batchETAText = UIFormatting.formatETA(ETA.remaining(completed: batchCompletedCount, total: total, elapsedSeconds: elapsed))

            #if SKIP
            serviceBox.setBatch(done: batchCompletedCount, total: total)
            #else
            BatchBackgroundTask.shared.update(
                completedUnits: Int64(batchCompletedCount * 100),
                subtitle: "\(batchCompletedCount) of \(total)"
            )
            #endif
        }

        finishBatch(wasCancelled: wasCancelled, failedCount: failedCount, total: total, succeededIDs: succeededIDs, failures: failures)
    }

    private func finishBatch(
        wasCancelled: Bool,
        failedCount: Int,
        total: Int,
        succeededIDs: [String],
        failures: [BatchFailureItem]
    ) {
        preparingTimerTask?.cancel()
        preparingTimerTask = nil
        preparingModel = nil
        preparingElapsedLong = false
        isBatchRunning = false
        try? FileManager.default.removeItem(at: storageDirectory.appendingPathComponent("batch-in-progress"))

        #if SKIP
        serviceBox.setBatch(done: 0, total: 0)
        serviceBox.endBatch()
        #else
        BatchBackgroundTask.shared.end(success: !wasCancelled && failedCount == 0)
        #endif

        if wasCancelled {
            if batchStoppedBySystem {
                // iOS ended the background task: keep the photos that did not finish, and say so.
                batchStoppedBySystem = false
                let known = Set(waitingItems.map { $0.url })
                let unfinished = batchItems.filter { $0.status != .done && $0.status != .failed && !known.contains($0.inputURL) }
                waitingItems.append(contentsOf: unfinished.map { WaitingPhotoItem(url: $0.inputURL, displayName: $0.displayName) })
                persistWaitingItems()
                batchSummary = BatchSummary(id: "interrupted", kind: .interrupted)
            } else {
                batchSummary = nil
            }
            return
        }

        if failedCount == 0 && total == 1, let singleID = succeededIDs.first {
            revealResultID = singleID
            stageTransitionResultID = singleID
            pendingAutoOpenResultID = singleID
            Haptics.success()
            return
        }

        if failedCount == 0 && !succeededIDs.isEmpty {
            batchSummary = BatchSummary(kind: .succeeded(count: succeededIDs.count, resultIDs: succeededIDs))
            Haptics.success()
            return
        }

        if !succeededIDs.isEmpty && !failures.isEmpty {
            batchSummary = BatchSummary(
                kind: .partialFailure(
                    succeededCount: succeededIDs.count,
                    totalCount: total,
                    resultIDs: succeededIDs,
                    failures: failures
                )
            )
            Haptics.warning()
            return
        }

        if !failures.isEmpty {
            let firstMsg = failures.first?.errorMessage ?? "Upscaling failed."
            let noWorkingChip = failures.contains {
                $0.errorMessage.contains("upscaling is disabled") || $0.errorMessage.contains("produced invalid output")
            }
            batchSummary = BatchSummary(
                kind: .allFailed(
                    totalCount: total,
                    failures: failures,
                    message: firstMsg,
                    noWorkingChip: noWorkingChip
                )
            )
            Haptics.error()
        }
    }

    private func deleteFiles(for result: UpscaleResult, keepInput: Bool) {
        let fm = FileManager.default
        let resolvedStorage = storageDirectory.standardizedFileURL.resolvingSymlinksInPath()
        let resolvedInputs = inputsDirectory.standardizedFileURL.resolvingSymlinksInPath()

        let resolvedOutput = result.outputURL.standardizedFileURL.resolvingSymlinksInPath()
        let outputDir = resolvedOutput.deletingLastPathComponent()
        if result.outputURL.isFileURL &&
           outputDir.deletingLastPathComponent().path == resolvedStorage.path &&
           outputDir.lastPathComponent == result.id {
            try? fm.removeItem(at: outputDir)
        } else {
            if isFile(result.outputURL, inside: resolvedStorage) {
                try? fm.removeItem(at: result.outputURL)
            }
            if isFile(result.previewURL, inside: resolvedStorage) {
                try? fm.removeItem(at: result.previewURL)
            }
        }

        if !keepInput {
            deleteInputFileIfUnreferenced(result.inputURL)
        }
    }

    private func deleteInputFileIfUnreferenced(_ url: URL) {
        let fm = FileManager.default
        let resolvedStorage = storageDirectory.standardizedFileURL.resolvingSymlinksInPath()
        let resolvedInputs = inputsDirectory.standardizedFileURL.resolvingSymlinksInPath()
        guard isFile(url, inside: resolvedStorage) || isFile(url, inside: resolvedInputs) else { return }
        let resolvedPath = url.standardizedFileURL.resolvingSymlinksInPath().path
        let referencedByResults = results.contains {
            $0.inputURL.isFileURL && $0.inputURL.standardizedFileURL.resolvingSymlinksInPath().path == resolvedPath
        }
        let referencedByWaiting = waitingItems.contains {
            $0.url.isFileURL && $0.url.standardizedFileURL.resolvingSymlinksInPath().path == resolvedPath
        }
        let referencedByBatch = batchItems.contains {
            $0.inputURL.isFileURL && $0.inputURL.standardizedFileURL.resolvingSymlinksInPath().path == resolvedPath
        }
        if !referencedByResults && !referencedByWaiting && !referencedByBatch {
            try? fm.removeItem(at: url)
        }
    }

    private func isFile(_ url: URL, inside directory: URL) -> Bool {
        guard url.isFileURL else { return false }
        let filePath = url.standardizedFileURL.resolvingSymlinksInPath().path
        let dirPath = directory.standardizedFileURL.resolvingSymlinksInPath().path
        return filePath.hasPrefix(dirPath + "/")
    }

    private func updateBatchItemProgress(id: String, progress: Double) {
        guard let idx = batchItems.firstIndex(where: { $0.id == id }) else { return }
        if batchItems[idx].status == .upscaling {
            batchItems[idx].progress = min(max(0.0, progress), 1.0)
            #if !SKIP
            BatchBackgroundTask.shared.update(completedUnits: Int64(batchCompletedCount * 100 + Int(batchItems[idx].progress * 99.0)))
            #endif
        }
    }

    private func markRemainingCancelled(from startIndex: Int) {
        for j in startIndex..<batchItems.count {
            if batchItems[j].status == .waiting || batchItems[j].status == .upscaling {
                batchItems[j].status = .cancelled
            }
        }
    }

    private func isCancellation(_ error: Error) -> Bool {
        if error is CancellationError { return true }
        #if !SKIP
        if let serviceError = error as? AppleUpscaleService.ServiceError, case .cancelled = serviceError {
            return true
        }
        #endif
        return false
    }

    private func isNotAuthorizedError(_ error: Error) -> Bool {
        #if !SKIP
        if let saveError = error as? PhotoLibrarySaver.SaveError, case .notAuthorized = saveError {
            return true
        }
        #endif
        let msg = describeError(error)
        return msg.contains("not granted") || msg.contains("notAuthorized")
    }

    private func describeError(_ error: Error) -> String {
        let text = String(describing: error)
        let localized = error.localizedDescription
        return localized.isEmpty ? text : localized
    }
}
