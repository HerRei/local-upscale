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

/// Which photos completed or failed, and when each photo's predictions ran.
private final class TabletOutcomes: @unchecked Sendable {
    private let lock = NSLock()
    private var completed: [String] = []
    private var failed: [(String, String)] = []
    private var spans: [String: (Double, Double)] = [:]
    private var seen: Set<String> = []
    func record(_ event: TabletUpscaleEvent) {
        lock.withLock {
            switch event {
            case .completed(let result): completed.append(result.id); seen.insert(result.id)
            case .failed(let failure): failed.append((failure.photoID, failure.message)); seen.insert(failure.photoID)
            case .progress(let id, _, _): seen.insert(id)
            case let .prediction(id, _, _, _, start, end):
                seen.insert(id)
                let old = spans[id] ?? (start, end)
                spans[id] = (min(old.0, start), max(old.1, end))
            default: break
            }
        }
    }
    var completedIDs: Set<String> { lock.withLock { Set(completed) } }
    var failedIDs: [String] { lock.withLock { failed.map { $0.0 } } }
    var failureMessages: [String] { lock.withLock { failed.map { $0.1 } } }
    var seenIDs: Set<String> { lock.withLock { seen } }
    /// True when two photos had predictions running at the same time.
    var photosOverlap: Bool {
        lock.withLock {
            let list = Array(spans.values)
            for i in 0..<list.count { for j in (i + 1)..<list.count where list[i].0 < list[j].1 && list[j].0 < list[i].1 { return true } }
            return false
        }
    }
}

final class AppleTabletServiceTests: XCTestCase {
    private func makeService(_ name: String, physicalMemory: Int64? = nil) -> (AppleTabletUpscaleService, URL, () -> Void) {
        let root = Synthetic.tempDirectory(name)
        let suite = "localsr.tablet.tests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        let service = AppleTabletUpscaleService(defaults: defaults, cachesDirectory: root, physicalMemory: physicalMemory)
        return (service, root, {
            defaults.removePersistentDomain(forName: suite)
            try? FileManager.default.removeItem(at: root)
        })
    }

    /// Finding #3: a broken photo fails alone, with its own reason, and the batch finishes the others.
    func testOneFailingPhotoFailsOnlyThatPhoto() async throws {
        let (service, root, cleanup) = makeService("tablet-one-fails")
        defer { cleanup() }
        let good = try Synthetic.writePhoto(.init(width: 200, height: 150, type: .jpeg), to: root)
        let broken = root.appendingPathComponent("broken.jpg")
        try Data([0xFF, 0xD8, 0xFF, 0x00, 0x01, 0x02, 0x03]).write(to: broken)
        let outcomes = TabletOutcomes()
        let inputs = [TabletInput(id: "a", url: good), TabletInput(id: "broken", url: broken), TabletInput(id: "c", url: good)]
        let results = try await service.run(plan: Plan(stages: [.fast]), inputs: inputs, fullScale: false) { outcomes.record($0) }
        XCTAssertEqual(Set(results.map(\.id)), ["a", "c"])
        XCTAssertEqual(outcomes.completedIDs, ["a", "c"])
        XCTAssertEqual(outcomes.failedIDs, ["broken"])
        let message = outcomes.failureMessages.first ?? ""
        XCTAssertFalse(message.isEmpty)
        XCTAssertFalse(message.contains("error 0"), "The reason must be readable: \(message)")
    }

    /// Finding #4: a photo removed while its batch runs is skipped, and only for that run.
    func testSkippedPhotoIsLeftOutOfTheRun() async throws {
        let (service, root, cleanup) = makeService("tablet-skip")
        defer { cleanup() }
        let url = try Synthetic.writePhoto(.init(width: 200, height: 150, type: .jpeg), to: root)
        service.skip(photoID: "b")
        let outcomes = TabletOutcomes()
        let inputs = ["a", "b", "c"].map { TabletInput(id: $0, url: url) }
        let results = try await service.run(plan: Plan(stages: [.fast]), inputs: inputs, fullScale: false) { outcomes.record($0) }
        XCTAssertEqual(Set(results.map(\.id)), ["a", "c"])
        XCTAssertFalse(outcomes.seenIDs.contains("b"))
        let again = try await service.run(plan: Plan(stages: [.fast]), inputs: [TabletInput(id: "b", url: url)], fullScale: false) { _ in }
        XCTAssertEqual(again.map(\.id), ["b"])
    }

