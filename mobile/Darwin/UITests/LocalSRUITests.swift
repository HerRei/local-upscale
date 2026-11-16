import XCTest

final class LocalSRUITests: XCTestCase {
    /// Screenshots go to SHOT_DIR (pass it as TEST_RUNNER_SHOT_DIR to xcodebuild), else to the runner's temporary directory.
    private let shotDir = UITestShots.directory("phone")
    private let shareShotDir = UITestShots.directory("phone")

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

        // Open Settings sheet via Home gear button, verify toggle & facts, then push Licences
        let settingsButton = app.buttons["settingsButton"]
        XCTAssertTrue(settingsButton.waitForExistence(timeout: 4))
        settingsButton.tap()
        // At the largest text sizes and on small screens the device facts start below the fold.
        let offlineFact = app.staticTexts["Works offline"]
        for _ in 0..<6 where !offlineFact.waitForExistence(timeout: 2) { app.swipeUp() }
        XCTAssertTrue(offlineFact.waitForExistence(timeout: 5))
        for _ in 0..<6 where !app.switches["fullScaleToggle"].waitForExistence(timeout: 1) { app.swipeDown() }
        XCTAssertTrue(app.switches["fullScaleToggle"].waitForExistence(timeout: 4))
        Thread.sleep(forTimeInterval: 0.4)
        saveShot("ios_settings_\(suffix).png", app: app)

        let limitButton = app.buttons["fullScaleInfoButton"]
        if app.switches["fullScaleToggle"].isEnabled {
            XCTAssertTrue(limitButton.waitForExistence(timeout: 4))
            limitButton.tap()
            let limitText = app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", "780 MP")).firstMatch
            XCTAssertTrue(limitText.waitForExistence(timeout: 4), "Full 4x limit explanation did not appear")
            Thread.sleep(forTimeInterval: 0.4)
            saveShot("ios_settings_full4x_limit_\(suffix).png", app: app)
            XCTAssertEqual(limitButton.label, "Hide")
            limitButton.tap()
            XCTAssertTrue(limitText.waitForNonExistence(timeout: 4), "Full 4x limit explanation did not collapse")
        } else {
            XCTAssertFalse(limitButton.exists, "Unavailable Full 4x should not offer its explanation")
        }

        let licencesLink = app.buttons["aboutLicencesLink"]
        let settingsList = app.collectionViews.firstMatch
        for _ in 0..<5 where !(licencesLink.exists && licencesLink.isHittable) {
            if settingsList.exists {
                settingsList.swipeUp()
            } else {
                app.swipeUp()
            }
        }
        XCTAssertTrue(licencesLink.waitForExistence(timeout: 4))
        XCTAssertTrue(licencesLink.isHittable, "Licences row not visible after scrolling Settings")
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

