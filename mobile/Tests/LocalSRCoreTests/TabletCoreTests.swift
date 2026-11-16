import Foundation
import XCTest
@testable import LocalSRCore

final class TabletCoreTests: XCTestCase {
    func testCatalogAndPlans() {
        XCTAssertEqual(ModelCatalog.all.count, 8)
        XCTAssertEqual(ModelCatalog.model(.jpegFix).window, 256)
        XCTAssertEqual(ModelCatalog.model(.noiseFix).preferredLane, .gpu)
        XCTAssertEqual(ModelCatalog.model(.finest).iosFileName, "nomos_realplksr.mlmodelc")
        XCTAssertEqual(ModelCatalog.model(.fast).androidFileName, "fast.tflite")
        XCTAssertTrue(Plan(stages: [.jpegFix, .noiseFix, .best]).isValid)
        XCTAssertTrue(Plan(stages: [.finest]).isValid)
        XCTAssertFalse(Plan(stages: []).isValid)
        XCTAssertTrue(Plan(stages: [.jpegFix]).isValid)
        XCTAssertTrue(Plan(stages: [.jpegFix, .noiseFix]).isValid)
        XCTAssertFalse(Plan(stages: [.noiseFix, .jpegFix, .best]).isValid)
        XCTAssertFalse(Plan(stages: [.jpegFix, .jpegFix, .best]).isValid)
        XCTAssertFalse(Plan(stages: [.best, .fast]).isValid)
    }

    func testRecipeRoundTripAndStageSizes() throws {
        let plan = Plan(stages: [.jpegFix, .noiseFix, .finest])
        let encoded = try JSONEncoder().encode(plan)
        let decoded = try JSONDecoder().decode(Plan.self, from: encoded)
        XCTAssertEqual(decoded.stages, plan.stages)
        let sizes = decoded.stageSizes(width: 4000, height: 3000)
        XCTAssertEqual(sizes[0].width, 4000)
        XCTAssertEqual(sizes[1].height, 3000)
        XCTAssertEqual(sizes[2].width, 8000)
        XCTAssertEqual(sizes[2].height, 6000)
        XCTAssertEqual(decoded.stageSizes(width: 4000, height: 3000, fullScale: true)[2].width, 16000)
        XCTAssertEqual(decoded.stageSizes(width: 1200, height: 900)[2].height, 3600)
        let capped = decoded.stageSizes(width: 8000, height: 6000)
        XCTAssertEqual(capped[0].width, 4000)
        XCTAssertEqual(capped[2].width, 8000)
        let restore = Plan(stages: [.jpegFix, .noiseFix]).stageSizes(width: 8000, height: 6000)
        XCTAssertEqual(restore[1].width, 8000)
        XCTAssertEqual(restore[1].height, 6000)
    }

    func testScaleOneWindow256Coverage() {
        for size in [[1, 1], [255, 256], [256, 256], [257, 600], [1200, 900]] {
            let tiles = TilePlanner.plan(width: size[0], height: size[1], window: 256, halo: 16, scale: 1)
            var area = 0
            for tile in tiles {
                XCTAssertEqual(tile.windowX + tile.sourceX, tile.outputX)
                XCTAssertEqual(tile.windowY + tile.sourceY, tile.outputY)
                XCTAssertLessThanOrEqual(tile.sourceX + tile.outputWidth, 256)
                XCTAssertLessThanOrEqual(tile.sourceY + tile.outputHeight, 256)
                area += tile.outputWidth * tile.outputHeight
            }
            XCTAssertEqual(area, size[0] * size[1])
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

    func testScaleOneStitch() {
        let tile = TilePlanner.plan(width: 2, height: 1, window: 4, halo: 1, scale: 1)[0]
        let input: [UInt8] = [UInt8(12), UInt8(34), UInt8(56), UInt8(255), UInt8(78), UInt8(90), UInt8(123), UInt8(255)]
        let extracted = PixelMath.extractWindow(input: input, width: 2, height: 1, tile: tile, window: 4)
        var output = [UInt8](repeating: UInt8(0), count: 8)
        TileStitcher.stitch(tile: tile, tileOutput: extracted, output: &output, outputWidth: 2, tileSize: 4, outputScale: 1, modelScale: 1)
        XCTAssertEqual(output, input)
    }
}
