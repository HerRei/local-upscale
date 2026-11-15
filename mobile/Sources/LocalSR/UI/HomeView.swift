import SwiftUI
import LocalSRCore

struct HomeView: View {
    @Environment(AppModel.self) private var appModel
    #if !SKIP
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize: DynamicTypeSize
    #endif
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency

    @AppStorage("selectedModel") private var selectedModel: ModelKey = .best
    @AppStorage("fullScale") private var fullScale: Bool = false

    @State private var navigationPath: [String] = []
    @State private var isPickerPresented = false
    @State private var isAboutPresented = false
    @State private var isShowingDeleteAllConfirm = false
    @State private var resultToDelete: UpscaleResult? = nil
    @State private var isShowingSaveError = false

    #if !SKIP
    @Namespace private var actionBarNamespace
    #endif

    private let gridColumns = [
        GridItem(.adaptive(minimum: 150.0), spacing: 12.0)
    ]

    private var isEmptyHome: Bool {
        appModel.results.isEmpty
            && !appModel.isBatchRunning
            && appModel.batchSummary == nil
            && appModel.stageTransitionResultID == nil
            && appModel.waitingItems.isEmpty
    }

    private var showStageOrSummary: Bool {
        appModel.isBatchRunning
            || appModel.batchSummary != nil
            || appModel.stageTransitionResultID != nil
            || !appModel.waitingItems.isEmpty
    }

