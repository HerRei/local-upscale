// localsr-media: LocalSR's bridge to the codecs that ship with macOS.
//
// LocalSR's own media runtime deliberately contains no H.264, HEVC or AAC code.
// macOS does, with Apple's licences, behind AVFoundation. This small program
// reads such files with AVAssetReader and writes them with AVAssetWriter, and
// exchanges raw frames with the LocalSR worker over pipes, so no codec code is
// ever loaded into LocalSR itself.
//
//   localsr-media version
//   localsr-media probe <file>
//   localsr-media decode <file> [--audio out.wav] [--max-frames N] [--no-video]
//   localsr-media encode --output out.mp4 --codec h264|hevc [--quality 0..1]
//                        [--bitrate N] [--hdr HLG|PQ] [--audio in.wav]
//                        [--aac-bitrate N] [--rotation DEG] < frames
//
// Frame stream (both directions): one JSON header line, then for each frame a
// 16-byte little-endian record (pts int64, duration int32, payload uint32) and
// the payload: tightly packed nv12 (8-bit) or p010le (10-bit) planes.

import AVFoundation
import CoreMedia
import CoreVideo
import Foundation
import VideoToolbox

let helperVersion = 1
let streamTag = "localsr-media/1"

// MARK: - Process helpers

struct HelperError: Error, CustomStringConvertible {
    let description: String
    init(_ description: String) { self.description = description }
}

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(1)
}

func writeAll(_ descriptor: Int32, _ pointer: UnsafeRawPointer, _ count: Int) throws {
    var written = 0
    while written < count {
        let result = write(descriptor, pointer.advanced(by: written), count - written)
        if result < 0 {
            if errno == EINTR { continue }
            throw HelperError("write failed: \(String(cString: strerror(errno)))")
        }
        written += result
    }
}

func writeAll(_ descriptor: Int32, _ data: Data) throws {
    try data.withUnsafeBytes { buffer in
        if let base = buffer.baseAddress { try writeAll(descriptor, base, buffer.count) }
    }
}

/// Read exactly `count` bytes, or fewer only at end of input.
func readExactly(_ descriptor: Int32, into pointer: UnsafeMutableRawPointer, count: Int) throws -> Int {
    var total = 0
    while total < count {
        let result = read(descriptor, pointer.advanced(by: total), count - total)
        if result < 0 {
            if errno == EINTR { continue }
            throw HelperError("read failed: \(String(cString: strerror(errno)))")
        }
        if result == 0 { break }
        total += result
    }
    return total
}

func readLine(_ descriptor: Int32) throws -> String {
    var bytes: [UInt8] = []
    var byte: UInt8 = 0
    while true {
        let result = try readExactly(descriptor, into: &byte, count: 1)
        if result == 0 || byte == 0x0A { break }
        bytes.append(byte)
        if bytes.count > 65536 { throw HelperError("stream header is too long") }
    }
    guard let line = String(bytes: bytes, encoding: .utf8) else {
        throw HelperError("stream header is not UTF-8")
    }
    return line
}

func jsonData(_ object: Any) throws -> Data {
    try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
}

func orNull(_ value: Any?) -> Any { value ?? NSNull() }

func printJSON(_ object: Any) throws {
    var data = try jsonData(object)
    data.append(0x0A)
    try writeAll(STDOUT_FILENO, data)
}

/// Run an async AVFoundation call from this synchronous command-line tool.
func awaitResult<T>(_ body: @escaping () async throws -> T) throws -> T {
    let semaphore = DispatchSemaphore(value: 0)
    var outcome: Result<T, Error>?
    Task {
        do { outcome = .success(try await body()) } catch { outcome = .failure(error) }
        semaphore.signal()
    }
    semaphore.wait()
    return try outcome!.get()
}

struct Arguments {
    var positional: [String] = []
    var options: [String: String] = [:]
    var flags: Set<String> = []

    init(_ raw: ArraySlice<String>) throws {
        var iterator = raw.makeIterator()
        while let item = iterator.next() {
            guard item.hasPrefix("--") else {
                positional.append(item)
                continue
            }
            let name = String(item.dropFirst(2))
            if ["no-video", "no-audio"].contains(name) {
                flags.insert(name)
            } else if let value = iterator.next() {
                options[name] = value
            } else {
                throw HelperError("missing value for --\(name)")
            }
        }
    }

    func int(_ name: String, default value: Int) throws -> Int {
        guard let text = options[name] else { return value }
        guard let number = Int(text) else { throw HelperError("--\(name) expects an integer") }
        return number
    }

    func double(_ name: String, default value: Double) throws -> Double {
        guard let text = options[name] else { return value }
        guard let number = Double(text) else { throw HelperError("--\(name) expects a number") }
        return number
    }
}

// MARK: - Colour and format descriptions

