#if !SKIP
import Foundation
import ImageIO
import Photos
import CoreLocation

/// Saves a finished output file to the photo library.
///
/// Uses the file-URL variant of `addResource(with:fileURL:options:)`: the `Data` variant lost metadata
/// (EXIF, HDR gain map) on iOS 26.7. Creation date and location are also set explicitly from the file's EXIF
/// because Photos does not always derive them from a HEIC written by ImageIO.
enum PhotoLibrarySaver {
    enum SaveError: Error, CustomStringConvertible {
        case notAuthorized(PHAuthorizationStatus)
        case failed(String)
        var description: String {
            switch self {
            case .notAuthorized(let status): return "Photos access (add only) was not granted (status \(status.rawValue))"
            case .failed(let why): return "Saving to Photos failed: \(why)"
            }
        }
    }

    /// Creation date and location from the EXIF/GPS dictionaries of the file.
    struct SourceMetadata {
        var creationDate: Date?
        var location: CLLocation?
    }

    static func readMetadata(from url: URL) -> SourceMetadata {
        guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
              let props = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [String: Any] else { return SourceMetadata() }
        let exif = props[kCGImagePropertyExifDictionary as String] as? [String: Any] ?? [:]
        let tiff = props[kCGImagePropertyTIFFDictionary as String] as? [String: Any] ?? [:]
        let gps = props[kCGImagePropertyGPSDictionary as String] as? [String: Any] ?? [:]
        return SourceMetadata(creationDate: creationDate(exif: exif, tiff: tiff, gps: gps), location: location(gps: gps, date: nil))
    }

    static func creationDate(exif: [String: Any], tiff: [String: Any], gps: [String: Any]) -> Date? {
        let text = (exif[kCGImagePropertyExifDateTimeOriginal as String] as? String) ?? (exif[kCGImagePropertyExifDateTimeDigitized as String] as? String) ?? (tiff[kCGImagePropertyTIFFDateTime as String] as? String)
        guard let text else { return nil }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy:MM:dd HH:mm:ss"
        // OffsetTimeOriginal ("+01:00") makes the local wall-clock time exact; else the device's zone.
        let offset = (exif["OffsetTimeOriginal"] as? String) ?? (exif["OffsetTime"] as? String)
        if let offset, let seconds = secondsFromOffset(offset) { formatter.timeZone = TimeZone(secondsFromGMT: seconds) }
        else { formatter.timeZone = TimeZone.current }
        return formatter.date(from: text)
    }

    static func secondsFromOffset(_ text: String) -> Int? {
        let t = text.trimmingCharacters(in: .whitespaces)
        if t == "Z" { return 0 }
        guard t.count >= 5, let sign = t.first, sign == "+" || sign == "-" else { return nil }
        let parts = t.dropFirst().split(separator: ":")
        guard parts.count == 2, let h = Int(parts[0]), let m = Int(parts[1]) else { return nil }
        return (sign == "-" ? -1 : 1) * (h * 3600 + m * 60)
    }

    static func location(gps: [String: Any], date: Date?) -> CLLocation? {
        guard let lat = (gps[kCGImagePropertyGPSLatitude as String] as? NSNumber)?.doubleValue,
              let lon = (gps[kCGImagePropertyGPSLongitude as String] as? NSNumber)?.doubleValue else { return nil }
        let latRef = (gps[kCGImagePropertyGPSLatitudeRef as String] as? String) ?? "N"
        let lonRef = (gps[kCGImagePropertyGPSLongitudeRef as String] as? String) ?? "E"
        let latitude = latRef.uppercased() == "S" ? -abs(lat) : abs(lat)
        let longitude = lonRef.uppercased() == "W" ? -abs(lon) : abs(lon)
        guard CLLocationCoordinate2DIsValid(CLLocationCoordinate2D(latitude: latitude, longitude: longitude)) else { return nil }
        var altitude = 0.0
        var verticalAccuracy = -1.0
        if let alt = (gps[kCGImagePropertyGPSAltitude as String] as? NSNumber)?.doubleValue {
            let below = (gps[kCGImagePropertyGPSAltitudeRef as String] as? NSNumber)?.intValue == 1
            altitude = below ? -abs(alt) : abs(alt)
            verticalAccuracy = 0
        }
        return CLLocation(coordinate: CLLocationCoordinate2D(latitude: latitude, longitude: longitude), altitude: altitude, horizontalAccuracy: 0, verticalAccuracy: verticalAccuracy, timestamp: date ?? Date())
    }

    static func save(fileURL: URL, originalFilename: String) async throws {
        let status = await PHPhotoLibrary.requestAuthorization(for: .addOnly)
        guard status == .authorized || status == .limited else { throw SaveError.notAuthorized(status) }
        let metadata = readMetadata(from: fileURL)
        let creationDate = metadata.creationDate
        let location = metadata.location
        do {
            try await PHPhotoLibrary.shared().performChanges {
                let request = PHAssetCreationRequest.forAsset()
                let options = PHAssetResourceCreationOptions()
                options.shouldMoveFile = false
                options.originalFilename = originalFilename
                request.addResource(with: .photo, fileURL: fileURL, options: options)
                if let creationDate { request.creationDate = creationDate }
                if let location { request.location = location }
            }
        } catch {
            throw SaveError.failed("\(error)")
        }
    }
}
#endif
