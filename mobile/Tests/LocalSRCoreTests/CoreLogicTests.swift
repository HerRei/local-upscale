import XCTest
@testable import LocalSRCore

// These tests run twice: as Swift and as transpiled Kotlin (Skip). Literals are written with an
// explicit type (Float(0), UInt8(...), 1.0) because Skip does not convert an Int literal to
// Float/Double/UInt8 inside generic collections and some call sites.

final class CoreLogicTests: XCTestCase {
    private func bytes(_ values: [Int]) -> [UInt8] {
        var result: [UInt8] = []
        for v in values { result.append(UInt8(v)) }
        return result
    }

    private func meas(loadMs: Double = 100.0, tileMs: Double = 13.0, psnrVsCPU: Double = 50.0, psnrDown: Double = 30.0, hasNaN: Bool = false, isConstant: Bool = false, placementAllNPU: Bool = true, timedOut: Bool = false) -> DeviceMeasurement {
        return DeviceMeasurement(loadMs: loadMs, tileMs: tileMs, psnrVsCPU: psnrVsCPU, psnrDownsampleVsInput: psnrDown, hasNaN: hasNaN, isConstant: isConstant, placementAllNPU: placementAllNPU, timedOut: timedOut)
    }

    // MARK: Tiling

    func testTiling() {
        let sizes: [[Int]] = [[100, 100], [384, 384], [385, 700], [1000, 1000], [4032, 3024]]
        for size in sizes {
            let w = size[0]
            let h = size[1]
            let tiles = TilePlanner.plan(width: w, height: h)
            var area = 0
            for tile in tiles {
                // Edge windows are shifted inside the image (padding only when the image is smaller than the window).
                XCTAssertGreaterThanOrEqual(tile.windowX, 0)
                XCTAssertGreaterThanOrEqual(tile.windowY, 0)
                XCTAssertLessThanOrEqual(tile.windowX + min(w, 384), w)
                XCTAssertLessThanOrEqual(tile.windowY + min(h, 384), h)
                XCTAssertEqual(tile.padRight, max(0, 384 - w))
                XCTAssertEqual(tile.padBottom, max(0, 384 - h))
                // The core region lies inside the 1536x1536 model output and inside the output image.
                XCTAssertGreaterThan(tile.outputWidth, 0)
                XCTAssertGreaterThan(tile.outputHeight, 0)
                XCTAssertLessThanOrEqual(tile.sourceX + tile.outputWidth, 1536)
                XCTAssertLessThanOrEqual(tile.sourceY + tile.outputHeight, 1536)
                XCTAssertLessThanOrEqual(tile.outputX + tile.outputWidth, w * 4)
                XCTAssertLessThanOrEqual(tile.outputY + tile.outputHeight, h * 4)
                // The copied region maps back to the same source pixels.
                XCTAssertEqual(tile.windowX * 4 + tile.sourceX, tile.outputX)
                XCTAssertEqual(tile.windowY * 4 + tile.sourceY, tile.outputY)
                area += tile.outputWidth * tile.outputHeight
            }
            // Every output pixel exactly once: total area matches and no two tiles overlap.
            XCTAssertEqual(area, w * h * 16)
            for i in 0..<tiles.count {
                for j in (i + 1)..<tiles.count {
                    let a = tiles[i]
                    let b = tiles[j]
                    let overlapX = max(0, min(a.outputX + a.outputWidth, b.outputX + b.outputWidth) - max(a.outputX, b.outputX))
                    let overlapY = max(0, min(a.outputY + a.outputHeight, b.outputY + b.outputHeight) - max(a.outputY, b.outputY))
                    XCTAssertEqual(overlapX * overlapY, 0)
                }
            }
        }
    }

    func testScaleTwoCoverage() {
        for size in [[100, 100], [385, 700], [1000, 1000], [4032, 3024]] {
            let tiles = TilePlanner.plan(width: size[0], height: size[1], scale: 2)
            var area = 0
            for tile in tiles {
                XCTAssertEqual(tile.windowX * 2 + tile.sourceX, tile.outputX)
                XCTAssertEqual(tile.windowY * 2 + tile.sourceY, tile.outputY)
                XCTAssertLessThanOrEqual((tile.sourceX + tile.outputWidth) * 2, 1536)
                XCTAssertLessThanOrEqual((tile.sourceY + tile.outputHeight) * 2, 1536)
                area += tile.outputWidth * tile.outputHeight
            }
            XCTAssertEqual(area, size[0] * size[1] * 4)
            for i in 0..<tiles.count {
                for j in (i + 1)..<tiles.count {
                    let a = tiles[i], b = tiles[j]
                    let x = max(0, min(a.outputX + a.outputWidth, b.outputX + b.outputWidth) - max(a.outputX, b.outputX))
                    let y = max(0, min(a.outputY + a.outputHeight, b.outputY + b.outputHeight) - max(a.outputY, b.outputY))
                    XCTAssertEqual(x * y, 0)
                }
            }
        }
    }