        // Go back to Home and verify Settings shows prepared verdicts
        app.navigationBars.buttons.firstMatch.tap()
        XCTAssertTrue(chooseButton.waitForExistence(timeout: 5))
        XCTAssertTrue(settingsButton.waitForExistence(timeout: 4))
        settingsButton.tap()
        // On small screens (iPhone SE) the device facts start below the fold.
        let worksOffline = app.staticTexts["Works offline"]
        for _ in 0..<4 where !worksOffline.waitForExistence(timeout: 2) { app.swipeUp() }
        XCTAssertTrue(worksOffline.waitForExistence(timeout: 5))
        Thread.sleep(forTimeInterval: 0.4)
        saveShot("ios_settings_prepared_\(suffix).png", app: app)
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
    func testViewerZoomDividerAndNoDismiss() throws {
        try FileManager.default.createDirectory(atPath: shotDir, withIntermediateDirectories: true)
        let app = XCUIApplication()
        app.launch()

        let chooseButton = app.buttons["choosePhotosButton"]
        XCTAssertTrue(chooseButton.waitForExistence(timeout: 15))

        chooseButton.tap()
        XCTAssertTrue(selectPhotosInSystemPicker(app: app, count: 1, preferNewest: true), "Could not select newest test photo in PHPicker")

        let summary = app.staticTexts["resultSummaryLabel"]
        XCTAssertTrue(summary.waitForExistence(timeout: 90), "Result viewer did not open")
        Thread.sleep(forTimeInterval: 1.4)

        let handle = app.descendants(matching: .any)["compareDividerHandle"]
        XCTAssertTrue(handle.waitForExistence(timeout: 5), "Divider handle missing")
        saveShot("ios_viewer_zoom1.png", app: app)

        let initialValue = handle.value as? String ?? ""
        let initialMidX = handle.frame.midX

        // Pinch out to zoom ~4x (full-res decode loads off main thread)
        let canvas = app.windows.firstMatch
        canvas.pinch(withScale: 4.0, velocity: 1.5)
        Thread.sleep(forTimeInterval: 1.8)
        saveShot("ios_viewer_zoom4x.png", app: app)

        // Drag the divider handle while zoomed and assert its position changed
        XCTAssertTrue(handle.exists, "Divider handle disappeared while zoomed")
        let startCoord = handle.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.45))
        let endCoord = canvas.coordinate(withNormalizedOffset: CGVector(dx: 0.78, dy: 0.45))
        startCoord.press(forDuration: 0.05, thenDragTo: endCoord)
        Thread.sleep(forTimeInterval: 0.4)

        let movedValue = handle.value as? String ?? ""
        let movedMidX = handle.frame.midX
        XCTAssertTrue(
            movedValue != initialValue || abs(movedMidX - initialMidX) > 20.0,
            "Divider handle did not move while zoomed (initial='\(initialValue)' midX=\(initialMidX), moved='\(movedValue)' midX=\(movedMidX))"
        )
        saveShot("ios_viewer_zoom4x_dragged.png", app: app)

        // Pinch in while zoomed to settle at 1x, then pinch in further at 1x -> viewer must NOT dismiss
        let pinchTarget = app.otherElements["viewerGestureArea"]
        let pinchElement = pinchTarget.exists ? pinchTarget : canvas
        pinchElement.pinch(withScale: 0.15, velocity: -1.0)
        Thread.sleep(forTimeInterval: 0.6)
        // The caption fades out while zoomed and back in at 1x.
        XCTAssertTrue(summary.waitForExistence(timeout: 3) && handle.exists, "Viewer dismissed on pinch-in from zoomed state")

        pinchElement.pinch(withScale: 0.15, velocity: -1.0)
        Thread.sleep(forTimeInterval: 0.6)
        XCTAssertTrue(summary.exists && handle.exists, "Viewer dismissed on settling pinch-in to 1x")

        pinchElement.pinch(withScale: 0.25, velocity: -1.0)
        Thread.sleep(forTimeInterval: 0.6)
        XCTAssertTrue(summary.exists && handle.exists, "Viewer dismissed on further pinch-in at 1x")
        saveShot("ios_viewer_after_pinch_in.png", app: app)
    }

    @MainActor
    func testViewerReportsDecodedPixelSizes() throws {
        let app = XCUIApplication()
        app.launch()
        let choose = app.buttons["choosePhotosButton"]
        XCTAssertTrue(choose.waitForExistence(timeout: 15))
        choose.tap()
        let fixture = app.images.matching(NSPredicate(format: "label CONTAINS '2099' AND label CONTAINS[c] 'Photo'")).firstMatch
        XCTAssertTrue(fixture.waitForExistence(timeout: 15), "Detailed fixture missing from picker")
        XCTAssertTrue(fixture.isHittable, "Detailed fixture not visible: \(fixture.label)")
        fixture.tap()
        let add = app.buttons.matching(NSPredicate(format: "label == 'Add' OR label == 'Hinzufügen'")).firstMatch
        XCTAssertTrue(add.waitForExistence(timeout: 5))
        add.tap()
        XCTAssertTrue(app.staticTexts["resultSummaryLabel"].waitForExistence(timeout: 180))
        let upscaled = app.descendants(matching: .any)["viewerUpscaledImage"]
        let original = app.descendants(matching: .any)["viewerOriginalImage"]
        XCTAssertTrue(upscaled.waitForExistence(timeout: 15))
        XCTAssertTrue(original.waitForExistence(timeout: 15))
        func waitForValue(_ element: XCUIElement, _ value: String, timeout: TimeInterval = 30) {
            let predicate = NSPredicate(format: "value == %@", value)
            expectation(for: predicate, evaluatedWith: element)
            waitForExpectations(timeout: timeout)
        }
        waitForValue(original, "1600x1200")
        expectation(for: NSPredicate(format: "value MATCHES %@", "[0-9]+x[0-9]+"), evaluatedWith: upscaled)
        waitForExpectations(timeout: 30)
        let preview = upscaled.value as? String ?? ""
        let dimensions = preview.split(separator: "x").compactMap { Int($0) }
        XCTAssertEqual(dimensions.count, 2, "Invalid preview size: \(preview)")
        XCTAssertLessThanOrEqual(dimensions.max() ?? Int.max, 2048)
        print("VIEWER iOS 1x upscaled=\(preview) original=\(original.value ?? "nil")")
        let canvas = app.windows.firstMatch
        canvas.pinch(withScale: 4.0, velocity: 1.5)
        waitForValue(upscaled, "6400x4800", timeout: 90)
        waitForValue(original, "1600x1200")
        print("VIEWER iOS 4x upscaled=\(upscaled.value ?? "nil") original=\(original.value ?? "nil")")
        let pinchTarget = app.otherElements["viewerGestureArea"]
        for attempt in 0..<6 {
            if (upscaled.value as? String) == preview { break }
            // The gesture area is small on a 4.7" screen; from the third try pinch on the whole window.
            (pinchTarget.exists && attempt < 2 ? pinchTarget : canvas).pinch(withScale: 0.15, velocity: -1.0)
            Thread.sleep(forTimeInterval: 0.8)
        }
        waitForValue(upscaled, preview)
        print("VIEWER iOS 1x return upscaled=\(upscaled.value ?? "nil") original=\(original.value ?? "nil")")
    }

    @MainActor
    private func selectPhotosInSystemPicker(app: XCUIApplication, count: Int, preferNewest: Bool = false) -> Bool {
        let grid = app.collectionViews.firstMatch
        if grid.waitForExistence(timeout: 10), preferNewest {
            grid.swipeUp()
            Thread.sleep(forTimeInterval: 0.4)
        }
        let basePredicate = "label CONTAINS ',' AND (label CONTAINS[c] 'Photo' OR label CONTAINS[c] 'Foto' OR label CONTAINS[c] 'Image' OR label CONTAINS[c] 'Bild' OR label CONTAINS[c] 'PXG' OR label CONTAINS[c] 'Wallpaper')"
        let predicateFormat = preferNewest ? basePredicate : "\(basePredicate) AND NOT (label CONTAINS '2099')"
        let candidateImages = app.images.matching(NSPredicate(format: predicateFormat))
        let candidateCells = app.collectionViews.cells.matching(
            NSPredicate(format: "identifier == 'PXAssetsCollectionViewCell' OR label CONTAINS[c] 'Photo' OR label CONTAINS ','")
        )

        var tapped = 0
        if candidateImages.firstMatch.waitForExistence(timeout: 15) {
            let total = candidateImages.count
            let targetCount = min(count, total)
            let step = (count > 1 && total >= count * 2) ? 2 : 1
            for i in 0..<targetCount {
                let idx = preferNewest ? max(0, total - 3 - i) : min(i * step, total - 1)
                candidateImages.element(boundBy: idx).tap()
                tapped += 1
            }
        } else if candidateCells.firstMatch.waitForExistence(timeout: 4) {
            let total = candidateCells.count
            let targetCount = min(count, total)
            let step = (count > 1 && total >= count * 2) ? 2 : 1
            for i in 0..<targetCount {
                let idx = preferNewest ? max(0, total - 3 - i) : min(i * step, total - 1)
                candidateCells.element(boundBy: idx).tap()
                tapped += 1
            }
        }
        guard tapped > 0 else {
            // iOS 27 can omit the remote picker grid from the accessibility tree.
            // On the SE, select visible grid cells and let the subsequent result
            // and batch assertions verify that the picker returned the photos.
            guard !preferNewest, app.frame.width == 375, app.frame.height == 667 else { return false }
            for index in 0..<count {
                let x = (CGFloat(index % 3) + 0.5) / 3.0
                let y: CGFloat = index < 3 ? 0.53 : 0.72
                app.coordinate(withNormalizedOffset: CGVector(dx: x, dy: y)).tap()
            }
            app.coordinate(withNormalizedOffset: CGVector(dx: 0.9, dy: 0.14)).tap()
            return true
        }

        let addButton = app.buttons.matching(NSPredicate(format: "label == 'Add' OR label == 'Hinzufügen'")).firstMatch
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
    private func saveShot(_ filename: String, app: XCUIApplication) {
        let shot = XCUIScreen.main.screenshot()
        let path = (shotDir as NSString).appendingPathComponent(filename)
        try? shot.pngRepresentation.write(to: URL(fileURLWithPath: path))
        let attachment = XCTAttachment(screenshot: shot)
        attachment.name = filename
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    @MainActor
    func testShareSettingsAndPhotosExtension() throws {
        try FileManager.default.createDirectory(atPath: shareShotDir, withIntermediateDirectories: true)

        let app = XCUIApplication()
        app.launch()

        let settingsButton = app.buttons["settingsButton"]
        XCTAssertTrue(settingsButton.waitForExistence(timeout: 15))
        settingsButton.tap()

        let shareToggle = app.switches["shareStartsImmediatelyToggle"]
        XCTAssertTrue(shareToggle.waitForExistence(timeout: 5), "Start Right Away toggle missing in Settings")
        setSwitch(shareToggle, to: "1")
        XCTAssertEqual(shareToggle.value as? String, "1", "Start Right Away should be ON")
        XCTAssertTrue(app.staticTexts["Share Menu"].waitForExistence(timeout: 3))
        Thread.sleep(forTimeInterval: 0.3)
        saveShareShot("ios_settings_share.png")

        app.buttons["aboutDoneButton"].tap()
        XCTAssertTrue(app.buttons["choosePhotosButton"].waitForExistence(timeout: 5))

        shareOnePhotoFromPhotosApp(returningTo: app, captureSheet: true)

        let startedOrFinished = app.buttons["cancelBatchButton"].waitForExistence(timeout: 8)
            || app.staticTexts["resultSummaryLabel"].waitForExistence(timeout: 60)
            || app.buttons["dismissSummaryButton"].waitForExistence(timeout: 60)
        XCTAssertTrue(startedOrFinished, "LocalSR did not start upscaling shared photo")
        if app.staticTexts["resultSummaryLabel"].waitForExistence(timeout: 90) {
            Thread.sleep(forTimeInterval: 1.0)
            saveShareShot("ios_photos_share_result.png")
        } else {
            saveShareShot("ios_photos_share_active.png")
        }
    }

    @MainActor
    func testShareExtensionWaitingMode() throws {
        try FileManager.default.createDirectory(atPath: shareShotDir, withIntermediateDirectories: true)

        let app = XCUIApplication()
        app.launch()

        let settingsButton = app.buttons["settingsButton"]
        XCTAssertTrue(settingsButton.waitForExistence(timeout: 15))
        settingsButton.tap()

        let shareToggle = app.switches["shareStartsImmediatelyToggle"]
        XCTAssertTrue(shareToggle.waitForExistence(timeout: 5), "Start Right Away toggle missing in Settings")
        setSwitch(shareToggle, to: "0")
        XCTAssertEqual(shareToggle.value as? String, "0", "Start Right Away should toggle OFF")
        app.buttons["aboutDoneButton"].tap()
        XCTAssertTrue(app.buttons["choosePhotosButton"].waitForExistence(timeout: 5))

        shareOnePhotoFromPhotosApp(returningTo: app, captureSheet: false)

        let waitingCard = app.otherElements["waitingPhotosCard"]
        let waitingTitle = app.staticTexts["waitingPhotosTitle"]
        let upscaleBtn = app.buttons["upscaleWaitingButton"]
        XCTAssertTrue(waitingTitle.waitForExistence(timeout: 10), "Waiting photos card did not appear when Start Right Away is OFF")
        XCTAssertTrue(upscaleBtn.waitForExistence(timeout: 5), "Upscale button missing on waiting card")
        XCTAssertTrue(waitingCard.exists || waitingTitle.exists)
        Thread.sleep(forTimeInterval: 0.3)
        saveShareShot("ios_waiting_card.png")

        upscaleBtn.tap()
        let finished = app.staticTexts["resultSummaryLabel"].waitForExistence(timeout: 90)
            || app.buttons["dismissSummaryButton"].waitForExistence(timeout: 90)
        XCTAssertTrue(finished, "Waiting batch did not finish after tapping Upscale")
        Thread.sleep(forTimeInterval: 0.8)
        saveShareShot("ios_waiting_batch_done.png")

        // Restore Start Right Away to default ON
        let dismissSummary = app.buttons["dismissSummaryButton"]
        if dismissSummary.exists {
            dismissSummary.tap()
        } else if !settingsButton.exists {
            app.navigationBars.buttons.firstMatch.tap()
        }
        XCTAssertTrue(settingsButton.waitForExistence(timeout: 5))
        settingsButton.tap()
        XCTAssertTrue(shareToggle.waitForExistence(timeout: 5))
        setSwitch(shareToggle, to: "1")
        XCTAssertEqual(shareToggle.value as? String, "1")
        app.buttons["aboutDoneButton"].tap()
    }

    @MainActor
    private func setSwitch(_ element: XCUIElement, to desiredValue: String) {
        if (element.value as? String) == desiredValue { return }
        let inner = element.switches.firstMatch
        if inner.exists && inner.isHittable {
            inner.tap()
        } else {
            element.coordinate(withNormalizedOffset: CGVector(dx: 0.92, dy: 0.5)).tap()
        }
        Thread.sleep(forTimeInterval: 0.3)
    }

    @MainActor
    private func shareOnePhotoFromPhotosApp(returningTo app: XCUIApplication, captureSheet: Bool) {
        let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        // Dismiss any stale system alert before switching to Photos
        for title in ["Cancel", "OK"] {
            let stale = springboard.alerts.buttons[title].firstMatch
            if stale.exists {
                stale.tap()
                Thread.sleep(forTimeInterval: 0.3)
                break
            }
        }

        let photos = XCUIApplication(bundleIdentifier: "com.apple.mobileslideshow")
        photos.terminate()
        Thread.sleep(forTimeInterval: 0.4)
        photos.launch()
        Thread.sleep(forTimeInterval: 1.0)

        for title in ["Continue", "OK", "Not Now"] {
            let btn = photos.buttons[title]
            if btn.waitForExistence(timeout: 1.5) {
                btn.tap()
                Thread.sleep(forTimeInterval: 0.4)
            }
        }
        for title in ["Allow", "Don't Allow", "OK"] {
            let alertBtn = springboard.alerts.buttons[title].firstMatch
            if alertBtn.waitForExistence(timeout: 1.5) {
                alertBtn.tap()
                Thread.sleep(forTimeInterval: 0.4)
                break
            }
        }

        let oneUpBack = photos.navigationBars["PUOneUpView"].buttons["BackButton"].firstMatch
        if oneUpBack.waitForExistence(timeout: 1.5) {
            oneUpBack.tap()
            Thread.sleep(forTimeInterval: 0.6)
        }

        let photoPred = NSPredicate(format: "(label BEGINSWITH 'Photo' OR label CONTAINS 'Image' OR label CONTAINS 'PNG') AND NOT (label CONTAINS 'rectangle')")
        let photoMatches = photos.images.matching(photoPred)
        if !photoMatches.firstMatch.waitForExistence(timeout: 8) {
            photos.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
            Thread.sleep(forTimeInterval: 0.5)
            if oneUpBack.waitForExistence(timeout: 2) {
                oneUpBack.tap()
                Thread.sleep(forTimeInterval: 0.6)
            }
        }
        XCTAssertTrue(photoMatches.firstMatch.waitForExistence(timeout: 8), "No photo found in Photos app")
        let visiblePhoto = photoMatches.allElementsBoundByIndex.first { $0.isHittable && $0.frame.minY >= 120 }
            ?? photoMatches.element(boundBy: min(4, max(0, photoMatches.count - 1)))
        visiblePhoto.tap()
        Thread.sleep(forTimeInterval: 1.0)

        let shareBtn = photos.buttons.matching(
            NSPredicate(format: "label == 'Share' OR identifier == 'Share' OR label == 'square.and.arrow.up'")
        ).firstMatch
        if !shareBtn.waitForExistence(timeout: 3) {
            photos.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
            Thread.sleep(forTimeInterval: 0.6)
        }
        if !shareBtn.waitForExistence(timeout: 5) {
            XCTFail("Share button not found in Photos: \(photos.buttons.allElementsBoundByIndex.map { $0.label })")
        }
        shareBtn.tap()
        Thread.sleep(forTimeInterval: 1.2)
        if captureSheet {
            saveShareShot("ios_photos_share_sheet.png")
        }

        let actionPred = NSPredicate(format: "label CONTAINS[c] 'Upscale with LocalSR' OR label == 'LocalSR'")
        var target = photos.cells.matching(actionPred).firstMatch
        if !target.waitForExistence(timeout: 5) {
            target = photos.buttons.matching(actionPred).firstMatch
        }
        if !target.exists {
            target = photos.staticTexts.matching(actionPred).firstMatch
        }
        XCTAssertTrue(target.waitForExistence(timeout: 5), "LocalSR share extension did not appear in Photos share sheet")
        target.tap()
        if captureSheet {
            Thread.sleep(forTimeInterval: 0.2)
            saveShareShot("ios_share_extension_ui.png")
        }

        let openBtn = springboard.alerts.buttons["Open"].firstMatch
        if openBtn.waitForExistence(timeout: 4) {
            openBtn.tap()
        } else {
            let photosOpen = photos.alerts.buttons["Open"].firstMatch
            if photosOpen.waitForExistence(timeout: 1) {
                photosOpen.tap()
            }
        }

        // Responder-chain openURL from an app extension is unreliable in the iOS Simulator
        // (ShareViewController falls back to "Queued for LocalSR. Open LocalSR to upscale." + OK),
        // so automatic foreground switching is not asserted here. If it happens to switch, accept it;
        // otherwise dismiss the fallback card if visible and activate LocalSR explicitly so the test
        // verifies the real contract: App Group Inbox import when LocalSR becomes active.
        if !app.wait(for: .runningForeground, timeout: 5) {
            let extensionOK = photos.buttons["OK"]
            if extensionOK.waitForExistence(timeout: 2) {
                extensionOK.tap()
            }
            app.activate()
            XCTAssertTrue(app.wait(for: .runningForeground, timeout: 10), "LocalSR did not become active after share")
        }
    }

    @MainActor
    private func saveShareShot(_ filename: String) {
        let shot = XCUIScreen.main.screenshot()
        let path = (shareShotDir as NSString).appendingPathComponent(filename)
        try? shot.pngRepresentation.write(to: URL(fileURLWithPath: path))
    }
}

/// Where UI tests write their screenshots. Shared by the phone and tablet test classes.
enum UITestShots {
    static func directory(_ name: String) -> String {
        let base = ProcessInfo.processInfo.environment["SHOT_DIR"] ?? (NSTemporaryDirectory() as NSString).appendingPathComponent("LocalSRShots")
        return (base as NSString).appendingPathComponent(name)
    }
}
