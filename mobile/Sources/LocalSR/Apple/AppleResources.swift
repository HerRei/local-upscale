#if !SKIP
import Foundation
import LocalSRCore

/// Where the iOS-only resources live (the two precompiled Core ML models and the device-check probe tile).
///
/// In the app they are copied into the main bundle by the Darwin Xcode project (folder references in the
/// "Resources" phase of the "LocalSR App" target), so they never reach the Android APK. When the code runs
/// without that bundle (macOS `swift test`), the search falls back to `LOCALSR_MODELS_DIR` and then to the
/// `mobile/Models` folder of the source tree (debug builds and the macOS host only).
public enum AppleResources {
    public enum ResourceError: Error, CustomStringConvertible {
        case missing(String)
        public var description: String {
            switch self { case .missing(let name): return "Resource not found: \(name)" }
        }
    }

    static func candidateDirectories() -> [URL] {
        var dirs: [URL] = []
        if let env = ProcessInfo.processInfo.environment["LOCALSR_MODELS_DIR"], !env.isEmpty {
            let url = URL(fileURLWithPath: env, isDirectory: true)
            dirs.append(url)
            dirs.append(url.appendingPathComponent("ios", isDirectory: true))
        }
        if let root = Bundle.main.resourceURL {
            dirs.append(root)
            dirs.append(root.appendingPathComponent("Models", isDirectory: true))
            dirs.append(root.appendingPathComponent("Models/ios", isDirectory: true))
        }
        #if DEBUG || os(macOS)
        // .../mobile/Sources/LocalSR/Apple/AppleResources.swift -> .../mobile/Models
        var url = URL(fileURLWithPath: #filePath)
        for _ in 0..<4 { url.deleteLastPathComponent() }
        dirs.append(url.appendingPathComponent("Models", isDirectory: true))
        dirs.append(url.appendingPathComponent("Models/ios", isDirectory: true))
        #endif
        return dirs
    }

    static func find(_ name: String) throws -> URL {
        for dir in candidateDirectories() {
            let url = dir.appendingPathComponent(name)
            if FileManager.default.fileExists(atPath: url.path) { return url }
        }
        throw ResourceError.missing(name)
    }

    public static func modelURL(_ model: ModelKey) throws -> URL {
        try find(model.fileName + ".mlmodelc")
    }

    public static func tabletModelURL(_ model: TabletModelID) throws -> URL {
        let name = ModelCatalog.model(model).iosFileName
        var dirs: [URL] = []
        if let env = ProcessInfo.processInfo.environment["LOCALSR_MODELS_DIR"], !env.isEmpty {
            let root = URL(fileURLWithPath: env, isDirectory: true)
            dirs.append(root.appendingPathComponent("ios-tablet", isDirectory: true))
            dirs.append(root)
        }
        if let root = Bundle.main.resourceURL {
            dirs.append(root.appendingPathComponent("Models/ios-tablet", isDirectory: true))
            dirs.append(root.appendingPathComponent("ios-tablet", isDirectory: true))
            dirs.append(root)
        }
        #if DEBUG || os(macOS)
        var root = URL(fileURLWithPath: #filePath)
        for _ in 0..<4 { root.deleteLastPathComponent() }
        dirs.append(root.appendingPathComponent("Models/ios-tablet", isDirectory: true))
        #endif
        for dir in dirs {
            let url = dir.appendingPathComponent(name)
            if FileManager.default.fileExists(atPath: url.path) { return url }
        }
        throw ResourceError.missing(name)
    }

    public static func tabletModelByteSize(_ model: TabletModelID) -> Int64? {
        guard let url = try? tabletModelURL(model) else { return nil }
        let fm = FileManager.default
        var isDir: ObjCBool = false
        guard fm.fileExists(atPath: url.path, isDirectory: &isDir) else { return nil }
        if !isDir.boolValue {
            let attrs = try? fm.attributesOfItem(atPath: url.path)
            return (attrs?[.size] as? NSNumber)?.int64Value
        }
        guard let enumerator = fm.enumerator(at: url, includingPropertiesForKeys: [.isRegularFileKey, .fileSizeKey]) else {
            return nil
        }
        var total: Int64 = 0
        for case let fileURL as URL in enumerator {
            if let values = try? fileURL.resourceValues(forKeys: [.isRegularFileKey, .fileSizeKey]),
               values.isRegularFile == true,
               let size = values.fileSize {
                total += Int64(size)
            }
        }
        return total > 0 ? total : nil
    }

    public static func probeURL() throws -> URL {
        try find("probe.png")
    }

    public static func noticeURL() throws -> URL {
        try find("NOTICE.txt")
    }
}
#endif