/// FFmpeg's names for the colour tags CoreMedia uses, so LocalSR can tag its
/// lossless intermediate exactly like the source.
let primariesNames: [String: String] = [
    kCMFormatDescriptionColorPrimaries_ITU_R_709_2 as String: "bt709",
    kCMFormatDescriptionColorPrimaries_ITU_R_2020 as String: "bt2020",
    kCMFormatDescriptionColorPrimaries_EBU_3213 as String: "bt470bg",
    kCMFormatDescriptionColorPrimaries_SMPTE_C as String: "smpte170m",
    kCMFormatDescriptionColorPrimaries_P3_D65 as String: "smpte432",
    kCMFormatDescriptionColorPrimaries_DCI_P3 as String: "smpte431",
]
let transferNames: [String: String] = [
    kCMFormatDescriptionTransferFunction_ITU_R_709_2 as String: "bt709",
    kCMFormatDescriptionTransferFunction_ITU_R_2020 as String: "bt2020-10",
    kCMFormatDescriptionTransferFunction_ITU_R_2100_HLG as String: "arib-std-b67",
    kCMFormatDescriptionTransferFunction_SMPTE_ST_2084_PQ as String: "smpte2084",
    kCMFormatDescriptionTransferFunction_SMPTE_240M_1995 as String: "smpte240m",
    kCMFormatDescriptionTransferFunction_sRGB as String: "iec61966-2-1",
    kCMFormatDescriptionTransferFunction_Linear as String: "linear",
    kCMFormatDescriptionTransferFunction_SMPTE_ST_428_1 as String: "smpte428",
]
let matrixNames: [String: String] = [
    kCMFormatDescriptionYCbCrMatrix_ITU_R_709_2 as String: "bt709",
    kCMFormatDescriptionYCbCrMatrix_ITU_R_2020 as String: "bt2020nc",
    kCMFormatDescriptionYCbCrMatrix_ITU_R_601_4 as String: "smpte170m",
    kCMFormatDescriptionYCbCrMatrix_SMPTE_240M_1995 as String: "smpte240m",
]

/// H.273 code points, for tags CoreMedia only knows by number ("YCbCrMatrix#5").
let primariesCodes = [1: "bt709", 4: "bt470m", 5: "bt470bg", 6: "smpte170m", 7: "smpte240m", 8: "film", 9: "bt2020", 10: "smpte428", 11: "smpte431", 12: "smpte432"]
let transferCodes = [1: "bt709", 4: "gamma22", 5: "gamma28", 6: "smpte170m", 7: "smpte240m", 8: "linear", 11: "iec61966-2-4", 13: "iec61966-2-1", 14: "bt2020-10", 15: "bt2020-12", 16: "smpte2084", 17: "smpte428", 18: "arib-std-b67"]
let matrixCodes = [0: "rgb", 1: "bt709", 4: "fcc", 5: "bt470bg", 6: "smpte170m", 7: "smpte240m", 8: "ycgco", 9: "bt2020nc", 10: "bt2020c"]

func lookup(_ table: [String: String], codes: [Int: String], _ value: CFPropertyList?) -> String {
    guard let text = value as? String else { return "" }
    if let name = table[text] { return name }
    if let hash = text.lastIndex(of: "#"), let code = Int(text[text.index(after: hash)...]) {
        return codes[code] ?? ""
    }
    return ""
}

func reverse(_ table: [String: String], _ name: String) -> String? {
    table.first { $0.value == name }?.key
}

struct ColorTags {
    var primaries = ""
    var transfer = ""
    var matrix = ""
    var fullRange = false

    var json: [String: Any] {
        ["primaries": primaries, "transfer": transfer, "matrix": matrix]
    }

    var hdr: String {
        switch transfer {
        case "arib-std-b67": return "HLG"
        case "smpte2084": return "PQ"
        default: return ""
        }
    }
}

func colorTags(from description: CMFormatDescription) -> ColorTags {
    var tags = ColorTags()
    tags.primaries = lookup(
        primariesNames, codes: primariesCodes,
        CMFormatDescriptionGetExtension(description, extensionKey: kCMFormatDescriptionExtension_ColorPrimaries))
    tags.transfer = lookup(
        transferNames, codes: transferCodes,
        CMFormatDescriptionGetExtension(description, extensionKey: kCMFormatDescriptionExtension_TransferFunction))
    tags.matrix = lookup(
        matrixNames, codes: matrixCodes,
        CMFormatDescriptionGetExtension(description, extensionKey: kCMFormatDescriptionExtension_YCbCrMatrix))
    if let full = CMFormatDescriptionGetExtension(description, extensionKey: kCMFormatDescriptionExtension_FullRangeVideo) as? Bool {
        tags.fullRange = full
    }
    return tags
}

func fourCC(_ code: FourCharCode) -> String {
    let bytes = [
        UInt8((code >> 24) & 0xFF), UInt8((code >> 16) & 0xFF), UInt8((code >> 8) & 0xFF), UInt8(code & 0xFF),
    ]
    return String(bytes: bytes, encoding: .ascii)?.trimmingCharacters(in: .whitespaces) ?? "????"
}

/// Luma bit depth from an HEVC decoder configuration record, 8 when unknown.
func hevcBitDepth(_ description: CMFormatDescription) -> Int {
    guard
        let atoms = CMFormatDescriptionGetExtension(
            description, extensionKey: kCMFormatDescriptionExtension_SampleDescriptionExtensionAtoms) as? [String: Any],
        let record = atoms["hvcC"] as? Data, record.count > 18
    else { return 8 }
    return Int(record[17] & 0x07) + 8
}

func rotationDegrees(_ transform: CGAffineTransform) -> (degrees: Int, mirrored: Bool) {
    let determinant = transform.a * transform.d - transform.b * transform.c
    let angle = atan2(transform.b, transform.a) * 180 / .pi
    var degrees = Int((angle + 360).rounded()) % 360
    if determinant < 0 {
        // A reflection: describe it as a rotation plus a horizontal mirror.
        degrees = Int((atan2(-transform.b, -transform.a) * 180 / .pi + 540).rounded()) % 360
        return (degrees, true)
    }
    return (degrees, false)
}

// MARK: - Asset inspection

