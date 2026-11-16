import SwiftUI
import LocalSRCore

struct StatusBarView: View {
    @Environment(TabletModel.self) private var model

    let width: CGFloat
    let isTabMode: Bool
    let onTapLanes: () -> Void

    private var isCompactWidth: Bool {
        TabletLayoutMetrics.forWidth(Double(width)).isCompactStatusBar
    }

    private var waitingCount: Int {
        model.waitingQueueItems.count
    }

    private var activeProgress: Double {
        if model.isCheckingDevice { return 0.0 }
        return model.wholeQueueProgress
    }

    var body: some View {
        VStack(spacing: 0.0) {
            topEdgeRule

            Group {
                if isCompactWidth {
                    VStack(alignment: .leading, spacing: Theme.Spacing.sm) {
                        HStack(spacing: Theme.Spacing.md) {
                            statusCopyBlock
                            Spacer(minLength: 4.0)
                            if model.isBatchRunning && !model.isCheckingDevice {
                                laneMinis(compact: true)
                            }
                        }
                        actionButtonsRow(fullWidth: true)
                    }
                    .padding(.horizontal, Theme.Spacing.lg)
                    .padding(.vertical, Theme.Spacing.sm)
                    .frame(minHeight: Theme.Spacing.statusBarTwoRowHeight)
                } else {
                    HStack(spacing: Theme.Spacing.lg) {
                        statusCopyBlock
                        Spacer(minLength: Theme.Spacing.md)
                        if model.isBatchRunning && !model.isCheckingDevice {
                            laneMinis(compact: width < Theme.Spacing.foldWidth)
                        }
                        actionButtonsRow(fullWidth: false)
                    }
                    .padding(.horizontal, Theme.Spacing.lg)
                    .frame(height: Theme.Spacing.statusBarHeight)
                }
            }
            .background(Theme.chrome)
        }
        .background(Theme.chrome)
    }

