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
                    VStack(spacing: 0.0) {
                        ZStack {
                            RoundedRectangle(cornerRadius: 16.0, style: .continuous)
                                .fill(DesignTokens.iconCream)
                                .frame(width: 72.0, height: 72.0)

                            FrameMark(
                                side: 44.0,
                                bracketColor: DesignTokens.iconInk,
                                squareColor: DesignTokens.iconRust
                            )
                        }
                        .accessibilityHidden(true)
                        .padding(.top, 24.0)
                        .padding(.bottom, 12.0)

                        Text("LocalSR")
                            .font(.title2.bold())

                        Text("Version \(versionText)")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                            .adaptiveMonospacedDigit()
                            .padding(.top, 2.0)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.bottom, 8.0)
                    .listRowBackground(Color.clear)
                    #if !SKIP
                    .listRowInsets(EdgeInsets())
                    #endif
                }

                Section {
                    factRow(
                        symbol: "wifi.slash",
                        title: "Works offline",
                        detail: "No network, no account, nothing uploaded."
                    )
                    factRow(
                        symbol: "arrow.up.left.and.arrow.down.right",
                        title: "4\u{00D7} larger, up to 48 MP",
                        detail: "Photos over 3 MP are scaled down first."
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
                    NavigationLink("Licences") {
                        LicencesView(noticeText: noticeText)
                    }
                    .accessibilityIdentifier("aboutLicencesLink")
                }
            }
            #if os(iOS)
            .listStyle(.insetGrouped)
            #endif
            .navigationTitle("About")
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