    func testScaleTwoModelRoundTrip() {
        let width = 9, height = 7, window = 4, tileSize = 16
        var input: [UInt8] = []
        for i in 0..<(width * height) {
            input.append(UInt8((i * 37) % 256)); input.append(UInt8((i * 13) % 256))
            input.append(UInt8((i * 7) % 256)); input.append(UInt8(255))
        }
        var output = [UInt8](repeating: UInt8(0), count: width * height * 16)
        for tile in TilePlanner.plan(width: width, height: height, window: window, halo: 1, scale: 2) {
            let ext = PixelMath.extractWindow(input: input, width: width, height: height, tile: tile, window: window)
            var model = [Float](repeating: Float(0), count: 3 * tileSize * tileSize)
            for c in 0..<3 { for y in 0..<tileSize { for x in 0..<tileSize {
                model[c * tileSize * tileSize + y * tileSize + x] = ext[c * window * window + (y / 4) * window + x / 4]
            } } }
            TileStitcher.stitch(tile: tile, tileOutput: model, output: &output, outputWidth: width * 2, tileSize: tileSize, outputScale: 2)
        }
        for y in 0..<(height * 2) { for x in 0..<(width * 2) { for c in 0..<4 {
            XCTAssertEqual(output[(y * width * 2 + x) * 4 + c], input[((y / 2) * width + x / 2) * 4 + c])
        } } }
    }

    func testTileCounts() {
        XCTAssertEqual(TilePlanner.plan(width: 100, height: 100).count, 1)
        XCTAssertEqual(TilePlanner.plan(width: 352, height: 352).count, 1)
        XCTAssertEqual(TilePlanner.plan(width: 353, height: 352).count, 2)
        XCTAssertEqual(TilePlanner.plan(width: 384, height: 384).count, 4)
        XCTAssertEqual(TilePlanner.plan(width: 4032, height: 3024).count, 12 * 9)
    }

    // MARK: Extraction, stitching

    func testExtractionAndStitch() {
        let tile = TilePlanner.plan(width: 2, height: 1, window: 4, halo: 1, scale: 4)[0]
        let input = bytes([0, 128, 255, 255, 255, 64, 0, 255])
        let extracted = PixelMath.extractWindow(input: input, width: 2, height: 1, tile: tile, window: 4)
        XCTAssertEqual(extracted.count, 48)
        XCTAssertEqual(extracted[0], Float(0))
        XCTAssertEqual(extracted[1], Float(1))
        XCTAssertEqual(extracted[3], Float(1))
        XCTAssertEqual(extracted[15], Float(1))
        XCTAssertEqual(extracted[16], Float(128) / Float(255))
        var tileOutput = [Float](repeating: Float(0), count: 3 * 16 * 16)
        for i in 0..<(16 * 16) {
            tileOutput[i] = Float(-1)
            tileOutput[256 + i] = Float(0.5)
            tileOutput[512 + i] = Float(2)
        }
        var output = [UInt8](repeating: UInt8(0), count: 8 * 4 * 4)
        TileStitcher.stitch(tile: tile, tileOutput: tileOutput, output: &output, outputWidth: 8, tileSize: 16)
        let expected = bytes([0, 128, 255, 255])
        for i in 0..<(8 * 4) {
            XCTAssertEqual(Array(output[(i * 4)..<(i * 4 + 4)]), expected)
        }
    }

    func testStitchSanitisesNonFinite() {
        let tile = TilePlanner.plan(width: 1, height: 1, window: 4, halo: 1, scale: 1)[0]
        var tileOutput = [Float](repeating: Float(0), count: 3 * 16)
        tileOutput[0] = Float.nan
        tileOutput[16] = Float.infinity
        tileOutput[32] = Float(1)
        var output = [UInt8](repeating: UInt8(7), count: 4)
        TileStitcher.stitch(tile: tile, tileOutput: tileOutput, output: &output, outputWidth: 1, tileSize: 4)
        XCTAssertEqual(output, bytes([0, 0, 255, 255]))
    }

