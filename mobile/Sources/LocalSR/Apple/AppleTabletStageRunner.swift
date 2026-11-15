#if !SKIP
import Foundation
import LocalSRCore

/// Runs a single image stage. Registry access is held for the whole stage because the engine owns
/// reusable tensor buffers; a caller may run a different model/unit concurrently on another lane.
enum AppleTabletStageRunner {
    static func run(source: PixelBuffer, model: TabletModelID, unit: ComputeUnit,
                    outputScale: Int, registry: TabletModelRegistry,
                    isCancelled: () -> Bool = { false },
                    progress: (Int, Int) -> Void = { _, _ in }) throws -> PixelBuffer {
        let descriptor = ModelCatalog.model(model)
        precondition((descriptor.scale == 1 && outputScale == 1) ||
                     (descriptor.scale == 4 && (outputScale == 2 || outputScale == 4)))
        let output = PixelBuffer(width: source.width * outputScale, height: source.height * outputScale)
        let tiles = TilePlanner.plan(width: source.width, height: source.height, window: descriptor.window,
                                     halo: descriptor.halo, scale: outputScale)
        try registry.withEngine(model: model, unit: unit) { engine in
            for (index, tile) in tiles.enumerated() {
                if isCancelled() { throw CancellationError() }
                let view = try engine.predict { destination in
                    ApplePixelKernels.fillWindow(source: source.pointer, width: source.width, height: source.height,
                                                 tile: tile, window: descriptor.window, dst: destination)
                }
                ApplePixelKernels.stitch(tile: tile, output: view, image: output.pointer,
                                         outputWidth: output.width, outputScale: outputScale,
                                         modelScale: descriptor.scale)
                progress(index + 1, tiles.count)
            }
            if isCancelled() { throw CancellationError() }
        }
        return output
    }
}
#endif
