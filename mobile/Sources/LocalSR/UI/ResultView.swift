import SwiftUI
import LocalSRCore

struct ResultView: View {
    let resultID: String

    @Environment(AppModel.self) private var appModel

    @State private var splitFraction: CGFloat = 0.5
    @State private var dragStartFraction: CGFloat? = nil

    @State private var zoom: CGFloat = 1.0
    @State private var gestureZoom: CGFloat = 1.0
    @State private var offset: CGSize = .zero
    @State private var gestureOffset: CGSize = .zero

    @State private var selectedModel: ModelKey = .best
    @State private var displayedResultID: String? = nil
    @State private var isShowingSaveError = false
    @State private var isShowingRerunError = false

    private var activeResultID: String {
        displayedResultID ?? resultID
    }

    private var currentResult: UpscaleResult? {
        appModel.result(for: activeResultID)
    }

    private var isRerunning: Bool {
        appModel.isRerunningResultID == activeResultID
    }

    var body: some View {
        Group {
            if let result = currentResult {
                resultBody(result: result)
            } else {
                Text("Result unavailable")
                    .foregroundStyle(.secondary)
            }
        }
        .navigationTitle("Result")
        #if !SKIP && os(iOS)
        .navigationBarTitleDisplayMode(.inline)
        #endif
        .onAppear {
            if let result = currentResult {
                selectedModel = result.model
            }
        }
        .onChange(of: appModel.saveErrorMessage) { _, newValue in
            isShowingSaveError = newValue != nil
        }
        .onChange(of: appModel.rerunErrorMessage) { _, newValue in
            isShowingRerunError = newValue != nil
        }
        .alert("Could Not Save", isPresented: $isShowingSaveError) {
            Button("OK") {
                appModel.saveErrorMessage = nil
            }
        } message: {
            Text(appModel.saveErrorMessage ?? "Saving to Photos failed.")
        }
        .alert("Could Not Rerun", isPresented: $isShowingRerunError) {
            Button("OK") {
                appModel.rerunErrorMessage = nil
            }
        } message: {
            Text(appModel.rerunErrorMessage ?? "Upscaling with the selected model failed.")
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

        VStack(spacing: 0.0) {
            GeometryReader { geo in
                let width = max(geo.size.width, 1.0)
                let height = max(geo.size.height, 1.0)
                let dividerX = splitFraction * width

                ZStack {
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
                        .clipShape(SplitClipShape(fraction: splitFraction))

                    CompareDivider(
                        x: dividerX,
                        height: height,
                        fraction: splitFraction,
                        viewportWidth: width,
                        splitFraction: $splitFraction,
                        dragStartFraction: $dragStartFraction
                    )

                    if isRerunning {
                        ZStack {
                            Color.black.opacity(0.45)
                            VStack(spacing: 10.0) {
                                ProgressView()
                                Text("Upscaling with \(selectedModel.displayName)…")
                                    .font(.subheadline)
                                    .foregroundStyle(.white)
                            }
                            .padding(16.0)
                        }
                    }
                }
                .frame(width: width, height: height)
                .clipped()
                .simultaneousGesture(
                    MagnifyGesture()
                        .onChanged { value in
                            gestureZoom = value.magnification
                        }
                        .onEnded { value in
                            let updated = min(max(zoom * value.magnification, 1.0), 8.0)
                            zoom = updated
                            gestureZoom = 1.0
                            if updated <= 1.01 {
                                offset = .zero
                            }
                        }
                )
                .gesture(
                    DragGesture(coordinateSpace: .global)
                        .onChanged { value in
                            gestureOffset = value.translation
                        }
                        .onEnded { value in
                            offset = CGSize(
                                width: offset.width + value.translation.width,
                                height: offset.height + value.translation.height
                            )
                            gestureOffset = .zero
                        },
                    isEnabled: effectiveZoom > 1.01
                )
                .onTapGesture(count: 2) { _ in
                    zoom = 1.0
                    gestureZoom = 1.0
                    offset = .zero
                    gestureOffset = .zero
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)

            VStack(spacing: 12.0) {
                Text(
                    UIFormatting.resultSummary(
                        width: result.width,
                        height: result.height,
                        model: result.model,
                        unit: result.unit,
                        seconds: result.seconds
                    )
                )
                .font(.footnote)
                .foregroundStyle(.secondary)
                .adaptiveMonospacedDigit()
                .multilineTextAlignment(.center)
                .accessibilityIdentifier("resultSummaryLabel")

                Picker("Model", selection: $selectedModel) {
                    Text("Best").tag(ModelKey.best)
                    Text("Fast").tag(ModelKey.fast)
                }
                .pickerStyle(.segmented)
                .disabled(isRerunning)
                .accessibilityLabel("Upscale model")
                .accessibilityIdentifier("resultModelPicker")
                .onChange(of: selectedModel) { _, newModel in
                    guard let latest = currentResult, newModel != latest.model else { return }
                    Task {
                        if let updated = await appModel.rerun(result: latest, model: newModel) {
                            displayedResultID = updated.id
                        } else if let current = currentResult {
                            selectedModel = current.model
                        }
                    }
                }

                HStack(spacing: 12.0) {
                    Button {
                        Task {
                            await appModel.saveToLibrary(result)
                        }
                    } label: {
                        Label(
                            isSaved ? "Saved" : "Save to Photos",
                            systemImage: isSaved ? "checkmark.circle.fill" : "square.and.arrow.down"
                        )
                        .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(appModel.isSaving || isSaved || isRerunning)
                    .accessibilityIdentifier("saveToPhotosButton")

                    PlatformShareButton(url: result.outputURL)
                }
            }
            .padding(16.0)
        }
    }
}

private struct CompareDivider: View {
    let x: CGFloat
    let height: CGFloat
    let fraction: CGFloat
    let viewportWidth: CGFloat
    @Binding var splitFraction: CGFloat
    @Binding var dragStartFraction: CGFloat?

    var body: some View {
        ZStack {
            Rectangle()
                .fill(Color.black.opacity(0.28))
                .frame(width: 4.0, height: height)
                .position(x: x, y: height / 2.0)

            Rectangle()
                .fill(Color.white)
                .frame(width: 2.0, height: height)
                .position(x: x, y: height / 2.0)

            ZStack {
                Color.black.opacity(0.001)

                HStack(spacing: 2.0) {
                    Image(systemName: "chevron.left")
                        .font(.caption2)
                    Image(systemName: "chevron.right")
                        .font(.caption2)
                }
                .foregroundStyle(.black)
                .padding(.horizontal, 8.0)
                .padding(.vertical, 6.0)
                .background(Color.white)
                .clipShape(Capsule())
                .overlay {
                    Capsule()
                        .stroke(Color.black.opacity(0.25), lineWidth: 1.0)
                }
            }
            .frame(width: 52.0, height: 52.0)
            .position(x: x, y: height / 2.0)
            .gesture(
                DragGesture(minimumDistance: 0.0, coordinateSpace: .global)
                    .onChanged { value in
                        if dragStartFraction == nil {
                            dragStartFraction = splitFraction
                        }
                        let base = dragStartFraction ?? splitFraction
                        let next = base + value.translation.width / viewportWidth
                        splitFraction = min(max(next, 0.02), 0.98)
                    }
                    .onEnded { _ in
                        dragStartFraction = nil
                    }
            )
            #if !SKIP
            .accessibilityElement(children: .ignore)
            #endif
            .accessibilityLabel("Before and after divider")
            .accessibilityValue("\(Int((fraction * 100.0).rounded()))%")
            .accessibilityIdentifier("compareDividerHandle")
            #if !SKIP
            .accessibilityAdjustableAction { direction in
                switch direction {
                case .increment:
                    splitFraction = min(splitFraction + 0.05, 0.98)
                case .decrement:
                    splitFraction = max(splitFraction - 0.05, 0.02)
                @unknown default:
                    break
                }
            }
            #endif
        }
    }
}

private struct SplitClipShape: Shape {
    let fraction: CGFloat

    func path(in rect: CGRect) -> Path {
        var path = Path()
        let clamped = min(max(fraction, 0.0), 1.0)
        path.addRect(CGRect(x: rect.minX, y: rect.minY, width: rect.width * clamped, height: rect.height))
        return path
    }
}