struct VideoTrackInfo {
    let track: AVAssetTrack
    let description: CMFormatDescription
    let codec: String
    let width: Int
    let height: Int
    let color: ColorTags
    let bitDepth: Int
    let rotation: Int
    let mirrored: Bool
    let transform: CGAffineTransform
    let nominalFrameRate: Double
    let timescale: Int32
    let duration: Double
    let decodable: Bool

    var tenBit: Bool { bitDepth > 8 || !color.hdr.isEmpty }

    var json: [String: Any] {
        [
            "codec": codec, "width": width, "height": height, "bit_depth": bitDepth,
            "color": color.json, "full_range": color.fullRange, "hdr": color.hdr,
            "rotation": rotation, "mirrored": mirrored, "nominal_fps": nominalFrameRate,
            "transform": [transform.a, transform.b, transform.c, transform.d],
            "timescale": Int(timescale), "duration": duration, "decodable": decodable,
            "frames": nominalFrameRate > 0 ? Int((duration * nominalFrameRate).rounded()) : 0,
        ]
    }
}

struct AudioTrackInfo {
    let track: AVAssetTrack
    let codec: String
    let sampleRate: Double
    let channels: Int
    let decodable: Bool

    var json: [String: Any] {
        ["codec": codec, "sample_rate": sampleRate, "channels": channels, "decodable": decodable]
    }
}

struct AssetInfo {
    let asset: AVURLAsset
    let readable: Bool
    let duration: Double
    let video: VideoTrackInfo?
    let audio: AudioTrackInfo?
    let subtitleTracks: Int
}

func inspect(_ path: String) throws -> AssetInfo {
    let url = URL(fileURLWithPath: path)
    guard FileManager.default.isReadableFile(atPath: path) else { throw HelperError("cannot read \(path)") }
    let asset = AVURLAsset(url: url, options: [AVURLAssetPreferPreciseDurationAndTimingKey: true])
    let (readable, duration, tracks) = try awaitResult { try await asset.load(.isReadable, .duration, .tracks) }
    guard readable else {
        return AssetInfo(asset: asset, readable: false, duration: 0, video: nil, audio: nil, subtitleTracks: 0)
    }
    var video: VideoTrackInfo?
    if let track = tracks.first(where: { $0.mediaType == .video }) {
        let (descriptions, size, transform, frameRate, timescale, range, decodable) = try awaitResult {
            try await track.load(
                .formatDescriptions, .naturalSize, .preferredTransform, .nominalFrameRate, .naturalTimeScale,
                .timeRange, .isDecodable)
        }
        if let description = descriptions.first {
            let dimensions = CMVideoFormatDescriptionGetDimensions(description)
            let codec = fourCC(CMFormatDescriptionGetMediaSubType(description))
            let color = colorTags(from: description)
            var depth = 8
            if ["hvc1", "hev1"].contains(codec) {
                depth = hevcBitDepth(description)
            } else if codec.hasPrefix("ap") || codec == "icod" {
                depth = 10
            }
            let (degrees, mirrored) = rotationDegrees(transform)
            video = VideoTrackInfo(
                track: track, description: description, codec: codec,
                width: Int(dimensions.width > 0 ? dimensions.width : Int32(size.width)),
                height: Int(dimensions.height > 0 ? dimensions.height : Int32(size.height)),
                color: color, bitDepth: depth, rotation: degrees, mirrored: mirrored, transform: transform,
                nominalFrameRate: Double(frameRate), timescale: timescale,
                duration: CMTIME_IS_NUMERIC(range.duration) ? CMTimeGetSeconds(range.duration) : 0,
                decodable: decodable)
        }
    }
    var audio: AudioTrackInfo?
    if let track = tracks.first(where: { $0.mediaType == .audio }) {
        let (descriptions, decodable) = try awaitResult { try await track.load(.formatDescriptions, .isDecodable) }
        if let description = descriptions.first {
            let basic = CMAudioFormatDescriptionGetStreamBasicDescription(description)?.pointee
            let codec = fourCC(CMFormatDescriptionGetMediaSubType(description))
            audio = AudioTrackInfo(
                track: track, codec: codec == "aac" ? "aac" : codec,
                sampleRate: basic?.mSampleRate ?? 0, channels: Int(basic?.mChannelsPerFrame ?? 0),
                decodable: decodable)
        }
    }
    let subtitles = tracks.filter { $0.mediaType == .subtitle || $0.mediaType == .closedCaption }.count
    return AssetInfo(
        asset: asset, readable: true, duration: CMTIME_IS_NUMERIC(duration) ? CMTimeGetSeconds(duration) : 0,
        video: video, audio: audio, subtitleTracks: subtitles)
}

func probe(_ arguments: Arguments) throws {
    guard let path = arguments.positional.first else { throw HelperError("probe needs a file") }
    let info = try inspect(path)
    var result: [String: Any] = ["readable": info.readable, "duration": info.duration, "subtitle_tracks": info.subtitleTracks]
    result["video"] = orNull(info.video?.json)
    result["audio"] = orNull(info.audio?.json)
    try printJSON(result)
}

// MARK: - Pixel formats

struct PixelLayout {
    let name: String
    let fourCC: OSType
    let bytesPerSample: Int

    static func forStream(_ name: String, fullRange: Bool) throws -> PixelLayout {
        switch name {
        case "nv12":
            return PixelLayout(
                name: name,
                fourCC: fullRange ? kCVPixelFormatType_420YpCbCr8BiPlanarFullRange : kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange,
                bytesPerSample: 1)
        case "p010le":
            return PixelLayout(
                name: name,
                fourCC: fullRange ? kCVPixelFormatType_420YpCbCr10BiPlanarFullRange : kCVPixelFormatType_420YpCbCr10BiPlanarVideoRange,
                bytesPerSample: 2)
        default:
            throw HelperError("unsupported frame format \(name); use nv12 or p010le")
        }
    }

