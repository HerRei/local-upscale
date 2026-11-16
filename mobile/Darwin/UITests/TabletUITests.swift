import XCTest
import UIKit

final class TabletUITests: XCTestCase {
    private let shotDir = "/private/tmp/claude-501/-Users-hermesheiniger/619da889-5d3f-4724-b759-70d636ce5866/scratchpad/tui1/shots"
    private let tui2ShotDir = "/private/tmp/claude-501/-Users-hermesheiniger/619da889-5d3f-4724-b759-70d636ce5866/scratchpad/tui2/shots"
    private let tui3ShotDir = "/private/tmp/claude-501/-Users-hermesheiniger/619da889-5d3f-4724-b759-70d636ce5866/scratchpad/tui3/shots"

    override func setUpWithError() throws {
        continueAfterFailure = false
        if UIDevice.current.userInterfaceIdiom != .pad {
            throw XCTSkip("Tablet UI tests only run on iPad")
        }
    }

    @MainActor
    func testTabletBatchLandscapeAndPortrait() throws {
        try FileManager.default.createDirectory(atPath: shotDir, withIntermediateDirectories: true)
        let appearance = ProcessInfo.processInfo.environment["SHOT_APPEARANCE"] ?? "light"
        let deviceTag = ProcessInfo.processInfo.environment["SHOT_DEVICE"] ?? "ipad13"

        XCUIDevice.shared.orientation = .landscapeLeft
        let app = XCUIApplication()
        app.launch()
        Thread.sleep(forTimeInterval: 0.6)

        // Ensure clean starting state if previous run left results
        cleanExistingResultsIfNeeded(app: app)

        XCTAssertTrue(app.staticTexts["tabletEmptyHeadline"].waitForExistence(timeout: 15), "Tablet empty headline missing")
        saveShot("tablet_\(deviceTag)_landscape_empty_\(appearance).png")

        // Open Media Add menu -> Choose from Photos...
        let addButton = app.buttons["tabletMediaAddButton"]
        XCTAssertTrue(addButton.waitForExistence(timeout: 5), "Media + button missing")
        addButton.tap()

        let photosMenuItem = app.buttons["tabletAddPhotosMenuItem"]
        XCTAssertTrue(photosMenuItem.waitForExistence(timeout: 5), "Choose from Photos menu item missing")
        photosMenuItem.tap()

        XCTAssertTrue(selectPhotosInSystemPicker(app: app, count: 3), "Could not select 3 photos in PHPicker")

        let startAllButton = app.buttons["tabletStartAllButton"]
        XCTAssertTrue(startAllButton.waitForExistence(timeout: 15), "Start All 3 button did not appear")
        XCTAssertTrue(app.buttons["tabletQueueSegmentButton"].exists)
        Thread.sleep(forTimeInterval: 0.6)
        saveShot("tablet_\(deviceTag)_landscape_queued_\(appearance).png")

        startAllButton.tap()

        if app.buttons["tabletCancelBatchButton"].waitForExistence(timeout: 4) {
            Thread.sleep(forTimeInterval: 0.5)
            saveShot("tablet_\(deviceTag)_landscape_running_\(appearance).png")
        }

        let completePredicate = NSPredicate(format: "label == 'Complete'")
        let statusTitle = app.staticTexts["tabletStatusTitle"]
        let exp = expectation(for: completePredicate, evaluatedWith: statusTitle)
        wait(for: [exp], timeout: 180)
        Thread.sleep(forTimeInterval: 0.8)
        saveShot("tablet_\(deviceTag)_landscape_done_\(appearance).png")

        // Switch to portrait -> verify tab mode
        XCUIDevice.shared.orientation = .portrait
        Thread.sleep(forTimeInterval: 1.0)

        let mediaTab = app.buttons["tabletTabMedia"]
        let viewerTab = app.buttons["tabletTabViewer"]
        let enhanceTab = app.buttons["tabletTabEnhance"]
        XCTAssertTrue(mediaTab.waitForExistence(timeout: 5), "Tab mode Media button missing in portrait")
        XCTAssertTrue(viewerTab.exists && enhanceTab.exists)
        saveShot("tablet_\(deviceTag)_portrait_viewer_\(appearance).png")

        mediaTab.tap()
        Thread.sleep(forTimeInterval: 0.5)
        saveShot("tablet_\(deviceTag)_portrait_media_queue_\(appearance).png")

        let resultsSeg = app.buttons["tabletResultsSegmentButton"]
        XCTAssertTrue(resultsSeg.waitForExistence(timeout: 4))
        resultsSeg.tap()
        Thread.sleep(forTimeInterval: 0.5)
        saveShot("tablet_\(deviceTag)_portrait_media_results_\(appearance).png")

        enhanceTab.tap()
        Thread.sleep(forTimeInterval: 0.5)
        saveShot("tablet_\(deviceTag)_portrait_enhance_\(appearance).png")
    }

