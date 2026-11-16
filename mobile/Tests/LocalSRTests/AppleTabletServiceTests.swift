#if !SKIP
import Foundation
import XCTest
import LocalSRCore
@testable import LocalSR

private final class TabletEvents: @unchecked Sendable {
    private let lock = NSLock()
    private var intervals: [(ModelLane, Double, Double)] = []
    func record(_ event: TabletUpscaleEvent) {
        if case let .prediction(_, _, _, lane, start, end) = event { lock.withLock { intervals.append((lane, start, end)) } }
    }
    func hasOverlap() -> Bool {
        lock.withLock {
            let a = intervals.filter { $0.0 == .neuralEngine }
            let b = intervals.filter { $0.0 == .gpu }
            return a.contains { x in b.contains { y in x.1 < y.2 && y.1 < x.2 } }
        }
    }
    var lanes: [ModelLane] { lock.withLock { intervals.map { $0.0 } } }
}

final class AppleTabletServiceTests: XCTestCase {
    func testMixedSixPhotoBatch() async throws {
        let root = Synthetic.tempDirectory("tablet-batch")
        defer { try? FileManager.default.removeItem(at: root) }
        let url = try Synthetic.writePhoto(.init(width: 400, height: 300, type: .jpeg), to: root)
        let suite = "localsr.tablet.tests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let service = AppleTabletUpscaleService(defaults: defaults, cachesDirectory: root)
        let fixed = Plan(stages: [.jpegFix, .best])
        let finest = Plan(stages: [.finest])
        let inputs = (0..<6).map { TabletInput(id: String($0), url: url, plan: $0 % 2 == 0 ? fixed : finest) }
        let events = TabletEvents()
        let start = Date()
        let results = try await service.run(plan: fixed, inputs: inputs, fullScale: false) { events.record($0) }
        print("TABLET_TIMING Both \(Date().timeIntervalSince(start))")
        XCTAssertEqual(results.count, 6)
        XCTAssertTrue(events.hasOverlap(), "Predictions on the two lanes should overlap")
        for result in results {
            XCTAssertEqual(result.width, 1600)
            XCTAssertEqual(result.height, 1200)
            XCTAssertTrue(FileManager.default.fileExists(atPath: result.previewURL.path))
            let before = Synthetic.rgba(of: url)
            let after = Synthetic.rgba(of: result.outputURL)
            var down = [UInt8](repeating: 255, count: before.width * before.height * 4)
            for y in 0..<before.height { for x in 0..<before.width { for c in 0..<3 {
                var sum = 0
                for dy in 0..<4 { for dx in 0..<4 { sum += Int(after.pixels[((y*4+dy)*after.width+x*4+dx)*4+c]) } }
                down[(y*before.width+x)*4+c] = UInt8((sum+8)/16)
            } } }
            XCTAssertGreaterThan(Synthetic.psnr(before.pixels, down), 22)
        }
        service.laneOverride = .gpuOnly
        let gpuEvents = TabletEvents()
        let gpuStart = Date()
        let gpuResults = try await service.run(plan: fixed, inputs: inputs, fullScale: false) { gpuEvents.record($0) }
        print("TABLET_TIMING GPU \(Date().timeIntervalSince(gpuStart))")
        XCTAssertEqual(gpuResults.count, 6)
        XCTAssertTrue(gpuEvents.lanes.allSatisfy { $0 == .gpu })
        service.laneOverride = .neuralEngineOnly
        do {
            _ = try await service.run(plan: fixed, inputs: inputs, fullScale: false) { _ in }
            XCTFail("Finest has CPU operations in its Neural Engine compute plan")
        } catch LaneScheduler.ScheduleError.unavailable { }
    }

    func testBackgroundFallbackAndCancellation() async throws {
        let root = Synthetic.tempDirectory("tablet-cancel")
        defer { try? FileManager.default.removeItem(at: root) }
        let url = try Synthetic.writePhoto(.init(width: 600, height: 500, type: .jpeg), to: root)
        let suite = "localsr.tablet.tests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let service = AppleTabletUpscaleService(defaults: defaults, cachesDirectory: root, gpuRestricted: { true })
        service.laneOverride = .gpuOnly
        let events = TabletEvents()
        let result = try await service.run(plan: Plan(stages: [.best]), inputs: [TabletInput(id: "one", url: url)], fullScale: false) { events.record($0) }
        XCTAssertEqual(result.count, 1)
        var laneIterator = service.laneActivity.makeAsyncIterator()
        let activity = await laneIterator.next()
        XCTAssertNotNil(activity)
        XCTAssertFalse(events.lanes.isEmpty)
        XCTAssertTrue(events.lanes.allSatisfy { $0 == .neuralEngine })
        let restored = try await service.run(plan: Plan(stages: [.jpegFix]), inputs: [TabletInput(id: "restore", url: url)], fullScale: false) { _ in }
        XCTAssertEqual(restored[0].outputScale, 1)
        XCTAssertEqual(restored[0].width, 600)
        XCTAssertEqual(restored[0].height, 500)
        let before = Set(try FileManager.default.contentsOfDirectory(atPath: root.appendingPathComponent("LocalSR").path))
        do {
            _ = try await service.run(plan: Plan(stages: [.best]), inputs: (0..<6).map { TabletInput(id: "\($0)", url: url) }, fullScale: false) { event in
                if case .progress = event { service.cancel() }
            }
            XCTFail("Expected cancellation")
        } catch { }
        let after = Set(try FileManager.default.contentsOfDirectory(atPath: root.appendingPathComponent("LocalSR").path))
        XCTAssertEqual(before, after)
    }

    func testComparableSixPhotoTimings() async throws {
        let root = Synthetic.tempDirectory("tablet-timings")
        defer { try? FileManager.default.removeItem(at: root) }
        let url = try Synthetic.writePhoto(.init(width: 256, height: 192, type: .jpeg), to: root)
        let suite = "localsr.tablet.tests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let service = AppleTabletUpscaleService(defaults: defaults, cachesDirectory: root)
        let fixed = Plan(stages: [.jpegFix, .best])
        let inputs = (0..<6).map { TabletInput(id: String($0), url: url, plan: $0 % 2 == 0 ? fixed : Plan(stages: [.fast])) }
        let checker = TabletDeviceCheck(defaults: defaults)
        for model in [TabletModelID.jpegFix, .best, .fast] { _ = try await checker.verdict(for: model) }
        // Load and warm every model/unit before timing so modes are compared on the same basis.
        for mode in [TabletLaneOverride.both, .neuralEngineOnly, .gpuOnly] {
            service.laneOverride = mode
            _ = try await service.run(plan: fixed, inputs: Array(inputs.prefix(2)), fullScale: false) { _ in }
        }
        for (name, mode) in [("Both", TabletLaneOverride.both), ("NE", .neuralEngineOnly), ("GPU", .gpuOnly)] {
            service.laneOverride = mode
            let events = TabletEvents()
            let start = Date()
            let results = try await service.run(plan: fixed, inputs: inputs, fullScale: false) { events.record($0) }
            print("TABLET_COMPARABLE \(name) \(Date().timeIntervalSince(start))")
            XCTAssertEqual(results.count, 6)
            if name == "NE" { XCTAssertTrue(events.lanes.allSatisfy { $0 == .neuralEngine }) }
            if name == "GPU" { XCTAssertTrue(events.lanes.allSatisfy { $0 == .gpu }) }
        }
    }
}
#endif
