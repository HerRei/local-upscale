import SwiftUI
import LocalSRCore

public struct ContentView: View {
    @State private var appModel = AppModel()
    @Environment(\.scenePhase) private var scenePhase

    @AppStorage("selectedModel") private var selectedModel: ModelKey = .best
    @AppStorage("fullScale") private var fullScale: Bool = false
    @AppStorage("shareStartsImmediately") private var shareStartsImmediately: Bool = true

    public init() {}

    public var body: some View {
        let root = HomeView()
            .environment(appModel)
            .tint(DesignTokens.brandText)
            .task {
                #if SKIP
                AndroidShareInbox.setOnInputsAvailable {
                    Task { @MainActor in
                        await appModel.importSharedInboxIfNeeded(
                            model: selectedModel,
                            fullScale: fullScale,
                            startImmediately: shareStartsImmediately
                        )
                    }
                }
                #endif
                await appModel.importSharedInboxIfNeeded(
                    model: selectedModel,
                    fullScale: fullScale,
                    startImmediately: shareStartsImmediately
                )
            }
            .onChange(of: scenePhase) { _, newPhase in
                if newPhase == .active {
                    Task {
                        await appModel.importSharedInboxIfNeeded(
                            model: selectedModel,
                            fullScale: fullScale,
                            startImmediately: shareStartsImmediately
                        )
                    }
                } else if newPhase == .background {
                    Task {
                        await appModel.releaseIdleModel()
                    }
                }
            }

        #if !SKIP
        root
            .onOpenURL { url in
                if url.scheme?.lowercased() == ShareInboxConstants.urlScheme {
                    Task {
                        await appModel.importSharedInboxIfNeeded(
                            model: selectedModel,
                            fullScale: fullScale,
                            startImmediately: shareStartsImmediately
                        )
                    }
                }
            }
        #else
        root
        #endif
    }
}
