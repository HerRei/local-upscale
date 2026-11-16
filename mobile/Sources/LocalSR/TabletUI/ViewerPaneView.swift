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

    @State private var stageAspect: CGFloat = 1.0

    private var activeQueueItem: TabletQueueItem? {
        model.selectedQueueItem
    }

    private var activeResult: TabletResultItem? {
        model.selectedResultItem
    }

    private var showHeader: Bool {
        activeQueueItem != nil || activeResult != nil || (isThreePane && isMediaCollapsed)
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
    }

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
                    Image(systemName: "sidebar.leading")
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
                }
                .frame(maxWidth: .infinity, alignment: .leading)

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
        .frame(height: Theme.Spacing.headerHeight)
        .background(Theme.panel)
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
                Button(role: .destructive) {
                    onConfirmDeleteResult(result)
                } label: {
                    Label("Delete Result\u{2026}", systemImage: "trash")
                }
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
    private var stageArea: some View {
        ZStack {
            Theme.stage

            if let item = activeQueueItem {
                if item.status == .failed {
                    failedView(item)
                } else if item.status == .done, let result = activeResult {
                    resultPreviewStage(result)
                } else {
                    queueItemStage(item)
                }
            } else if let result = activeResult {
                resultPreviewStage(result)
            } else {
                emptyStage
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .task(id: currentStageImageURL?.absoluteString ?? "") {
            guard let url = currentStageImageURL else { return }
            if let ratio = await PlatformSupport.imageAspectRatio(url: url), ratio > 0.0 {
                stageAspect = ratio
            }
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
    private func queueItemStage(_ item: TabletQueueItem) -> some View {
        let isRunning = item.status == .running
        let isPreparing = model.isBatchRunning && model.isCheckingDevice && model.batchTargetIDs.contains(item.id)

        ZStack {
            GeometryReader { geo in
                let availW = max(geo.size.width, 1.0)
                let availH = max(geo.size.height, 1.0)
                let fitted = fittedSize(width: availW, height: availH, aspect: stageAspect)
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

                        TabletSweepLine(
                            lineX: lineX,
                            height: fitted.height,
                            lineWidth: 2.0,
                            squareSide: 36.0,
                            showHandle: false
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
                            color: Theme.stageText
                        )
                    }
                }
                .position(x: availW / 2.0, y: availH / 2.0)
            }
            .padding(Theme.Spacing.xl)

            if item.status == .waiting,
               model.isBatchRunning,
               let pos = model.queuePosition(for: item.id) {
                VStack {
                    Spacer()
                    waitingChip(position: pos)
                        .padding(.bottom, Theme.Spacing.lg)
                }
            }
        }
    }

    @ViewBuilder
    private func resultPreviewStage(_ result: TabletResultItem) -> some View {
        let missing = !FileManager.default.fileExists(atPath: result.previewURL.path)
        if missing {
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
        } else {
            DecodedImageView(url: result.previewURL, maxPixelSize: 2048, contentMode: .fit)
                .padding(Theme.Spacing.xl)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .tabletDraggableFile(result.outputURL)
                .accessibilityIdentifier("tabletViewerResultImage")
        }
    }

    private func failedView(_ item: TabletQueueItem) -> some View {
        VStack(spacing: Theme.Spacing.md) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 24.0))
                .foregroundStyle(Theme.stageText)

            Text("Couldn't upscale this photo.")
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
            .foregroundStyle(Theme.stageText)
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
        let dims = UIFormatting.resultDimensionsLine(width: result.width, height: result.height)
        let sec = UIFormatting.formatSeconds(result.seconds)
        return "\(dims) \u{00B7} Best \u{00B7} \(result.chipsLabel) \u{00B7} \(sec)"
    }

    private func fittedSize(width: CGFloat, height: CGFloat, aspect: CGFloat) -> CGSize {
        let safeAspect = max(aspect, 0.05)
        if width / height > safeAspect {
            return CGSize(width: height * safeAspect, height: height)
        }
        return CGSize(width: width, height: width / safeAspect)
    }
}
