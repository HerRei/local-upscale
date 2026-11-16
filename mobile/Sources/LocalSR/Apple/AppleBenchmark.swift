#if !SKIP
import Foundation
import CoreML
import CoreGraphics
import ImageIO
import UniformTypeIdentifiers
import LocalSRCore
import Darwin
import os

enum AppleBenchmark {
    private static let launch: Void = {
        guard ProcessInfo.processInfo.environment["LOCALSR_BENCH"] == "1" else { return }
        Task.detached(priority: .utility) { await run() }
    }()
    static func startIfRequested() { _ = launch }

    private static func run() async {
        let root = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0].appendingPathComponent("LocalSR")
        try? FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        let logURL = root.appendingPathComponent("bench.txt")
        FileManager.default.createFile(atPath: logURL.path, contents: nil)
        let handle = try? FileHandle(forWritingTo: logURL)
        defer { try? handle?.close() }
        func log(_ message: String) {
            let data = Data("BENCH \(message)\n".utf8)
            FileHandle.standardError.write(data)
            try? handle?.write(contentsOf: data)
        }
        let marker = root.appendingPathComponent("bench-in-progress.txt")
        if let previous = try? String(contentsOf: marker, encoding: .utf8) {
            log("previous run was killed during \(previous)")
            try? FileManager.default.removeItem(at: marker)
        }
        let forceFull = ProcessInfo.processInfo.environment["LOCALSR_BENCH_FORCE_FULL"] == "1"
        var machine = utsname(); uname(&machine)
        let name = withUnsafePointer(to: &machine.machine) { $0.withMemoryRebound(to: CChar.self, capacity: 256) { String(cString: $0) } }
        #if os(iOS)
        let available = UInt64(os_proc_available_memory())
        #else
        let available: UInt64 = 0
        #endif
        log("device=\(name) os=\(ProcessInfo.processInfo.operatingSystemVersionString) available_bytes=\(available)")
        let dir = root.appendingPathComponent("benchmark-\(UUID().uuidString)")
        do {
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            defer { try? FileManager.default.removeItem(at: dir) }
            for model in [ModelKey.best, .fast] {
                log("model=\(model.rawValue) begin")
                AppleDeviceCheck().clearCache(model: model)
                let service = AppleUpscaleService()
                do {
                    let verdict = try await service.prepare(model: model)
                    log("model=\(model.rawValue) verdict=\(verdict.unit.rawValue) reason=\(verdict.reason)")
                    if let report = service.report(for: model) {
                        log("model=\(model.rawValue) check_placement=\(report.placement)")
                        for (unit, m) in [("cpu", report.cpu), ("gpu", report.gpu), ("ane", report.npu)] {
                            if let m, !m.placementAllNPU { log("model=\(model.rawValue) check_unit=\(unit) load=not_attempted placement_rejected=true") }
                            else if let m { log("model=\(model.rawValue) check_unit=\(unit) cold_load_ms=\(m.loadMs) tile_ms=\(m.tileMs) psnr_cpu=\(m.psnrVsCPU) psnr_input=\(m.psnrDownsampleVsInput) timeout=\(m.timedOut)") }
                            else { log("model=\(model.rawValue) check_unit=\(unit) unavailable") }
                        }
                    }
                    await placement(model, log: log)
                    try measurements(model, log: log)
                    for (w, h, full) in [(1500, 1000, false), (4032, 3024, false), (4032, 3024, true), (8064, 6048, false), (8064, 6048, true)] {
                        let caseName = "model=\(model.rawValue) e2e=\(w)x\(h) fullScale=\(full)"
                        do {
                            try Data(caseName.utf8).write(to: marker, options: .atomic)
                            #if os(iOS)
                            let caseAvailable = UInt64(os_proc_available_memory())
                            #else
                            let caseAvailable: UInt64 = 0
                            #endif
                            log("\(caseName) begin available_bytes=\(caseAvailable) force_full=\(forceFull && full)")
                            let input = try makeInput(width: w, height: h, directory: dir)
                            let sampler = FootprintSampler(); sampler.start()
                            do {
                                let result = try await service.upscaleForBenchmark(input: input, model: model, fullScale: full, forceFull: forceFull && full, gate: { decision in
                                    log("\(caseName) gate_allowed=\(decision.allowed) required_bytes=\(decision.required) available_bytes=\(decision.available) selected_scale=\(decision.scale)")
                                }) { _ in }
                                let peak = sampler.stop(), stats = service.lastJobStats!
                                log("\(caseName) unit=\(result.unit.rawValue) total_s=\(result.seconds) tiles_s=\(stats.tilesSeconds) encode_s=\(stats.encodeSeconds) peak_bytes=\(peak) output=\(result.width)x\(result.height)")
                                try? FileManager.default.removeItem(at: result.outputURL.deletingLastPathComponent())
                            } catch { let peak = sampler.stop(); log("\(caseName) peak_bytes=\(peak) ERROR=\(error)") }
                            try? FileManager.default.removeItem(at: input)
                        } catch { log("\(caseName) ERROR=\(error)") }
                        try? FileManager.default.removeItem(at: marker)
                    }
                } catch { log("model=\(model.rawValue) ERROR=\(error)") }
            }
        } catch { log("ERROR=\(error)") }
        log("DONE")
    }

    private static func placement(_ model: ModelKey, log: (String) -> Void) async {
        do {
            let plan = try await MLComputePlan.load(contentsOf: AppleResources.modelURL(model), configuration: CoreMLEngine.configuration(for: .npu))
            guard case .program(let program) = plan.modelStructure else { log("placement ERROR=not_program"); return }
            var counts: [String: Int] = [:], costs: [String: Double] = [:], off: [String: Int] = [:]
            var missingCost = 0
            func visit(_ block: MLModelStructure.Program.Block) {
                for op in block.operations {
                    if let usage = plan.deviceUsage(for: op) {
                        let device: String
                        switch usage.preferred { case .neuralEngine: device = "ane"; case .gpu: device = "gpu"; case .cpu: device = "cpu"; @unknown default: device = "other" }
                        counts[device, default: 0] += 1
                        if let cost = plan.estimatedCost(of: op) { costs[device, default: 0] += cost.weight } else { missingCost += 1 }
                        if device != "ane" { off["\(device):\(op.operatorName)", default: 0] += 1 }
                    }
                    for inner in op.blocks { visit(inner) }
                }
            }
            for function in program.functions.values { visit(function.block) }
            let total = costs.values.reduce(0, +)
            let shares = costs.mapValues { total > 0 ? $0 / total : 0 }
            log("model=\(model.rawValue) placement_counts=\(counts) cost_shares=\(shares) missing_cost=\(missingCost) off_ane_ops=\(off)")
        } catch { log("model=\(model.rawValue) placement ERROR=\(error)") }
    }

    private static func measurements(_ model: ModelKey, log: (String) -> Void) throws {
        let probe = try AppleDeviceCheck.loadProbe()
        var reference: [Float] = []
        for unit in [ComputeUnit.cpu, .npu, .gpu] {
            let start = Date()
            do {
                let engine = CoreMLEngine()
                try engine.load(model: model, unit: unit)
                let load = Date().timeIntervalSince(start) * 1000
                let half = probe.floats.map(Float16.init)
                func predict() throws -> TileOutputView { try engine.predict { dst in half.withUnsafeBufferPointer { dst.update(from: $0.baseAddress!, count: $0.count) } } }
                _ = try predict(); _ = try predict()
                var times: [Double] = [], output: [Float] = []
                for i in 0..<10 {
                    let t = DispatchTime.now().uptimeNanoseconds
                    let view = try predict()
                    times.append(Double(DispatchTime.now().uptimeNanoseconds - t) / 1e6)
                    if i == 0 { output = engine.toFloats(view) }
                }
                times.sort()
                if unit == .cpu { reference = output }
                let psnr = reference.count == output.count ? String(PixelMath.psnr(output, reference)) : "unavailable"
                let inputPSNR = PixelMath.psnr(PixelMath.downsample4x(output, width: CoreMLEngine.outputSize, height: CoreMLEngine.outputSize), probe.floats)
                log("model=\(model.rawValue) unit=\(unit.rawValue) load_ms=\(load) median_ms=\((times[4] + times[5]) / 2) psnr_cpu=\(psnr) psnr_input=\(inputPSNR) nonfinite=\(PixelMath.hasNonFinite(output)) constant=\(PixelMath.isConstant(output))")
                engine.unload()
            } catch { log("model=\(model.rawValue) unit=\(unit.rawValue) elapsed_ms=\(Date().timeIntervalSince(start) * 1000) ERROR=\(error)") }
        }
    }

    private static func makeInput(width: Int, height: Int, directory: URL) throws -> URL {
        let pixels = PixelBuffer(width: width, height: height)
        for y in 0..<height { for x in 0..<width {
            let i = (y * width + x) * 4
            let noise = ((x &* 73 &+ y &* 151 &+ (x ^ y) &* 19) & 31) - 16
            let checker = ((x / 3 + y / 3) & 1) * 20 - 10
            pixels.pointer[i] = UInt8(clamping: x * 220 / width + noise + checker)
            pixels.pointer[i + 1] = UInt8(clamping: y * 220 / height + noise - checker)
            pixels.pointer[i + 2] = UInt8(clamping: (x + y) * 200 / (width + height) + noise + checker)
            pixels.pointer[i + 3] = 255
        } }
        let url = directory.appendingPathComponent("input-\(width).png")
        guard let image = pixels.makeCGImage(colorSpace: CGColorSpace(name: CGColorSpace.displayP3)!), let dest = CGImageDestinationCreateWithURL(url as CFURL, UTType.png.identifier as CFString, 1, nil) else { throw ImageIOCodec.CodecError.encodeFailed("benchmark input") }
        CGImageDestinationAddImage(dest, image, nil)
        guard CGImageDestinationFinalize(dest) else { throw ImageIOCodec.CodecError.encodeFailed("benchmark input finalize") }
        return url
    }
}

private final class FootprintSampler: @unchecked Sendable {
    private let lock = NSLock()
    private var peak: UInt64 = 0
    private var timer: DispatchSourceTimer?
    private func sample() {
        var info = task_vm_info_data_t()
        var count = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size / MemoryLayout<natural_t>.size)
        let result = withUnsafeMutablePointer(to: &info) { $0.withMemoryRebound(to: integer_t.self, capacity: Int(count)) { task_info(mach_task_self_, task_flavor_t(TASK_VM_INFO), $0, &count) } }
        if result == KERN_SUCCESS { lock.withLock { peak = max(peak, info.phys_footprint) } }
    }
    func start() {
        sample()
        let t = DispatchSource.makeTimerSource(queue: DispatchQueue.global(qos: .utility))
        t.schedule(deadline: .now(), repeating: .milliseconds(100))
        t.setEventHandler { [weak self] in self?.sample() }
        timer = t; t.resume()
    }
    func stop() -> UInt64 { timer?.cancel(); timer = nil; sample(); return lock.withLock { peak } }
}
#endif
