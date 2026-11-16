import SwiftUI
import LocalSRCore

struct ViewerPaneView: View {
    @Environment(TabletModel.self) private var model
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency

    let isThreePane: Bool
    let isMediaCollapsed: Bool
    let onExpandMedia: () -> Void
    let onChoosePhotos: () -> Void
    let onChooseFiles: () -> Void
    let onChooseFolder: () -> Void
    let onPaste: () -> Void
    let onConfirmDeleteResult: (TabletResultItem) -> Void

    @State private var stageAspect: CGFloat? = nil
    @State private var isDraggingDivider: Bool = false
    @State private var isZoomHUDVisible: Bool = true
    @State private var isZoomHUDHovered: Bool = false
    @State private var zoomHUDFadeTask: Task<Void, Never>? = nil
    #if SKIP
    @State private var sheetHeaderResult: TabletResultItem? = nil
    #endif

    private var activeQueueItem: TabletQueueItem? {
        model.selectedQueueItem
    }

    private var activeResult: TabletResultItem? {
        model.selectedResultItem
    }

    private var showHeader: Bool {
        activeQueueItem != nil || activeResult != nil || (isThreePane && isMediaCollapsed)
    }

    private var hudForeground: Color {
        #if SKIP
        return Theme.stageText
        #else
        return reduceTransparency ? Theme.text : Theme.stageText
        #endif
    }

    private var showZoomHUDNow: Bool {
        isZoomHUDVisible || isZoomHUDHovered
    }

    var body: some View {
        VStack(spacing: 0.0) {
            if showHeader {
                viewerHeader
                Rectangle()
                    .fill(Theme.divider)
                    .frame(height: 1.0)
            }

            stageArea
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Theme.stage)
        #if SKIP
        .sheet(item: $sheetHeaderResult) { result in
            androidHeaderActionSheet(result)
        }
        #endif
    }

    // MARK: - Header

