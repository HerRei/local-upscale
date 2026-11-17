import SwiftUI
#if !SKIP && canImport(UIKit)
import UIKit
#elseif SKIP
import androidx.compose.ui.platform.LocalContext
#endif

/// Design tokens for the LocalSR tablet workspace (dark values match desktop CSS; light values mirror them).
public enum Theme {
    // MARK: - Colours

    static let stage = Color(red: 0x0F / 255.0, green: 0x0F / 255.0, blue: 0x0F / 255.0)
    static let stageText = Color(red: 0xEC / 255.0, green: 0xEC / 255.0, blue: 0xEC / 255.0)
    static let stageSecondary = Color(red: 0xA3 / 255.0, green: 0xA3 / 255.0, blue: 0xA3 / 255.0)
    static let stageTrack = Color.white.opacity(0.14)

    static var base: Color { adaptive(dark: 0x161616, light: 0xE4E4E4) }
    static var panel: Color { adaptive(dark: 0x1B1B1B, light: 0xF4F4F4) }
    static var chrome: Color { adaptive(dark: 0x222222, light: 0xFAFAFA) }
    static var raised: Color { adaptive(dark: 0x2A2A2A, light: 0xFFFFFF) }
    static var hover: Color { adaptive(dark: 0x313131, light: 0xE9E9E9) }
    static var recess: Color { adaptive(dark: 0x141414, light: 0xE8E8E8) }

    static var track: Color { adaptiveAlpha(darkWhite: true, darkAlpha: 0.10, lightAlpha: 0.10) }

    static var text: Color { adaptive(dark: 0xECECEC, light: 0x1A1A1A) }
    static var secondary: Color { adaptive(dark: 0xA3A3A3, light: 0x5C5C5C) }
    static var tertiary: Color { adaptive(dark: 0x8C8C8C, light: 0x6B6B6B) }

    static var border: Color { adaptiveAlpha(darkWhite: true, darkAlpha: 0.09, lightAlpha: 0.10) }
    static var divider: Color { adaptive(dark: 0x09090B, light: 0xD0D0D0) }

    static let accent = Color(red: 0xB3 / 255.0, green: 0x45 / 255.0, blue: 0x2A / 255.0)
    static let accentHi = Color(red: 0xBF / 255.0, green: 0x4C / 255.0, blue: 0x2F / 255.0)

    static var selectFill: Color { adaptiveAlpha(darkWhite: true, darkAlpha: 0.06, lightAlpha: 0.05) }
    static var selectLine: Color { adaptiveAlpha(darkWhite: true, darkAlpha: 0.50, lightAlpha: 0.55) }
    static var focus: Color { adaptiveAlpha(darkWhite: true, darkAlpha: 0.72, lightAlpha: 0.72) }

    static var danger: Color { adaptive(dark: 0xE5484D, light: 0xC62F35) }
    static var warning: Color { adaptive(dark: 0xDE9935, light: 0x9A5B00) }
    static var ok: Color { adaptive(dark: 0x6DB07F, light: 0x3F8F58) }

    private static func adaptive(dark: Int, light: Int) -> Color {
        #if SKIP
        return Color(colorImpl: {
            let hex = EnvironmentValues.shared.colorScheme == .dark ? dark : light
            return androidx.compose.ui.graphics.Color(
                red: (hex >> 16) & 0xFF,
                green: (hex >> 8) & 0xFF,
                blue: hex & 0xFF,
                alpha: 255
            )
        })
        #elseif os(iOS)
        return Color(uiColor: UIColor { traits in
            let hex = traits.userInterfaceStyle == .dark ? dark : light
            return UIColor(
                red: CGFloat((hex >> 16) & 0xFF) / 255.0,
                green: CGFloat((hex >> 8) & 0xFF) / 255.0,
                blue: CGFloat(hex & 0xFF) / 255.0,
                alpha: 1.0
            )
        })
        #else
        return Color(
            red: Double((dark >> 16) & 0xFF) / 255.0,
            green: Double((dark >> 8) & 0xFF) / 255.0,
            blue: Double(dark & 0xFF) / 255.0
        )
        #endif
    }

    private static func adaptiveAlpha(darkWhite: Bool, darkAlpha: Double, lightAlpha: Double) -> Color {
        #if SKIP
        return Color(colorImpl: {
            if EnvironmentValues.shared.colorScheme == .dark {
                let c = darkWhite ? 255 : 0
                return androidx.compose.ui.graphics.Color(red: c, green: c, blue: c, alpha: Int((darkAlpha * 255.0).rounded()))
            } else {
                return androidx.compose.ui.graphics.Color(red: 0, green: 0, blue: 0, alpha: Int((lightAlpha * 255.0).rounded()))
            }
        })
        #elseif os(iOS)
        return Color(uiColor: UIColor { traits in
            if traits.userInterfaceStyle == .dark {
                return UIColor(white: darkWhite ? 1.0 : 0.0, alpha: darkAlpha)
            }
            return UIColor(white: 0.0, alpha: lightAlpha)
        })
        #else
        return Color.white.opacity(darkAlpha)
        #endif
    }

    // MARK: - Spacing & Layout

