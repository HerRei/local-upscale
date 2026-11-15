import SwiftUI
import LocalSRCore

struct AboutSheetView: View {
    @Environment(AppModel.self) private var appModel
    @Environment(\.dismiss) private var dismiss

    @State private var noticeText = ""
    @State private var versionText = ""

    var body: some View {
        NavigationStack {
            List {
                Section {
                    VStack(alignment: .leading, spacing: 6.0) {
                        Text("LocalSR")
                            .font(.headline)
                        Text("Version \(versionText)")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                        Text("Everything runs on this device. Nothing is uploaded.")
                            .font(.body)
                            .padding(.top, 4.0)
                    }
                    .padding(.vertical, 4.0)
                }

                Section("Compute Unit") {
                    if appModel.verdicts.isEmpty {
                        Text("Compute unit is checked when a model is first used.")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                    } else {
                        ForEach([ModelKey.best, ModelKey.fast], id: \.self) { model in
                            if let verdict = appModel.verdicts[model] {
                                VStack(alignment: .leading, spacing: 4.0) {
                                    HStack {
                                        Text(model.displayName)
                                            .font(.headline)
                                        Spacer()
                                        Text(verdict.unit.displayName)
                                            .font(.subheadline)
                                            .foregroundStyle(.secondary)
                                    }
                                    Text(verdict.reason)
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                                .padding(.vertical, 2.0)
                            }
                        }
                    }
                }

                Section("Notices") {
                    ScrollView {
                        Text(noticeText.isEmpty ? "Notices unavailable." : noticeText)
                            .font(.caption)
                            .monospaced()
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .frame(minHeight: 180.0, maxHeight: 320.0)
                }
            }
            .navigationTitle("About")
            #if !SKIP && os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") {
                        dismiss()
                    }
                    .accessibilityIdentifier("aboutDoneButton")
                }
            }
        }
        .onAppear {
            if versionText.isEmpty {
                versionText = PlatformSupport.appVersionString()
            }
            if noticeText.isEmpty {
                noticeText = PlatformSupport.loadNoticeText()
            }
        }
    }
}
