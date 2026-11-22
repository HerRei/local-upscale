import Testing
import Foundation
import LocalSRCore
@testable import LocalSR

@Suite struct LocalSRTests {

    @Test func localSR() throws {
        #expect(1 + 2 == 3, "basic test")
    }

    @Test func decodeType() throws {
        // load the TestData.json file from the Resources folder and decode it into a struct
        let resourceURL: URL = try #require(Bundle.module.url(forResource: "TestData", withExtension: "json"))
        let testData = try JSONDecoder().decode(TestData.self, from: Data(contentsOf: resourceURL))
        #expect(testData.testModuleName == "LocalSR")
    }

}

struct TestData : Codable, Hashable {
    var testModuleName: String
}

@Suite struct ResultPersistenceTests {
    @Test @MainActor func restoresExistingResultsAndReportsInterruptedBatch() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let directory = root.appendingPathComponent("LocalSR")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let output = directory.appendingPathComponent("output")
        let preview = directory.appendingPathComponent("preview")
        try Data().write(to: output)
        try Data().write(to: preview)
        let result = UpscaleResult(id: "one", inputURL: output, outputURL: output, previewURL: preview, width: 4, height: 4, model: .best, unit: .cpu, seconds: 1)
        let missing = UpscaleResult(id: "missing", inputURL: output, outputURL: directory.appendingPathComponent("gone"), previewURL: preview, width: 4, height: 4, model: .best, unit: .cpu, seconds: 1)
        try JSONEncoder().encode([result, missing]).write(to: directory.appendingPathComponent("results.json"))
        try Data().write(to: directory.appendingPathComponent("batch-in-progress"))
        let app = AppModel(cachesDirectory: root)
        #expect(app.results.map(\.id) == ["one"])
        #expect(app.lastBatchInterrupted)
        #expect(!AppModel(cachesDirectory: root).lastBatchInterrupted)
    }

    @Test @MainActor func deletionRemovesOnlyResultDirectoryAndLeavesOutsideFilesUntouched() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }

        let cachesDir = root.appendingPathComponent("Caches")
        let resultDir = cachesDir.appendingPathComponent("LocalSR").appendingPathComponent("target-id")
        let otherResultDir = cachesDir.appendingPathComponent("LocalSR").appendingPathComponent("sibling-id")
        let externalDir = root.appendingPathComponent("PhotosLibrary")

        try FileManager.default.createDirectory(at: resultDir, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: otherResultDir, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: externalDir, withIntermediateDirectories: true)

        let targetOutput = resultDir.appendingPathComponent("output.heic")
        let targetPreview = resultDir.appendingPathComponent("preview.jpg")
        let externalInput = externalDir.appendingPathComponent("original.jpg")
        let outsideSibling = cachesDir.appendingPathComponent("LocalSR").appendingPathComponent("keep-me.txt")
        let siblingOutput = otherResultDir.appendingPathComponent("output.heic")

        try Data("out".utf8).write(to: targetOutput)
        try Data("prev".utf8).write(to: targetPreview)
        try Data("orig".utf8).write(to: externalInput)
        try Data("keep".utf8).write(to: outsideSibling)
        try Data("sib".utf8).write(to: siblingOutput)

        let targetResult = UpscaleResult(
            id: "target-id",
            inputURL: externalInput,
            outputURL: targetOutput,
            previewURL: targetPreview,
            width: 4,
            height: 4,
            model: .best,
            unit: .cpu,
            seconds: 1
        )
        let siblingResult = UpscaleResult(
            id: "sibling-id",
            inputURL: externalInput,
            outputURL: siblingOutput,
            previewURL: siblingOutput,
            width: 4,
            height: 4,
            model: .best,
            unit: .cpu,
            seconds: 1
        )

        let storeDir = cachesDir.appendingPathComponent("LocalSR")
        try JSONEncoder().encode([targetResult, siblingResult]).write(to: storeDir.appendingPathComponent("results.json"))

        let app = AppModel(cachesDirectory: cachesDir)
        #expect(app.results.map(\.id) == ["target-id", "sibling-id"])

        app.delete(targetResult)

        #expect(app.results.map(\.id) == ["sibling-id"])
        #expect(!FileManager.default.fileExists(atPath: resultDir.path))
        #expect(FileManager.default.fileExists(atPath: externalInput.path))
        #expect(FileManager.default.fileExists(atPath: outsideSibling.path))
        #expect(FileManager.default.fileExists(atPath: siblingOutput.path))
    }

    @Test @MainActor func savedResultIDsPersistAcrossRelaunch() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }

        let storeDir = root.appendingPathComponent("LocalSR")
        let itemDir = storeDir.appendingPathComponent("saved-id")
        try FileManager.default.createDirectory(at: itemDir, withIntermediateDirectories: true)
        let output = itemDir.appendingPathComponent("output.heic")
        let preview = itemDir.appendingPathComponent("preview.jpg")
        try Data("out".utf8).write(to: output)
        try Data("prev".utf8).write(to: preview)

        let result = UpscaleResult(id: "saved-id", inputURL: output, outputURL: output, previewURL: preview, width: 4, height: 4, model: .best, unit: .cpu, seconds: 1)
        try JSONEncoder().encode([result]).write(to: storeDir.appendingPathComponent("results.json"))
        try JSONEncoder().encode(["saved-id"]).write(to: storeDir.appendingPathComponent("saved.json"))

        let app = AppModel(cachesDirectory: root)
        #expect(app.savedResultIDs.contains("saved-id"))
    }
}
