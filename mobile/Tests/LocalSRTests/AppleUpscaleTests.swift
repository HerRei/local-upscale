#if !SKIP
import XCTest
import Foundation
import ImageIO
import CoreGraphics
import CoreML
import LocalSRCore
@testable import LocalSR

/// macOS-host tests of the iOS implementation (Core ML, ImageIO and PhotoKit exist on macOS).
/// Ignored by the Kotlin test run because everything sits inside `#if !SKIP`.
final class AppleUpscaleTests: XCTestCase {
    private var directories: [URL] = []
    private func tempDirectory(_ name: String) -> URL {
        let url = Synthetic.tempDirectory(name)
        directories.append(url)
        return url
    }
    override func tearDown() {
        for url in directories { try? FileManager.default.removeItem(at: url) }
        directories = []
    }

    private func makeService() -> (AppleUpscaleService, UserDefaults, URL) {
        let suite = "localsr.tests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        addTeardownBlock { UserDefaults(suiteName: suite)?.removePersistentDomain(forName: suite) }
        let caches = tempDirectory("caches")
        return (AppleUpscaleService(defaults: defaults, cachesDirectory: caches), defaults, caches)
    }

    private func box4(_ rgba: [UInt8], width: Int, height: Int) -> [UInt8] {
        let w = width / 4, h = height / 4
        var out = [UInt8](repeating: 255, count: w * h * 4)
        rgba.withUnsafeBufferPointer { src in
            for y in 0..<h { for x in 0..<w {
                for c in 0..<3 {
                    var sum = 0
                    for dy in 0..<4 { for dx in 0..<4 { sum += Int(src[((y * 4 + dy) * width + x * 4 + dx) * 4 + c]) } }
                    out[(y * w + x) * 4 + c] = UInt8((sum + 8) / 16)
                }
            } }
        }
        return out
    }

    // MARK: Engine

    func testEngineMatchesCPU() throws {
        let probe = try AppleDeviceCheck.loadProbe()
        for model in [ModelKey.best, ModelKey.fast] {
            let cpu = CoreMLEngine()
            try cpu.load(model: model, unit: .cpu)
            let reference = try cpu.run(tile: probe.floats)
            XCTAssertEqual(reference.count, 3 * 1536 * 1536)
            XCTAssertFalse(PixelMath.hasNonFinite(reference))
            let down = PixelMath.psnr(PixelMath.downsample4x(reference, width: 1536, height: 1536), probe.floats)
            XCTAssertGreaterThanOrEqual(down, 26, "\(model) CPU output is not a plausible 4x of the probe")

            let npu = CoreMLEngine()
            try npu.load(model: model, unit: .npu)
            let out = try npu.run(tile: probe.floats)
            let psnr = PixelMath.psnr(out, reference)
            print("engine \(model.rawValue): NPU vs CPU PSNR \(psnr) dB, CPU 4x-downsample vs input \(down) dB")
            XCTAssertGreaterThanOrEqual(psnr, 45, "\(model) NPU output differs from the CPU reference")
        }
    }

    func testEngineTileTimes() throws {
        let probe = try AppleDeviceCheck.loadProbe()
        for model in [ModelKey.best, ModelKey.fast] {
            for unit in [ComputeUnit.npu, ComputeUnit.gpu] {
                let engine = CoreMLEngine()
                try engine.load(model: model, unit: unit)
                let run = try AppleDeviceCheck.measureRun(engine: engine, probe: probe, timedRuns: 10)
                print("tile time \(model.rawValue) \(unit.rawValue): \(String(format: "%.1f", run.tileMs)) ms/tile (median of 10, predict only)")
                XCTAssertGreaterThan(run.tileMs, 0)
            }
        }
    }

    func testMissingModelThrows() {
        XCTAssertThrowsError(try CoreMLEngine().run(tile: [Float](repeating: 0, count: 3 * 384 * 384)))
    }

    // MARK: Kernels vs core semantics