    /// Tight row lengths and row counts of the luma and chroma planes.
    func planes(width: Int, height: Int) -> [(rowBytes: Int, rows: Int)] {
        [
            (width * bytesPerSample, height),
            (((width + 1) / 2) * 2 * bytesPerSample, (height + 1) / 2),
        ]
    }

    func payloadSize(width: Int, height: Int) -> Int {
        planes(width: width, height: height).reduce(0) { $0 + $1.rowBytes * $1.rows }
    }
}

struct FrameHeader {
    var pts: Int64
    var duration: Int32
    var length: UInt32

    static let size = 16

    var data: Data {
        var buffer = Data(count: FrameHeader.size)
        buffer.withUnsafeMutableBytes { raw in
            raw.storeBytes(of: pts.littleEndian, toByteOffset: 0, as: Int64.self)
            raw.storeBytes(of: duration.littleEndian, toByteOffset: 8, as: Int32.self)
            raw.storeBytes(of: length.littleEndian, toByteOffset: 12, as: UInt32.self)
        }
        return buffer
    }

    static func read(_ descriptor: Int32) throws -> FrameHeader? {
        var bytes = [UInt8](repeating: 0, count: size)
        let count = try bytes.withUnsafeMutableBytes { try readExactly(descriptor, into: $0.baseAddress!, count: size) }
        if count == 0 { return nil }
        guard count == size else { throw HelperError("truncated frame header") }
        return bytes.withUnsafeBytes { raw in
            FrameHeader(
                pts: Int64(littleEndian: raw.load(fromByteOffset: 0, as: Int64.self)),
                duration: Int32(littleEndian: raw.load(fromByteOffset: 8, as: Int32.self)),
                length: UInt32(littleEndian: raw.load(fromByteOffset: 12, as: UInt32.self)))
        }
    }
}

// MARK: - WAV files

struct WavWriter {
    let handle: FileHandle
    let sampleRate: Int
    let channels: Int
    let bitsPerSample: Int
    var dataBytes = 0

    init(path: String, sampleRate: Int, channels: Int, bitsPerSample: Int) throws {
        FileManager.default.createFile(atPath: path, contents: nil)
        guard let handle = FileHandle(forWritingAtPath: path) else { throw HelperError("cannot create \(path)") }
        self.handle = handle
        self.sampleRate = sampleRate
        self.channels = channels
        self.bitsPerSample = bitsPerSample
        try handle.write(contentsOf: header(dataBytes: 0))
    }

    func header(dataBytes: Int) -> Data {
        var data = Data()
        func append32(_ value: UInt32) { withUnsafeBytes(of: value.littleEndian) { data.append(contentsOf: $0) } }
        func append16(_ value: UInt16) { withUnsafeBytes(of: value.littleEndian) { data.append(contentsOf: $0) } }
        let blockAlign = channels * bitsPerSample / 8
        data.append(contentsOf: Array("RIFF".utf8))
        append32(UInt32(36 + dataBytes))
        data.append(contentsOf: Array("WAVEfmt ".utf8))
        append32(16)
        append16(1)
        append16(UInt16(channels))
        append32(UInt32(sampleRate))
        append32(UInt32(sampleRate * blockAlign))
        append16(UInt16(blockAlign))
        append16(UInt16(bitsPerSample))
        data.append(contentsOf: Array("data".utf8))
        append32(UInt32(dataBytes))
        return data
    }

    mutating func write(_ data: Data) throws {
        try handle.write(contentsOf: data)
        dataBytes += data.count
    }

    func finish() throws {
        try handle.seek(toOffset: 0)
        try handle.write(contentsOf: header(dataBytes: dataBytes))
        try handle.close()
    }
}

let pcmOutputSettings: [String: Any] = [
    AVFormatIDKey: kAudioFormatLinearPCM,
    AVLinearPCMBitDepthKey: 16,
    AVLinearPCMIsFloatKey: false,
    AVLinearPCMIsBigEndianKey: false,
    AVLinearPCMIsNonInterleaved: false,
]

/// Decode the first audio track to 16-bit PCM in a WAV file; nil when there is none.
func decodeAudio(_ info: AssetInfo, to path: String) throws -> [String: Any]? {
    guard let audio = info.audio, audio.decodable else { return nil }
    let reader = try AVAssetReader(asset: info.asset)
    var settings = pcmOutputSettings
    if audio.channels > 2 { settings[AVNumberOfChannelsKey] = 2 }
    let output = AVAssetReaderTrackOutput(track: audio.track, outputSettings: settings)
    output.alwaysCopiesSampleData = false
    guard reader.canAdd(output) else { throw HelperError("cannot read the audio track") }
    reader.add(output)
    guard reader.startReading() else {
        throw HelperError("audio decode failed: \(reader.error?.localizedDescription ?? "unknown error")")
    }
    var writer: WavWriter?
    var sampleRate = 0
    var channels = 0
    var start = 0.0
    while let sample = output.copyNextSampleBuffer() {
        if writer == nil {
            let first = CMSampleBufferGetPresentationTimeStamp(sample)
            start = CMTIME_IS_NUMERIC(first) ? CMTimeGetSeconds(first) : 0
            guard let description = CMSampleBufferGetFormatDescription(sample),
                let basic = CMAudioFormatDescriptionGetStreamBasicDescription(description)?.pointee
            else { throw HelperError("audio decode produced no format description") }
            sampleRate = Int(basic.mSampleRate)
            channels = Int(basic.mChannelsPerFrame)
            writer = try WavWriter(path: path, sampleRate: sampleRate, channels: channels, bitsPerSample: 16)
        }
        guard let block = CMSampleBufferGetDataBuffer(sample) else { continue }
        let length = CMBlockBufferGetDataLength(block)
        var bytes = Data(count: length)
        try bytes.withUnsafeMutableBytes { raw in
            let status = CMBlockBufferCopyDataBytes(block, atOffset: 0, dataLength: length, destination: raw.baseAddress!)
            if status != kCMBlockBufferNoErr { throw HelperError("audio sample copy failed (\(status))") }
        }
        try writer!.write(bytes)
    }
    if reader.status == .failed {
        throw HelperError("audio decode failed: \(reader.error?.localizedDescription ?? "unknown error")")
    }
    guard var finished = writer else { return nil }
    try finished.finish()
    return [
        "path": path, "sample_rate": sampleRate, "channels": channels, "bits": 16, "bytes": finished.dataBytes,
        "start": start,
    ]
}

