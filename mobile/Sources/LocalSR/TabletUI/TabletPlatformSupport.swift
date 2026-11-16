import Foundation
import SwiftUI
import LocalSRCore

#if !SKIP
import UniformTypeIdentifiers
import ImageIO
#if canImport(PhotosUI)
import PhotosUI
#endif
#if canImport(UIKit)
import UIKit
#endif
#endif

public struct TabletImportBatch: Sendable {
    public let inputs: [PickedPhotoInput]
    public let hasVideo: Bool
    public let hasUnreadable: Bool
}

enum TabletPlatform {
    @MainActor
    static var isTablet: Bool {
        #if SKIP
        return AndroidUISupport.isTablet(ProcessInfo.processInfo.androidContext)
        #elseif os(iOS)
        return UIDevice.current.userInterfaceIdiom == .pad
        #else
        return false
        #endif
    }

    static var isAndroid: Bool {
        #if SKIP
        return true
        #else
        return false
        #endif
    }

    static var deviceNoun: String {
        isAndroid ? "this tablet" : "this iPad"
    }

    static var libraryName: String {
        isAndroid ? "Gallery" : "Photos"
    }

    static var npuName: String {
        isAndroid ? "NPU" : "Neural Engine"
    }

    static var hasNeuralEngine: Bool {
        #if SKIP
        return false
        #else
        return AppleDeviceCheck.hasNeuralEngine
        #endif
    }

    static let minimumRequiredStorageBytes: Int64 = 120_000_000

