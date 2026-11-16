import SwiftUI
import LocalSRCore

struct TabletWorkspaceView: View {
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var model = TabletModel()
    @State private var settingsAppModel = AppModel()

    @AppStorage("fullScale") private var fullScale: Bool = false

    @State private var isPhotoPickerPresented: Bool = false
    @State private var isFilesImporterPresented: Bool = false
    @State private var isFolderImporterPresented: Bool = false
    @State private var isSettingsPresented: Bool = false
    @State private var isModelLibraryPresented: Bool = false
    @State private var resultToDelete: TabletResultItem? = nil
    @State private var isShowingDeleteAllConfirm: Bool = false
    @State private var isShowingSaveError: Bool = false
    @State private var isShowingStorageError: Bool = false
    @State private var lastMeasuredThreePane: Bool = true

    var body: some View {
        attachPresentations(to: attachStateObservers(to: workspaceShell))
    }

    private var workspaceShell: some View {
        @Bindable var bindableModel = model

        return GeometryReader { geo in
            let width = geo.size.width
            let metrics = TabletLayoutMetrics.forWidth(Double(width))
            let isThreePane = metrics.isThreePane

            ZStack {
                Theme.base
                    .ignoresSafeArea()

                VStack(spacing: 0.0) {
                    if isThreePane {
                        threePaneBody
                    } else {
                        tabModeBody(width: width, metrics: metrics)
                    }

                    StatusBarView(
                        width: width,
                        isTabMode: !isThreePane,
                        onTapLanes: {
                            if !isThreePane {
                                model.activeTab = .enhance
                            }
                            model.scrollToChipsTrigger += 1
                        }
                    )
                    .background(Theme.chrome.ignoresSafeArea(edges: .bottom))
                }

                if model.isDropTargeted {
                    dropOverlay
                        .transition(reduceMotion ? .identity : .opacity)
                }

                #if SKIP
                ComposeView { _ in
                    AndroidBackHandlerHost(enabled: !isThreePane && model.activeTab != .viewer) {
                        model.activeTab = .viewer
                    }
                }
                .frame(width: 0.0, height: 0.0)
                #endif
            }
            .onChange(of: isThreePane) { _, newValue in
                lastMeasuredThreePane = newValue
                #if !SKIP
                syncCommandRouter(isThreePane: newValue)
                #endif
            }
            .onAppear {
                lastMeasuredThreePane = isThreePane
                #if !SKIP
                syncCommandRouter(isThreePane: isThreePane)
                #endif
            }
            #if SKIP
            .composeModifier { modifier in
                modifier.androidCtrlShortcuts { action in
                    handleAndroidShortcut(action, isThreePane: isThreePane)
                }
            }
            #endif
            .tabletWindowDropDestination(isTargeted: $bindableModel.isDropTargeted) { batch in
                model.importBatch(batch)
            }
        }
        .environment(model)
        .tint(Theme.text)
    }

    private func attachStateObservers<Content: View>(to content: Content) -> some View {
        let base = content
            .onAppear {
                model.fullScale = fullScale
                TabletPlatform.configureMinimumWindowSize()
                #if !SKIP
                wireCommandRouterActions()
                syncCommandRouter(isThreePane: lastMeasuredThreePane)
                #endif
            }
            .onChange(of: fullScale) { _, newValue in
                model.fullScale = newValue
            }
            .onChange(of: scenePhase) { _, newPhase in
                model.isAppInBackground = (newPhase == .background)
            }
            .onChange(of: model.selection) { oldSelection, newSelection in
                if oldSelection != newSelection {
                    model.resetViewerTransform()
                }
                #if !SKIP
                syncCommandRouter(isThreePane: lastMeasuredThreePane)
                #endif
            }

        #if !SKIP
        return base
            .onReceive(NotificationCenter.default.publisher(for: ProcessInfo.thermalStateDidChangeNotification)) { _ in
                model.updateThermalState()
            }
            .onDisappear {
                TabletCommandRouter.shared.isActive = false
            }
            .onChange(of: model.isBatchRunning) { _, _ in
                syncCommandRouter(isThreePane: lastMeasuredThreePane)
            }
            .onChange(of: model.queueItems.count) { _, _ in
                syncCommandRouter(isThreePane: lastMeasuredThreePane)
            }
            .onChange(of: model.results.count) { _, _ in
                syncCommandRouter(isThreePane: lastMeasuredThreePane)
            }
            .onChange(of: model.savedResultIDs.count) { _, _ in
                syncCommandRouter(isThreePane: lastMeasuredThreePane)
            }
            .onChange(of: model.setup.task) { _, _ in
                syncCommandRouter(isThreePane: lastMeasuredThreePane)
            }
        #else
        return base
        #endif
    }

