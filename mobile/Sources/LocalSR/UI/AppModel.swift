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

private final class ServiceBox: @unchecked Sendable {
    private let service: UpscaleService

    init(_ service: UpscaleService) {
        self.service = service
    }

    func prepare(model: ModelKey) async throws -> DeviceVerdict {
        try await service.prepare(model: model)
    }

    func upscale(input: URL, model: ModelKey, progress: @escaping @Sendable (Double) -> Void) async throws -> UpscaleResult {
        try await service.upscale(input: input, model: model, progress: progress)
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
    private let serviceBox: ServiceBox

    public var results: [UpscaleResult] = []
    public var lastBatchInterrupted = false
    private let storageDirectory: URL
    public var verdicts: [ModelKey: DeviceVerdict] = [:]
    public var savedResultIDs: Set<String> = []
    public var isSaving: Bool = false
    public var saveErrorMessage: String? = nil

    public var isBatchPresented: Bool = false
    public var isBatchRunning: Bool = false
    public var preparingModel: ModelKey? = nil
    public var batchModel: ModelKey = .best
    public var batchItems: [BatchItem] = []
    public var batchCompletedCount: Int = 0
    public var batchETAText: String? = nil

    public var isRerunningResultID: String? = nil
    public var rerunErrorMessage: String? = nil

    @ObservationIgnored private var batchTask: Task<Void, Never>? = nil
    @ObservationIgnored private var didRequestNotifications: Bool = false

    public init(service: UpscaleService = makeUpscaleService(), cachesDirectory: URL? = nil) {
        self.serviceBox = ServiceBox(service)
        self.storageDirectory = (cachesDirectory ?? URL.cachesDirectory).appendingPathComponent("LocalSR", isDirectory: true)
        let marker = storageDirectory.appendingPathComponent("batch-in-progress")
        lastBatchInterrupted = FileManager.default.fileExists(atPath: marker.path)
        if lastBatchInterrupted { try? FileManager.default.removeItem(at: marker) }
        if let data = try? Data(contentsOf: storageDirectory.appendingPathComponent("results.json")),
           let stored = try? JSONDecoder().decode([UpscaleResult].self, from: data) {
            results = stored.filter { FileManager.default.fileExists(atPath: $0.outputURL.path) && FileManager.default.fileExists(atPath: $0.previewURL.path) }
            persistResults()
        }
    }

    private func persistResults() {
        try? FileManager.default.createDirectory(at: storageDirectory, withIntermediateDirectories: true)
        if let data = try? JSONEncoder().encode(results) {
            try? data.write(to: storageDirectory.appendingPathComponent("results.json"), options: Data.WritingOptions.atomic)
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

    public func startBatch(inputs: [PickedPhotoInput], model: ModelKey) {
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
        batchModel = model
        batchItems = items
        batchCompletedCount = 0
        batchETAText = nil
        preparingModel = nil
        isBatchRunning = true
        isBatchPresented = true

        batchTask?.cancel()
        batchTask = Task { @MainActor in
            await self.runBatch(model: model)
        }
    }

    public func cancelBatch(dismiss: Bool = true) {
        batchTask?.cancel()
        if dismiss {
            isBatchPresented = false
        }
    }

    public func dismissBatch() {
        guard !isBatchRunning else { return }
        isBatchPresented = false
    }

    public func saveToLibrary(_ result: UpscaleResult) async {
        guard !isSaving, !savedResultIDs.contains(result.id) else { return }
        isSaving = true
        saveErrorMessage = nil
        do {
            try await serviceBox.saveToLibrary(result)
            var next = savedResultIDs
            next.insert(result.id)
            savedResultIDs = next
        } catch {
            saveErrorMessage = describeError(error)
        }
        isSaving = false
    }

    public func rerun(result: UpscaleResult, model: ModelKey) async -> UpscaleResult? {
        guard isRerunningResultID == nil, !isBatchRunning else { return nil }
        isRerunningResultID = result.id
        rerunErrorMessage = nil
        defer { isRerunningResultID = nil }

        do {
            let verdict = try await serviceBox.prepare(model: model)
            verdicts[model] = verdict
            let updated = try await serviceBox.upscale(input: result.inputURL, model: model) { _ in }
            if let idx = results.firstIndex(where: { $0.id == result.id }) {
                results[idx] = updated
            } else {
                results.insert(updated, at: 0)
            }
            persistResults()
            return updated
        } catch {
            if !isCancellation(error) {
                rerunErrorMessage = describeError(error)
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

    private func runBatch(model: ModelKey) async {
        let total = batchItems.count
        let startedAt = Date()
        var wasCancelled = false
        var failedCount = 0

        #if SKIP
        do {
            try await serviceBox.beginBatch()
        } catch {
            for i in 0..<batchItems.count {
                batchItems[i].status = .failed
                batchItems[i].errorMessage = describeError(error)
            }
            finishBatch(wasCancelled: false, failedCount: total, total: total)
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
            totalUnits: Int64(total),
            onExpire: { [weak self] in
                Task { @MainActor in
                    self?.cancelBatch(dismiss: false)
                }
            }
        )
        #endif

        preparingModel = model
        do {
            let verdict = try await serviceBox.prepare(model: model)
            verdicts[model] = verdict
            preparingModel = nil
        } catch {
            preparingModel = nil
            if isCancellation(error) || Task.isCancelled {
                wasCancelled = true
                markRemainingCancelled(from: 0)
            } else {
                failedCount = total
                let msg = describeError(error)
                for i in 0..<batchItems.count {
                    batchItems[i].status = .failed
                    batchItems[i].errorMessage = msg
                }
            }
            finishBatch(wasCancelled: wasCancelled, failedCount: failedCount, total: total)
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
            batchItems[i].status = .upscaling
            batchItems[i].progress = 0.0

            #if SKIP
            serviceBox.setBatch(done: i, total: total)
            #endif

            do {
                let res = try await serviceBox.upscale(input: inputURL, model: model) { @Sendable p in
                    Task { @MainActor in
                        self.updateBatchItemProgress(id: itemID, progress: p)
                    }
                }
                batchItems[i].status = .done
                batchItems[i].progress = 1.0
                batchItems[i].resultID = res.id
                results.insert(res, at: 0)
                persistResults()
            } catch {
                if isCancellation(error) || Task.isCancelled {
                    wasCancelled = true
                    markRemainingCancelled(from: i)
                    break
                } else {
                    failedCount += 1
                    batchItems[i].status = .failed
                    batchItems[i].errorMessage = describeError(error)
                }
            }

            batchCompletedCount = i + 1
            let elapsed = Date().timeIntervalSince(startedAt)
            batchETAText = UIFormatting.formatETA(ETA.remaining(completed: batchCompletedCount, total: total, elapsedSeconds: elapsed))

            #if SKIP
            serviceBox.setBatch(done: batchCompletedCount, total: total)
            #else
            BatchBackgroundTask.shared.update(
                completedUnits: Int64(batchCompletedCount),
                subtitle: "\(batchCompletedCount) of \(total)"
            )
            #endif
        }

        finishBatch(wasCancelled: wasCancelled, failedCount: failedCount, total: total)
    }

    private func finishBatch(wasCancelled: Bool, failedCount: Int, total: Int) {
        preparingModel = nil
        isBatchRunning = false
        try? FileManager.default.removeItem(at: storageDirectory.appendingPathComponent("batch-in-progress"))

        #if SKIP
        serviceBox.setBatch(done: 0, total: 0)
        serviceBox.endBatch()
        #else
        BatchBackgroundTask.shared.end(success: !wasCancelled && failedCount == 0)
        #endif

        if !wasCancelled && failedCount == 0 {
            isBatchPresented = false
        }
    }

    private func updateBatchItemProgress(id: String, progress: Double) {
        guard let idx = batchItems.firstIndex(where: { $0.id == id }) else { return }
        if batchItems[idx].status == .upscaling {
            batchItems[idx].progress = min(max(0.0, progress), 1.0)
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

    private func describeError(_ error: Error) -> String {
        let text = String(describing: error)
        let localized = error.localizedDescription
        return localized.isEmpty ? text : localized
    }
}
