import Foundation
import SwiftUI

#if !SKIP
import ImageIO
import UniformTypeIdentifiers
#if canImport(PhotosUI)
import PhotosUI
#endif
#if canImport(UIKit)
import UIKit
#elseif canImport(AppKit)
import AppKit
#endif
#endif

public struct PickedPhotoInput: Sendable {
    public let url: URL
    public let displayName: String?

    public init(url: URL, displayName: String? = nil) {
        self.url = url
        self.displayName = displayName
    }
}

public enum PlatformSupport {
    public static func loadNoticeText() -> String {
        #if SKIP
        return AndroidUISupport.readNoticeText(ProcessInfo.processInfo.androidContext)
        #else
        guard let url = try? AppleResources.noticeURL(),
              let text = try? String(contentsOf: url, encoding: .utf8) else {
            return ""
        }
        return text
        #endif
    }

    public static func appVersionString() -> String {
        #if SKIP
        return AndroidUISupport.appVersionString(ProcessInfo.processInfo.androidContext)
        #else
        let version = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "0.0.1"
        let build = Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "1"
        return "\(version) (\(build))"
        #endif
    }

    public static func requestNotificationPermissionIfNeeded() async {
        #if SKIP
        if android.os.Build.VERSION.SDK_INT >= 33 {
            _ = await UIApplication.shared.requestPermission("android.permission.POST_NOTIFICATIONS")
        }
        #endif
    }

    @MainActor
    public static func openAppSettings() {
        #if !SKIP && os(iOS)
        if let url = URL(string: UIApplication.openSettingsURLString) {
            UIApplication.shared.open(url)
        }
        #endif
    }

    public static func imageAspectRatio(url: URL) async -> CGFloat? {
        #if SKIP
        let context = ProcessInfo.processInfo.androidContext
        if let ratio = AndroidUISupport.imageAspectRatio(context, url.absoluteString), ratio > 0.0 {
            return CGFloat(ratio)
        }
        return nil
        #else
        return await Task.detached(priority: .userInitiated) {
            let sourceOptions: [CFString: Any] = [kCGImageSourceShouldCache: false]
            guard let source = CGImageSourceCreateWithURL(url as CFURL, sourceOptions as CFDictionary),
                  let props = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
                  let width = (props[kCGImagePropertyPixelWidth] as? NSNumber)?.doubleValue,
                  let height = (props[kCGImagePropertyPixelHeight] as? NSNumber)?.doubleValue,
                  width > 0, height > 0 else {
                return nil
            }
            let orientation = (props[kCGImagePropertyOrientation] as? NSNumber)?.intValue ?? 1
            if orientation >= 5 && orientation <= 8 {
                return CGFloat(height / width)
            }
            return CGFloat(width / height)
        }.value
        #endif
    }
}

public struct DecodedImageView: View {
    public let url: URL
    public let maxPixelSize: Int
    public let contentMode: ContentMode

    #if !SKIP
    #if canImport(UIKit)
    @State private var loadedImage: UIImage? = nil
    #elseif canImport(AppKit)
    @State private var loadedImage: NSImage? = nil
    #endif
    #endif

    public init(url: URL, maxPixelSize: Int, contentMode: ContentMode = .fit) {
        self.url = url
        self.maxPixelSize = maxPixelSize
        self.contentMode = contentMode
    }

    public var body: some View {
        #if SKIP
        ComposeView { ctx in
            AndroidDecodedImage(
                uriString: url.absoluteString,
                maxSide: maxPixelSize,
                crop: contentMode == .fill,
                modifier: ctx.modifier
            )
        }
        #else
        Group {
            if let loadedImage {
                #if canImport(UIKit)
                Image(uiImage: loadedImage)
                    .resizable()
                    .aspectRatio(contentMode: contentMode)
                #elseif canImport(AppKit)
                Image(nsImage: loadedImage)
                    .resizable()
                    .aspectRatio(contentMode: contentMode)
                #endif
            } else {
                Color.clear
            }
        }
        .task(id: url) {
            loadedImage = await Self.decodePlatformImage(url: url, maxPixelSize: maxPixelSize)
        }
        #endif
    }

    #if !SKIP
    #if canImport(UIKit)
    private static func decodePlatformImage(url: URL, maxPixelSize: Int) async -> UIImage? {
        await Task.detached(priority: .userInitiated) {
            guard let cgImage = decodeCGImage(url: url, maxPixelSize: maxPixelSize) else { return nil }
            return UIImage(cgImage: cgImage)
        }.value
    }
    #elseif canImport(AppKit)
    private static func decodePlatformImage(url: URL, maxPixelSize: Int) async -> NSImage? {
        await Task.detached(priority: .userInitiated) {
            guard let cgImage = decodeCGImage(url: url, maxPixelSize: maxPixelSize) else { return nil }
            return NSImage(cgImage: cgImage, size: NSSize(width: cgImage.width, height: cgImage.height))
        }.value
    }
    #endif

