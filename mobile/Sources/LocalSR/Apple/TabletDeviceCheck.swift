#if !SKIP
import Foundation
import LocalSRCore

/// Unit-specific results are retained because a lane override can select a unit other than the winner.
public struct TabletDeviceVerdict: Codable, Sendable {
    public let preferred: ComputeUnit
    public let cpu: Bool
    public let gpu: Bool
    public let neuralEngine: Bool

    public func supports(_ unit: ComputeUnit) -> Bool {
        switch unit { case .cpu: return cpu; case .gpu: return gpu; case .npu: return neuralEngine }
    }
}

/// Checks tablet artifacts using the phone's placement, watchdog and image-quality rules.
public final class TabletDeviceCheck: @unchecked Sendable {
    private let defaults: UserDefaults
    private let watchdogSeconds: Double
    public init(defaults: UserDefaults = .standard, watchdogSeconds: Double = AppleDeviceCheck.loadWatchdogSeconds) {
        self.defaults = defaults
        self.watchdogSeconds = watchdogSeconds
    }

    private func key(_ model: TabletModelID) -> String {
        let os = ProcessInfo.processInfo.operatingSystemVersion
        let version = "\(os.majorVersion).\(os.minorVersion).\(os.patchVersion)"
        let build = (Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String) ?? "dev"
        let device = AppleDeviceCheck.deviceModel()
        return "LocalSR.tabletCheck.v1.\(device.count):\(device)\(version.count):\(version)\(build.count):\(build)\(model.rawValue.count):\(model.rawValue)"
    }

    private func failedKey(_ model: TabletModelID) -> String {
        key(model) + ".failed"
    }

    public func cachedVerdict(for model: TabletModelID) -> TabletDeviceVerdict? {
        guard let data = defaults.data(forKey: key(model)),
              let result = try? JSONDecoder().decode(TabletDeviceVerdict.self, from: data) else {
            return nil
        }
        return result
    }

    public func hasFailedCheck(for model: TabletModelID) -> Bool {
        defaults.bool(forKey: failedKey(model))
    }

    public func verdict(for model: TabletModelID, force: Bool = false) async throws -> TabletDeviceVerdict {
        let cacheKey = key(model)
        if !force, let data = defaults.data(forKey: cacheKey), let result = try? JSONDecoder().decode(TabletDeviceVerdict.self, from: data) { return result }
        let descriptor = ModelCatalog.model(model)
        let fullProbe = try AppleDeviceCheck.loadProbe().floats
        let w = descriptor.window
        let full = CoreMLEngine.window
        var floats = [Float](repeating: 0, count: 3 * w * w)
        let offset = (full - w) / 2
        for c in 0..<3 { for y in 0..<w { for x in 0..<w {
            floats[c*w*w+y*w+x] = fullProbe[c*full*full+(y+offset)*full+x+offset]
        } } }
        let probe = AppleDeviceCheck.Probe(floats: floats)
        let placement = await AppleDeviceCheck.placement(url: try AppleResources.tabletModelURL(model))
        let cpuEngine = CoreMLEngine(window: w, modelScale: descriptor.scale)
        let start = Date()
        try cpuEngine.load(tabletModel: model, unit: .cpu)
        let cpuLoad = Date().timeIntervalSince(start) * 1000
        let cpuRun = try AppleDeviceCheck.measureRun(engine: cpuEngine, probe: probe, timedRuns: 0)
        cpuEngine.unload()
        func measure(_ run: AppleDeviceCheck.Run, load: Double) -> DeviceMeasurement {
            let bad = PixelMath.hasNonFinite(run.output)
            let image = descriptor.scale == 4 && !bad ? PixelMath.downsample4x(run.output, width: w*4, height: w*4) : run.output
            return DeviceMeasurement(loadMs: load, tileMs: run.tileMs,
                psnrVsCPU: bad ? 0 : PixelMath.psnr(run.output, cpuRun.output),
                psnrDownsampleVsInput: bad ? 0 : PixelMath.psnr(image, floats),
                hasNaN: bad, isConstant: PixelMath.isConstant(run.output))
        }
        let cpu = measure(cpuRun, load: cpuLoad)
        var gpu: DeviceMeasurement?
        do {
            let engine = CoreMLEngine(window: w, modelScale: descriptor.scale)
            let t = Date()
            try engine.load(tabletModel: model, unit: .gpu)
            let load = Date().timeIntervalSince(t) * 1000
            gpu = measure(try AppleDeviceCheck.measureRun(engine: engine, probe: probe, timedRuns: 3), load: load)
        } catch { gpu = nil }
        var npu: DeviceMeasurement?
        var timedOut = false
        if placement.allNeuralEngine {
            let engine = CoreMLEngine(window: w, modelScale: descriptor.scale)
            let t = Date()
            let task = Task { try await engine.loadAsync(tabletModel: model, unit: .npu) }
            let outcome = await AppleDeviceCheck.race(task, timeoutSeconds: watchdogSeconds)
            let load = Date().timeIntervalSince(t) * 1000
            switch outcome {
            case .loaded:
                if let run = try? AppleDeviceCheck.measureRun(engine: engine, probe: probe, timedRuns: 3) { npu = measure(run, load: load) }
            case .failed: break
            case .timedOut: timedOut = true
            }
        }
        guard let chosen = DeviceCheck.decide(cpu: cpu, gpu: gpu, npu: npu) else {
            if !timedOut {
                defaults.set(true, forKey: failedKey(model))
            }
            throw NSError(domain: "LocalSR", code: 1, userInfo: [NSLocalizedDescriptionKey: "This model failed the device check."])
        }
        defaults.removeObject(forKey: failedKey(model))
        let result = TabletDeviceVerdict(preferred: chosen.unit, cpu: DeviceCheck.passes(cpu), gpu: DeviceCheck.passes(gpu), neuralEngine: DeviceCheck.passes(npu))
        if !timedOut, let data = try? JSONEncoder().encode(result) { defaults.set(data, forKey: cacheKey) }
        return result
    }
}
#endif
