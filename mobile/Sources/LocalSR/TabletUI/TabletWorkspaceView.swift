import SwiftUI
import LocalSRCore

struct TabletWorkspaceView: View {
    @State private var model = TabletModel()
    @State private var settingsAppModel = AppModel()

    @AppStorage("fullScale") private var fullScale: Bool = false

    @State private var isPhotoPickerPresented: Bool = false
    @State private var isFilesImporterPresented: Bool = false
    @State private var isFolderImporterPresented: Bool = false
    @State private var isSettingsPresented: Bool = false
    @State private var resultToDelete: TabletResultItem? = nil
    @State private var isShowingDeleteAllConfirm: Bool = false
    @State private var isShowingSaveError: Bool = false

    var body: some View {
        @Bindable var bindableModel = model

        GeometryReader { geo in
            let width = geo.size.width
            let isThreePane = width >= Theme.Spacing.foldWidth

            ZStack {
                Theme.base
                    .ignoresSafeArea()

                VStack(spacing: 0.0) {
                    if isThreePane {
                        threePaneBody
                    } else {
                        tabModeBody(width: width)
                    }

                    StatusBarView(
                        width: width,
                        isTabMode: !isThreePane,
                        onTapLanes: {
                            if !isThreePane {
                                model.activeTab = .enhance
                            }
                        }
                    )
                    .background(Theme.chrome.ignoresSafeArea(edges: .bottom))
                }

                if model.isDropTargeted {
                    dropOverlay
                        .transition(.opacity)
                }
            }
            .tabletWindowDropDestination(isTargeted: $bindableModel.isDropTargeted) { batch in
                model.importBatch(batch)
            }
        }
        .environment(model)
        .tint(Theme.text)
        .onAppear {
            model.fullScale = fullScale
            TabletPlatform.configureMinimumWindowSize()
        }
        .onChange(of: fullScale) { _, newValue in
            model.fullScale = newValue
        }
        .onChange(of: model.selection) { oldSelection, newSelection in
            if oldSelection != newSelection {
                model.resetViewerTransform()
            }
        }
        .background {
            HStack(spacing: 0.0) {
                TabletPhotoPickerHost(
                    isPresented: $isPhotoPickerPresented,
                    onLoadingChanged: { loading in
                        model.isLoadingInputs = loading
                    },
                    onImported: { batch in
                        model.importBatch(batch)
                    }
                )
                TabletFileImporterHost(
                    isFilesPresented: $isFilesImporterPresented,
                    isFolderPresented: $isFolderImporterPresented,
                    onLoadingChanged: { loading in
                        model.isLoadingInputs = loading
                    },
                    onImported: { batch in
                        model.importBatch(batch)
                    }
                )
            }
        }
        .sheet(isPresented: $isSettingsPresented) {
            AboutSheetView()
                .environment(settingsAppModel)
        }
        .onChange(of: model.saveErrorMessage) { _, newValue in
            isShowingSaveError = newValue != nil
        }
        .alert("Couldn't Save Photo", isPresented: $isShowingSaveError) {
            if model.saveAccessDenied {
                Button("Open Settings") {
                    model.saveErrorMessage = nil
                    model.saveAccessDenied = false
                    PlatformSupport.openAppSettings()
                }
                Button("Cancel", role: .cancel) {
                    model.saveErrorMessage = nil
                    model.saveAccessDenied = false
                }
            } else {
                Button("OK") {
                    model.saveErrorMessage = nil
                }
            }
        } message: {
            Text(model.saveErrorMessage ?? "Saving failed.")
        }
        .confirmationDialog(
            "Delete this enhanced photo from LocalSR?",
            isPresented: Binding(
                get: { resultToDelete != nil },
                set: { if !$0 { resultToDelete = nil } }
            ),
            titleVisibility: .visible
        ) {
            Button("Delete Photo", role: .destructive) {
                if let target = resultToDelete {
                    model.deleteResult(id: target.id)
                }
                resultToDelete = nil
            }
            Button("Cancel", role: .cancel) {
                resultToDelete = nil
            }
        } message: {
            if let target = resultToDelete, !model.isSaved(target.id) {
                Text("It hasn't been saved to \(TabletPlatform.libraryName).")
            } else {
                Text(TabletPlatform.isAndroid ? "Your gallery isn't changed." : "Your photo library isn't changed.")
            }
        }
        .confirmationDialog(
            "Delete all \(model.results.count) enhanced photos from LocalSR?",
            isPresented: $isShowingDeleteAllConfirm,
            titleVisibility: .visible
        ) {
            Button("Delete All", role: .destructive) {
                model.deleteAllResults()
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            let unsaved = model.unsavedResultsCount
            if unsaved > 0 {
                let verb = unsaved == 1 ? "hasn't" : "haven't"
                let place = TabletPlatform.isAndroid ? "Photos in your gallery" : "Photos in your library"
                Text("\(place) stay there. \(unsaved) \(verb) been saved.")
            } else {
                Text(TabletPlatform.isAndroid ? "Photos in your gallery stay there." : "Photos in your library stay there.")
            }
        }
    }

    private var threePaneBody: some View {
        HStack(spacing: 0.0) {
            if !model.isMediaPaneCollapsed {
                MediaPaneView(
                    isThreePane: true,
                    showsGrid: false,
                    onChoosePhotos: { openPhotosPicker() },
                    onChooseFiles: { openFilesImporter() },
                    onChooseFolder: { openFolderImporter() },
                    onPaste: { pasteFromClipboard() },
                    onCollapsePane: { model.isMediaPaneCollapsed = true },
                    onStartSingle: { id in model.upscaleSingleQueueItem(id: id, switchToViewer: false) },
                    onConfirmDeleteResult: { result in resultToDelete = result }
                )
                .frame(width: Theme.Spacing.mediaPaneWidth)
                .background(Theme.panel.ignoresSafeArea(edges: .top))

                Rectangle()
                    .fill(Theme.divider)
                    .frame(width: 1.0)
            }

            ViewerPaneView(
                isThreePane: true,
                isMediaCollapsed: model.isMediaPaneCollapsed,
                onExpandMedia: { model.isMediaPaneCollapsed = false },
                onChoosePhotos: { openPhotosPicker() },
                onChooseFiles: { openFilesImporter() },
                onChooseFolder: { openFolderImporter() },
                onPaste: { pasteFromClipboard() },
                onConfirmDeleteResult: { result in resultToDelete = result }
            )
            .frame(maxWidth: .infinity, maxHeight: .infinity)

            Rectangle()
                .fill(Theme.divider)
                .frame(width: 1.0)

            EnhancePaneView(
                isThreePane: true,
                isTwoColumn: false,
                onSaveAll: { Task { await model.saveAllResults() } },
                onClearFinished: { model.clearFinishedQueueItems() },
                onDeleteAllResults: { isShowingDeleteAllConfirm = true },
                onOpenSettings: { isSettingsPresented = true }
            )
            .frame(width: Theme.Spacing.enhancePaneWidth)
            .background(Theme.panel.ignoresSafeArea(edges: .top))
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func tabModeBody(width: CGFloat) -> some View {
        VStack(spacing: 0.0) {
            tabTopBar(width: width)
                .background(Theme.chrome.ignoresSafeArea(edges: .top))

            Rectangle()
                .fill(Theme.divider)
                .frame(height: 1.0)

            Group {
                switch model.activeTab {
                case .media:
                    MediaPaneView(
                        isThreePane: false,
                        showsGrid: width >= Theme.Spacing.mediaGridWidth,
                        onChoosePhotos: { openPhotosPicker() },
                        onChooseFiles: { openFilesImporter() },
                        onChooseFolder: { openFolderImporter() },
                        onPaste: { pasteFromClipboard() },
                        onCollapsePane: {},
                        onStartSingle: { id in model.upscaleSingleQueueItem(id: id, switchToViewer: true) },
                        onConfirmDeleteResult: { result in resultToDelete = result }
                    )
                case .viewer:
                    ViewerPaneView(
                        isThreePane: false,
                        isMediaCollapsed: false,
                        onExpandMedia: {},
                        onChoosePhotos: { openPhotosPicker() },
                        onChooseFiles: { openFilesImporter() },
                        onChooseFolder: { openFolderImporter() },
                        onPaste: { pasteFromClipboard() },
                        onConfirmDeleteResult: { result in resultToDelete = result }
                    )
                case .enhance:
                    EnhancePaneView(
                        isThreePane: false,
                        isTwoColumn: width >= Theme.Spacing.twoColumnWidth,
                        onSaveAll: { Task { await model.saveAllResults() } },
                        onClearFinished: { model.clearFinishedQueueItems() },
                        onDeleteAllResults: { isShowingDeleteAllConfirm = true },
                        onOpenSettings: { isSettingsPresented = true }
                    )
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    private func tabTopBar(width: CGFloat) -> some View {
        let hideCount = width < 440.0
        let segmentWidth: CGFloat = hideCount
            ? min(96.0, max(76.0, (width - 116.0) / 3.0))
            : 104.0
        let mediaLabel = (!hideCount && !model.queueItems.isEmpty)
            ? "Media \(model.queueItems.count)"
            : "Media"

        return HStack(spacing: Theme.Spacing.sm) {
            Menu {
                TabletAddMenuContent(
                    canPaste: TabletPlatform.canPasteImages,
                    onChoosePhotos: { openPhotosPicker() },
                    onChooseFiles: { openFilesImporter() },
                    onChooseFolder: { openFolderImporter() },
                    onPaste: { pasteFromClipboard() }
                )
            } label: {
                Image(systemName: "plus")
                    .font(.system(size: 16.0, weight: .medium))
                    .foregroundStyle(Theme.text)
                    .frame(width: 36.0, height: 36.0)
            }
            .tabletCornerOffsetLeading()
            .accessibilityLabel("Add Photos")
            .accessibilityIdentifier("tabletTopBarAddButton")

            Spacer(minLength: 4.0)

            HStack(spacing: 2.0) {
                topTabButton(title: mediaLabel, tab: .media, width: segmentWidth, identifier: "tabletTabMedia")
                topTabButton(title: "Viewer", tab: .viewer, width: segmentWidth, identifier: "tabletTabViewer")
                topTabButton(title: "Enhance", tab: .enhance, width: segmentWidth, identifier: "tabletTabEnhance")
            }
            .padding(3.0)
            .frame(height: Theme.Spacing.controlHeight)
            .background(
                RoundedRectangle(cornerRadius: Theme.Radius.control, style: .continuous)
                    .fill(Theme.recess)
            )

            Spacer(minLength: 4.0)

            Menu {
                TabletEnhanceMenuContent(
                    onSaveAll: { Task { await model.saveAllResults() } },
                    onClearFinished: { model.clearFinishedQueueItems() },
                    onDeleteAllResults: { isShowingDeleteAllConfirm = true },
                    onOpenSettings: { isSettingsPresented = true }
                )
            } label: {
                Image(systemName: "ellipsis")
                    .font(.system(size: 16.0, weight: .medium))
                    .foregroundStyle(Theme.text)
                    .frame(width: 36.0, height: 36.0)
            }
            .accessibilityLabel("More")
            .accessibilityIdentifier("tabletTopBarMoreMenu")
        }
        .padding(.horizontal, hideCount ? Theme.Spacing.md : Theme.Spacing.lg)
        .frame(height: Theme.Spacing.headerHeight)
        .background(Theme.chrome)
    }

    private func topTabButton(title: String, tab: TabletWorkspaceTab, width: CGFloat, identifier: String) -> some View {
        let isSelected = model.activeTab == tab
        return Button {
            model.activeTab = tab
        } label: {
            Text(title)
                .themeFont(.control, weight: isSelected ? .semibold : .medium)
                .foregroundStyle(isSelected ? Theme.text : Theme.secondary)
                .lineLimit(1)
                .frame(width: width, height: 30.0)
                .background(
                    RoundedRectangle(cornerRadius: Theme.Radius.control, style: .continuous)
                        .fill(isSelected ? Theme.raised : Color.clear)
                )
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier(identifier)
    }

    private var dropOverlay: some View {
        ZStack {
            Color.black.opacity(0.35)
                .ignoresSafeArea()

            ZStack {
                PhotoBrackets(
                    photoWidth: 320.0,
                    photoHeight: 240.0,
                    mode: BracketMotionMode.hunt,
                    triggerID: "windowDrop",
                    color: Theme.stageText
                )

                Text("Drop to Add")
                    .themeFont(.emptyTitle)
                    .foregroundStyle(Theme.stageText)
            }
            .frame(width: 320.0, height: 240.0)
        }
        .allowsHitTesting(false)
    }

    private func openPhotosPicker() {
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 120_000_000)
            isPhotoPickerPresented = true
        }
    }

    private func openFilesImporter() {
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 120_000_000)
            isFilesImporterPresented = true
        }
    }

    private func openFolderImporter() {
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 120_000_000)
            isFolderImporterPresented = true
        }
    }

    private func pasteFromClipboard() {
        let batch = TabletPlatform.pasteImagesFromClipboard()
        if batch.inputs.isEmpty && !batch.hasVideo && !batch.hasUnreadable {
            model.showTransientStatus("LocalSR can't open this file.")
        } else {
            model.importBatch(batch)
        }
    }
}
