import XCTest
import UIKit

final class TabletUITests: XCTestCase {
    private let shotDir = "/private/tmp/claude-501/-Users-hermesheiniger/619da889-5d3f-4724-b759-70d636ce5866/scratchpad/tui1/shots"

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
    private func saveShot(_ filename: String) {
        let shot = XCUIScreen.main.screenshot()
        let path = (shotDir as NSString).appendingPathComponent(filename)
        try? shot.pngRepresentation.write(to: URL(fileURLWithPath: path))
        let attachment = XCTAttachment(screenshot: shot)
        attachment.name = filename
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
