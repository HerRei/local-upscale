import SwiftUI

enum CompareGeometry {
    static func fittedSize(width: CGFloat, height: CGFloat, aspect: CGFloat) -> CGSize {
        let safeAspect = max(aspect, 0.05)
        if width / height > safeAspect {
            return CGSize(width: height * safeAspect, height: height)
        }
        return CGSize(width: width, height: width / safeAspect)
    }

    static func clampOffset(
        _ raw: CGSize,
        zoom: CGFloat,
        fitW: CGFloat,
        fitH: CGFloat,
        viewW: CGFloat,
        viewH: CGFloat
    ) -> CGSize {
        let maxX = max(0.0, (fitW * zoom - viewW) / 2.0)
        let maxY = max(0.0, (fitH * zoom - viewH) / 2.0)
        return CGSize(
            width: min(max(raw.width, -maxX), maxX),
            height: min(max(raw.height, -maxY), maxY)
        )
    }

    static func rubberBandedZoom(_ raw: CGFloat, minZoom: CGFloat = 1.0, maxZoom: CGFloat = 8.0) -> CGFloat {
        if raw < minZoom {
            let deficit = minZoom - raw
            return minZoom - (deficit * 0.45) / (1.0 + deficit * 1.2)
        }
        if raw > maxZoom {
            let excess = raw - maxZoom
            return maxZoom + (excess * 0.35) / (1.0 + excess * 0.25)
        }
        return raw
    }
}

struct CompareLockedOverlayConfig {
    let progress: Double
    let isPreparing: Bool
    let triggerID: String
}

struct CompareStageView: View {
    let originalURL: URL
    let upscaledURL: URL?
    let originalMaxPixelSize: Int
    let upscaledMaxPixelSize: Int
    let aspectRatio: CGFloat
    let stageBackground: Color
    let stageInset: CGFloat
    let dividerSquareColor: Color
    let minZoom: CGFloat
    let maxZoom: CGFloat
    let lockedOverlay: CompareLockedOverlayConfig?

    @Binding var splitFraction: CGFloat
    @Binding var zoom: CGFloat
    @Binding var offset: CGSize
    @Binding var isFullResolutionActive: Bool
    @Binding var isDraggingDivider: Bool

    var onSingleTap: (() -> Void)? = nil
    var onUserInteraction: (() -> Void)? = nil

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    @State private var dragStartFraction: CGFloat? = nil
    @State private var dividerAtEdge: Int = 0
    @State private var gestureZoom: CGFloat = 1.0
    @State private var gestureOffset: CGSize = .zero
    @State private var isPinching: Bool = false
    @State private var isDividerHovered: Bool = false

    private var hasCompareResult: Bool {
        upscaledURL != nil
    }

    private var isInteractionLocked: Bool {
        lockedOverlay != nil
    }