    var body: some View {
        NavigationStack(path: $navigationPath) {
            contentRoot
                .navigationTitle("LocalSR")
                .toolbar {
                    ToolbarItemGroup(placement: .primaryAction) {
                        Button {
                            isAboutPresented = true
                        } label: {
                            Image(systemName: "gearshape")
                        }
                        .accessibilityLabel("Settings")
                        .accessibilityIdentifier("settingsButton")

                        Menu {
                            Button {
                                Task {
                                    await appModel.saveAll()
                                }
                            } label: {
                                #if SKIP
                                Label("Save All to Gallery", systemImage: "square.and.arrow.down.on.square")
                                #else
                                Label("Save All to Photos", systemImage: "square.and.arrow.down.on.square")
                                #endif
                            }
                            .disabled(appModel.unsavedResultsCount == 0 || appModel.isBatchRunning || appModel.isSaving || appModel.isSavingAll)
                            .accessibilityIdentifier("saveAllMenuButton")

                            Button(role: .destructive) {
                                isShowingDeleteAllConfirm = true
                            } label: {
                                Label("Delete All\u{2026}", systemImage: "trash")
                            }
                            .disabled(appModel.results.isEmpty || appModel.isBatchRunning)
                            .accessibilityIdentifier("deleteAllMenuButton")
                        } label: {
                            Image(systemName: "ellipsis")
                        }
                        .accessibilityLabel("More")
                        .accessibilityIdentifier("homeMoreMenu")
                    }
                }
                .navigationDestination(for: String.self) { resultID in
                    ResultView(resultID: resultID)
                }
                .sheet(isPresented: $isAboutPresented) {
                    AboutSheetView()
                }
                .background {
                    PlatformPhotoPickerHost(
                        isPresented: $isPickerPresented,
                        onLoadingChanged: { loading in
                            appModel.isLoadingPickedPhotos = loading
                        },
                        onPicked: { inputs in
                            let allowFull = fullScale && appModel.refreshFullScaleAvailability()
                            appModel.startBatch(inputs: inputs, model: selectedModel, fullScale: allowFull)
                        }
                    )
                }
                .onChange(of: appModel.pendingAutoOpenResultID) { _, newID in
                    guard let resultID = newID else { return }
                    if navigationPath.isEmpty && !isAboutPresented && !isShowingDeleteAllConfirm && resultToDelete == nil {
                        navigationPath.append(resultID)
                        Task { @MainActor in
                            try? await Task.sleep(nanoseconds: 80_000_000)
                            #if !SKIP
                            var tx = Transaction()
                            tx.disablesAnimations = true
                            withTransaction(tx) {
                                appModel.completeAutoOpenTransition()
                            }
                            #else
                            appModel.completeAutoOpenTransition()
                            #endif
                        }
                    } else {
                        appModel.completeAutoOpenTransition()
                    }
                }
                .onChange(of: appModel.saveErrorMessage) { _, newValue in
                    if navigationPath.isEmpty {
                        isShowingSaveError = newValue != nil
                    }
                }
                .alert("Couldn't Save Photo", isPresented: $isShowingSaveError) {
                    if appModel.saveAccessDenied {
                        Button("Open Settings") {
                            appModel.saveErrorMessage = nil
                            appModel.saveAccessDenied = false
                            PlatformSupport.openAppSettings()
                        }
                        Button("Cancel", role: .cancel) {
                            appModel.saveErrorMessage = nil
                            appModel.saveAccessDenied = false
                        }
                    } else {
                        Button("OK") {
                            appModel.saveErrorMessage = nil
                        }
                    }
                } message: {
                    Text(appModel.saveErrorMessage ?? "Saving failed.")
                }
                .confirmationDialog(
                    "Delete this upscaled photo from LocalSR?",
                    isPresented: Binding(
                        get: { resultToDelete != nil },
                        set: { if !$0 { resultToDelete = nil } }
                    ),
                    titleVisibility: .visible
                ) {
                    Button("Delete Photo", role: .destructive) {
                        if let target = resultToDelete {
                            if reduceMotion {
                                appModel.delete(target)
                            } else {
                                withAnimation(.easeOut(duration: 0.25)) {
                                    appModel.delete(target)
                                }
                            }
                        }
                        resultToDelete = nil
                    }
                    Button("Cancel", role: .cancel) {
                        resultToDelete = nil
                    }
                } message: {
                    if let target = resultToDelete, !appModel.isSaved(target.id) {
                        #if SKIP
                        Text("It hasn't been saved to Gallery.")
                        #else
                        Text("It hasn't been saved to Photos.")
                        #endif
                    } else {
                        #if SKIP
                        Text("Your gallery isn't changed.")
                        #else
                        Text("Your photo library isn't changed.")
                        #endif
                    }
                }
                .confirmationDialog(
                    "Delete all \(appModel.results.count) upscaled photos from LocalSR?",
                    isPresented: $isShowingDeleteAllConfirm,
                    titleVisibility: .visible
                ) {
                    Button("Delete All", role: .destructive) {
                        if reduceMotion {
                            appModel.deleteAll()
                        } else {
                            withAnimation(.easeOut(duration: 0.25)) {
                                appModel.deleteAll()
                            }
                        }
                    }
                    Button("Cancel", role: .cancel) {}
                } message: {
                    let unsaved = appModel.unsavedResultsCount
                    if unsaved > 0 {
                        let verb = unsaved == 1 ? "hasn't" : "haven't"
                        #if SKIP
                        Text("Photos in your gallery stay there. \(unsaved) \(verb) been saved.")
                        #else
                        Text("Photos in your library stay there. \(unsaved) \(verb) been saved.")
                        #endif
                    } else {
                        #if SKIP
                        Text("Photos in your gallery stay there.")
                        #else
                        Text("Photos in your library stay there.")
                        #endif
                    }
                }
        }
    }

    @ViewBuilder
    private var contentRoot: some View {
        #if SKIP
        ZStack(alignment: .bottom) {
            mainScrollContent
            actionBarContainer
        }
        #else
        mainScrollContent
            .scrollEdgeEffectStyle(.soft, for: .bottom)
            .safeAreaInset(edge: .bottom, spacing: 0.0) {
                actionBarContainer
            }
        #endif
    }