    func testKernelsMatchCore() throws {
        var rng = SystemRandomNumberGenerator()
        for scale in [2, 4] {
        for (w, h) in [(500, 300), (384, 384), (100, 90)] {
            var rgba = [UInt8](repeating: 0, count: w * h * 4)
            for i in 0..<rgba.count { rgba[i] = UInt8.random(in: 0...255, using: &rng) }
            let tiles = TilePlanner.plan(width: w, height: h, scale: scale)
            var coreOutput = [UInt8](repeating: 0, count: w * scale * h * scale * 4)
            var fastOutput = PixelBuffer(width: w * scale, height: h * scale)
            fastOutput.pointer.initialize(repeating: 0, count: fastOutput.byteCount)
            let half = UnsafeMutablePointer<Float16>.allocate(capacity: 3 * 384 * 384)
            defer { half.deallocate() }
            let out = UnsafeMutablePointer<Float16>.allocate(capacity: 3 * 1536 * 1536)
            defer { out.deallocate() }
            for tile in tiles {
                // Input window: same floats as the core (after the FP16 rounding the model input has anyway).
                let floats = PixelMath.extractWindow(input: rgba, width: w, height: h, tile: tile)
                rgba.withUnsafeBufferPointer { ApplePixelKernels.fillWindow(source: $0.baseAddress!, width: w, height: h, tile: tile, window: 384, dst: half) }
                for i in 0..<floats.count { XCTAssertEqual(half[i], Float16(floats[i])) }
                // Output: random FP16 including out-of-range and non-finite values.
                for i in 0..<(3 * 1536 * 1536) {
                    let r = Int.random(in: 0..<100, using: &rng)
                    out[i] = r == 0 ? .nan : r == 1 ? .infinity : r == 2 ? -.infinity : Float16.random(in: -0.2...1.3, using: &rng)
                }
                let asFloat = (0..<(3 * 1536 * 1536)).map { Float(out[$0]) }
                TileStitcher.stitch(tile: tile, tileOutput: asFloat, output: &coreOutput, outputWidth: w * scale, outputScale: scale)
                ApplePixelKernels.stitch(tile: tile, output: TileOutputView(base: UnsafePointer(out), channelStride: 1536 * 1536, rowStride: 1536, columnStride: 1), image: fastOutput.pointer, outputWidth: w * scale, outputScale: scale)
            }
            let fast = Array(UnsafeBufferPointer(start: fastOutput.pointer, count: fastOutput.byteCount))
            XCTAssertEqual(fast, coreOutput, "stitch differs for \(w)x\(h)")
            fastOutput = PixelBuffer(width: 1, height: 1)
        }
        }
    }

    // MARK: Device check

    func testDeviceCheckReturnsVerdictAndCaches() async throws {
        let suite = "localsr.tests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let check = AppleDeviceCheck(defaults: defaults)
        XCTAssertNil(check.cachedVerdict(model: .best))
        let report = try await check.verdict(for: .best)
        print("device check best: \(report.verdict.unit.rawValue) - \(report.verdict.reason)")
        print("  placement: \(report.placement)")
        for (name, m) in [("cpu", report.cpu), ("gpu", report.gpu), ("npu", report.npu)] {
            if let m { print("  \(name): load \(Int(m.loadMs)) ms, tile \(String(format: "%.1f", m.tileMs)) ms, PSNR vs CPU \(String(format: "%.1f", m.psnrVsCPU)), down vs input \(String(format: "%.1f", m.psnrDownsampleVsInput)), nan \(m.hasNaN), const \(m.isConstant), allNPU \(m.placementAllNPU)") }
        }
        XCTAssertFalse(report.fromCache)
        XCTAssertTrue(report.cached)
        #if targetEnvironment(simulator)
        // No Neural Engine in the simulator: the placement rejects it and the NPU is never loaded.
        XCTAssertEqual(report.npu?.placementAllNPU, false)
        XCTAssertNotEqual(report.verdict.unit, .npu)
        #else
        // The Mac's M1 Pro has an ANE that passes: the check must not end on the CPU here.
        XCTAssertNotEqual(report.verdict.unit, .cpu)
        XCTAssertEqual(report.npu?.placementAllNPU, true)
        #endif
        // Cached under DeviceCheck.cacheKey and returned without measuring.
        XCTAssertNotNil(defaults.data(forKey: AppleDeviceCheck.cacheKey(model: .best)))
        let second = try await check.verdict(for: .best)
        XCTAssertTrue(second.fromCache)
        XCTAssertEqual(second.verdict.unit, report.verdict.unit)
        // Another model has its own entry.
        XCTAssertNil(check.cachedVerdict(model: .fast))
    }