    private var viewerHeader: some View {
        HStack(spacing: Theme.Spacing.md) {
            if isThreePane && isMediaCollapsed {
                Button {
                    if reduceMotion {
                        onExpandMedia()
                    } else {
                        withAnimation(.smooth(duration: 0.25)) {
                            onExpandMedia()
                        }
                    }
                } label: {
                    Image(systemName: TabletPlatform.sidebarSymbol)
                        .font(.system(size: 16.0, weight: .medium))
                        .foregroundStyle(Theme.text)
                        .frame(width: 36.0, height: 36.0)
                }
                .buttonStyle(.plain)
                .tabletCornerOffsetLeading()
                .accessibilityLabel("Show Media Pane")
                .accessibilityIdentifier("tabletExpandMediaButton")
            }

            if let result = activeResult {
                let note = resultScaleNote(result)
                VStack(alignment: .leading, spacing: 1.0) {
                    Text(result.displayName)
                        .themeFont(.paneTitle)
                        .foregroundStyle(Theme.text)
                        .lineLimit(1)
                        .truncationMode(.middle)

                    Text(resultSubtitle(result))
                        .themeFont(.meta)
                        .foregroundStyle(Theme.secondary)
                        .lineLimit(1)
                        .accessibilityIdentifier("tabletViewerResultMeta")

                    if !note.isEmpty {
                        Text(note)
                            .themeFont(.meta)
                            .foregroundStyle(Theme.secondary)
                            .lineLimit(1)
                            .accessibilityIdentifier("tabletViewerResultScaleNote")
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .tabletDraggableFile(result.outputURL)

                resultActions(result)
            } else if let item = activeQueueItem {
                VStack(alignment: .leading, spacing: 1.0) {
                    Text(item.displayName)
                        .themeFont(.paneTitle)
                        .foregroundStyle(Theme.text)
                        .lineLimit(1)
                        .truncationMode(.middle)

                    Text(item.dimensionsText ?? (item.isPreparingPreview ? "Preparing preview\u{2026}" : "Ready"))
                        .themeFont(.meta)
                        .foregroundStyle(Theme.secondary)
                        .lineLimit(1)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            } else {
                Spacer()
            }
        }
        .padding(.horizontal, Theme.Spacing.lg)
        .padding(.vertical, 4.0)
        .frame(minHeight: Theme.Spacing.headerHeight)
        .background(Theme.panel)
        #if SKIP
        .tabletLongPressAction(enabled: activeResult != nil) {
            if let result = activeResult {
                sheetHeaderResult = result
            }
        }
        #else
        .contextMenu {
            if let result = activeResult {
                resultMenuItems(result: result, includeShareAndSave: true)
            }
        }
        #endif
    }

    @ViewBuilder
    private func resultActions(_ result: TabletResultItem) -> some View {
        let isSaved = model.isSaved(result.id)
        HStack(spacing: Theme.Spacing.sm) {
            #if !SKIP
            ShareLink(item: result.outputURL) {
                Image(systemName: "square.and.arrow.up")
                    .font(.system(size: 15.0, weight: .medium))
                    .foregroundStyle(Theme.text)
                    .frame(width: 36.0, height: 36.0)
            }
            .accessibilityLabel("Share")
            .accessibilityIdentifier("tabletViewerShareButton")
            #else
            Button {
                AndroidUISupport.shareImageFile(ProcessInfo.processInfo.androidContext, result.outputURL.path)
            } label: {
                Image(systemName: "square.and.arrow.up")
                    .font(.system(size: 15.0, weight: .medium))
                    .foregroundStyle(Theme.text)
                    .frame(width: 36.0, height: 36.0)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Share")
            .accessibilityIdentifier("tabletViewerShareButton")
            #endif

            Button {
                Task { await model.saveResult(result) }
            } label: {
                HStack(spacing: 6.0) {
                    Image(systemName: isSaved ? "checkmark" : "square.and.arrow.down")
                        .font(.system(size: 13.0, weight: .medium))
                    Text(isSaved ? "Saved" : "Save")
                        .themeFont(.control)
                }
                .foregroundStyle(isSaved ? Theme.secondary : Theme.text)
                .padding(.horizontal, Theme.Spacing.md)
                .frame(height: Theme.Spacing.controlHeight)
                .background(
                    RoundedRectangle(cornerRadius: Theme.Radius.control, style: .continuous)
                        .fill(Theme.raised)
                )
                .overlay(
                    RoundedRectangle(cornerRadius: Theme.Radius.control, style: .continuous)
                        .stroke(Theme.border, lineWidth: 1.0)
                )
            }
            .buttonStyle(.plain)
            .disabled(isSaved || model.isSaving || model.isSavingAll)
            .accessibilityIdentifier("tabletViewerSaveButton")

            Menu {
                resultMenuItems(result: result, includeShareAndSave: false)
            } label: {
                Image(systemName: "ellipsis")
                    .font(.system(size: 15.0, weight: .medium))
                    .foregroundStyle(Theme.text)
                    .frame(width: 36.0, height: 36.0)
            }
            .accessibilityLabel("More")
            .accessibilityIdentifier("tabletViewerMoreMenu")
        }
    }

    @ViewBuilder
    private func resultMenuItems(result: TabletResultItem, includeShareAndSave: Bool) -> some View {
        let isSaved = model.isSaved(result.id)
        if includeShareAndSave {
            Button {
                Task { await model.saveResult(result) }
            } label: {
                Label(isSaved ? "Saved" : "Save to \(TabletPlatform.libraryName)", systemImage: isSaved ? "checkmark" : "square.and.arrow.down")
            }
            .disabled(isSaved || model.isSaving || model.isSavingAll)

            #if !SKIP
            ShareLink(item: result.outputURL) {
                Label("Share\u{2026}", systemImage: "square.and.arrow.up")
            }
            #else
            Button {
                AndroidUISupport.shareImageFile(ProcessInfo.processInfo.androidContext, result.outputURL.path)
            } label: {
                Label("Share\u{2026}", systemImage: "square.and.arrow.up")
            }
            #endif

            Divider()
        }

        Button {
            model.enhanceAgain(result: result)
        } label: {
            Label("Enhance Again", systemImage: "arrow.clockwise")
        }
        .disabled(model.isBatchRunning || !model.canEnhanceAgain(result))
        .accessibilityIdentifier("tabletViewerEnhanceAgainMenuItem")

        Button(role: .destructive) {
            onConfirmDeleteResult(result)
        } label: {
            Label("Delete Result\u{2026}", systemImage: "trash")
        }
        .accessibilityIdentifier("tabletViewerDeleteResultMenuItem")
    }

    // MARK: - Stage

    @ViewBuilder
    private var stageArea: some View {
        ZStack {
            Theme.stage

            if let item = activeQueueItem {
                let isPreparing = model.isBatchRunning && model.isCheckingDevice && model.batchTargetIDs.contains(item.id)
                if item.status == .failed {
                    failedView(item)
                } else if item.status == .done, let result = activeResult {
                    inspectStage(result: result, queueItem: item)
                } else if item.status == .running || isPreparing || item.isPreparingPreview {
                    processingStage(item, isPreparing: isPreparing)
                } else {
                    inspectStage(result: nil, queueItem: item)
                }
            } else if let result = activeResult {
                inspectStage(result: result, queueItem: nil)
            } else {
                emptyStage
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .task(id: currentStageImageURL?.absoluteString ?? "") {
            stageAspect = nil
            guard let url = currentStageImageURL else { return }
            if let ratio = await PlatformSupport.imageAspectRatio(url: url), ratio > 0.0 {
                stageAspect = ratio
            }
        }
        .onDisappear {
            zoomHUDFadeTask?.cancel()
            zoomHUDFadeTask = nil
        }
    }

    private var currentStageImageURL: URL? {
        if let result = activeResult {
            return result.previewURL
        }
        return activeQueueItem?.inputURL
    }

    private var emptyStage: some View {
        VStack(spacing: 0.0) {
            Spacer(minLength: 16.0)

            Menu {
                TabletAddMenuContent(
                    canPaste: TabletPlatform.canPasteImages,
                    onChoosePhotos: onChoosePhotos,
                    onChooseFiles: onChooseFiles,
                    onChooseFolder: onChooseFolder,
                    onPaste: onPaste
                )
            } label: {
                FrameMark(
                    side: 200.0,
                    showsPlus: true,
                    animateLockOnAppear: true,
                    bracketColor: Theme.stageText,
                    squareColor: Theme.accent
                )
            }
            .accessibilityLabel("Add Photos")
            .accessibilityIdentifier("tabletEmptyStageAddButton")
            .padding(.bottom, 28.0)

            Text("Upscale Your Photos")
                .themeFont(.emptyTitle)
                .foregroundStyle(Theme.stageText)
                .multilineTextAlignment(.center)
                .accessibilityIdentifier("tabletEmptyHeadline")

            Text("Drop photos anywhere in this window, or choose them from \(TabletPlatform.libraryName) or Files. Everything stays on \(TabletPlatform.deviceNoun).")
                .themeFont(.body)
                .foregroundStyle(Theme.stageSecondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 340.0)
                .padding(.top, Theme.Spacing.sm)

            Spacer(minLength: 16.0)
        }
        .padding(Theme.Spacing.xl)
    }

    @ViewBuilder
    private func processingStage(_ item: TabletQueueItem, isPreparing: Bool) -> some View {
        let isRunning = item.status == .running
        let aspect = resolvedAspect(queueItem: item, result: nil)

        GeometryReader { geo in
            let availW = max(geo.size.width, 1.0)
            let availH = max(geo.size.height, 1.0)
            let fitted = CompareGeometry.fittedSize(width: availW, height: availH, aspect: aspect)
            let lineX = min(max(item.progress, 0.0), 1.0) * fitted.width

            ZStack(alignment: .topLeading) {
                if !item.isPreparingPreview {
                    DecodedImageView(url: item.inputURL, maxPixelSize: 1600, contentMode: .fit)
                        .frame(width: fitted.width, height: fitted.height)
                } else {
                    Color.clear
                        .frame(width: fitted.width, height: fitted.height)
                }

                if isPreparing || item.isPreparingPreview {
                    Color.black.opacity(0.35)
                        .frame(width: fitted.width, height: fitted.height)
                } else if isRunning {
                    Color.black.opacity(0.35)
                        .frame(width: max(0.0, fitted.width - lineX), height: fitted.height)
                        .offset(x: lineX)

                    SweepLine(
                        lineX: lineX,
                        squareX: lineX,
                        height: fitted.height,
                        squareSide: 12.0,
                        showHandle: false,
                        squareColor: Theme.accent
                    )
                    .frame(width: fitted.width, height: fitted.height)
                }
            }
            .frame(width: fitted.width, height: fitted.height)
            .clipShape(Rectangle())
            .animation(reduceMotion ? nil : .linear(duration: 0.25), value: item.progress)
            .overlay {
                if isPreparing || isRunning || item.isPreparingPreview {
                    PhotoBrackets(
                        photoWidth: fitted.width,
                        photoHeight: fitted.height,
                        mode: (isPreparing || item.isPreparingPreview) ? BracketMotionMode.hunt : BracketMotionMode.lock,
                        triggerID: item.id,
                        color: Theme.stageText,
                        arm: 28.0,
                        thickness: 6.0,
                        restOutset: 18.0
                    )
                }
            }
            .position(x: availW / 2.0, y: availH / 2.0)
        }
        .padding(Theme.Spacing.xl)
    }

    @ViewBuilder
    private func inspectStage(result: TabletResultItem?, queueItem: TabletQueueItem?) -> some View {
        if let result,
           (!FileManager.default.fileExists(atPath: result.previewURL.path)
            || !FileManager.default.fileExists(atPath: result.outputURL.path)) {
            missingResultView
        } else if let originalURL = result?.inputURL ?? queueItem?.inputURL {
            @Bindable var bindableModel = model
            let aspect = resolvedAspect(queueItem: queueItem, result: result)
            let upscaledURL: URL? = result.map { model.isFullResolutionActive ? $0.outputURL : $0.previewURL }
            let maxPx = model.isFullResolutionActive ? 0 : 2048

            GeometryReader { geo in
                let width = max(geo.size.width, 1.0)
                let height = max(geo.size.height, 1.0)
                let fitAvailW = max(width - 2.0 * Theme.Spacing.xl, 1.0)
                let fitAvailH = max(height - 2.0 * Theme.Spacing.xl, 1.0)
                let fitted = CompareGeometry.fittedSize(width: fitAvailW, height: fitAvailH, aspect: aspect)
                let realSize = resolvedPixelSize(queueItem: queueItem, result: result, fallback: fitted)
                let actualZoom = max(
                    realSize.width / max(fitted.width, 1.0),
                    realSize.height / max(fitted.height, 1.0)
                )
                let minZoom = min(1.0, actualZoom)
                let maxZoom = min(64.0, max(8.0, actualZoom * 2.0))

                ZStack {
                    CompareStageView(
                        originalURL: originalURL,
                        upscaledURL: upscaledURL,
                        originalMaxPixelSize: maxPx,
                        upscaledMaxPixelSize: maxPx,
                        aspectRatio: aspect,
                        stageBackground: Theme.stage,
                        stageInset: Theme.Spacing.xl,
                        dividerSquareColor: Theme.accent,
                        minZoom: minZoom,
                        maxZoom: maxZoom,
                        lockedOverlay: nil,
                        splitFraction: $bindableModel.dividerFraction,
                        zoom: $bindableModel.zoomScale,
                        offset: $bindableModel.panOffset,
                        isFullResolutionActive: $bindableModel.isFullResolutionActive,
                        isDraggingDivider: $isDraggingDivider,
                        onSingleTap: { noteViewerTouched() },
                        onUserInteraction: { noteViewerTouched() }
                    )

                    hudOverlay(
                        result: result,
                        queueItem: queueItem,
                        actualZoom: actualZoom,
                        minZoom: minZoom,
                        maxZoom: maxZoom,
                        fitSize: fitted,
                        viewSize: geo.size
                    )
                }
                .frame(width: width, height: height)
                .onChange(of: actualZoom) { _, newValue in
                    model.viewerActualZoom = newValue
                    model.viewerMinZoom = minZoom
                    model.viewerMaxZoom = maxZoom
                }
                .onAppear {
                    model.viewerActualZoom = actualZoom
                    model.viewerMinZoom = minZoom
                    model.viewerMaxZoom = maxZoom
                }
            }
            .task(id: result?.id ?? queueItem?.id ?? "") {
                bumpZoomHUD()
                if let result, model.revealResultID == result.id {
                    model.revealResultID = nil
                    if reduceMotion {
                        model.dividerFraction = 0.5
                    } else {
                        model.dividerFraction = 1.0
                        try? await Task.sleep(nanoseconds: 250_000_000)
                        if !Task.isCancelled {
                            Haptics.softImpact()
                            withAnimation(.smooth(duration: 0.9)) {
                                model.dividerFraction = 0.5
                            }
                        }
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func hudOverlay(
        result: TabletResultItem?,
        queueItem: TabletQueueItem?,
        actualZoom: CGFloat,
        minZoom: CGFloat,
        maxZoom: CGFloat,
        fitSize: CGSize,
        viewSize: CGSize
    ) -> some View {
        let content = ZStack {
            VStack {
                HStack {
                    Spacer()
                    zoomHUD(
                        actualZoom: actualZoom,
                        minZoom: minZoom,
                        maxZoom: maxZoom,
                        fitSize: fitSize,
                        viewSize: viewSize
                    )
                }
                Spacer()
            }
            .padding(.top, Theme.Spacing.md)
            .padding(.trailing, Theme.Spacing.md)
            .opacity(showZoomHUDNow ? 1.0 : 0.0)
            .allowsHitTesting(showZoomHUDNow)
            .animation(reduceMotion ? nil : .easeInOut(duration: 0.2), value: showZoomHUDNow)

            if result != nil {
                VStack {
                    Spacer()
                    CompareChipsRow(
                        splitFraction: Binding(
                            get: { model.dividerFraction },
                            set: { model.dividerFraction = $0 }
                        ),
                        enhancedTitle: "Enhanced",
                        enhancedIdentifier: "enhancedChipButton",
                        isDisabled: false,
                        useLiquidGlass: true,
                        reduceMotion: reduceMotion,
                        reduceTransparency: reduceTransparency,
                        onTapChip: { noteViewerTouched() }
                    )
                    .padding(.horizontal, Theme.Spacing.lg)
                    .padding(.bottom, Theme.Spacing.lg)
                }
            } else if let item = queueItem,
                      item.status == .waiting,
                      model.isBatchRunning,
                      let pos = model.queuePosition(for: item.id) {
                VStack {
                    Spacer()
                    waitingChip(position: pos)
                        .padding(.bottom, Theme.Spacing.lg)
                }
            }
        }

        #if !SKIP
        GlassEffectContainer {
            content
        }
        #else
        content
        #endif
    }

    @ViewBuilder
    private func zoomHUD(
        actualZoom: CGFloat,
        minZoom: CGFloat,
        maxZoom: CGFloat,
        fitSize: CGSize,
        viewSize: CGSize
    ) -> some View {
        let safeActual = max(actualZoom, 0.001)
        let pct = max(1, Int(((model.zoomScale / safeActual) * 100.0).rounded()))
        let isFitActive = abs(model.zoomScale - 1.0) < 0.015
        let isActualActive = abs(model.zoomScale - actualZoom) < max(0.015, actualZoom * 0.015)

        let controls = HStack(spacing: 2.0) {
            Button {
                applyZoom(model.zoomScale / 1.25, minZoom: minZoom, maxZoom: maxZoom, fitSize: fitSize, viewSize: viewSize)
            } label: {
                Group {
                    #if SKIP
                    // SkipUI has no Material mapping for "minus"; the minus sign as text matches the plus icon's weight.
                    Text("\u{2212}")
                        .font(.system(size: 20.0, weight: .medium))
                    #else
                    Image(systemName: "minus")
                        .font(.system(size: 14.0, weight: .medium))
                    #endif
                }
                    .foregroundStyle(model.zoomScale <= minZoom + 0.001 ? hudForeground.opacity(0.35) : hudForeground)
                    .frame(width: 34.0, height: 36.0)
            }
            .buttonStyle(.plain)
            .disabled(model.zoomScale <= minZoom + 0.001)
            .accessibilityLabel("Zoom Out")
            .accessibilityIdentifier("tabletZoomOutButton")

            Text("\(pct) %")
                .themeFont(.meta)
                .foregroundStyle(hudForeground)
                .lineLimit(1)
                .frame(width: 64.0)
                .multilineTextAlignment(.center)
                .accessibilityIdentifier("tabletZoomPercentLabel")

            Button {
                applyZoom(model.zoomScale * 1.25, minZoom: minZoom, maxZoom: maxZoom, fitSize: fitSize, viewSize: viewSize)
            } label: {
                Image(systemName: "plus")
                    .font(.system(size: 14.0, weight: .medium))
                    .foregroundStyle(model.zoomScale >= maxZoom - 0.001 ? hudForeground.opacity(0.35) : hudForeground)
                    .frame(width: 34.0, height: 36.0)
            }
            .buttonStyle(.plain)
            .disabled(model.zoomScale >= maxZoom - 0.001)
            .accessibilityLabel("Zoom In")
            .accessibilityIdentifier("tabletZoomInButton")

            Rectangle()
                .fill(hudForeground.opacity(0.18))
                .frame(width: 1.0, height: 18.0)
                .padding(.horizontal, 4.0)

            Button {
                applyZoom(1.0, minZoom: minZoom, maxZoom: maxZoom, fitSize: fitSize, viewSize: viewSize)
            } label: {
                Text("Fit")
                    .themeFont(.meta, weight: isFitActive ? .semibold : .regular)
                    .foregroundStyle(isFitActive ? hudForeground : hudForeground.opacity(0.72))
                    .padding(.horizontal, 10.0)
                    .frame(height: 30.0)
                    .background(
                        Capsule()
                            .fill(isFitActive ? hudForeground.opacity(0.16) : Color.clear)
                    )
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Fit Photo")
            .accessibilityIdentifier("tabletZoomFitButton")

            Button {
                applyZoom(actualZoom, minZoom: minZoom, maxZoom: maxZoom, fitSize: fitSize, viewSize: viewSize)
            } label: {
                Text("1:1")
                    .themeFont(.meta, weight: isActualActive ? .semibold : .regular)
                    .foregroundStyle(isActualActive ? hudForeground : hudForeground.opacity(0.72))
                    .padding(.horizontal, 10.0)
                    .frame(height: 30.0)
                    .background(
                        Capsule()
                            .fill(isActualActive ? hudForeground.opacity(0.16) : Color.clear)
                    )
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Actual Size 1 to 1")
            .accessibilityIdentifier("tabletZoomActualButton")
        }
        .padding(.horizontal, 8.0)
        .frame(height: 44.0)

        #if SKIP
        controls
            .background(Color.black.opacity(0.6), in: Capsule())
            .accessibilityIdentifier("tabletZoomHUD")
        #else
        Group {
            if reduceTransparency {
                controls
                    .background(Theme.chrome.opacity(0.92), in: Capsule())
            } else {
                controls
                    .glassEffect(.regular, in: Capsule())
            }
        }
        .onHover { hovering in
            isZoomHUDHovered = hovering
            if hovering {
                isZoomHUDVisible = true
                zoomHUDFadeTask?.cancel()
            } else {
                scheduleZoomHUDFade()
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("tabletZoomHUD")
        #endif
    }

    private var missingResultView: some View {
        VStack(spacing: Theme.Spacing.sm) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 24.0))
                .foregroundStyle(Theme.stageText)
            Text("Photo Unavailable")
                .themeFont(.paneTitle)
                .foregroundStyle(Theme.stageText)
            Text("This enhanced photo is no longer on the device.")
                .themeFont(.body)
                .foregroundStyle(Theme.stageSecondary)
                .multilineTextAlignment(.center)
        }
        .padding(Theme.Spacing.xl)
    }

    private func failedView(_ item: TabletQueueItem) -> some View {
        VStack(spacing: Theme.Spacing.md) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 24.0))
                .foregroundStyle(Theme.stageText)

            Text(item.lastTask == .restore ? "Couldn't restore this photo." : "Couldn't upscale this photo.")
                .themeFont(.paneTitle)
                .foregroundStyle(Theme.stageText)

            if let msg = item.errorMessage, !msg.isEmpty {
                Text(msg)
                    .themeFont(.body)
                    .foregroundStyle(Theme.stageSecondary)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: 360.0)
            }

            Button {
                model.upscaleSingleQueueItem(id: item.id, switchToViewer: false)
            } label: {
                Label("Try Again", systemImage: "arrow.clockwise")
                    .themeFont(.control)
                    .foregroundStyle(Theme.text)
                    .padding(.horizontal, Theme.Spacing.lg)
                    .frame(height: Theme.Spacing.controlHeight)
                    .background(
                        RoundedRectangle(cornerRadius: Theme.Radius.control, style: .continuous)
                            .fill(Theme.raised)
                    )
                    .overlay(
                        RoundedRectangle(cornerRadius: Theme.Radius.control, style: .continuous)
                            .stroke(Theme.border, lineWidth: 1.0)
                    )
            }
            .buttonStyle(.plain)
            .disabled(model.isBatchRunning)
        }
        .padding(Theme.Spacing.xl)
    }

    @ViewBuilder
    private func waitingChip(position: Int) -> some View {
        let label = "Waiting \u{00B7} \(ordinal(position)) in the queue"
        let text = Text(label)
            .themeFont(.meta, weight: .medium)
            .foregroundStyle(hudForeground)
            .padding(.horizontal, Theme.Spacing.md)
            .frame(height: 32.0)

        #if SKIP
        text.background(Color.black.opacity(0.6), in: Capsule())
        #else
        if reduceTransparency {
            text.background(Theme.chrome.opacity(0.92), in: Capsule())
        } else {
            text.glassEffect(.regular, in: Capsule())
        }
        #endif
    }

    // MARK: - Helpers

    /// A touch on the stage or its HUD: the bare keys (arrows, Home, End, M) now act on the viewer.
    private func noteViewerTouched() {
        model.keyboardPane = .viewer
        bumpZoomHUD()
    }

    private func bumpZoomHUD() {
        isZoomHUDVisible = true
        scheduleZoomHUDFade()
    }

    private func scheduleZoomHUDFade() {
        zoomHUDFadeTask?.cancel()
        if isZoomHUDHovered { return }
        zoomHUDFadeTask = Task { @MainActor in
            try? await Task.sleep(nanoseconds: 2_500_000_000)
            if !Task.isCancelled && !isZoomHUDHovered {
                isZoomHUDVisible = false
            }
        }
    }

    private func applyZoom(
        _ rawTarget: CGFloat,
        minZoom: CGFloat,
        maxZoom: CGFloat,
        fitSize: CGSize,
        viewSize: CGSize
    ) {
        noteViewerTouched()
        let clamped = min(max(rawTarget, minZoom), maxZoom)
        let snapped: CGFloat
        if (minZoom >= 1.0 && clamped <= 1.01) || abs(clamped - 1.0) <= 0.015 {
            snapped = 1.0
        } else {
            snapped = clamped
        }

        let update = {
            model.zoomScale = snapped
            if snapped <= 1.01 {
                model.panOffset = .zero
                model.isFullResolutionActive = false
            } else {
                model.panOffset = CompareGeometry.clampOffset(
                    model.panOffset,
                    zoom: snapped,
                    fitW: fitSize.width,
                    fitH: fitSize.height,
                    viewW: viewSize.width,
                    viewH: viewSize.height
                )
                if snapped > 1.2 {
                    model.isFullResolutionActive = true
                }
            }
        }
        if reduceMotion {
            update()
        } else {
            withAnimation(.snappy(duration: 0.25)) {
                update()
            }
        }
    }

    private func resolvedAspect(queueItem: TabletQueueItem?, result: TabletResultItem?) -> CGFloat {
        if let stageAspect, stageAspect > 0.0 {
            return stageAspect
        }
        if let result, result.width > 0, result.height > 0 {
            return CGFloat(result.width) / CGFloat(result.height)
        }
        if let queueItem, let w = queueItem.pixelWidth, let h = queueItem.pixelHeight, w > 0, h > 0 {
            return CGFloat(w) / CGFloat(h)
        }
        return 1.0
    }

    private func resolvedPixelSize(queueItem: TabletQueueItem?, result: TabletResultItem?, fallback: CGSize) -> CGSize {
        if let result, result.width > 0, result.height > 0 {
            return CGSize(width: CGFloat(result.width), height: CGFloat(result.height))
        }
        if let queueItem, let w = queueItem.pixelWidth, let h = queueItem.pixelHeight, w > 0, h > 0 {
            return CGSize(width: CGFloat(w), height: CGFloat(h))
        }
        return fallback
    }

    private func ordinal(_ n: Int) -> String {
        let mod100 = n % 100
        if mod100 >= 11 && mod100 <= 13 { return "\(n)th" }
        switch n % 10 {
        case 1: return "\(n)st"
        case 2: return "\(n)nd"
        case 3: return "\(n)rd"
        default: return "\(n)th"
        }
    }

    private func resultSubtitle(_ result: TabletResultItem) -> String {
        let dims: String = {
            if result.stages.contains(where: { ModelCatalog.model($0).scale == 4 }),
               let inW = result.inputWidth, let inH = result.inputHeight, inW > 0, inH > 0 {
                let derivedScale = UIFormatting.scaleFactorText(
                    inputWidth: inW,
                    inputHeight: inH,
                    outputWidth: result.width,
                    outputHeight: result.height
                )
                return "\(result.width) \u{00D7} \(result.height) \u{00B7} \(derivedScale)\u{00D7} \u{00B7} \(UIFormatting.formatMegapixels(width: result.width, height: result.height))"
            }
            return UIFormatting.resultDimensionsLine(width: result.width, height: result.height)
        }()
        let stages = result.stages.isEmpty
            ? "Best"
            : result.stages.map { ModelCatalog.model($0).displayName }.joined(separator: " + ")
        let sec = UIFormatting.formatSeconds(result.seconds)
        return "\(dims) \u{00B7} \(stages) \u{00B7} \(result.chipsLabel) \u{00B7} \(sec)"
    }

    private func resultScaleNote(_ result: TabletResultItem) -> String {
        guard result.stages.contains(where: { ModelCatalog.model($0).scale == 4 }),
              let inW = result.inputWidth, let inH = result.inputHeight, inW > 0, inH > 0 else {
            return ""
        }
        let reduced = Double(inW) * Double(inH) > Double(SizePolicy.maxInputPixels)
        let policyScale = SizePolicy.target(width: inW, height: inH).outputScale
        if !reduced && result.outputScale < 4 && policyScale == 4 {
            return ""
        }
        return UIFormatting.scaleNote(
            inputWidth: inW,
            inputHeight: inH,
            outputScale: result.outputScale,
            fullScaleFallback: result.fullScaleFallback
        )
    }

    #if SKIP
    private func androidHeaderActionSheet(_ result: TabletResultItem) -> some View {
        let isSaved = model.isSaved(result.id)
        return VStack(alignment: .leading, spacing: Theme.Spacing.sm) {
            Text(result.displayName)
                .themeFont(.paneTitle)
                .foregroundStyle(Theme.text)
                .lineLimit(1)
                .truncationMode(.middle)
                .padding(.bottom, Theme.Spacing.xs)

            androidViewerSheetButton(
                title: isSaved ? "Saved" : "Save to \(TabletPlatform.libraryName)",
                systemImage: isSaved ? "checkmark" : "square.and.arrow.down",
                destructive: false
            ) {
                sheetHeaderResult = nil
                Task { await model.saveResult(result) }
            }
            .disabled(isSaved || model.isSaving || model.isSavingAll)

            androidViewerSheetButton(
                title: "Share\u{2026}",
                systemImage: "square.and.arrow.up",
                destructive: false
            ) {
                sheetHeaderResult = nil
                AndroidUISupport.shareImageFile(ProcessInfo.processInfo.androidContext, result.outputURL.path)
            }

            androidViewerSheetButton(
                title: "Enhance Again",
                systemImage: "arrow.clockwise",
                destructive: false
            ) {
                sheetHeaderResult = nil
                model.enhanceAgain(result: result)
            }
            .disabled(model.isBatchRunning || !model.canEnhanceAgain(result))

            androidViewerSheetButton(
                title: "Delete Result\u{2026}",
                systemImage: "trash",
                destructive: true
            ) {
                sheetHeaderResult = nil
                onConfirmDeleteResult(result)
            }
        }
        .padding(Theme.Spacing.lg)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.panel)
        .presentationDetents([.height(310.0)])
    }

    private func androidViewerSheetButton(title: String, systemImage: String, destructive: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: Theme.Spacing.md) {
                Image(systemName: systemImage)
                    .font(.system(size: 16.0, weight: .medium))
                    .frame(width: 24.0)
                Text(title)
                    .themeFont(.control, weight: .medium)
                Spacer()
            }
            .foregroundStyle(destructive ? Theme.danger : Theme.text)
            .padding(.horizontal, Theme.Spacing.md)
            .frame(height: Theme.Spacing.hitArea)
            .background(
                RoundedRectangle(cornerRadius: Theme.Radius.control, style: .continuous)
                    .fill(Theme.raised)
            )
            .overlay(
                RoundedRectangle(cornerRadius: Theme.Radius.control, style: .continuous)
                    .stroke(Theme.border, lineWidth: 1.0)
            )
        }
        .buttonStyle(.plain)
    }
    #endif
}