// MARK: - decode

func decode(_ arguments: Arguments) throws {
    guard let path = arguments.positional.first else { throw HelperError("decode needs a file") }
    let info = try inspect(path)
    guard info.readable else { throw HelperError("macOS cannot read \(path)") }
    let maxFrames = try arguments.int("max-frames", default: 0)
    var audioInfo: [String: Any]?
    if let wav = arguments.options["audio"], !arguments.flags.contains("no-audio") {
        audioInfo = try decodeAudio(info, to: wav)
    }
    guard !arguments.flags.contains("no-video") else {
        try printJSON(["stream": streamTag, "video": false, "audio": orNull(audioInfo)])
        return
    }
    guard let video = info.video else { throw HelperError("no video track in \(path)") }
    guard video.decodable else { throw HelperError("macOS has no decoder for the \(video.codec) video in \(path)") }
    let formatName = arguments.options["format"] ?? (video.tenBit ? "p010le" : "nv12")
    let layout = try PixelLayout.forStream(formatName, fullRange: video.color.fullRange)

    let reader = try AVAssetReader(asset: info.asset)
    let output = AVAssetReaderTrackOutput(
        track: video.track, outputSettings: [kCVPixelBufferPixelFormatTypeKey as String: layout.fourCC])
    output.alwaysCopiesSampleData = false
    guard reader.canAdd(output) else { throw HelperError("cannot read the video track") }
    reader.add(output)
    guard reader.startReading() else {
        throw HelperError("video decode failed: \(reader.error?.localizedDescription ?? "unknown error")")
    }

    let header: [String: Any] = [
        "stream": streamTag, "video": true, "width": video.width, "height": video.height,
        "format": layout.name, "timescale": Int(video.timescale), "full_range": video.color.fullRange,
        "color": video.color.json, "hdr": video.color.hdr, "rotation": video.rotation, "mirrored": video.mirrored,
        "transform": [video.transform.a, video.transform.b, video.transform.c, video.transform.d],
        "nominal_fps": video.nominalFrameRate, "frames": video.json["frames"] ?? 0, "codec": video.codec,
        "audio": orNull(audioInfo),
    ]
    try printJSON(header)

    var payload = Data(count: layout.payloadSize(width: video.width, height: video.height))
    var frames = 0
    while let sample = output.copyNextSampleBuffer() {
        guard let pixels = CMSampleBufferGetImageBuffer(sample) else { continue }
        let pts = CMTimeConvertScale(
            CMSampleBufferGetPresentationTimeStamp(sample), timescale: video.timescale, method: .roundHalfAwayFromZero)
        let durationTime = CMSampleBufferGetDuration(sample)
        let duration =
            CMTIME_IS_NUMERIC(durationTime)
            ? CMTimeConvertScale(durationTime, timescale: video.timescale, method: .roundHalfAwayFromZero).value : 0
        let width = CVPixelBufferGetWidth(pixels)
        let height = CVPixelBufferGetHeight(pixels)
        guard width == video.width, height == video.height else {
            throw HelperError("frame size changed to \(width)x\(height); resolution changes are not supported")
        }
        CVPixelBufferLockBaseAddress(pixels, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(pixels, .readOnly) }
        var offset = 0
        for (index, plane) in layout.planes(width: width, height: height).enumerated() {
            guard let base = CVPixelBufferGetBaseAddressOfPlane(pixels, index) else {
                throw HelperError("decoded frame has no plane \(index)")
            }
            let stride = CVPixelBufferGetBytesPerRowOfPlane(pixels, index)
            payload.withUnsafeMutableBytes { raw in
                let destination = raw.baseAddress!.advanced(by: offset)
                if stride == plane.rowBytes {
                    memcpy(destination, base, plane.rowBytes * plane.rows)
                } else {
                    for row in 0..<plane.rows {
                        memcpy(destination.advanced(by: row * plane.rowBytes), base.advanced(by: row * stride), plane.rowBytes)
                    }
                }
            }
            offset += plane.rowBytes * plane.rows
        }
        try writeAll(STDOUT_FILENO, FrameHeader(pts: pts.value, duration: Int32(clamping: duration), length: UInt32(payload.count)).data)
        try writeAll(STDOUT_FILENO, payload)
        frames += 1
        if maxFrames > 0 && frames >= maxFrames {
            reader.cancelReading()
            break
        }
    }
    if reader.status == .failed {
        throw HelperError("video decode failed: \(reader.error?.localizedDescription ?? "unknown error")")
    }
}