    func testDeviceCheckWatchdogFallsBackAndDoesNotCache() async throws {
        let suite = "localsr.tests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let check = AppleDeviceCheck(defaults: defaults, watchdogSeconds: 0.0001)
        let report = try await check.verdict(for: .fast)
        #if targetEnvironment(simulator)
        throw XCTSkip("no NPU load in the simulator, so there is nothing for the watchdog to time out")
        #endif
        print("watchdog check: \(report.verdict.unit.rawValue) - \(report.verdict.reason)")
        XCTAssertEqual(report.npu?.timedOut, true)
        XCTAssertNotEqual(report.verdict.unit, .npu)
        XCTAssertFalse(report.cached)
        XCTAssertNil(check.cachedVerdict(model: .fast), "a timed-out NPU load must be retried later, not cached")
    }

    func testCacheKeyUsesDeviceOSAndBuild() {
        let key = AppleDeviceCheck.cacheKey(model: .best)
        XCTAssertTrue(key.contains(AppleDeviceCheck.deviceModel()))
        XCTAssertTrue(key.hasSuffix("4:best"))
    }

    // MARK: Decode

    func testDecodeAppliesOrientationAndPrescale() throws {
        let dir = tempDirectory("decode")
        // 4600x2900 = 13.3 MP stored landscape with orientation 6 -> upright 2900x4600, prescaled to <= 12 MP.
        let url = try Synthetic.writePhoto(.init(width: 4600, height: 2900, orientation: 6, gps: false, displayP3: true, type: .jpeg), to: dir)
        let decoded = try ImageIOCodec.decode(url: url)
        XCTAssertEqual(decoded.orientation, 6)
        XCTAssertEqual(decoded.originalWidth, 2900)
        XCTAssertEqual(decoded.originalHeight, 4600)
        let target = SizePolicy.target(width: 2900, height: 4600)
        XCTAssertEqual(decoded.pixels.width, target.width)
        XCTAssertEqual(decoded.pixels.height, target.height)
        XCTAssertLessThanOrEqual(decoded.pixels.width * decoded.pixels.height, SizePolicy.maxInputPixels)
        XCTAssertLessThan(decoded.pixels.width, decoded.pixels.height)
        XCTAssertEqual(decoded.colorSpace.name as String?, CGColorSpace.displayP3 as String)
        // Marker (red square at the stored top-left) is at the upright top-right after a 90 degree CW turn.
        let px = decoded.pixels.pointer
        let x = decoded.pixels.width - 20, y = 20
        XCTAssertGreaterThan(Int(px[(y * decoded.pixels.width + x) * 4]), 200)
        XCTAssertLessThan(Int(px[(y * decoded.pixels.width + x) * 4 + 1]), 80)
    }

    // MARK: End to end