    /// Identity "model" (scale 1) and nearest-neighbour "model" (scale 2) through plan, extract and stitch must
    /// reproduce the image exactly, including images smaller than the window (replicate padding, cropped on output).
    func testStitcherRoundTrip() {
        let sizes: [[Int]] = [[7, 5], [3, 2], [4, 4], [9, 1], [1, 9], [12, 10]]
        for size in sizes {
            let w = size[0]
            let h = size[1]
            var input: [UInt8] = []
            for i in 0..<(w * h) {
                input.append(UInt8((i * 37 + 11) % 256))
                input.append(UInt8((i * 91 + 5) % 256))
                input.append(UInt8((i * 13 + 200) % 256))
                input.append(UInt8(255))
            }
            for scale in [1, 2] {
                let window = 4
                let tileSize = window * scale
                let tiles = TilePlanner.plan(width: w, height: h, window: window, halo: 1, scale: scale)
                var output = [UInt8](repeating: UInt8(0), count: w * scale * h * scale * 4)
                for tile in tiles {
                    let ext = PixelMath.extractWindow(input: input, width: w, height: h, tile: tile, window: window)
                    var tileOutput = [Float](repeating: Float(0), count: 3 * tileSize * tileSize)
                    for c in 0..<3 {
                        for y in 0..<tileSize {
                            for x in 0..<tileSize {
                                tileOutput[c * tileSize * tileSize + y * tileSize + x] = ext[c * window * window + (y / scale) * window + (x / scale)]
                            }
                        }
                    }
                    TileStitcher.stitch(tile: tile, tileOutput: tileOutput, output: &output, outputWidth: w * scale, tileSize: tileSize)
                }
                var expected: [UInt8] = []
                for y in 0..<(h * scale) {
                    for x in 0..<(w * scale) {
                        let s = ((y / scale) * w + (x / scale)) * 4
                        for c in 0..<4 { expected.append(input[s + c]) }
                    }
                }
                XCTAssertEqual(output, expected, "round trip \(w)x\(h) scale \(scale)")
            }
        }
    }

    // MARK: Pixel math

    func testPixelMath() {
        XCTAssertEqual(PixelMath.psnr([Float(0), Float(1)], [Float(0), Float(1)]), 99.0)
        XCTAssertEqual(PixelMath.psnr([Float(0)], [Float(0.5)]), 6.0206, accuracy: 0.0001)
        XCTAssertEqual(PixelMath.psnr([Float(0), Float(0)], [Float(1), Float(1)]), 0.0, accuracy: 0.0001)
        XCTAssertEqual(PixelMath.psnr([Float(0.9)], [Float(1)]), 20.0, accuracy: 0.001)
        XCTAssertEqual(PixelMath.psnr([Float(0.5), Float(0.5)], [Float(0.5), Float(0.75)]), 15.0515, accuracy: 0.0001)
        // Values are clamped to [0, 1] before comparing.
        XCTAssertEqual(PixelMath.psnr([Float(-1), Float(2)], [Float(0), Float(1)]), 99.0)
        XCTAssertEqual(PixelMath.psnr([Float.nan], [Float(0)]), 0.0)
        XCTAssertEqual(PixelMath.psnr([Float(0)], [Float.infinity]), 0.0)
        XCTAssertTrue(PixelMath.hasNonFinite([Float(0), Float.infinity]))
        XCTAssertTrue(PixelMath.hasNonFinite([Float.nan]))
        XCTAssertFalse(PixelMath.hasNonFinite([Float(0), Float(1)]))
        XCTAssertTrue(PixelMath.isConstant([Float(1), Float(1)]))
        XCTAssertFalse(PixelMath.isConstant([Float(1), Float(2)]))
        XCTAssertTrue(PixelMath.isConstant([Float]()))
        var data = [Float](repeating: Float(1), count: 48)
        data[0] = Float(0)
        XCTAssertEqual(PixelMath.downsample4x(data, width: 4, height: 4), [Float(15) / Float(16), Float(1), Float(1)])
    }

    // MARK: Device check

