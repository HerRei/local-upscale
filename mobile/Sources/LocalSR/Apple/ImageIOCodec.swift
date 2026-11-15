#if !SKIP
import Foundation
import ImageIO
import CoreGraphics
import Accelerate
import UniformTypeIdentifiers
import LocalSRCore

/// Owns a malloc'ed RGBA8 (noneSkipLast, premultiplication irrelevant: alpha is opaque) pixel buffer.
final class PixelBuffer: @unchecked Sendable {
    let pointer: UnsafeMutablePointer<UInt8>
    let width: Int
    let height: Int
    var byteCount: Int { width * height * 4 }

    init(width: Int, height: Int) {
        self.width = width
        self.height = height
        pointer = UnsafeMutablePointer<UInt8>.allocate(capacity: width * height * 4)
    }
    deinit { pointer.deallocate() }

    func makeCGImage(colorSpace: CGColorSpace) -> CGImage? {
        guard let provider = CGDataProvider(dataInfo: Unmanaged.passRetained(self).toOpaque(), data: pointer, size: byteCount, releaseData: { info, _, _ in
            if let info { Unmanaged<PixelBuffer>.fromOpaque(info).release() }
        }) else { return nil }
        return CGImage(width: width, height: height, bitsPerComponent: 8, bitsPerPixel: 32, bytesPerRow: width * 4, space: colorSpace, bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.noneSkipLast.rawValue), provider: provider, decode: nil, shouldInterpolate: false, intent: .defaultIntent)
    }
}

/// An HDR gain map as ImageIO hands it out (`CGImageSourceCopyAuxiliaryDataInfoAtIndex`).
struct GainMapSource {
    let type: CFString
    let data: Data
    let description: [String: Any]
    let metadata: CGImageMetadata?
    /// `kCGImageAuxiliaryDataInfoColorSpace`, present for the ISO gain map of iPhone photos.
    let colorSpace: CGColorSpace?
    /// 'L008' = kCVPixelFormatType_OneComponent8, what Apple's and the ISO gain maps use.
    static let oneComponent8: Int = 0x4C30_3038
    var pixelFormat: Int { (description["PixelFormat"] as? NSNumber)?.intValue ?? 0 }
    var width: Int { (description[kCGImagePropertyWidth as String] as? NSNumber)?.intValue ?? 0 }
    var height: Int { (description[kCGImagePropertyHeight as String] as? NSNumber)?.intValue ?? 0 }
    var bytesPerRow: Int { (description[kCGImagePropertyBytesPerRow as String] as? NSNumber)?.intValue ?? 0 }
}

struct DecodedImage {
    let pixels: PixelBuffer
    let colorSpace: CGColorSpace
    /// EXIF orientation of the file (1...8); the pixels are already upright.
    let orientation: Int
    /// Upright size of the file before the size policy prescale.
    let originalWidth: Int
    let originalHeight: Int
    let properties: [String: Any]
    let gainMap: GainMapSource?
}

enum ImageIOCodec {
    enum CodecError: Error, CustomStringConvertible {
        case unreadable(URL)
        case decodeFailed
        case encodeFailed(String)
        var description: String {
            switch self {
            case .unreadable(let url): return "Cannot read image: \(url.lastPathComponent)"
            case .decodeFailed: return "Image decoding failed"
            case .encodeFailed(let why): return "Image encoding failed: \(why)"
            }
        }
    }

    // MARK: Decode