    private func checkMetadata(output: URL, expectedWidth: Int, expectedHeight: Int) throws {
        let props = Synthetic.properties(of: output)
        XCTAssertEqual((props[kCGImagePropertyPixelWidth as String] as? NSNumber)?.intValue, expectedWidth)
        XCTAssertEqual((props[kCGImagePropertyPixelHeight as String] as? NSNumber)?.intValue, expectedHeight)
        XCTAssertEqual((props[kCGImagePropertyOrientation as String] as? NSNumber)?.intValue ?? 1, 1)
        let tiff = props[kCGImagePropertyTIFFDictionary as String] as? [String: Any]
        XCTAssertEqual(tiff?[kCGImagePropertyTIFFModel as String] as? String, "Synthetic 1")
        XCTAssertEqual((tiff?[kCGImagePropertyTIFFOrientation as String] as? NSNumber)?.intValue ?? 1, 1)
        let exif = props[kCGImagePropertyExifDictionary as String] as? [String: Any]
        XCTAssertEqual(exif?[kCGImagePropertyExifDateTimeOriginal as String] as? String, "2026:11:14 10:30:00")
        XCTAssertEqual(exif?[kCGImagePropertyExifLensModel as String] as? String, "Synthetic lens")
        XCTAssertEqual((exif?[kCGImagePropertyExifPixelXDimension as String] as? NSNumber)?.intValue, expectedWidth)
        XCTAssertEqual((exif?[kCGImagePropertyExifPixelYDimension as String] as? NSNumber)?.intValue, expectedHeight)
        let gps = props[kCGImagePropertyGPSDictionary as String] as? [String: Any]
        XCTAssertEqual((gps?[kCGImagePropertyGPSLatitude as String] as? NSNumber)?.doubleValue ?? 0, Synthetic.gpsLatitude, accuracy: 1e-4)
        XCTAssertEqual((gps?[kCGImagePropertyGPSLongitude as String] as? NSNumber)?.doubleValue ?? 0, Synthetic.gpsLongitude, accuracy: 1e-4)
        XCTAssertEqual(gps?[kCGImagePropertyGPSLatitudeRef as String] as? String, "N")
        XCTAssertEqual(gps?[kCGImagePropertyGPSLongitudeRef as String] as? String, "E")
        // What saveToLibrary hands to Photos.
        let meta = PhotoLibrarySaver.readMetadata(from: output)
        let expectedDate = ISO8601DateFormatter().date(from: "2026-11-14T10:30:00+01:00")
        XCTAssertEqual(meta.creationDate, expectedDate)
        XCTAssertEqual(meta.location?.coordinate.latitude ?? 0, Synthetic.gpsLatitude, accuracy: 1e-4)
        XCTAssertEqual(meta.location?.coordinate.longitude ?? 0, Synthetic.gpsLongitude, accuracy: 1e-4)
        XCTAssertEqual(meta.location?.altitude ?? 0, Synthetic.gpsAltitude, accuracy: 0.5)
    }

    func testEndToEndDisplayP3Photo() async throws {
        let (service, _, caches) = makeService()
        let dir = tempDirectory("e2e")
        let input = try Synthetic.writePhoto(.init(), to: dir)
        let verdict = try await service.prepare(model: .best)
        print("e2e unit: \(verdict.unit.rawValue)")
        let values = LockedArray()
        let result = try await service.upscale(input: input, model: .best) { p in values.append(p) }

        XCTAssertEqual(result.width, 4000)
        XCTAssertEqual(result.height, 3000)
        XCTAssertEqual(result.model, .best)
        XCTAssertEqual(result.unit, verdict.unit)
        XCTAssertEqual(result.inputURL, input)
        XCTAssertTrue(result.outputURL.path.hasPrefix(caches.appendingPathComponent("LocalSR").path))
        XCTAssertEqual(result.outputURL.deletingLastPathComponent().lastPathComponent, result.id)
        XCTAssertEqual(result.outputURL.pathExtension, "heic")
        // Really an HEIC.
        let type = CGImageSourceGetType(CGImageSourceCreateWithURL(result.outputURL as CFURL, nil)!) as String?
        XCTAssertEqual(type, "public.heic")
        try checkMetadata(output: result.outputURL, expectedWidth: 4000, expectedHeight: 3000)

        // Colour space stays Display P3.
        let out = Synthetic.rgba(of: result.outputURL)
        XCTAssertEqual(out.width, 4000)
        XCTAssertEqual(out.colorSpaceName, CGColorSpace.displayP3 as String)
        // Content: the 4x box downsample of the output agrees with the input.
        let original = Synthetic.rgba(of: input)
        let psnr = Synthetic.psnr(box4(out.pixels, width: out.width, height: out.height), original.pixels)
        print("e2e best: downsample-vs-input PSNR \(psnr) dB, stats \(String(describing: service.lastJobStats))")
        XCTAssertGreaterThanOrEqual(psnr, 30)

        // Preview.
        let preview = Synthetic.properties(of: result.previewURL)
        XCTAssertLessThanOrEqual(max((preview[kCGImagePropertyPixelWidth as String] as! NSNumber).intValue, (preview[kCGImagePropertyPixelHeight as String] as! NSNumber).intValue), 4096)
        XCTAssertEqual(result.previewURL.pathExtension, "jpg")

        // Progress is monotonic and ends at 1.
        let seen = values.snapshot()
        XCTAssertEqual(seen.first, 0)
        XCTAssertEqual(seen.last, 1)
        XCTAssertEqual(seen, seen.sorted())
        XCTAssertEqual(seen.count, TilePlanner.plan(width: 1000, height: 750).count + 2)
    }

