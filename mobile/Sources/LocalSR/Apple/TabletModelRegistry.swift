#if !SKIP
import Foundation
import LocalSRCore
#if os(iOS)
import UIKit
#endif

/// Lazily owns one engine per model and compute unit. Each entry serializes access to its
/// reusable tensors. Different entries can predict concurrently; an active entry survives eviction.
public final class TabletModelRegistry: @unchecked Sendable {
    private final class Entry: @unchecked Sendable {
        let lock = NSLock()
        let engine: CoreMLEngine
        init(_ descriptor: ModelDescriptor) {
            engine = CoreMLEngine(window: descriptor.window, modelScale: descriptor.scale)
        }
    }
    private let lock = NSLock()
    private var entries: [String: Entry] = [:]
    #if os(iOS)
    private var observer: NSObjectProtocol?
    #endif

    public init() {
        #if os(iOS)
        observer = NotificationCenter.default.addObserver(forName: UIApplication.didReceiveMemoryWarningNotification, object: nil, queue: nil) { [weak self] _ in
            self?.unloadAll()
        }
        #endif
    }

    deinit {
        #if os(iOS)
        if let observer { NotificationCenter.default.removeObserver(observer) }
        #endif
    }

    /// Runs a synchronous operation with exclusive use of the selected engine.
    public func withEngine<T>(model: TabletModelID, unit: ComputeUnit, _ body: (CoreMLEngine) throws -> T) throws -> T {
        let key = model.rawValue + ":" + unit.rawValue
        let entry: Entry = lock.withLock {
            if let existing = entries[key] { return existing }
            let created = Entry(ModelCatalog.model(model))
            entries[key] = created
            return created
        }
        return try entry.lock.withLock {
            if entry.engine.loadedTabletModel != model || entry.engine.loadedUnit != unit {
                try entry.engine.load(tabletModel: model, unit: unit)
            }
            return try body(entry.engine)
        }
    }

    public func unloadAll() { lock.withLock { entries.removeAll() } }
}
#endif
