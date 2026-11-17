import SwiftUI
#if !SKIP && os(iOS)
import UIKit
#endif

enum DesignTokens {
    static var brandFill: Color {
        #if SKIP
        return Color(colorImpl: {
            EnvironmentValues.shared.colorScheme == .dark
                ? androidx.compose.ui.graphics.Color(0xFFC2502F)
                : androidx.compose.ui.graphics.Color(0xFFB3452A)
        })
        #elseif os(iOS)
        return Color(uiColor: UIColor { traits in
            if traits.userInterfaceStyle == .dark {
                return UIColor(red: 0.76078, green: 0.31373, blue: 0.18431, alpha: 1.0)
            }
            return UIColor(red: 0.70196, green: 0.27059, blue: 0.16471, alpha: 1.0)
        })
        #else
        return Color(red: 0.70196, green: 0.27059, blue: 0.16471)
        #endif
    }

    static var brandText: Color {
        #if SKIP
        return Color(colorImpl: {
            EnvironmentValues.shared.colorScheme == .dark
                ? androidx.compose.ui.graphics.Color(0xFFE8704F)
                : androidx.compose.ui.graphics.Color(0xFFB3452A)
        })
        #elseif os(iOS)
        return Color(uiColor: UIColor { traits in
            if traits.userInterfaceStyle == .dark {
                return UIColor(red: 0.90980, green: 0.43922, blue: 0.30980, alpha: 1.0)
            }
            return UIColor(red: 0.70196, green: 0.27059, blue: 0.16471, alpha: 1.0)
        })
        #else
        return Color(red: 0.70196, green: 0.27059, blue: 0.16471)
        #endif
    }

    static var ink: Color {
        Color.primary
    }

    static var stage: Color {
        #if SKIP
        return Color(colorImpl: {
            androidx.compose.material3.MaterialTheme.colorScheme.surfaceContainerHigh
        })
        #elseif os(iOS)
        return Color(uiColor: .secondarySystemBackground)
        #else
        return Color.secondary.opacity(0.12)
        #endif
    }

    static var androidBarBackground: Color {
        #if SKIP
        return Color(colorImpl: {
            androidx.compose.material3.MaterialTheme.colorScheme.surfaceContainerHigh
        })
        #else
        return stage
        #endif
    }

    static var controlCircleFill: Color {
        #if SKIP
        return Color.primary.opacity(0.10)
        #else
        return Color.secondary.opacity(0.18)
        #endif
    }

    static let iconCream = Color(red: 0.94510, green: 0.93725, blue: 0.90196)
    static let iconInk = Color(red: 0.14118, green: 0.15294, blue: 0.12549)
    static let iconRust = Color(red: 0.70196, green: 0.23922, blue: 0.14902)
}

@MainActor
enum Haptics {
    static func success() {
        #if SKIP || os(iOS)
        let generator = UINotificationFeedbackGenerator()
        generator.notificationOccurred(.success)
        #endif
    }

    static func warning() {
        #if SKIP || os(iOS)
        let generator = UINotificationFeedbackGenerator()
        generator.notificationOccurred(.warning)
        #endif
    }

    static func error() {
        #if SKIP || os(iOS)
        let generator = UINotificationFeedbackGenerator()
        generator.notificationOccurred(.error)
        #endif
    }

    static func lightImpact() {
        #if SKIP || os(iOS)
        let generator = UIImpactFeedbackGenerator(style: .light)
        generator.impactOccurred()
        #endif
    }

    static func softImpact() {
        #if SKIP || os(iOS)
        let generator = UIImpactFeedbackGenerator(style: .soft)
        generator.impactOccurred()
        #endif
    }
}

struct FrameMark: View {
    let side: CGFloat
    var showsPlus: Bool = false
    var animateLockOnAppear: Bool = false
    var bracketColor: Color = DesignTokens.ink
    var squareColor: Color = DesignTokens.brandFill

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var lockOffset: CGFloat = 0.0
    @State private var lockOpacity: Double = 1.0
    @State private var didAnimateLock: Bool = false

    var body: some View {
        let arm = 0.30 * side
        let thickness = 0.10 * side
        let squareSide = 0.24 * side
        let squareRadius = 0.12 * squareSide

        ZStack {
            // Top-left bracket
            cornerBracket(arm: arm, thickness: thickness, flipX: false, flipY: false)
                .frame(width: side, height: side, alignment: .topLeading)
                .offset(x: -lockOffset, y: -lockOffset)
                .opacity(lockOpacity)

            // Top-right bracket
            cornerBracket(arm: arm, thickness: thickness, flipX: true, flipY: false)
                .frame(width: side, height: side, alignment: .topTrailing)
                .offset(x: lockOffset, y: -lockOffset)
                .opacity(lockOpacity)

            // Bottom-left bracket
            cornerBracket(arm: arm, thickness: thickness, flipX: false, flipY: true)
                .frame(width: side, height: side, alignment: .bottomLeading)
                .offset(x: -lockOffset, y: lockOffset)
                .opacity(lockOpacity)

            // Bottom-right bracket
            cornerBracket(arm: arm, thickness: thickness, flipX: true, flipY: true)
                .frame(width: side, height: side, alignment: .bottomTrailing)
                .offset(x: lockOffset, y: lockOffset)
                .opacity(lockOpacity)

            RoundedRectangle(cornerRadius: squareRadius, style: .continuous)
                .fill(squareColor)
                .frame(width: squareSide, height: squareSide)
                .overlay {
                    if showsPlus {
                        Image(systemName: "plus")
                            .font(.system(size: squareSide * 0.42, weight: .bold))
                            .foregroundStyle(Color.white)
                    }
                }
        }
        .frame(width: side, height: side)
        .task {
            guard animateLockOnAppear && !reduceMotion && !didAnimateLock else {
                lockOffset = 0.0
                lockOpacity = 1.0
                return
            }
            didAnimateLock = true
            lockOffset = 16.0
            lockOpacity = 0.0
            try? await Task.sleep(nanoseconds: 20_000_000)
            withAnimation(.spring(response: 0.35, dampingFraction: 0.7)) {
                lockOffset = 0.0
                lockOpacity = 1.0
            }
        }
    }

