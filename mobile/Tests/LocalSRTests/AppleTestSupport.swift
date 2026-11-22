#if !SKIP
import Foundation
import ImageIO
import CoreGraphics
import CoreImage
import UniformTypeIdentifiers
import LocalSRCore
@testable import LocalSR

/// Synthetic photos for the macOS-host tests.
enum Synthetic {
    struct Options {
        var width = 1000
        var height = 750
        var orientation = 1
        var gps = true
        var displayP3 = true
        var type: UTType = .heic
    }

    static let gpsLatitude = 47.5596, gpsLongitude = 7.5886, gpsAltitude = 260.0

    /// Smooth gradients plus a few sharp shapes, so 4x SR and a 4x box downsample have something to agree on.
    /// A red square in the top-left corner of the *stored* image marks the orientation.
    static func pixels(width: Int, height: Int) -> [UInt8] {
        var rgba = [UInt8](repeating: 255, count: width * height * 4)
        for y in 0..<height {
            for x in 0..<width {
                let i = (y * width + x) * 4
                var r = Double(x) / Double(width), g = Double(y) / Double(height), b = 0.5 + 0.4 * sin(Double(x + y) / 37.0)
                if (x / 40 + y / 40) % 2 == 0 && x > width / 2 && y > height / 2 { r = 0.1; g = 0.1; b = 0.1 }
                let dx = Double(x - width / 3), dy = Double(y - height / 2)
                if dx * dx + dy * dy < 90 * 90 { r = 0.95; g = 0.9; b = 0.1 }
                if x < width / 8 && y < height / 8 { r = 1; g = 0; b = 0 }
                rgba[i] = UInt8(max(0, min(255, r * 255))); rgba[i + 1] = UInt8(max(0, min(255, g * 255))); rgba[i + 2] = UInt8(max(0, min(255, b * 255)))
            }
        }
        return rgba
    }

    static func cgImage(width: Int, height: Int, colorSpace: CGColorSpace) -> CGImage {
        var rgba = pixels(width: width, height: height)
        let ctx = rgba.withUnsafeMutableBytes { raw in
            CGContext(data: raw.baseAddress, width: width, height: height, bitsPerComponent: 8, bytesPerRow: width * 4, space: colorSpace, bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)!
        }
        return ctx.makeImage()!
    }

    static func tempDirectory(_ name: String) -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("localsr-tests-\(name)-\(UUID().uuidString)", isDirectory: true)
        try! FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    static func metadata(orientation: Int, gps: Bool) -> [String: Any] {
        var props: [String: Any] = [
            kCGImagePropertyOrientation as String: orientation,
            kCGImagePropertyTIFFDictionary as String: [
                kCGImagePropertyTIFFMake as String: "LocalSR", kCGImagePropertyTIFFModel as String: "Synthetic 1",
                kCGImagePropertyTIFFOrientation as String: orientation,
            ],
            kCGImagePropertyExifDictionary as String: [
                kCGImagePropertyExifDateTimeOriginal as String: "2026:11:14 10:30:00",
                "OffsetTimeOriginal": "+01:00",
                kCGImagePropertyExifFNumber as String: 1.8,
                kCGImagePropertyExifLensModel as String: "Synthetic lens",
            ],
        ]
        if gps {
            props[kCGImagePropertyGPSDictionary as String] = [
                kCGImagePropertyGPSLatitude as String: gpsLatitude, kCGImagePropertyGPSLatitudeRef as String: "N",
                kCGImagePropertyGPSLongitude as String: gpsLongitude, kCGImagePropertyGPSLongitudeRef as String: "E",
                kCGImagePropertyGPSAltitude as String: gpsAltitude, kCGImagePropertyGPSAltitudeRef as String: 0,
            ]
        }
        return props
    }

    /// Writes a photo with EXIF/TIFF/GPS and the given orientation tag (pixels are stored as given).
    @discardableResult
    static func writePhoto(_ options: Options = Options(), to directory: URL, name: String = "photo") throws -> URL {
        let space = CGColorSpace(name: options.displayP3 ? CGColorSpace.displayP3 : CGColorSpace.sRGB)!
        let image = cgImage(width: options.width, height: options.height, colorSpace: space)
        let ext = options.type == .heic ? "heic" : "jpg"
        let url = directory.appendingPathComponent("\(name).\(ext)")
        let dest = CGImageDestinationCreateWithURL(url as CFURL, options.type.identifier as CFString, 1, nil)!
        var props = metadata(orientation: options.orientation, gps: options.gps)
        props[kCGImageDestinationLossyCompressionQuality as String] = 0.95
        CGImageDestinationAddImage(dest, image, props as CFDictionary)
        guard CGImageDestinationFinalize(dest) else { throw ImageIOCodec.CodecError.encodeFailed("test photo") }
        return url
    }

    /// Tone-map metadata of an ISO gain map as iPhone 16/17 photos carry it (HDRToneMap XMP).
    static func isoGainMapMetadata() -> CGImageMetadata? {
        let xmp = """
        <x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description rdf:about="" xmlns:HDRToneMap="http://ns.apple.com/HDRToneMap/1.0/" HDRToneMap:Version="1" HDRToneMap:BaseHeadroom="0.0" HDRToneMap:AlternateHeadroom="1.5" HDRToneMap:BaseColorIsWorkingColor="True"><HDRToneMap:ChannelMetadata><rdf:Seq><rdf:li rdf:parseType="Resource"><HDRToneMap:GainMapMin>-0.2</HDRToneMap:GainMapMin><HDRToneMap:GainMapMax>1.5</HDRToneMap:GainMapMax><HDRToneMap:Gamma>0.9</HDRToneMap:Gamma><HDRToneMap:BaseOffset>0.00001</HDRToneMap:BaseOffset><HDRToneMap:AlternateOffset>0.00001</HDRToneMap:AlternateOffset></rdf:li></rdf:Seq></HDRToneMap:ChannelMetadata></rdf:Description></rdf:RDF></x:xmpmeta>
        """
        return CGImageMetadataCreateFromXMPData(Data(xmp.utf8) as CFData)
    }

