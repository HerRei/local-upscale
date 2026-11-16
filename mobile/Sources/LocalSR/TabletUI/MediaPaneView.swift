import SwiftUI
import LocalSRCore

struct TabletAddMenuContent: View {
    let canPaste: Bool
    let onChoosePhotos: () -> Void
    let onChooseFiles: () -> Void
    let onChooseFolder: () -> Void
    let onPaste: () -> Void

    var body: some View {
        Button {
            onChoosePhotos()
        } label: {
            Label("Choose from \(TabletPlatform.libraryName)\u{2026}", systemImage: "photo.on.rectangle")
        }
        .accessibilityIdentifier("tabletAddPhotosMenuItem")

        Button {
            onChooseFiles()
        } label: {
            Label("Choose Files\u{2026}", systemImage: "doc")
        }
        .accessibilityIdentifier("tabletAddFilesMenuItem")

        Button {
            onChooseFolder()
        } label: {
            Label("Choose Folder\u{2026}", systemImage: "folder")
        }
        .accessibilityIdentifier("tabletAddFolderMenuItem")

        Divider()

        Button {
            onPaste()
        } label: {
            Label("Paste", systemImage: "doc.on.clipboard")
        }
        .disabled(!canPaste)
        .accessibilityIdentifier("tabletAddPasteMenuItem")
    }
}

struct MediaPaneView: View {
    @Environment(TabletModel.self) private var model
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    let isThreePane: Bool
    let showsGrid: Bool
    let onChoosePhotos: () -> Void
    let onChooseFiles: () -> Void
    let onChooseFolder: () -> Void
    let onPaste: () -> Void
    let onCollapsePane: () -> Void
    let onStartSingle: (String) -> Void
    let onConfirmDeleteResult: (TabletResultItem) -> Void

    @State private var hoveredID: String? = nil
    #if SKIP
    @State private var sheetQueueItem: TabletQueueItem? = nil
    @State private var sheetResultItem: TabletResultItem? = nil
    #endif

    private let gridColumns = [
        GridItem(.adaptive(minimum: 148.0), spacing: 8.0)
    ]

    var body: some View {
        VStack(spacing: 0.0) {
            if isThreePane {
                threePaneHeading
            }

            segmentBar
                .padding(.horizontal, 12.0)
                .padding(.top, isThreePane ? 4.0 : 12.0)
                .padding(.bottom, 10.0)

            contentArea
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Theme.panel)
        #if SKIP
        .sheet(item: $sheetQueueItem) { item in
            androidQueueActionSheet(item)
        }
        .sheet(item: $sheetResultItem) { result in
            androidResultActionSheet(result)
        }
        #endif
    }

    private var threePaneHeading: some View {
        HStack(spacing: Theme.Spacing.sm) {
            Button {
                if reduceMotion {
                    onCollapsePane()
                } else {
                    withAnimation(.smooth(duration: 0.25)) {
                        onCollapsePane()
                    }
                }
            } label: {
                Image(systemName: TabletPlatform.sidebarSymbol)
                    .font(.system(size: 16.0, weight: .medium))
                    .foregroundStyle(Theme.text)
                    .frame(width: 36.0, height: 36.0)
            }
            .buttonStyle(.plain)
            .tabletCornerOffsetLeading()
            .accessibilityLabel("Hide Media Pane")
            .accessibilityIdentifier("tabletCollapseMediaButton")

            Text("Media")
                .themeFont(.paneTitle)
                .foregroundStyle(Theme.text)

            if !model.queueItems.isEmpty || !model.results.isEmpty {
                let count = model.mediaSegment == .queue ? model.queueItems.count : model.results.count
                Text("\(count)")
                    .themeFont(.caption)
                    .foregroundStyle(Theme.tertiary)
            }

            Spacer(minLength: 4.0)

            Menu {
                TabletAddMenuContent(
                    canPaste: TabletPlatform.canPasteImages,
                    onChoosePhotos: onChoosePhotos,
                    onChooseFiles: onChooseFiles,
                    onChooseFolder: onChooseFolder,
                    onPaste: onPaste
                )
            } label: {
                Image(systemName: "plus")
                    .font(.system(size: 16.0, weight: .medium))
                    .foregroundStyle(Theme.text)
                    .frame(width: 36.0, height: 36.0)
            }
            .accessibilityLabel("Add Photos")
            .accessibilityIdentifier("tabletMediaAddButton")
        }
        .padding(.horizontal, Theme.Spacing.md)
        .frame(height: Theme.Spacing.headerHeight)
    }