    func testLargePhotoScaleTwoMetadata() async throws {
        let (service, _, _) = makeService()
        let dir = tempDirectory("large-p3")
        let input = try Synthetic.writePhoto(.init(width: 4032, height: 3024, gps: true, type: .jpeg), to: dir)
        let result = try await service.upscale(input: input, model: .fast) { _ in }
        let target = SizePolicy.target(width: 4032, height: 3024)
        XCTAssertEqual(result.outputScale, 2)
        XCTAssertEqual(result.width, target.width * 2)
        XCTAssertEqual(result.height, target.height * 2)
        try checkMetadata(output: result.outputURL, expectedWidth: result.width, expectedHeight: result.height)
        let source = try XCTUnwrap(CGImageSourceCreateWithURL(result.outputURL as CFURL, nil))
        let image = try XCTUnwrap(CGImageSourceCreateImageAtIndex(source, 0, [kCGImageSourceShouldCache: false] as CFDictionary))
        XCTAssertEqual(image.colorSpace?.name as String?, CGColorSpace.displayP3 as String)
    }

    func testResultOutputScaleCodingAndLegacyDefault() throws {
        let url = URL(fileURLWithPath: "/tmp/example")
        let result = UpscaleResult(id: "legacy", inputURL: url, outputURL: url, previewURL: url, width: 8, height: 8, model: .fast, unit: .cpu, seconds: 1, outputScale: 2)
        let encoded = try JSONEncoder().encode(result)
        XCTAssertEqual(try JSONDecoder().decode(UpscaleResult.self, from: encoded).outputScale, 2)
        var object = try XCTUnwrap(JSONSerialization.jsonObject(with: encoded) as? [String: Any])
        object.removeValue(forKey: "outputScale")
        let legacy = try JSONSerialization.data(withJSONObject: object)
        XCTAssertEqual(try JSONDecoder().decode(UpscaleResult.self, from: legacy).outputScale, 4)
    }

    func testEndToEndFastModel() async throws {
        let (service, _, _) = makeService()
        let dir = tempDirectory("fast")
        let input = try Synthetic.writePhoto(.init(width: 600, height: 420, gps: true), to: dir)
        let result = try await service.upscale(input: input, model: .fast) { _ in }
        XCTAssertEqual(result.width, 2400)
        XCTAssertEqual(result.height, 1680)
        XCTAssertEqual(result.model, .fast)
        try checkMetadata(output: result.outputURL, expectedWidth: 2400, expectedHeight: 1680)
    }

    func testRotatedInputOrientation6() async throws {
        let (service, _, _) = makeService()
        let dir = tempDirectory("rot")
        // Stored 1000x750 with orientation 6: upright 750x1000 -> output 3000x4000, orientation 1.
        let input = try Synthetic.writePhoto(.init(width: 1000, height: 750, orientation: 6), to: dir)
        let result = try await service.upscale(input: input, model: .best) { _ in }
        XCTAssertEqual(result.width, 3000)
        XCTAssertEqual(result.height, 4000)
        try checkMetadata(output: result.outputURL, expectedWidth: 3000, expectedHeight: 4000)
        let out = Synthetic.rgba(of: result.outputURL)
        XCTAssertEqual(out.width, 3000)
        XCTAssertEqual(out.height, 4000)
        // The red marker (stored top-left) is upright top-right.
        let at = { (x: Int, y: Int) -> (Int, Int, Int) in let i = (y * out.width + x) * 4; return (Int(out.pixels[i]), Int(out.pixels[i + 1]), Int(out.pixels[i + 2])) }
        let marker = at(out.width - 40, 40)
        XCTAssertGreaterThan(marker.0, 200); XCTAssertLessThan(marker.1, 80)
        let other = at(40, 40)
        XCTAssertFalse(other.0 > 200 && other.1 < 80, "marker must not stay top-left")
        // Same content as the (upright-decoded) input.
        let original = Synthetic.rgba(of: input)
        XCTAssertEqual(original.width, 750)
        let psnr = Synthetic.psnr(box4(out.pixels, width: out.width, height: out.height), original.pixels)
        print("rot6: downsample-vs-input PSNR \(psnr) dB")
        XCTAssertGreaterThanOrEqual(psnr, 28)
    }