    func testDeviceNPUPasses() {
        let cpu = meas(tileMs: 100.0, psnrVsCPU: 99.0)
        // Exactly at the thresholds (45 dB, 26 dB) still passes; GPU only 1.18x faster does not win.
        let npu = meas(tileMs: 13.0, psnrVsCPU: 45.0, psnrDown: 26.0)
        let gpu = meas(tileMs: 11.0)
        let verdict = DeviceCheck.decide(cpu: cpu, gpu: gpu, npu: npu)
        XCTAssertEqual(verdict?.unit, ComputeUnit.npu)
        XCTAssertEqual(DeviceCheck.decide(cpu: cpu, gpu: nil, npu: npu)?.unit, ComputeUnit.npu)
        // GPU failing its own check never beats a passing NPU.
        XCTAssertEqual(DeviceCheck.decide(cpu: cpu, gpu: meas(tileMs: 1.0, hasNaN: true), npu: npu)?.unit, ComputeUnit.npu)
    }

    func testDeviceNPUFailsFallsToGPU() {
        let cpu = meas(tileMs: 100.0, psnrVsCPU: 99.0)
        let gpu = meas(tileMs: 40.0)
        let failing: [DeviceMeasurement] = [
            meas(psnrVsCPU: 44.9),
            meas(psnrDown: 25.9),
            meas(hasNaN: true),
            meas(isConstant: true),
            meas(placementAllNPU: false),
            meas(tileMs: 0.0),
        ]
        for npu in failing {
            XCTAssertEqual(DeviceCheck.decide(cpu: cpu, gpu: gpu, npu: npu)?.unit, ComputeUnit.gpu)
        }
        XCTAssertEqual(DeviceCheck.decide(cpu: cpu, gpu: gpu, npu: nil)?.unit, ComputeUnit.gpu)
    }

    func testDeviceGPUMuchFasterWins() {
        let cpu = meas(tileMs: 100.0, psnrVsCPU: 99.0)
        let npu = meas(tileMs: 13.0)
        XCTAssertEqual(DeviceCheck.decide(cpu: cpu, gpu: meas(tileMs: 10.0), npu: npu)?.unit, ComputeUnit.gpu) // exactly 1.3x
        XCTAssertEqual(DeviceCheck.decide(cpu: cpu, gpu: meas(tileMs: 5.0), npu: npu)?.unit, ComputeUnit.gpu)
        XCTAssertEqual(DeviceCheck.decide(cpu: cpu, gpu: meas(tileMs: 10.1), npu: npu)?.unit, ComputeUnit.npu) // 1.287x
    }

    func testDeviceAllFailFallsToCPU() {
        let cpu = meas(tileMs: 100.0, psnrVsCPU: 99.0)
        let badNPU = meas(psnrVsCPU: 10.0)
        let badGPU = meas(hasNaN: true)
        let verdict = DeviceCheck.decide(cpu: cpu, gpu: badGPU, npu: badNPU)
        XCTAssertEqual(verdict?.unit, ComputeUnit.cpu)
        XCTAssertEqual(DeviceCheck.decide(cpu: cpu, gpu: nil, npu: nil)?.unit, ComputeUnit.cpu)
        XCTAssertNil(DeviceCheck.decide(cpu: nil, gpu: badGPU, npu: badNPU))
        XCTAssertNil(DeviceCheck.decide(cpu: meas(hasNaN: true), gpu: nil, npu: meas(timedOut: true)))
    }

    func testDeviceNPULoadTimeout() {
        let cpu = meas(tileMs: 100.0, psnrVsCPU: 99.0)
        let gpu = meas(tileMs: 40.0)
        // The NPU is otherwise perfect and much faster, but its load timed out.
        let verdict = DeviceCheck.decide(cpu: cpu, gpu: gpu, npu: meas(tileMs: 1.0, timedOut: true))
        XCTAssertEqual(verdict?.unit, ComputeUnit.gpu)
        XCTAssertTrue((verdict?.reason ?? "").contains("timed out"))
        // A load longer than 15 s counts as a timeout even without the flag; exactly 15 s does not.
        XCTAssertEqual(DeviceCheck.decide(cpu: cpu, gpu: gpu, npu: meas(loadMs: 15001.0, tileMs: 1.0))?.unit, ComputeUnit.gpu)
        XCTAssertEqual(DeviceCheck.decide(cpu: cpu, gpu: gpu, npu: meas(loadMs: 15000.0, tileMs: 1.0))?.unit, ComputeUnit.npu)
        // Timeout and a failing GPU: CPU.
        let noGPU = DeviceCheck.decide(cpu: cpu, gpu: meas(hasNaN: true), npu: meas(timedOut: true))
        XCTAssertEqual(noGPU?.unit, ComputeUnit.cpu)
        XCTAssertTrue((noGPU?.reason ?? "").contains("timed out"))
    }