    private var segmentBar: some View {
        HStack(spacing: 2.0) {
            segmentButton(
                title: model.queueItems.isEmpty ? "Queue" : "Queue \(model.queueItems.count)",
                segment: .queue,
                identifier: "tabletQueueSegmentButton"
            )
            segmentButton(
                title: model.results.isEmpty ? "Results" : "Results \(model.results.count)",
                segment: .results,
                identifier: "tabletResultsSegmentButton"
            )
        }
        .padding(3.0)
        .frame(height: Theme.Spacing.controlHeight)
        .background(
            RoundedRectangle(cornerRadius: Theme.Radius.control, style: .continuous)
                .fill(Theme.recess)
        )
    }

    private func segmentButton(title: String, segment: TabletMediaSegment, identifier: String) -> some View {
        let isSelected = model.mediaSegment == segment
        return Button {
            model.mediaSegment = segment
        } label: {
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

    @ViewBuilder
    private var contentArea: some View {
        switch model.mediaSegment {
        case .queue:
            if model.queueItems.isEmpty {
                queueEmptyState
            } else if showsGrid {
                queueGridView
            } else {
                queueListView
            }
        case .results:
            if model.results.isEmpty {
                resultsEmptyState
            } else if showsGrid {
                resultsGridView
            } else {
                resultsListView
            }
        }
    }

    private var queueEmptyState: some View {
        VStack(alignment: .center, spacing: Theme.Spacing.sm) {
            Spacer()
            Text("No photos yet")
                .themeFont(.control, weight: .semibold)
                .foregroundStyle(Theme.text)
            Text("A folder becomes a batch.")
                .themeFont(.meta)
                .foregroundStyle(Theme.secondary)
                .multilineTextAlignment(.center)

            HStack(spacing: Theme.Spacing.sm) {
                Button {
                    onChoosePhotos()
                } label: {
                    Text("\(TabletPlatform.libraryName)\u{2026}")
                        .themeFont(.control, weight: .medium)
                        .foregroundStyle(Theme.text)
                        .padding(.horizontal, Theme.Spacing.md)
                        .frame(height: Theme.Spacing.controlHeight)
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
                .accessibilityIdentifier("tabletEmptyPhotosButton")

                Button {
                    onChooseFiles()
                } label: {
                    Text("Files\u{2026}")
                        .themeFont(.control, weight: .medium)
                        .foregroundStyle(Theme.text)
                        .padding(.horizontal, Theme.Spacing.md)
                        .frame(height: Theme.Spacing.controlHeight)
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
                .accessibilityIdentifier("tabletEmptyFilesButton")
            }
            .padding(.top, Theme.Spacing.xs)
            Spacer()
        }
        .padding(Theme.Spacing.panePadding)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var resultsEmptyState: some View {
        VStack(alignment: .center, spacing: Theme.Spacing.sm) {
            Spacer()
            Text("No results yet")
                .themeFont(.control, weight: .semibold)
                .foregroundStyle(Theme.text)
            Text("Finished photos stay here until you save or delete them.")
                .themeFont(.meta)
                .foregroundStyle(Theme.secondary)
                .multilineTextAlignment(.center)
            Spacer()
        }
        .padding(Theme.Spacing.panePadding)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: - Queue List (width < 600)

    private var queueListView: some View {
        List {
            ForEach(model.queueItems) { item in
                queueRow(item)
                    #if !SKIP
                    .listRowInsets(EdgeInsets(top: 2.0, leading: 8.0, bottom: 2.0, trailing: 8.0))
                    .listRowSeparator(.hidden)
                    #endif
                    .listRowBackground(Color.clear)
                    .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                        if item.status != .running {
                            Button(role: .destructive) {
                                model.removeQueueItem(id: item.id)
                            } label: {
                                Text("Remove")
                            }
                        }
                    }
            }
        }
        .listStyle(.plain)
        #if !SKIP
        .scrollContentBackground(.hidden)
        #endif
    }

    @ViewBuilder
    private func queueRow(_ item: TabletQueueItem) -> some View {
        let isSelected = model.selection == .queue(item.id)
        let isHovered = hoveredID == item.id

        let row = HStack(spacing: Theme.Spacing.md) {
            queueThumbnail(item: item, side: 48.0)

            VStack(alignment: .leading, spacing: 2.0) {
                Text(item.displayName)
                    .themeFont(.control)
                    .foregroundStyle(Theme.text)
                    .lineLimit(1)
                    .truncationMode(.middle)

                queueStateLine(item)
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            if item.status == .done {
                Image(systemName: "checkmark")
                    .font(.system(size: 13.0, weight: .semibold))
                    .foregroundStyle(Theme.text)
            } else if isHovered && item.status != .running {
                Button {
                    model.removeQueueItem(id: item.id)
                } label: {
                    Image(systemName: "xmark")
                        .font(.system(size: 12.0, weight: .semibold))
                        .foregroundStyle(Theme.tertiary)
                        .frame(width: 28.0, height: 28.0)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Remove \(item.displayName)")
            }
        }
        .padding(.horizontal, Theme.Spacing.sm)
        .frame(height: Theme.Spacing.listRowHeight)
        .background(
            RoundedRectangle(cornerRadius: Theme.Radius.control, style: .continuous)
                .fill(isSelected ? Theme.selectFill : (isHovered ? Theme.hover : Color.clear))
        )
        .overlay(
            RoundedRectangle(cornerRadius: Theme.Radius.control, style: .continuous)
                .stroke(isSelected ? Theme.selectLine : Color.clear, lineWidth: 1.0)
        )
        #if !SKIP
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
        #endif
        .tabletHover { hovering in
            hoveredID = hovering ? item.id : (hoveredID == item.id ? nil : hoveredID)
        }
        .onTapGesture {
            model.selection = .queue(item.id)
        }
        .accessibilityAddTraits(isSelected ? [.isButton, .isSelected] : [.isButton])
        .accessibilityIdentifier("tabletQueueRow_\(item.id)")

        #if SKIP
        row.tabletLongPressAction {
            sheetQueueItem = item
        }
        #else
        row.contextMenu {
            Button {
                onStartSingle(item.id)
            } label: {
                Label("Upscale Only This Photo", systemImage: "play")
            }
            .disabled(model.isBatchRunning)

            Button(role: .destructive) {
                model.removeQueueItem(id: item.id)
            } label: {
                Label("Remove from Queue", systemImage: "trash")
            }
            .disabled(item.status == .running)
        }
        #endif
    }

    // MARK: - Queue Grid (width >= 600)

    private var queueGridView: some View {
        ScrollView {
            LazyVGrid(columns: gridColumns, spacing: Theme.Spacing.sm) {
                ForEach(model.queueItems) { item in
                    queueGridCell(item)
                }
            }
            .padding(Theme.Spacing.panePadding)
        }
    }

    @ViewBuilder
    private func queueGridCell(_ item: TabletQueueItem) -> some View {
        let isSelected = model.selection == .queue(item.id)
        let isHovered = hoveredID == item.id

        let cell = VStack(alignment: .leading, spacing: 6.0) {
            GeometryReader { geo in
                let side = geo.size.width
                queueThumbnail(item: item, side: side)
                    .overlay(alignment: .topTrailing) {
                        if item.status == .done {
                            Image(systemName: "checkmark")
                                .font(.system(size: 11.0, weight: .bold))
                                .foregroundStyle(Color.white)
                                .padding(5.0)
                                .background(Color.black.opacity(0.6))
                                .clipShape(RoundedRectangle(cornerRadius: Theme.Radius.control, style: .continuous))
                                .padding(6.0)
                        }
                    }
            }
            .aspectRatio(1.0, contentMode: .fit)

            Text(item.displayName)
                .themeFont(.control)
                .foregroundStyle(Theme.text)
                .lineLimit(1)
                .truncationMode(.middle)

            queueStateLine(item)
        }
        .padding(6.0)
        .background(
            RoundedRectangle(cornerRadius: Theme.Radius.card, style: .continuous)
                .fill(isSelected ? Theme.selectFill : (isHovered ? Theme.hover : Color.clear))
        )
        .overlay(
            RoundedRectangle(cornerRadius: Theme.Radius.card, style: .continuous)
                .stroke(isSelected ? Theme.selectLine : Color.clear, lineWidth: 1.0)
        )
        #if !SKIP
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
        #endif
        .tabletHover { hovering in
            hoveredID = hovering ? item.id : (hoveredID == item.id ? nil : hoveredID)
        }
        .onTapGesture {
            model.selection = .queue(item.id)
        }
        .accessibilityAddTraits(isSelected ? [.isButton, .isSelected] : [.isButton])
        .accessibilityIdentifier("tabletQueueCell_\(item.id)")

        #if SKIP
        cell.tabletLongPressAction {
            sheetQueueItem = item
        }
        #else
        cell.contextMenu {
            Button {
                onStartSingle(item.id)
            } label: {
                Label("Upscale Only This Photo", systemImage: "play")
            }
            .disabled(model.isBatchRunning)

            Button(role: .destructive) {
                model.removeQueueItem(id: item.id)
            } label: {
                Label("Remove from Queue", systemImage: "trash")
            }
            .disabled(item.status == .running)
        }
        #endif
    }

    // MARK: - Results List & Grid

    private var resultsListView: some View {
        List {
            ForEach(model.results) { result in
                resultRow(result)
                    #if !SKIP
                    .listRowInsets(EdgeInsets(top: 2.0, leading: 8.0, bottom: 2.0, trailing: 8.0))
                    .listRowSeparator(.hidden)
                    #endif
                    .listRowBackground(Color.clear)
                    .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                        Button(role: .destructive) {
                            onConfirmDeleteResult(result)
                        } label: {
                            Text("Delete")
                        }
                    }
            }
        }
        .listStyle(.plain)
        #if !SKIP
        .scrollContentBackground(.hidden)
        #endif
    }

    @ViewBuilder
    private func resultRow(_ result: TabletResultItem) -> some View {
        let isSelected = model.selection == .result(result.id)
        let isHovered = hoveredID == result.id
        let isSaved = model.isSaved(result.id)

        let row = HStack(spacing: Theme.Spacing.md) {
            resultThumbnail(result: result, side: 48.0)

            VStack(alignment: .leading, spacing: 2.0) {
                Text(result.displayName)
                    .themeFont(.control)
                    .foregroundStyle(Theme.text)
                    .lineLimit(1)
                    .truncationMode(.middle)

                Text(UIFormatting.resultDimensionsLine(width: result.width, height: result.height))
                    .themeFont(.meta)
                    .foregroundStyle(Theme.secondary)
                    .lineLimit(1)
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            if isSaved {
                Image(systemName: "checkmark")
                    .font(.system(size: 13.0, weight: .semibold))
                    .foregroundStyle(Theme.ok)
            }
        }
        .padding(.horizontal, Theme.Spacing.sm)
        .frame(height: Theme.Spacing.listRowHeight)
        .background(
            RoundedRectangle(cornerRadius: Theme.Radius.control, style: .continuous)
                .fill(isSelected ? Theme.selectFill : (isHovered ? Theme.hover : Color.clear))
        )
        .overlay(
            RoundedRectangle(cornerRadius: Theme.Radius.control, style: .continuous)
                .stroke(isSelected ? Theme.selectLine : Color.clear, lineWidth: 1.0)
        )
        #if !SKIP
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
        #endif
        .tabletHover { hovering in
            hoveredID = hovering ? result.id : (hoveredID == result.id ? nil : hoveredID)
        }
        .onTapGesture {
            model.selection = .result(result.id)
        }
        .tabletDraggableFile(result.outputURL)
        .accessibilityAddTraits(isSelected ? [.isButton, .isSelected] : [.isButton])
        .accessibilityIdentifier("tabletResultRow_\(result.id)")

        resultContextMenu(for: row, result: result, isSaved: isSaved)
    }

    private var resultsGridView: some View {
        ScrollView {
            LazyVGrid(columns: gridColumns, spacing: Theme.Spacing.sm) {
                ForEach(model.results) { result in
                    resultGridCell(result)
                }
            }
            .padding(Theme.Spacing.panePadding)
        }
    }

    @ViewBuilder
    private func resultGridCell(_ result: TabletResultItem) -> some View {
        let isSelected = model.selection == .result(result.id)
        let isHovered = hoveredID == result.id
        let isSaved = model.isSaved(result.id)

        let cell = VStack(alignment: .leading, spacing: 6.0) {
            GeometryReader { geo in
                let side = geo.size.width
                resultThumbnail(result: result, side: side)
                    .overlay(alignment: .bottomTrailing) {
                        if isSaved {
                            Image(systemName: "checkmark")
                                .font(.system(size: 11.0, weight: .bold))
                                .foregroundStyle(Color.white)
                                .padding(5.0)
                                .background(Color.black.opacity(0.6))
                                .clipShape(RoundedRectangle(cornerRadius: Theme.Radius.control, style: .continuous))
                                .padding(6.0)
                        }
                    }
            }
            .aspectRatio(1.0, contentMode: .fit)

            Text(result.displayName)
                .themeFont(.control)
                .foregroundStyle(Theme.text)
                .lineLimit(1)
                .truncationMode(.middle)

            Text(UIFormatting.resultDimensionsLine(width: result.width, height: result.height))
                .themeFont(.meta)
                .foregroundStyle(Theme.secondary)
                .lineLimit(1)
        }
        .padding(6.0)
        .background(
            RoundedRectangle(cornerRadius: Theme.Radius.card, style: .continuous)
                .fill(isSelected ? Theme.selectFill : (isHovered ? Theme.hover : Color.clear))
        )
        .overlay(
            RoundedRectangle(cornerRadius: Theme.Radius.card, style: .continuous)
                .stroke(isSelected ? Theme.selectLine : Color.clear, lineWidth: 1.0)
        )
        #if !SKIP
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
        #endif
        .tabletHover { hovering in
            hoveredID = hovering ? result.id : (hoveredID == result.id ? nil : hoveredID)
        }
        .onTapGesture {
            model.selection = .result(result.id)
        }
        .tabletDraggableFile(result.outputURL)
        .accessibilityAddTraits(isSelected ? [.isButton, .isSelected] : [.isButton])
        .accessibilityIdentifier("tabletResultCell_\(result.id)")

        resultContextMenu(for: cell, result: result, isSaved: isSaved)
    }

    @ViewBuilder
    private func resultContextMenu<Content: View>(for view: Content, result: TabletResultItem, isSaved: Bool) -> some View {
        #if SKIP
        view.tabletLongPressAction {
            sheetResultItem = result
        }
        #else
        view.contextMenu {
            Button {
                Task { await model.saveResult(result) }
            } label: {
                Label("Save to \(TabletPlatform.libraryName)", systemImage: "square.and.arrow.down")
            }
            .disabled(isSaved || model.isSaving || model.isSavingAll)

            ShareLink(item: result.outputURL) {
                Label("Share\u{2026}", systemImage: "square.and.arrow.up")
            }

            Button(role: .destructive) {
                onConfirmDeleteResult(result)
            } label: {
                Label("Delete\u{2026}", systemImage: "trash")
            }
        }
        #endif
    }

    #if SKIP
    private func androidQueueActionSheet(_ item: TabletQueueItem) -> some View {
        VStack(alignment: .leading, spacing: Theme.Spacing.sm) {
            Text(item.displayName)
                .themeFont(.paneTitle)
                .foregroundStyle(Theme.text)
                .lineLimit(1)
                .truncationMode(.middle)
                .padding(.bottom, Theme.Spacing.xs)

            androidSheetActionButton(title: model.setup.task == .restore ? "Restore Only This Photo" : "Upscale Only This Photo", systemImage: "play", destructive: false) {
                sheetQueueItem = nil
                onStartSingle(item.id)
            }
            .disabled(model.isBatchRunning)

            androidSheetActionButton(title: "Remove from Queue", systemImage: "trash", destructive: true) {
                sheetQueueItem = nil
                model.removeQueueItem(id: item.id)
            }
            .disabled(item.status == .running)
        }
        .padding(Theme.Spacing.lg)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.panel)
        .presentationDetents([.height(210.0)])
    }

    private func androidResultActionSheet(_ result: TabletResultItem) -> some View {
        let isSaved = model.isSaved(result.id)
        return VStack(alignment: .leading, spacing: Theme.Spacing.sm) {
            Text(result.displayName)
                .themeFont(.paneTitle)
                .foregroundStyle(Theme.text)
                .lineLimit(1)
                .truncationMode(.middle)
                .padding(.bottom, Theme.Spacing.xs)

            androidSheetActionButton(title: isSaved ? "Saved" : "Save to \(TabletPlatform.libraryName)", systemImage: isSaved ? "checkmark" : "square.and.arrow.down", destructive: false) {
                sheetResultItem = nil
                Task { await model.saveResult(result) }
            }
            .disabled(isSaved || model.isSaving || model.isSavingAll)

            androidSheetActionButton(title: "Share\u{2026}", systemImage: "square.and.arrow.up", destructive: false) {
                sheetResultItem = nil
                AndroidUISupport.shareImageFile(ProcessInfo.processInfo.androidContext, result.outputURL.path)
            }

            androidSheetActionButton(title: "Delete\u{2026}", systemImage: "trash", destructive: true) {
                sheetResultItem = nil
                onConfirmDeleteResult(result)
            }
        }
        .padding(Theme.Spacing.lg)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.panel)
        .presentationDetents([.height(260.0)])
    }

    private func androidSheetActionButton(title: String, systemImage: String, destructive: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: Theme.Spacing.md) {
                Image(systemName: systemImage)
                    .font(.system(size: 16.0, weight: .medium))
                    .frame(width: 24.0)
                Text(title)
                    .themeFont(.control, weight: .medium)
                Spacer()
            }
            .foregroundStyle(destructive ? Theme.danger : Theme.text)
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
    }
    #endif

    // MARK: - Thumbnail & State Copy Helpers

    @ViewBuilder
    private func queueThumbnail(item: TabletQueueItem, side: CGFloat) -> some View {
        let clampedProgress = min(max(0.0, item.progress), 1.0)
        let lineX = clampedProgress * side

        ZStack(alignment: .topLeading) {
            Theme.raised
                .frame(width: side, height: side)

            if !item.isPreparingPreview {
                DecodedImageView(url: item.inputURL, maxPixelSize: side > 80 ? 360 : 160, contentMode: .fill)
                    .frame(width: side, height: side)
            }

            if item.status == .running {
                Color.black.opacity(0.35)
                    .frame(width: max(0.0, side - lineX), height: side)
                    .offset(x: lineX)

                SweepLine(
                    lineX: lineX,
                    squareX: lineX,
                    height: side,
                    squareSide: 6.0,
                    showHandle: false,
                    lineWidth: 1.5,
                    squareColor: Theme.accent
                )
                .frame(width: side, height: side)
                .animation(reduceMotion ? nil : .linear(duration: 0.25), value: item.progress)
            }
        }
        .frame(width: side, height: side)
        .clipped()
        .clipShape(RoundedRectangle(cornerRadius: Theme.Radius.thumbnail, style: .continuous))
    }

    @ViewBuilder
    private func resultThumbnail(result: TabletResultItem, side: CGFloat) -> some View {
        Theme.raised
            .frame(width: side, height: side)
            .overlay {
                DecodedImageView(url: result.previewURL, maxPixelSize: side > 80 ? 360 : 160, contentMode: .fill)
                    .frame(width: side, height: side)
            }
            .clipped()
            .clipShape(RoundedRectangle(cornerRadius: Theme.Radius.thumbnail, style: .continuous))
    }

    @ViewBuilder
    private func queueStateLine(_ item: TabletQueueItem) -> some View {
        if item.isPreparingPreview {
            Text("Preparing preview\u{2026}")
                .themeFont(.meta)
                .foregroundStyle(Theme.secondary)
                .lineLimit(1)
        } else {
            switch item.status {
            case .waiting:
                let text = (model.isBatchRunning && model.batchTargetIDs.contains(item.id))
                    ? "Waiting"
                    : (item.dimensionsText ?? "Waiting")
                Text(text)
                    .themeFont(.meta)
                    .foregroundStyle(Theme.secondary)
                    .lineLimit(1)

            case .running:
                let stage = item.stageName ?? "Upscaling"
                let chip = item.chipName ?? ComputeUnit.npu.platformDisplayName
                Text("\(stage) \u{00B7} \(chip)")
                    .themeFont(.meta)
                    .foregroundStyle(Theme.secondary)
                    .lineLimit(1)

            case .done:
                let mp = UIFormatting.formatMegapixels(width: item.outputWidth ?? 0, height: item.outputHeight ?? 0)
                Text("Done \u{00B7} \(mp)")
                    .themeFont(.meta)
                    .foregroundStyle(Theme.secondary)
                    .lineLimit(1)

            case .failed:
                HStack(alignment: .top, spacing: 4.0) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .font(.system(size: 11.0))
                        .foregroundStyle(Theme.text)
                        .padding(.top, 2.0)
                    Text("Couldn't upscale: \(item.errorMessage ?? "Unknown error")")
                        .themeFont(.meta)
                        .foregroundStyle(Theme.secondary)
                        .lineLimit(2)
                }
            }
        }
    }
}
