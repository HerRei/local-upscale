import AppKit
import CoreGraphics
import ImageIO
import UniformTypeIdentifiers

let width = 1600, height = 1200
let colorSpace = CGColorSpaceCreateDeviceRGB()
let context = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8,
                        bytesPerRow: width * 4, space: colorSpace,
                        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
let colors = [CGColor(red: 0.1, green: 0.2, blue: 0.35, alpha: 1),
              CGColor(red: 0.8, green: 0.65, blue: 0.38, alpha: 1)] as CFArray
context.drawLinearGradient(CGGradient(colorsSpace: colorSpace, colors: colors, locations: nil)!,
                           start: .zero, end: CGPoint(x: width, y: height), options: [])
// Thin, contrasting lines and small marks provide real native-resolution detail.
for y in stride(from: 0, to: height, by: 5) {
    for x in stride(from: 0, to: width, by: 7) {
        let value = CGFloat((x * 17 + y * 31) % 255) / 255
        context.setFillColor(CGColor(red: value, green: 1 - value, blue: 0.35, alpha: 0.45))
        context.fill(CGRect(x: x, y: y, width: 2, height: 3))
    }
}
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(cgContext: context, flipped: false)
let text = "LocalSR • native detail 1600 × 1200 • 0123456789"
(text as NSString).draw(at: NSPoint(x: 60, y: 100), withAttributes: [.font: NSFont.boldSystemFont(ofSize: 44), .foregroundColor: NSColor.white])
NSGraphicsContext.restoreGraphicsState()
let target = URL(fileURLWithPath: CommandLine.arguments[1])
let destination = CGImageDestinationCreateWithURL(target as CFURL, UTType.jpeg.identifier as CFString, 1, nil)!
CGImageDestinationAddImage(destination, context.makeImage()!, [kCGImageDestinationLossyCompressionQuality: 0.95,
    kCGImagePropertyExifDictionary: [kCGImagePropertyExifDateTimeOriginal: "2099:01:01 12:00:00"]] as CFDictionary)
precondition(CGImageDestinationFinalize(destination))
