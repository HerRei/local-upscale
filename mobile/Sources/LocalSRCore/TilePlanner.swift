public struct Tile {
    public let windowX: Int
    public let windowY: Int
    public let padLeft: Int
    public let padRight: Int
    public let padTop: Int
    public let padBottom: Int
    public let outputX: Int
    public let outputY: Int
    public let outputWidth: Int
    public let outputHeight: Int
    public let sourceX: Int
    public let sourceY: Int
}

public enum TilePlanner {
    public static func plan(width: Int, height: Int, window: Int = 384, halo: Int = 16, scale: Int = 4) -> [Tile] {
        precondition(width > 0 && height > 0 && window > 2 * halo && halo >= 0 && scale > 0)
        let core = window - 2 * halo
        var tiles: [Tile] = []
        var y = 0
        while y < height {
            var x = 0
            while x < width {
                let wx = max(0, min(x - halo, width - window))
                let wy = max(0, min(y - halo, height - window))
                tiles.append(Tile(windowX: wx, windowY: wy, padLeft: 0, padRight: max(0, window - width), padTop: 0, padBottom: max(0, window - height), outputX: x * scale, outputY: y * scale, outputWidth: min(core, width - x) * scale, outputHeight: min(core, height - y) * scale, sourceX: (x - wx) * scale, sourceY: (y - wy) * scale))
                x += core
            }
            y += core
        }
        return tiles
    }
}
