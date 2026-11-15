import Testing
import Foundation
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
