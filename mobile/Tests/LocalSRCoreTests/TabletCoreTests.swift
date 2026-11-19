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
        XCTAssertEqual(ModelCatalog.models(in: .upscalePhotos).count, 5)
        XCTAssertEqual(ModelCatalog.models(in: .illustration).count, 1)
        XCTAssertEqual(ModelCatalog.models(in: .fixNoiseAndJpeg).count, 2)
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
        XCTAssertEqual(decoded.stageSizes(width: 1200, height: 900, requestedScale: 2)[2].width, 2400)
        XCTAssertEqual(decoded.stageSizes(width: 1200, height: 900, requestedScale: 2)[2].height, 1800)
        // A 48 MP input is prescaled to the 12.19 MP cap (4032 x 3024, the iPhone photo size) before 2x.
        let capped = decoded.stageSizes(width: 8000, height: 6000)
        XCTAssertEqual(capped[0].width, 4032)
        XCTAssertEqual(capped[2].width, 8064)
        let restore = Plan(stages: [.jpegFix, .noiseFix]).stageSizes(width: 8000, height: 6000)
        XCTAssertEqual(restore[1].width, 8000)
        XCTAssertEqual(restore[1].height, 6000)
    }

    func testEnhanceSetupPlansRecipesAndLanes() {
        var setup = TabletEnhanceSetup()
        XCTAssertEqual(setup.buildPlan().stages, [TabletModelID.best])
        XCTAssertEqual(setup.headingSummary(matchingRecipe: nil), "Best \u{00B7} Photo")

        setup.upscaleModel = TabletModelID.finest
        setup.fixJpeg = true
        XCTAssertEqual(setup.buildPlan().stages, [TabletModelID.jpegFix, TabletModelID.finest])

        let recipe = setup.makeRecipe(id: "r1", name: "Old scans")
        XCTAssertEqual(recipe.summaryText, "Upscale \u{00B7} Finest \u{00B7} JPEG fix")
        XCTAssertTrue(setup.matches(recipe: recipe))

        let json = TabletRecipeStore.encode([recipe])
        let decodedRecipes = TabletRecipeStore.decode(json)
        XCTAssertEqual(decodedRecipes, [recipe])

        setup.task = TabletTaskKind.restore
        setup.fixJpeg = false
        setup.fixNoise = false
        XCTAssertEqual(setup.buildPlan().stages, [TabletModelID.noiseFix])
        XCTAssertEqual(setup.headingSummary(matchingRecipe: nil), "Restore")

        let applied = setup.applying(recipe: recipe)
        XCTAssertEqual(applied.task, TabletTaskKind.upscale)
        XCTAssertEqual(applied.upscaleModel, TabletModelID.finest)
        XCTAssertTrue(applied.fixJpeg)
        XCTAssertFalse(applied.fixNoise)

        // Lane verdicts & validation
        let bothVerdict = TabletChipVerdict(preferred: ComputeUnit.npu, cpu: true, gpu: true, neuralEngine: true)
        let gpuOnlyVerdict = TabletChipVerdict(preferred: ComputeUnit.gpu, cpu: true, gpu: true, neuralEngine: false)
        XCTAssertEqual(
            TabletEnhanceSetup.chipLabel(for: TabletModelID.best, verdict: bothVerdict, laneChoice: TabletLaneChoice.both, isAndroid: false),
            "Both chips"
        )
        XCTAssertEqual(
            TabletEnhanceSetup.chipLabel(for: TabletModelID.finest, verdict: gpuOnlyVerdict, laneChoice: TabletLaneChoice.both, isAndroid: false),
            "GPU"
        )
        XCTAssertEqual(
            TabletEnhanceSetup.chipLabel(for: TabletModelID.best, verdict: nil, laneChoice: TabletLaneChoice.both, isAndroid: false),
            "Checked on first use"
        )
        XCTAssertEqual(
            TabletEnhanceSetup.chipLabel(for: TabletModelID.best, verdict: nil, laneChoice: TabletLaneChoice.both, isAndroid: true),
            "Chosen on first use"
        )

        let verdicts: [TabletModelID: TabletChipVerdict] = [TabletModelID.jpegFix: bothVerdict, TabletModelID.finest: gpuOnlyVerdict]
        let neFailure = TabletEnhanceSetup.laneValidationFailureMessage(
            plan: applied.buildPlan(),
            verdicts: verdicts,
            failedModels: [],
            laneChoice: TabletLaneChoice.neuralEngineOnly,
            deviceNoun: "this iPad"
        )
        XCTAssertEqual(neFailure?.detail, "Finest needs the GPU.")
        XCTAssertEqual(neFailure?.allFailed, false)

        let noFailure = TabletEnhanceSetup.laneValidationFailureMessage(
            plan: applied.buildPlan(),
            verdicts: verdicts,
            failedModels: [],
            laneChoice: TabletLaneChoice.both,
            deviceNoun: "this iPad"
        )
        XCTAssertNil(noFailure)

        // Only when every bundled model failed is the iPad unable to upscale at all.
        let allFailed = TabletEnhanceSetup.laneValidationFailureMessage(
            plan: Plan(stages: [TabletModelID.best]),
            verdicts: [:],
            failedModels: [TabletModelID.best],
            laneChoice: TabletLaneChoice.both,
            deviceNoun: "this iPad",
            bundledModels: [TabletModelID.best]
        )
        XCTAssertEqual(allFailed?.title, "Can't Upscale on This iPad")
        XCTAssertEqual(allFailed?.allFailed, true)

        XCTAssertEqual(TabletLaneChoice.neuralEngineOnly.shortTitle(isAndroid: true), "NPU")
        XCTAssertEqual(TabletLaneChoice.neuralEngineOnly.menuTitle(isAndroid: true), "NPU Only")
    }

    func testTabletLayoutBreakpoints() {
        let w1376 = TabletLayoutMetrics.forWidth(1376.0)
        XCTAssertEqual(w1376.mode, TabletWorkspaceLayoutMode.threePane)
        XCTAssertTrue(w1376.isThreePane)
        XCTAssertFalse(w1376.isTwoColumnEnhance)
        XCTAssertFalse(w1376.isCompactStatusBar)

        let w1100 = TabletLayoutMetrics.forWidth(1100.0)
        XCTAssertTrue(w1100.isThreePane)

        let w1099 = TabletLayoutMetrics.forWidth(1099.0)
        XCTAssertEqual(w1099.mode, TabletWorkspaceLayoutMode.twoColumnTabs)
        XCTAssertFalse(w1099.isThreePane)
        XCTAssertTrue(w1099.isTwoColumnEnhance)
        XCTAssertTrue(w1099.isMediaGrid)
        XCTAssertFalse(w1099.isCompactStatusBar)

        let w834 = TabletLayoutMetrics.forWidth(834.0)
        XCTAssertEqual(w834.mode, TabletWorkspaceLayoutMode.twoColumnTabs)
        XCTAssertTrue(w834.isTwoColumnEnhance)
        XCTAssertTrue(w834.isMediaGrid)

        let w700 = TabletLayoutMetrics.forWidth(700.0)
        XCTAssertEqual(w700.mode, TabletWorkspaceLayoutMode.twoColumnTabs)
        XCTAssertTrue(w700.isTwoColumnEnhance)

        let w699 = TabletLayoutMetrics.forWidth(699.0)
        XCTAssertEqual(w699.mode, TabletWorkspaceLayoutMode.oneColumnTabs)
        XCTAssertFalse(w699.isTwoColumnEnhance)
        XCTAssertTrue(w699.isMediaGrid)
        XCTAssertTrue(w699.isCompactStatusBar)

        let w600 = TabletLayoutMetrics.forWidth(600.0)
        XCTAssertTrue(w600.isMediaGrid)
        XCTAssertTrue(w600.isCompactStatusBar)

        let w507 = TabletLayoutMetrics.forWidth(507.0)
        XCTAssertEqual(w507.mode, TabletWorkspaceLayoutMode.oneColumnTabs)
        XCTAssertFalse(w507.isMediaGrid)
        XCTAssertTrue(w507.isCompactStatusBar)

        let w375 = TabletLayoutMetrics.forWidth(375.0)
        XCTAssertEqual(w375.mode, TabletWorkspaceLayoutMode.oneColumnTabs)
        XCTAssertFalse(w375.isMediaGrid)
        XCTAssertTrue(w375.isCompactStatusBar)
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

    func testValidationNamesTheOneFailedOrMissingModel() {
        let bundled: Set<TabletModelID> = [TabletModelID.best, TabletModelID.fast, TabletModelID.finest, TabletModelID.jpegFix]
        let oneFailed = TabletEnhanceSetup.laneValidationFailureMessage(
            plan: Plan(stages: [TabletModelID.finest]),
            verdicts: [:],
            failedModels: [TabletModelID.finest],
            laneChoice: TabletLaneChoice.both,
            deviceNoun: "this iPad",
            bundledModels: bundled
        )
        XCTAssertEqual(oneFailed?.title, "Can't Use Finest")
        XCTAssertEqual(oneFailed?.detail, "Finest doesn't run correctly on this iPad. Choose another quality.")
        XCTAssertEqual(oneFailed?.allFailed, true)

        let fixFailed = TabletEnhanceSetup.laneValidationFailureMessage(
            plan: Plan(stages: [TabletModelID.jpegFix, TabletModelID.best]),
            verdicts: [:],
            failedModels: [TabletModelID.jpegFix],
            laneChoice: TabletLaneChoice.both,
            deviceNoun: "this tablet",
            bundledModels: bundled
        )
        XCTAssertEqual(fixFailed?.title, "Can't Use JPEG Fix")
        XCTAssertEqual(fixFailed?.detail, "JPEG fix doesn't run correctly on this tablet. Turn off JPEG artifacts.")

        let missing = TabletEnhanceSetup.laneValidationFailureMessage(
            plan: Plan(stages: [TabletModelID.anime]),
            verdicts: [:],
            failedModels: [],
            laneChoice: TabletLaneChoice.both,
            deviceNoun: "this iPad",
            bundledModels: bundled
        )
        XCTAssertEqual(missing?.title, "Model Not Included")
        XCTAssertEqual(missing?.detail, "Anime isn't included in this build.")

        let restoreAllFailed = TabletEnhanceSetup.laneValidationFailureMessage(
            plan: Plan(stages: [TabletModelID.jpegFix]),
            verdicts: [:],
            failedModels: [TabletModelID.jpegFix],
            laneChoice: TabletLaneChoice.both,
            deviceNoun: "this tablet",
            bundledModels: [TabletModelID.jpegFix]
        )
        XCTAssertEqual(restoreAllFailed?.title, "Can't Restore on This Tablet")

        XCTAssertNil(TabletEnhanceSetup.laneValidationFailureMessage(
            plan: Plan(stages: [TabletModelID.best]),
            verdicts: [:],
            failedModels: [TabletModelID.finest],
            laneChoice: TabletLaneChoice.both,
            deviceNoun: "this iPad",
            bundledModels: bundled
        ))
    }

    func testBatchSummaryStaysWhileItDescribesTheQueue() {
        let complete = TabletBatchCompletion.complete(count: 5, seconds: 72.0)
        XCTAssertTrue(complete.isShown(failedInQueue: 0))
        XCTAssertTrue(complete.isShown(failedInQueue: 2))
        let someFailed = TabletBatchCompletion.partialFailure(succeeded: 3, total: 5, failed: 2)
        XCTAssertTrue(someFailed.isShown(failedInQueue: 2))
        XCTAssertFalse(someFailed.isShown(failedInQueue: 0), "Removing every failed photo leaves nothing to try again")
    }

    func testMemoryPolicyWorkersAndRestoreCap() {
        let gib: Int64 = 1_073_741_824
        XCTAssertEqual(TabletMemoryPolicy.workerCount(physicalMemory: 3 * gib, photoCount: 6), 1)
        XCTAssertEqual(TabletMemoryPolicy.workerCount(physicalMemory: 3_900_000_000, photoCount: 6), 1)
        XCTAssertEqual(TabletMemoryPolicy.workerCount(physicalMemory: 5_900_000_000, photoCount: 6), 2)
        XCTAssertEqual(TabletMemoryPolicy.workerCount(physicalMemory: 16 * gib, photoCount: 6), 2)
        XCTAssertEqual(TabletMemoryPolicy.workerCount(physicalMemory: 16 * gib, photoCount: 1), 1)
        XCTAssertEqual(TabletMemoryPolicy.workerCount(physicalMemory: 16 * gib, photoCount: 0), 0)
        XCTAssertFalse(TabletMemoryPolicy.isRestoreInputTooLarge(width: 8000, height: 6000))
        XCTAssertTrue(TabletMemoryPolicy.isRestoreInputTooLarge(width: 8001, height: 6000))
        XCTAssertTrue(TabletMemoryPolicy.isRestoreInputTooLarge(width: 16000, height: 4000))
    }

    func testModelNoticeSections() {
        let text = """
        LocalSR model notices
        =====================

        Best (best_key): PULSE
        ----------------------
        Best body.

        Tablet models
        =============
        Intro.

        JPEG fix (fbcnn): FBCNN
        -----------------------
        FBCNN body.

        Apache License 2.0 (apache-2.0)
        -------------------------------
        Apache text.
        """
        XCTAssertEqual(ModelNotice.section(key: "best_key", in: text), "Best (best_key): PULSE\n----------------------\nBest body.")
        XCTAssertEqual(ModelNotice.section(key: "fbcnn", in: text), "JPEG fix (fbcnn): FBCNN\n-----------------------\nFBCNN body.")
        XCTAssertNil(ModelNotice.section(key: "missing", in: text))
        let fbcnn = ModelNotice.text(for: ModelCatalog.model(TabletModelID.jpegFix), in: text)
        XCTAssertEqual(fbcnn?.hasSuffix("Apache text."), true)
        XCTAssertEqual(ModelCatalog.model(TabletModelID.span48).licence, "Apache-2.0")
        XCTAssertEqual(ModelCatalog.model(TabletModelID.span48).sourceURL, "https://github.com/hongyuanyu/SPAN")
        XCTAssertNil(ModelCatalog.model(TabletModelID.finest).sharedLicenceNoticeKey)
    }

    #if !SKIP
    /// The iPhone-only app's Licences screen: phone models and the font, no tablet models or Apache text.
    func testNoticeWithoutTabletModels() throws {
        var url = URL(fileURLWithPath: #filePath)
        for _ in 0..<3 { url.deleteLastPathComponent() }
        let notice = try String(contentsOf: url.appendingPathComponent("Models/NOTICE.txt"), encoding: .utf8)
        let phone = ModelNotice.withoutTabletModels(notice)
        XCTAssertTrue(phone.count < notice.count)
        for kept in ["(localsr_photo_pulse48_v1_x4)", "(localsr_photo_span28_v1_x4)", "MIT License", "(IBMPlexSansVar-Roman)", "SIL OPEN FONT LICENSE"] {
            XCTAssertTrue(phone.contains(kept), "\(kept) missing")
        }
        for dropped in ["Tablet models", "(nomos_realplksr)", "(fbcnn)", "(anime_esrgan)", "(span48)", "(apache-2.0)", "END OF TERMS AND CONDITIONS"] {
            XCTAssertFalse(phone.contains(dropped), "\(dropped) still there")
        }
        XCTAssertEqual(ModelNotice.withoutTabletModels("Only\n====\n\ntext"), "Only\n====\n\ntext")
    }

    /// Every bundled model's notice must be in the NOTICE.txt the Licences screen shows.
    func testShippedNoticeCoversEveryModel() throws {
        var url = URL(fileURLWithPath: #filePath)
        for _ in 0..<3 { url.deleteLastPathComponent() }
        let notice = try String(contentsOf: url.appendingPathComponent("Models/NOTICE.txt"), encoding: .utf8)
        for model in ModelCatalog.all {
            let text = ModelNotice.text(for: model, in: notice)
            XCTAssertNotNil(text, "No NOTICE.txt section for \(model.id.rawValue)")
            if model.licence == "Apache-2.0" {
                XCTAssertEqual(text?.contains("END OF TERMS AND CONDITIONS"), true, "\(model.id.rawValue) lacks the Apache-2.0 text")
            }
            if model.licence == "CC BY 4.0" {
                XCTAssertEqual(text?.contains("https://creativecommons.org/licenses/by/4.0/"), true)
                XCTAssertEqual(text?.contains("Changes:"), true)
            }
        }
        let anime = ModelNotice.text(for: ModelCatalog.model(TabletModelID.anime), in: notice) ?? ""
        XCTAssertTrue(anime.contains("Copyright (c) 2021, Xintao Wang"))
        XCTAssertTrue(anime.contains("Redistributions in binary form must reproduce the above copyright notice"))
    }
    #endif
}
