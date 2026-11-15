import Foundation
import OSLog
import SwiftUI

/// A logger for the LocalSR module.
let logger: Logger = Logger(subsystem: "com.hermesreisner.localsr", category: "LocalSR")

/// The shared top-level view for the app, loaded from the platform-specific App delegates below.
///
/// The default implementation merely loads the `ContentView` for the app and logs a message.
public struct LocalSRRootView : View {
    public init() {
    }

    public var body: some View {
        ContentView()
            .task {
                logger.info("Skip app logs are viewable in the Xcode console for iOS; Android logs can be viewed in Studio or using adb logcat")
            }
    }
}

/// Global application delegate functions.
///
/// These functions can update a shared observable object to communicate app state changes to interested views.
public final class LocalSRAppDelegate : Sendable {
    public static let shared = LocalSRAppDelegate()

    private init() {
    }

    public func onInit() {
        logger.debug("onInit")
    }

    public func onLaunch() {
        logger.debug("onLaunch")
        #if !SKIP
        // iOS: launch handler for continued processing of user-started batches (no-op on macOS).
        BatchBackgroundTask.register()
        #if DEBUG
        AppleSelfTest.runIfRequested()   // debug-only PhotoKit smoke test, see AppleSelfTest.swift
        #endif
        #endif
    }

    public func onResume() {
        logger.debug("onResume")
    }

    public func onPause() {
        logger.debug("onPause")
    }

    public func onStop() {
        logger.debug("onStop")
    }

    public func onDestroy() {
        logger.debug("onDestroy")
    }

    public func onLowMemory() {
        logger.debug("onLowMemory")
    }
}
