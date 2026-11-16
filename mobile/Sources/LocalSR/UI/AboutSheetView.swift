import SwiftUI
import LocalSRCore

struct AboutSheetView: View {
    @Environment(AppModel.self) private var appModel
    @Environment(\.dismiss) private var dismiss

    @AppStorage("fullScale") private var fullScale: Bool = false
    @AppStorage("shareStartsImmediately") private var shareStartsImmediately: Bool = true

    @State private var noticeText = ""
    @State private var isShowingFullScaleInfo = false
    @State private var versionText = ""

    /// The tablet shows this sheet as its Settings; its chips per model are in the Model Library.
    private var isTablet: Bool {
        TabletPlatform.isTablet
    }

    private var upscalingFooterText: String {
        let base = "Photos from 3 to 12 MP get 2\u{00D7} by default. Full 4\u{00D7} takes longer to save and needs more memory."
        if !appModel.isFullScaleAvailable {
            return "\(base) Not available right now: it needs more free memory."
        }
        return base
    }

    private let fullScaleInfoText = "Full 4\u{00D7} enlarges photos up to 12 MP four times, to at most 16128 \u{00D7} 12096 (195 MP).\n\nLarger photos, like 48 MP shots, are reduced to 12 MP first, so they come out at 2\u{00D7}, also 195 MP. A true 4\u{00D7} of 48 MP would be 780 MP: more memory than this device gives an app, and a file most apps can't open."

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Toggle("Full 4\u{00D7} for Large Photos", isOn: $fullScale)
                        .foregroundStyle(appModel.isFullScaleAvailable || fullScale ? .primary : .secondary)
                        .disabled(!appModel.isFullScaleAvailable && !fullScale)
                        .accessibilityIdentifier("fullScaleToggle")
                } header: {
                    Text("Upscaling")
                } footer: {
                    VStack(alignment: .leading, spacing: 6.0) {
                        Text(upscalingFooterText)
                        if isShowingFullScaleInfo {
                            Text(fullScaleInfoText)
                                .accessibilityIdentifier("fullScaleInfoText")
                        }
                        if appModel.isFullScaleAvailable || fullScale {
                            Button(isShowingFullScaleInfo ? "Hide" : "Why 195 MP?") {
                                withAnimation(.smooth(duration: 0.25)) {
                                    isShowingFullScaleInfo.toggle()
                                }
                            }
                            .font(.footnote)
                            .frame(minHeight: 44.0)
                            .accessibilityIdentifier("fullScaleInfoButton")
                        }
                    }
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
                    Text("“Upscale with LocalSR” appears when you share photos. To hide it, tap Edit Actions at the end of the share menu.")
                    #endif
                }

                if !isTablet {
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
                        detail: appModel.isFullScaleAvailable
                            ? "Up to 3 MP: 4\u{00D7}. Up to 12 MP: 2\u{00D7}, or 4\u{00D7} with Full 4\u{00D7}. Larger photos are reduced to 12 MP first."
                            : "Up to 3 MP: 4\u{00D7}. Up to 12 MP: 2\u{00D7}. Larger photos are reduced to 12 MP first."
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
                        // The tablet can save PNG, which has no HDR gain map.
                        detail: isTablet
                            ? "Keeps date and location, and HDR in HEIC and JPEG. Your original isn't changed."
                            : "Keeps date, location and HDR. Your original isn't changed."
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

                    // Required by App Store guideline 5.1.1: the privacy policy, reachable from inside the app.
                    if let privacyURL = URL(string: "https://herrei.github.io/localsr/privacy/") {
                        Link("Privacy Policy", destination: privacyURL)
                            .accessibilityIdentifier("aboutPrivacyLink")
                    }
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

private struct NoticeSection {
    var title: String
    var paragraphs: [String]
}

private struct LicencesView: View {
    let noticeText: String

    // NOTICE uses underlined headings and hard-wrapped paragraphs.
    private var sections: [NoticeSection] {
        let lines = noticeText.components(separatedBy: "\n")
        var result: [NoticeSection] = []
        var title = ""
        var paragraphs: [String] = []
        var paragraph = ""
        var index = 0

        func isRule(_ line: String) -> Bool {
            let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
            return trimmed.count >= 3 && (trimmed.allSatisfy { $0 == "-" } || trimmed.allSatisfy { $0 == "=" })
        }
        func flushParagraph() {
            if !paragraph.isEmpty {
                paragraphs.append(paragraph)
                paragraph = ""
            }
        }
        func flushSection() {
            flushParagraph()
            if !title.isEmpty || !paragraphs.isEmpty {
                result.append(NoticeSection(title: title, paragraphs: paragraphs))
            }
            paragraphs = []
        }
        while index < lines.count {
            let line = lines[index].trimmingCharacters(in: .whitespacesAndNewlines)
            if !line.isEmpty && !isRule(line) && index + 1 < lines.count && isRule(lines[index + 1]) {
                flushSection()
                title = line
                index += 2
                continue
            }
            if line.isEmpty || isRule(line) {
                flushParagraph()
            } else {
                paragraph += paragraph.isEmpty ? line : " " + line
            }
            index += 1
        }
        flushSection()
        return result
    }

    var body: some View {
        let noticeSections = sections
        ScrollView {
            VStack(alignment: .leading, spacing: 24.0) {
                if noticeSections.isEmpty {
                    Text("Licences unavailable.")
                }
                ForEach(0..<noticeSections.count, id: \.self) { index in
                    let section = noticeSections[index]
                    VStack(alignment: .leading, spacing: 12.0) {
                        if !section.title.isEmpty {
                            Text(section.title).font(.headline)
                        }
                        ForEach(0..<section.paragraphs.count, id: \.self) { paragraphIndex in
                            Text(section.paragraphs[paragraphIndex]).font(.footnote)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .padding(16.0)
            #if !SKIP
            .textSelection(.enabled)
            .environment(\.locale, Locale(identifier: "en"))
            #endif
        }
        .navigationTitle("Licences")
        #if !SKIP && os(iOS)
        .navigationBarTitleDisplayMode(.inline)
        #endif
    }
}