    func testDeviceCacheKey() {
        let a = DeviceCheck.cacheKey(deviceModel: "ab", osVersion: "c", appBuild: "d", modelKey: ModelKey.best)
        let b = DeviceCheck.cacheKey(deviceModel: "a", osVersion: "bc", appBuild: "d", modelKey: ModelKey.best)
        XCTAssertNotEqual(a, b)
        XCTAssertNotEqual(a, DeviceCheck.cacheKey(deviceModel: "ab", osVersion: "c", appBuild: "d", modelKey: ModelKey.fast))
        XCTAssertEqual(a, DeviceCheck.cacheKey(deviceModel: "ab", osVersion: "c", appBuild: "d", modelKey: ModelKey.best))
    }

    // MARK: Size policy, ETA

    func testSizePolicy() {
        let same = SizePolicy.target(width: 2000, height: 1500) // exactly 3 MP stays
        XCTAssertEqual(same.width, 2000)
        XCTAssertEqual(same.height, 1500)
        XCTAssertEqual(same.factor, 1.0)
        let small = SizePolicy.target(width: 640, height: 480)
        XCTAssertEqual(small.width, 640)
        XCTAssertEqual(small.factor, 1.0)
        let reduced = SizePolicy.target(width: 4000, height: 3000) // 12 MP phone photo
        XCTAssertEqual(reduced.width, 4000)
        XCTAssertEqual(reduced.height, 3000)
        XCTAssertEqual(reduced.factor, 1.0)
        XCTAssertEqual(reduced.outputScale, 2)
        XCTAssertEqual(same.outputScale, 4)
        let iphonePhoto = SizePolicy.target(width: 4032, height: 3024)
        XCTAssertEqual(iphonePhoto.width, 4032)
        XCTAssertEqual(iphonePhoto.height, 3024)
        XCTAssertEqual(iphonePhoto.factor, 1)
        let sizes: [[Int]] = [[8000, 6000], [4032, 3024], [4001, 3000], [10000, 10000], [40000, 1000], [1000, 40000], [30000, 401], [12001, 1000]]
        for size in sizes {
            let t = SizePolicy.target(width: size[0], height: size[1])
            XCTAssertGreaterThan(t.width, 0)
            XCTAssertGreaterThan(t.height, 0)
            XCTAssertLessThanOrEqual(t.width * t.height, SizePolicy.maxInputPixels)
            XCTAssertLessThanOrEqual(t.width, size[0])
            XCTAssertLessThanOrEqual(t.height, size[1])
            // Default output stays within the policy limit.
            XCTAssertLessThanOrEqual((t.width * t.outputScale) * (t.height * t.outputScale), SizePolicy.maxInputPixels * 4)
        }
    }

    func testETA() {
        XCTAssertNil(ETA.remaining(completed: 0, total: 10, elapsedSeconds: 2.0))
        XCTAssertNil(ETA.remaining(completed: 0, total: 0, elapsedSeconds: 2.0))
        XCTAssertNil(ETA.remaining(completed: 11, total: 10, elapsedSeconds: 2.0))
        XCTAssertNil(ETA.remaining(completed: 1, total: 10, elapsedSeconds: -1.0))
        XCTAssertEqual(ETA.remaining(completed: 2, total: 10, elapsedSeconds: 4.0), 16.0)
        XCTAssertEqual(ETA.remaining(completed: 10, total: 10, elapsedSeconds: 4.0), 0.0)
    }

    func testUIFormatting() {
        XCTAssertEqual(ModelKey.best.displayName, "Best")
        XCTAssertEqual(ModelKey.fast.displayName, "Fast")
        XCTAssertEqual(ComputeUnit.npu.displayName, "NPU")
        XCTAssertEqual(ComputeUnit.gpu.displayName, "GPU")
        XCTAssertEqual(ComputeUnit.cpu.displayName, "CPU")
        XCTAssertEqual(UIFormatting.formatSeconds(2.36), "2.4 s")
        XCTAssertEqual(
            UIFormatting.resultSummary(width: 4000, height: 3000, model: ModelKey.best, unit: ComputeUnit.npu, seconds: 2.36),
            "4000 × 3000 · Best · NPU · 2.4 s"
        )
        XCTAssertEqual(UIFormatting.formatETA(45.2), "45 s remaining")
        XCTAssertEqual(UIFormatting.formatETA(125.0), "2 min 5 s remaining")
        XCTAssertEqual(UIFormatting.formatETA(120.0), "2 min remaining")
    }
}
