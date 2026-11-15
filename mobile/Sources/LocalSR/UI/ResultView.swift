import SwiftUI
import LocalSRCore

struct ResultView: View {
    let resultID: String

    @Environment(AppModel.self) private var appModel
    @Environment(\.dismiss) private var dismiss
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency

    @State private var splitFraction: CGFloat = 0.5
    @State private var dragStartFraction: CGFloat? = nil
    @State private var dividerAtEdge: Int = 0

    @State private var zoom: CGFloat = 1.0
    @State private var gestureZoom: CGFloat = 1.0
    @State private var offset: CGSize = .zero
    @State private var gestureOffset: CGSize = .zero
    @State private var isPinching: Bool = false

    @State private var photoAspect: CGFloat? = nil
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
            photoAspect = await PlatformSupport.imageAspectRatio(url: result.previewURL)

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
            if let updated = await appModel.rerun(result: latest, model: newModel) {
                displayedResultID = updated.id
                selectedModel = updated.model
            } else if let current = currentResult {
                selectedModel = current.model
            }
        }
    }

    @ViewBuilder
    private func resultBody(result: UpscaleResult) -> some View {
        let effectiveZoom = min(max(zoom * gestureZoom, 1.0), 8.0)
        let effectiveOffset = CGSize(
            width: offset.width + gestureOffset.width,
            height: offset.height + gestureOffset.height
        )
        let isSaved = appModel.isSaved(result.id)

        ZStack(alignment: .bottom) {
            GeometryReader { geo in
                let width = max(geo.size.width, 1.0)
                let height = max(geo.size.height, 1.0)
                let fallbackAspect = result.height > 0 ? CGFloat(result.width) / CGFloat(result.height) : 1.0
                let aspect = photoAspect ?? fallbackAspect
                let fitted = fittedSize(width: width, height: height, aspect: aspect)
                let fitW = fitted.width
                let fitH = fitted.height

                let activeW = min(fitW * effectiveZoom, width)
                let activeH = min(fitH * effectiveZoom, height)
                let activeLeft = (width - activeW) / 2.0
                let activeTop = (height - activeH) / 2.0
                let dividerX = activeLeft + splitFraction * activeW
                let minSquareX = activeLeft + min(22.0, activeW / 2.0)
                let maxSquareX = activeLeft + activeW - min(22.0, activeW / 2.0)
                let squareX = min(max(dividerX, minSquareX), maxSquareX)

                ZStack(alignment: .topLeading) {
                    Color.black

                    DecodedImageView(url: result.previewURL, maxPixelSize: 2048, contentMode: .fit)
                        .frame(width: width, height: height)
                        .scaleEffect(effectiveZoom)
                        .offset(effectiveOffset)
                        .clipped()

                    DecodedImageView(url: result.inputURL, maxPixelSize: 2048, contentMode: .fit)
                        .frame(width: width, height: height)
                        .scaleEffect(effectiveZoom)
                        .offset(effectiveOffset)
                        .clipped()
                        .clipShape(SplitClipRect(clipWidth: dividerX))

                    if isRerunning {
                        let rerunW = fitW
                        let rerunH = fitH
                        let rerunLeft = (width - rerunW) / 2.0
                        let rerunTop = (height - rerunH) / 2.0
                        let rerunLineX = appModel.rerunProgress * rerunW

                        ZStack(alignment: .topLeading) {
                            if appModel.preparingModel != nil {
                                Color.black.opacity(0.35)
                                    .frame(width: rerunW, height: rerunH)
                            } else {
                                Color.black.opacity(0.35)
                                    .frame(width: max(0.0, rerunW - rerunLineX), height: rerunH)
                                    .offset(x: rerunLineX)
                                SweepLine(
                                    lineX: rerunLineX,
                                    squareX: rerunLineX,
                                    height: rerunH,
                                    squareSide: 12.0,
                                    showHandle: false
                                )
                                .frame(width: rerunW, height: rerunH)
                                .animation(reduceMotion ? nil : .linear(duration: 0.25), value: appModel.rerunProgress)
                            }
                        }
                        .frame(width: rerunW, height: rerunH)
                        .overlay {
                            PhotoBrackets(
                                photoWidth: rerunW,
                                photoHeight: rerunH,
                                mode: appModel.preparingModel != nil ? BracketMotionMode.hunt : BracketMotionMode.lock,
                                triggerID: "\(result.id)_\(selectedModel.rawValue)",
                                color: Color.white
                            )
                        }
                        .position(x: rerunLeft + rerunW / 2.0, y: rerunTop + rerunH / 2.0)
                    } else {
                        SweepLine(
                            lineX: dividerX - activeLeft,
                            squareX: squareX - activeLeft,
                            height: activeH,
                            squareSide: 36.0,
                            showHandle: true
                        )
                        .frame(width: activeW, height: activeH)
                        .position(x: activeLeft + activeW / 2.0, y: activeTop + activeH / 2.0)
                        .allowsHitTesting(false)

                        // 44pt wide hit strip over the divider line
                        Color.black.opacity(0.001)
                            .frame(width: 44.0, height: activeH)
                            .position(x: dividerX, y: activeTop + activeH / 2.0)
                            .gesture(dividerDragGesture(activeWidth: activeW))
                            #if !SKIP
                            .accessibilityElement(children: .ignore)
                            #endif
                            .accessibilityLabel("Before and after divider")
                            .accessibilityValue("\(Int((splitFraction * 100.0).rounded())) percent")
                            .accessibilityIdentifier("compareDividerHandle")
                            #if !SKIP
                            .accessibilityAdjustableAction { direction in
                                switch direction {
                                case .increment:
                                    updateDividerFraction(splitFraction + 0.05)
                                case .decrement:
                                    updateDividerFraction(splitFraction - 0.05)
                                @unknown default:
                                    break
                                }
                            }
                            #endif
                    }
                }
                .frame(width: width, height: height)
                #if !SKIP
                .contentShape(Rectangle())
                #endif
                .clipped()
                #if SKIP
                .onTapGesture(count: 2) {
                    guard !isRerunning else { return }
                    let toggle = {
                        if zoom > 1.01 {
                            zoom = 1.0
                            gestureZoom = 1.0
                            offset = .zero
                            gestureOffset = .zero
                        } else {
                            zoom = 3.0
                            gestureZoom = 1.0
                            offset = .zero
                            gestureOffset = .zero
                        }
                    }
                    if reduceMotion {
                        toggle()
                    } else {
                        withAnimation(.snappy(duration: 0.3)) {
                            toggle()
                        }
                    }
                }
                .onTapGesture {
                    if reduceMotion {
                        showChrome.toggle()
                    } else {
                        withAnimation(.easeInOut(duration: 0.2)) {
                            showChrome.toggle()
                        }
                    }
                }
                #else
                .gesture(
                    SpatialTapGesture(count: 2)
                        .onEnded { value in
                            guard !isRerunning else { return }
                            let tap = value.location
                            let toggle = {
                                if zoom > 1.01 {
                                    zoom = 1.0
                                    gestureZoom = 1.0
                                    offset = .zero
                                    gestureOffset = .zero
                                } else {
                                    let targetZoom: CGFloat = 3.0
                                    let raw = CGSize(
                                        width: (width / 2.0 - tap.x) * (targetZoom - 1.0),
                                        height: (height / 2.0 - tap.y) * (targetZoom - 1.0)
                                    )
                                    zoom = targetZoom
                                    gestureZoom = 1.0
                                    offset = clampOffset(raw, zoom: targetZoom, fitW: fitW, fitH: fitH, viewW: width, viewH: height)
                                    gestureOffset = .zero
                                }
                            }
                            if reduceMotion {
                                toggle()
                            } else {
                                withAnimation(.snappy(duration: 0.3)) {
                                    toggle()
                                }
                            }
                        }
                        .exclusively(
                            before: TapGesture(count: 1)
                                .onEnded {
                                    if reduceMotion {
                                        showChrome.toggle()
                                    } else {
                                        withAnimation(.easeInOut(duration: 0.2)) {
                                            showChrome.toggle()
                                        }
                                    }
                                }
                        )
                )
                #endif
                .simultaneousGesture(
                    MagnifyGesture()
                        .onChanged { value in
                            isPinching = true
                            gestureZoom = value.magnification
                        }
                        .onEnded { value in
                            isPinching = false
                            let updated = min(max(zoom * value.magnification, 1.0), 8.0)
                            zoom = updated
                            gestureZoom = 1.0
                            if updated <= 1.01 {
                                offset = .zero
                                gestureOffset = .zero
                            } else {
                                offset = clampOffset(offset, zoom: updated, fitW: fitW, fitH: fitH, viewW: width, viewH: height)
                            }
                        }
                )
                .simultaneousGesture(
                    DragGesture(minimumDistance: 6.0, coordinateSpace: .local)
                        .onChanged { value in
                            guard !isPinching else { return }
                            if effectiveZoom > 1.01 {
                                gestureOffset = value.translation
                            } else if !isRerunning {
                                if dragStartFraction == nil {
                                    dragStartFraction = splitFraction
                                }
                                let base = dragStartFraction ?? splitFraction
                                let next = base + value.translation.width / max(activeW, 1.0)
                                updateDividerFraction(next)
                            }
                        }
                        .onEnded { value in
                            guard !isPinching else {
                                gestureOffset = .zero
                                dragStartFraction = nil
                                return
                            }
                            if effectiveZoom > 1.01 {
                                let raw = CGSize(
                                    width: offset.width + value.translation.width,
                                    height: offset.height + value.translation.height
                                )
                                offset = clampOffset(raw, zoom: zoom, fitW: fitW, fitH: fitH, viewW: width, viewH: height)
                                gestureOffset = .zero
                            } else {
                                dragStartFraction = nil
                            }
                        }
                )
            }
            .ignoresSafeArea()

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
                GeometryReader { chipGeo in
                    let totalWidth = max(chipGeo.size.width, 1.0)
                    let origWidth = splitFraction * totalWidth
                    let upscaledWidth = (1.0 - splitFraction) * totalWidth

                    HStack {
                        chipButton(
                            title: "Original",
                            accessibilityLabel: "Show original only",
                            identifier: "originalChipButton",
                            visible: origWidth >= 60.0
                        ) {
                            let target: CGFloat = splitFraction >= 0.99 ? 0.5 : 1.0
                            animateDivider(to: target)
                        }

                        Spacer()

                        chipButton(
                            title: "Upscaled",
                            accessibilityLabel: "Show upscaled only",
                            identifier: "upscaledChipButton",
                            visible: upscaledWidth >= 60.0
                        ) {
                            let target: CGFloat = splitFraction <= 0.01 ? 0.5 : 0.0
                            animateDivider(to: target)
                        }
                    }
                }
                .frame(height: 30.0)
                .padding(.horizontal, 16.0)

                VStack(spacing: 2.0) {
                    Text(UIFormatting.resultDimensionsLine(width: result.width, height: result.height))
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(Color.white)
                        .adaptiveMonospacedDigit()

                    Text(UIFormatting.resultDetailsLine(model: result.model, unit: result.unit, seconds: result.seconds))
                        .font(.footnote)
                        .foregroundStyle(Color.white.opacity(0.7))
                        .adaptiveMonospacedDigit()
                }
                .multilineTextAlignment(.center)
                .padding(.horizontal, 16.0)
                #if !SKIP
                .accessibilityElement(children: .ignore)
                .accessibilityAddTraits(.isStaticText)
                #endif
                .accessibilityLabel(
                    UIFormatting.resultAccessibilityCaption(
                        width: result.width,
                        height: result.height,
                        model: result.model,
                        unit: result.unit,
                        seconds: result.seconds
                    )
                )
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
    private func chipButton(
        title: String,
        accessibilityLabel: String,
        identifier: String,
        visible: Bool,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            Text(title)
                .font(.caption.weight(.semibold))
                .foregroundStyle(Color.white)
                .padding(.horizontal, 10.0)
                .padding(.vertical, 6.0)
                #if SKIP
                .background(Color.black.opacity(0.6), in: Capsule())
                #else
                .background {
                    if reduceTransparency {
                        Capsule().fill(Color.black.opacity(0.75))
                    } else {
                        Capsule().fill(.ultraThinMaterial)
                    }
                }
                #endif
        }
        .buttonStyle(.plain)
        .opacity(visible ? 1.0 : 0.0)
        .allowsHitTesting(visible && !isRerunning)
        .disabled(!visible || isRerunning)
        #if !SKIP
        .accessibilityHidden(!visible)
        #endif
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.15), value: visible)
        .accessibilityLabel(accessibilityLabel)
        .accessibilityIdentifier(identifier)
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

    private func dividerDragGesture(activeWidth: CGFloat) -> some Gesture {
        DragGesture(minimumDistance: 0.0, coordinateSpace: .local)
            .onChanged { value in
                if dragStartFraction == nil {
                    dragStartFraction = splitFraction
                }
                let base = dragStartFraction ?? splitFraction
                let next = base + value.translation.width / max(activeWidth, 1.0)
                updateDividerFraction(next)
            }
            .onEnded { _ in
                dragStartFraction = nil
            }
    }

    private func updateDividerFraction(_ raw: CGFloat) {
        let clamped = min(max(raw, 0.0), 1.0)
        splitFraction = clamped
        if clamped <= 0.001 {
            if dividerAtEdge != -1 {
                dividerAtEdge = -1
                Haptics.lightImpact()
            }
        } else if clamped >= 0.999 {
            if dividerAtEdge != 1 {
                dividerAtEdge = 1
                Haptics.lightImpact()
            }
        } else if clamped > 0.01 && clamped < 0.99 {
            dividerAtEdge = 0
        }
    }

    private func animateDivider(to target: CGFloat) {
        if reduceMotion {
            splitFraction = target
        } else {
            withAnimation(.smooth(duration: 0.3)) {
                splitFraction = target
            }
        }
    }

    private func fittedSize(width: CGFloat, height: CGFloat, aspect: CGFloat) -> CGSize {
        let safeAspect = max(aspect, 0.05)
        if width / height > safeAspect {
            return CGSize(width: height * safeAspect, height: height)
        }
        return CGSize(width: width, height: width / safeAspect)
    }

    private func clampOffset(_ raw: CGSize, zoom: CGFloat, fitW: CGFloat, fitH: CGFloat, viewW: CGFloat, viewH: CGFloat) -> CGSize {
        let maxX = max(0.0, (fitW * zoom - viewW) / 2.0)
        let maxY = max(0.0, (fitH * zoom - viewH) / 2.0)
        return CGSize(
            width: min(max(raw.width, -maxX), maxX),
            height: min(max(raw.height, -maxY), maxY)
        )
    }
}

private struct SplitClipRect: Shape {
    let clipWidth: CGFloat

    func path(in rect: CGRect) -> Path {
        var path = Path()
        let clamped = min(max(clipWidth, 0.0), rect.width)
        path.addRect(CGRect(x: rect.minX, y: rect.minY, width: clamped, height: rect.height))
        return path
    }
}
