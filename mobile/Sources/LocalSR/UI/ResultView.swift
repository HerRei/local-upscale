import SwiftUI
import LocalSRCore
#if !SKIP && os(iOS)
import UIKit
#endif

struct ResultView: View {
    let resultID: String

    @Environment(AppModel.self) private var appModel
    @Environment(\.dismiss) private var dismiss
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency

    @AppStorage("fullScale") private var fullScale: Bool = false

    @State private var splitFraction: CGFloat = 0.5

    @State private var zoom: CGFloat = 1.0
    @State private var offset: CGSize = .zero
    @State private var isDraggingDivider: Bool = false
    @State private var isFullResolutionActive: Bool = false

    @State private var photoAspect: CGFloat? = nil
    @State private var inputPixelWidth: Int? = nil
    @State private var inputPixelHeight: Int? = nil
    @State private var showChrome: Bool = true
    @State private var selectedModel: ModelKey = .best
    @State private var displayedResultID: String? = nil

    @State private var isShowingSaveError = false
    @State private var isShowingRerunError = false
    @State private var isShowingDeleteConfirm = false

    private var activeResultID: String {
        displayedResultID ?? resultID
    }

    private var currentResult: UpscaleResult? {
        appModel.result(for: activeResultID)
    }

    private var isRerunning: Bool {
        appModel.isRerunningResultID == activeResultID
    }

    private var hasValidFiles: Bool {
        guard let result = currentResult else { return false }
        return FileManager.default.fileExists(atPath: result.previewURL.path)
            && FileManager.default.fileExists(atPath: result.outputURL.path)
    }

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()

