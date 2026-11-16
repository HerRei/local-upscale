#if !SKIP
import Foundation
import Observation
import SwiftUI

@MainActor
@Observable
public final class TabletCommandRouter {
    public static let shared = TabletCommandRouter()

    var isActive: Bool = false
    var isThreePane: Bool = false
    var canPaste: Bool = false
    var canStartSelected: Bool = false
    var canStartAll: Bool = false
    var isBatchRunning: Bool = false
    var hasSelection: Bool = false
    var canRemoveSelected: Bool = false
    var hasViewerPhoto: Bool = false
    var hasCompareResult: Bool = false
    var canSaveSelected: Bool = false
    var canSaveAll: Bool = false
    var upscaleSelectedTitle: String = "Upscale Selected"

    var onChooseFiles: (() -> Void)?
    var onChoosePhotos: (() -> Void)?
    var onPaste: (() -> Void)?
    var onUpscaleSelected: (() -> Void)?
    var onStartAll: (() -> Void)?
    var onCancelQueue: (() -> Void)?
    var onPreviousPhoto: (() -> Void)?
    var onNextPhoto: (() -> Void)?
    var onRemoveSelected: (() -> Void)?
    var onTab1: (() -> Void)?
    var onTab2: (() -> Void)?
    var onTab3: (() -> Void)?
    var onZoomFit: (() -> Void)?
    var onZoomActual: (() -> Void)?
    var onZoomIn: (() -> Void)?
    var onZoomOut: (() -> Void)?
    var onDividerLeft: (() -> Void)?
    var onDividerRight: (() -> Void)?
    var onDividerStart: (() -> Void)?
    var onDividerEnd: (() -> Void)?
    var onToggleOriginal: (() -> Void)?
    var onSaveSelected: (() -> Void)?
    var onSaveAll: (() -> Void)?
    var onModelLibrary: (() -> Void)?
    var onSettings: (() -> Void)?

    private init() {}
}

public struct LocalSRCommands: Commands {
    @State private var router = TabletCommandRouter.shared

    public init() {}

    public var body: some Commands {
        CommandGroup(replacing: .newItem) {
            if router.isActive {
                Button("Choose Files\u{2026}") {
                    router.onChooseFiles?()
                }
                .keyboardShortcut("o", modifiers: .command)

                Button("Choose from Photos\u{2026}") {
                    router.onChoosePhotos?()
                }
                .keyboardShortcut("o", modifiers: [.command, .shift])
            }
        }

        CommandGroup(replacing: .pasteboard) {
            if router.isActive {
                Button("Paste") {
                    router.onPaste?()
                }
                .keyboardShortcut("v", modifiers: .command)
                .disabled(!router.canPaste)
            }
        }

        CommandGroup(replacing: .saveItem) {
            if router.isActive {
                Button("Save to Photos") {
                    router.onSaveSelected?()
                }
                .keyboardShortcut("s", modifiers: .command)
                .disabled(!router.canSaveSelected)

                Button("Save All to Photos") {
                    router.onSaveAll?()
                }
                .keyboardShortcut("s", modifiers: [.command, .shift])
                .disabled(!router.canSaveAll)
            }
        }

        CommandGroup(replacing: .appSettings) {
            if router.isActive {
                Button("Settings\u{2026}") {
                    router.onSettings?()
                }
                .keyboardShortcut(",", modifiers: .command)
            }
        }

        CommandMenu("View") {
            if router.isActive {
                Button(router.isThreePane ? "Toggle Media Pane" : "Media") {
                    router.onTab1?()
                }
                .keyboardShortcut("1", modifiers: .command)

                Button("Viewer") {
                    router.onTab2?()
                }
                .keyboardShortcut("2", modifiers: .command)
                .disabled(router.isThreePane)

                Button("Enhance") {
                    router.onTab3?()
                }
                .keyboardShortcut("3", modifiers: .command)
                .disabled(router.isThreePane)

                Divider()

                Button("Model Library\u{2026}") {
                    router.onModelLibrary?()
                }
                .keyboardShortcut("l", modifiers: [.command, .shift])

                Divider()

                Button("Fit Photo") {
                    router.onZoomFit?()
                }
                .keyboardShortcut("0", modifiers: .command)
                .disabled(!router.hasViewerPhoto)

                Button("Actual Size (1:1)") {
                    router.onZoomActual?()
                }
                .keyboardShortcut("0", modifiers: [.command, .option])
                .disabled(!router.hasViewerPhoto)

                Button("Zoom In") {
                    router.onZoomIn?()
                }
                .keyboardShortcut("+", modifiers: .command)
                .disabled(!router.hasViewerPhoto)

                Button("Zoom In (Equals)") {
                    router.onZoomIn?()
                }
                .keyboardShortcut("=", modifiers: .command)
                .disabled(!router.hasViewerPhoto)

                Button("Zoom Out") {
                    router.onZoomOut?()
                }
                .keyboardShortcut("-", modifiers: .command)
                .disabled(!router.hasViewerPhoto)

                Divider()

                Button("Move Divider Left") {
                    router.onDividerLeft?()
                }
                .keyboardShortcut(.leftArrow, modifiers: [])
                .disabled(!router.hasCompareResult)

                Button("Move Divider Right") {
                    router.onDividerRight?()
                }
                .keyboardShortcut(.rightArrow, modifiers: [])
                .disabled(!router.hasCompareResult)

                Button("Show Enhanced") {
                    router.onDividerStart?()
                }
                .keyboardShortcut(.home, modifiers: [])
                .disabled(!router.hasCompareResult)

                Button("Show Original") {
                    router.onDividerEnd?()
                }
                .keyboardShortcut(.end, modifiers: [])
                .disabled(!router.hasCompareResult)

                Button("Compare Original") {
                    router.onToggleOriginal?()
                }
                .keyboardShortcut("m", modifiers: [])
                .disabled(!router.hasCompareResult)
            }
        }

        CommandMenu("Queue") {
            if router.isActive {
                Button(router.upscaleSelectedTitle) {
                    router.onUpscaleSelected?()
                }
                .keyboardShortcut(.return, modifiers: .command)
                .disabled(!router.canStartSelected)

                Button("Start All") {
                    router.onStartAll?()
                }
                .keyboardShortcut(.return, modifiers: [.command, .shift])
                .disabled(!router.canStartAll)

                Button("Cancel Queue") {
                    router.onCancelQueue?()
                }
                .keyboardShortcut(".", modifiers: .command)
                .disabled(!router.isBatchRunning)

                Divider()

                Button("Previous Photo") {
                    router.onPreviousPhoto?()
                }
                .keyboardShortcut(.upArrow, modifiers: [])
                .disabled(!router.hasSelection)

                Button("Next Photo") {
                    router.onNextPhoto?()
                }
                .keyboardShortcut(.downArrow, modifiers: [])
                .disabled(!router.hasSelection)

                Button("Remove from Queue") {
                    router.onRemoveSelected?()
                }
                .keyboardShortcut(.delete, modifiers: [])
                .disabled(!router.canRemoveSelected)
            }
        }
    }
}
#endif
