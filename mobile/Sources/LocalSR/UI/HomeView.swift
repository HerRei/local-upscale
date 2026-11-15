import SwiftUI
import LocalSRCore

struct HomeView: View {
    @Environment(AppModel.self) private var appModel
    @AppStorage("selectedModel") private var selectedModel: ModelKey = .best
    @State private var isAboutPresented = false

    private let gridColumns = [
        GridItem(.adaptive(minimum: 104.0, maximum: 160.0), spacing: 8.0)
    ]

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20.0) {
                    VStack(spacing: 12.0) {
                        PlatformPhotoPickerButton(disabled: appModel.isBatchRunning) { inputs in
                            appModel.startBatch(inputs: inputs, model: selectedModel)
                        }
                        .frame(maxWidth: .infinity)

                        Picker("Model", selection: $selectedModel) {
                            Text("Best").tag(ModelKey.best)
                            Text("Fast").tag(ModelKey.fast)
                        }
                        .pickerStyle(.segmented)
                        .accessibilityLabel("Upscale model")
                        .accessibilityIdentifier("homeModelPicker")
                    }

                    if appModel.lastBatchInterrupted {
                        Text("The last batch was interrupted.")
                            .font(.subheadline)
                    }

                    if appModel.results.isEmpty {
                        Text("Upscales photos 4x on this device.")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                            .multilineTextAlignment(.center)
                            .frame(maxWidth: .infinity)
                            .padding(.top, 48.0)
                            .accessibilityIdentifier("homeEmptyLabel")
                    } else {
                        VStack(alignment: .leading, spacing: 10.0) {
                            Text("Recent")
                                .font(.headline)

                            LazyVGrid(columns: gridColumns, spacing: 8.0) {
                                ForEach(appModel.results) { result in
                                    NavigationLink(value: result.id) {
                                        ResultThumbnailCell(result: result)
                                    }
                                    .buttonStyle(.plain)
                                    .accessibilityLabel(result.inputURL.lastPathComponent)
                                    .accessibilityIdentifier("recentResult_\(result.id)")
                                }
                            }
                        }
                    }
                }
                .padding(16.0)
            }
            .navigationTitle("LocalSR")
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        isAboutPresented = true
                    } label: {
                        Image(systemName: "info.circle")
                    }
                    .accessibilityLabel("About")
                    .accessibilityIdentifier("aboutButton")
                }
            }
            .navigationDestination(for: String.self) { resultID in
                ResultView(resultID: resultID)
            }
            .sheet(isPresented: $isAboutPresented) {
                AboutSheetView()
            }
            #if os(iOS) || SKIP
            .fullScreenCover(
                isPresented: Binding(
                    get: { appModel.isBatchPresented },
                    set: { appModel.isBatchPresented = $0 }
                )
            ) {
                ProgressSheetView()
            }
            #else
            .sheet(
                isPresented: Binding(
                    get: { appModel.isBatchPresented },
                    set: { appModel.isBatchPresented = $0 }
                )
            ) {
                ProgressSheetView()
            }
            #endif
        }
    }
}

private struct ResultThumbnailCell: View {
    let result: UpscaleResult

    var body: some View {
        Color.secondary.opacity(0.12)
            .aspectRatio(1.0, contentMode: .fit)
            .overlay {
                DecodedImageView(url: result.previewURL, maxPixelSize: 400, contentMode: .fill)
            }
            .clipped()
            .clipShape(RoundedRectangle(cornerRadius: 8.0))
            .overlay {
                RoundedRectangle(cornerRadius: 8.0)
                    .stroke(Color.primary.opacity(0.1), lineWidth: 0.5)
            }
    }
}