    private func gainMapPixel(_ g: GainMapSource, x: Int, y: Int) -> Int {
        g.data.withUnsafeBytes { (raw: UnsafeRawBufferPointer) -> Int in Int(raw.load(fromByteOffset: y * g.bytesPerRow + x, as: UInt8.self)) }
    }

    private func checkGainMapCarried(type: CFString, orientation: Int, width: Int = 1000, height: Int = 750, file: StaticString = #filePath, line: UInt = #line) async throws {
        let dir = tempDirectory("hdr")
        guard let input = Synthetic.writeGainMapPhoto(width: width, height: height, type: type, to: dir, orientation: orientation) else {
            throw XCTSkip("ImageIO cannot write a \(type) HEIC on this host")
        }
        let source = CGImageSourceCreateWithURL(input as CFURL, nil)!
        let gain = try XCTUnwrap(ImageIOCodec.readGainMap(source), "synthetic input lost its gain map")
        XCTAssertEqual(gain.type as String, type as String)
        let (service, _, _) = makeService()
        let result = try await service.upscale(input: input, model: .fast) { _ in }
        let scale = SizePolicy.target(width: width, height: height).outputScale
        let upright = orientation >= 5 ? (w: height * scale, h: width * scale) : (w: width * scale, h: height * scale)
        XCTAssertEqual(result.width, upright.w, file: file, line: line)
        XCTAssertEqual(result.height, upright.h, file: file, line: line)
        let outSource = CGImageSourceCreateWithURL(result.outputURL as CFURL, nil)!
        let outGain = try XCTUnwrap(ImageIOCodec.readGainMap(outSource), "output has no gain map (\(service.lastJobStats?.gainMapNote ?? "?"))", file: file, line: line)
        print("gain map \(type): in \(gain.width)x\(gain.height) rowBytes \(gain.bytesPerRow) -> out \(outGain.type) \(outGain.width)x\(outGain.height) rowBytes \(outGain.bytesPerRow); \(service.lastJobStats?.gainMapNote ?? "")")
        XCTAssertEqual(outGain.type as String, type as String, file: file, line: line)
        // Half the output size, upright.
        XCTAssertEqual(outGain.width, upright.w / 2, file: file, line: line)
        XCTAssertEqual(outGain.height, upright.h / 2, file: file, line: line)
        XCTAssertEqual(outGain.pixelFormat, GainMapSource.oneComponent8, file: file, line: line)
        // Tone-map / headroom metadata travelled with it.
        let tags = (CGImageMetadataCopyTags(try XCTUnwrap(outGain.metadata)) as? [CGImageMetadataTag] ?? []).compactMap { CGImageMetadataTagCopyName($0) as String? }
        XCTAssertTrue(tags.contains(where: { $0.contains("Headroom") || $0.contains("Version") }), "metadata tags: \(tags)", file: file, line: line)
        // The stored top-left bright block follows the base image's orientation.
        let nearTopLeft = gainMapPixel(outGain, x: 6, y: 6)
        let nearTopRight = gainMapPixel(outGain, x: outGain.width - 7, y: 6)
        let nearBottomLeft = gainMapPixel(outGain, x: 6, y: outGain.height - 7)
        switch orientation {
        case 6: XCTAssertGreaterThan(nearTopRight, 200, file: file, line: line); XCTAssertLessThan(nearTopLeft, 160, file: file, line: line)
        case 8: XCTAssertGreaterThan(nearBottomLeft, 200, file: file, line: line); XCTAssertLessThan(nearTopLeft, 160, file: file, line: line)
        default: XCTAssertGreaterThan(nearTopLeft, 200, file: file, line: line)
        }
        // The base image keeps EXIF/GPS and orientation 1 next to the gain map.
        try checkMetadata(output: result.outputURL, expectedWidth: upright.w, expectedHeight: upright.h)
    }

