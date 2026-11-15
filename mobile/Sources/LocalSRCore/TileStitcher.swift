public enum TileStitcher {
    public static func stitch(tile: Tile, tileOutput: [Float], output: inout [UInt8], outputWidth: Int, tileSize: Int = 1536, outputScale: Int = 4) {
        precondition(tileOutput.count == 3 * tileSize * tileSize && outputWidth > 0 && output.count % (outputWidth * 4) == 0)
        precondition(outputScale == 2 || outputScale == 4)
        let reduction = 4 / outputScale
        let plane = tileSize * tileSize
        for y in 0..<tile.outputHeight {
            for x in 0..<tile.outputWidth {
                let source = ((tile.sourceY + y) * tileSize + tile.sourceX + x) * reduction
                let dest = ((tile.outputY + y) * outputWidth + tile.outputX + x) * 4
                for channel in 0..<3 {
                    var value = tileOutput[channel * plane + source]
                    if reduction == 2 {
                        value = (value + tileOutput[channel * plane + source + 1] + tileOutput[channel * plane + source + tileSize] + tileOutput[channel * plane + source + tileSize + 1]) / 4
                    }
                    let safe: Float = value.isFinite ? PixelMath.clamp01(value) : Float(0)
                    output[dest + channel] = UInt8(Int(Double(safe * Float(255)) + 0.5))
                }
                output[dest + 3] = UInt8(255)
            }
        }
    }
}