    /// Finding #8: iPads with less than 6 GB run one photo at a time.
    func testLowMemoryIPadRunsOnePhotoAtATime() async throws {
        let (service, root, cleanup) = makeService("tablet-one-worker", physicalMemory: 3 * 1024 * 1024 * 1024)
        defer { cleanup() }
        let url = try Synthetic.writePhoto(.init(width: 400, height: 300, type: .jpeg), to: root)
        let outcomes = TabletOutcomes()
        let inputs = (0..<4).map { TabletInput(id: String($0), url: url) }
        let results = try await service.run(plan: Plan(stages: [.best]), inputs: inputs, fullScale: false) { outcomes.record($0) }
        XCTAssertEqual(results.count, 4)
        XCTAssertFalse(outcomes.photosOverlap, "One worker must not run two photos at once")
    }

    /// Finding #8: the Full 4x memory check and the reservation are one step for both workers.
    func testFullScaleBudgetAdmitsOneWorkerAtATime() {
        let budget = TabletMemoryBudget(available: { 1_000 })
        XCTAssertTrue(budget.reserve(600))
        XCTAssertFalse(budget.reserve(600), "A second worker must not pass the check the first one used")
        budget.release(600)
        XCTAssertTrue(budget.reserve(600))
    }

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

    func testLaneSchedulerSpreadsDualChipWorkAcrossBothLanes() throws {
        final class LaneBox: @unchecked Sendable {
            private let lock = NSLock()
            private var lanes: [ModelLane] = []
            func append(_ lane: ModelLane) { lock.withLock { lanes.append(lane) } }
            var values: [ModelLane] { lock.withLock { lanes } }
        }
        let scheduler = LaneScheduler()
        let verdict = TabletDeviceVerdict(preferred: .npu, cpu: true, gpu: true, neuralEngine: true)
        let enteredFirst = DispatchSemaphore(value: 0)
        let releaseFirst = DispatchSemaphore(value: 0)
        let recorded = LaneBox()
        let group = DispatchGroup()

        group.enter()
        DispatchQueue.global().async {
            defer { group.leave() }
            try? scheduler.run(photoID: "p1", stage: 0, model: .best, verdict: verdict, override: .both, isCancelled: { false }) { _, lane in
                recorded.append(lane)
                enteredFirst.signal()
                _ = releaseFirst.wait(timeout: .now() + 2)
            }
        }

        _ = enteredFirst.wait(timeout: .now() + 2)
        group.enter()
        DispatchQueue.global().async {
            defer { group.leave() }
            try? scheduler.run(photoID: "p2", stage: 0, model: .best, verdict: verdict, override: .both, isCancelled: { false }) { _, lane in
                recorded.append(lane)
                releaseFirst.signal()
            }
        }

        XCTAssertEqual(group.wait(timeout: .now() + 5), .success)
        XCTAssertEqual(Set(recorded.values), Set([.neuralEngine, .gpu]), "Dual-chip model should spread concurrent jobs across both lanes")

        // When NPU failed and GPU passed, concurrent jobs must not spill onto CPU.
        let gpuOnlyVerdict = TabletDeviceVerdict(preferred: .gpu, cpu: true, gpu: true, neuralEngine: false)
        let gpuUnits = LaneBox()
        let gpuGroup = DispatchGroup()
        for idx in 0..<2 {
            gpuGroup.enter()
            DispatchQueue.global().async {
                defer { gpuGroup.leave() }
                try? scheduler.run(photoID: "g\(idx)", stage: 0, model: .best, verdict: gpuOnlyVerdict, override: .both, isCancelled: { false }) { _, lane in
                    gpuUnits.append(lane)
                }
            }
        }
        XCTAssertEqual(gpuGroup.wait(timeout: .now() + 5), .success)
        XCTAssertEqual(gpuUnits.values, [.gpu, .gpu])
    }
}
#endif