    var body: some View {
        let rawZoom = zoom * gestureZoom
        let effectiveZoom = CompareGeometry.rubberBandedZoom(rawZoom, minZoom: minZoom, maxZoom: maxZoom)
        let rawOffset = CGSize(
            width: offset.width + gestureOffset.width,
            height: offset.height + gestureOffset.height
        )

        GeometryReader { geo in
            let width = max(geo.size.width, 1.0)
            let height = max(geo.size.height, 1.0)
            let fitAvailW = max(width - 2.0 * stageInset, 1.0)
            let fitAvailH = max(height - 2.0 * stageInset, 1.0)
            let fitted = CompareGeometry.fittedSize(width: fitAvailW, height: fitAvailH, aspect: aspectRatio)
            let fitW = fitted.width
            let fitH = fitted.height
            let effectiveOffset = CompareGeometry.clampOffset(
                rawOffset,
                zoom: max(effectiveZoom, 1.0),
                fitW: fitW,
                fitH: fitH,
                viewW: width,
                viewH: height
            )

            let activeW = min(fitW * effectiveZoom, width)
            let activeH = min(fitH * effectiveZoom, height)
            let activeLeft = (width - activeW) / 2.0
            let activeTop = (height - activeH) / 2.0
            let dividerX = activeLeft + splitFraction * activeW
            let minSquareX = activeLeft + min(22.0, activeW / 2.0)
            let maxSquareX = activeLeft + activeW - min(22.0, activeW / 2.0)
            let squareX = min(max(dividerX, minSquareX), maxSquareX)

            ZStack(alignment: .topLeading) {
                #if !SKIP
                Color.clear
                    .frame(width: max(width - 80.0, 1.0), height: max(height - 320.0, 1.0))
                    .position(x: width / 2.0, y: height / 2.0)
                    .allowsHitTesting(false)
                    .accessibilityElement()
                    .accessibilityLabel("Viewer Canvas")
                    .accessibilityIdentifier("viewerGestureArea")
                #endif

                // Layer 1: Background + images + tap/pan gestures (separate from divider handle layer)
                ZStack(alignment: .topLeading) {
                    stageBackground

                    if let upscaledURL {
                        DecodedImageView(
                            url: upscaledURL,
                            maxPixelSize: upscaledMaxPixelSize,
                            contentMode: .fit,
                            exactFrame: true,
                            accessibilityID: "viewerUpscaledImage"
                        )
                        .frame(width: fitW, height: fitH)
                        .frame(width: width, height: height)
                        .scaleEffect(effectiveZoom)
                        .offset(effectiveOffset)
                        .clipped()

                        DecodedImageView(
                            url: originalURL,
                            maxPixelSize: originalMaxPixelSize,
                            contentMode: .fit,
                            exactFrame: true,
                            accessibilityID: "viewerOriginalImage"
                        )
                        .frame(width: fitW, height: fitH)
                        .frame(width: width, height: height)
                        .scaleEffect(effectiveZoom)
                        .offset(effectiveOffset)
                        .clipped()
                        #if SKIP
                        .composeModifier { $0.clipHorizontalBounds(dividerX) }
                        #else
                        .clipShape(SplitClipRect(clipWidth: dividerX))
                        #endif
                    } else {
                        DecodedImageView(
                            url: originalURL,
                            maxPixelSize: originalMaxPixelSize,
                            contentMode: .fit,
                            exactFrame: true,
                            accessibilityID: "viewerOriginalImage"
                        )
                        .frame(width: fitW, height: fitH)
                        .frame(width: width, height: height)
                        .scaleEffect(effectiveZoom)
                        .offset(effectiveOffset)
                        .clipped()
                    }
                }
                .frame(width: width, height: height)
                #if !SKIP
                .contentShape(Rectangle())
                #endif
                #if SKIP
                .onTapGesture(count: 2) { tap in
                    onUserInteraction?()
                    guard !isInteractionLocked else { return }
                    toggleDoubleTapZoom(at: tap, fitW: fitW, fitH: fitH, viewW: width, viewH: height)
                }
                .onTapGesture {
                    onUserInteraction?()
                    onSingleTap?()
                }
                #else
                .gesture(
                    SpatialTapGesture(count: 2)
                        .onEnded { value in
                            onUserInteraction?()
                            guard !isInteractionLocked else { return }
                            toggleDoubleTapZoom(at: value.location, fitW: fitW, fitH: fitH, viewW: width, viewH: height)
                        }
                        .exclusively(
                            before: TapGesture(count: 1)
                                .onEnded {
                                    onUserInteraction?()
                                    onSingleTap?()
                                }
                        )
                )
                #endif
                .simultaneousGesture(
                    DragGesture(minimumDistance: 6.0, coordinateSpace: .global)
                        .onChanged { value in
                            onUserInteraction?()
                            guard !isPinching && !isDraggingDivider else { return }
                            if zoom > 1.01 || effectiveZoom > 1.01 {
                                gestureOffset = value.translation
                            } else if !isInteractionLocked && hasCompareResult {
                                if dragStartFraction == nil {
                                    dragStartFraction = splitFraction
                                }
                                let base = dragStartFraction ?? splitFraction
                                let next = base + value.translation.width / max(activeW, 1.0)
                                updateDividerFraction(next)
                            }
                        }
                        .onEnded { value in
                            guard !isPinching && !isDraggingDivider else {
                                gestureOffset = .zero
                                dragStartFraction = nil
                                return
                            }
                            if zoom > 1.01 || effectiveZoom > 1.01 {
                                let raw = CGSize(
                                    width: offset.width + value.translation.width,
                                    height: offset.height + value.translation.height
                                )
                                offset = CompareGeometry.clampOffset(raw, zoom: zoom, fitW: fitW, fitH: fitH, viewW: width, viewH: height)
                                gestureOffset = .zero
                            } else {
                                dragStartFraction = nil
                            }
                        }
                )

                // Layer 2: Locked overlay OR Divider + 44x44 minimum priority handle
                if let locked = lockedOverlay {
                    let rerunW = fitW
                    let rerunH = fitH
                    let rerunLeft = (width - rerunW) / 2.0
                    let rerunTop = (height - rerunH) / 2.0
                    let rerunLineX = locked.progress * rerunW

                    ZStack(alignment: .topLeading) {
                        if locked.isPreparing {
                            Color.black.opacity(0.35)
                                .frame(width: rerunW, height: rerunH)
                        } else {
                            Color.black.opacity(0.35)
                                .frame(width: max(0.0, rerunW - rerunLineX), height: rerunH)
                                .offset(x: rerunLineX)
                            SweepLine(
                                lineX: rerunLineX,
                                squareX: rerunLineX,
                                height: rerunH,
                                squareSide: 12.0,
                                showHandle: false,
                                squareColor: dividerSquareColor
                            )
                            .frame(width: rerunW, height: rerunH)
                            .animation(reduceMotion ? nil : .linear(duration: 0.25), value: locked.progress)
                        }
                    }
                    .frame(width: rerunW, height: rerunH)
                    .overlay {
                        PhotoBrackets(
                            photoWidth: rerunW,
                            photoHeight: rerunH,
                            mode: locked.isPreparing ? BracketMotionMode.hunt : BracketMotionMode.lock,
                            triggerID: locked.triggerID,
                            color: Color.white
                        )
                    }
                    .position(x: rerunLeft + rerunW / 2.0, y: rerunTop + rerunH / 2.0)
                } else if hasCompareResult {
                    let hoverScale: CGFloat = isDividerHovered ? (40.0 / 36.0) : 1.0
                    SweepLine(
                        lineX: dividerX - activeLeft,
                        squareX: squareX - activeLeft,
                        height: activeH,
                        squareSide: 36.0,
                        showHandle: true,
                        squareColor: dividerSquareColor,
                        handleScale: hoverScale
                    )
                    .frame(width: activeW, height: activeH)
                    .position(x: activeLeft + activeW / 2.0, y: activeTop + activeH / 2.0)
                    .animation(reduceMotion ? nil : .easeOut(duration: 0.15), value: isDividerHovered)
                    .allowsHitTesting(false)

                    let hitMinX = min(dividerX, squareX) - 22.0
                    let hitMaxX = max(dividerX, squareX) + 22.0
                    let hitWidth = max(44.0, hitMaxX - hitMinX)
                    let hitHeight = max(44.0, activeH)
                    let hitCenterX = (hitMinX + hitMaxX) / 2.0

                    Color.black.opacity(0.001)
                        .frame(width: hitWidth, height: hitHeight)
                        #if !SKIP
                        .contentShape(Rectangle())
                        .onHover { hovering in
                            isDividerHovered = hovering
                            if hovering {
                                onUserInteraction?()
                            }
                        }
                        .highPriorityGesture(dividerDragGesture(activeWidth: activeW))
                        #else
                        .gesture(dividerDragGesture(activeWidth: activeW))
                        #endif
                        #if !SKIP
                        .accessibilityElement(children: .ignore)
                        #endif
                        .accessibilityLabel("Before and after divider")
                        .accessibilityValue("\(Int((splitFraction * 100.0).rounded())) percent")
                        .accessibilityIdentifier("compareDividerHandle")
                        #if !SKIP
                        .accessibilityAdjustableAction { direction in
                            onUserInteraction?()
                            switch direction {
                            case .increment:
                                updateDividerFraction(splitFraction + 0.05)
                            case .decrement:
                                updateDividerFraction(splitFraction - 0.05)
                            @unknown default:
                                break
                            }
                        }
                        #endif
                        .position(x: hitCenterX, y: activeTop + activeH / 2.0)
                }
            }
            .frame(width: width, height: height)
            .clipped()
            .simultaneousGesture(
                MagnifyGesture()
                    .onChanged { value in
                        onUserInteraction?()
                        isPinching = true
                        gestureZoom = value.magnification
                        let currentEffective = CompareGeometry.rubberBandedZoom(
                            zoom * value.magnification,
                            minZoom: minZoom,
                            maxZoom: maxZoom
                        )
                        if currentEffective > 1.2 && !isInteractionLocked && !isFullResolutionActive {
                            isFullResolutionActive = true
                        }
                    }
                    .onEnded { value in
                        isPinching = false
                        let rawEnd = zoom * value.magnification
                        let updated = min(max(rawEnd, minZoom), maxZoom)
                        let settle = {
                            if minZoom >= 1.0 && updated <= 1.01 {
                                zoom = 1.0
                            } else if abs(updated - 1.0) <= 0.015 {
                                zoom = 1.0
                            } else {
                                zoom = updated
                            }
                            gestureZoom = 1.0
                            if zoom <= 1.01 {
                                offset = .zero
                                gestureOffset = .zero
                                isFullResolutionActive = false
                            } else {
                                offset = CompareGeometry.clampOffset(offset, zoom: zoom, fitW: fitW, fitH: fitH, viewW: width, viewH: height)
                                if zoom > 1.2 && !isInteractionLocked {
                                    isFullResolutionActive = true
                                }
                            }
                        }
                        if reduceMotion {
                            settle()
                        } else {
                            withAnimation(.snappy(duration: 0.28)) {
                                settle()
                            }
                        }
                    }
            )
        }
    }