    func testLargeGainMapScaleTwo() async throws { try await checkGainMapCarried(type: kCGImageAuxiliaryDataTypeISOGainMap, orientation: 1, width: 2000, height: 1502) }
    func testISOGainMapKeptAndOriented() async throws { try await checkGainMapCarried(type: kCGImageAuxiliaryDataTypeISOGainMap, orientation: 1) }
    func testISOGainMapKeptWithOrientation6() async throws { try await checkGainMapCarried(type: kCGImageAuxiliaryDataTypeISOGainMap, orientation: 6) }
    func testAppleGainMapKept() async throws { try await checkGainMapCarried(type: kCGImageAuxiliaryDataTypeHDRGainMap, orientation: 1) }

    func testGainMapTransformOrientations() {
        // 3x2 gain map with distinct values: [[1,2,3],[4,5,6]].
        let values: [UInt8] = [1, 2, 3, 4, 5, 6]
        let gain = GainMapSource(type: kCGImageAuxiliaryDataTypeISOGainMap, data: Data(values), description: [kCGImagePropertyWidth as String: 3, kCGImagePropertyHeight as String: 2, kCGImagePropertyBytesPerRow as String: 3, "PixelFormat": GainMapSource.oneComponent8], metadata: nil, colorSpace: nil)
        // Orientation 6 (display = stored rotated 90 degrees clockwise): upright 2x3 = [[4,1],[5,2],[6,3]].
        let r6 = ImageIOCodec.transformGainMap(gain, orientation: 6, targetWidth: 2, targetHeight: 3)!
        XCTAssertEqual([UInt8](r6.data), [4, 1, 5, 2, 6, 3])
        XCTAssertEqual((r6.description[kCGImagePropertyWidth as String] as? Int), 2)
        let r3 = ImageIOCodec.transformGainMap(gain, orientation: 3, targetWidth: 3, targetHeight: 2)!
        XCTAssertEqual([UInt8](r3.data), [6, 5, 4, 3, 2, 1])
        let r8 = ImageIOCodec.transformGainMap(gain, orientation: 8, targetWidth: 2, targetHeight: 3)!
        XCTAssertEqual([UInt8](r8.data), [3, 6, 2, 5, 1, 4])
        let r2 = ImageIOCodec.transformGainMap(gain, orientation: 2, targetWidth: 3, targetHeight: 2)!
        XCTAssertEqual([UInt8](r2.data), [3, 2, 1, 6, 5, 4])
    }

    func testCancellationRemovesOutput() async throws {
        let (service, _, caches) = makeService()
        let dir = tempDirectory("cancel")
        let input = try Synthetic.writePhoto(.init(width: 1600, height: 1200, type: .jpeg), to: dir)
        _ = try await service.prepare(model: .best)
        let task = Task { Box(try await service.upscale(input: input, model: .best) { _ in }) }
        try await Task.sleep(nanoseconds: 150_000_000)
        task.cancel()
        do {
            _ = try await task.value
            XCTFail("expected cancellation")
        } catch {
            XCTAssertTrue(error is AppleUpscaleService.ServiceError, "\(error)")
        }
        let root = caches.appendingPathComponent("LocalSR")
        let left = (try? FileManager.default.contentsOfDirectory(atPath: root.path)) ?? []
        XCTAssertEqual(left, [])
        // The service still works afterwards.
        let again = try await service.upscale(input: input, model: .fast) { _ in }
        XCTAssertEqual(again.width, 6400)
    }

    func testUnreadableInputThrows() async throws {
        let (service, _, _) = makeService()
        let dir = tempDirectory("bad")
        let url = dir.appendingPathComponent("nope.heic")
        try Data("not an image".utf8).write(to: url)
        do {
            _ = try await service.upscale(input: url, model: .fast) { _ in }
            XCTFail("expected an error")
        } catch {}
    }

