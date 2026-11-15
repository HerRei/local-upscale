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
}
