#if !SKIP
import Foundation
import CoreML
import ImageIO
import CoreGraphics
import LocalSRCore

/// The device check from DESIGN.md "Device check", run once per model on the first `prepare`, cached in
/// UserDefaults under `DeviceCheck.cacheKey(device model, OS version, app build, model key)`.
///
/// Order (always sequential, one model load at a time): placement (MLComputePlan) -> CPU reference ->
/// GPU -> NPU with the 15 s load watchdog. The NPU goes last so that a load that exceeds the watchdog
/// (Core ML cannot cancel it) keeps compiling in the background while nothing else is loading.
public final class AppleDeviceCheck: @unchecked Sendable {
    public struct Report {
        public var verdict: DeviceVerdict
        public var cpu: DeviceMeasurement?
        public var gpu: DeviceMeasurement?
        public var npu: DeviceMeasurement?
        /// "ops with a device usage / ops preferring the Neural Engine", for the About screen and logs.
        public var placement: String
        public var fromCache: Bool
        public var cached: Bool
    }

    public static let loadWatchdogSeconds: Double = 15

    private let defaults: UserDefaults
    private let watchdogSeconds: Double

    public init(defaults: UserDefaults = .standard, watchdogSeconds: Double = AppleDeviceCheck.loadWatchdogSeconds) {
        self.defaults = defaults
        self.watchdogSeconds = watchdogSeconds
    }

    // MARK: Cache

    public static var hasNeuralEngine: Bool {
        #if targetEnvironment(simulator)
        return false
        #else
        return MLModel.availableComputeDevices.contains { device in
            if case .neuralEngine = device { return true }
            return false
        }
        #endif
    }

    public static func deviceModel() -> String {
        var name = "unknown"
        #if os(macOS)
        name = sysctlString("hw.model") ?? sysctlString("hw.machine") ?? name
        #else
        name = sysctlString("hw.machine") ?? name
        if let sim = ProcessInfo.processInfo.environment["SIMULATOR_MODEL_IDENTIFIER"] { name = "simulator-" + sim }
        #endif
        return name
    }

    private static func sysctlString(_ key: String) -> String? {
        var size = 0
        guard sysctlbyname(key, nil, &size, nil, 0) == 0, size > 0 else { return nil }
        var buffer = [CChar](repeating: 0, count: size)
        guard sysctlbyname(key, &buffer, &size, nil, 0) == 0 else { return nil }
        return String(decoding: buffer.prefix(while: { $0 != 0 }).map { UInt8(bitPattern: $0) }, as: UTF8.self)
    }

    public static func cacheKey(model: ModelKey) -> String {
        let os = ProcessInfo.processInfo.operatingSystemVersion
        let osVersion = "\(os.majorVersion).\(os.minorVersion).\(os.patchVersion)"
        let build = (Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String) ?? "dev"
        return "LocalSR.deviceCheck." + DeviceCheck.cacheKey(deviceModel: deviceModel(), osVersion: osVersion, appBuild: build, modelKey: model)
    }

    public func cachedVerdict(model: ModelKey) -> DeviceVerdict? {
        guard let data = defaults.data(forKey: Self.cacheKey(model: model)) else { return nil }
        guard let verdict = try? JSONDecoder().decode(DeviceVerdict.self, from: data), !verdict.reason.contains("its output did not pass validation") else { return nil }
        return verdict
    }

    public func clearCache(model: ModelKey) {
        defaults.removeObject(forKey: Self.cacheKey(model: model))
    }

    private func store(_ verdict: DeviceVerdict, model: ModelKey) {
        if let data = try? JSONEncoder().encode(verdict) { defaults.set(data, forKey: Self.cacheKey(model: model)) }
    }

    // MARK: Check

    /// Returns the cached verdict, or runs the check (and caches the result unless the NPU load timed out).
    public func verdict(for model: ModelKey, force: Bool = false) async throws -> Report {
        if !force, let cached = cachedVerdict(model: model) {
            return Report(verdict: cached, cpu: nil, gpu: nil, npu: nil, placement: "cached", fromCache: true, cached: true)
        }
        var report = try await measure(model: model)
        // A watchdog timeout is "use the GPU for this session and retry later": do not cache it.
        let timedOut = report.npu?.timedOut ?? false
        if !timedOut { store(report.verdict, model: model) }
        report.cached = !timedOut
        return report
    }