    private func toggleDoubleTapZoom(at tap: CGPoint, fitW: CGFloat, fitH: CGFloat, viewW: CGFloat, viewH: CGFloat) {
        let toggle = {
            if zoom > 1.01 || zoom < 0.99 {
                zoom = 1.0
                gestureZoom = 1.0
                offset = .zero
                gestureOffset = .zero
                isFullResolutionActive = false
            } else {
                let targetZoom = min(maxZoom, 3.0)
                let raw = CGSize(
                    width: (viewW / 2.0 - tap.x) * (targetZoom - 1.0),
                    height: (viewH / 2.0 - tap.y) * (targetZoom - 1.0)
                )
                zoom = targetZoom
                gestureZoom = 1.0
                offset = CompareGeometry.clampOffset(raw, zoom: targetZoom, fitW: fitW, fitH: fitH, viewW: viewW, viewH: viewH)
                gestureOffset = .zero
                if targetZoom > 1.2 {
                    isFullResolutionActive = true
                }
            }
        }
        if reduceMotion {
            toggle()
        } else {
            withAnimation(.snappy(duration: 0.3)) {
                toggle()
            }
        }
    }

    private func dividerDragGesture(activeWidth: CGFloat) -> some Gesture {
        DragGesture(minimumDistance: 0.0, coordinateSpace: .global)
            .onChanged { value in
                onUserInteraction?()
                guard !isPinching else { return }
                isDraggingDivider = true
                if dragStartFraction == nil {
                    dragStartFraction = splitFraction
                }
                let base = dragStartFraction ?? splitFraction
                let next = base + value.translation.width / max(activeWidth, 1.0)
                updateDividerFraction(next)
            }
            .onEnded { _ in
                dragStartFraction = nil
                isDraggingDivider = false
            }
    }

