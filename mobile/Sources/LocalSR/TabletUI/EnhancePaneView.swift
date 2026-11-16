import SwiftUI
import LocalSRCore
#if !SKIP
import UniformTypeIdentifiers
#endif

struct TabletEnhanceMenuContent: View {
    @Environment(TabletModel.self) private var model

    let onOpenLibrary: () -> Void
    let onSaveAll: () -> Void
    let onClearFinished: () -> Void
    let onDeleteAllResults: () -> Void
    let onOpenSettings: () -> Void

    var body: some View {
        Button {
            onOpenLibrary()
        } label: {
            Label("Model Library", systemImage: "square.stack.3d.up")
        }
        .accessibilityIdentifier("tabletModelLibraryMenuItem")

        Divider()

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

        Button {
            onOpenSettings()
        } label: {
            Label("About LocalSR", systemImage: "info.circle")
        }
        .accessibilityIdentifier("tabletAboutMenuItem")
    }
}

struct EnhancePaneView: View {
    @Environment(TabletModel.self) private var model
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    #if !SKIP
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    #endif

    @AppStorage("fullScale") private var fullScale: Bool = false

    let isThreePane: Bool
    let isTwoColumn: Bool
    let onOpenLibrary: () -> Void
    let onSaveAll: () -> Void
    let onClearFinished: () -> Void
    let onDeleteAllResults: () -> Void
    let onOpenSettings: () -> Void

    @State private var isOutputExpanded: Bool = false
    @State private var isShowingSaveRecipeAlert: Bool = false
    @State private var recipeNameDraft: String = ""
    @State private var recipeToRename: TabletRecipe? = nil
    @State private var renameRecipeDraft: String = ""
    @State private var isFolderImporterPresented: Bool = false
    @State private var checkingStep: Int = 0
    @State private var checkingTimerTask: Task<Void, Never>? = nil
    #if SKIP
    @State private var sheetRecipe: TabletRecipe? = nil
    #endif

    private var stackQualityCards: Bool {
        #if !SKIP
        return dynamicTypeSize.isAccessibilitySize
        #else
        return false
        #endif
    }

    private var showOutputBody: Bool {
        !isThreePane || isOutputExpanded
    }

