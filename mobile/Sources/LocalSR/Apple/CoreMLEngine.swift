#if !SKIP
import Foundation
import CoreML
import Accelerate
import LocalSRCore

/// Core ML implementation of `SREngine`.
///
/// Model: precompiled `.mlmodelc`, FP16 input `image` [1,3,384,384] -> FP16 output `out` [1,3,1536,1536].
/// The input and output tensors live in buffers owned by the engine and are reused for every tile
/// (the input array wraps `inputBuffer`, the output uses `MLPredictionOptions.outputBackings`).
/// An engine runs one prediction at a time; create one engine per concurrent job.
public final class CoreMLEngine: SREngine, @unchecked Sendable {
    public static let window = 384
    public static let outputSize = 1536
    static let inputName = "image"
    static let outputName = "out"

    public enum EngineError: Error, CustomStringConvertible {
        case notLoaded
        case badTile(Int)
        case badOutput(String)
        public var description: String {
            switch self {
            case .notLoaded: return "Model not loaded"
            case .badTile(let n): return "Tile must hold \(3 * 384 * 384) floats, got \(n)"
            case .badOutput(let why): return "Unexpected model output: \(why)"
            }
        }
    }

    public private(set) var loadedModel: ModelKey?
    public private(set) var loadedUnit: ComputeUnit?
    private var model: MLModel?

    private let inputCount = 3 * CoreMLEngine.window * CoreMLEngine.window
    private let outputCount = 3 * CoreMLEngine.outputSize * CoreMLEngine.outputSize
    private let inputBuffer: UnsafeMutablePointer<Float16>
    /// Two output buffers used alternately, so that the caller can still read the previous tile while the
    /// next one is predicted (the view of tile n stays valid until tile n + 2 is predicted).
    private let outputBuffers: [UnsafeMutablePointer<Float16>]
    private let inputArray: MLMultiArray
    private let outputArrays: [MLMultiArray]
    private var phase = 0
    private let provider: MLDictionaryFeatureProvider
    private var useOutputBackings = true
    /// Keeps the latest two predictions (and so the memory behind `TileOutputView`) alive.
    private var results: [MLFeatureProvider?] = [nil, nil]

    public init() {
        let w = CoreMLEngine.window, o = CoreMLEngine.outputSize
        inputBuffer = UnsafeMutablePointer<Float16>.allocate(capacity: 3 * w * w)
        inputBuffer.initialize(repeating: 0, count: 3 * w * w)
        outputBuffers = (0..<2).map { _ in
            let p = UnsafeMutablePointer<Float16>.allocate(capacity: 3 * o * o)
            p.initialize(repeating: 0, count: 3 * o * o)
            return p
        }
        inputArray = try! MLMultiArray(dataPointer: inputBuffer, shape: [1, 3, NSNumber(value: w), NSNumber(value: w)], dataType: .float16, strides: [NSNumber(value: 3 * w * w), NSNumber(value: w * w), NSNumber(value: w), 1], deallocator: nil)
        outputArrays = outputBuffers.map { buffer in
            try! MLMultiArray(dataPointer: buffer, shape: [1, 3, NSNumber(value: o), NSNumber(value: o)], dataType: .float16, strides: [NSNumber(value: 3 * o * o), NSNumber(value: o * o), NSNumber(value: o), 1], deallocator: nil)
        }
        provider = try! MLDictionaryFeatureProvider(dictionary: [CoreMLEngine.inputName: MLFeatureValue(multiArray: inputArray)])
    }

    deinit {
        inputBuffer.deallocate()
        for buffer in outputBuffers { buffer.deallocate() }
    }

    // MARK: Loading

    static func configuration(for unit: ComputeUnit) -> MLModelConfiguration {
        let config = MLModelConfiguration()
        switch unit {
        case .npu: config.computeUnits = .cpuAndNeuralEngine
        case .gpu: config.computeUnits = .cpuAndGPU
        case .cpu: config.computeUnits = .cpuOnly
        }
        return config
    }

    public func load(model key: ModelKey, unit: ComputeUnit) throws {
        let url = try AppleResources.modelURL(key)
        let loaded = try MLModel(contentsOf: url, configuration: Self.configuration(for: unit))
        adopt(loaded, key, unit)
    }

    /// Async variant (Core ML loads on its own queue, so a caller can put a timeout around it).
    public func loadAsync(model key: ModelKey, unit: ComputeUnit) async throws {
        let url = try AppleResources.modelURL(key)
        let loaded = try await MLModel.load(contentsOf: url, configuration: Self.configuration(for: unit))
        adopt(loaded, key, unit)
    }