    private func updateDividerFraction(_ raw: CGFloat) {
        let clamped = min(max(raw, 0.0), 1.0)
        splitFraction = clamped
        if clamped <= 0.001 {
            if dividerAtEdge != -1 {
                dividerAtEdge = -1
                Haptics.lightImpact()
            }
        } else if clamped >= 0.999 {
            if dividerAtEdge != 1 {
                dividerAtEdge = 1
                Haptics.lightImpact()
            }
        } else if clamped > 0.01 && clamped < 0.99 {
            dividerAtEdge = 0
        }
    }
}

struct CompareChipsRow: View {
    #if !SKIP
    @ScaledMetric(relativeTo: .footnote) private var scaledRowHeight: CGFloat = 44.0
    #endif
    @Binding var splitFraction: CGFloat
    let enhancedTitle: String
    let enhancedIdentifier: String
    let isDisabled: Bool
    let useLiquidGlass: Bool
    let reduceMotion: Bool
    let reduceTransparency: Bool
    var onTapChip: (() -> Void)? = nil

    var body: some View {
        GeometryReader { chipGeo in
            let totalWidth = max(chipGeo.size.width, 1.0)
            let origWidth = splitFraction * totalWidth
            let enhancedWidth = (1.0 - splitFraction) * totalWidth

            HStack {
                chipButton(
                    title: "Original",
                    accessibilityLabel: "Show original only",
                    identifier: "originalChipButton",
                    visible: origWidth >= 60.0
                ) {
                    onTapChip?()
                    let target: CGFloat = splitFraction >= 0.99 ? 0.5 : 1.0
                    animateDivider(to: target)
                }

                Spacer()

                chipButton(
                    title: enhancedTitle,
                    accessibilityLabel: "Show \(enhancedTitle.lowercased()) only",
                    identifier: enhancedIdentifier,
                    visible: enhancedWidth >= 60.0
                ) {
                    onTapChip?()
                    let target: CGFloat = splitFraction <= 0.01 ? 0.5 : 0.0
                    animateDivider(to: target)
                }
            }
        }
        #if SKIP
        .frame(height: 44.0)
        #else
        .frame(height: scaledRowHeight)
        #endif
    }