    var body: some View {
        VStack(spacing: 0.0) {
            if isThreePane {
                heading
            }

            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: Theme.Spacing.lg) {
                        if model.isBatchRunning {
                            settingsLockedBanner
                        }

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
                .onChange(of: model.scrollToChipsTrigger) { _, _ in
                    if reduceMotion {
                        proxy.scrollTo("enhanceChipsSection", anchor: .top)
                    } else {
                        withAnimation(.smooth(duration: 0.25)) {
                            proxy.scrollTo("enhanceChipsSection", anchor: .top)
                        }
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Theme.panel)
        .onChange(of: fullScale) { _, newValue in
            model.fullScale = newValue
        }
        .onChange(of: model.isCheckingDevice) { _, checking in
            updateCheckingTicker(active: checking)
        }
        .onAppear {
            updateCheckingTicker(active: model.isCheckingDevice)
        }
        .onDisappear {
            checkingTimerTask?.cancel()
            checkingTimerTask = nil
        }
        .alert("Save as Recipe", isPresented: $isShowingSaveRecipeAlert) {
            TextField("Name this setup", text: $recipeNameDraft)
            Button("Cancel", role: .cancel) {
                recipeNameDraft = ""
            }
            Button("Save") {
                model.saveRecipe(name: recipeNameDraft)
                recipeNameDraft = ""
            }
        } message: {
            Text("Saves the task, model, fixes, size, and format.")
        }
        .alert(
            "Rename Recipe",
            isPresented: Binding(
                get: { recipeToRename != nil },
                set: { if !$0 { recipeToRename = nil } }
            )
        ) {
            TextField("Recipe name", text: $renameRecipeDraft)
            Button("Cancel", role: .cancel) {
                recipeToRename = nil
                renameRecipeDraft = ""
            }
            Button("Save") {
                if let target = recipeToRename {
                    model.renameRecipe(id: target.id, name: renameRecipeDraft)
                }
                recipeToRename = nil
                renameRecipeDraft = ""
            }
        }
        #if !SKIP
        .fileImporter(
            isPresented: $isFolderImporterPresented,
            allowedContentTypes: [.folder],
            allowsMultipleSelection: false
        ) { result in
            if case .success(let urls) = result, let folderURL = urls.first {
                model.setSaveFolder(folderURL)
            }
        }
        #else
        .sheet(item: $sheetRecipe) { recipe in
            androidRecipeActionSheet(recipe)
        }
        #endif
    }

    // MARK: - Heading & Locked Notice

    private var heading: some View {
        HStack(spacing: Theme.Spacing.sm) {
            Text("Enhance")
                .themeFont(.paneTitle)
                .foregroundStyle(Theme.text)

            Text(model.headingSummary)
                .themeFont(.meta)
                .foregroundStyle(Theme.secondary)
                .lineLimit(1)
                .truncationMode(.tail)
                .accessibilityIdentifier("tabletEnhanceSummary")

            Spacer(minLength: 4.0)

            Menu {
                TabletEnhanceMenuContent(
                    onOpenLibrary: onOpenLibrary,
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

    private var settingsLockedBanner: some View {
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
        #if !SKIP
        .accessibilityElement(children: .combine)
        #endif
        .accessibilityIdentifier("tabletSettingsLockedNotice")
    }

    // MARK: - Columns

    private var leftColumn: some View {
        VStack(alignment: .leading, spacing: 0.0) {
            taskSection
            sectionRule
            qualityAndRecipesSection
            sectionRule
            yourPhotoSection
        }
    }

    private var rightColumn: some View {
        VStack(alignment: .leading, spacing: 0.0) {
            modelPlanSection
            sectionRule
            chipsSection
                .id("enhanceChipsSection")
            sectionRule
            outputSection
            if !model.queueItems.isEmpty {
                sectionRule
                queueSection
            }
        }
    }

    // MARK: - 1. Task

    private var taskSection: some View {
        VStack(alignment: .leading, spacing: Theme.Spacing.sm) {
            Text("Task")
                .themeFont(.label)
                .foregroundStyle(Theme.text)

            HStack(spacing: 2.0) {
                segmentButton(
                    title: TabletTaskKind.upscale.title,
                    isSelected: model.setup.task == .upscale,
                    identifier: "tabletTaskUpscaleButton"
                ) {
                    model.setTask(.upscale)
                }
                segmentButton(
                    title: TabletTaskKind.restore.title,
                    isSelected: model.setup.task == .restore,
                    identifier: "tabletTaskRestoreButton"
                ) {
                    model.setTask(.restore)
                }
            }
            .padding(3.0)
            .frame(height: Theme.Spacing.controlHeight)
            .background(
                RoundedRectangle(cornerRadius: Theme.Radius.control, style: .continuous)
                    .fill(Theme.recess)
            )
            .disabled(model.isBatchRunning)

            Text(model.setup.task.caption)
                .themeFont(.meta)
                .foregroundStyle(Theme.secondary)
        }
    }

    // MARK: - 2. Quality & Recipes

    private var qualityAndRecipesSection: some View {
        let isUpscale = model.setup.task == .upscale
        let presets = isUpscale ? model.availablePresets(for: model.setup.content) : []

        return VStack(alignment: .leading, spacing: Theme.Spacing.sm) {
            HStack(alignment: .firstTextBaseline, spacing: Theme.Spacing.sm) {
                Text(isUpscale ? "Quality" : "Recipes")
                    .themeFont(.label)
                    .foregroundStyle(Theme.text)

                Spacer(minLength: 4.0)

                Button {
                    recipeNameDraft = ""
                    isShowingSaveRecipeAlert = true
                } label: {
                    Text("Save as Recipe\u{2026}")
                        .themeFont(.meta, weight: .medium)
                        .foregroundStyle(model.isBatchRunning ? Theme.tertiary : Theme.text)
                }
                .buttonStyle(.plain)
                .disabled(model.isBatchRunning)
                .accessibilityIdentifier("tabletSaveRecipeButton")
            }

            if isUpscale && !presets.isEmpty {
                if stackQualityCards {
                    VStack(spacing: 6.0) {
                        ForEach(presets, id: \.rawValue) { preset in
                            qualityCard(preset)
                        }
                    }
                    .disabled(model.isBatchRunning)
                } else {
                    HStack(alignment: .top, spacing: 6.0) {
                        ForEach(presets, id: \.rawValue) { preset in
                            qualityCard(preset)
                        }
                    }
                    .fixedSize(horizontal: false, vertical: true)
                    .disabled(model.isBatchRunning)
                }
            }

            if !model.recipes.isEmpty {
                VStack(spacing: 6.0) {
                    ForEach(model.recipes) { recipe in
                        recipeRow(recipe)
                    }
                }
                .padding(.top, isUpscale ? 4.0 : 0.0)
            }
        }
    }

    private func qualityCard(_ preset: TabletQualityPreset) -> some View {
        let modelID = preset.modelID
        let isSelected = !model.setup.isManualModelChoice && modelID != nil && model.setup.upscaleModel == modelID
        let secondLine: String = {
            if let id = modelID, let measured = model.measuredSeconds(for: id) {
                return "\u{2248} \(TabletModel.formatDurationCompact(measured)) per photo"
            }
            return preset.subtitle
        }()

        return Button {
            model.selectPreset(preset)
        } label: {
            VStack(alignment: .leading, spacing: 2.0) {
                Text(preset.title)
                    .themeFont(.control, weight: .semibold)
                    .foregroundStyle(Theme.text)
                    .lineLimit(1)
                Text(secondLine)
                    .themeFont(.meta)
                    .foregroundStyle(Theme.secondary)
                    .lineLimit(2)
                    .minimumScaleFactor(0.85)
                    .multilineTextAlignment(.leading)
            }
            .frame(maxWidth: .infinity, minHeight: 64.0, maxHeight: .infinity, alignment: .topLeading)
            .padding(.horizontal, 8.0)
            .padding(.vertical, 8.0)
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
        .tabletDisableLiftHover()
        .accessibilityAddTraits(isSelected ? [.isSelected] : [])
        .accessibilityIdentifier("tabletQualityCard_\(preset.rawValue)")
    }

    private func recipeRow(_ recipe: TabletRecipe) -> some View {
        let isActive = model.matchingRecipe?.id == recipe.id
        return HStack(spacing: Theme.Spacing.sm) {
            Button {
                model.applyRecipe(recipe)
            } label: {
                VStack(alignment: .leading, spacing: 1.0) {
                    Text(recipe.name)
                        .themeFont(.control)
                        .foregroundStyle(Theme.text)
                        .lineLimit(1)
                    Text(recipe.summaryText)
                        .themeFont(.meta)
                        .foregroundStyle(Theme.secondary)
                        .lineLimit(1)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .buttonStyle(.plain)
            .disabled(model.isBatchRunning)
            .accessibilityIdentifier("tabletRecipeRow_\(recipe.id)")

            Menu {
                Button {
                    recipeToRename = recipe
                    renameRecipeDraft = recipe.name
                } label: {
                    Label("Rename\u{2026}", systemImage: "pencil")
                }
                .disabled(model.isBatchRunning)

                Button(role: .destructive) {
                    model.deleteRecipe(id: recipe.id)
                } label: {
                    Label("Delete", systemImage: "trash")
                }
                .disabled(model.isBatchRunning)
            } label: {
                Image(systemName: "ellipsis")
                    .font(.system(size: 13.0, weight: .medium))
                    .foregroundStyle(Theme.tertiary)
                    .frame(width: 28.0, height: 28.0)
            }
            .disabled(model.isBatchRunning)
            .accessibilityLabel("Recipe options for \(recipe.name)")
            .accessibilityIdentifier("tabletRecipeMenu_\(recipe.id)")
        }
        .padding(.horizontal, Theme.Spacing.md)
        .frame(minHeight: Theme.Spacing.hitArea)
        .background(
            RoundedRectangle(cornerRadius: Theme.Radius.card, style: .continuous)
                .fill(isActive ? Theme.selectFill : Theme.recess)
        )
        .overlay(
            RoundedRectangle(cornerRadius: Theme.Radius.card, style: .continuous)
                .stroke(isActive ? Theme.selectLine : Color.clear, lineWidth: 1.0)
        )
        #if SKIP
        .tabletLongPressAction(enabled: !model.isBatchRunning) {
            sheetRecipe = recipe
        }
        #else
        .contextMenu {
            Button {
                recipeToRename = recipe
                renameRecipeDraft = recipe.name
            } label: {
                Label("Rename\u{2026}", systemImage: "pencil")
            }
            .disabled(model.isBatchRunning)

            Button(role: .destructive) {
                model.deleteRecipe(id: recipe.id)
            } label: {
                Label("Delete", systemImage: "trash")
            }
            .disabled(model.isBatchRunning)
        }
        #endif
    }

    // MARK: - 3. Your Photo / What to Fix

    private var yourPhotoSection: some View {
        let isUpscale = model.setup.task == .upscale

        return VStack(alignment: .leading, spacing: Theme.Spacing.md) {
            Text(isUpscale ? "Your photo" : "What to fix")
                .themeFont(.label)
                .foregroundStyle(Theme.text)

            if isUpscale {
                HStack(spacing: 2.0) {
                    segmentButton(
                        title: TabletContentKind.photo.title,
                        isSelected: model.setup.content == .photo,
                        identifier: "tabletContentPhotoButton"
                    ) {
                        model.setContent(.photo)
                    }
                    segmentButton(
                        title: TabletContentKind.illustration.title,
                        isSelected: model.setup.content == .illustration,
                        identifier: "tabletContentIllustrationButton"
                    ) {
                        model.setContent(.illustration)
                    }
                }
                .padding(3.0)
                .frame(height: Theme.Spacing.controlHeight)
                .background(
                    RoundedRectangle(cornerRadius: Theme.Radius.control, style: .continuous)
                        .fill(Theme.recess)
                )
                .disabled(model.isBatchRunning)
            }

            VStack(alignment: .leading, spacing: Theme.Spacing.sm) {
                if isUpscale {
                    Text("Fix first")
                        .themeFont(.meta)
                        .foregroundStyle(Theme.secondary)
                }

                HStack(spacing: Theme.Spacing.sm) {
                    if model.isModelAvailable(.noiseFix) {
                        fixChip(
                            title: "Noise",
                            isOn: model.setup.fixNoise,
                            identifier: "tabletFixNoiseButton"
                        ) {
                            model.toggleFixNoise()
                        }
                    }

                    if model.isModelAvailable(.jpegFix) {
                        fixChip(
                            title: "JPEG artifacts",
                            isOn: model.setup.fixJpeg,
                            identifier: "tabletFixJpegButton"
                        ) {
                            model.toggleFixJpeg()
                        }
                    }
                }
                .disabled(model.isBatchRunning)
            }

            if model.setup.fixJpeg && model.isModelAvailable(.jpegFix) {
                VStack(alignment: .leading, spacing: Theme.Spacing.sm) {
                    Text("JPEG strength")
                        .themeFont(.meta)
                        .foregroundStyle(Theme.secondary)

                    HStack(spacing: 2.0) {
                        Text("Auto")
                            .themeFont(.control, weight: .semibold)
                            .foregroundStyle(Theme.text)
                            .frame(maxWidth: .infinity, maxHeight: .infinity)
                            .background(
                                RoundedRectangle(cornerRadius: Theme.Radius.control, style: .continuous)
                                    .fill(Theme.raised)
                            )
                    }
                    .padding(3.0)
                    .frame(height: Theme.Spacing.controlHeight)
                    .background(
                        RoundedRectangle(cornerRadius: Theme.Radius.control, style: .continuous)
                            .fill(Theme.recess)
                    )
                    .accessibilityIdentifier("tabletJpegStrengthAuto")

                    Text("FBCNN estimates compression strength from each photo automatically.")
                        .themeFont(.meta)
                        .foregroundStyle(Theme.secondary)
                        .accessibilityIdentifier("tabletJpegStrengthNote")
                }
            }
        }
    }

    private func fixChip(title: String, isOn: Bool, identifier: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 6.0) {
                if isOn {
                    Image(systemName: "checkmark")
                        .font(.system(size: 12.0, weight: .semibold))
                        .foregroundStyle(Theme.text)
                }
                Text(title)
                    .themeFont(.control, weight: isOn ? .semibold : .medium)
                    .foregroundStyle(Theme.text)
                    .lineLimit(1)
            }
            .padding(.horizontal, Theme.Spacing.md)
            .frame(height: Theme.Spacing.controlHeight)
            .background(
                RoundedRectangle(cornerRadius: Theme.Radius.control, style: .continuous)
                    .fill(isOn ? Theme.selectFill : Theme.recess)
            )
            .overlay(
                RoundedRectangle(cornerRadius: Theme.Radius.control, style: .continuous)
                    .stroke(isOn ? Theme.selectLine : Color.clear, lineWidth: 1.0)
            )
        }
        .buttonStyle(.plain)
        .tabletDisableLiftHover()
        .accessibilityValue(isOn ? "On" : "Off")
        .accessibilityAddTraits(isOn ? [.isSelected] : [])
        .accessibilityIdentifier(identifier)
    }

    // MARK: - 4. Model Plan

    private var modelPlanSection: some View {
        let stages = model.activePlan.stages

        return VStack(alignment: .leading, spacing: Theme.Spacing.sm) {
            HStack(alignment: .firstTextBaseline, spacing: Theme.Spacing.sm) {
                Text("Model")
                    .themeFont(.label)
                    .foregroundStyle(Theme.text)

                Spacer(minLength: 4.0)

                if model.setup.isManualModelChoice {
                    Button {
                        model.resetToBestModel()
                    } label: {
                        Text("Back to Best")
                            .themeFont(.meta, weight: .medium)
                            .foregroundStyle(model.isBatchRunning ? Theme.tertiary : Theme.text)
                    }
                    .buttonStyle(.plain)
                    .disabled(model.isBatchRunning)
                    .accessibilityIdentifier("tabletModelBackToBestButton")
                }

                Button {
                    onOpenLibrary()
                } label: {
                    Text("Change\u{2026}")
                        .themeFont(.meta, weight: .medium)
                        .foregroundStyle(model.isBatchRunning ? Theme.tertiary : Theme.text)
                }
                .buttonStyle(.plain)
                .disabled(model.isBatchRunning)
                .accessibilityIdentifier("tabletModelChangeButton")
            }

            VStack(alignment: .leading, spacing: 0.0) {
                ForEach(Array(stages.enumerated()), id: \.offset) { index, stageModel in
                    planRow(
                        stageModel: stageModel,
                        stageIndex: index,
                        hasPrevious: index > 0,
                        hasNext: index < stages.count - 1
                    )
                }
            }
        }
    }

    private enum PlanNodeState {
        case idle
        case running
        case done
    }

    private func planNodeState(stageIndex: Int) -> PlanNodeState {
        if let selected = model.selectedQueueItem {
            if selected.status == .running && !model.isCheckingDevice {
                if stageIndex < selected.stageIndex { return .done }
                if stageIndex == selected.stageIndex { return .running }
                return .idle
            }
            if selected.status == .done {
                return .done
            }
            return .idle
        }
        if model.selectedResultItem != nil && !model.isBatchRunning && model.batchLastRunSummary != nil {
            return .done
        }
        return .idle
    }

    private func planRow(stageModel: TabletModelID, stageIndex: Int, hasPrevious: Bool, hasNext: Bool) -> some View {
        let nodeState = planNodeState(stageIndex: stageIndex)
        let title = TabletEnhanceSetup.stageTitle(for: stageModel, outputSize: model.setup.outputSize)
        let detail = TabletEnhanceSetup.stageModelDetail(for: stageModel)
        let chipText = model.chipLabel(for: stageModel)

        return HStack(spacing: Theme.Spacing.md) {
            VStack(spacing: 0.0) {
                Rectangle()
                    .fill(hasPrevious ? Theme.border : Color.clear)
                    .frame(width: 1.0)
                    .frame(maxHeight: .infinity)

                RoundedRectangle(cornerRadius: Theme.Radius.node, style: .continuous)
                    .fill(nodeState == .running ? Theme.accent : (nodeState == .done ? Theme.text : Color.clear))
                    .frame(width: 8.0, height: 8.0)
                    .overlay(
                        RoundedRectangle(cornerRadius: Theme.Radius.node, style: .continuous)
                            .stroke(
                                nodeState == .running ? Theme.accent : (nodeState == .done ? Theme.text : Theme.tertiary),
                                lineWidth: 1.0
                            )
                    )

                Rectangle()
                    .fill(hasNext ? Theme.border : Color.clear)
                    .frame(width: 1.0)
                    .frame(maxHeight: .infinity)
            }
            .frame(width: 10.0)
            .frame(height: 52.0)

            VStack(alignment: .leading, spacing: 2.0) {
                HStack(spacing: Theme.Spacing.sm) {
                    Text(title)
                        .themeFont(.control, weight: .semibold)
                        .foregroundStyle(Theme.text)
                        .lineLimit(1)

                    Spacer(minLength: 4.0)

                    Text(chipText)
                        .themeFont(.meta)
                        .foregroundStyle(Theme.secondary)
                        .lineLimit(1)
                }

                Text(detail)
                    .themeFont(.meta)
                    .foregroundStyle(Theme.secondary)
                    .lineLimit(1)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .frame(minHeight: 52.0)
        #if !SKIP
        .accessibilityElement(children: .combine)
        #endif
        .accessibilityIdentifier("tabletPlanRow_\(stageModel.rawValue)")
    }

    // MARK: - 5. Chips

    private var chipsSection: some View {
        let lanes = model.visibleLanes

        return VStack(alignment: .leading, spacing: Theme.Spacing.sm) {
            HStack(alignment: .firstTextBaseline, spacing: Theme.Spacing.sm) {
                Text("Chips")
                    .themeFont(.label)
                    .foregroundStyle(Theme.text)

                Spacer(minLength: 4.0)

                if lanes.count > 1 {
                    Menu {
                        ForEach(TabletLaneChoice.allCases, id: \.rawValue) { choice in
                            Button {
                                model.updateLaneChoice(choice)
                            } label: {
                                if model.laneChoice == choice {
                                    Label(choice.menuTitle(isAndroid: TabletPlatform.isAndroid), systemImage: "checkmark")
                                } else {
                                    Text(choice.menuTitle(isAndroid: TabletPlatform.isAndroid))
                                }
                            }
                        }
                    } label: {
                        HStack(spacing: 4.0) {
                            Text(model.laneChoice.shortTitle(isAndroid: TabletPlatform.isAndroid))
                                .themeFont(.meta, weight: .medium)
                            Image(systemName: "chevron.down")
                                .font(.system(size: 10.0, weight: .semibold))
                        }
                        .foregroundStyle(model.isBatchRunning ? Theme.tertiary : Theme.text)
                    }
                    .disabled(model.isBatchRunning)
                    .accessibilityLabel("Compute chip, \(model.laneChoice.menuTitle(isAndroid: TabletPlatform.isAndroid))")
                    .accessibilityIdentifier("tabletChipsMenu")
                }
            }

            VStack(alignment: .leading, spacing: Theme.Spacing.xs) {
                ForEach(lanes, id: \.self) { lane in
                    laneRow(lane: lane, identifier: laneIdentifier(for: lane))
                }

                if !model.supportsBackgroundGPU && model.batchLastRunSummary == nil && !model.isBatchRunning {
                    Text("In the background \(TabletPlatform.deviceNoun) works on the \(TabletPlatform.npuName) only.")
                        .themeFont(.meta)
                        .foregroundStyle(Theme.secondary)
                        .padding(.top, 2.0)
                }

                if let summary = model.lastBatchLaneSummary, !model.isBatchRunning {
                    Text(summary)
                        .themeFont(.meta)
                        .foregroundStyle(Theme.secondary)
                        .padding(.top, 2.0)
                        .accessibilityIdentifier("tabletLastBatchLine")
                }
            }
        }
    }

    private func laneIdentifier(for lane: ModelLane) -> String {
        switch lane {
        case .neuralEngine: return "tabletLaneRow_neuralEngine"
        case .gpu: return "tabletLaneRow_gpu"
        case .cpu: return "tabletLaneRow_cpu"
        }
    }

    private struct LaneDisplayState {
        let line2: String
        let showMeter: Bool
        let litCount: Int
        let steppingIndex: Int?
        let isDimmed: Bool
        let isWarningCells: Bool
        let isWarningTitle: Bool
    }

    private func computeLaneState(_ lane: ModelLane) -> LaneDisplayState {
        let stages = model.activePlan.stages
        let unit: ComputeUnit = lane == .gpu ? .gpu : (lane == .cpu ? .cpu : .npu)
        let isSwitchedOff = (lane == .neuralEngine && model.laneChoice == .gpuOnly)
            || (lane == .gpu && model.laneChoice == .neuralEngineOnly)

        if isSwitchedOff {
            return LaneDisplayState(
                line2: "Off",
                showMeter: false,
                litCount: 0,
                steppingIndex: nil,
                isDimmed: false,
                isWarningCells: false,
                isWarningTitle: false
            )
        }

        if lane == .neuralEngine && !model.isNeuralEngineAvailable {
            return LaneDisplayState(
                line2: "Not available on \(TabletPlatform.deviceNoun)",
                showMeter: false,
                litCount: 0,
                steppingIndex: nil,
                isDimmed: false,
                isWarningCells: false,
                isWarningTitle: false
            )
        }

        let isPausedBackground = lane == .gpu && model.isBatchRunning && model.isAppInBackground && !model.supportsBackgroundGPU
        if isPausedBackground {
            return LaneDisplayState(
                line2: "Paused while LocalSR is in the background",
                showMeter: true,
                litCount: 0,
                steppingIndex: nil,
                isDimmed: false,
                isWarningCells: false,
                isWarningTitle: true
            )
        }

        let activity = model.laneActivities[lane]
        if model.isBatchRunning, let activity, let pid = activity.photoID {
            let fileName = model.queueItems.first(where: { $0.id == pid })?.displayName ?? "photo"
            let stageVerb: String = {
                switch activity.model {
                case .jpegFix: return "Fixing JPEG"
                case .noiseFix: return "Denoising"
                default: return "Upscaling"
                }
            }()
            let lit = Int((min(max(activity.busyFraction, 0.0), 1.0) * 12.0).rounded())
            if model.isThermalSlowed {
                return LaneDisplayState(
                    line2: "Slowed down, \(TabletPlatform.isAndroid ? "tablet" : "iPad") is warm",
                    showMeter: true,
                    litCount: lit,
                    steppingIndex: nil,
                    isDimmed: false,
                    isWarningCells: true,
                    isWarningTitle: false
                )
            }
            return LaneDisplayState(
                line2: "\(stageVerb) \u{00B7} \(fileName)",
                showMeter: true,
                litCount: lit,
                steppingIndex: nil,
                isDimmed: false,
                isWarningCells: false,
                isWarningTitle: false
            )
        }

        let stagesUsingLane = stagesForLane(lane)
        let failedForExpectedLane = !stages.isEmpty && stagesUsingLane.isEmpty && stages.allSatisfy { stageModel in
            if model.failedModels.contains(stageModel) { return true }
            let desc = ModelCatalog.model(stageModel)
            if let v = model.verdictsByModel[stageModel] {
                return desc.preferredLane == lane && !v.supports(unit)
            }
            return false
        }

        if failedForExpectedLane {
            let ranAndFailed: Bool = {
                if lane != .neuralEngine { return true }
                return stages.contains { stageModel in
                    if let v = model.verdictsByModel[stageModel] {
                        return v.neuralEngineChecked && !v.neuralEngine
                    }
                    return false
                }
            }()
            return LaneDisplayState(
                line2: ranAndFailed
                    ? "Not used: gave wrong results on \(TabletPlatform.deviceNoun)"
                    : "Not available on \(TabletPlatform.deviceNoun)",
                showMeter: false,
                litCount: 0,
                steppingIndex: nil,
                isDimmed: false,
                isWarningCells: false,
                isWarningTitle: false
            )
        }

        if model.isBatchRunning && model.isCheckingDevice {
            return LaneDisplayState(
                line2: "Checking\u{2026}",
                showMeter: true,
                litCount: 0,
                steppingIndex: checkingStep % 12,
                isDimmed: false,
                isWarningCells: false,
                isWarningTitle: false
            )
        }

        if model.isBatchRunning && !stagesUsingLane.isEmpty {
            let otherLane: ModelLane = lane == .gpu ? .neuralEngine : .gpu
            let otherBusy = model.laneActivities[otherLane]?.photoID != nil
            if otherBusy {
                return LaneDisplayState(
                    line2: lane == .gpu ? "Waiting for the \(TabletPlatform.npuName)" : "Waiting for the GPU",
                    showMeter: true,
                    litCount: 0,
                    steppingIndex: nil,
                    isDimmed: false,
                    isWarningCells: false,
                    isWarningTitle: false
                )
            }
        }

        if stagesUsingLane.isEmpty {
            return LaneDisplayState(
                line2: "Not needed for this setup",
                showMeter: true,
                litCount: 0,
                steppingIndex: nil,
                isDimmed: true,
                isWarningCells: false,
                isWarningTitle: false
            )
        }

        let idleText = idleLaneLine(lane: lane, stageModels: stagesUsingLane)
        return LaneDisplayState(
            line2: idleText,
            showMeter: true,
            litCount: 0,
            steppingIndex: nil,
            isDimmed: false,
            isWarningCells: false,
            isWarningTitle: false
        )
    }

    private func stagesForLane(_ lane: ModelLane) -> [TabletModelID] {
        let unit: ComputeUnit = lane == .gpu ? .gpu : (lane == .cpu ? .cpu : .npu)
        return model.activePlan.stages.filter { stageModel in
            if TabletPlatform.isAndroid && model.visibleLanes == [.cpu] {
                return lane == .cpu
            }
            switch model.laneChoice {
            case .neuralEngineOnly:
                guard lane == .neuralEngine else { return false }
                return model.verdictsByModel[stageModel]?.neuralEngine ?? true
            case .gpuOnly:
                guard lane == .gpu else { return false }
                return model.verdictsByModel[stageModel]?.gpu ?? true
            case .both:
                if let verdict = model.verdictsByModel[stageModel] {
                    return verdict.supports(unit)
                }
                if TabletPlatform.isAndroid {
                    return lane == model.visibleLanes.first || ModelCatalog.model(stageModel).preferredLane == lane
                }
                return ModelCatalog.model(stageModel).preferredLane == lane
            }
        }
    }

    private func idleLaneLine(lane: ModelLane, stageModels: [TabletModelID]) -> String {
        let otherLaneName = lane == .gpu ? TabletPlatform.npuName : "GPU"
        if model.visibleLanes.count > 1 && model.laneChoice == .both && stageModels.count == 1, let only = stageModels.first,
           let verdict = model.verdictsByModel[only],
           verdict.neuralEngine && verdict.gpu {
            let stageName = TabletEnhanceSetup.stageTitle(for: only, outputSize: model.setup.outputSize)
            return "Shares \(stageName) with the \(otherLaneName)"
        }
        let names = stageModels.map { TabletEnhanceSetup.stageTitle(for: $0, outputSize: model.setup.outputSize) }
        return "Will run " + names.joined(separator: ", ")
    }

    private func laneRow(lane: ModelLane, identifier: String) -> some View {
        let name: String = {
            switch lane {
            case .gpu: return "GPU"
            case .cpu: return "CPU"
            case .neuralEngine: return TabletPlatform.npuName
            }
        }()
        let state = computeLaneState(lane)

        return VStack(alignment: .leading, spacing: 4.0) {
            HStack(spacing: Theme.Spacing.sm) {
                Text(name)
                    .themeFont(.control, weight: .semibold)
                    .foregroundStyle(state.isWarningTitle ? Theme.warning : Theme.text)
                    .lineLimit(1)

                Spacer(minLength: 4.0)

                if state.showMeter {
                    laneMeter12(
                        litCount: state.litCount,
                        steppingIndex: state.steppingIndex,
                        isWarning: state.isWarningCells
                    )
                }
            }

            Text(state.line2)
                .themeFont(.meta)
                .foregroundStyle(Theme.secondary)
                .lineLimit(1)
                .truncationMode(.middle)
        }
        .frame(maxWidth: .infinity, minHeight: 56.0, alignment: .leading)
        .opacity(state.isDimmed ? 0.4 : 1.0)
        #if !SKIP
        .accessibilityElement(children: .ignore)
        #endif
        .accessibilityLabel("\(name), \(state.line2)")
        .accessibilityIdentifier(identifier)
    }

    private func laneMeter12(litCount: Int, steppingIndex: Int?, isWarning: Bool) -> some View {
        let litColor = isWarning ? Theme.warning : Theme.text
        return HStack(spacing: 2.0) {
            ForEach(0..<12, id: \.self) { index in
                let isLit = steppingIndex != nil ? (index == steppingIndex) : (index < litCount)
                RoundedRectangle(cornerRadius: Theme.Radius.meterCell, style: .continuous)
                    .fill(isLit ? litColor : Theme.track)
                    .frame(width: 8.0, height: 8.0)
            }
        }
        .animation(reduceMotion ? nil : .linear(duration: 0.2), value: litCount)
    }

    // MARK: - 6. Output

    private var outputSection: some View {
        let summaryText = outputSummaryLine()

        return VStack(alignment: .leading, spacing: Theme.Spacing.sm) {
            if isThreePane {
                Button {
                    if reduceMotion {
                        isOutputExpanded.toggle()
                    } else {
                        withAnimation(.smooth(duration: 0.2)) {
                            isOutputExpanded.toggle()
                        }
                    }
                } label: {
                    HStack(spacing: Theme.Spacing.sm) {
                        Text("Output")
                            .themeFont(.label)
                            .foregroundStyle(Theme.text)

                        Spacer(minLength: 4.0)

                        Text(summaryText)
                            .themeFont(.meta)
                            .foregroundStyle(Theme.secondary)
                            .lineLimit(1)

                        Image(systemName: isOutputExpanded ? "chevron.up" : "chevron.down")
                            .font(.system(size: 11.0, weight: .semibold))
                            .foregroundStyle(Theme.tertiary)
                    }
                    .frame(minHeight: Theme.Spacing.controlHeight)
                }
                .buttonStyle(.plain)
                .tabletDisableLiftHover()
                .accessibilityLabel("Output, \(summaryText)")
                .accessibilityValue(isOutputExpanded ? "Expanded" : "Collapsed")
                .accessibilityIdentifier("tabletOutputDisclosureButton")
            } else {
                Text("Output")
                    .themeFont(.label)
                    .foregroundStyle(Theme.text)
            }

            if showOutputBody {
                outputBody
            }
        }
    }

    private var outputBody: some View {
        let isUpscale = model.setup.task == .upscale
        let formatOptions: [TabletOutputFormat] = TabletPlatform.isAndroid ? [.jpeg, .png] : TabletOutputFormat.allCases
        let sizeDisplay = outputSizeDisplayText()
        let showMemoryFallbackNote = model.selectedResultItem?.fullScaleFallback == true

        return VStack(alignment: .leading, spacing: Theme.Spacing.md) {
            if isUpscale {
                VStack(alignment: .leading, spacing: 4.0) {
                    HStack(spacing: Theme.Spacing.sm) {
                        Text("Size")
                            .themeFont(.control)
                            .foregroundStyle(Theme.text)

                        Spacer(minLength: 4.0)

                        Menu {
                            ForEach(TabletOutputSizeChoice.allCases, id: \.rawValue) { choice in
                                Button {
                                    model.setOutputSize(choice)
                                } label: {
                                    if model.setup.outputSize == choice {
                                        Label(choice.label, systemImage: "checkmark")
                                    } else {
                                        Text(choice.label)
                                    }
                                }
                            }
                        } label: {
                            HStack(spacing: 4.0) {
                                Text(sizeDisplay)
                                    .themeFont(.meta)
                                    .foregroundStyle(Theme.secondary)
                                    .lineLimit(1)
                                Image(systemName: "chevron.down")
                                    .font(.system(size: 10.0, weight: .semibold))
                                    .foregroundStyle(Theme.tertiary)
                            }
                        }
                        .disabled(model.isBatchRunning)
                        .accessibilityLabel("Output size, \(sizeDisplay)")
                        .accessibilityIdentifier("tabletOutputSizeMenu")
                    }
                    .frame(minHeight: Theme.Spacing.controlHeight)

                    if showMemoryFallbackNote {
                        Text("4\u{00D7} needs more memory than \(TabletPlatform.deviceNoun) has free. This photo is saved at 2\u{00D7}.")
                            .themeFont(.meta)
                            .foregroundStyle(Theme.secondary)
                    }
                }
            }

            HStack(spacing: Theme.Spacing.sm) {
                Text("Format")
                    .themeFont(.control)
                    .foregroundStyle(Theme.text)

                Spacer(minLength: 4.0)

                Menu {
                    ForEach(formatOptions, id: \.rawValue) { format in
                        Button {
                            model.setOutputFormat(format)
                        } label: {
                            if model.setup.outputFormat == format {
                                Label(format.displayName, systemImage: "checkmark")
                            } else {
                                Text(format.displayName)
                            }
                        }
                    }
                } label: {
                    HStack(spacing: 4.0) {
                        Text(model.setup.outputFormat.displayName)
                            .themeFont(.meta)
                            .foregroundStyle(Theme.secondary)
                        Image(systemName: "chevron.down")
                            .font(.system(size: 10.0, weight: .semibold))
                            .foregroundStyle(Theme.tertiary)
                    }
                }
                .disabled(model.isBatchRunning)
                .accessibilityLabel("Output format, \(model.setup.outputFormat.displayName)")
                .accessibilityIdentifier("tabletOutputFormatMenu")
            }
            .frame(minHeight: Theme.Spacing.controlHeight)

            VStack(alignment: .leading, spacing: 4.0) {
                Toggle("Full 4\u{00D7} for Large Photos", isOn: $fullScale)
                    .themeFont(.control)
                    .foregroundStyle(Theme.text)
                    .tint(Theme.text)
                    .disabled(model.isBatchRunning)
                    .accessibilityIdentifier("tabletFullScaleToggle")

                Text("Photos over 3 MP get 2\u{00D7} by default. Full 4\u{00D7} takes longer to save and needs more memory.")
                    .themeFont(.meta)
                    .foregroundStyle(Theme.secondary)
            }

            Toggle(
                "Save Automatically",
                isOn: Binding(
                    get: { model.autoSave },
                    set: { model.updateAutoSave($0) }
                )
            )
            .themeFont(.control)
            .foregroundStyle(Theme.text)
            .tint(Theme.text)
            .disabled(model.isBatchRunning)
            .accessibilityIdentifier("tabletAutoSaveToggle")

            if model.autoSave {
                HStack(spacing: Theme.Spacing.sm) {
                    Text("Save to")
                        .themeFont(.control)
                        .foregroundStyle(Theme.text)

                    Spacer(minLength: 4.0)

                    Menu {
                        Button {
                            model.updateSaveDestination(.photos)
                        } label: {
                            if model.saveDestination == .photos {
                                Label(TabletPlatform.libraryName, systemImage: "checkmark")
                            } else {
                                Text(TabletPlatform.libraryName)
                            }
                        }

                        #if !SKIP
                        Button {
                            isFolderImporterPresented = true
                        } label: {
                            Label("Choose Folder\u{2026}", systemImage: "folder")
                        }
                        #endif
                    } label: {
                        HStack(spacing: 4.0) {
                            Text(saveDestinationLabel)
                                .themeFont(.meta)
                                .foregroundStyle(Theme.secondary)
                                .lineLimit(1)
                            Image(systemName: "chevron.down")
                                .font(.system(size: 10.0, weight: .semibold))
                                .foregroundStyle(Theme.tertiary)
                        }
                    }
                    .disabled(model.isBatchRunning)
                    .accessibilityLabel("Save destination, \(saveDestinationLabel)")
                    .accessibilityIdentifier("tabletSaveToMenu")
                }
                .frame(minHeight: Theme.Spacing.controlHeight)
            }
        }
    }

    private var saveDestinationLabel: String {
        if model.saveDestination == .folder {
            return model.saveFolderDisplayName ?? "Folder"
        }
        return TabletPlatform.libraryName
    }

    private func outputSummaryLine() -> String {
        let sizePart = model.setup.task == .restore ? "1\u{00D7}" : model.setup.outputSize.label
        let formatPart = model.setup.outputFormat.displayName
        let savePart = model.autoSave ? saveDestinationLabel : "Ask"
        return "\(sizePart) \u{00B7} \(formatPart) \u{00B7} \(savePart)"
    }

    private func outputSizeDisplayText() -> String {
        let sizeChoice = model.setup.outputSize
        if let qItem = model.selectedQueueItem, let pw = qItem.pixelWidth, let ph = qItem.pixelHeight, pw > 0, ph > 0 {
            let pred = model.setup.predictedOutputSize(inputWidth: pw, inputHeight: ph, fullScale: model.fullScale)
            return "\(pred.scale)\u{00D7} \u{00B7} \(UIFormatting.resultDimensionsLine(width: pred.width, height: pred.height))"
        }
        if let res = model.selectedResultItem, let iw = res.inputWidth, let ih = res.inputHeight, iw > 0, ih > 0 {
            let pred = model.setup.predictedOutputSize(inputWidth: iw, inputHeight: ih, fullScale: model.fullScale)
            return "\(pred.scale)\u{00D7} \u{00B7} \(UIFormatting.resultDimensionsLine(width: pred.width, height: pred.height))"
        }
        return sizeChoice.label
    }

    // MARK: - 7. Queue

    private var queueSection: some View {
        let count = model.queueItems.count
        let photoWord = count == 1 ? "photo" : "photos"

        return VStack(alignment: .leading, spacing: Theme.Spacing.sm) {
            Text("Queue")
                .themeFont(.label)
                .foregroundStyle(Theme.text)

            if model.isBatchRunning {
                if let perPhoto = model.estimatedSecondsPerPhotoForActivePlan() {
                    Text("This photo \u{2248} \(TabletModel.formatDurationCompact(perPhoto))")
                        .themeFont(.meta)
                        .foregroundStyle(Theme.secondary)
                }

                if let whole = model.estimatedRemainingSeconds() {
                    Text("Whole queue \u{2248} \(TabletModel.formatDurationCompact(whole))")
                        .themeFont(.meta)
                        .foregroundStyle(Theme.secondary)
                } else {
                    Text("\(count) \(photoWord) \u{00B7} time is measured during the first one")
                        .themeFont(.meta)
                        .foregroundStyle(Theme.secondary)
                }

                if let nextName = model.nextQueuedItemName,
                   let perPhoto = model.estimatedSecondsPerPhotoForActivePlan() {
                    Text("Next \(nextName) \u{2248} \(TabletModel.formatDurationCompact(perPhoto))")
                        .themeFont(.meta)
                        .foregroundStyle(Theme.secondary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
            } else if let perPhoto = model.estimatedSecondsPerPhotoForActivePlan() {
                let targetCount = max(1, model.waitingQueueItems.isEmpty ? count : model.waitingQueueItems.count)
                let est = perPhoto * Double(targetCount)
                Text("\(count) \(photoWord) \u{00B7} about \(TabletModel.formatDurationCompact(est)) with this setup")
                    .themeFont(.meta)
                    .foregroundStyle(Theme.secondary)
                    .accessibilityIdentifier("tabletQueueEstimateLine")
            } else {
                Text("\(count) \(photoWord) \u{00B7} time is measured during the first one")
                    .themeFont(.meta)
                    .foregroundStyle(Theme.secondary)
                    .accessibilityIdentifier("tabletQueueEstimateLine")
            }
        }
    }

    // MARK: - Helpers

    private func segmentButton(title: String, isSelected: Bool, identifier: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title)
                .themeFont(.control, weight: isSelected ? .semibold : .medium)
                .foregroundStyle(isSelected ? Theme.text : Theme.secondary)
                .lineLimit(1)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(
                    RoundedRectangle(cornerRadius: Theme.Radius.control, style: .continuous)
                        .fill(isSelected ? Theme.raised : Color.clear)
                )
        }
        .buttonStyle(.plain)
        .tabletDisableLiftHover()
        .accessibilityAddTraits(isSelected ? [.isSelected] : [])
        .accessibilityIdentifier(identifier)
    }

    #if SKIP
    private func androidRecipeActionSheet(_ recipe: TabletRecipe) -> some View {
        VStack(alignment: .leading, spacing: Theme.Spacing.sm) {
            Text(recipe.name)
                .themeFont(.paneTitle)
                .foregroundStyle(Theme.text)
                .lineLimit(1)
                .padding(.bottom, Theme.Spacing.xs)

            Button {
                sheetRecipe = nil
                recipeToRename = recipe
                renameRecipeDraft = recipe.name
            } label: {
                HStack(spacing: Theme.Spacing.md) {
                    Image(systemName: "pencil")
                        .font(.system(size: 16.0, weight: .medium))
                        .frame(width: 24.0)
                    Text("Rename\u{2026}")
                        .themeFont(.control, weight: .medium)
                    Spacer()
                }
                .foregroundStyle(Theme.text)
                .padding(.horizontal, Theme.Spacing.md)
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

            Button {
                sheetRecipe = nil
                model.deleteRecipe(id: recipe.id)
            } label: {
                HStack(spacing: Theme.Spacing.md) {
                    Image(systemName: "trash")
                        .font(.system(size: 16.0, weight: .medium))
                        .frame(width: 24.0)
                    Text("Delete")
                        .themeFont(.control, weight: .medium)
                    Spacer()
                }
                .foregroundStyle(Theme.danger)
                .padding(.horizontal, Theme.Spacing.md)
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
        }
        .padding(Theme.Spacing.lg)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.panel)
        .presentationDetents([.height(210.0)])
    }
    #endif

    private var sectionRule: some View {
        Rectangle()
            .fill(Theme.border)
            .frame(height: 1.0)
            .padding(.vertical, Theme.Spacing.sectionGap)
    }

    private func updateCheckingTicker(active: Bool) {
        checkingTimerTask?.cancel()
        checkingTimerTask = nil
        if !active || reduceMotion {
            checkingStep = 0
            return
        }
        checkingTimerTask = Task { @MainActor in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 500_000_000)
                if !Task.isCancelled {
                    checkingStep = (checkingStep + 1) % 12
                }
            }
        }
    }
}
