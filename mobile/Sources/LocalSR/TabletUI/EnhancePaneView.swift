import SwiftUI
import LocalSRCore

struct TabletEnhanceMenuContent: View {
    @Environment(TabletModel.self) private var model

    let onSaveAll: () -> Void
    let onClearFinished: () -> Void
    let onDeleteAllResults: () -> Void
    let onOpenSettings: () -> Void

    var body: some View {
        Button {
            onSaveAll()
        } label: {
            Label("Save All to \(TabletPlatform.libraryName)", systemImage: "square.and.arrow.down.on.square")
        }
        .disabled(model.unsavedResultsCount == 0 || model.isBatchRunning || model.isSaving || model.isSavingAll)
        .accessibilityIdentifier("tabletSaveAllMenuItem")

        Button {
            onClearFinished()
        } label: {
            Label("Clear Finished", systemImage: "checkmark.circle")
        }
        .disabled(!model.queueItems.contains(where: { $0.status == .done }) || model.isBatchRunning)
        .accessibilityIdentifier("tabletClearFinishedMenuItem")

        Button(role: .destructive) {
            onDeleteAllResults()
        } label: {
            Label("Delete All Results\u{2026}", systemImage: "trash")
        }
        .disabled(model.results.isEmpty || model.isBatchRunning)
        .accessibilityIdentifier("tabletDeleteAllResultsMenuItem")

        Divider()

        Button {
            onOpenSettings()
        } label: {
            Label("Settings", systemImage: "gearshape")
        }
        .accessibilityIdentifier("tabletSettingsMenuItem")
    }
}

struct EnhancePaneView: View {
    @Environment(TabletModel.self) private var model

    let isThreePane: Bool
    let isTwoColumn: Bool
    let onSaveAll: () -> Void
    let onClearFinished: () -> Void
    let onDeleteAllResults: () -> Void
    let onOpenSettings: () -> Void