    @ViewBuilder
    private func chipButton(
        title: String,
        accessibilityLabel: String,
        identifier: String,
        visible: Bool,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            if useLiquidGlass {
                #if SKIP
                Text(title)
                    .themeFont(.meta)
                    .foregroundStyle(Theme.stageText)
                    .padding(.horizontal, 12.0)
                    .padding(.vertical, 6.0)
                    .background(Color.black.opacity(0.6), in: Capsule())
                #else
                Text(title)
                    .themeFont(.meta)
                    .foregroundStyle(reduceTransparency ? Theme.text : Theme.stageText)
                    .padding(.horizontal, 12.0)
                    .padding(.vertical, 6.0)
                    .modifier(TabletGlassCapsuleModifier(reduceTransparency: reduceTransparency))
                #endif
            } else {
                let phoneLabel = Text(title)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Color.white)
                    .padding(.horizontal, 10.0)
                    .padding(.vertical, 6.0)
                #if SKIP
                phoneLabel
                    .background(Color.black.opacity(0.6), in: Capsule())
                #else
                phoneLabel
                    .background {
                        if reduceTransparency {
                            Capsule().fill(Color.black.opacity(0.75))
                        } else {
                            Capsule().fill(.ultraThinMaterial)
                        }
                    }
                #endif
            }
        }
        .padding(.vertical, 8.0)
        #if !SKIP
        .contentShape(Rectangle())
        #endif
        .buttonStyle(.plain)
        .opacity(visible ? 1.0 : 0.0)
        .allowsHitTesting(visible && !isDisabled)
        .disabled(!visible || isDisabled)
        #if !SKIP
        .accessibilityHidden(!visible)
        #endif
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.15), value: visible)
        .accessibilityLabel(accessibilityLabel)
        .accessibilityIdentifier(identifier)
    }

    private func animateDivider(to target: CGFloat) {
        if reduceMotion {
            splitFraction = target
        } else {
            withAnimation(.smooth(duration: 0.3)) {
                splitFraction = target
            }
        }
    }
}

#if !SKIP
private struct TabletGlassCapsuleModifier: ViewModifier {
    let reduceTransparency: Bool

    func body(content: Content) -> some View {
        if reduceTransparency {
            content.background(Theme.chrome.opacity(0.92), in: Capsule())
        } else {
            content.glassEffect(.regular, in: Capsule())
        }
    }
}

struct SplitClipRect: Shape {
    let clipWidth: CGFloat

    func path(in rect: CGRect) -> Path {
        var path = Path()
        let clamped = min(max(clipWidth, 0.0), rect.width)
        path.addRect(CGRect(x: rect.minX, y: rect.minY, width: clamped, height: rect.height))
        return path
    }
}
#endif