// MARK: - encode

struct EncodeHeader {
    let width: Int
    let height: Int
    let layout: PixelLayout
    let timescale: Int32
    let color: ColorTags
    let fps: Double

    init(json: [String: Any]) throws {
        guard json["stream"] as? String == streamTag else { throw HelperError("unexpected stream header") }
        guard let width = json["width"] as? Int, let height = json["height"] as? Int, width > 0, height > 0 else {
            throw HelperError("stream header needs width and height")
        }
        self.width = width
        self.height = height
        var tags = ColorTags()
        if let color = json["color"] as? [String: Any] {
            tags.primaries = color["primaries"] as? String ?? ""
            tags.transfer = color["transfer"] as? String ?? ""
            tags.matrix = color["matrix"] as? String ?? ""
        }
        tags.fullRange = json["full_range"] as? Bool ?? false
        self.color = tags
        self.layout = try PixelLayout.forStream(json["format"] as? String ?? "nv12", fullRange: tags.fullRange)
        self.timescale = Int32(json["timescale"] as? Int ?? 90000)
        self.fps = json["fps"] as? Double ?? 0
    }
}

func colorProperties(_ tags: ColorTags, hdr: String) -> [String: Any]? {
    if hdr == "HLG" || hdr == "PQ" {
        return [
            AVVideoColorPrimariesKey: AVVideoColorPrimaries_ITU_R_2020,
            AVVideoTransferFunctionKey: hdr == "HLG" ? AVVideoTransferFunction_ITU_R_2100_HLG : AVVideoTransferFunction_SMPTE_ST_2084_PQ,
            AVVideoYCbCrMatrixKey: AVVideoYCbCrMatrix_ITU_R_2020,
        ]
    }
    guard let primaries = reverse(primariesNames, tags.primaries), let transfer = reverse(transferNames, tags.transfer),
        let matrix = reverse(matrixNames, tags.matrix)
    else { return nil }
    return [AVVideoColorPrimariesKey: primaries, AVVideoTransferFunctionKey: transfer, AVVideoYCbCrMatrixKey: matrix]
}

func attachColor(_ buffer: CVPixelBuffer, _ properties: [String: Any]?) {
    guard let properties else { return }
    CVBufferSetAttachment(buffer, kCVImageBufferColorPrimariesKey, properties[AVVideoColorPrimariesKey] as CFTypeRef, .shouldPropagate)
    CVBufferSetAttachment(buffer, kCVImageBufferTransferFunctionKey, properties[AVVideoTransferFunctionKey] as CFTypeRef, .shouldPropagate)
    CVBufferSetAttachment(buffer, kCVImageBufferYCbCrMatrixKey, properties[AVVideoYCbCrMatrixKey] as CFTypeRef, .shouldPropagate)
}

/// Frames from stdin into the video input; runs inside the writer's ready callback.
final class VideoFeed {
    let header: EncodeHeader
    let input: AVAssetWriterInput
    let adaptor: AVAssetWriterInputPixelBufferAdaptor
    let colors: [String: Any]?
    let codec: String
    let planes: [(rowBytes: Int, rows: Int)]
    let expected: Int
    var payload: Data
    var pending: FrameHeader?
    var frames = 0
    var lastEnd = CMTime.zero
    var error: HelperError?
    var finished = false

    init(header: EncodeHeader, input: AVAssetWriterInput, adaptor: AVAssetWriterInputPixelBufferAdaptor,
         colors: [String: Any]?, codec: String, first: FrameHeader) {
        self.header = header
        self.input = input
        self.adaptor = adaptor
        self.colors = colors
        self.codec = codec
        planes = header.layout.planes(width: header.width, height: header.height)
        expected = header.layout.payloadSize(width: header.width, height: header.height)
        payload = Data(count: expected)
        pending = first
    }

    /// Append frames while the input wants them. Returns true once the feed is over.
    func pump(writer: AVAssetWriter) -> Bool {
        if finished { return false }
        while input.isReadyForMoreMediaData {
            do {
                guard let frame = try pending ?? FrameHeader.read(STDIN_FILENO) else { return finish() }
                pending = nil
                try append(frame, writer: writer)
            } catch {
                self.error = error as? HelperError ?? HelperError("\(error)")
                return finish()
            }
        }
        return false
    }

    private func finish() -> Bool {
        finished = true
        input.markAsFinished()
        return true
    }