    var body: some View {
        VStack(spacing: 0.0) {
            if isThreePane {
                heading
            }

            ScrollView {
                Group {
                    if isTwoColumn {
                        HStack(alignment: .top, spacing: Theme.Spacing.lg) {
                            leftColumn
                                .frame(maxWidth: 520.0, alignment: .leading)
                            rightColumn
                                .frame(maxWidth: 520.0, alignment: .leading)
                        }
                        .frame(maxWidth: .infinity, alignment: .center)
                    } else {
                        VStack(alignment: .leading, spacing: 0.0) {
                            leftColumn
                            sectionRule
                            rightColumn
                        }
                    }
                }
                .padding(Theme.Spacing.panePadding)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Theme.panel)
    }

    private var heading: some View {
        HStack(spacing: Theme.Spacing.sm) {
            Text("Enhance")
                .themeFont(.paneTitle)
                .foregroundStyle(Theme.text)

            Text("Best \u{00B7} Photo")
                .themeFont(.meta)
                .foregroundStyle(Theme.secondary)
                .lineLimit(1)

            Spacer(minLength: 4.0)

            Menu {
                TabletEnhanceMenuContent(
                    onSaveAll: onSaveAll,
                    onClearFinished: onClearFinished,
                    onDeleteAllResults: onDeleteAllResults,
                    onOpenSettings: onOpenSettings
                )
            } label: {
                Image(systemName: "ellipsis")
                    .font(.system(size: 16.0, weight: .medium))
                    .foregroundStyle(Theme.text)
                    .frame(width: 36.0, height: 36.0)
            }
            .accessibilityLabel("More")
            .accessibilityIdentifier("tabletEnhanceMoreMenu")
        }
        .padding(.horizontal, Theme.Spacing.lg)
        .frame(height: Theme.Spacing.headerHeight)
    }

    private var leftColumn: some View {
        VStack(alignment: .leading, spacing: Theme.Spacing.lg) {
            if model.isBatchRunning {
                VStack(alignment: .leading, spacing: 2.0) {
                    Text("Settings Locked")
                        .themeFont(.label)
                        .foregroundStyle(Theme.text)
                    Text("The queue uses this setup.")
                        .themeFont(.meta)
                        .foregroundStyle(Theme.secondary)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(Theme.Spacing.md)
                .background(
                    RoundedRectangle(cornerRadius: Theme.Radius.card, style: .continuous)
                        .fill(Theme.recess)
                )
            }

            VStack(alignment: .leading, spacing: Theme.Spacing.sm) {
                Text("Task")
                    .themeFont(.label)
                    .foregroundStyle(Theme.text)

                HStack {
                    Text("Upscale")
                        .themeFont(.control, weight: .semibold)
                        .foregroundStyle(Theme.text)
                    Spacer()
                    Text("4\u{00D7}")
                        .themeFont(.meta)
                        .foregroundStyle(Theme.secondary)
                }
                .padding(.horizontal, Theme.Spacing.md)
                .frame(height: Theme.Spacing.controlHeight)
                .background(
                    RoundedRectangle(cornerRadius: Theme.Radius.card, style: .continuous)
                        .fill(Theme.selectFill)
                )
                .overlay(
                    RoundedRectangle(cornerRadius: Theme.Radius.card, style: .continuous)
                        .stroke(Theme.selectLine, lineWidth: 1.0)
                )

                Text("Enlarges photos and artwork 4\u{00D7}.")
                    .themeFont(.meta)
                    .foregroundStyle(Theme.secondary)
            }

            sectionRule

            VStack(alignment: .leading, spacing: Theme.Spacing.sm) {
                Text("Quality")
                    .themeFont(.label)
                    .foregroundStyle(Theme.text)

                VStack(alignment: .leading, spacing: 2.0) {
                    Text("Best")
                        .themeFont(.control, weight: .semibold)
                        .foregroundStyle(Theme.text)
                    Text("PULSE-48 \u{00B7} More detail")
                        .themeFont(.meta)
                        .foregroundStyle(Theme.secondary)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(Theme.Spacing.md)
                .background(
                    RoundedRectangle(cornerRadius: Theme.Radius.card, style: .continuous)
                        .fill(Theme.selectFill)
                )
                .overlay(
                    RoundedRectangle(cornerRadius: Theme.Radius.card, style: .continuous)
                        .stroke(Theme.selectLine, lineWidth: 1.0)
                )
            }
        }
    }

    private var rightColumn: some View {
        VStack(alignment: .leading, spacing: Theme.Spacing.lg) {
            VStack(alignment: .leading, spacing: Theme.Spacing.sm) {
                Text("Model")
                    .themeFont(.label)
                    .foregroundStyle(Theme.text)

                HStack(spacing: Theme.Spacing.md) {
                    let isRunning = model.isBatchRunning && !model.isCheckingDevice
                    let isDone = !model.isBatchRunning && model.batchLastRunSummary != nil
                    RoundedRectangle(cornerRadius: Theme.Radius.node, style: .continuous)
                        .fill(isRunning ? Theme.accent : (isDone ? Theme.text : Color.clear))
                        .frame(width: 8.0, height: 8.0)
                        .overlay(
                            RoundedRectangle(cornerRadius: Theme.Radius.node, style: .continuous)
                                .stroke(isRunning ? Theme.accent : (isDone ? Theme.text : Theme.tertiary), lineWidth: 1.0)
                        )

                    VStack(alignment: .leading, spacing: 2.0) {
                        HStack {
                            Text("Upscale \u{00D7}4")
                                .themeFont(.control, weight: .semibold)
                                .foregroundStyle(Theme.text)
                            Spacer()
                            Text(activeChipLabel)
                                .themeFont(.meta)
                                .foregroundStyle(Theme.secondary)
                        }
                        Text("PULSE-48")
                            .themeFont(.meta)
                            .foregroundStyle(Theme.secondary)
                    }
                }
                .frame(minHeight: 52.0)
            }

            if !model.queueItems.isEmpty {
                sectionRule

                VStack(alignment: .leading, spacing: Theme.Spacing.sm) {
                    Text("Queue")
                        .themeFont(.label)
                        .foregroundStyle(Theme.text)

                    let count = model.queueItems.count
                    Text(count == 1 ? "1 photo in this queue" : "\(count) photos in this queue")
                        .themeFont(.meta)
                        .foregroundStyle(Theme.secondary)
                }
            }
        }
    }

    private var activeChipLabel: String {
        if let active = model.queueItems.first(where: { $0.status == .running }), let chip = active.chipName {
            return chip
        }
        if let known = model.lastKnownChipByModel[.best] {
            return known
        }
        return "Checked on first use"
    }

    private var sectionRule: some View {
        Rectangle()
            .fill(Theme.border)
            .frame(height: 1.0)
            .padding(.vertical, Theme.Spacing.sectionGap)
    }
}
