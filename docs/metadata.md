# Image metadata and color behavior

LocalSR applies EXIF orientation to pixels exactly once before inference and writes orientation `1`
to outputs. It regenerates EXIF pixel width/height from the encoded output. To reduce accidental
privacy leakage, metadata preservation is intentionally an allow-list: DateTime (`306`), Artist
(`315`), and Copyright (`33432`). GPS, camera/body serials, host/device names, software history,
maker notes, and embedded thumbnails are not copied.

When a readable source ICC profile is present, pixels are converted to sRGB and the resulting sRGB
profile is embedded in JPEG, PNG, TIFF, and WebP outputs when that encoder supports it. DNG pixels
are developed through LibRaw into sRGB; LocalSR does not claim to preserve the original RAW camera
profile or full DNG metadata. Invalid ICC data produces a warning rather than being copied.

PNG, TIFF, and WebP preserve and resize an input alpha channel. JPEG cannot carry alpha and is
written as RGB. EXIF fields unsupported by an output encoder are not claimed as preserved. Turning
metadata preservation off omits both the safe EXIF subset and ICC profile.

Every final image is encoded to a LocalSR-owned temporary sibling and atomically replaces the final
path only after encoding succeeds. Existing outputs are not selected for overwrite by default.