    /// Runs all stages without touching the cache.
    public func measure(model: ModelKey) async throws -> Report {
        let probe = try Self.loadProbe()
        let url = try AppleResources.modelURL(model)

        // 1. Placement.
        let placement = await Self.placement(url: url)

        // 2. CPU reference.
        let cpuEngine = CoreMLEngine()
        let cpuStart = Date()
        try cpuEngine.load(model: model, unit: .cpu)
        let cpuLoad = Date().timeIntervalSince(cpuStart) * 1000
        let cpuRun = try Self.measureRun(engine: cpuEngine, probe: probe, timedRuns: 0)
        let reference = cpuRun.output
        let cpu = Self.measurement(loadMs: cpuLoad, run: cpuRun, reference: reference, probe: probe)
        cpuEngine.unload()

        // 3. GPU.
        var gpu: DeviceMeasurement?
        do {
            let engine = CoreMLEngine()
            let start = Date()
            try engine.load(model: model, unit: .gpu)
            let loadMs = Date().timeIntervalSince(start) * 1000
            let run = try Self.measureRun(engine: engine, probe: probe, timedRuns: 3)
            gpu = Self.measurement(loadMs: loadMs, run: run, reference: reference, probe: probe)
            engine.unload()
        } catch {
            gpu = nil
        }

        // 4. NPU: only if every op with a device usage prefers the Neural Engine.
        var npu: DeviceMeasurement?
        if !placement.allNeuralEngine {
            npu = DeviceMeasurement(loadMs: 0, tileMs: 0, psnrVsCPU: 0, psnrDownsampleVsInput: 0, placementAllNPU: false)
        } else {
            let engine = CoreMLEngine()
            let start = Date()
            let key = Box(model)
            let loadTask = Task { try await engine.loadAsync(model: key.value, unit: .npu) }
            let outcome = await Self.race(loadTask, timeoutSeconds: watchdogSeconds)
            let loadMs = Date().timeIntervalSince(start) * 1000
            switch outcome {
            case .timedOut:
                npu = DeviceMeasurement(loadMs: loadMs, tileMs: 0, psnrVsCPU: 0, psnrDownsampleVsInput: 0, placementAllNPU: true, timedOut: true)
            case .failed:
                npu = DeviceMeasurement(loadMs: loadMs, tileMs: 0, psnrVsCPU: 0, psnrDownsampleVsInput: 0, placementAllNPU: true)
            case .loaded:
                if let run = try? Self.measureRun(engine: engine, probe: probe, timedRuns: 3) {
                    npu = Self.measurement(loadMs: loadMs, run: run, reference: reference, probe: probe, placementAllNPU: true)
                } else {
                    npu = DeviceMeasurement(loadMs: loadMs, tileMs: 0, psnrVsCPU: 0, psnrDownsampleVsInput: 0, placementAllNPU: true)
                }
                engine.unload()
            }
        }

        guard let verdict = DeviceCheck.decide(cpu: cpu, gpu: gpu, npu: npu) else {
            throw NSError(domain: "LocalSR", code: 1, userInfo: [NSLocalizedDescriptionKey: "This device produced invalid output; upscaling is disabled."])
        }
        return Report(verdict: verdict, cpu: cpu, gpu: gpu, npu: npu, placement: placement.summary, fromCache: false, cached: false)
    }

    // MARK: Pieces

    struct Probe {
        let floats: [Float]      // planar 3 x 384 x 384, [0, 1]
    }

    static func loadProbe() throws -> Probe {
        let url = try AppleResources.probeURL()
        guard let source = CGImageSourceCreateWithURL(url as CFURL, nil), let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
            throw AppleResources.ResourceError.missing("probe.png (unreadable)")
        }
        let w = CoreMLEngine.window
        guard image.width == w, image.height == w else { throw AppleResources.ResourceError.missing("probe.png (not \(w)x\(w))") }
        var rgba = [UInt8](repeating: 0, count: w * w * 4)
        let ok = rgba.withUnsafeMutableBytes { raw -> Bool in
            guard let ctx = CGContext(data: raw.baseAddress, width: w, height: w, bitsPerComponent: 8, bytesPerRow: w * 4, space: CGColorSpace(name: CGColorSpace.sRGB)!, bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { return false }
            ctx.draw(image, in: CGRect(x: 0, y: 0, width: w, height: w))
            return true
        }
        guard ok else { throw AppleResources.ResourceError.missing("probe.png (cannot draw)") }
        // One 384 x 384 image has exactly one tile, with window (0, 0) and no padding.
        let tile = TilePlanner.plan(width: w, height: w)[0]
        return Probe(floats: PixelMath.extractWindow(input: rgba, width: w, height: w, tile: tile, window: w))
    }

    struct Run {
        let output: [Float]
        let tileMs: Double
    }

