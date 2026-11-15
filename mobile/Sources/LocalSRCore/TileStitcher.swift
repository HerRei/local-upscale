public enum TileStitcher {
    public static func stitch(tile: Tile, tileOutput: [Float], output: inout [UInt8], outputWidth: Int, tileSize: Int = 1536) {
        precondition(tileOutput.count == 3 * tileSize * tileSize && outputWidth > 0 && output.count % (outputWidth * 4) == 0)
        let plane = tileSize * tileSize
        for y in 0..<tile.outputHeight {
            for x in 0..<tile.outputWidth {
                let source = (tile.sourceY + y) * tileSize + tile.sourceX + x
                let dest = ((tile.outputY + y) * outputWidth + tile.outputX + x) * 4
                for channel in 0..<3 {
                    let value = tileOutput[channel * plane + source]
                    let safe: Float = value.isFinite ? PixelMath.clamp01(value) : Float(0)
                    output[dest + channel] = UInt8(Int((safe * Float(255)).rounded()))
                }
                output[dest + 3] = UInt8(255)
            }
        }
    }
}
