#if !SKIP
import XCTest
import Photos
import CoreLocation
import ImageIO
import LocalSRCore
@testable import LocalSR

/// Real PhotoKit save. Only runs in the iOS simulator (its own, disposable photo library) with
/// `TEST_RUNNER_LOCALSR_PHOTOS_TEST=1`, after `xcrun simctl privacy booted grant photos <bundle id>`.
/// It is skipped on the Mac host so the tests never add pictures to the real Photos library.
final class ApplePhotosTests: XCTestCase {
    func testSaveToLibraryKeepsDateLocationAndPixels() async throws {
        #if targetEnvironment(simulator)
        guard ProcessInfo.processInfo.environment["LOCALSR_PHOTOS_TEST"] == "1" else { throw XCTSkip("set LOCALSR_PHOTOS_TEST=1 (simulator only)") }
        let suite = "localsr.tests.photos.\(UUID().uuidString)"
        let dir = Synthetic.tempDirectory("photos")
        defer { try? FileManager.default.removeItem(at: dir); UserDefaults(suiteName: suite)?.removePersistentDomain(forName: suite) }
        let service = AppleUpscaleService(defaults: UserDefaults(suiteName: suite)!, cachesDirectory: dir)
        let input = try Synthetic.writePhoto(.init(width: 420, height: 300), to: dir)
        let result = try await service.upscale(input: input, model: .fast) { _ in }
        print("simulator verdict for fast: \(String(describing: service.verdict(for: .fast)))")
        try await service.saveToLibrary(result)

        let status = PHPhotoLibrary.authorizationStatus(for: .readWrite)
        guard status == .authorized || status == .limited else { throw XCTSkip("read access to the simulator library not granted (\(status.rawValue)); cannot read the asset back") }
        let options = PHFetchOptions()
        options.sortDescriptors = [NSSortDescriptor(key: "modificationDate", ascending: false)]
        options.fetchLimit = 1
        let asset = try XCTUnwrap(PHAsset.fetchAssets(with: .image, options: options).firstObject)
        XCTAssertEqual(asset.pixelWidth, 1680)
        XCTAssertEqual(asset.pixelHeight, 1200)
        XCTAssertEqual(asset.creationDate, ISO8601DateFormatter().date(from: "2026-11-14T10:30:00+01:00"))
        XCTAssertEqual(asset.location?.coordinate.latitude ?? 0, Synthetic.gpsLatitude, accuracy: 1e-3)
        XCTAssertEqual(asset.location?.coordinate.longitude ?? 0, Synthetic.gpsLongitude, accuracy: 1e-3)
        // The stored original still has EXIF/GPS (file-URL variant keeps metadata).
        let resource = try XCTUnwrap(PHAssetResource.assetResources(for: asset).first)
        let data: Data = try await withCheckedThrowingContinuation { continuation in
            var collected = Data()
            PHAssetResourceManager.default().requestData(for: resource, options: nil, dataReceivedHandler: { collected.append($0) }, completionHandler: { error in
                if let error { continuation.resume(throwing: error) } else { continuation.resume(returning: collected) }
            })
        }
        let source = try XCTUnwrap(CGImageSourceCreateWithData(data as CFData, nil))
        let props = try XCTUnwrap(CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [String: Any])
        XCTAssertNotNil(props[kCGImagePropertyGPSDictionary as String])
        let exif = props[kCGImagePropertyExifDictionary as String] as? [String: Any]
        XCTAssertEqual(exif?[kCGImagePropertyExifDateTimeOriginal as String] as? String, "2026:11:14 10:30:00")
        #else
        throw XCTSkip("PhotoKit save is exercised in the iOS simulator only (never on the Mac's real library)")
        #endif
    }

    func testAuthorizationFailureIsReported() async throws {
        // On the Mac host the real library must not be touched: only check the error type is usable.
        let error = PhotoLibrarySaver.SaveError.notAuthorized(.denied)
        XCTAssertTrue(error.description.contains("not granted"))
    }
}
#endif