    private func attachPresentations<Content: View>(to content: Content) -> some View {
        content
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
            .sheet(isPresented: $isModelLibraryPresented) {
                ModelLibraryView(initialModel: model.setup.upscaleModel)
                    .environment(model)
            }
            .onChange(of: model.saveErrorMessage) { _, newValue in
                isShowingSaveError = newValue != nil
            }
            .onChange(of: model.storageErrorMessage) { _, newValue in
                isShowingStorageError = newValue != nil
            }
            .alert("Not Enough Storage", isPresented: $isShowingStorageError) {
                Button("OK") {
                    model.storageErrorMessage = nil
                }
            } message: {
                Text(model.storageErrorMessage ?? "Free up 120 MB and try again.")
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
                onOpenLibrary: { isModelLibraryPresented = true },
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

    private func tabModeBody(width: CGFloat, metrics: TabletLayoutMetrics) -> some View {
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
                        showsGrid: metrics.isMediaGrid,
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
                        isTwoColumn: metrics.isTwoColumnEnhance,
                        onOpenLibrary: { isModelLibraryPresented = true },
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
                    onOpenLibrary: { isModelLibraryPresented = true },
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
        .tabletDisableLiftHover()
        .accessibilityAddTraits(isSelected ? [.isSelected] : [])
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

    #if !SKIP
    private func wireCommandRouterActions() {
        let router = TabletCommandRouter.shared
        router.onChooseFiles = { openFilesImporter() }
        router.onChoosePhotos = { openPhotosPicker() }
        router.onPaste = { pasteFromClipboard() }
        router.onUpscaleSelected = { model.upscaleSelected(switchToViewer: !lastMeasuredThreePane) }
        router.onStartAll = { model.startAll(switchToViewer: !lastMeasuredThreePane) }
        router.onCancelQueue = { model.cancelQueue() }
        router.onPreviousPhoto = { model.selectPreviousPhoto() }
        router.onNextPhoto = { model.selectNextPhoto() }
        router.onRemoveSelected = { model.removeSelectedQueueItem() }
        router.onTab1 = {
            if lastMeasuredThreePane {
                if reduceMotion {
                    model.isMediaPaneCollapsed.toggle()
                } else {
                    withAnimation(.smooth(duration: 0.25)) {
                        model.isMediaPaneCollapsed.toggle()
                    }
                }
            } else {
                model.activeTab = .media
            }
        }
        router.onTab2 = {
            if !lastMeasuredThreePane {
                model.activeTab = .viewer
            }
        }
        router.onTab3 = {
            if !lastMeasuredThreePane {
                model.activeTab = .enhance
            }
        }
        router.onZoomFit = { model.zoomFit() }
        router.onZoomActual = { model.zoomActual() }
        router.onZoomIn = { model.zoomIn() }
        router.onZoomOut = { model.zoomOut() }
        router.onDividerLeft = { model.nudgeDivider(delta: -0.05) }
        router.onDividerRight = { model.nudgeDivider(delta: 0.05) }
        router.onDividerStart = { model.moveDivider(to: 0.0) }
        router.onDividerEnd = { model.moveDivider(to: 1.0) }
        router.onToggleOriginal = { model.toggleCompareOriginal() }
        router.onSaveSelected = {
            if let res = model.selectedResultItem {
                Task { await model.saveResult(res) }
            }
        }
        router.onSaveAll = {
            Task { await model.saveAllResults() }
        }
        router.onModelLibrary = { isModelLibraryPresented = true }
        router.onSettings = { isSettingsPresented = true }
    }

    private func syncCommandRouter(isThreePane: Bool) {
        let router = TabletCommandRouter.shared
        router.isActive = true
        router.isThreePane = isThreePane
        router.canPaste = TabletPlatform.canPasteImages
        let waiting = model.waitingQueueItems
        router.canStartSelected = !model.isBatchRunning && !model.isLoadingInputs && model.laneValidationError == nil && (!waiting.isEmpty && (model.hasRunnableSelectedItem || waiting.count > 0))
        router.canStartAll = !model.isBatchRunning && !model.isLoadingInputs && model.laneValidationError == nil && !waiting.isEmpty
        router.isBatchRunning = model.isBatchRunning
        router.hasSelection = !model.queueItems.isEmpty || !model.results.isEmpty
        if let q = model.selectedQueueItem, q.status != .running {
            router.canRemoveSelected = true
        } else {
            router.canRemoveSelected = false
        }
        router.hasViewerPhoto = model.selectedQueueItem != nil || model.selectedResultItem != nil
        router.hasCompareResult = model.selectedResultItem != nil
        if let res = model.selectedResultItem {
            router.canSaveSelected = !model.isSaved(res.id) && !model.isSaving && !model.isSavingAll
        } else {
            router.canSaveSelected = false
        }
        router.canSaveAll = model.unsavedResultsCount > 0 && !model.isBatchRunning && !model.isSaving && !model.isSavingAll
        router.upscaleSelectedTitle = model.setup.task == .restore ? "Restore Selected" : "Upscale Selected"
    }
    #else
    private func handleAndroidShortcut(_ action: String, isThreePane: Bool) -> Bool {
        switch action {
        case "chooseFiles":
            openFilesImporter()
            return true
        case "choosePhotos":
            openPhotosPicker()
            return true
        case "paste":
            if TabletPlatform.canPasteImages {
                pasteFromClipboard()
                return true
            }
            return false
        case "upscaleSelected":
            if !model.isBatchRunning && !model.isLoadingInputs && model.laneValidationError == nil && !model.waitingQueueItems.isEmpty {
                model.upscaleSelected(switchToViewer: !isThreePane)
                return true
            }
            return false
        case "startAll":
            if !model.isBatchRunning && !model.isLoadingInputs && model.laneValidationError == nil && !model.waitingQueueItems.isEmpty {
                model.startAll(switchToViewer: !isThreePane)
                return true
            }
            return false
        case "cancelQueue":
            if model.isBatchRunning {
                model.cancelQueue()
                return true
            }
            return false
        case "tab1":
            if isThreePane {
                model.isMediaPaneCollapsed.toggle()
            } else {
                model.activeTab = .media
            }
            return true
        case "tab2":
            if !isThreePane {
                model.activeTab = .viewer
                return true
            }
            return false
        case "tab3":
            if !isThreePane {
                model.activeTab = .enhance
                return true
            }
            return false
        case "zoomFit":
            model.zoomFit()
            return true
        case "zoom1to1":
            model.zoomActual()
            return true
        case "zoomIn":
            model.zoomIn()
            return true
        case "zoomOut":
            model.zoomOut()
            return true
        case "save":
            if let res = model.selectedResultItem, !model.isSaved(res.id) {
                Task { await model.saveResult(res) }
                return true
            }
            return false
        case "saveAll":
            if model.unsavedResultsCount > 0 && !model.isBatchRunning {
                Task { await model.saveAllResults() }
                return true
            }
            return false
        case "modelLibrary":
            isModelLibraryPresented = true
            return true
        case "settings":
            isSettingsPresented = true
            return true
        case "prevPhoto":
            model.selectPreviousPhoto()
            return true
        case "nextPhoto":
            model.selectNextPhoto()
            return true
        case "removePhoto":
            model.removeSelectedQueueItem()
            return true
        case "dividerLeft":
            model.nudgeDivider(delta: -0.05)
            return true
        case "dividerRight":
            model.nudgeDivider(delta: 0.05)
            return true
        case "dividerStart":
            model.moveDivider(to: 0.0)
            return true
        case "dividerEnd":
            model.moveDivider(to: 1.0)
            return true
        case "toggleOriginal":
            model.toggleCompareOriginal()
            return true
        default:
            return false
        }
    }
    #endif
}
