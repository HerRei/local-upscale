import SwiftUI
import LocalSRCore

struct AboutSheetView: View {
    @Environment(AppModel.self) private var appModel
    @Environment(\.dismiss) private var dismiss

    @AppStorage("fullScale") private var fullScale: Bool = false
    @AppStorage("shareStartsImmediately") private var shareStartsImmediately: Bool = true

    @State private var noticeText = ""
    @State private var versionText = ""

    private var upscalingFooterText: String {
        let base = "Photos over 3 MP are upscaled 2\u{00D7} by default. Full 4\u{00D7} gives up to 192 MP, takes longer to save and needs more memory."
        if !appModel.isFullScaleAvailable {
            return "\(base) Not available on this device."
        }
        return base
    }

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Toggle("Full 4\u{00D7} for Large Photos", isOn: $fullScale)
                        .disabled(!appModel.isFullScaleAvailable)
                        .accessibilityIdentifier("fullScaleToggle")
                } header: {
                    Text("Upscaling")
                } footer: {
                    Text(upscalingFooterText)
                }

                Section {
                    Toggle("Start Right Away", isOn: $shareStartsImmediately)
                        .accessibilityIdentifier("shareStartsImmediatelyToggle")
                } header: {
                    Text("Share Menu")
                } footer: {
                    #if SKIP
                    Text("LocalSR appears when you share photos from other apps.")
                    #else
                    Text("\"Upscale with LocalSR\" appears when you share photos. To hide it, tap Edit Actions at the end of the share menu.")
                    #endif
                }

                Section {
                    ForEach([ModelKey.best, ModelKey.fast], id: \.self) { model in
                        let verdict = appModel.verdicts[model]
                        VStack(alignment: .leading, spacing: 4.0) {
                            HStack {
                                Text(model.displayName)
                                    .font(.body)
                                Spacer()
                                Text(verdict?.unit.platformDisplayName ?? "Checked on first use")
                                    .font(.body)
                                    .foregroundStyle(.secondary)
                            }
                            if let verdict {
                                Text(verdict.reason)
                                    .font(.footnote)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        .padding(.vertical, 2.0)
                        #if !SKIP
                        .accessibilityElement(children: .combine)
                        #endif
                    }
                } header: {
                    Text("This Device")
                } footer: {
                    Text("LocalSR tests each model once and uses the fastest chip that gives correct results.")
                }

                Section {
                    factRow(
                        symbol: "wifi.slash",
                        title: "Works offline",
                        detail: "No network, no account, nothing uploaded."
                    )
                    factRow(
                        symbol: "arrow.up.left.and.arrow.down.right",
                        title: "Up to 4\u{00D7} larger",
                        detail: "Small photos get 4\u{00D7}, photos over 3 MP get 2\u{00D7} of the full original (or 4\u{00D7} with Full 4\u{00D7})."
                    )
                    #if SKIP
                    factRow(
                        symbol: "photo.badge.checkmark",
                        title: "Saves a new photo",
                        detail: "Keeps the date. HDR isn't carried over. Your original isn't changed."
                    )
                    #else
                    factRow(
                        symbol: "photo.badge.checkmark",
                        title: "Saves a new photo",
                        detail: "Keeps date, location and HDR. Your original isn't changed."
                    )
                    #endif
                }

                Section {
                    HStack {
                        Text("Version")
                        Spacer()
                        Text(versionText)
                            .foregroundStyle(.secondary)
                            .adaptiveMonospacedDigit()
                    }
                    #if !SKIP
                    .accessibilityElement(children: .combine)
                    #endif

                    NavigationLink("Licences") {
                        LicencesView(noticeText: noticeText)
                    }
                    .accessibilityIdentifier("aboutLicencesLink")
                }
            }
            #if os(iOS)
            .listStyle(.insetGrouped)
            #endif
            .navigationTitle("Settings")
            #if !SKIP && os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                #if !SKIP && os(iOS)
                ToolbarItem(placement: .cancellationAction) {
                    Button(role: .close) {
                        dismiss()
                    }
                    .accessibilityIdentifier("aboutDoneButton")
                }
                #else
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") {
                        dismiss()
                    }
                    .accessibilityIdentifier("aboutDoneButton")
                }
                #endif
            }
        }
        .onAppear {
            _ = appModel.refreshFullScaleAvailability()
            if versionText.isEmpty {
                versionText = PlatformSupport.appVersionString()
            }
            if noticeText.isEmpty {
                noticeText = PlatformSupport.loadNoticeText()
            }
        }
    }

    @ViewBuilder
    private func factRow(symbol: String, title: String, detail: String) -> some View {
        Label {
            VStack(alignment: .leading, spacing: 2.0) {
                Text(title)
                    .font(.body)
                Text(detail)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        } icon: {
            Image(systemName: symbol)
                .foregroundStyle(DesignTokens.ink)
        }
        .padding(.vertical, 2.0)
        #if !SKIP
        .accessibilityElement(children: .combine)
        #endif
    }
}

private struct LicencesView: View {
    let noticeText: String

    var body: some View {
        ScrollView {
            Text(noticeText.isEmpty ? "Licences unavailable." : noticeText)
                .font(.footnote)
                .monospaced()
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(16.0)
                #if !SKIP
                .textSelection(.enabled)
                #endif
        }
        .navigationTitle("Licences")
        #if !SKIP && os(iOS)
        .navigationBarTitleDisplayMode(.inline)
        #endif
    }
}
