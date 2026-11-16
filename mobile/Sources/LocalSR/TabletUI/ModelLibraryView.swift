import SwiftUI
import LocalSRCore

struct ModelLibraryView: View {
    @Environment(TabletModel.self) private var model
    @Environment(\.dismiss) private var dismiss

    @State private var selectedModelID: TabletModelID

    init(initialModel: TabletModelID) {
        _selectedModelID = State(initialValue: initialModel)
    }

    private var visibleGroups: [(group: TabletLibraryGroup, models: [ModelDescriptor])] {
        TabletLibraryGroup.allCases.compactMap { group in
            let list = model.availableLibraryModels(in: group)
            return list.isEmpty ? nil : (group, list)
        }
    }

    private var allVisibleModels: [ModelDescriptor] {
        visibleGroups.flatMap(\.models)
    }

    private var activeDescriptor: ModelDescriptor? {
        if let match = allVisibleModels.first(where: { $0.id == selectedModelID }) {
            return match
        }
        return allVisibleModels.first
    }

    var body: some View {
        NavigationStack {
            GeometryReader { geo in
                let isWide = geo.size.width >= Theme.Spacing.twoColumnWidth
                Group {
                    if isWide {
                        HStack(spacing: 0.0) {
                            libraryList
                                .frame(maxWidth: .infinity, maxHeight: .infinity)

                            Rectangle()
                                .fill(Theme.divider)
                                .frame(width: 1.0)

                            if let descriptor = activeDescriptor {
                                detailScroll(descriptor)
                                    .frame(width: 320.0)
                                    .frame(maxHeight: .infinity)
                            }
                        }
                    } else {
                        VStack(spacing: 0.0) {
                            libraryList
                                .frame(maxWidth: .infinity, maxHeight: .infinity)

                            if let descriptor = activeDescriptor {
                                Rectangle()
                                    .fill(Theme.divider)
                                    .frame(height: 1.0)

                                detailScroll(descriptor)
                                    .frame(maxWidth: .infinity)
                                    .frame(maxHeight: min(340.0, geo.size.height * 0.48))
                            }
                        }
                    }
                }
                .background(Theme.panel)
            }
            .navigationTitle("Model Library")
            #if !SKIP && os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                #if !SKIP && os(iOS)
                ToolbarItem(placement: .cancellationAction) {
                    Button(role: .close) {
                        dismiss()
                    }
                    .accessibilityIdentifier("tabletModelLibraryCloseButton")
                }
                #else
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") {
                        dismiss()
                    }
                    .accessibilityIdentifier("tabletModelLibraryCloseButton")
                }
                #endif
            }
        }
        #if !SKIP
        .presentationSizing(.page)
        #endif
    }

    private var libraryList: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: Theme.Spacing.lg) {
                ForEach(visibleGroups, id: \.group.rawValue) { entry in
                    VStack(alignment: .leading, spacing: Theme.Spacing.sm) {
                        Text(entry.group.title)
                            .themeFont(.label)
                            .foregroundStyle(Theme.text)
                            .accessibilityIdentifier("tabletLibraryGroup_\(entry.group.rawValue)")

                        VStack(spacing: 6.0) {
                            ForEach(entry.models, id: \.id.rawValue) { desc in
                                libraryRow(desc)
                            }
                        }
                    }
                }
            }
            .padding(Theme.Spacing.panePadding)
        }
        .background(Theme.panel)
    }

    private func libraryRow(_ desc: ModelDescriptor) -> some View {
        let isSelected = activeDescriptor?.id == desc.id
        return Button {
            selectedModelID = desc.id
        } label: {
            VStack(alignment: .leading, spacing: 6.0) {
                HStack(alignment: .firstTextBaseline, spacing: Theme.Spacing.sm) {
                    Text(desc.fullName)
                        .themeFont(.control, weight: .semibold)
                        .foregroundStyle(Theme.text)
                        .lineLimit(1)

                    if desc.displayName != desc.fullName && desc.libraryGroup != .fixNoiseAndJpeg {
                        Text(desc.displayName)
                            .themeFont(.caption)
                            .foregroundStyle(Theme.secondary)
                    }

                    Spacer(minLength: 4.0)

                    Text(model.chipLabel(for: desc.id))
                        .themeFont(.meta)
                        .foregroundStyle(Theme.secondary)
                        .lineLimit(1)
                }

                Text(desc.summary)
                    .themeFont(.meta)
                    .foregroundStyle(Theme.secondary)
                    .lineLimit(1)
                    .truncationMode(.tail)
                    .multilineTextAlignment(.leading)

                HStack(spacing: Theme.Spacing.lg) {
                    ratingDots(title: "Quality", count: desc.qualityRating)
                    ratingDots(title: "Speed", count: desc.speedRating)
                    Spacer()
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(Theme.Spacing.md)
            .background(
                RoundedRectangle(cornerRadius: Theme.Radius.card, style: .continuous)
                    .fill(isSelected ? Theme.selectFill : Theme.recess)
            )
            .overlay(
                RoundedRectangle(cornerRadius: Theme.Radius.card, style: .continuous)
                    .stroke(isSelected ? Theme.selectLine : Color.clear, lineWidth: 1.0)
            )
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("tabletLibraryRow_\(desc.id.rawValue)")
    }

    private func ratingDots(title: String, count: Int) -> some View {
        HStack(spacing: 6.0) {
            Text(title)
                .themeFont(.caption)
                .foregroundStyle(Theme.secondary)
            HStack(spacing: 3.0) {
                ForEach(1...4, id: \.self) { index in
                    Circle()
                        .fill(index <= count ? Theme.text : Theme.track)
                        .frame(width: 5.0, height: 5.0)
                }
            }
        }
        #if !SKIP
        .accessibilityElement(children: .ignore)
        #endif
        .accessibilityLabel("\(title) \(count) of 4")
    }

    private func detailScroll(_ desc: ModelDescriptor) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Theme.Spacing.lg) {
                VStack(alignment: .leading, spacing: 4.0) {
                    Text(desc.fullName)
                        .themeFont(.paneTitle)
                        .foregroundStyle(Theme.text)
                        .accessibilityIdentifier("tabletLibraryDetailTitle")

                    Text(desc.summary)
                        .themeFont(.body)
                        .foregroundStyle(Theme.secondary)
                }

                HStack(spacing: Theme.Spacing.lg) {
                    ratingDots(title: "Quality", count: desc.qualityRating)
                    ratingDots(title: "Speed", count: desc.speedRating)
                }

                VStack(spacing: 0.0) {
                    detailRow("Scale", value: "\(desc.scale)\u{00D7}")
                    detailRule
                    detailRow("Size", value: TabletModel.formatBytesCompact(model.modelByteSizes[desc.id] ?? desc.fallbackBytes))
                    detailRule
                    detailRow("Runs on", value: model.chipLabel(for: desc.id))
                    detailRule
                    detailRow("Licence", value: desc.licence)
                    detailRule
                    detailRow("Author", value: desc.author)
                    detailRule
                    sourceRow(desc.sourceURL)
                }
                .padding(.horizontal, Theme.Spacing.md)
                .background(
                    RoundedRectangle(cornerRadius: Theme.Radius.card, style: .continuous)
                        .fill(Theme.recess)
                )

                Button {
                    model.selectModelFromLibrary(desc.id)
                    dismiss()
                } label: {
                    Text("Use for This Job")
                        .themeFont(.control, weight: .semibold)
                        .foregroundStyle(model.isBatchRunning ? Theme.secondary : Theme.text)
                        .frame(maxWidth: .infinity)
                        .frame(height: Theme.Spacing.hitArea)
                        .background(
                            RoundedRectangle(cornerRadius: Theme.Radius.control, style: .continuous)
                                .fill(Theme.raised)
                        )
                        .overlay(
                            RoundedRectangle(cornerRadius: Theme.Radius.control, style: .continuous)
                                .stroke(Theme.border, lineWidth: 1.0)
                        )
                }
                .buttonStyle(.plain)
                .disabled(model.isBatchRunning)
                .accessibilityIdentifier("tabletLibraryUseButton")
            }
            .padding(Theme.Spacing.panePadding)
        }
        .background(Theme.panel)
        .accessibilityIdentifier("tabletLibraryDetail")
    }

    private func detailRow(_ title: String, value: String) -> some View {
        HStack(alignment: .top, spacing: Theme.Spacing.sm) {
            Text(title)
                .themeFont(.meta)
                .foregroundStyle(Theme.secondary)
            Spacer(minLength: 8.0)
            Text(value)
                .themeFont(.meta)
                .foregroundStyle(Theme.text)
                .multilineTextAlignment(.trailing)
        }
        .padding(.vertical, 10.0)
    }

    @ViewBuilder
    private func sourceRow(_ urlString: String) -> some View {
        HStack(alignment: .top, spacing: Theme.Spacing.sm) {
            Text("Source")
                .themeFont(.meta)
                .foregroundStyle(Theme.secondary)
            Spacer(minLength: 8.0)
            #if !SKIP
            if let url = URL(string: urlString) {
                Link(sourceDisplayText(urlString), destination: url)
                    .themeFont(.meta)
                    .foregroundStyle(Theme.text)
                    .lineLimit(1)
                    .truncationMode(.middle)
            } else {
                Text(urlString)
                    .themeFont(.meta)
                    .foregroundStyle(Theme.text)
                    .lineLimit(1)
            }
            #else
            Text(sourceDisplayText(urlString))
                .themeFont(.meta)
                .foregroundStyle(Theme.text)
                .lineLimit(1)
                .truncationMode(.middle)
            #endif
        }
        .padding(.vertical, 10.0)
    }

    private func sourceDisplayText(_ raw: String) -> String {
        raw.replacingOccurrences(of: "https://", with: "")
    }

    private var detailRule: some View {
        Rectangle()
            .fill(Theme.border)
            .frame(height: 1.0)
    }
}