    @MainActor
    func testTabletMiniPortrait() throws {
        try FileManager.default.createDirectory(atPath: shotDir, withIntermediateDirectories: true)
        let appearance = ProcessInfo.processInfo.environment["SHOT_APPEARANCE"] ?? "light"
        let deviceTag = ProcessInfo.processInfo.environment["SHOT_DEVICE"] ?? "ipadmini"

        XCUIDevice.shared.orientation = .portrait
        let app = XCUIApplication()
        app.launch()
        Thread.sleep(forTimeInterval: 0.6)

        let mediaTab = app.buttons["tabletTabMedia"]
        XCTAssertTrue(mediaTab.waitForExistence(timeout: 15), "Tab mode Media button missing on iPad mini portrait")

        // Import 3 photos via top bar + menu if queue is empty
        if app.staticTexts["tabletEmptyHeadline"].exists {
            let addBtn = app.buttons["tabletTopBarAddButton"]
            XCTAssertTrue(addBtn.waitForExistence(timeout: 5))
            addBtn.tap()
            let photosMenuItem = app.buttons["tabletAddPhotosMenuItem"]
            XCTAssertTrue(photosMenuItem.waitForExistence(timeout: 5))
            photosMenuItem.tap()
            XCTAssertTrue(selectPhotosInSystemPicker(app: app, count: 3), "Could not select 3 photos in PHPicker on iPad mini")
            XCTAssertTrue(app.buttons["tabletStartAllButton"].waitForExistence(timeout: 15))
            Thread.sleep(forTimeInterval: 0.5)
        }

        saveShot("tablet_\(deviceTag)_portrait_viewer_\(appearance).png")

        mediaTab.tap()
        Thread.sleep(forTimeInterval: 0.5)
        saveShot("tablet_\(deviceTag)_portrait_media_\(appearance).png")

        app.buttons["tabletTabEnhance"].tap()
        Thread.sleep(forTimeInterval: 0.5)
        saveShot("tablet_\(deviceTag)_portrait_enhance_\(appearance).png")
    }