    @ViewBuilder
    private func cornerBracket(arm: CGFloat, thickness: CGFloat, flipX: Bool, flipY: Bool) -> some View {
        ZStack(alignment: alignment(flipX: flipX, flipY: flipY)) {
            Rectangle()
                .fill(bracketColor)
                .frame(width: arm, height: thickness)
            Rectangle()
                .fill(bracketColor)
                .frame(width: thickness, height: arm)
        }
        .frame(width: arm, height: arm)
    }

    private func alignment(flipX: Bool, flipY: Bool) -> Alignment {
        if !flipX && !flipY {
            return Alignment.topLeading
        } else if flipX && !flipY {
            return Alignment.topTrailing
        } else if !flipX && flipY {
            return Alignment.bottomLeading
        } else {
            return Alignment.bottomTrailing
        }
    }
}

enum BracketMotionMode: Equatable {
    case lock
    case hunt
}

struct PhotoBrackets: View {
    let photoWidth: CGFloat
    let photoHeight: CGFloat
    let mode: BracketMotionMode
    let triggerID: String
    var color: Color = DesignTokens.ink
    var arm: CGFloat = 22.0
    var thickness: CGFloat = 5.0
    var restOutset: CGFloat = 14.0

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var outset: CGFloat = 14.0
    @State private var bracketOpacity: Double = 1.0

    var body: some View {
        let boxWidth = photoWidth + 2.0 * (outset + thickness)
        let boxHeight = photoHeight + 2.0 * (outset + thickness)

        ZStack {
            cornerBracket(flipX: false, flipY: false)
                .frame(width: boxWidth, height: boxHeight, alignment: .topLeading)
            cornerBracket(flipX: true, flipY: false)
                .frame(width: boxWidth, height: boxHeight, alignment: .topTrailing)
            cornerBracket(flipX: false, flipY: true)
                .frame(width: boxWidth, height: boxHeight, alignment: .bottomLeading)
            cornerBracket(flipX: true, flipY: true)
                .frame(width: boxWidth, height: boxHeight, alignment: .bottomTrailing)
        }
        .opacity(bracketOpacity)
        .allowsHitTesting(false)
        .onAppear {
            if mode == .hunt || reduceMotion {
                outset = restOutset
            }
        }
        .task(id: "\(triggerID)_\(mode == .hunt ? "hunt" : "lock")") {
            if reduceMotion {
                outset = restOutset
                bracketOpacity = 1.0
                return
            }
            switch mode {
            case .hunt:
                outset = restOutset
                bracketOpacity = 1.0
                try? await Task.sleep(nanoseconds: 20_000_000)
                if Task.isCancelled { return }
                withAnimation(.easeInOut(duration: 0.9).repeatForever(autoreverses: true)) {
                    outset = restOutset + 8.0
                }
            case .lock:
                outset = restOutset + 16.0
                bracketOpacity = 0.0
                try? await Task.sleep(nanoseconds: 20_000_000)
                if Task.isCancelled { return }
                withAnimation(.spring(response: 0.35, dampingFraction: 0.7)) {
                    outset = restOutset
                    bracketOpacity = 1.0
                }
            }
        }
    }

    private func bracketAlignment(flipX: Bool, flipY: Bool) -> Alignment {
        if !flipX && !flipY {
            return Alignment.topLeading
        } else if flipX && !flipY {
            return Alignment.topTrailing
        } else if !flipX && flipY {
            return Alignment.bottomLeading
        } else {
            return Alignment.bottomTrailing
        }
    }

    @ViewBuilder
    private func cornerBracket(flipX: Bool, flipY: Bool) -> some View {
        ZStack(alignment: bracketAlignment(flipX: flipX, flipY: flipY)) {
            Rectangle()
                .fill(color)
                .frame(width: arm, height: thickness)
            Rectangle()
                .fill(color)
                .frame(width: thickness, height: arm)
        }
        .frame(width: arm, height: arm)
    }
}

struct SweepLine: View {
    let lineX: CGFloat
    let squareX: CGFloat
    let height: CGFloat
    let squareSide: CGFloat
    var showHandle: Bool = false
    var lineWidth: CGFloat = 2.0
    var squareColor: Color = DesignTokens.brandFill
    var handleScale: CGFloat = 1.0

    var body: some View {
        let cornerRadius = 0.12 * squareSide

        ZStack(alignment: .topLeading) {
            ZStack {
                Rectangle()
                    .fill(Color.black.opacity(0.25))
                    .frame(width: lineWidth + 1.0, height: height)
                Rectangle()
                    .fill(Color.white)
                    .frame(width: lineWidth, height: height)
            }
            .position(x: lineX, y: height / 2.0)

            RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                .fill(squareColor)
                .frame(width: squareSide, height: squareSide)
                .overlay {
                    if showHandle {
                        RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                            .stroke(Color.white, lineWidth: 1.5)
                    }
                }
                .overlay {
                    if showHandle {
                        Image(systemName: "chevron.left.chevron.right")
                            .font(.system(size: 13.0, weight: .semibold))
                            .foregroundStyle(Color.white)
                    }
                }
                .scaleEffect(handleScale)
                .position(x: squareX, y: height / 2.0)
        }
    }
}