    /// Decodes with EXIF orientation applied and the `SizePolicy` prescale in one ImageIO step
    /// (`CGImageSourceCreateThumbnailAtIndex` with transform + max pixel size), keeps the file's colour
    /// space, and renders 8-bit RGBA in that colour space.
    static func decode(url: URL) throws -> DecodedImage {
        let sourceOptions: [CFString: Any] = [kCGImageSourceShouldCache: false]
        guard let source = CGImageSourceCreateWithURL(url as CFURL, sourceOptions as CFDictionary), CGImageSourceGetCount(source) > 0 else {
            throw CodecError.unreadable(url)
        }
        let props = (CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [String: Any]) ?? [:]
        guard let pw = (props[kCGImagePropertyPixelWidth as String] as? NSNumber)?.intValue,
              let ph = (props[kCGImagePropertyPixelHeight as String] as? NSNumber)?.intValue, pw > 0, ph > 0 else {
            throw CodecError.decodeFailed
        }
        let orientation = max(1, min(8, (props[kCGImagePropertyOrientation as String] as? NSNumber)?.intValue ?? 1))
        let swap = orientation >= 5
        let uprightW = swap ? ph : pw
        let uprightH = swap ? pw : ph

        let target = SizePolicy.target(width: uprightW, height: uprightH)
        let thumbOptions: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceThumbnailMaxPixelSize: max(target.width, target.height),
            kCGImageSourceShouldCacheImmediately: true,
        ]
        guard let thumb = CGImageSourceCreateThumbnailAtIndex(source, 0, thumbOptions as CFDictionary) else { throw CodecError.decodeFailed }

        let colorSpace = renderingColorSpace(source: source, fallback: thumb.colorSpace)
        let buffer = PixelBuffer(width: target.width, height: target.height)
        guard let context = CGContext(data: buffer.pointer, width: target.width, height: target.height, bitsPerComponent: 8, bytesPerRow: target.width * 4, space: colorSpace, bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else {
            throw CodecError.decodeFailed
        }
        context.interpolationQuality = .high
        // Opaque black first so that transparent sources (PNG) do not leave garbage under the skip channel.
        context.setFillColor(CGColor(colorSpace: colorSpace, components: [0, 0, 0, 1]) ?? CGColor(gray: 0, alpha: 1))
        context.fill(CGRect(x: 0, y: 0, width: target.width, height: target.height))
        context.draw(thumb, in: CGRect(x: 0, y: 0, width: target.width, height: target.height))

        return DecodedImage(pixels: buffer, colorSpace: colorSpace, orientation: orientation, originalWidth: uprightW, originalHeight: uprightH, properties: props, gainMap: readGainMap(source))
    }

    /// The file's own RGB colour space (Display P3 for iPhone photos). Grey/CMYK/extended-range/HDR spaces
    /// fall back to sRGB resp. Display P3 because the pipeline renders 8-bit SDR.
    static func renderingColorSpace(source: CGImageSource, fallback: CGColorSpace?) -> CGColorSpace {
        var space = CGImageSourceCreateImageAtIndex(source, 0, [kCGImageSourceShouldCache: false] as CFDictionary)?.colorSpace ?? fallback
        if let s = space, s.model != .rgb { space = nil }
        if let s = space, let name = s.name as String?, name.localizedCaseInsensitiveContains("extended") || name.localizedCaseInsensitiveContains("HLG") || name.localizedCaseInsensitiveContains("PQ") || name.localizedCaseInsensitiveContains("2100") {
            space = CGColorSpace(name: CGColorSpace.displayP3)
        }
        return space ?? CGColorSpace(name: CGColorSpace.sRGB)!
    }

    static func readGainMap(_ source: CGImageSource) -> GainMapSource? {
        for type in [kCGImageAuxiliaryDataTypeISOGainMap, kCGImageAuxiliaryDataTypeHDRGainMap] {
            guard let info = CGImageSourceCopyAuxiliaryDataInfoAtIndex(source, 0, type) as? [String: Any],
                  let data = info[kCGImageAuxiliaryDataInfoData as String] as? Data,
                  let description = info[kCGImageAuxiliaryDataInfoDataDescription as String] as? [String: Any] else { continue }
            let metadata = info[kCGImageAuxiliaryDataInfoMetadata as String].map { $0 as! CGImageMetadata }
            let space = info[kCGImageAuxiliaryDataInfoColorSpace as String].map { $0 as! CGColorSpace }
            return GainMapSource(type: type, data: data, description: description, metadata: metadata, colorSpace: space)
        }
        return nil
    }

    // MARK: Gain map

