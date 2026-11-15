import XCTest

final class LocalSRUITests: XCTestCase {
    private let shotDir = "/private/tmp/claude-501/-Users-hermesheiniger/619da889-5d3f-4724-b759-70d636ce5866/scratchpad/ui2/shots"

    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    @MainActor
    func testEndToEndFlow() throws {
        try FileManager.default.createDirectory(atPath: shotDir, withIntermediateDirectories: true)
        let suffix = ProcessInfo.processInfo.environment["SHOT_APPEARANCE"] ?? "light"

        let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        let app = XCUIApplication()
        app.launch()

        // Ensure clean starting state if previous run left items
        let chooseButton = app.buttons["choosePhotosButton"]
        XCTAssertTrue(chooseButton.waitForExistence(timeout: 15))
        if !app.staticTexts["homeEmptyLabel"].exists {
            let moreBtn = app.buttons["homeMoreMenu"]
            if moreBtn.waitForExistence(timeout: 3) {
                moreBtn.tap()
                let deleteAll = app.buttons["deleteAllMenuButton"]
                if deleteAll.waitForExistence(timeout: 3) && deleteAll.isEnabled {
                    deleteAll.tap()
                    let confirm = app.buttons["Delete All"]
                    if confirm.waitForExistence(timeout: 3) {
                        confirm.tap()
                    }
                } else {
                    app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
                }
            }
        }

        XCTAssertTrue(app.staticTexts["homeEmptyLabel"].waitForExistence(timeout: 5))
        saveShot("ios_home_empty_\(suffix).png", app: app)

        // Open About sheet via Home More menu, then push Licences
        app.buttons["homeMoreMenu"].tap()
        let aboutButton = app.buttons["aboutButton"]
        XCTAssertTrue(aboutButton.waitForExistence(timeout: 4))
        aboutButton.tap()
        XCTAssertTrue(app.staticTexts["Works offline"].waitForExistence(timeout: 5))
        Thread.sleep(forTimeInterval: 0.4)
        saveShot("ios_about_\(suffix).png", app: app)

        let licencesLink = app.buttons["aboutLicencesLink"]
        XCTAssertTrue(licencesLink.waitForExistence(timeout: 4))
        licencesLink.tap()
        XCTAssertTrue(app.navigationBars["Licences"].waitForExistence(timeout: 5))
        Thread.sleep(forTimeInterval: 0.3)
        saveShot("ios_licences_\(suffix).png", app: app)
        app.navigationBars["Licences"].buttons.firstMatch.tap()
        XCTAssertTrue(app.buttons["aboutDoneButton"].waitForExistence(timeout: 4))
        app.buttons["aboutDoneButton"].tap()
        XCTAssertTrue(chooseButton.waitForExistence(timeout: 5))

        // Pick 1 photo -> capture stage if visible -> single photo auto-opens Result
        chooseButton.tap()
        XCTAssertTrue(selectPhotosInSystemPicker(app: app, count: 1), "Could not select 1 photo in PHPicker")

        if app.buttons["cancelBatchButton"].waitForExistence(timeout: 5) {
            Thread.sleep(forTimeInterval: 0.3)
            saveShot("ios_stage_single_\(suffix).png", app: app)
        }

        let summary = app.staticTexts["resultSummaryLabel"]
        XCTAssertTrue(summary.waitForExistence(timeout: 90), "Single photo did not auto-open Result viewer")
        Thread.sleep(forTimeInterval: 1.4)
        saveShot("ios_result_mid_\(suffix).png", app: app)

        // Test Original / Upscaled chips (tap once to snap to side, tap again to return to mid)
        let origChip = app.buttons["originalChipButton"]
        let upChip = app.buttons["upscaledChipButton"]
        if origChip.waitForExistence(timeout: 3) {
            origChip.tap()
            Thread.sleep(forTimeInterval: 0.45)
            origChip.tap()
            Thread.sleep(forTimeInterval: 0.45)
        }
        if upChip.waitForExistence(timeout: 3) {
            upChip.tap()
            Thread.sleep(forTimeInterval: 0.45)
            upChip.tap()
            Thread.sleep(forTimeInterval: 0.45)
        }

        // Save to Photos -> handle permission prompt if shown -> verify Saved to Photos
        let saveButton = app.buttons["saveToPhotosButton"]
        XCTAssertTrue(saveButton.waitForExistence(timeout: 4))
        saveButton.tap()

        allowPhotoAccessIfPrompted(springboard: springboard)

        let savedButton = app.buttons.matching(NSPredicate(format: "label CONTAINS 'Saved to Photos' OR label == 'Saved to Photos'")).firstMatch
        XCTAssertTrue(savedButton.waitForExistence(timeout: 15), "Save button did not transition to Saved to Photos")
        Thread.sleep(forTimeInterval: 0.3)
        saveShot("ios_result_saved_\(suffix).png", app: app)

        // Open result more menu -> capture menu -> rerun with Fast
        let resultMenu = app.buttons["resultMoreMenu"]
        XCTAssertTrue(resultMenu.waitForExistence(timeout: 4))
        resultMenu.tap()
        let fastMenuBtn = app.buttons["Fast"]
        XCTAssertTrue(fastMenuBtn.waitForExistence(timeout: 4))
        saveShot("ios_result_menu_\(suffix).png", app: app)
        fastMenuBtn.tap()

        let updatedPredicate = NSPredicate(format: "label CONTAINS 'Fast'")
        let exp = expectation(for: updatedPredicate, evaluatedWith: summary)
        wait(for: [exp], timeout: 90)
        Thread.sleep(forTimeInterval: 1.2)
        saveShot("ios_result_fast_\(suffix).png", app: app)

        // Save the newly re-upscaled Fast result so relaunch test verifies persistence
        XCTAssertTrue(saveButton.waitForExistence(timeout: 4))
        saveButton.tap()
        allowPhotoAccessIfPrompted(springboard: springboard)
        XCTAssertTrue(savedButton.waitForExistence(timeout: 15), "Fast result did not transition to Saved to Photos")

        // Go back to Home and verify About shows prepared verdicts
        app.navigationBars.buttons.firstMatch.tap()
        XCTAssertTrue(chooseButton.waitForExistence(timeout: 5))
        app.buttons["homeMoreMenu"].tap()
        XCTAssertTrue(aboutButton.waitForExistence(timeout: 4))
        aboutButton.tap()
        XCTAssertTrue(app.staticTexts["Works offline"].waitForExistence(timeout: 5))
        Thread.sleep(forTimeInterval: 0.4)
        saveShot("ios_about_prepared_\(suffix).png", app: app)
        app.buttons["aboutDoneButton"].tap()
        XCTAssertTrue(chooseButton.waitForExistence(timeout: 5))

        // Verify "Saved to Photos" survives relaunch
        app.terminate()
        app.launch()
        XCTAssertTrue(chooseButton.waitForExistence(timeout: 10))
        let firstTile = app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH 'recentResult_'")).firstMatch
        XCTAssertTrue(firstTile.waitForExistence(timeout: 5))
        firstTile.tap()
        XCTAssertTrue(summary.waitForExistence(timeout: 5))
        XCTAssertTrue(savedButton.waitForExistence(timeout: 5), "Saved to Photos did not persist across relaunch")
        saveShot("ios_result_relaunch_saved_\(suffix).png", app: app)
        app.navigationBars.buttons.firstMatch.tap()
        XCTAssertTrue(chooseButton.waitForExistence(timeout: 5))

        // Pick 5 photos for a multi-photo batch (creates 6 total results!)
        chooseButton.tap()
        XCTAssertTrue(selectPhotosInSystemPicker(app: app, count: 5), "Could not select 5 photos in PHPicker")

        if app.buttons["cancelBatchButton"].waitForExistence(timeout: 8) || app.staticTexts["batchCountLabel"].waitForExistence(timeout: 2) {
            Thread.sleep(forTimeInterval: 1.0)
            saveShot("ios_home_batch_\(suffix).png", app: app)
        }

        // Wait for batch summary card
        let summaryDone = app.buttons["dismissSummaryButton"]
        XCTAssertTrue(summaryDone.waitForExistence(timeout: 300), "Batch summary card did not appear")
        Thread.sleep(forTimeInterval: 0.4)
        saveShot("ios_batch_summary_\(suffix).png", app: app)

        // Dismiss summary card and verify 6-result grid
        let dismissBtn = app.buttons["dismissSummaryButton"]
        if dismissBtn.exists {
            dismissBtn.tap()
        }
        Thread.sleep(forTimeInterval: 0.4)
        saveShot("ios_home_6results_\(suffix).png", app: app)

        // Capture Delete All confirmation dialog (then dismiss)
        app.buttons["homeMoreMenu"].tap()
        let deleteAllMenu = app.buttons["deleteAllMenuButton"]
        XCTAssertTrue(deleteAllMenu.waitForExistence(timeout: 4))
        deleteAllMenu.tap()
        let deleteAllConfirm = app.buttons["Delete All"]
        XCTAssertTrue(deleteAllConfirm.waitForExistence(timeout: 4))
        Thread.sleep(forTimeInterval: 0.3)
        saveShot("ios_delete_all_confirm_\(suffix).png", app: app)
        if app.buttons["Cancel"].exists {
            app.buttons["Cancel"].tap()
        } else {
            app.coordinate(withNormalizedOffset: CGVector(dx: 0.1, dy: 0.5)).tap()
        }
        XCTAssertTrue(app.buttons["choosePhotosButton"].waitForExistence(timeout: 4))
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
        if candidateImages.firstMatch.waitForExistence(timeout: 8) {
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
        guard tapped > 0 else {
            return false
        }

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
                let alertBtn = springboard.alerts.buttons[title]
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
    private func saveShot(_ filename: String, app: XCUIApplication) {
        let shot = XCUIScreen.main.screenshot()
        let path = (shotDir as NSString).appendingPathComponent(filename)
        try? shot.pngRepresentation.write(to: URL(fileURLWithPath: path))
        let attachment = XCTAttachment(screenshot: shot)
        attachment.name = filename
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