    @MainActor
    func testTabletViewerCompareZoomAndSave() throws {
        try FileManager.default.createDirectory(atPath: tui2ShotDir, withIntermediateDirectories: true)
        let appearance = ProcessInfo.processInfo.environment["SHOT_APPEARANCE"] ?? "light"
        let deviceTag = ProcessInfo.processInfo.environment["SHOT_DEVICE"] ?? "ipad13"
        let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")

        XCUIDevice.shared.orientation = .landscapeLeft
        let app = XCUIApplication()
        app.launch()
        Thread.sleep(forTimeInterval: 0.6)

        cleanExistingResultsIfNeeded(app: app)
        XCTAssertTrue(app.staticTexts["tabletEmptyHeadline"].waitForExistence(timeout: 15), "Tablet empty headline missing")

        // Import 1 photo via Media Add menu -> Choose from Photos...
        let addButton = app.buttons["tabletMediaAddButton"]
        XCTAssertTrue(addButton.waitForExistence(timeout: 5), "Media + button missing")
        addButton.tap()

        let photosMenuItem = app.buttons["tabletAddPhotosMenuItem"]
        XCTAssertTrue(photosMenuItem.waitForExistence(timeout: 5), "Choose from Photos menu item missing")
        photosMenuItem.tap()

        XCTAssertTrue(selectOnePhotoPreferringDetailedFixture(app: app), "Could not select 1 photo in PHPicker")

        let upscaleBtn = app.buttons["tabletUpscaleSelectedButton"]
        XCTAssertTrue(upscaleBtn.waitForExistence(timeout: 15), "Upscale Selected button did not appear")
        upscaleBtn.tap()

        // Wait for result & compare divider
        let handle = app.descendants(matching: .any)["compareDividerHandle"]
        XCTAssertTrue(handle.waitForExistence(timeout: 180), "Compare divider handle did not appear")
        // Wait for the 0.9s reveal glide from right edge to middle
        let midExp = expectation(for: NSPredicate(format: "value == %@", "50 percent"), evaluatedWith: handle)
        wait(for: [midExp], timeout: 5)
        Thread.sleep(forTimeInterval: 0.3)
        saveShot("viewer_\(deviceTag)_landscape_\(appearance).png", in: tui2ShotDir)

        // Verify preview decoded size (<= 2048)
        let upscaledImg = app.descendants(matching: .any)["viewerUpscaledImage"]
        XCTAssertTrue(upscaledImg.waitForExistence(timeout: 15), "viewerUpscaledImage missing")
        let sizeMatchExp = expectation(for: NSPredicate(format: "value MATCHES %@", "[0-9]+x[0-9]+"), evaluatedWith: upscaledImg)
        wait(for: [sizeMatchExp], timeout: 30)
        let previewSize = upscaledImg.value as? String ?? ""
        let previewDims = previewSize.split(separator: "x").compactMap { Int($0) }
        XCTAssertEqual(previewDims.count, 2, "Invalid preview size: \(previewSize)")
        let previewMax = previewDims.max() ?? 0
        XCTAssertLessThanOrEqual(previewMax, 2048, "Preview decode exceeded 2048: \(previewSize)")

        // Ensure Zoom HUD is visible, then zoom via HUD "+" and "1:1"
        let pctLabel = app.staticTexts["tabletZoomPercentLabel"]
        let beforeZoomPct = pctLabel.exists ? pctLabel.label : ""
        tapZoomHUDButton("tabletZoomInButton", in: app)
        if !beforeZoomPct.isEmpty && pctLabel.label == beforeZoomPct {
            tapZoomHUDButton("tabletZoomInButton", in: app)
        }

        // Wait for full-res decoded size (> previewMax)
        let largerPredicate = NSPredicate { _, _ in
            let current = upscaledImg.value as? String ?? ""
            let dims = current.split(separator: "x").compactMap { Int($0) }
            return dims.count == 2 && (dims.max() ?? 0) > previewMax
        }
        let largerExp = expectation(for: largerPredicate, evaluatedWith: upscaledImg)
        // The full-resolution decode of a 6400x4800 result can take minutes on a simulator while the Mac is busy.
        wait(for: [largerExp], timeout: 180)

        tapZoomHUDButton("tabletZoomActualButton", in: app)
        XCTAssertTrue(pctLabel.waitForExistence(timeout: 5))
        let actualPctExp = expectation(for: NSPredicate(format: "label == %@", "100 %"), evaluatedWith: pctLabel)
        wait(for: [actualPctExp], timeout: 5)
        Thread.sleep(forTimeInterval: 0.4)
        saveShot("viewer_\(deviceTag)_zoomed_1to1_\(appearance).png", in: tui2ShotDir)

        // Tap Fit -> viewerUpscaledImage returns to preview size
        tapZoomHUDButton("tabletZoomFitButton", in: app)
        let returnExp = expectation(for: NSPredicate(format: "value == %@", previewSize), evaluatedWith: upscaledImg)
        wait(for: [returnExp], timeout: 30)

        // Chips snap the line
        let origChip = app.buttons["originalChipButton"]
        let enhChip = app.buttons["enhancedChipButton"]
        XCTAssertTrue(origChip.waitForExistence(timeout: 5), "Original chip missing")
        XCTAssertTrue(enhChip.waitForExistence(timeout: 5), "Enhanced chip missing")

        origChip.tap()
        let origSnapExp = expectation(for: NSPredicate(format: "value == %@", "100 percent"), evaluatedWith: handle)
        wait(for: [origSnapExp], timeout: 5)
        origChip.tap()
        let origReturnExp = expectation(for: NSPredicate(format: "value == %@", "50 percent"), evaluatedWith: handle)
        wait(for: [origReturnExp], timeout: 5)

        enhChip.tap()
        let enhSnapExp = expectation(for: NSPredicate(format: "value == %@", "0 percent"), evaluatedWith: handle)
        wait(for: [enhSnapExp], timeout: 5)
        enhChip.tap()
        let enhReturnExp = expectation(for: NSPredicate(format: "value == %@", "50 percent"), evaluatedWith: handle)
        wait(for: [enhReturnExp], timeout: 5)

        // Save turns into Saved (disabled)
        let saveBtn = app.buttons["tabletViewerSaveButton"]
        XCTAssertTrue(saveBtn.waitForExistence(timeout: 5), "Save button missing in viewer header")
        XCTAssertTrue(saveBtn.isEnabled)
        saveBtn.tap()
        allowPhotoAccessIfPrompted(springboard: springboard)

        let savedExp = expectation(for: NSPredicate(format: "label == %@", "Saved"), evaluatedWith: saveBtn)
        wait(for: [savedExp], timeout: 15)
        XCTAssertFalse(saveBtn.isEnabled, "Saved button should be disabled")

        // Rotate to portrait -> verify Viewer tab keeps selection & divider
        XCUIDevice.shared.orientation = .portrait
        Thread.sleep(forTimeInterval: 1.0)
        XCTAssertTrue(app.buttons["tabletTabViewer"].waitForExistence(timeout: 5), "Portrait Viewer tab missing")
        XCTAssertTrue(handle.waitForExistence(timeout: 5), "Divider handle missing after rotating to portrait")
        ensureZoomHUDVisible(app: app)
        Thread.sleep(forTimeInterval: 0.3)
        saveShot("viewer_\(deviceTag)_portrait_\(appearance).png", in: tui2ShotDir)
    }

