#if !SKIP
import Foundation
#if os(iOS)
import BackgroundTasks

/// Keeps a user-started batch running when the app goes to the background (iOS 26 `BGContinuedProcessingTask`).
///
/// API (all calls from any thread):
/// ```
/// // 1. once, in application(_:didFinishLaunchingWithOptions:) (LocalSRAppDelegate.onLaunch calls it):
/// BatchBackgroundTask.register()
///
/// // 2. when the user starts a batch (a foreground action, as the system requires):
/// let started = BatchBackgroundTask.shared.begin(title: "Upscaling photos", subtitle: "0 of 12", totalUnits: 12) {
///     // expiration: the system wants the time back -> cancel the running Task; `end` is called for you
///     batchTask.cancel()
/// }
///
/// // 3. after every finished photo (units = number of photos or tiles done):
/// BatchBackgroundTask.shared.update(completedUnits: 3, subtitle: "3 of 12")
///
/// // 4. when the batch finished or was cancelled:
/// BatchBackgroundTask.shared.end(success: true)
/// ```
/// `begin` returns false when the request is rejected (queue full, iOS < 26, simulator without support); the
/// batch then simply runs in the foreground only. By default the task requests CPU and Neural Engine.
/// A tablet caller may request `.gpu` where supported and when the app has the necessary entitlement;
/// the scheduler rejects that request if the entitlement is absent. The system shows a Live Activity with the progress bar; it may
/// expire the task if progress stalls, so call `update` regularly.
public final class BatchBackgroundTask: @unchecked Sendable {
    public static let shared = BatchBackgroundTask()

    /// Info.plist `BGTaskSchedulerPermittedIdentifiers` lists the wildcard `<bundle id>.batch.*`. A literal
    /// wildcard string cannot be registered (BGTaskScheduler refuses it; checked in the simulator), a concrete
    /// identifier matching it can, so one fixed identifier is registered at launch and reused for every batch.
    public static let identifierPattern = "com.hermesreisner.localsr.batch.*"
    public static let taskIdentifier = "com.hermesreisner.localsr.batch.run"

    private let lock = NSLock()
    private var task: BGContinuedProcessingTask?
    private var pendingTitle = ""
    private var pendingSubtitle = ""
    private var pendingCompleted: Int64 = 0
    private var pendingTotal: Int64 = 1
    private var onExpire: (() -> Void)?
    private var finished = false
    private var activeIdentifier: String?
    private var registered = false

    private init() {}

    /// Registers the launch handler. Idempotent; call at launch.
    public static func register() {
        guard #available(iOS 26.0, *) else { return }
        let alreadyRegistered: Bool = shared.lock.withLock {
            let was = shared.registered
            shared.registered = true
            return was
        }
        if alreadyRegistered { return }
        let ok = BGTaskScheduler.shared.register(forTaskWithIdentifier: taskIdentifier, using: nil) { task in
            guard let task = task as? BGContinuedProcessingTask else { task.setTaskCompleted(success: false); return }
            BatchBackgroundTask.shared.attach(task)
        }
        if !ok { NSLog("LocalSR: BGContinuedProcessingTask registration refused (identifier not permitted in Info.plist?)") }
    }

    @discardableResult
    public func begin(title: String, subtitle: String, totalUnits: Int64, requestGPU: Bool = false, onExpire: @escaping () -> Void) -> Bool {
        guard #available(iOS 26.0, *) else { return false }
        Self.register()
        let identifier = Self.taskIdentifier
        lock.lock()
        self.onExpire = onExpire
        pendingTitle = title
        pendingSubtitle = subtitle
        pendingTotal = max(1, totalUnits)
        pendingCompleted = 0
        finished = false
        task = nil
        activeIdentifier = identifier
        lock.unlock()

        let request = BGContinuedProcessingTaskRequest(identifier: identifier, title: title, subtitle: subtitle)
        request.strategy = .fail            // the user just tapped: run now or not at all
        request.requiredResources = requestGPU && BGTaskScheduler.supportedResources.contains(.gpu) ? [.gpu] : []
        do {
            try BGTaskScheduler.shared.submit(request)
            return true
        } catch {
            NSLog("LocalSR: BGContinuedProcessingTask submit failed: \(error)")
            lock.lock(); activeIdentifier = nil; self.onExpire = nil; lock.unlock()
            return false
        }
    }

    public func update(completedUnits: Int64, title: String? = nil, subtitle: String? = nil) {
        lock.lock()
        pendingCompleted = completedUnits
        if let title { pendingTitle = title }
        if let subtitle { pendingSubtitle = subtitle }
        let current = task
        let (t, s, c, total) = (pendingTitle, pendingSubtitle, pendingCompleted, pendingTotal)
        lock.unlock()
        if #available(iOS 26.0, *), let current { Self.apply(current, title: t, subtitle: s, completed: c, total: total) }
    }

    public func end(success: Bool) {
        lock.lock()
        let current = task
        let wasFinished = finished
        finished = true
        task = nil
        onExpire = nil
        let id = activeIdentifier
        activeIdentifier = nil
        lock.unlock()
        if wasFinished { return }
        if #available(iOS 26.0, *) {
            if let current { current.setTaskCompleted(success: success) }
            else if let id { BGTaskScheduler.shared.cancel(taskRequestWithIdentifier: id) }
        }
    }

    @available(iOS 26.0, *)
    private static func apply(_ task: BGContinuedProcessingTask, title: String, subtitle: String, completed: Int64, total: Int64) {
        task.progress.totalUnitCount = total
        task.progress.completedUnitCount = min(completed, total)
        task.updateTitle(title, subtitle: subtitle)
    }

    @available(iOS 26.0, *)
    private func attach(_ task: BGContinuedProcessingTask) {
        lock.lock()
        let stale = finished || activeIdentifier == nil
        if !stale { self.task = task }
        let (t, s, c, total) = (pendingTitle, pendingSubtitle, pendingCompleted, pendingTotal)
        lock.unlock()
        if stale { task.setTaskCompleted(success: false); return }
        task.expirationHandler = { [weak self] in
            guard let self else { return }
            self.lock.lock()
            let expire = self.onExpire
            self.lock.unlock()
            expire?()
            self.end(success: false)
        }
        Self.apply(task, title: t, subtitle: s, completed: c, total: total)
    }
}
#else
/// macOS host (tests, previews): the same API without background execution.
public final class BatchBackgroundTask: @unchecked Sendable {
    public static let shared = BatchBackgroundTask()
    public static let identifierPattern = "com.hermesreisner.localsr.batch.*"
    public static let taskIdentifier = "com.hermesreisner.localsr.batch.run"
    private init() {}
    public static func register() {}
    @discardableResult
    public func begin(title: String, subtitle: String, totalUnits: Int64, requestGPU: Bool = false, onExpire: @escaping () -> Void) -> Bool { false }
    public func update(completedUnits: Int64, title: String? = nil, subtitle: String? = nil) {}
    public func end(success: Bool) {}
}
#endif
#endif