    /// Developer check with a real iPhone photo: LOCALSR_REAL_PHOTO=/path/IMG.HEIC (not part of the normal run).
    func testRealPhotoFromEnvironment() async throws {
        guard let path = ProcessInfo.processInfo.environment["LOCALSR_REAL_PHOTO"] else { throw XCTSkip("set LOCALSR_REAL_PHOTO") }
        let input = URL(fileURLWithPath: path)
        let (service, _, _) = makeService()
        let result = try await service.upscale(input: input, model: .fast) { _ in }
        let s = service.lastJobStats!
        print("REAL \(input.lastPathComponent): \(result.width)x\(result.height) on \(result.unit.rawValue) in \(String(format: "%.2f", result.seconds)) s; decode \(String(format: "%.2f", s.decodeSeconds)) tiles \(String(format: "%.2f", s.tilesSeconds)) encode \(String(format: "%.2f", s.encodeSeconds)); \(s.gainMapNote)")
        let inSource = CGImageSourceCreateWithURL(input as CFURL, nil)!
        let outSource = CGImageSourceCreateWithURL(result.outputURL as CFURL, nil)!
        for type in [kCGImageAuxiliaryDataTypeISOGainMap, kCGImageAuxiliaryDataTypeHDRGainMap] {
            let a = ImageIOCodec.readGainMap(inSource) .map { _ in ImageIOCodec.readGainMap(inSource) }
            _ = a
            for (label, src) in [("in", inSource), ("out", outSource)] {
                if let info = CGImageSourceCopyAuxiliaryDataInfoAtIndex(src, 0, type) as? [String: Any], let d = info["kCGImageAuxiliaryDataInfoDataDescription"] as? [String: Any] {
                    let tags = ((info["kCGImageAuxiliaryDataInfoMetadata"].map { $0 as! CGImageMetadata }).flatMap { CGImageMetadataCopyTags($0) as? [CGImageMetadataTag] } ?? []).compactMap { CGImageMetadataTagCopyName($0) as String? }
                    print("  \(label) \(type): \(d["Width"] ?? "?")x\(d["Height"] ?? "?") tags \(tags)")
                } else { print("  \(label) \(type): none") }
            }
        }
        let inProps = Synthetic.properties(of: input), outProps = Synthetic.properties(of: result.outputURL)
        print("  in keys \(inProps.keys.sorted()) out keys \(outProps.keys.sorted())")
        XCTAssertEqual((outProps[kCGImagePropertyOrientation as String] as? NSNumber)?.intValue ?? 1, 1)
        try? FileManager.default.removeItem(at: result.outputURL.deletingLastPathComponent())
    }

    // MARK: Performance (LOCALSR_PERF=1, release build recommended)

    func testPerformance12MP() async throws {
        guard ProcessInfo.processInfo.environment["LOCALSR_PERF"] == "1" else { throw XCTSkip("set LOCALSR_PERF=1") }
        let dir = tempDirectory("perf")
        let input = try Synthetic.writePhoto(.init(width: 4032, height: 3024, gps: true, type: .jpeg), to: dir)
        let (service, _, _) = makeService()
        for model in [ModelKey.best, ModelKey.fast] {
            let verdict = try await service.prepare(model: model)
            let t0 = Date()
            let result = try await service.upscale(input: input, model: model, fullScale: true) { _ in }
            let total = Date().timeIntervalSince(t0)
            let s = service.lastJobStats!
            let size = (try? FileManager.default.attributesOfItem(atPath: result.outputURL.path)[.size] as? Int) ?? 0
            print("PERF 12MP \(model.rawValue) on \(verdict.unit.rawValue): total \(String(format: "%.2f", total)) s (decode \(String(format: "%.2f", s.decodeSeconds)), \(s.tiles) tiles \(String(format: "%.2f", s.tilesSeconds)) s = \(String(format: "%.1f", s.tilesSeconds * 1000 / Double(s.tiles))) ms/tile, encode+preview \(String(format: "%.2f", s.encodeSeconds))), output \(result.width)x\(result.height), HEIC \(size / 1_000_000) MB")
            print("PERF full scale requested; actual scale \(result.outputScale)")
            XCTAssertEqual(result.width, SizePolicy.target(width: 4032, height: 3024).width * result.outputScale)
        }
    }
}

final class LockedArray: @unchecked Sendable {
    private let lock = NSLock()
    private var values: [Double] = []
    func append(_ v: Double) { lock.withLock { values.append(v) } }
    func snapshot() -> [Double] { lock.withLock { values } }
}
#endif