    @MainActor
    func testTabletEnhanceRecipesLibraryAndMeters() throws {
        try FileManager.default.createDirectory(atPath: tui3ShotDir, withIntermediateDirectories: true)
        let appearance = ProcessInfo.processInfo.environment["SHOT_APPEARANCE"] ?? "light"
        let deviceTag = ProcessInfo.processInfo.environment["SHOT_DEVICE"] ?? "ipad13"

        XCUIDevice.shared.orientation = .landscapeLeft
        let app = XCUIApplication()
        app.launch()
        Thread.sleep(forTimeInterval: 0.6)

        cleanExistingResultsIfNeeded(app: app)

        // Ensure Enhance pane is reachable (in tab mode on narrower devices, switch to Enhance tab)
        let enhanceTab = app.buttons["tabletTabEnhance"]
        let isTabModeAtLaunch = enhanceTab.exists
        if isTabModeAtLaunch {
            enhanceTab.tap()
            Thread.sleep(forTimeInterval: 0.4)
        }

        // 1. Switch Quality to Fast and back to Best
        let fastCard = app.buttons["tabletQualityCard_fast"]
        let bestCard = app.buttons["tabletQualityCard_best"]
        XCTAssertTrue(fastCard.waitForExistence(timeout: 10), "Fast quality card missing")
        XCTAssertTrue(bestCard.waitForExistence(timeout: 5), "Best quality card missing")
        fastCard.tap()
        XCTAssertTrue(app.descendants(matching: .any)["tabletPlanRow_fast"].waitForExistence(timeout: 4), "Plan row did not switch to Fast")
        bestCard.tap()
        XCTAssertTrue(app.descendants(matching: .any)["tabletPlanRow_best"].waitForExistence(timeout: 4), "Plan row did not switch back to Best")

        // 2. Turn on JPEG artifacts and check plan shows Fix JPEG above Upscale x4 + Auto strength note
        let jpegChip = app.buttons["tabletFixJpegButton"]
        XCTAssertTrue(jpegChip.waitForExistence(timeout: 5), "JPEG artifacts fix chip missing")
        jpegChip.tap()
        XCTAssertTrue(app.staticTexts["tabletJpegStrengthNote"].waitForExistence(timeout: 4), "FBCNN Auto strength caption missing")
        let fixJpegRow = app.descendants(matching: .any)["tabletPlanRow_fbcnn"]
        let upscaleRow = app.descendants(matching: .any)["tabletPlanRow_best"]
        XCTAssertTrue(fixJpegRow.waitForExistence(timeout: 4), "Fix JPEG plan row missing")
        XCTAssertTrue(upscaleRow.waitForExistence(timeout: 4), "Upscale x4 plan row missing")
        XCTAssertLessThan(fixJpegRow.frame.minY, upscaleRow.frame.minY, "Fix JPEG must appear above Upscale x4")

        // 3. Save a recipe, reset, apply it, rename and delete it
        let saveRecipeBtn = app.buttons["tabletSaveRecipeButton"]
        XCTAssertTrue(saveRecipeBtn.waitForExistence(timeout: 4), "Save as Recipe button missing")
        saveRecipeBtn.tap()
        let saveAlert = app.alerts["Save as Recipe"]
        XCTAssertTrue(saveAlert.waitForExistence(timeout: 5), "Save as Recipe alert missing")
        let nameField = saveAlert.textFields.firstMatch
        XCTAssertTrue(nameField.waitForExistence(timeout: 3))
        nameField.tap()
        nameField.typeText("Old scans")
        saveAlert.buttons["Save"].tap()

        let savedRecipeBtn = app.buttons.matching(NSPredicate(format: "label CONTAINS 'Old scans'")).firstMatch
        XCTAssertTrue(savedRecipeBtn.waitForExistence(timeout: 5), "Saved recipe row 'Old scans' missing")

        // Turn off JPEG artifacts and switch to Fast, then apply recipe to verify restore
        jpegChip.tap()
        fastCard.tap()
        XCTAssertFalse(fixJpegRow.exists, "Fix JPEG row should disappear when turned off")
        savedRecipeBtn.tap()
        XCTAssertTrue(fixJpegRow.waitForExistence(timeout: 4), "Applying recipe should re-enable Fix JPEG")
        XCTAssertTrue(upscaleRow.waitForExistence(timeout: 4), "Applying recipe should restore Best model")

        // Rename recipe via recipe menu
        let recipeMenu = app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH 'tabletRecipeMenu_'")).firstMatch
        XCTAssertTrue(recipeMenu.waitForExistence(timeout: 4), "Recipe options menu missing")
        recipeMenu.tap()
        let renameMenuBtn = app.buttons["Rename\u{2026}"]
        XCTAssertTrue(renameMenuBtn.waitForExistence(timeout: 4), "Rename menu item missing")
        renameMenuBtn.tap()

        let renameAlert = app.alerts["Rename Recipe"]
        XCTAssertTrue(renameAlert.waitForExistence(timeout: 5), "Rename Recipe alert missing")
        let renameField = renameAlert.textFields.firstMatch
        XCTAssertTrue(renameField.waitForExistence(timeout: 3))
        renameField.tap()
        if let existing = renameField.value as? String, !existing.isEmpty {
            let deleteStr = String(repeating: XCUIKeyboardKey.delete.rawValue, count: existing.count)
            renameField.typeText(deleteStr)
        }
        renameField.typeText("Clean scan")
        renameAlert.buttons["Save"].tap()

        let renamedRecipeBtn = app.buttons.matching(NSPredicate(format: "label CONTAINS 'Clean scan'")).firstMatch
        XCTAssertTrue(renamedRecipeBtn.waitForExistence(timeout: 5), "Renamed recipe row missing")

        // Delete recipe
        recipeMenu.tap()
        let deleteMenuBtn = app.buttons["Delete"]
        XCTAssertTrue(deleteMenuBtn.waitForExistence(timeout: 4), "Delete menu item missing")
        deleteMenuBtn.tap()
        XCTAssertTrue(renamedRecipeBtn.waitForNonExistence(timeout: 4), "Deleted recipe row still present")

        // Turn JPEG artifacts back off so batch runs fast with Best
        jpegChip.tap()

        // 4. Open Model Library and check the 3 groups
        let changeBtn = app.buttons["tabletModelChangeButton"]
        XCTAssertTrue(changeBtn.waitForExistence(timeout: 4), "Change... button missing")
        changeBtn.tap()

        XCTAssertTrue(app.staticTexts["tabletLibraryGroup_upscalePhotos"].waitForExistence(timeout: 6), "Upscale photos group missing")
        XCTAssertTrue(app.staticTexts["tabletLibraryGroup_illustration"].waitForExistence(timeout: 4), "Illustration group missing")
        XCTAssertTrue(app.staticTexts["tabletLibraryGroup_fixNoiseAndJpeg"].waitForExistence(timeout: 4), "Fix noise and JPEG group missing")
        Thread.sleep(forTimeInterval: 0.4)
        saveShot("model_library_\(deviceTag)_\(appearance).png", in: tui3ShotDir)

        // Pick SPAN x4 NomosUni, tap Use for This Job, verify Back to Best, then reset
        let nomosSpanRow = app.buttons["tabletLibraryRow_nomos_span"]
        XCTAssertTrue(nomosSpanRow.waitForExistence(timeout: 4), "SPAN x4 NomosUni row missing")
        nomosSpanRow.tap()
        let useBtn = app.buttons["tabletLibraryUseButton"]
        XCTAssertTrue(useBtn.waitForExistence(timeout: 4), "Use for This Job button missing")
        useBtn.tap()

        let backToBest = app.buttons["tabletModelBackToBestButton"]
        XCTAssertTrue(backToBest.waitForExistence(timeout: 5), "Back to Best link did not appear after manual model choice")
        backToBest.tap()
        XCTAssertTrue(backToBest.waitForNonExistence(timeout: 4), "Back to Best link should disappear after reset")

        // 5. Check Chips rows exist with their second lines
        let neRow = app.descendants(matching: .any)["tabletLaneRow_neuralEngine"]
        let gpuRow = app.descendants(matching: .any)["tabletLaneRow_gpu"]
        if !neRow.exists || !neRow.isHittable {
            app.swipeUp()
        }
        XCTAssertTrue(neRow.waitForExistence(timeout: 4), "Neural Engine chips row missing")
        XCTAssertTrue(gpuRow.waitForExistence(timeout: 4), "GPU chips row missing")
        XCTAssertTrue(neRow.label.contains(","), "Neural Engine row label should include second line: \(neRow.label)")
        XCTAssertTrue(gpuRow.label.contains(","), "GPU row label should include second line: \(gpuRow.label)")

        // Capture Enhance landscape screenshot (only in three-pane landscape)
        if !isTabModeAtLaunch {
            saveShot("enhance_\(deviceTag)_landscape_\(appearance).png", in: tui3ShotDir)
        }

        // 6. Import 3 photos, run batch, capture running meters, verify "Last batch" line
        let addBtn = isTabModeAtLaunch ? app.buttons["tabletTopBarAddButton"] : app.buttons["tabletMediaAddButton"]
        XCTAssertTrue(addBtn.waitForExistence(timeout: 5))
        addBtn.tap()
        let photosMenuItem = app.buttons["tabletAddPhotosMenuItem"]
        XCTAssertTrue(photosMenuItem.waitForExistence(timeout: 5))
        photosMenuItem.tap()
        XCTAssertTrue(selectPhotosInSystemPicker(app: app, count: 3), "Could not select 3 photos")

        let startAllBtn = app.buttons["tabletStartAllButton"]
        XCTAssertTrue(startAllBtn.waitForExistence(timeout: 15), "Start All 3 button missing")
        startAllBtn.tap()

        let statusTitle = app.staticTexts["tabletStatusTitle"]
        let runningPredicate = NSPredicate(format: "label BEGINSWITH 'Upscaling' OR label BEGINSWITH 'Restoring' OR label == 'Complete'")
        let runningExp = expectation(for: runningPredicate, evaluatedWith: statusTitle)
        wait(for: [runningExp], timeout: 30)
        if statusTitle.label != "Complete" {
            Thread.sleep(forTimeInterval: 0.6)
            saveShot("running_batch_meters_\(deviceTag)_\(appearance).png", in: tui3ShotDir)
        }

        let completePredicate = NSPredicate(format: "label == 'Complete'")
        let exp = expectation(for: completePredicate, evaluatedWith: statusTitle)
        wait(for: [exp], timeout: 180)
        Thread.sleep(forTimeInterval: 0.5)

        if isTabModeAtLaunch {
            app.buttons["tabletTabEnhance"].tap()
            Thread.sleep(forTimeInterval: 0.4)
        }
        let lastBatchLine = app.staticTexts["tabletLastBatchLine"]
        if !lastBatchLine.exists {
            app.swipeUp()
        }
        XCTAssertTrue(lastBatchLine.waitForExistence(timeout: 5), "Last batch line missing after batch completion")
        XCTAssertTrue(lastBatchLine.label.hasPrefix("Last batch:"), "Unexpected Last batch text: \(lastBatchLine.label)")

        // 7. Portrait Enhance tab two columns screenshot (on 13" or 11" iPad)
        XCUIDevice.shared.orientation = .portrait
        Thread.sleep(forTimeInterval: 1.0)
        if app.buttons["tabletTabEnhance"].waitForExistence(timeout: 5) {
            app.buttons["tabletTabEnhance"].tap()
            Thread.sleep(forTimeInterval: 0.5)
            saveShot("enhance_\(deviceTag)_portrait_\(appearance).png", in: tui3ShotDir)
        }
    }