    /// Reorients (EXIF `orientation`, like the base image) and resizes an 8-bit single-channel gain map.
    /// Returns nil for formats this code does not handle (the result is then written without a gain map).
    static func transformGainMap(_ gain: GainMapSource, orientation: Int, targetWidth: Int, targetHeight: Int) -> (data: Data, description: [String: Any])? {
        let w = gain.width, h = gain.height, rowBytes = gain.bytesPerRow
        guard w > 0, h > 0, rowBytes >= w, gain.data.count >= rowBytes * h, targetWidth > 0, targetHeight > 0 else { return nil }
        // 8 bits per pixel only; anything else is dropped (rows may be padded: bytesPerRow >= width).
        guard gain.pixelFormat == GainMapSource.oneComponent8 else { return nil }

        let swap = orientation >= 5
        let ow = swap ? h : w, oh = swap ? w : h
        var oriented = [UInt8](repeating: 0, count: ow * oh)
        gain.data.withUnsafeBytes { raw in
            let src = raw.bindMemory(to: UInt8.self)
            for dy in 0..<oh {
                for dx in 0..<ow {
                    let sx: Int, sy: Int
                    switch orientation {
                    case 2: sx = w - 1 - dx; sy = dy
                    case 3: sx = w - 1 - dx; sy = h - 1 - dy
                    case 4: sx = dx; sy = h - 1 - dy
                    case 5: sx = dy; sy = dx
                    case 6: sx = dy; sy = h - 1 - dx
                    case 7: sx = w - 1 - dy; sy = h - 1 - dx
                    case 8: sx = w - 1 - dy; sy = dx
                    default: sx = dx; sy = dy
                    }
                    oriented[dy * ow + dx] = src[sy * rowBytes + sx]
                }
            }
        }
        var resized = [UInt8](repeating: 0, count: targetWidth * targetHeight)
        let status: vImage_Error = oriented.withUnsafeMutableBytes { s in
            resized.withUnsafeMutableBytes { d in
                var sb = vImage_Buffer(data: s.baseAddress, height: vImagePixelCount(oh), width: vImagePixelCount(ow), rowBytes: ow)
                var db = vImage_Buffer(data: d.baseAddress, height: vImagePixelCount(targetHeight), width: vImagePixelCount(targetWidth), rowBytes: targetWidth)
                return vImageScale_Planar8(&sb, &db, nil, vImage_Flags(kvImageNoFlags))
            }
        }
        guard status == kvImageNoError else { return nil }
        var description = gain.description
        description[kCGImagePropertyWidth as String] = targetWidth
        description[kCGImagePropertyHeight as String] = targetHeight
        description[kCGImagePropertyBytesPerRow as String] = targetWidth
        return (Data(resized), description)
    }

    // MARK: Encode

    /// Properties carried from the source to the output: EXIF, TIFF, GPS, IPTC, with orientation 1 and the new
    /// pixel size. The Apple maker note is not copied: it holds the Live Photo content identifier and capture
    /// data that must not be duplicated onto a derived image (the HDR headroom travels in the gain map's metadata).
    static func outputProperties(source: [String: Any], width: Int, height: Int) -> [String: Any] {
        var result: [String: Any] = [:]
        let keys = [kCGImagePropertyExifDictionary, kCGImagePropertyTIFFDictionary, kCGImagePropertyGPSDictionary, kCGImagePropertyIPTCDictionary, kCGImagePropertyExifAuxDictionary]
        for key in keys {
            if let value = source[key as String] { result[key as String] = value }
        }
        if var exif = result[kCGImagePropertyExifDictionary as String] as? [String: Any] {
            exif[kCGImagePropertyExifPixelXDimension as String] = width
            exif[kCGImagePropertyExifPixelYDimension as String] = height
            result[kCGImagePropertyExifDictionary as String] = exif
        }
        var tiff = (result[kCGImagePropertyTIFFDictionary as String] as? [String: Any]) ?? [:]
        tiff[kCGImagePropertyTIFFOrientation as String] = 1
        result[kCGImagePropertyTIFFDictionary as String] = tiff
        result[kCGImagePropertyOrientation as String] = 1
        return result
    }

