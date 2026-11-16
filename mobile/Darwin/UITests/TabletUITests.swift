import XCTest
import UIKit

final class TabletUITests: XCTestCase {
    private let shotDir = "/private/tmp/claude-501/-Users-hermesheiniger/619da889-5d3f-4724-b759-70d636ce5866/scratchpad/tui1/shots"
    private let tui2ShotDir = "/private/tmp/claude-501/-Users-hermesheiniger/619da889-5d3f-4724-b759-70d636ce5866/scratchpad/tui2/shots"

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
        wait(for: [largerExp], timeout: 60)

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