    @MainActor
    private func cleanExistingResultsIfNeeded(app: XCUIApplication) {
        if app.staticTexts["tabletEmptyHeadline"].exists { return }
        let moreMenu = app.buttons["tabletEnhanceMoreMenu"]
        let topMore = app.buttons["tabletTopBarMoreMenu"]
        let menuBtn = moreMenu.exists ? moreMenu : topMore
        guard menuBtn.waitForExistence(timeout: 3) else { return }
        menuBtn.tap()
        let deleteAll = app.buttons["tabletDeleteAllResultsMenuItem"]
        if deleteAll.waitForExistence(timeout: 3) && deleteAll.isEnabled {
            deleteAll.tap()
            let confirm = app.buttons["Delete All"]
            if confirm.waitForExistence(timeout: 3) {
                confirm.tap()
                Thread.sleep(forTimeInterval: 0.4)
            }
        } else {
            app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        }
    }

    @MainActor
    private func selectPhotosInSystemPicker(app: XCUIApplication, count: Int) -> Bool {
        print("PHOTO_PICKER_HIERARCHY_BEGIN\n\(app.debugDescription)\nPHOTO_PICKER_HIERARCHY_END")
        let candidateImages = app.images.matching(
            NSPredicate(format: "label CONTAINS ',' AND (label CONTAINS[c] 'Photo' OR label CONTAINS[c] 'Image' OR label CONTAINS[c] 'PXG' OR label CONTAINS[c] 'Wallpaper')")
        )
        let candidateCells = app.collectionViews.cells.matching(
            NSPredicate(format: "identifier == 'PXAssetsCollectionViewCell' OR label CONTAINS[c] 'Photo' OR label CONTAINS ','")
        )

        var tapped = 0
        if candidateImages.firstMatch.waitForExistence(timeout: 15) {
            let total = candidateImages.count
            let targetCount = min(count, total)
            let step = (count > 1 && total >= count * 2) ? 2 : 1
            for i in 0..<targetCount {
                let idx = min(i * step, total - 1)
                candidateImages.element(boundBy: idx).tap()
                tapped += 1
            }
        } else if candidateCells.firstMatch.waitForExistence(timeout: 4) {
            let total = candidateCells.count
            let targetCount = min(count, total)
            let step = (count > 1 && total >= count * 2) ? 2 : 1
            for i in 0..<targetCount {
                let idx = min(i * step, total - 1)
                candidateCells.element(boundBy: idx).tap()
                tapped += 1
            }
        }
        guard tapped > 0 else { return false }

        let addButton = app.buttons["Add"]
        if addButton.waitForExistence(timeout: 5) {
            addButton.tap()
            return true
        }
        let pickerAdd = app.buttons.matching(NSPredicate(format: "label BEGINSWITH 'Add' OR label == 'checkmark' OR identifier == 'Done'")).firstMatch
        if pickerAdd.waitForExistence(timeout: 3) {
            pickerAdd.tap()
            return true
        }
        return false
    }