    /// Apple's older gain-map metadata (HDRGainMap XMP).
    static func appleGainMapMetadata() -> CGImageMetadata? {
        let m = CGImageMetadataCreateMutable()
        CGImageMetadataRegisterNamespaceForPrefix(m, "http://ns.apple.com/HDRGainMap/1.0/" as CFString, "HDRGainMap" as CFString, nil)
        CGImageMetadataSetValueWithPath(m, nil, "HDRGainMap:HDRGainMapVersion" as CFString, 131072 as CFNumber)
        CGImageMetadataSetValueWithPath(m, nil, "HDRGainMap:HDRGainMapHeadroom" as CFString, 2.5 as CFNumber)
        return m
    }

    /// Gain map (stored orientation, half size, 64-byte padded rows like iPhone files): a bright block at the
    /// stored top-left corner, otherwise a ramp.
    static func gainMapBytes(width: Int, height: Int, rowBytes: Int) -> Data {
        var bytes = [UInt8](repeating: 0, count: rowBytes * height)
        for y in 0..<height { for x in 0..<width {
            bytes[y * rowBytes + x] = (x < width / 8 && y < height / 8) ? 250 : UInt8(40 + 80 * x / width)
        } }
        return Data(bytes)
    }

    /// An HDR HEIC written with ImageIO: SDR base + gain map of the given type. The base is `width` x `height`
    /// as stored, tagged with `orientation`.
    static func writeGainMapPhoto(width: Int, height: Int, type: CFString, to directory: URL, orientation: Int = 1) -> URL? {
        let space = CGColorSpace(name: CGColorSpace.displayP3)!
        let image = cgImage(width: width, height: height, colorSpace: space)
        let url = directory.appendingPathComponent("hdr-\(type == kCGImageAuxiliaryDataTypeISOGainMap ? "iso" : "apple").heic")
        guard let dest = CGImageDestinationCreateWithURL(url as CFURL, UTType.heic.identifier as CFString, 1, nil) else { return nil }
        var props = metadata(orientation: orientation, gps: true)
        props[kCGImageDestinationLossyCompressionQuality as String] = 0.95
        CGImageDestinationAddImage(dest, image, props as CFDictionary)
        let gw = width / 2, gh = height / 2, rowBytes = (gw + 63) / 64 * 64
        let meta = type == kCGImageAuxiliaryDataTypeISOGainMap ? isoGainMapMetadata() : appleGainMapMetadata()
        var info: [String: Any] = [
            kCGImageAuxiliaryDataInfoData as String: gainMapBytes(width: gw, height: gh, rowBytes: rowBytes),
            kCGImageAuxiliaryDataInfoDataDescription as String: ["Width": gw, "Height": gh, "BytesPerRow": rowBytes, "PixelFormat": GainMapSource.oneComponent8],
        ]
        if let meta { info[kCGImageAuxiliaryDataInfoMetadata as String] = meta }
        CGImageDestinationAddAuxiliaryDataInfo(dest, type, info as CFDictionary)
        guard CGImageDestinationFinalize(dest) else { return nil }
        return url
    }

    // MARK: Reading results

    static func properties(of url: URL) -> [String: Any] {
        let source = CGImageSourceCreateWithURL(url as CFURL, nil)!
        return CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as! [String: Any]
    }

    static func rgba(of url: URL, maxSide: Int? = nil) -> (pixels: [UInt8], width: Int, height: Int, colorSpaceName: String?) {
        let source = CGImageSourceCreateWithURL(url as CFURL, nil)!
        var options: [CFString: Any] = [kCGImageSourceCreateThumbnailFromImageAlways: true, kCGImageSourceCreateThumbnailWithTransform: true]
        let full = CGImageSourceCreateImageAtIndex(source, 0, nil)!
        options[kCGImageSourceThumbnailMaxPixelSize] = maxSide ?? max(full.width, full.height)
        let image = CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary)!
        let space = CGColorSpace(name: CGColorSpace.displayP3)!
        var rgba = [UInt8](repeating: 0, count: image.width * image.height * 4)
        rgba.withUnsafeMutableBytes { raw in
            let ctx = CGContext(data: raw.baseAddress, width: image.width, height: image.height, bitsPerComponent: 8, bytesPerRow: image.width * 4, space: space, bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)!
            ctx.interpolationQuality = .high
            ctx.draw(image, in: CGRect(x: 0, y: 0, width: image.width, height: image.height))
        }
        return (rgba, image.width, image.height, full.colorSpace?.name as String?)
    }

    /// PSNR (dB) over RGB between two RGBA8 buffers of the same size.
    static func psnr(_ a: [UInt8], _ b: [UInt8]) -> Double {
        precondition(a.count == b.count)
        var sum = 0.0
        var n = 0
        var i = 0
        while i < a.count {
            for c in 0..<3 { let d = Double(a[i + c]) - Double(b[i + c]); sum += d * d; n += 1 }
            i += 4
        }
        if sum == 0 { return 99 }
        return 10 * log10(255.0 * 255.0 / (sum / Double(n)))
    }
}
#endif
