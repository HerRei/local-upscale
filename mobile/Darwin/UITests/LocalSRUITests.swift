import XCTest

final class LocalSRUITests: XCTestCase {
    private let shotDir = "/private/tmp/claude-501/-Users-hermesheiniger/619da889-5d3f-4724-b759-60d636ce5866/scratchpad/ui1/shots"

    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    @MainActor
    func testEndToEndFlow() throws {
        try FileManager.default.createDirectory(atPath: shotDir, withIntermediateDirectories: true)
        let suffix = ProcessInfo.processInfo.environment["SHOT_APPEARANCE"] ?? "light"

        let app = XCUIApplication()
        app.launch()

        let chooseButton = app.buttons["choosePhotosButton"]
        XCTAssertTrue(chooseButton.waitForExistence(timeout: 15))
        XCTAssertTrue(app.staticTexts["homeEmptyLabel"].exists)
        saveShot("ios_home_empty_\(suffix).png", app: app)

        // Open About sheet and verify notices + compute note
        app.buttons["aboutButton"].tap()
        XCTAssertTrue(app.staticTexts["Everything runs on this device. Nothing is uploaded."].waitForExistence(timeout: 5))
        Thread.sleep(forTimeInterval: 0.4)
        saveShot("ios_about_\(suffix).png", app: app)
        app.buttons["aboutDoneButton"].tap()
        XCTAssertTrue(chooseButton.waitForExistence(timeout: 5))

        // Open system photo picker and pick the first photo
        chooseButton.tap()

        let photoSelected = selectPhotoInSystemPicker(app: app)
        XCTAssertTrue(photoSelected, "Could not select a photo in system PhotosPicker")

        // Progress sheet should appear while upscaling; settle transition before capture
        if app.navigationBars["Upscaling"].waitForExistence(timeout: 8) {
            Thread.sleep(forTimeInterval: 0.6)
            saveShot("ios_progress_\(suffix).png", app: app)
        }

        // Wait for Home Recent section after batch completes
        let recentText = app.staticTexts["Recent"]
        XCTAssertTrue(recentText.waitForExistence(timeout: 90), "Recent results did not appear after upscaling")
        Thread.sleep(forTimeInterval: 0.5)
        saveShot("ios_home_results_\(suffix).png", app: app)

        // Open Result
        let recentCell = app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH 'recentResult_'")).firstMatch
        XCTAssertTrue(recentCell.waitForExistence(timeout: 5))
        recentCell.tap()

        let summary = app.staticTexts["resultSummaryLabel"]
        XCTAssertTrue(summary.waitForExistence(timeout: 10))
        Thread.sleep(forTimeInterval: 0.4)
        saveShot("ios_result_\(suffix).png", app: app)

        // Drag compare divider slightly right
        let handle = app.otherElements["compareDividerHandle"].firstMatch
        if handle.exists {
            let start = handle.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5))
            let end = app.windows.firstMatch.coordinate(withNormalizedOffset: CGVector(dx: 0.72, dy: 0.45))
            start.press(forDuration: 0.05, thenDragTo: end)
        }

        // Save to Photos
        let saveButton = app.buttons["saveToPhotosButton"]
        XCTAssertTrue(saveButton.exists)
        saveButton.tap()

        let savedButton = app.buttons.matching(NSPredicate(format: "label == 'Saved'")).firstMatch
        XCTAssertTrue(savedButton.waitForExistence(timeout: 15), "Save button did not transition to Saved")
        saveShot("ios_result_saved_\(suffix).png", app: app)

        // Switch model on Result screen to Fast and verify rerun completes
        let fastSegment = app.segmentedControls["resultModelPicker"].buttons["Fast"]
        XCTAssertTrue(fastSegment.exists)
        fastSegment.tap()
        let updatedPredicate = NSPredicate(format: "label CONTAINS 'Fast'")
        let exp = expectation(for: updatedPredicate, evaluatedWith: summary)
        wait(for: [exp], timeout: 90)
        saveShot("ios_result_fast_\(suffix).png", app: app)

        // Go back to Home and open About again to verify prepared model verdicts appear
        app.navigationBars.buttons.firstMatch.tap()
        XCTAssertTrue(app.buttons["aboutButton"].waitForExistence(timeout: 5))
        app.buttons["aboutButton"].tap()
        XCTAssertTrue(app.staticTexts["Everything runs on this device. Nothing is uploaded."].waitForExistence(timeout: 5))
        Thread.sleep(forTimeInterval: 0.4)
        saveShot("ios_about_prepared_\(suffix).png", app: app)
        app.buttons["aboutDoneButton"].tap()
    }

    @MainActor
    private func selectPhotoInSystemPicker(app: XCUIApplication) -> Bool {
        // PHPicker runs out-of-process on iOS; query both app tree and system picker bundle.
        let candidateImages = app.images.matching(NSPredicate(format: "label CONTAINS[c] 'Photo' OR label CONTAINS[c] 'PXG' OR identifier == 'PXAssetsCollectionViewCell'"))
        if candidateImages.firstMatch.waitForExistence(timeout: 8) {
            candidateImages.firstMatch.tap()
        } else {
            let pickerCells = app.cells
            if pickerCells.firstMatch.waitForExistence(timeout: 5) {
                pickerCells.firstMatch.tap()
            } else {
                // Fallback: inspect scrollView cells inside picker host
                let anyPickerElement = app.scrollViews.firstMatch
                if anyPickerElement.waitForExistence(timeout: 5) {
                    anyPickerElement.coordinate(withNormalizedOffset: CGVector(dx: 0.16, dy: 0.25)).tap()
                } else {
                    return false
                }
            }
        }

        // Multi-select PHPicker shows an Add button; tap it if present.
        let addButton = app.buttons["Add"]
        if addButton.waitForExistence(timeout: 5) {
            addButton.tap()
            return true
        }
        let pickerAdd = app.navigationBars.buttons.matching(NSPredicate(format: "label BEGINSWITH 'Add'")).firstMatch
        if pickerAdd.waitForExistence(timeout: 3) {
            pickerAdd.tap()
            return true
        }
        return false
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