    enum Spacing {
        static let xs: CGFloat = 4.0
        static let sm: CGFloat = 8.0
        static let md: CGFloat = 12.0
        static let lg: CGFloat = 16.0
        static let xl: CGFloat = 24.0

        static let panePadding: CGFloat = 16.0
        static let sectionGap: CGFloat = 16.0
        static let controlHeight: CGFloat = 36.0
        static let hitArea: CGFloat = 44.0
        static let listRowHeight: CGFloat = 64.0
        static let headerHeight: CGFloat = 52.0
        static let statusBarHeight: CGFloat = 64.0
        static let statusBarTwoRowHeight: CGFloat = 108.0

        static let mediaPaneWidth: CGFloat = 280.0
        static let enhancePaneWidth: CGFloat = 360.0
        static let foldWidth: CGFloat = 1100.0
        static let twoColumnWidth: CGFloat = 700.0
        static let mediaGridWidth: CGFloat = 600.0
    }

    // MARK: - Radii

    enum Radius {
        static let control: CGFloat = 3.0
        static let card: CGFloat = 3.0
        static let thumbnail: CGFloat = 3.0
        static let meterCell: CGFloat = 1.0
        static let node: CGFloat = 1.0
        static let photo: CGFloat = 0.0
    }

    // MARK: - Type Scale

    public enum TextStyle {
        case paneTitle
        case control
        case body
        case label
        case meta
        case caption
        case emptyTitle

        var baseSize: CGFloat {
            switch self {
            case .paneTitle: return 17.0
            case .control: return 15.0
            case .body: return 15.0
            case .label: return 13.0
            case .meta: return 13.0
            case .caption: return 12.0
            case .emptyTitle: return 22.0
            }
        }

        var defaultWeight: Font.Weight {
            switch self {
            case .paneTitle: return .semibold
            case .control: return .medium
            case .body: return .regular
            case .label: return .semibold
            case .meta: return .regular
            case .caption: return .regular
            case .emptyTitle: return .semibold
            }
        }

        var relativeTextStyle: Font.TextStyle {
            switch self {
            case .paneTitle: return .headline
            case .control: return .subheadline
            case .body: return .subheadline
            case .label: return .footnote
            case .meta: return .footnote
            case .caption: return .caption
            case .emptyTitle: return .title2
            }
        }
    }

    static let fontName = "IBM Plex Sans Var"

    static func font(_ style: TextStyle, weight: Font.Weight? = nil) -> Font {
        let resolvedWeight = weight ?? style.defaultWeight
        #if SKIP
        let weightCode = composeFontWeightCode(resolvedWeight)
        let sizeFloat = Float(style.baseSize)
        return Font(fontImpl: {
            AndroidUISupport.ibmPlexTextStyle(LocalContext.current, sizeFloat, weightCode)
        })
        #else
        return Font.custom(fontName, size: style.baseSize, relativeTo: style.relativeTextStyle)
            .weight(resolvedWeight)
        #endif
    }

    #if SKIP
    private static func composeFontWeightCode(_ weight: Font.Weight) -> Int {
        switch weight {
        case .medium: return 500
        case .semibold: return 600
        case .bold, .heavy, .black: return 700
        default: return 400
        }
    }
    #endif
}

private struct ThemeFontModifier: ViewModifier {
    let style: Theme.TextStyle
    let weight: Font.Weight?
    let monospacedDigits: Bool

    init(style: Theme.TextStyle, weight: Font.Weight?, monospacedDigits: Bool) {
        self.style = style
        self.weight = weight
        self.monospacedDigits = monospacedDigits
    }

    func body(content: Content) -> some View {
        let font = Theme.font(style, weight: weight)
        #if SKIP
        content.font(font)
        #else
        if monospacedDigits {
            content
                .font(font)
                .monospacedDigit()
        } else {
            content
                .font(font)
        }
        #endif
    }
}

extension View {
    func themeFont(
        _ style: Theme.TextStyle,
        weight: Font.Weight? = nil,
        monospacedDigits: Bool = true
    ) -> some View {
        modifier(ThemeFontModifier(style: style, weight: weight, monospacedDigits: monospacedDigits))
    }

    @ViewBuilder
    func tabletCompactDynamicType() -> some View {
        #if !SKIP
        self.dynamicTypeSize(...DynamicTypeSize.xLarge)
        #else
        self
        #endif
    }

    @ViewBuilder
    func tabletHitTarget() -> some View {
        #if !SKIP
        self.frame(minWidth: 44.0, minHeight: 44.0).contentShape(Rectangle())
        #else
        self.frame(minWidth: 44.0, minHeight: 44.0)
        #endif
    }

    @ViewBuilder
    func tabletHoverFill(_ isHovered: Bool, cornerRadius: CGFloat = Theme.Radius.control) -> some View {
        if isHovered {
            self.background(
                RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                    .fill(Theme.hover)
            )
        } else {
            self
        }
    }

    @ViewBuilder
    func tabletDisableLiftHover() -> some View {
        #if !SKIP && os(iOS)
        self.hoverEffectDisabled()
        #else
        self
        #endif
    }
}