    @ViewBuilder
    private var mainScrollContent: some View {
        if isEmptyHome {
            GeometryReader { geo in
                ScrollView {
                    VStack(spacing: 0.0) {
                        Spacer(minLength: 16.0)

                        Button {
                            if !appModel.isLoadingPickedPhotos && !appModel.isBatchRunning {
                                isPickerPresented = true
                            }
                        } label: {
                            FrameMark(side: 200.0, showsPlus: true, animateLockOnAppear: true)
                        }
                        .buttonStyle(.plain)
                        .accessibilityHidden(true)
                        .padding(.bottom, 28.0)

                        Text("Upscale Your Photos")
                            .font(.title2.bold())
                            .multilineTextAlignment(.center)
                            .accessibilityIdentifier("homeEmptyLabel")

                        Text("Choose photos to enlarge them 4\u{00D7} with more detail. Everything stays on this device.")
                            .font(.body)
                            .foregroundStyle(.secondary)
                            .multilineTextAlignment(.center)
                            .frame(maxWidth: 300.0)
                            .padding(.top, 8.0)

                        Spacer(minLength: 16.0)
                    }
                    .padding(.horizontal, 16.0)
                    #if SKIP
                    .padding(.bottom, 96.0)
                    #endif
                    .frame(maxWidth: .infinity, minHeight: geo.size.height)
                }
            }
        } else {
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVGrid(columns: gridColumns, spacing: 12.0) {
                        if showStageOrSummary {
                            Section {
                                EmptyView()
                            } header: {
                                stageOrSummaryHeader
                                    .id("homeTop")
                                    .padding(.bottom, 4.0)
                            }
                        }

                        Section {
                            ForEach(appModel.results) { result in
                                resultGridTile(result)
                            }
                        }

                        #if SKIP
                        Section {
                            EmptyView()
                        } footer: {
                            Color.clear
                                .frame(height: 96.0)
                        }
                        #endif
                    }
                    .padding(16.0)
                }
                .onChange(of: appModel.isBatchRunning) { _, running in
                    if running {
                        Task { @MainActor in
                            try? await Task.sleep(nanoseconds: 50_000_000)
                            if reduceMotion {
                                proxy.scrollTo("homeTop", anchor: .top)
                            } else {
                                withAnimation(.smooth(duration: 0.3)) {
                                    proxy.scrollTo("homeTop", anchor: .top)
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    @ViewBuilder
    private var stageOrSummaryHeader: some View {
        VStack(spacing: 12.0) {
            if appModel.isBatchRunning || appModel.stageTransitionResultID != nil {
                StageCardView(
                    item: appModel.currentBatchItem,
                    preparingModel: appModel.preparingModel,
                    preparingElapsedLong: appModel.preparingElapsedLong,
                    currentIndex: appModel.currentBatchItemNumber,
                    totalCount: appModel.batchItems.count
                )
            } else if let summary = appModel.batchSummary {
                BatchSummaryCard(summary: summary)
            }

            if !appModel.waitingItems.isEmpty && !appModel.isBatchRunning && appModel.stageTransitionResultID == nil {
                WaitingPhotosCard(
                    items: appModel.waitingItems,
                    onUpscale: {
                        let allowFull = fullScale && appModel.refreshFullScaleAvailability()
                        appModel.startWaitingBatch(model: selectedModel, fullScale: allowFull)
                    },
                    onDismiss: {
                        appModel.clearWaitingItems()
                    }
                )
            }
        }
    }

    @ViewBuilder
    private func resultGridTile(_ result: UpscaleResult) -> some View {
        let isSaved = appModel.isSaved(result.id)
        let tileContent = ResultThumbnailCell(result: result, isSaved: isSaved)
            .transition(
                reduceMotion
                    ? AnyTransition.opacity
                    : AnyTransition.scale(scale: 0.92).combined(with: AnyTransition.opacity)
            )
            .animation(
                reduceMotion ? nil : .snappy(duration: 0.3),
                value: appModel.results.map(\.id)
            )

        let link = NavigationLink(value: result.id) {
            tileContent
        }
        .buttonStyle(.plain)
        #if !SKIP
        .accessibilityElement(children: .ignore)
        .accessibilityAddTraits(.isButton)
        #endif
        .accessibilityLabel(tileAccessibilityLabel(result: result, isSaved: isSaved))
        .accessibilityIdentifier("recentResult_\(result.id)")

        #if !SKIP
        link.contextMenu {
            Button {
                Task {
                    await appModel.saveToLibrary(result)
                }
            } label: {
                Label("Save to Photos", systemImage: "square.and.arrow.down")
            }
            .disabled(isSaved || appModel.isSaving || appModel.isSavingAll)

            ShareLink(item: result.outputURL) {
                Label("Share", systemImage: "square.and.arrow.up")
            }

            Button(role: .destructive) {
                resultToDelete = result
            } label: {
                Label("Delete", systemImage: "trash")
            }
        }
        #else
        link
        #endif
    }

    private func tileAccessibilityLabel(result: UpscaleResult, isSaved: Bool) -> String {
        let base = "Upscaled photo, \(result.width) by \(result.height)"
        return isSaved ? "\(base), saved" : base
    }

    @ViewBuilder
    private var actionBarContainer: some View {
        #if SKIP
        Group {
            if appModel.isBatchRunning {
                batchActionBar
                    .transition(.opacity)
            } else {
                idleActionBar
                    .transition(.opacity)
            }
        }
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.25), value: appModel.isBatchRunning)
        .padding(.horizontal, 16.0)
        .padding(.bottom, 8.0)
        #else
        GlassEffectContainer(spacing: 12.0) {
            if appModel.isBatchRunning {
                batchActionBar
                    .glassEffectID("actionBar", in: actionBarNamespace)
                    .transition(reduceMotion ? .opacity : .identity)
            } else {
                idleActionBar
                    .transition(reduceMotion ? .opacity : .identity)
            }
        }
        .animation(reduceMotion ? .easeInOut(duration: 0.2) : .smooth(duration: 0.35), value: appModel.isBatchRunning)
        .padding(.horizontal, 16.0)
        .padding(.bottom, 8.0)
        #endif
    }

    private var usesVerticalActionBar: Bool {
        #if SKIP
        return false
        #else
        return dynamicTypeSize.isAccessibilitySize
        #endif
    }

    @ViewBuilder
    private var idleActionBar: some View {
        if usesVerticalActionBar {
            VStack(spacing: 10.0) {
                modelMenuButton
                choosePhotosButton
            }
        } else {
            HStack(spacing: 12.0) {
                modelMenuButton
                choosePhotosButton
            }
        }
    }

    @ViewBuilder
    private var modelMenuButton: some View {
        let label = HStack(spacing: 6.0) {
            Text(selectedModel.displayName)
                .font(.headline)
            Image(systemName: "chevron.up.chevron.down")
                .font(.footnote)
        }
        .frame(minHeight: 40.0)
        .padding(.horizontal, 6.0)

        let menuContent = Menu {
            #if SKIP
            Button {
                selectedModel = .best
            } label: {
                if selectedModel == .best {
                    Label("Best — Most detail", systemImage: "checkmark")
                } else {
                    Text("Best — Most detail")
                }
            }
            Button {
                selectedModel = .fast
            } label: {
                if selectedModel == .fast {
                    Label("Fast — Quicker", systemImage: "checkmark")
                } else {
                    Text("Fast — Quicker")
                }
            }
            #else
            Picker("Model", selection: $selectedModel) {
                VStack(alignment: .leading) {
                    Text("Best")
                    Text("Most detail")
                }
                .tag(ModelKey.best)

                VStack(alignment: .leading) {
                    Text("Fast")
                    Text("Quicker")
                }
                .tag(ModelKey.fast)
            }
            .pickerStyle(.inline)
            #endif
        } label: {
            label
        }
        .accessibilityLabel("Model")
        .accessibilityValue(selectedModel.displayName)
        .accessibilityIdentifier("homeModelPicker")

        #if SKIP
        menuContent
            .foregroundStyle(DesignTokens.ink)
            .padding(.horizontal, 14.0)
            .frame(minHeight: 56.0)
            .background(DesignTokens.androidBarBackground, in: Capsule())
        #else
        if reduceTransparency {
            menuContent
                .buttonStyle(.bordered)
                .buttonBorderShape(.capsule)
                .controlSize(.large)
                .glassEffectID("modelMenu", in: actionBarNamespace)
        } else {
            menuContent
                .buttonStyle(.glass)
                .buttonBorderShape(.capsule)
                .controlSize(.large)
                .glassEffectID("modelMenu", in: actionBarNamespace)
        }
        #endif
    }

    @ViewBuilder
    private var choosePhotosButton: some View {
        let button = Button {
            isPickerPresented = true
        } label: {
            Group {
                if appModel.isLoadingPickedPhotos {
                    HStack(spacing: 8.0) {
                        ProgressView()
                            #if !SKIP
                            .tint(Color.white)
                            #endif
                        Text("Loading Photos\u{2026}")
                    }
                } else {
                    Label("Choose Photos", systemImage: "photo.badge.plus")
                }
            }
            .font(.headline)
            .foregroundStyle(Color.white)
            .frame(maxWidth: .infinity, minHeight: 40.0)
        }
        .disabled(appModel.isLoadingPickedPhotos || appModel.isBatchRunning)
        .accessibilityIdentifier("choosePhotosButton")

        #if SKIP
        button
            .buttonStyle(.borderedProminent)
            .tint(DesignTokens.brandFill)
            .clipShape(Capsule())
            .frame(minHeight: 56.0)
        #else
        button
            .buttonStyle(.glassProminent)
            .buttonBorderShape(.capsule)
            .controlSize(.large)
            .tint(DesignTokens.brandFill)
            .glassEffectID("choosePhotos", in: actionBarNamespace)
        #endif
    }

    @ViewBuilder
    private var batchActionBar: some View {
        let titleText: String = {
            if appModel.preparingModel != nil {
                return "Getting ready\u{2026}"
            }
            if appModel.batchItems.count <= 1 {
                return "Upscaling\u{2026}"
            }
            return "Upscaling \(appModel.currentBatchItemNumber) of \(appModel.batchItems.count)"
        }()

        let subtitleText: String? = {
            if appModel.preparingModel != nil {
                return appModel.preparingElapsedLong ? "The first run checks which chip is fastest." : nil
            }
            return appModel.batchETAText
        }()

        let row = HStack(spacing: 12.0) {
            VStack(alignment: .leading, spacing: 4.0) {
                Text(titleText)
                    .font(.headline)
                    .lineLimit(1)
                    .accessibilityIdentifier("batchCountLabel")

                if let subtitleText {
                    Text(subtitleText)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .adaptiveMonospacedDigit()
                        .transition(.opacity)
                        .accessibilityIdentifier("batchETALabel")
                }

                ProgressView(value: appModel.overallBatchProgress, total: 1.0)
                    .tint(DesignTokens.brandFill)
                    .padding(.top, 2.0)
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            Button {
                appModel.cancelBatch()
            } label: {
                ZStack {
                    Circle()
                        .fill(DesignTokens.controlCircleFill)
                        .frame(width: 32.0, height: 32.0)
                    Image(systemName: "xmark")
                        .font(.system(size: 15.0, weight: .semibold))
                        .foregroundStyle(DesignTokens.ink)
                }
                .frame(width: 44.0, height: 44.0)
                #if !SKIP
                .contentShape(Rectangle())
                #endif
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Cancel")
            .accessibilityIdentifier("cancelBatchButton")
        }
        .padding(.leading, 20.0)
        .padding(.trailing, 8.0)
        .padding(.vertical, 8.0)
        .frame(minHeight: 56.0)
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.25), value: appModel.preparingElapsedLong)

        #if SKIP
        row
            .background(DesignTokens.androidBarBackground, in: Capsule())
        #else
        if reduceTransparency {
            row
                .background(DesignTokens.stage, in: Capsule())
        } else {
            row
                .glassEffect(.regular, in: Capsule())
        }
        #endif
    }
}

private struct ResultThumbnailCell: View {
    let result: UpscaleResult
    let isSaved: Bool

    var body: some View {
        DesignTokens.stage
            .aspectRatio(1.0, contentMode: .fit)
            .overlay {
                DecodedImageView(url: result.previewURL, maxPixelSize: 400, contentMode: .fill)
            }
            .clipped()
            .clipShape(RoundedRectangle(cornerRadius: 20.0, style: .continuous))
            .overlay(alignment: .bottomTrailing) {
                if isSaved {
                    ZStack {
                        #if SKIP
                        Circle()
                            .fill(Color.black.opacity(0.6))
                        #else
                        Circle()
                            .fill(.ultraThinMaterial)
                        #endif
                        Image(systemName: "checkmark")
                            .font(.system(size: 11.0, weight: .bold))
                            .foregroundStyle(Color.white)
                    }
                    .frame(width: 24.0, height: 24.0)
                    .padding(8.0)
                }
            }
    }
}

private struct StageCardView: View {
    let item: BatchItem?
    let preparingModel: ModelKey?
    let preparingElapsedLong: Bool
    let currentIndex: Int
    let totalCount: Int

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var photoAspect: CGFloat = 1.0

    var body: some View {
        let isPreparing = preparingModel != nil
        let progress = item?.progress ?? 0.0
        let itemID = item?.id ?? "none"

        RoundedRectangle(cornerRadius: 28.0, style: .continuous)
            .fill(DesignTokens.stage)
            .frame(maxWidth: .infinity)
            .frame(height: 320.0)
            .overlay {
                if let item {
                    GeometryReader { geo in
                        let availW = max(geo.size.width, 1.0)
                        let availH = max(geo.size.height, 1.0)
                        let fitted = fittedSize(width: availW, height: availH, aspect: photoAspect)
                        let fitW = fitted.width
                        let fitH = fitted.height
                        let lineX = min(max(progress, 0.0), 1.0) * fitW

                        ZStack(alignment: .topLeading) {
                            DecodedImageView(url: item.inputURL, maxPixelSize: 800, contentMode: .fit)
                                .frame(width: fitW, height: fitH)
                                .id(item.id)
                                .transition(.opacity)

                            if isPreparing {
                                Color.black.opacity(0.35)
                                    .frame(width: fitW, height: fitH)
                            } else {
                                Color.black.opacity(0.35)
                                    .frame(width: max(0.0, fitW - lineX), height: fitH)
                                    .offset(x: lineX)

                                SweepLine(
                                    lineX: lineX,
                                    squareX: lineX,
                                    height: fitH,
                                    squareSide: 12.0,
                                    showHandle: false
                                )
                                .frame(width: fitW, height: fitH)
                            }
                        }
                        .frame(width: fitW, height: fitH)
                        .clipShape(RoundedRectangle(cornerRadius: 4.0, style: .continuous))
                        .animation(reduceMotion ? nil : .linear(duration: 0.25), value: progress)
                        .animation(reduceMotion ? nil : .easeInOut(duration: 0.2), value: item.id)
                        .overlay {
                            PhotoBrackets(
                                photoWidth: fitW,
                                photoHeight: fitH,
                                mode: isPreparing ? BracketMotionMode.hunt : BracketMotionMode.lock,
                                triggerID: itemID,
                                color: DesignTokens.ink
                            )
                        }
                        .position(x: availW / 2.0, y: availH / 2.0)
                    }
                    .padding(36.0)
                }
            }
            .task(id: item?.inputURL.absoluteString ?? "") {
                guard let url = item?.inputURL else { return }
                if let ratio = await PlatformSupport.imageAspectRatio(url: url), ratio > 0.0 {
                    photoAspect = ratio
                }
            }
            #if !SKIP
            .accessibilityElement(children: .ignore)
            #endif
            .accessibilityLabel(stageAccessibilityLabel(isPreparing: isPreparing, progress: progress))
            .accessibilityIdentifier("stageCard")
    }

    private func stageAccessibilityLabel(isPreparing: Bool, progress: Double) -> String {
        if isPreparing {
            if preparingElapsedLong {
                return "Getting ready. The first run checks which chip is fastest."
            }
            return "Getting ready"
        }
        let pct = Int((min(max(progress, 0.0), 1.0) * 100.0).rounded())
        if totalCount <= 1 {
            return "Upscaling photo, \(pct) percent"
        }
        return "Upscaling photo \(currentIndex) of \(totalCount), \(pct) percent"
    }

    private func fittedSize(width: CGFloat, height: CGFloat, aspect: CGFloat) -> CGSize {
        let safeAspect = max(aspect, 0.05)
        if width / height > safeAspect {
            return CGSize(width: height * safeAspect, height: height)
        }
        return CGSize(width: width, height: width / safeAspect)
    }
}

private struct BatchSummaryCard: View {
    let summary: BatchSummary

    @Environment(AppModel.self) private var appModel

    var body: some View {
        VStack(alignment: .leading, spacing: 14.0) {
            HStack(alignment: .top, spacing: 12.0) {
                titleView
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .accessibilityIdentifier("batchSummaryCard")

                Button {
                    appModel.dismissBatchSummary()
                } label: {
                    ZStack {
                        Circle()
                            .fill(DesignTokens.controlCircleFill)
                            .frame(width: 32.0, height: 32.0)
                        Image(systemName: "xmark")
                            .font(.system(size: 14.0, weight: .semibold))
                            .foregroundStyle(DesignTokens.ink)
                    }
                    .frame(width: 44.0, height: 44.0)
                    #if !SKIP
                    .contentShape(Rectangle())
                    #endif
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Dismiss")
                .accessibilityIdentifier("dismissSummaryButton")
            }

            contentView
        }
        .padding(20.0)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 28.0, style: .continuous)
                .fill(DesignTokens.stage)
        )
    }

    @ViewBuilder
    private var titleView: some View {
        switch summary.kind {
        case .succeeded(let count, _):
            Text("\(count) Photos Upscaled")
                .font(.headline)
                .adaptiveMonospacedDigit()
        case .partialFailure(let succeededCount, let totalCount, _, _):
            Text("\(succeededCount) of \(totalCount) Upscaled")
                .font(.headline)
                .adaptiveMonospacedDigit()
        case .allFailed(let totalCount, _, _, let noWorkingChip):
            if noWorkingChip {
                HStack(spacing: 8.0) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundStyle(DesignTokens.ink)
                    Text("Can't Upscale on This Device")
                        .font(.headline)
                }
            } else {
                HStack(spacing: 8.0) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundStyle(DesignTokens.ink)
                    Text(totalCount == 1 ? "Couldn't Upscale Photo" : "Couldn't Upscale")
                        .font(.headline)
                }
            }
        case .interrupted:
            Text("Last Batch Was Interrupted")
                .font(.headline)
        }
    }

    @ViewBuilder
    private var contentView: some View {
        switch summary.kind {
        case .succeeded(_, let resultIDs):
            saveAllButton(resultIDs: resultIDs)

        case .partialFailure(_, _, let resultIDs, let failures):
            VStack(alignment: .leading, spacing: 10.0) {
                ForEach(failures) { failure in
                    failureRow(failure)
                }

                HStack(spacing: 10.0) {
                    tryAgainButton
                    saveAllButton(resultIDs: resultIDs)
                }
                .padding(.top, 4.0)
            }

        case .allFailed(_, let failures, let message, let noWorkingChip):
            if noWorkingChip {
                Text("The device check found no chip that runs the models correctly.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            } else {
                VStack(alignment: .leading, spacing: 10.0) {
                    if failures.count > 1 {
                        ForEach(failures) { failure in
                            failureRow(failure)
                        }
                    } else {
                        Text(message)
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                    tryAgainButton
                }
            }

        case .interrupted:
            Text("Photos that finished are below.")
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
    }

    @ViewBuilder
    private func failureRow(_ failure: BatchFailureItem) -> some View {
        HStack(alignment: .center, spacing: 12.0) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(DesignTokens.ink)
                .accessibilityHidden(true)

            Color.secondary.opacity(0.12)
                .frame(width: 40.0, height: 40.0)
                .overlay {
                    DecodedImageView(url: failure.inputURL, maxPixelSize: 160, contentMode: .fill)
                }
                .clipped()
                .clipShape(RoundedRectangle(cornerRadius: 8.0, style: .continuous))

            VStack(alignment: .leading, spacing: 2.0) {
                Text(failure.displayName)
                    .font(.body)
                    .lineLimit(1)
                Text(failure.errorMessage)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
        }
    }

    @ViewBuilder
    private var tryAgainButton: some View {
        Button {
            appModel.retryFailed()
        } label: {
            Label("Try Again", systemImage: "arrow.clockwise")
                .font(.subheadline.weight(.semibold))
        }
        .buttonStyle(.bordered)
        #if !SKIP
        .buttonBorderShape(.capsule)
        #else
        .clipShape(Capsule())
        #endif
        .accessibilityIdentifier("retryFailedButton")
    }

    @ViewBuilder
    private func saveAllButton(resultIDs: [String]) -> some View {
        let allSaved = !resultIDs.isEmpty && resultIDs.allSatisfy { appModel.isSaved($0) }
        let labelText: String = {
            if appModel.isSavingAll {
                return "Saving \(appModel.saveAllDoneCount) of \(appModel.saveAllTotalCount)\u{2026}"
            }
            if allSaved {
                return "Saved"
            }
            return "Save All"
        }()

        Button {
            Task {
                await appModel.saveAll(resultIDs: resultIDs)
            }
        } label: {
            Text(labelText)
                .font(.subheadline.weight(.semibold))
                .adaptiveMonospacedDigit()
        }
        .buttonStyle(.bordered)
        #if !SKIP
        .buttonBorderShape(.capsule)
        #else
        .clipShape(Capsule())
        #endif
        .disabled(appModel.isSaving || appModel.isSavingAll || allSaved)
        .accessibilityIdentifier("summarySaveAllButton")
    }
}

private struct WaitingPhotosCard: View {
    let items: [WaitingPhotoItem]
    let onUpscale: () -> Void
    let onDismiss: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14.0) {
            HStack(alignment: .center, spacing: 12.0) {
                Text(items.count == 1 ? "1 Photo Ready" : "\(items.count) Photos Ready")
                    .font(.headline)
                    .adaptiveMonospacedDigit()
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .accessibilityIdentifier("waitingPhotosTitle")

                Button {
                    onDismiss()
                } label: {
                    ZStack {
                        Circle()
                            .fill(DesignTokens.controlCircleFill)
                            .frame(width: 32.0, height: 32.0)
                        Image(systemName: "xmark")
                            .font(.system(size: 14.0, weight: .semibold))
                            .foregroundStyle(DesignTokens.ink)
                    }
                    .frame(width: 44.0, height: 44.0)
                    #if !SKIP
                    .contentShape(Rectangle())
                    #endif
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Dismiss")
                .accessibilityIdentifier("dismissWaitingButton")
            }

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8.0) {
                    ForEach(items) { item in
                        DesignTokens.controlCircleFill
                            .frame(width: 64.0, height: 64.0)
                            .overlay {
                                DecodedImageView(url: item.url, maxPixelSize: 200, contentMode: .fill)
                            }
                            .clipped()
                            .clipShape(RoundedRectangle(cornerRadius: 12.0, style: .continuous))
                    }
                }
            }

            Button {
                onUpscale()
            } label: {
                Text("Upscale")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(Color.white)
            }
            .buttonStyle(.borderedProminent)
            .tint(DesignTokens.brandFill)
            #if !SKIP
            .buttonBorderShape(.capsule)
            #else
            .clipShape(Capsule())
            #endif
            .accessibilityIdentifier("upscaleWaitingButton")
        }
        .padding(20.0)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 28.0, style: .continuous)
                .fill(DesignTokens.stage)
        )
        #if !SKIP
        .accessibilityElement(children: .contain)
        #endif
        .accessibilityIdentifier("waitingPhotosCard")
    }
}