            if let result = currentResult, hasValidFiles {
                resultBody(result: result)
            } else {
                missingPhotoView
            }
        }
        .environment(\.colorScheme, .dark)
        .navigationTitle("")
        #if !SKIP && os(iOS)
        .navigationBarTitleDisplayMode(.inline)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .statusBarHidden(!showChrome)
        #endif
        #if os(iOS) || SKIP
        .toolbar(showChrome ? .visible : .hidden, for: .navigationBar)
        #endif
        .toolbar {
            if let result = currentResult, hasValidFiles {
                ToolbarItemGroup(placement: .primaryAction) {
                    #if SKIP
                    Button {
                        AndroidUISupport.shareImageFile(ProcessInfo.processInfo.androidContext, result.outputURL.path)
                    } label: {
                        Image(systemName: "square.and.arrow.up")
                    }
                    .accessibilityLabel("Share")
                    .accessibilityIdentifier("shareResultButton")
                    #else
                    ShareLink(item: result.outputURL) {
                        Image(systemName: "square.and.arrow.up")
                    }
                    .accessibilityLabel("Share")
                    .accessibilityIdentifier("shareResultButton")
                    #endif

                    Menu {
                        #if SKIP
                        Section("Upscale Again") {
                            Button {
                                triggerRerun(to: .best)
                            } label: {
                                if selectedModel == .best {
                                    Label("Best", systemImage: "checkmark")
                                } else {
                                    Text("Best")
                                }
                            }
                            .disabled(appModel.isBatchRunning || isRerunning)

                            Button {
                                triggerRerun(to: .fast)
                            } label: {
                                if selectedModel == .fast {
                                    Label("Fast", systemImage: "checkmark")
                                } else {
                                    Text("Fast")
                                }
                            }
                            .disabled(appModel.isBatchRunning || isRerunning)
                        }
                        #else
                        Section("Upscale Again") {
                            Picker("Upscale Again", selection: $selectedModel) {
                                Text("Best").tag(ModelKey.best)
                                Text("Fast").tag(ModelKey.fast)
                            }
                            .pickerStyle(.inline)
                        }
                        .disabled(appModel.isBatchRunning || isRerunning)
                        #endif

                        Divider()

                        Button(role: .destructive) {
                            isShowingDeleteConfirm = true
                        } label: {
                            Label("Delete", systemImage: "trash")
                        }
                        .disabled(isRerunning)
                        .accessibilityIdentifier("deleteResultMenuButton")
                    } label: {
                        Image(systemName: "ellipsis")
                    }
                    .accessibilityLabel("More")
                    .accessibilityIdentifier("resultMoreMenu")
                    // Attached to the menu so the iOS 26 popover points at "…", not at the middle of the photo.
                    .confirmationDialog(
                        "Delete this upscaled photo from LocalSR?",
                        isPresented: $isShowingDeleteConfirm,
                        titleVisibility: .visible
                    ) {
                        Button("Delete Photo", role: .destructive) {
                            if let result = currentResult {
                                appModel.delete(result)
                            }
                            dismiss()
                        }
                        Button("Cancel", role: .cancel) {}
                    } message: {
                        if let result = currentResult, !appModel.isSaved(result.id) {
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
                }
            }
        }
        .onAppear {
            if let result = currentResult {
                selectedModel = result.model
            }
        }
        .task(id: activeResultID) {
            guard let result = currentResult else { return }
            selectedModel = result.model
            zoom = 1.0
            offset = .zero
            isFullResolutionActive = false
            photoAspect = await PlatformSupport.imageAspectRatio(url: result.previewURL)
            if let size = await PlatformSupport.imagePixelSize(url: result.inputURL) {
                inputPixelWidth = size.width
                inputPixelHeight = size.height
            }

            if appModel.revealResultID == result.id {
                appModel.revealResultID = nil
                if reduceMotion {
                    splitFraction = 0.5
                } else {
                    splitFraction = 1.0
                    try? await Task.sleep(nanoseconds: 250_000_000)
                    if !Task.isCancelled {
                        Haptics.softImpact()
                        withAnimation(.smooth(duration: 0.9)) {
                            splitFraction = 0.5
                        }
                    }
                }
            } else {
                splitFraction = 0.5
            }
        }
        .onDisappear {
            isFullResolutionActive = false
        }
        .onChange(of: selectedModel) { _, newModel in
            #if !SKIP
            triggerRerun(to: newModel)
            #endif
        }
        .onChange(of: appModel.saveErrorMessage) { _, newValue in
            isShowingSaveError = newValue != nil
        }
        .onChange(of: appModel.rerunErrorMessage) { _, newValue in
            isShowingRerunError = newValue != nil
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
        .alert("Couldn't Upscale Again", isPresented: $isShowingRerunError) {
            Button("OK") {
                appModel.rerunErrorMessage = nil
            }
        } message: {
            Text(appModel.rerunErrorMessage ?? "Upscaling with the selected model failed.")
        }
    }

    @ViewBuilder
    private var missingPhotoView: some View {
        #if SKIP
        VStack(spacing: 12.0) {
            Image(systemName: "photo.badge.exclamationmark")
                .font(.system(size: 44.0))
                .foregroundStyle(Color.white.opacity(0.8))
            Text("Photo Unavailable")
                .font(.title2.bold())
                .foregroundStyle(Color.white)
            Text("This upscaled photo is no longer on the device.")
                .font(.body)
                .foregroundStyle(Color.white.opacity(0.7))
                .multilineTextAlignment(.center)
                .padding(.horizontal, 32.0)
        }
        #else
        ContentUnavailableView(
            "Photo Unavailable",
            systemImage: "photo.badge.exclamationmark",
            description: Text("This upscaled photo is no longer on the device.")
        )
        #endif
    }

    private func triggerRerun(to newModel: ModelKey) {
        guard let latest = currentResult, newModel != latest.model else { return }
        selectedModel = newModel
        Task {
            _ = appModel.refreshFullScaleAvailability()
            if let updated = await appModel.rerun(result: latest, model: newModel, fullScale: fullScale) {
                displayedResultID = updated.id
                selectedModel = updated.model
            } else if let current = currentResult {
                selectedModel = current.model
            }
        }
    }

    @ViewBuilder
    private func resultBody(result: UpscaleResult) -> some View {
        let isSaved = appModel.isSaved(result.id)
        let upscaledURL = isFullResolutionActive ? result.outputURL : result.previewURL
        let upscaledMaxPx = isFullResolutionActive ? 0 : 2048
        let originalMaxPx = isFullResolutionActive ? 0 : 2048
        let fallbackAspect = result.height > 0 ? CGFloat(result.width) / CGFloat(result.height) : 1.0
        let aspect = photoAspect ?? fallbackAspect
        let lockedOverlay: CompareLockedOverlayConfig? = isRerunning
            ? CompareLockedOverlayConfig(
                progress: appModel.rerunProgress,
                isPreparing: appModel.preparingModel != nil,
                triggerID: "\(result.id)_\(selectedModel.rawValue)"
            )
            : nil

        ZStack(alignment: .bottom) {
            CompareStageView(
                originalURL: result.inputURL,
                upscaledURL: upscaledURL,
                originalMaxPixelSize: originalMaxPx,
                upscaledMaxPixelSize: upscaledMaxPx,
                aspectRatio: aspect,
                stageBackground: Color.black,
                stageInset: 0.0,
                dividerSquareColor: DesignTokens.brandFill,
                minZoom: 1.0,
                maxZoom: 8.0,
                lockedOverlay: lockedOverlay,
                splitFraction: $splitFraction,
                zoom: $zoom,
                offset: $offset,
                isFullResolutionActive: $isFullResolutionActive,
                isDraggingDivider: $isDraggingDivider,
                onSingleTap: {
                    toggleChromeVisibility()
                }
            )
            .ignoresSafeArea()
            #if !SKIP && os(iOS)
            .background {
                PopGestureGuard(disableInteractivePop: zoom > 1.01 || isDraggingDivider)
                    .frame(width: 0.0, height: 0.0)
            }
            #endif

            if showChrome {
                bottomOverlay(result: result, isSaved: isSaved)
                    .transition(.opacity)
            }
        }
    }

    @ViewBuilder
    private func bottomOverlay(result: UpscaleResult, isSaved: Bool) -> some View {
        let saveIdleTitle = {
            #if SKIP
            return "Save to Gallery"
            #else
            return "Save to Photos"
            #endif
        }()
        let saveDoneTitle = {
            #if SKIP
            return "Saved to Gallery"
            #else
            return "Saved to Photos"
            #endif
        }()

        ZStack(alignment: .bottom) {
            LinearGradient(
                colors: [Color.clear, Color.black.opacity(0.55)],
                startPoint: .top,
                endPoint: .bottom
            )
            .frame(height: 180.0)
            .frame(maxWidth: .infinity)
            .ignoresSafeArea(edges: .bottom)
            .allowsHitTesting(false)

            VStack(spacing: 12.0) {
                CompareChipsRow(
                    splitFraction: $splitFraction,
                    enhancedTitle: "Upscaled",
                    enhancedIdentifier: "upscaledChipButton",
                    isDisabled: isRerunning,
                    useLiquidGlass: false,
                    reduceMotion: reduceMotion,
                    reduceTransparency: reduceTransparency
                )
                .padding(.horizontal, 16.0)

                let outputScale = derivedOutputScale(result: result)
                let dimensionsLine: String = {
                    if let outputScale {
                        return "\(result.width) \u{00D7} \(result.height) \u{00B7} \(outputScale)\u{00D7} \u{00B7} \(UIFormatting.formatMegapixels(width: result.width, height: result.height))"
                    }
                    return UIFormatting.resultDimensionsLine(width: result.width, height: result.height)
                }()
                let scaleNote = scaleNoteText(result: result)
                let baseAccessibility = UIFormatting.resultAccessibilityCaption(
                    width: result.width,
                    height: result.height,
                    model: result.model,
                    unit: result.unit,
                    seconds: result.seconds
                )
                let accessibilityCaption: String = {
                    let scaled = outputScale.map { "\($0) times, \(baseAccessibility)" } ?? baseAccessibility
                    return scaleNote.isEmpty ? scaled : "\(scaled). \(scaleNote)"
                }()

                VStack(spacing: 2.0) {
                    Text(dimensionsLine)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(Color.white)
                        .adaptiveMonospacedDigit()

                    Text(UIFormatting.resultDetailsLine(model: result.model, unit: result.unit, seconds: result.seconds))
                        .font(.footnote)
                        .foregroundStyle(Color.white.opacity(0.85))
                        .adaptiveMonospacedDigit()

                    if !scaleNote.isEmpty {
                        Text(scaleNote)
                            .font(.footnote)
                            .foregroundStyle(Color.white.opacity(0.85))
                    }
                }
                .multilineTextAlignment(.center)
                // A dark backing keeps the caption readable over bright photos (white at 85 % stays above 4.5:1).
                .padding(.horizontal, 12.0)
                .padding(.vertical, 6.0)
                .background(RoundedRectangle(cornerRadius: 12.0).fill(Color.black.opacity(0.6)))
                // Nobody reads the size while inspecting pixels, and the divider would run through the text.
                .opacity(zoom > 1.01 ? 0.0 : 1.0)
                .animation(.easeOut(duration: 0.2), value: zoom > 1.01)
                .padding(.horizontal, 16.0)
                #if !SKIP
                .accessibilityElement(children: .ignore)
                .accessibilityAddTraits(.isStaticText)
                #endif
                .accessibilityLabel(accessibilityCaption)
                .accessibilityIdentifier("resultSummaryLabel")

                saveButton(
                    result: result,
                    isSaved: isSaved,
                    idleTitle: saveIdleTitle,
                    doneTitle: saveDoneTitle
                )
                .padding(.horizontal, 16.0)
                .padding(.bottom, 8.0)
            }
        }
    }

    @ViewBuilder
    private func saveButton(
        result: UpscaleResult,
        isSaved: Bool,
        idleTitle: String,
        doneTitle: String
    ) -> some View {
        let buttonContent = Group {
            if appModel.isSaving {
                HStack(spacing: 8.0) {
                    ProgressView()
                        #if !SKIP
                        .tint(Color.white)
                        #endif
                    Text("Saving\u{2026}")
                }
                .font(.headline)
                .frame(maxWidth: .infinity, minHeight: 56.0)
            } else if isSaved {
                Label(doneTitle, systemImage: "checkmark")
                    .font(.headline)
                    .frame(maxWidth: .infinity, minHeight: 56.0)
            } else {
                Label(idleTitle, systemImage: "square.and.arrow.down")
                    .font(.headline)
                    .frame(maxWidth: .infinity, minHeight: 56.0)
            }
        }

        #if SKIP
        if isSaved {
            Button {
            } label: {
                buttonContent
            }
            .buttonStyle(.bordered)
            .tint(Color.white)
            .clipShape(Capsule())
            .disabled(true)
            .accessibilityIdentifier("saveToPhotosButton")
        } else {
            Button {
                Task {
                    await appModel.saveToLibrary(result)
                }
            } label: {
                buttonContent
                    .foregroundStyle(Color.white)
            }
            .buttonStyle(.borderedProminent)
            .tint(DesignTokens.brandFill)
            .clipShape(Capsule())
            .disabled(appModel.isSaving || appModel.isSavingAll || isRerunning)
            .accessibilityIdentifier("saveToPhotosButton")
        }
        #else
        if isSaved {
            Button {
            } label: {
                buttonContent
                    .foregroundStyle(Color.white)
            }
            .buttonStyle(.glass)
            .contentTransition(.symbolEffect(.replace))
            .disabled(true)
            .accessibilityIdentifier("saveToPhotosButton")
        } else {
            Button {
                Task {
                    await appModel.saveToLibrary(result)
                }
            } label: {
                buttonContent
                    .foregroundStyle(Color.white)
            }
            .buttonStyle(.glassProminent)
            .tint(DesignTokens.brandFill)
            .contentTransition(.symbolEffect(.replace))
            .disabled(appModel.isSaving || appModel.isSavingAll || isRerunning)
            .accessibilityIdentifier("saveToPhotosButton")
        }
        #endif
    }

    private func toggleChromeVisibility() {
        if reduceMotion {
            showChrome.toggle()
        } else {
            withAnimation(.easeInOut(duration: 0.2)) {
                showChrome.toggle()
            }
        }
    }

    private func scaleNoteText(result: UpscaleResult) -> String {
        guard let inW = inputPixelWidth, let inH = inputPixelHeight, inW > 0, inH > 0 else {
            return ""
        }
        return UIFormatting.scaleNote(inputWidth: inW, inputHeight: inH, outputScale: result.outputScale, fullScaleFallback: result.fullScaleFallback, fullScaleAvailable: appModel.isFullScaleAvailable)
    }

    private func derivedOutputScale(result: UpscaleResult) -> String? {
        guard let inW = inputPixelWidth, let inH = inputPixelHeight, inW > 0, inH > 0 else {
            return nil
        }
        return UIFormatting.scaleFactorText(inputWidth: inW, inputHeight: inH, outputWidth: result.width, outputHeight: result.height)
    }
}

#if !SKIP && os(iOS)
private struct PopGestureGuard: UIViewControllerRepresentable {
    let disableInteractivePop: Bool

    func makeUIViewController(context: Context) -> PopGestureGuardController {
        PopGestureGuardController(disableInteractivePop: disableInteractivePop)
    }

    func updateUIViewController(_ uiViewController: PopGestureGuardController, context: Context) {
        uiViewController.disableInteractivePop = disableInteractivePop
        uiViewController.apply()
    }

    static func dismantleUIViewController(_ uiViewController: PopGestureGuardController, coordinator: ()) {
        uiViewController.restore()
    }
}

private final class PopGestureGuardController: UIViewController {
    var disableInteractivePop: Bool
    private weak var trackedNav: UINavigationController?

    init(disableInteractivePop: Bool) {
        self.disableInteractivePop = disableInteractivePop
        super.init(nibName: nil, bundle: nil)
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func didMove(toParent parent: UIViewController?) {
        super.didMove(toParent: parent)
        apply()
    }

    func apply() {
        let nav = navigationController ?? parent?.navigationController
        if let nav {
            trackedNav = nav
            nav.interactivePopGestureRecognizer?.isEnabled = !disableInteractivePop
            if #available(iOS 26.0, *) {
                nav.interactiveContentPopGestureRecognizer?.isEnabled = false
            }
        }
    }

    func restore() {
        trackedNav?.interactivePopGestureRecognizer?.isEnabled = true
        if #available(iOS 26.0, *) {
            trackedNav?.interactiveContentPopGestureRecognizer?.isEnabled = true
        }
    }
}
#endif