    @MainActor
    private func tapZoomHUDButton(_ identifier: String, in app: XCUIApplication) {
        let button = app.buttons[identifier]
        ensureZoomHUDVisible(app: app)
        if !button.exists {
            XCTAssertTrue(button.waitForExistence(timeout: 2), "\(identifier) HUD button missing")
        }
        button.tap()
    }

    @MainActor
    private func ensureZoomHUDVisible(app: XCUIApplication) {
        let zoomIn = app.buttons["tabletZoomInButton"]
        if zoomIn.exists && zoomIn.isHittable { return }
        let stage = app.otherElements["viewerGestureArea"]
        if stage.exists {
            stage.tap()
        } else {
            app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        }
        for _ in 0..<8 {
            if zoomIn.exists && zoomIn.isHittable { return }
            Thread.sleep(forTimeInterval: 0.15)
        }
    }

    @MainActor
    private func selectOnePhotoPreferringDetailedFixture(app: XCUIApplication) -> Bool {
        let detailed = app.images.matching(NSPredicate(format: "label CONTAINS '2099' AND label CONTAINS[c] 'Photo'")).firstMatch
        if detailed.waitForExistence(timeout: 4) && detailed.isHittable {
            detailed.tap()
            let addButton = app.buttons["Add"]
            if addButton.waitForExistence(timeout: 5) {
                addButton.tap()
                return true
            }
        }
        return selectPhotosInSystemPicker(app: app, count: 1)
    }

    @MainActor
    private func allowPhotoAccessIfPrompted(springboard: XCUIApplication) {
        let allowButtons = [
            "Allow Full Access",
            "Allow Access to All Photos",
            "OK",
            "Allow"
        ]
        for _ in 0..<3 {
            var handled = false
            for title in allowButtons {
                let alertBtn = springboard.alerts.buttons[title].firstMatch
                if alertBtn.waitForExistence(timeout: 1.0) {
                    alertBtn.tap()
                    handled = true
                    break
                }
            }
            if !handled { break }
        }
    }

    @MainActor
    private func saveShot(_ filename: String) {
        saveShot(filename, in: shotDir)
    }

    @MainActor
    private func saveShot(_ filename: String, in directory: String) {
        let shot = XCUIScreen.main.screenshot()
        let path = (directory as NSString).appendingPathComponent(filename)
        try? shot.pngRepresentation.write(to: URL(fileURLWithPath: path))
        let attachment = XCTAttachment(screenshot: shot)
        attachment.name = filename
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