    static func encodeHEIC(pixels: PixelBuffer, colorSpace: CGColorSpace, decoded: DecodedImage, to url: URL, quality: Double = 0.9) throws -> (hasGainMap: Bool, note: String) {
        guard let image = pixels.makeCGImage(colorSpace: colorSpace) else { throw CodecError.encodeFailed("CGImage") }
        guard let destination = CGImageDestinationCreateWithURL(url as CFURL, UTType.heic.identifier as CFString, 1, nil) else { throw CodecError.encodeFailed("HEIC destination") }

        // Gain map: ISO first, else Apple's; oriented like the base, sized by the source's gain-map-to-base ratio.
        var gainOutput: (type: CFString, data: Data, description: [String: Any], metadata: CGImageMetadata?, colorSpace: CGColorSpace?)?
        var note = "no gain map"
        if let gain = decoded.gainMap {
            let uprightGainW = decoded.orientation >= 5 ? gain.height : gain.width
            let ratio = decoded.originalWidth > 0 ? Double(uprightGainW) / Double(decoded.originalWidth) : 0.5
            let usable = ratio > 0 && ratio <= 1 ? ratio : 0.5
            let tw = max(1, Int((Double(pixels.width) * usable).rounded()))
            let th = max(1, Int((Double(pixels.height) * usable).rounded()))
            if let t = transformGainMap(gain, orientation: decoded.orientation, targetWidth: tw, targetHeight: th) {
                gainOutput = (gain.type, t.data, t.description, gain.metadata, gain.colorSpace)
                note = "gain map \(gain.type) \(tw)x\(th)"
            } else {
                note = "gain map dropped (unsupported pixel format)"
            }
        }

        var props = outputProperties(source: decoded.properties, width: pixels.width, height: pixels.height)
        props[kCGImageDestinationLossyCompressionQuality as String] = quality
        props[kCGImageDestinationOptimizeColorForSharing as String] = false
        CGImageDestinationAddImage(destination, image, props as CFDictionary)
        if let g = gainOutput {
            var info: [String: Any] = [
                kCGImageAuxiliaryDataInfoData as String: g.data,
                kCGImageAuxiliaryDataInfoDataDescription as String: g.description,
            ]
            if let m = g.metadata { info[kCGImageAuxiliaryDataInfoMetadata as String] = m }
            if let c = g.colorSpace { info[kCGImageAuxiliaryDataInfoColorSpace as String] = c }
            CGImageDestinationAddAuxiliaryDataInfo(destination, g.type, info as CFDictionary)
        }
        guard CGImageDestinationFinalize(destination) else { throw CodecError.encodeFailed("finalize") }
        return (gainOutput != nil, note)
    }

    /// JPEG preview with the long side <= `maxSide` (the whole image when it is already smaller).
    static func writePreviewJPEG(pixels: PixelBuffer, colorSpace: CGColorSpace, to url: URL, maxSide: Int = 4096, quality: Double = 0.85) throws -> (width: Int, height: Int) {
        guard let full = pixels.makeCGImage(colorSpace: colorSpace) else { throw CodecError.encodeFailed("CGImage") }
        let longSide = max(pixels.width, pixels.height)
        var image = full
        var pw = pixels.width, ph = pixels.height
        if longSide > maxSide {
            let scale = Double(maxSide) / Double(longSide)
            pw = max(1, Int((Double(pixels.width) * scale).rounded()))
            ph = max(1, Int((Double(pixels.height) * scale).rounded()))
            guard let ctx = CGContext(data: nil, width: pw, height: ph, bitsPerComponent: 8, bytesPerRow: 0, space: colorSpace, bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { throw CodecError.encodeFailed("preview context") }
            ctx.interpolationQuality = .high
            ctx.draw(full, in: CGRect(x: 0, y: 0, width: pw, height: ph))
            guard let scaled = ctx.makeImage() else { throw CodecError.encodeFailed("preview image") }
            image = scaled
        }
        guard let destination = CGImageDestinationCreateWithURL(url as CFURL, UTType.jpeg.identifier as CFString, 1, nil) else { throw CodecError.encodeFailed("JPEG destination") }
        CGImageDestinationAddImage(destination, image, [kCGImageDestinationLossyCompressionQuality: quality] as CFDictionary)
        guard CGImageDestinationFinalize(destination) else { throw CodecError.encodeFailed("JPEG finalize") }
        return (pw, ph)
    }
}
#endif