    private func append(_ frame: FrameHeader, writer: AVAssetWriter) throws {
        guard Int(frame.length) == expected else {
            throw HelperError("frame \(frames) has \(frame.length) bytes, expected \(expected)")
        }
        let got = try payload.withUnsafeMutableBytes { try readExactly(STDIN_FILENO, into: $0.baseAddress!, count: expected) }
        guard got == expected else { throw HelperError("truncated frame \(frames)") }
        guard let pool = adaptor.pixelBufferPool else { throw HelperError("no pixel buffer pool") }
        var buffer: CVPixelBuffer?
        guard CVPixelBufferPoolCreatePixelBuffer(nil, pool, &buffer) == kCVReturnSuccess, let pixels = buffer else {
            throw HelperError("cannot allocate a pixel buffer")
        }
        CVPixelBufferLockBaseAddress(pixels, [])
        var offset = 0
        for (index, plane) in planes.enumerated() {
            guard let base = CVPixelBufferGetBaseAddressOfPlane(pixels, index) else {
                CVPixelBufferUnlockBaseAddress(pixels, [])
                throw HelperError("pixel buffer has no plane \(index)")
            }
            let stride = CVPixelBufferGetBytesPerRowOfPlane(pixels, index)
            payload.withUnsafeBytes { raw in
                let source = raw.baseAddress!.advanced(by: offset)
                if stride == plane.rowBytes {
                    memcpy(base, source, plane.rowBytes * plane.rows)
                } else {
                    for row in 0..<plane.rows {
                        memcpy(base.advanced(by: row * stride), source.advanced(by: row * plane.rowBytes), plane.rowBytes)
                    }
                }
            }
            offset += plane.rowBytes * plane.rows
        }
        CVPixelBufferUnlockBaseAddress(pixels, [])
        attachColor(pixels, colors)
        let pts = CMTime(value: frame.pts, timescale: header.timescale)
        guard adaptor.append(pixels, withPresentationTime: pts) else {
            throw HelperError("\(codec.uppercased()) encode failed: \(writer.error?.localizedDescription ?? "unknown error")")
        }
        let duration = frame.duration > 0 ? CMTime(value: Int64(frame.duration), timescale: header.timescale)
            : (header.fps > 0 ? CMTime(seconds: 1 / header.fps, preferredTimescale: header.timescale) : .zero)
        lastEnd = CMTimeAdd(pts, duration)
        frames += 1
    }
}

/// PCM from a WAV file into the AAC input; runs inside the writer's ready callback.
final class AudioFeeder {
    let reader: AVAssetReader
    let output: AVAssetReaderTrackOutput
    let input: AVAssetWriterInput
    var fedUntil = CMTime.zero
    var error: HelperError?
    var finished = false

    init(wav: String, writer: AVAssetWriter, aacBitRate: Int) throws {
        let asset = AVURLAsset(url: URL(fileURLWithPath: wav))
        let tracks = try awaitResult { try await asset.loadTracks(withMediaType: .audio) }
        guard let track = tracks.first else { throw HelperError("no audio in \(wav)") }
        let descriptions = try awaitResult { try await track.load(.formatDescriptions) }
        guard let description = descriptions.first,
            let basic = CMAudioFormatDescriptionGetStreamBasicDescription(description)?.pointee
        else { throw HelperError("cannot read the audio format of \(wav)") }
        let channels = min(2, Int(basic.mChannelsPerFrame))
        reader = try AVAssetReader(asset: asset)
        var settings = pcmOutputSettings
        settings[AVNumberOfChannelsKey] = channels
        output = AVAssetReaderTrackOutput(track: track, outputSettings: settings)
        reader.add(output)
        var layout = AudioChannelLayout()
        layout.mChannelLayoutTag = channels == 1 ? kAudioChannelLayoutTag_Mono : kAudioChannelLayoutTag_Stereo
        let layoutData = Data(bytes: &layout, count: MemoryLayout<AudioChannelLayout>.size)
        let aac: [String: Any] = [
            AVFormatIDKey: kAudioFormatMPEG4AAC, AVSampleRateKey: basic.mSampleRate,
            AVNumberOfChannelsKey: channels, AVEncoderBitRateKey: aacBitRate, AVChannelLayoutKey: layoutData,
        ]
        guard writer.canApply(outputSettings: aac, forMediaType: .audio) else { throw HelperError("AAC settings rejected") }
        input = AVAssetWriterInput(mediaType: .audio, outputSettings: aac)
        input.expectsMediaDataInRealTime = false
        guard writer.canAdd(input) else { throw HelperError("cannot add the audio track") }
        writer.add(input)
        guard reader.startReading() else {
            throw HelperError("cannot read \(wav): \(reader.error?.localizedDescription ?? "unknown error")")
        }
    }

    /// Append samples while the input wants them. Returns true once the feed is over.
    func pump(writer: AVAssetWriter) -> Bool {
        if finished { return false }
        while input.isReadyForMoreMediaData {
            guard let sample = output.copyNextSampleBuffer() else {
                if reader.status == .failed {
                    error = HelperError("audio read failed: \(reader.error?.localizedDescription ?? "")")
                }
                finished = true
                input.markAsFinished()
                return true
            }
            guard input.append(sample) else {
                error = HelperError("AAC encode failed: \(writer.error?.localizedDescription ?? "unknown error")")
                finished = true
                input.markAsFinished()
                return true
            }
            fedUntil = CMTimeAdd(CMSampleBufferGetPresentationTimeStamp(sample), CMSampleBufferGetDuration(sample))
        }
        return false
    }
}

