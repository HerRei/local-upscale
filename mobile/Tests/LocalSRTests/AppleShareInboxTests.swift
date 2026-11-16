#if !SKIP
import Testing
import Foundation
import LocalSRCore
@testable import LocalSR

private final class StubUpscaleService: UpscaleService, @unchecked Sendable {
    var upscaledInputs: [URL] = []

    func prepare(model: ModelKey) async throws -> DeviceVerdict {
        DeviceVerdict(unit: .cpu, reason: "Stub CPU")
    }

    func upscale(input: URL, model: ModelKey, fullScale: Bool, progress: @escaping (Double) -> Void) async throws -> UpscaleResult {
        upscaledInputs.append(input)
        progress(0.0)
        progress(1.0)
        let dir = input.deletingLastPathComponent()
        let out = dir.appendingPathComponent("out-\(UUID().uuidString).heic")
        let prev = dir.appendingPathComponent("prev-\(UUID().uuidString).jpg")
        try Data("out".utf8).write(to: out)
        try Data("prev".utf8).write(to: prev)
        return UpscaleResult(
            id: UUID().uuidString,
            inputURL: input,
            outputURL: out,
            previewURL: prev,
            width: 400,
            height: 300,
            model: model,
            unit: .cpu,
            seconds: 0.1
        )
    }

    func canUseFullScale(forInput: URL) -> Bool { true }

    func saveToLibrary(_ result: UpscaleResult) async throws {}
}

