#if !SKIP && DEBUG
import Foundation
import ImageIO
import CoreGraphics
import Photos
import CoreLocation
import UniformTypeIdentifiers
import LocalSRCore

/// Debug-only smoke test that runs inside the real app (simulator or device), because PhotoKit and
/// BackgroundTasks need an app bundle: launch with the environment variable `LOCALSR_SELFTEST=photos`
/// (simulator: `SIMCTL_CHILD_LOCALSR_SELFTEST=photos xcrun simctl launch --console-pty booted <bundle id>`
/// after `xcrun simctl privacy booted grant photos <bundle id>`). It upscales a generated photo with EXIF
/// date and GPS, saves it with `saveToLibrary` and reads the asset back. Results go to stdout ("SELFTEST ...").
enum AppleSelfTest {
    /// stderr is unbuffered, so the lines show up in `simctl launch --console` immediately.
    static func log(_ line: String) { fputs(line + "\n", stderr) }

    static func runIfRequested() {
        guard ProcessInfo.processInfo.environment["LOCALSR_SELFTEST"] == "photos" else { return }
        Task.detached {
            do { try await run(); log("SELFTEST PASS") } catch { log("SELFTEST FAIL: \(error)") }
        }
    }

    static func makeInput(in directory: URL) throws -> URL {
        let w = 420, h = 300
        var rgba = [UInt8](repeating: 255, count: w * h * 4)
        for y in 0..<h { for x in 0..<w { let i = (y * w + x) * 4; rgba[i] = UInt8(x * 255 / w); rgba[i + 1] = UInt8(y * 255 / h); rgba[i + 2] = 120 } }
        let space = CGColorSpace(name: CGColorSpace.displayP3)!
        let ctx = rgba.withUnsafeMutableBytes { CGContext(data: $0.baseAddress, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4, space: space, bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)! }
        let url = directory.appendingPathComponent("selftest.heic")
        let dest = CGImageDestinationCreateWithURL(url as CFURL, UTType.heic.identifier as CFString, 1, nil)!
        let props: [String: Any] = [
            kCGImageDestinationLossyCompressionQuality as String: 0.95,
            kCGImagePropertyExifDictionary as String: [kCGImagePropertyExifDateTimeOriginal as String: "2026:11:14 10:30:00", "OffsetTimeOriginal": "+01:00"],
            kCGImagePropertyGPSDictionary as String: [kCGImagePropertyGPSLatitude as String: 47.5596, kCGImagePropertyGPSLatitudeRef as String: "N", kCGImagePropertyGPSLongitude as String: 7.5886, kCGImagePropertyGPSLongitudeRef as String: "E"],
        ]
        CGImageDestinationAddImage(dest, ctx.makeImage()!, props as CFDictionary)
        guard CGImageDestinationFinalize(dest) else { throw ImageIOCodec.CodecError.encodeFailed("selftest input") }
        return url
    }

    static func run() async throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("selftest-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let service = AppleUpscaleService()
        let verdict = try await service.prepare(model: .fast)
        log("SELFTEST verdict \(verdict.unit.rawValue): \(verdict.reason)")
        if let report = service.report(for: .fast) { log("SELFTEST placement \(report.placement)") }
        let result = try await service.upscale(input: try makeInput(in: dir), model: .fast) { _ in }
        log("SELFTEST upscaled \(result.width)x\(result.height) in \(result.seconds) s on \(result.unit.rawValue)")
        try await service.saveToLibrary(result)
        log("SELFTEST saved")

        let status = PHPhotoLibrary.authorizationStatus(for: .readWrite)
        guard status == .authorized || status == .limited else { log("SELFTEST cannot read back (status \(status.rawValue))"); return }
        let options = PHFetchOptions()
        options.sortDescriptors = [NSSortDescriptor(key: "modificationDate", ascending: false)]
        options.fetchLimit = 1
        guard let asset = PHAsset.fetchAssets(with: .image, options: options).firstObject else { throw PhotoLibrarySaver.SaveError.failed("saved asset not found") }
        log("SELFTEST asset \(asset.pixelWidth)x\(asset.pixelHeight) date \(String(describing: asset.creationDate)) location \(String(describing: asset.location?.coordinate))")
        guard asset.pixelWidth == result.width, asset.pixelHeight == result.height else { throw PhotoLibrarySaver.SaveError.failed("asset size mismatch") }
        guard asset.creationDate == ISO8601DateFormatter().date(from: "2026-11-14T10:30:00+01:00") else { throw PhotoLibrarySaver.SaveError.failed("creation date not kept") }
        guard let loc = asset.location, abs(loc.coordinate.latitude - 47.5596) < 1e-3, abs(loc.coordinate.longitude - 7.5886) < 1e-3 else { throw PhotoLibrarySaver.SaveError.failed("location not kept") }
        let resource = PHAssetResource.assetResources(for: asset).first!
        let data: Data = try await withCheckedThrowingContinuation { continuation in
            let collected = DataBox()
            PHAssetResourceManager.default().requestData(for: resource, options: nil, dataReceivedHandler: { collected.append($0) }, completionHandler: { error in
                if let error { continuation.resume(throwing: error) } else { continuation.resume(returning: collected.data) }
            })
        }
        let src = CGImageSourceCreateWithData(data as CFData, nil)!
        let props = CGImageSourceCopyPropertiesAtIndex(src, 0, nil) as! [String: Any]
        guard props[kCGImagePropertyGPSDictionary as String] != nil, (props[kCGImagePropertyExifDictionary as String] as? [String: Any])?[kCGImagePropertyExifDateTimeOriginal as String] as? String == "2026:11:14 10:30:00" else { throw PhotoLibrarySaver.SaveError.failed("stored original lost EXIF/GPS") }
        log("SELFTEST stored original keeps EXIF + GPS (\(data.count) bytes)")
        let started = BatchBackgroundTask.shared.begin(title: "LocalSR self-test", subtitle: "1 of 1", totalUnits: 1) { log("SELFTEST BGTask expired") }
        log("SELFTEST BGTask begin returned \(started) (id \(BatchBackgroundTask.taskIdentifier))")
        BatchBackgroundTask.shared.update(completedUnits: 1)
        BatchBackgroundTask.shared.end(success: true)
    }
}

private final class DataBox: @unchecked Sendable {
    private let lock = NSLock()
    private var buffer = Data()
    func append(_ d: Data) { lock.withLock { buffer.append(d) } }
    var data: Data { lock.withLock { buffer } }
}
#endif
