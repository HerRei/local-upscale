import SwiftUI

public struct ContentView: View {
    @State private var appModel = AppModel()
    @Environment(\.scenePhase) private var scenePhase

    public init() {}

    public var body: some View {
        HomeView()
            .environment(appModel)
            .onChange(of: scenePhase) { _, newPhase in
                if newPhase == .background {
                    Task {
                        await appModel.releaseIdleModel()
                    }
                }
            }
    }
}