    static func availableStorageBytes() -> Int64? {
        #if SKIP
        let bytes = AndroidUISupport.availableStorageBytes(ProcessInfo.processInfo.androidContext)
        return bytes == Int64.max ? nil : bytes
        #else
        let caches = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
        if let values = try? caches.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey, .volumeAvailableCapacityKey]) {
            if let important = values.volumeAvailableCapacityForImportantUsage, important > 0 {
                return important
            }
            if let regular = values.volumeAvailableCapacity {
                return Int64(regular)
            }
        }
        return nil
        #endif
    }

    static func isOutOfSpaceError(_ error: Error) -> Bool {
        #if SKIP
        let msg = String(describing: error).lowercased()
        return msg.contains("enospc") || msg.contains("no space left")
        #else
        let ns = error as NSError
        if ns.domain == NSCocoaErrorDomain && ns.code == NSFileWriteOutOfSpaceError {
            return true
        }
        if ns.domain == NSPOSIXErrorDomain && ns.code == Int(ENOSPC) {
            return true
        }
        let msg = ns.localizedDescription.lowercased()
        return msg.contains("no space left") || msg.contains("not enough space")
        #endif
    }

    @MainActor
    static func configureMinimumWindowSize() {
        #if !SKIP && os(iOS)
        guard UIDevice.current.userInterfaceIdiom == .pad else { return }
        for scene in UIApplication.shared.connectedScenes {
            if let windowScene = scene as? UIWindowScene {
                windowScene.sizeRestrictions?.minimumSize = CGSize(width: 375.0, height: 560.0)
            }
        }
        #endif
    }

    static func saveOutputToLibrary(outputURL: URL, originalFilename: String) async throws {
        #if SKIP
        let upscaler = AndroidUpscaler(ProcessInfo.processInfo.androidContext)
        _ = try await upscaler.saveToLibrary(outputURL.path)
        #elseif os(iOS)
        try await PhotoLibrarySaver.save(fileURL: outputURL, originalFilename: originalFilename)
        #endif
    }

    static func tabletInputsDirectory() -> URL {
        #if SKIP
        let caches = URL(fileURLWithPath: ProcessInfo.processInfo.androidContext.cacheDir.absolutePath, isDirectory: true)
        #else
        let caches = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
        #endif
        let dir = caches.appendingPathComponent("LocalSRTabletInputs", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    static func copyFileToInputs(_ sourceURL: URL, preferredName: String? = nil) -> PickedPhotoInput? {
        let dir = tabletInputsDirectory()
        let ext = sourceURL.pathExtension.isEmpty ? "jpg" : sourceURL.pathExtension
        let dest = dir.appendingPathComponent("\(UUID().uuidString).\(ext)")
        do {
            try FileManager.default.copyItem(at: sourceURL, to: dest)
            let name = (preferredName?.isEmpty == false) ? preferredName! : sourceURL.lastPathComponent
            return PickedPhotoInput(url: dest, displayName: name)
        } catch {
            return nil
        }
    }

    #if !SKIP
    static func isReadableImage(url: URL) -> Bool {
        let options: [CFString: Any] = [kCGImageSourceShouldCache: false]
        guard let source = CGImageSourceCreateWithURL(url as CFURL, options as CFDictionary),
              CGImageSourceGetCount(source) > 0,
              let props = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
              let w = (props[kCGImagePropertyPixelWidth] as? NSNumber)?.intValue,
              let h = (props[kCGImagePropertyPixelHeight] as? NSNumber)?.intValue else {
            return false
        }
        return w > 0 && h > 0
    }

    static func isVideoURL(_ url: URL) -> Bool {
        guard let type = UTType(filenameExtension: url.pathExtension) else { return false }
        return type.conforms(to: .movie) || type.conforms(to: .video)
    }

    static func collectInputs(from urls: [URL]) -> TabletImportBatch {
        var inputs: [PickedPhotoInput] = []
        var hasVideo = false
        var hasUnreadable = false
        let fm = FileManager.default

        for url in urls {
            let scoped = url.startAccessingSecurityScopedResource()
            defer {
                if scoped { url.stopAccessingSecurityScopedResource() }
            }

            var isDir: ObjCBool = false
            if fm.fileExists(atPath: url.path, isDirectory: &isDir), isDir.boolValue {
                if let enumerator = fm.enumerator(
                    at: url,
                    includingPropertiesForKeys: [.isRegularFileKey, .isHiddenKey],
                    options: [.skipsHiddenFiles]
                ) {
                    var fileURLs: [URL] = []
                    for case let child as URL in enumerator {
                        if (try? child.resourceValues(forKeys: [.isRegularFileKey]).isRegularFile) == true {
                            fileURLs.append(child)
                        }
                    }
                    fileURLs.sort { $0.lastPathComponent.localizedStandardCompare($1.lastPathComponent) == .orderedAscending }
                    for child in fileURLs {
                        if isVideoURL(child) {
                            hasVideo = true
                        } else if isReadableImage(url: child), let copied = copyFileToInputs(child, preferredName: child.lastPathComponent) {
                            inputs.append(copied)
                        } else {
                            hasUnreadable = true
                        }
                    }
                }
            } else if isVideoURL(url) {
                hasVideo = true
            } else if isReadableImage(url: url), let copied = copyFileToInputs(url, preferredName: url.lastPathComponent) {
                inputs.append(copied)
            } else {
                hasUnreadable = true
            }
        }
        return TabletImportBatch(inputs: inputs, hasVideo: hasVideo, hasUnreadable: hasUnreadable)
    }
    #endif

    @MainActor
    static func pasteImagesFromClipboard() -> TabletImportBatch {
        #if SKIP
        let context = ProcessInfo.processInfo.androidContext
        let uris = AndroidUISupport.pasteImageUriStrings(context)
        var inputs: [PickedPhotoInput] = []
        for i in 0..<uris.size {
            let uriStr = uris[i]
            if let url = URL(string: uriStr) {
                let name = AndroidUISupport.displayNameForUri(context, uriStr, "Pasted photo \(i + 1)")
                inputs.append(PickedPhotoInput(url: url, displayName: name))
            }
        }
        return TabletImportBatch(inputs: inputs, hasVideo: false, hasUnreadable: inputs.isEmpty)
        #elseif os(iOS)
        let pb = UIPasteboard.general
        var inputs: [PickedPhotoInput] = []
        let dir = tabletInputsDirectory()

        if let images = pb.images, !images.isEmpty {
            for (index, img) in images.enumerated() {
                if let data = img.pngData() ?? img.jpegData(compressionQuality: 0.95) {
                    let ext = img.pngData() != nil ? "png" : "jpg"
                    let dest = dir.appendingPathComponent("\(UUID().uuidString).\(ext)")
                    if (try? data.write(to: dest, options: .atomic)) != nil {
                        inputs.append(PickedPhotoInput(url: dest, displayName: "Pasted \(index + 1).\(ext)"))
                    }
                }
            }
            if !inputs.isEmpty {
                return TabletImportBatch(inputs: inputs, hasVideo: false, hasUnreadable: false)
            }
        }

        if let urls = pb.urls, !urls.isEmpty {
            let fileURLs = urls.filter { $0.isFileURL }
            if !fileURLs.isEmpty {
                return collectInputs(from: fileURLs)
            }
        }
        return TabletImportBatch(inputs: [], hasVideo: false, hasUnreadable: false)
        #else
        return TabletImportBatch(inputs: [], hasVideo: false, hasUnreadable: false)
        #endif
    }

    @MainActor
    static var canPasteImages: Bool {
        #if SKIP
        return !AndroidUISupport.pasteImageUriStrings(ProcessInfo.processInfo.androidContext).isEmpty()
        #elseif os(iOS)
        return UIPasteboard.general.hasImages || (UIPasteboard.general.urls?.contains(where: { $0.isFileURL }) == true)
        #else
        return false
        #endif
    }
}

// MARK: - Pickers

#if SKIP
private final class TabletPickerLaunchBox {
    var launch: (() -> Void)? = nil
}
#endif

struct TabletPhotoPickerHost: View {
    @Binding var isPresented: Bool
    let onLoadingChanged: (Bool) -> Void
    let onImported: (TabletImportBatch) -> Void

    #if SKIP
    @State private var pickerBox = TabletPickerLaunchBox()
    #elseif canImport(PhotosUI)
    @State private var selectedItems: [PhotosPickerItem] = []
    #endif

    var body: some View {
        #if SKIP
        ComposeView { ctx in
            AndroidPhotoPickerHost(
                onReady: { action in
                    pickerBox.launch = action
                },
                onPicked: { uris in
                    let androidContext = ProcessInfo.processInfo.androidContext
                    var inputs: [PickedPhotoInput] = []
                    for i in 0..<uris.size {
                        let uriStr = uris[i]
                        if let url = URL(string: uriStr) {
                            let name = AndroidUISupport.displayNameForUri(androidContext, uriStr, "Photo \(i + 1)")
                            inputs.append(PickedPhotoInput(url: url, displayName: name))
                        }
                    }
                    if !inputs.isEmpty {
                        onImported(TabletImportBatch(inputs: inputs, hasVideo: false, hasUnreadable: false))
                    }
                },
                modifier: ctx.modifier
            )
        }
        .frame(width: 0.0, height: 0.0)
        .onChange(of: isPresented) { _, presented in
            if presented {
                isPresented = false
                pickerBox.launch?()
            }
        }
        #elseif canImport(PhotosUI)
        Color.clear
            .frame(width: 0.0, height: 0.0)
            .photosPicker(isPresented: $isPresented, selection: $selectedItems, maxSelectionCount: 50, matching: .images)
            .onChange(of: selectedItems) { _, newItems in
                guard !newItems.isEmpty else { return }
                let items = newItems
                selectedItems = []
                onLoadingChanged(true)
                Task {
                    let batch = await Self.materializePickerItems(items)
                    await MainActor.run {
                        onLoadingChanged(false)
                        onImported(batch)
                    }
                }
            }
        #else
        Color.clear.frame(width: 0.0, height: 0.0)
        #endif
    }

    #if !SKIP && canImport(PhotosUI)
    private static func materializePickerItems(_ items: [PhotosPickerItem]) async -> TabletImportBatch {
        let inputDir = TabletPlatform.tabletInputsDirectory()
        var out: [PickedPhotoInput] = []
        var hasUnreadable = false

        for (index, item) in items.enumerated() {
            if let file = try? await item.loadTransferable(type: TabletPickedImageFile.self) {
                if TabletPlatform.isReadableImage(url: file.url) {
                    out.append(PickedPhotoInput(url: file.url, displayName: file.suggestedName))
                    continue
                }
            }
            if let data = try? await item.loadTransferable(type: Data.self), !data.isEmpty {
                let ext = item.supportedContentTypes.first?.preferredFilenameExtension ?? "jpg"
                let fileURL = inputDir.appendingPathComponent("\(UUID().uuidString).\(ext)")
                do {
                    try data.write(to: fileURL, options: .atomic)
                    if TabletPlatform.isReadableImage(url: fileURL) {
                        out.append(PickedPhotoInput(url: fileURL, displayName: "Photo \(index + 1).\(ext)"))
                        continue
                    } else {
                        try? FileManager.default.removeItem(at: fileURL)
                    }
                } catch {}
            }
            hasUnreadable = true
        }
        return TabletImportBatch(inputs: out, hasVideo: false, hasUnreadable: hasUnreadable)
    }
    #endif
}

#if !SKIP
private struct TabletPickedImageFile: Transferable {
    let url: URL
    let suggestedName: String?

    static var transferRepresentation: some TransferRepresentation {
        FileRepresentation(importedContentType: .image) { received in
            let originalName = received.file.lastPathComponent
            let ext = received.file.pathExtension.isEmpty ? "jpg" : received.file.pathExtension
            let dest = TabletPlatform.tabletInputsDirectory().appendingPathComponent("\(UUID().uuidString).\(ext)")
            try FileManager.default.copyItem(at: received.file, to: dest)
            let display = originalName.isEmpty ? nil : originalName
            return Self(url: dest, suggestedName: display)
        }
    }
}
#endif

struct TabletFileImporterHost: View {
    @Binding var isFilesPresented: Bool
    @Binding var isFolderPresented: Bool
    let onLoadingChanged: (Bool) -> Void
    let onImported: (TabletImportBatch) -> Void

    #if SKIP
    @State private var filesBox = TabletPickerLaunchBox()
    @State private var folderBox = TabletPickerLaunchBox()
    #endif

    var body: some View {
        #if SKIP
        HStack(spacing: 0.0) {
            ComposeView { ctx in
                AndroidFilePickerHost(
                    onReady: { action in filesBox.launch = action },
                    onPicked: { uris in
                        let context = ProcessInfo.processInfo.androidContext
                        var inputs: [PickedPhotoInput] = []
                        for i in 0..<uris.size {
                            let uriStr = uris[i]
                            if let url = URL(string: uriStr) {
                                let name = AndroidUISupport.displayNameForUri(context, uriStr, "File \(i + 1)")
                                inputs.append(PickedPhotoInput(url: url, displayName: name))
                            }
                        }
                        if !inputs.isEmpty {
                            onImported(TabletImportBatch(inputs: inputs, hasVideo: false, hasUnreadable: false))
                        }
                    },
                    modifier: ctx.modifier
                )
            }
            .frame(width: 0.0, height: 0.0)

            ComposeView { ctx in
                AndroidFolderPickerHost(
                    onReady: { action in folderBox.launch = action },
                    onPickedTree: { treeUri in
                        let context = ProcessInfo.processInfo.androidContext
                        let uris = AndroidUISupport.collectImagesInTree(context, treeUri)
                        var inputs: [PickedPhotoInput] = []
                        for i in 0..<uris.size {
                            let uriStr = uris[i]
                            if let url = URL(string: uriStr) {
                                let name = AndroidUISupport.displayNameForUri(context, uriStr, "File \(i + 1)")
                                inputs.append(PickedPhotoInput(url: url, displayName: name))
                            }
                        }
                        onImported(TabletImportBatch(inputs: inputs, hasVideo: false, hasUnreadable: inputs.isEmpty))
                    },
                    modifier: ctx.modifier
                )
            }
            .frame(width: 0.0, height: 0.0)
        }
        .onChange(of: isFilesPresented) { _, presented in
            if presented {
                isFilesPresented = false
                filesBox.launch?()
            }
        }
        .onChange(of: isFolderPresented) { _, presented in
            if presented {
                isFolderPresented = false
                folderBox.launch?()
            }
        }
        #else
        HStack(spacing: 0.0) {
            Color.clear
                .frame(width: 0.0, height: 0.0)
                .fileImporter(
                    isPresented: $isFilesPresented,
                    allowedContentTypes: [.image],
                    allowsMultipleSelection: true
                ) { result in
                    guard case .success(let urls) = result, !urls.isEmpty else { return }
                    onLoadingChanged(true)
                    Task.detached(priority: .userInitiated) {
                        let batch = TabletPlatform.collectInputs(from: urls)
                        await MainActor.run {
                            onLoadingChanged(false)
                            onImported(batch)
                        }
                    }
                }

            Color.clear
                .frame(width: 0.0, height: 0.0)
                .fileImporter(
                    isPresented: $isFolderPresented,
                    allowedContentTypes: [.folder],
                    allowsMultipleSelection: false
                ) { result in
                    guard case .success(let urls) = result, !urls.isEmpty else { return }
                    onLoadingChanged(true)
                    Task.detached(priority: .userInitiated) {
                        let batch = TabletPlatform.collectInputs(from: urls)
                        await MainActor.run {
                            onLoadingChanged(false)
                            onImported(batch)
                        }
                    }
                }
        }
        #endif
    }
}

// MARK: - Drag & Drop

#if !SKIP
private enum DroppedMediaPayload: Transferable {
    case folder(URL)
    case video
    case image(PickedPhotoInput)
    case unreadable

    static var transferRepresentation: some TransferRepresentation {
        FileRepresentation(importedContentType: .folder) { received in
            let dest = TabletPlatform.tabletInputsDirectory().appendingPathComponent("folder-\(UUID().uuidString)", isDirectory: true)
            try FileManager.default.copyItem(at: received.file, to: dest)
            return .folder(dest)
        }
        FileRepresentation(importedContentType: .movie) { _ in
            return .video
        }
        FileRepresentation(importedContentType: .image) { received in
            let originalName = received.file.lastPathComponent
            let ext = received.file.pathExtension.isEmpty ? "jpg" : received.file.pathExtension
            let dest = TabletPlatform.tabletInputsDirectory().appendingPathComponent("\(UUID().uuidString).\(ext)")
            try FileManager.default.copyItem(at: received.file, to: dest)
            if TabletPlatform.isReadableImage(url: dest) {
                return .image(PickedPhotoInput(url: dest, displayName: originalName.isEmpty ? nil : originalName))
            } else {
                try? FileManager.default.removeItem(at: dest)
                return .unreadable
            }
        }
        FileRepresentation(importedContentType: .item) { received in
            if TabletPlatform.isVideoURL(received.file) {
                return .video
            }
            if let copied = TabletPlatform.copyFileToInputs(received.file, preferredName: received.file.lastPathComponent),
               TabletPlatform.isReadableImage(url: copied.url) {
                return .image(copied)
            }
            return .unreadable
        }
    }
}
#endif

extension View {
    @ViewBuilder
    func tabletWindowDropDestination(
        isTargeted: Binding<Bool>,
        onDropped: @escaping (TabletImportBatch) -> Void
    ) -> some View {
        #if SKIP
        self.composeModifier { modifier in
            modifier.androidWindowDropTarget(
                context: ProcessInfo.processInfo.androidContext,
                onTargeted: { targeted in
                    Task { @MainActor in
                        isTargeted.wrappedValue = targeted
                    }
                },
                onDropped: { paths, names, hasVideo, hasUnreadable in
                    var inputs: [PickedPhotoInput] = []
                    for i in 0..<paths.size {
                        let p = paths[i]
                        let name = i < names.size ? names[i] : nil
                        inputs.append(PickedPhotoInput(url: URL(fileURLWithPath: p), displayName: name))
                    }
                    let batch = TabletImportBatch(inputs: inputs, hasVideo: hasVideo, hasUnreadable: hasUnreadable)
                    Task { @MainActor in
                        onDropped(batch)
                    }
                }
            )
        }
        #else
        self.dropDestination(for: DroppedMediaPayload.self) { items, _ in
            guard !items.isEmpty else { return false }
            Task { @MainActor in
                let batch = await Task.detached(priority: .userInitiated) {
                    var inputs: [PickedPhotoInput] = []
                    var hasVideo = false
                    var hasUnreadable = false
                    for item in items {
                        switch item {
                        case .folder(let folderURL):
                            let folderBatch = TabletPlatform.collectInputs(from: [folderURL])
                            inputs.append(contentsOf: folderBatch.inputs)
                            hasVideo = hasVideo || folderBatch.hasVideo
                            hasUnreadable = hasUnreadable || folderBatch.hasUnreadable
                            try? FileManager.default.removeItem(at: folderURL)
                        case .video:
                            hasVideo = true
                        case .image(let photo):
                            inputs.append(photo)
                        case .unreadable:
                            hasUnreadable = true
                        }
                    }
                    return TabletImportBatch(inputs: inputs, hasVideo: hasVideo, hasUnreadable: hasUnreadable)
                }.value
                onDropped(batch)
            }
            return true
        } isTargeted: { targeted in
            isTargeted.wrappedValue = targeted
        }
        #endif
    }

    @ViewBuilder
    func tabletDraggableFile(_ url: URL?) -> some View {
        #if !SKIP
        if let url {
            self.draggable(url)
        } else {
            self
        }
        #else
        if let url {
            self.composeModifier { modifier in
                modifier.androidDragOutFile(context: ProcessInfo.processInfo.androidContext, path: url.path)
            }
        } else {
            self
        }
        #endif
    }

    @ViewBuilder
    func tabletLongPressAction(enabled: Bool = true, action: @escaping () -> Void) -> some View {
        #if SKIP
        self.composeModifier { modifier in
            androidLongPressModifier(modifier: modifier, enabled: enabled, onLongPress: action)
        }
        #else
        self
        #endif
    }

    @ViewBuilder
    func tabletHover(_ onChanged: @escaping (Bool) -> Void) -> some View {
        #if SKIP
        self.composeModifier { modifier in
            modifier.androidHover(onHoverChanged: onChanged)
        }
        #else
        self.onHover(perform: onChanged)
        #endif
    }

    @ViewBuilder
    func tabletCornerOffsetLeading() -> some View {
        #if !SKIP && os(iOS)
        self.containerCornerOffset(.leading, sizeToFit: true)
        #else
        self
        #endif
    }
}