    nonisolated private static func decodeCGImage(url: URL, maxPixelSize: Int) -> CGImage? {
        let sourceOptions: [CFString: Any] = [kCGImageSourceShouldCache: false]
        guard let source = CGImageSourceCreateWithURL(url as CFURL, sourceOptions as CFDictionary),
              CGImageSourceGetCount(source) > 0 else {
            return nil
        }
        let thumbOptions: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceThumbnailMaxPixelSize: maxPixelSize,
            kCGImageSourceShouldCacheImmediately: true,
        ]
        return CGImageSourceCreateThumbnailAtIndex(source, 0, thumbOptions as CFDictionary)
    }
    #endif
}

public struct PlatformShareButton: View {
    public let url: URL

    public init(url: URL) {
        self.url = url
    }

    public var body: some View {
        #if SKIP
        Button {
            AndroidUISupport.shareImageFile(ProcessInfo.processInfo.androidContext, url.path)
        } label: {
            Label("Share", systemImage: "square.and.arrow.up")
        }
        #else
        ShareLink(item: url) {
            Label("Share", systemImage: "square.and.arrow.up")
        }
        #endif
    }
}

#if SKIP
private final class PickerLaunchBox {
    var launch: (() -> Void)? = nil
}
#endif

public struct PlatformPhotoPickerHost: View {
    @Binding public var isPresented: Bool
    public let onLoadingChanged: (Bool) -> Void
    public let onPicked: ([PickedPhotoInput]) -> Void

    #if SKIP
    @State private var pickerBox = PickerLaunchBox()
    #elseif canImport(PhotosUI)
    @State private var selectedItems: [PhotosPickerItem] = []
    #endif

    public init(
        isPresented: Binding<Bool>,
        onLoadingChanged: @escaping (Bool) -> Void,
        onPicked: @escaping ([PickedPhotoInput]) -> Void
    ) {
        self._isPresented = isPresented
        self.onLoadingChanged = onLoadingChanged
        self.onPicked = onPicked
    }

    public var body: some View {
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
                        onPicked(inputs)
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
            .photosPicker(isPresented: $isPresented, selection: $selectedItems, maxSelectionCount: 20, matching: .images)
            .onChange(of: selectedItems) { _, newItems in
                guard !newItems.isEmpty else { return }
                let items = newItems
                selectedItems = []
                onLoadingChanged(true)
                Task {
                    let inputs = await Self.materializePickerItems(items)
                    await MainActor.run {
                        onLoadingChanged(false)
                        if !inputs.isEmpty {
                            onPicked(inputs)
                        }
                    }
                }
            }
        #else
        Color.clear
            .frame(width: 0.0, height: 0.0)
        #endif
    }

    #if !SKIP && canImport(PhotosUI)
    private static func materializePickerItems(_ items: [PhotosPickerItem]) async -> [PickedPhotoInput] {
        let caches = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
        let inputDir = caches.appendingPathComponent("LocalSRInputs", isDirectory: true)
        try? FileManager.default.createDirectory(at: inputDir, withIntermediateDirectories: true)

        var out: [PickedPhotoInput] = []
        for item in items {
            if let data = try? await item.loadTransferable(type: Data.self), !data.isEmpty {
                let ext = item.supportedContentTypes.first?.preferredFilenameExtension ?? "jpg"
                let fileURL = inputDir.appendingPathComponent("\(UUID().uuidString).\(ext)")
                do {
                    try data.write(to: fileURL, options: .atomic)
                    out.append(PickedPhotoInput(url: fileURL, displayName: nil))
                    continue
                } catch {
                    // Fall through to file transferable if direct write fails
                }
            }
            if let file = try? await item.loadTransferable(type: PickedImageFile.self) {
                out.append(PickedPhotoInput(url: file.url, displayName: nil))
            }
        }
        return out
    }
    #endif
}

#if !SKIP
private struct PickedImageFile: Transferable {
    let url: URL
    static var transferRepresentation: some TransferRepresentation {
        FileRepresentation(importedContentType: .image) { received in
            let ext = received.file.pathExtension.isEmpty ? "jpg" : received.file.pathExtension
            let caches = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
            let inputDir = caches.appendingPathComponent("LocalSRInputs", isDirectory: true)
            try FileManager.default.createDirectory(at: inputDir, withIntermediateDirectories: true)
            let dest = inputDir.appendingPathComponent("\(UUID().uuidString).\(ext)")
            try FileManager.default.copyItem(at: received.file, to: dest)
            return Self(url: dest)
        }
    }
}
#endif

extension View {
    @ViewBuilder
    func adaptiveMonospacedDigit() -> some View {
        #if !SKIP
        self.monospacedDigit()
        #else
        self
        #endif
    }
}