@Suite struct AppleShareInboxTests {
    private func makeTempRoot() throws -> URL {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("share-inbox-tests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        return root
    }

    @Test func importsValidBatchesPreservingBytesAndCleansInbox() throws {
        let root = try makeTempRoot()
        defer { try? FileManager.default.removeItem(at: root) }

        let inbox = root.appendingPathComponent("Inbox", isDirectory: true)
        let inputs = root.appendingPathComponent("LocalSRInputs", isDirectory: true)
        let batch1 = inbox.appendingPathComponent("batch-1", isDirectory: true)
        let batch2 = inbox.appendingPathComponent("batch-2", isDirectory: true)
        try FileManager.default.createDirectory(at: batch1, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: batch2, withIntermediateDirectories: true)

        let bytes1 = Data([0x01, 0x02, 0x03, 0x48, 0x45, 0x49, 0x43])
        let bytes2 = Data([0xFF, 0xD8, 0xFF, 0xE0, 0x99])
        try bytes1.write(to: batch1.appendingPathComponent("a.heic"))
        try bytes2.write(to: batch2.appendingPathComponent("b.jpg"))

        let m1 = ShareInboxManifest(version: 1, createdAt: "2026-11-15T10:00:00Z", items: [
            .init(filename: "a.heic", displayName: "IMG_0001")
        ])
        let m2 = ShareInboxManifest(version: 1, createdAt: "2026-11-15T10:01:00Z", items: [
            .init(filename: "b.jpg", displayName: "IMG_0002")
        ])
        try JSONEncoder().encode(m1).write(to: batch1.appendingPathComponent("manifest.json"))
        try JSONEncoder().encode(m2).write(to: batch2.appendingPathComponent("manifest.json"))

        let imported = ShareInboxImporter.importAll(from: inbox, to: inputs)
        #expect(imported.count == 2)
        #expect(Set(imported.compactMap(\.displayName)) == ["IMG_0001", "IMG_0002"])

        for item in imported {
            #expect(item.url.deletingLastPathComponent().standardizedFileURL.path == inputs.standardizedFileURL.path)
            let copied = try Data(contentsOf: item.url)
            if item.displayName == "IMG_0001" {
                #expect(item.url.pathExtension == "heic")
                #expect(copied == bytes1)
            } else {
                #expect(item.url.pathExtension == "jpg")
                #expect(copied == bytes2)
            }
        }

        #expect(!FileManager.default.fileExists(atPath: batch1.path))
        #expect(!FileManager.default.fileExists(atPath: batch2.path))
    }

    @Test func ignoresPartialAndCorruptManifestsSafely() throws {
        let root = try makeTempRoot()
        defer { try? FileManager.default.removeItem(at: root) }

        let inbox = root.appendingPathComponent("Inbox", isDirectory: true)
        let inputs = root.appendingPathComponent("LocalSRInputs", isDirectory: true)
        let outsideFile = root.appendingPathComponent("secret.jpg")
        try Data("outside".utf8).write(to: outsideFile)

        // 1. Missing manifest.json (partial write)
        let noManifest = inbox.appendingPathComponent("01-missing-manifest", isDirectory: true)
        try FileManager.default.createDirectory(at: noManifest, withIntermediateDirectories: true)
        try Data("img".utf8).write(to: noManifest.appendingPathComponent("pending.jpg"))

        // 2. Corrupt JSON
        let corrupt = inbox.appendingPathComponent("02-corrupt", isDirectory: true)
        try FileManager.default.createDirectory(at: corrupt, withIntermediateDirectories: true)
        try Data("{\"version\":1,\"items\":[{\"filename\":".utf8).write(to: corrupt.appendingPathComponent("manifest.json"))
        try Data("img".utf8).write(to: corrupt.appendingPathComponent("photo.jpg"))

        // 3. Unsupported version
        let badVersion = inbox.appendingPathComponent("03-bad-version", isDirectory: true)
        try FileManager.default.createDirectory(at: badVersion, withIntermediateDirectories: true)
        try JSONEncoder().encode(ShareInboxManifest(version: 99, items: [.init(filename: "photo.jpg")]))
            .write(to: badVersion.appendingPathComponent("manifest.json"))
        try Data("img".utf8).write(to: badVersion.appendingPathComponent("photo.jpg"))

        // 4. Path traversal and missing referenced file
        let traversal = inbox.appendingPathComponent("04-traversal", isDirectory: true)
        try FileManager.default.createDirectory(at: traversal, withIntermediateDirectories: true)
        try JSONEncoder().encode(ShareInboxManifest(version: 1, items: [
            .init(filename: "../../secret.jpg"),
            .init(filename: "does-not-exist.jpg"),
        ])).write(to: traversal.appendingPathComponent("manifest.json"))

        // 5. Valid batch alongside broken ones
        let valid = inbox.appendingPathComponent("05-valid", isDirectory: true)
        try FileManager.default.createDirectory(at: valid, withIntermediateDirectories: true)
        let validBytes = Data("valid-heic".utf8)
        try validBytes.write(to: valid.appendingPathComponent("good.heic"))
        try JSONEncoder().encode(ShareInboxManifest(version: 1, items: [.init(filename: "good.heic", displayName: "Good")]))
            .write(to: valid.appendingPathComponent("manifest.json"))

        let imported = ShareInboxImporter.importAll(from: inbox, to: inputs)
        #expect(imported.count == 1)
        #expect(imported[0].displayName == "Good")
        #expect(try Data(contentsOf: imported[0].url) == validBytes)

        #expect(!FileManager.default.fileExists(atPath: valid.path))
        #expect(FileManager.default.fileExists(atPath: noManifest.path))
        #expect(FileManager.default.fileExists(atPath: corrupt.path))
        #expect(FileManager.default.fileExists(atPath: badVersion.path))
        #expect(FileManager.default.fileExists(atPath: traversal.path))
        #expect(FileManager.default.fileExists(atPath: outsideFile.path))
    }

    @Test func inboxNotCleanedWhenDestinationMoveFails() throws {
        let root = try makeTempRoot()
        defer { try? FileManager.default.removeItem(at: root) }

        let inbox = root.appendingPathComponent("Inbox", isDirectory: true)
        let batch = inbox.appendingPathComponent("batch-fail", isDirectory: true)
        try FileManager.default.createDirectory(at: batch, withIntermediateDirectories: true)
        let imgURL = batch.appendingPathComponent("photo.jpg")
        try Data("photo-bytes".utf8).write(to: imgURL)
        try JSONEncoder().encode(ShareInboxManifest(version: 1, items: [.init(filename: "photo.jpg")]))
            .write(to: batch.appendingPathComponent("manifest.json"))

        // Block inputsDirectory creation by placing a regular file at that path.
        let blockedInputs = root.appendingPathComponent("BlockedInputs")
        try Data("not-a-directory".utf8).write(to: blockedInputs)

        let imported = ShareInboxImporter.importAll(from: inbox, to: blockedInputs)
        #expect(imported.isEmpty)
        #expect(FileManager.default.fileExists(atPath: imgURL.path))
        #expect(FileManager.default.fileExists(atPath: batch.appendingPathComponent("manifest.json").path))
    }

    @Test @MainActor func appModelWaitingAndImmediateImportModes() async throws {
        let root = try makeTempRoot()
        defer { try? FileManager.default.removeItem(at: root) }

        let suite = "localsr.share.tests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }

        let caches = root.appendingPathComponent("Caches", isDirectory: true)
        let inbox = root.appendingPathComponent("Inbox", isDirectory: true)

        func writeShareBatch(name: String, bytes: Data) throws -> URL {
            let dir = inbox.appendingPathComponent(UUID().uuidString, isDirectory: true)
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            try bytes.write(to: dir.appendingPathComponent(name))
            try JSONEncoder().encode(ShareInboxManifest(version: 1, items: [.init(filename: name, displayName: "Shared")]))
                .write(to: dir.appendingPathComponent("manifest.json"))
            return dir
        }

        // Setting OFF: adds waiting items without starting batch
        defaults.set(false, forKey: "shareStartsImmediately")
        let batchWait = try writeShareBatch(name: "wait.jpg", bytes: Data("wait-bytes".utf8))
        let service1 = StubUpscaleService()
        let model1 = AppModel(service: service1, cachesDirectory: caches, defaults: defaults, inboxDirectory: inbox)
        await model1.importSharedInboxIfNeeded()

        #expect(!FileManager.default.fileExists(atPath: batchWait.path))
        #expect(model1.waitingItems.count == 1)
        #expect(!model1.isBatchRunning)
        #expect(service1.upscaledInputs.isEmpty)

        // Waiting items persist across relaunch and clear removes input file
        let model1Relaunch = AppModel(service: service1, cachesDirectory: caches, defaults: defaults, inboxDirectory: inbox)
        #expect(model1Relaunch.waitingItems.count == 1)
        let waitingFileURL = model1Relaunch.waitingItems[0].url
        #expect(FileManager.default.fileExists(atPath: waitingFileURL.path))
        model1Relaunch.clearWaitingItems()
        #expect(model1Relaunch.waitingItems.isEmpty)
        #expect(!FileManager.default.fileExists(atPath: waitingFileURL.path))

        // Setting ON (default): starts batch immediately
        defaults.removeObject(forKey: "shareStartsImmediately")
        let batchAuto = try writeShareBatch(name: "auto.heic", bytes: Data("auto-bytes".utf8))
        let service2 = StubUpscaleService()
        let model2 = AppModel(service: service2, cachesDirectory: caches, defaults: defaults, inboxDirectory: inbox)
        await model2.importSharedInboxIfNeeded()

        #expect(!FileManager.default.fileExists(atPath: batchAuto.path))
        for _ in 0..<50 where model2.isBatchRunning {
            try await Task.sleep(nanoseconds: 20_000_000)
        }
        #expect(!model2.isBatchRunning)
        #expect(service2.upscaledInputs.count == 1)
        #expect(model2.results.count == 1)
    }
}
#endif
