#if !SKIP
import Foundation

public enum ShareInboxConstants {
    public static let appGroupID = "group.com.hermesreisner.localsr"
    public static let urlScheme = "localsr"
}

public struct ShareInboxManifest: Codable, Sendable, Equatable {
    public struct Item: Codable, Sendable, Equatable {
        public let filename: String
        public let displayName: String?

        public init(filename: String, displayName: String? = nil) {
            self.filename = filename
            self.displayName = displayName
        }
    }

    public let version: Int
    public let createdAt: String?
    public let items: [Item]

    public init(version: Int = 1, createdAt: String? = nil, items: [Item]) {
        self.version = version
        self.createdAt = createdAt
        self.items = items
    }
}

public enum ShareInboxImporter {
    public static func defaultInboxDirectory() -> URL? {
        FileManager.default
            .containerURL(forSecurityApplicationGroupIdentifier: ShareInboxConstants.appGroupID)?
            .appendingPathComponent("Inbox", isDirectory: true)
    }

    public static func importAll(from inboxDirectory: URL, to inputsDirectory: URL) -> [PickedPhotoInput] {
        let fm = FileManager.default
        var isInboxDir: ObjCBool = false
        guard fm.fileExists(atPath: inboxDirectory.path, isDirectory: &isInboxDir), isInboxDir.boolValue else {
            return []
        }

        let entries: [URL]
        do {
            entries = try fm.contentsOfDirectory(
                at: inboxDirectory,
                includingPropertiesForKeys: [.isDirectoryKey, .creationDateKey],
                options: [.skipsHiddenFiles]
            )
        } catch {
            return []
        }

        let batchDirs = entries.filter { url in
            (try? url.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true
        }.sorted { lhs, rhs in
            let ld = (try? lhs.resourceValues(forKeys: [.creationDateKey]).creationDate) ?? .distantPast
            let rd = (try? rhs.resourceValues(forKeys: [.creationDateKey]).creationDate) ?? .distantPast
            if ld != rd {
                return ld < rd
            }
            return lhs.lastPathComponent < rhs.lastPathComponent
        }

        var imported: [PickedPhotoInput] = []
        for batchDir in batchDirs {
            imported.append(contentsOf: importBatch(from: batchDir, to: inputsDirectory, fileManager: fm))
        }
        return imported
    }

    private static func importBatch(
        from batchDir: URL,
        to inputsDirectory: URL,
        fileManager fm: FileManager
    ) -> [PickedPhotoInput] {
        let manifestURL = batchDir.appendingPathComponent("manifest.json")
        guard fm.fileExists(atPath: manifestURL.path),
              let data = try? Data(contentsOf: manifestURL),
              let manifest = try? JSONDecoder().decode(ShareInboxManifest.self, from: data),
              manifest.version == 1,
              !manifest.items.isEmpty else {
            return []
        }

        let resolvedBatchPath = batchDir.standardizedFileURL.resolvingSymlinksInPath().path
        var validItems: [(sourceURL: URL, displayName: String?)] = []

        for item in manifest.items {
            guard !item.filename.isEmpty,
                  !item.filename.contains("/"),
                  !item.filename.contains("\\"),
                  item.filename != ".",
                  item.filename != ".." else {
                continue
            }
            let resolvedSource = batchDir
                .appendingPathComponent(item.filename)
                .standardizedFileURL
                .resolvingSymlinksInPath()
            guard resolvedSource.path.hasPrefix(resolvedBatchPath + "/") else {
                continue
            }
            var isFileDir: ObjCBool = false
            guard fm.fileExists(atPath: resolvedSource.path, isDirectory: &isFileDir), !isFileDir.boolValue else {
                continue
            }
            validItems.append((resolvedSource, item.displayName))
        }

        guard !validItems.isEmpty else {
            return []
        }

        do {
            try fm.createDirectory(at: inputsDirectory, withIntermediateDirectories: true)
        } catch {
            return []
        }

        var copiedDestURLs: [URL] = []
        var importedBatch: [PickedPhotoInput] = []

        for entry in validItems {
            let ext = entry.sourceURL.pathExtension
            let destName = ext.isEmpty ? UUID().uuidString : "\(UUID().uuidString).\(ext)"
            let destURL = inputsDirectory.appendingPathComponent(destName)
            do {
                try fm.copyItem(at: entry.sourceURL, to: destURL)
                copiedDestURLs.append(destURL)
                importedBatch.append(PickedPhotoInput(url: destURL, displayName: entry.displayName))
            } catch {
                for url in copiedDestURLs {
                    try? fm.removeItem(at: url)
                }
                return []
            }
        }

        try? fm.removeItem(at: batchDir)
        return importedBatch
    }
}
#endif
