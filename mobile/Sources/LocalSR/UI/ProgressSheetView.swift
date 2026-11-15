import SwiftUI
import LocalSRCore

struct ProgressSheetView: View {
    @Environment(AppModel.self) private var appModel

    var body: some View {
        NavigationStack {
            VStack(spacing: 0.0) {
                VStack(alignment: .leading, spacing: 8.0) {
                    if let preparing = appModel.preparingModel {
                        HStack(spacing: 10.0) {
                            ProgressView()
                            Text("Preparing \(preparing.displayName)…")
                                .font(.subheadline)
                                .foregroundStyle(.secondary)
                        }
                        .accessibilityIdentifier("preparingModelBanner")
                    }

                    HStack {
                        Text("\(appModel.batchCompletedCount) of \(appModel.batchItems.count)")
                            .font(.headline)
                            .adaptiveMonospacedDigit()
                            .accessibilityIdentifier("batchCountLabel")

                        Spacer()

                        if let eta = appModel.batchETAText {
                            Text(eta)
                                .font(.subheadline)
                                .foregroundStyle(.secondary)
                                .adaptiveMonospacedDigit()
                                .accessibilityIdentifier("batchETALabel")
                        }
                    }
                }
                .padding(.horizontal, 16.0)
                .padding(.vertical, 12.0)

                Divider()

                List {
                    ForEach(appModel.batchItems) { item in
                        BatchItemRow(item: item)
                    }
                }
                .listStyle(.plain)
            }
            #if !SKIP && os(iOS)
            .background(Color(uiColor: .systemBackground))
            #endif
            .navigationTitle("Upscaling")
            #if !SKIP && os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    if appModel.isBatchRunning {
                        Button("Cancel") {
                            appModel.cancelBatch(dismiss: true)
                        }
                        .accessibilityIdentifier("cancelBatchButton")
                    } else {
                        Button("Done") {
                            appModel.dismissBatch()
                        }
                        .accessibilityIdentifier("doneBatchButton")
                    }
                }
            }
        }
        #if !SKIP && os(iOS)
        .presentationBackground(Color(uiColor: .systemBackground))
        #endif
        .interactiveDismissDisabled(appModel.isBatchRunning)
    }
}

private struct BatchItemRow: View {
    let item: BatchItem

    var body: some View {
        HStack(alignment: .center, spacing: 12.0) {
            ZStack {
                Color.secondary.opacity(0.12)
                DecodedImageView(url: item.inputURL, maxPixelSize: 200, contentMode: .fill)
            }
            .frame(width: 48.0, height: 48.0)
            .clipShape(RoundedRectangle(cornerRadius: 6.0))

            VStack(alignment: .leading, spacing: 6.0) {
                HStack {
                    Text(item.displayName)
                        .font(.body)
                        .lineLimit(1)

                    Spacer()

                    statusView
                }

                if item.status == .upscaling {
                    ProgressView(value: item.progress, total: 1.0)
                }

                if item.status == .failed, let message = item.errorMessage {
                    Text(message)
                        .font(.caption)
                        .foregroundStyle(.red)
                        .lineLimit(2)
                }
            }
        }
        .padding(.vertical, 4.0)
    }

    @ViewBuilder
    private var statusView: some View {
        switch item.status {
        case .waiting:
            Text("Waiting")
                .font(.caption)
                .foregroundStyle(.secondary)
        case .upscaling:
            Text("\(Int((item.progress * 100.0).rounded()))%")
                .font(.caption)
                .foregroundStyle(.secondary)
                .adaptiveMonospacedDigit()
        case .done:
            Image(systemName: "checkmark.circle.fill")
                .foregroundStyle(.green)
                .accessibilityLabel("Done")
        case .failed:
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(.red)
                .accessibilityLabel("Failed")
        case .cancelled:
            Text("Cancelled")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }
}