func encode(_ arguments: Arguments) throws {
    guard let outputPath = arguments.options["output"] else { throw HelperError("encode needs --output") }
    guard let codecName = arguments.options["codec"], ["h264", "hevc"].contains(codecName) else {
        throw HelperError("encode needs --codec h264 or hevc")
    }
    let hdr = arguments.options["hdr"] ?? ""
    guard ["", "HLG", "PQ"].contains(hdr) else { throw HelperError("--hdr must be HLG or PQ") }
    if !hdr.isEmpty && codecName != "hevc" { throw HelperError("HDR export needs HEVC") }
    let quality = try arguments.double("quality", default: 0.7)
    let bitrate = try arguments.int("bitrate", default: 0)
    let aacBitRate = try arguments.int("aac-bitrate", default: 192_000)
    let rotation = try arguments.int("rotation", default: 0)

    let headerLine = try readLine(STDIN_FILENO)
    guard let headerObject = try JSONSerialization.jsonObject(with: Data(headerLine.utf8)) as? [String: Any] else {
        throw HelperError("stream header is not a JSON object")
    }
    let header = try EncodeHeader(json: headerObject)
    if !hdr.isEmpty && header.layout.name != "p010le" { throw HelperError("HDR export needs p010le frames") }

    let url = URL(fileURLWithPath: outputPath)
    try? FileManager.default.removeItem(at: url)
    let fileType: AVFileType = url.pathExtension.lowercased() == "mov" ? .mov : .mp4
    let writer = try AVAssetWriter(outputURL: url, fileType: fileType)
    writer.shouldOptimizeForNetworkUse = true

    let codec: AVVideoCodecType = codecName == "h264" ? .h264 : .hevc
    let colors = colorProperties(header.color, hdr: hdr)
    var compression: [String: Any] = [:]
    if bitrate > 0 {
        compression[AVVideoAverageBitRateKey] = bitrate
    } else {
        compression[AVVideoQualityKey] = max(0.0, min(1.0, quality))
    }
    if header.fps > 0 { compression[AVVideoExpectedSourceFrameRateKey] = header.fps }
    if !hdr.isEmpty { compression[AVVideoProfileLevelKey] = kVTProfileLevel_HEVC_Main10_AutoLevel as String }
    var settings: [String: Any] = [
        AVVideoCodecKey: codec, AVVideoWidthKey: header.width, AVVideoHeightKey: header.height,
        AVVideoCompressionPropertiesKey: compression,
    ]
    if let colors { settings[AVVideoColorPropertiesKey] = colors }
    if !writer.canApply(outputSettings: settings, forMediaType: .video) && bitrate == 0 {
        // Constant-quality encoding is not available on every encoder; fall back to
        // a bitrate of about 0.12 bits per pixel per frame, like the FFmpeg route.
        compression.removeValue(forKey: AVVideoQualityKey)
        let fps = header.fps > 0 ? header.fps : 30
        compression[AVVideoAverageBitRateKey] = max(500_000, Int(Double(header.width * header.height) * fps * 0.12))
        settings[AVVideoCompressionPropertiesKey] = compression
    }
    guard writer.canApply(outputSettings: settings, forMediaType: .video) else {
        throw HelperError("macOS cannot encode \(codecName.uppercased())\(hdr.isEmpty ? "" : " \(hdr)") at \(header.width)x\(header.height)")
    }
    let input = AVAssetWriterInput(mediaType: .video, outputSettings: settings)
    input.expectsMediaDataInRealTime = false
    if rotation != 0 { input.transform = CGAffineTransform(rotationAngle: CGFloat(rotation) * .pi / 180) }
    let adaptor = AVAssetWriterInputPixelBufferAdaptor(
        assetWriterInput: input,
        sourcePixelBufferAttributes: [
            kCVPixelBufferPixelFormatTypeKey as String: header.layout.fourCC,
            kCVPixelBufferWidthKey as String: header.width, kCVPixelBufferHeightKey as String: header.height,
        ])
    guard writer.canAdd(input) else { throw HelperError("cannot add the video track") }
    writer.add(input)

    var audio: AudioFeeder?
    if let wav = arguments.options["audio"] {
        audio = try AudioFeeder(wav: wav, writer: writer, aacBitRate: aacBitRate)
    }
    guard writer.startWriting() else {
        throw HelperError("cannot start writing \(outputPath): \(writer.error?.localizedDescription ?? "unknown error")")
    }
    // The session starts at the first frame's timestamp, so read that one here.
    guard let first = try FrameHeader.read(STDIN_FILENO) else { throw HelperError("no frames were received") }
    writer.startSession(atSourceTime: CMTime(value: first.pts, timescale: header.timescale))

    let video = VideoFeed(header: header, input: input, adaptor: adaptor, colors: colors, codec: codecName, first: first)
    let group = DispatchGroup()
    group.enter()
    input.requestMediaDataWhenReady(on: DispatchQueue(label: "localsr.media.video")) {
        if video.pump(writer: writer) { group.leave() }
    }
    if let audio {
        group.enter()
        audio.input.requestMediaDataWhenReady(on: DispatchQueue(label: "localsr.media.audio")) {
            if audio.pump(writer: writer) { group.leave() }
        }
    }
    group.wait()
    if let error = video.error ?? audio?.error { throw error }
    var end = video.lastEnd
    if let audio, CMTimeCompare(audio.fedUntil, end) > 0 { end = audio.fedUntil }
    writer.endSession(atSourceTime: end)
    let semaphore = DispatchSemaphore(value: 0)
    writer.finishWriting { semaphore.signal() }
    semaphore.wait()
    guard writer.status == .completed else {
        throw HelperError("finishing \(outputPath) failed: \(writer.error?.localizedDescription ?? "unknown error")")
    }
    try printJSON(["frames": video.frames, "output": outputPath, "codec": codecName, "hdr": hdr])
}

// MARK: - main

do {
    let arguments = Array(CommandLine.arguments.dropFirst())
    guard let command = arguments.first else {
        throw HelperError("usage: localsr-media version|probe|decode|encode ...")
    }
    let parsed = try Arguments(arguments.dropFirst())
    switch command {
    case "version":
        try printJSON(["helper": "localsr-media", "version": helperVersion, "platform": "macOS", "stream": streamTag])
    case "probe":
        try probe(parsed)
    case "decode":
        try decode(parsed)
    case "encode":
        try encode(parsed)
    default:
        throw HelperError("unknown command \(command)")
    }
} catch {
    fail("localsr-media: \(error)")
}