    private func adopt(_ loaded: MLModel, _ key: ModelKey, _ unit: ComputeUnit) {
        model = loaded
        loadedModel = key
        loadedUnit = unit
        results = [nil, nil]
        useOutputBackings = true
    }

    public func unload() {
        model = nil
        loadedModel = nil
        loadedUnit = nil
        results = [nil, nil]
    }

    // MARK: Prediction

    /// Runs one tile. `fill` writes the planar FP16 input (3 x 384 x 384) into the reused input buffer.
    /// The returned view stays valid until the second next call (two alternating output buffers).
    func predict(fill: (UnsafeMutablePointer<Float16>) -> Void) throws -> TileOutputView {
        guard let model else { throw EngineError.notLoaded }
        fill(inputBuffer)
        phase ^= 1
        let result: MLFeatureProvider
        if useOutputBackings {
            let options = MLPredictionOptions()
            options.outputBackings = [Self.outputName: outputArrays[phase]]
            do {
                result = try model.prediction(from: provider, options: options)
            } catch {
                // A backend that cannot write into our buffer: fall back to Core ML's own output arrays.
                useOutputBackings = false
                result = try model.prediction(from: provider)
            }
        } else {
            result = try model.prediction(from: provider)
        }
        results[phase] = result
        guard let array = result.featureValue(for: Self.outputName)?.multiArrayValue else {
            throw EngineError.badOutput("no multi-array named \(Self.outputName)")
        }
        guard array.dataType == .float16, array.shape.count == 4, array.shape[1].intValue == 3, array.shape[2].intValue == Self.outputSize, array.shape[3].intValue == Self.outputSize else {
            throw EngineError.badOutput("dtype \(array.dataType.rawValue) shape \(array.shape)")
        }
        let strides = array.strides.map { $0.intValue }
        // Core ML keeps this memory alive as long as `result` is (it is stored in `results`), so the
        // pointer may outlive the closure. When our own backing was used it is simply `outputBuffers[phase]`.
        let base: UnsafePointer<Float16> = array.withUnsafeBytes { raw in
            UnsafePointer(raw.baseAddress!.assumingMemoryBound(to: Float16.self))
        }
        return TileOutputView(base: base, channelStride: strides[1], rowStride: strides[2], columnStride: strides[3])
    }

    /// `SREngine`: planar float [0,1] input (3 x 384 x 384) -> planar float output (3 x 1536 x 1536).
    public func run(tile: [Float]) throws -> [Float] {
        guard tile.count == inputCount else { throw EngineError.badTile(tile.count) }
        let view = try predict { dst in
            tile.withUnsafeBufferPointer { src in
                var s = vImage_Buffer(data: UnsafeMutableRawPointer(mutating: src.baseAddress!), height: 1, width: vImagePixelCount(inputCount), rowBytes: inputCount * 4)
                var d = vImage_Buffer(data: UnsafeMutableRawPointer(dst), height: 1, width: vImagePixelCount(inputCount), rowBytes: inputCount * 2)
                vImageConvert_PlanarFtoPlanar16F(&s, &d, vImage_Flags(kvImageNoFlags))
            }
        }
        return toFloats(view)
    }

    /// Planar float copy of a prediction (3 x 1536 x 1536).
    func toFloats(_ view: TileOutputView) -> [Float] {
        let o = Self.outputSize
        var result = [Float](repeating: 0, count: outputCount)
        result.withUnsafeMutableBufferPointer { out in
            for c in 0..<3 {
                for y in 0..<o {
                    let src = view.base + c * view.channelStride + y * view.rowStride
                    let dst = out.baseAddress! + (c * o + y) * o
                    if view.columnStride == 1 {
                        var s = vImage_Buffer(data: UnsafeMutableRawPointer(mutating: src), height: 1, width: vImagePixelCount(o), rowBytes: o * 2)
                        var d = vImage_Buffer(data: UnsafeMutableRawPointer(dst), height: 1, width: vImagePixelCount(o), rowBytes: o * 4)
                        vImageConvert_Planar16FtoPlanarF(&s, &d, vImage_Flags(kvImageNoFlags))
                    } else {
                        for x in 0..<o { dst[x] = Float(src[x * view.columnStride]) }
                    }
                }
            }
        }
        return result
    }
}
#endif