    /// One prediction that produces the output (time taken as the tile time when `timedRuns == 0`),
    /// then `timedRuns` more predictions of which the median is the tile time.
    static func measureRun(engine: CoreMLEngine, probe: Probe, timedRuns: Int) throws -> Run {
        let half = probe.floats.map { Float16($0) }
        func predictOnce() throws -> (TileOutputView, Double) {
            let t0 = DispatchTime.now().uptimeNanoseconds
            let view = try engine.predict { dst in
                half.withUnsafeBufferPointer { dst.update(from: $0.baseAddress!, count: $0.count) }
            }
            return (view, Double(DispatchTime.now().uptimeNanoseconds - t0) / 1e6)
        }
        let (view, first) = try predictOnce()
        let output = engine.toFloats(view)
        if timedRuns == 0 { return Run(output: output, tileMs: first) }
        var times: [Double] = []
        for _ in 0..<timedRuns { times.append(try predictOnce().1) }
        times.sort()
        return Run(output: output, tileMs: times[times.count / 2])
    }

    static func measurement(loadMs: Double, run: Run, reference: [Float], probe: Probe, placementAllNPU: Bool = true) -> DeviceMeasurement {
        let hasNaN = PixelMath.hasNonFinite(run.output)
        let psnrVsCPU = hasNaN ? 0 : PixelMath.psnr(run.output, reference)
        var down = 0.0
        if !hasNaN {
            let o = CoreMLEngine.outputSize
            down = PixelMath.psnr(PixelMath.downsample4x(run.output, width: o, height: o), probe.floats)
        }
        return DeviceMeasurement(loadMs: loadMs, tileMs: run.tileMs, psnrVsCPU: psnrVsCPU, psnrDownsampleVsInput: down, hasNaN: hasNaN, isConstant: PixelMath.isConstant(run.output), placementAllNPU: placementAllNPU, timedOut: false)
    }

    // MARK: Placement

    struct Placement {
        var allNeuralEngine: Bool
        var summary: String
    }

    /// `MLComputePlan` with `.cpuAndNeuralEngine`: the NPU is a candidate only if every operation that has a
    /// device usage prefers the Neural Engine.
    static func placement(url: URL) async -> Placement {
        do {
            let plan = try await MLComputePlan.load(contentsOf: url, configuration: CoreMLEngine.configuration(for: .npu))
            guard case .program(let program) = plan.modelStructure else {
                return Placement(allNeuralEngine: false, summary: "not an ML program")
            }
            var withUsage = 0, onNPU = 0
            var kinds: [String: Int] = [:]
            func visit(_ block: MLModelStructure.Program.Block) {
                for op in block.operations {
                    if let usage = plan.deviceUsage(for: op) {
                        withUsage += 1
                        switch usage.preferred {
                        case .neuralEngine: onNPU += 1; kinds["npu", default: 0] += 1
                        case .gpu: kinds["gpu", default: 0] += 1
                        case .cpu: kinds["cpu", default: 0] += 1
                        @unknown default: kinds["other", default: 0] += 1
                        }
                    }
                    for inner in op.blocks { visit(inner) }
                }
            }
            for (_, function) in program.functions { visit(function.block) }
            let all = withUsage > 0 && onNPU == withUsage
            return Placement(allNeuralEngine: all, summary: "\(onNPU)/\(withUsage) ops prefer the Neural Engine \(kinds)")
        } catch {
            return Placement(allNeuralEngine: false, summary: "compute plan failed: \(error)")
        }
    }

    // MARK: Watchdog

    enum RaceOutcome { case loaded, failed, timedOut }

    /// Waits for `task` for at most `timeoutSeconds`. The task is not cancelled on timeout (Core ML loads
    /// cannot be cancelled and finish in the background).
    static func race(_ task: Task<Void, Error>, timeoutSeconds: Double) async -> RaceOutcome {
        await withCheckedContinuation { (continuation: CheckedContinuation<RaceOutcome, Never>) in
            let once = OnceFlag()
            Task {
                let result = await task.result
                if once.claim() { continuation.resume(returning: { if case .success = result { return .loaded } else { return .failed } }()) }
            }
            Task {
                try? await Task.sleep(nanoseconds: UInt64(timeoutSeconds * 1e9))
                if once.claim() { continuation.resume(returning: .timedOut) }
            }
        }
    }
}

final class OnceFlag: @unchecked Sendable {
    private let lock = NSLock()
    private var taken = false
    func claim() -> Bool {
        lock.lock(); defer { lock.unlock() }
        if taken { return false }
        taken = true
        return true
    }
}
#endif