    @ViewBuilder
    private var topEdgeRule: some View {
        if model.isBatchRunning && !model.isCheckingDevice {
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Rectangle()
                        .fill(Theme.border)
                        .frame(height: 1.0)
                    Rectangle()
                        .fill(Theme.accent)
                        .frame(width: max(4.0, geo.size.width * activeProgress), height: 2.0)
                }
            }
            .frame(height: 2.0)
        } else {
            Rectangle()
                .fill(Theme.border)
                .frame(height: 1.0)
        }
    }

    private var statusCopyBlock: some View {
        HStack(spacing: Theme.Spacing.sm) {
            statusIndicator

            VStack(alignment: .leading, spacing: 1.0) {
                Text(statusTitle)
                    .themeFont(.control, weight: .semibold)
                    .foregroundStyle(Theme.text)
                    .lineLimit(1)
                    .accessibilityIdentifier("tabletStatusTitle")

                Text(statusDetail)
                    .themeFont(.meta)
                    .foregroundStyle(Theme.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .accessibilityIdentifier("tabletStatusDetail")
            }
        }
    }

    @ViewBuilder
    private var statusIndicator: some View {
        if model.isBatchRunning {
            Circle()
                .fill(Theme.accent)
                .frame(width: 8.0, height: 8.0)
        } else if model.isPausedBySystem {
            Circle()
                .fill(Theme.warning)
                .frame(width: 8.0, height: 8.0)
        } else if let laneFailure = model.laneValidationError, laneFailure.allFailed {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 12.0))
                .foregroundStyle(Theme.text)
        } else if let summary = model.batchLastRunSummary, waitingCount == 0 {
            switch summary {
            case .complete:
                Circle()
                    .fill(Theme.ok)
                    .frame(width: 8.0, height: 8.0)
            case .partialFailure:
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(.system(size: 12.0))
                    .foregroundStyle(Theme.text)
            }
        }
    }

    private var statusTitle: String {
        if model.isBatchRunning {
            if model.isCheckingDevice {
                return "Getting Ready\u{2026}"
            }
            let total = max(1, model.batchTotalInRun)
            let current = min(model.batchCompletedInRun + 1, total)
            let pct = Int((activeProgress * 100.0).rounded())
            let verb = model.setup.task == .restore ? "Restoring" : "Upscaling"
            return "\(verb) \(current) of \(total) \u{00B7} \(pct) %"
        }

        if model.isPausedBySystem {
            return TabletPlatform.isAndroid ? "Paused by Android" : "Paused by iPadOS"
        }

        if let laneFailure = model.laneValidationError, laneFailure.allFailed {
            return laneFailure.title
        }

        if waitingCount == 0, let summary = model.batchLastRunSummary {
            switch summary {
            case .complete:
                return "Complete"
            case .partialFailure(let succeeded, let total, _):
                let verb = model.setup.task == .restore ? "Restored" : "Upscaled"
                return "\(succeeded) of \(total) \(verb)"
            }
        }

        return "Ready"
    }

    private var statusDetail: String {
        if let transient = model.transientStatusMessage {
            return transient
        }
        if model.isLoadingInputs {
            return "Loading photos\u{2026}"
        }
        if model.isBatchRunning {
            if model.isCheckingDevice {
                return "Checking which chip runs \(model.checkingModelDisplayName) correctly."
            }
            var parts: [String] = []
            if let eta = model.estimatedRemainingSeconds() {
                parts.append("About \(TabletModel.formatDurationCompact(eta)) left")
            }
            if let nextName = model.nextQueuedItemName {
                parts.append("next \(nextName)")
            }
            if parts.isEmpty {
                return "\(model.batchTotalInRun) \(model.batchTotalInRun == 1 ? "photo" : "photos")"
            }
            return parts.joined(separator: " \u{00B7} ")
        }

        if model.isPausedBySystem {
            return "\(model.pausedCompletedCount) of \(model.pausedTotalCount) done. Nothing is lost."
        }

        if let laneFailure = model.laneValidationError {
            return laneFailure.detail
        }

        if waitingCount == 0, let summary = model.batchLastRunSummary {
            switch summary {
            case .complete(let count, let seconds):
                let unit = count == 1 ? "photo" : "photos"
                return "\(count) \(unit) in \(TabletModel.formatDurationCompact(seconds))"
            case .partialFailure(_, _, let failed):
                let unit = failed == 1 ? "photo" : "photos"
                return "\(failed) \(unit) failed. Select one to see why."
            }
        }

        if waitingCount == 0 {
            return "Add photos to start."
        }
        if waitingCount == 1 {
            return "1 photo in the queue"
        }
        return "\(waitingCount) photos in the queue"
    }

    @ViewBuilder
    private func laneMinis(compact: Bool) -> some View {
        let lanes = model.visibleLanes

        Button {
            onTapLanes()
        } label: {
            HStack(spacing: Theme.Spacing.md) {
                ForEach(lanes, id: \.self) { lane in
                    let label = laneMiniLabel(for: lane, compact: compact)
                    let busy = model.laneActivities[lane]?.busyFraction ?? 0.0
                    laneMini(label: label, busy: busy)
                }
            }
        }
        .buttonStyle(.plain)
        .tabletDisableLiftHover()
        .accessibilityLabel("Chip activity, open Chips section")
        .accessibilityIdentifier("tabletStatusLaneMinis")
    }

    private func laneMiniLabel(for lane: ModelLane, compact: Bool) -> String {
        switch lane {
        case .neuralEngine:
            if TabletPlatform.isAndroid { return "NPU" }
            return compact ? "NE" : "Neural Engine"
        case .gpu:
            return "GPU"
        case .cpu:
            return "CPU"
        }
    }

    private func laneMini(label: String, busy: Double) -> some View {
        let lit = Int((min(max(busy, 0.0), 1.0) * 6.0).rounded())
        return HStack(spacing: 6.0) {
            Text(label)
                .themeFont(.caption)
                .foregroundStyle(Theme.secondary)
            HStack(spacing: 2.0) {
                ForEach(0..<6, id: \.self) { index in
                    RoundedRectangle(cornerRadius: Theme.Radius.meterCell, style: .continuous)
                        .fill(index < lit ? Theme.text : Theme.track)
                        .frame(width: 6.0, height: 6.0)
                }
            }
        }
    }

    @ViewBuilder
    private func actionButtonsRow(fullWidth: Bool) -> some View {
        HStack(spacing: Theme.Spacing.sm) {
            if model.isBatchRunning {
                if model.isCheckingDevice {
                    secondaryButton(title: "Cancel", fullWidth: fullWidth, identifier: "tabletCancelBatchButton") {
                        model.cancelQueue()
                    }
                } else {
                    if model.canAddSelectedToRunningBatch {
                        secondaryButton(title: "Add Selected to Queue", fullWidth: false, identifier: "tabletAddSelectedToQueueButton") {
                            model.addSelectedToRunningBatch()
                        }
                    }
                    dangerButton(title: "Cancel Queue", fullWidth: fullWidth, identifier: "tabletCancelBatchButton") {
                        model.cancelQueue()
                    }
                }
            } else if model.isPausedBySystem {
                if model.laneValidationError != nil {
                    disabledStartButton(title: "Resume", fullWidth: fullWidth)
                } else {
                    primaryStartButton(title: "Resume", fullWidth: fullWidth, identifier: "tabletResumeButton") {
                        model.resumePausedQueue(switchToViewer: isTabMode)
                    }
                    .disabled(model.isLoadingInputs)
                }
            } else if waitingCount == 0, let summary = model.batchLastRunSummary {
                switch summary {
                case .complete:
                    if model.unsavedResultsCount > 0 {
                        secondaryButton(
                            title: model.isSavingAll
                                ? "Saving \(model.saveAllDone) of \(model.saveAllTotal)\u{2026}"
                                : "Save All to \(TabletPlatform.libraryName)",
                            fullWidth: fullWidth,
                            identifier: "tabletStatusSaveAllButton"
                        ) {
                            Task { await model.saveAllResults() }
                        }
                        .disabled(model.isSaving || model.isSavingAll)
                    } else {
                        secondaryButton(
                            title: "Saved",
                            fullWidth: fullWidth,
                            identifier: "tabletStatusSavedButton"
                        ) {}
                        .disabled(true)
                    }

                case .partialFailure:
                    secondaryButton(title: "Try Again", fullWidth: fullWidth, identifier: "tabletTryAgainButton") {
                        model.retryFailed(switchToViewer: isTabMode)
                    }
                    if model.unsavedResultsCount > 0 {
                        secondaryButton(
                            title: "Save All to \(TabletPlatform.libraryName)",
                            fullWidth: false,
                            identifier: "tabletStatusSaveAllButton"
                        ) {
                            Task { await model.saveAllResults() }
                        }
                        .disabled(model.isSaving || model.isSavingAll)
                    }
                }
            } else if waitingCount == 0 {
                let startTitle = model.setup.task == .restore ? "Restore Selected" : "Upscale Selected"
                disabledStartButton(title: startTitle, fullWidth: fullWidth)
            } else if model.laneValidationError != nil {
                let title = waitingCount == 1
                    ? (model.setup.task == .restore ? "Restore Selected" : "Upscale Selected")
                    : "Start All \(waitingCount)"
                disabledStartButton(title: title, fullWidth: fullWidth)
            } else if waitingCount == 1 {
                let startTitle = model.setup.task == .restore ? "Restore Selected" : "Upscale Selected"
                primaryStartButton(title: startTitle, fullWidth: fullWidth, identifier: "tabletUpscaleSelectedButton") {
                    model.upscaleSelected(switchToViewer: isTabMode)
                }
                .disabled(model.isLoadingInputs)
            } else {
                if !fullWidth {
                    let selectedTitle = model.setup.task == .restore ? "Restore Selected" : "Upscale Selected"
                    secondaryButton(title: selectedTitle, fullWidth: false, identifier: "tabletUpscaleSelectedButton") {
                        model.upscaleSelected(switchToViewer: isTabMode)
                    }
                    .disabled(model.isLoadingInputs || !model.hasRunnableSelectedItem)
                }
                primaryStartButton(title: "Start All \(waitingCount)", fullWidth: fullWidth, identifier: "tabletStartAllButton") {
                    model.startAll(switchToViewer: isTabMode)
                }
                .disabled(model.isLoadingInputs)
            }
        }
    }

    private func primaryStartButton(title: String, fullWidth: Bool, identifier: String, action: @escaping () -> Void) -> some View {
        let minW: CGFloat? = fullWidth ? nil : (width < 820.0 ? 156.0 : 200.0)
        return Button(action: action) {
            Text(title)
                .themeFont(.control, weight: .semibold)
                .foregroundStyle(Color.white)
                .lineLimit(1)
                .fixedSize(horizontal: !fullWidth, vertical: false)
                .padding(.horizontal, Theme.Spacing.lg)
                .frame(minWidth: minW, maxWidth: fullWidth ? .infinity : nil)
                .frame(height: 44.0)
                .background(
                    RoundedRectangle(cornerRadius: Theme.Radius.control, style: .continuous)
                        .fill(Theme.accent)
                )
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier(identifier)
    }

    private func disabledStartButton(title: String, fullWidth: Bool) -> some View {
        let minW: CGFloat? = fullWidth ? nil : (width < 820.0 ? 156.0 : 200.0)
        return Button {} label: {
            Text(title)
                .themeFont(.control, weight: .medium)
                .foregroundStyle(Theme.secondary)
                .lineLimit(1)
                .fixedSize(horizontal: !fullWidth, vertical: false)
                .padding(.horizontal, Theme.Spacing.lg)
                .frame(minWidth: minW, maxWidth: fullWidth ? .infinity : nil)
                .frame(height: 44.0)
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
        .disabled(true)
        .accessibilityIdentifier("tabletUpscaleSelectedButton")
    }

    private func secondaryButton(title: String, fullWidth: Bool, identifier: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title)
                .themeFont(.control, weight: .medium)
                .foregroundStyle(Theme.text)
                .lineLimit(1)
                .fixedSize(horizontal: !fullWidth, vertical: false)
                .padding(.horizontal, Theme.Spacing.md)
                .frame(maxWidth: fullWidth ? .infinity : nil)
                .frame(height: 44.0)
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
        .accessibilityIdentifier(identifier)
    }

    private func dangerButton(title: String, fullWidth: Bool, identifier: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title)
                .themeFont(.control, weight: .medium)
                .foregroundStyle(Theme.danger)
                .lineLimit(1)
                .fixedSize(horizontal: !fullWidth, vertical: false)
                .padding(.horizontal, Theme.Spacing.md)
                .frame(maxWidth: fullWidth ? .infinity : nil)
                .frame(height: 44.0)
                .background(
                    RoundedRectangle(cornerRadius: Theme.Radius.control, style: .continuous)
                        .fill(Theme.danger.opacity(0.09))
                )
                .overlay(
                    RoundedRectangle(cornerRadius: Theme.Radius.control, style: .continuous)
                        .stroke(Theme.danger.opacity(0.25), lineWidth: 1.0)
                )
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier(identifier)
    }
}
