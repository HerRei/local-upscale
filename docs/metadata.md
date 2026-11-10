# Image metadata and color behavior

LocalSR applies EXIF orientation to pixels exactly once before inference and writes orientation `1`
to outputs. It regenerates EXIF pixel width/height from the encoded output. To reduce accidental
privacy leakage, metadata preservation is intentionally an allow-list: DateTime (`306`), Artist
(`315`), and Copyright (`33432`). GPS, camera/body serials, host/device names, software history,
maker notes, and embedded thumbnails are not copied.

When a readable source ICC profile is present, pixels are converted to sRGB and the resulting sRGB
profile is embedded in JPEG, PNG, TIFF, WebP, and (on macOS) HEIC outputs when that encoder supports it. DNG pixels
are developed through LibRaw into sRGB; LocalSR does not claim to preserve the original RAW camera
profile or full DNG metadata. Invalid ICC data produces a warning rather than being copied.
Grey and CMYK profiles convert to sRGB the same way. 16-bit grey PNG and TIFF files are scaled
to 8 bits, the precision LocalSR works in.

A grey source (a grey, black-and-white or grey-palette file, or an RGB file whose channels are
all equal) has no colour, so LocalSR saves only the luma of the model's result: models trained
on colour images otherwise tint grey manga pages and scans. The file stays RGB. RGB images with
unequal channels, including some compressed grey JPEGs, are treated as colour. The live preview
shows the model output before this save-time adjustment.
Other camera RAW formats are developed the same way; their EXIF is not read, so nothing from
them is preserved. HEIC/HEIF and JPEG XL are decoded by macOS, which applies the orientation and
keeps the colour profile and the allow-listed tags. GIF, HEIC and other multi-image files use
their first image.

PNG, TIFF, WebP, and HEIC preserve and resize an input alpha channel. JPEG cannot carry alpha and is
written as RGB. EXIF fields unsupported by an output encoder are not claimed as preserved. Turning
metadata preservation off omits both the safe EXIF subset and ICC profile.

HEIC output exists on macOS only. LocalSR writes a lossless TIFF carrying the sRGB profile, the safe
EXIF subset and any alpha, and `/usr/bin/sips` (ImageIO) encodes it at the chosen quality (the same
slider as JPEG), so metadata is kept as for the other formats. HEIC stores 8-bit colour here.

Every final image is encoded to a LocalSR-owned temporary sibling and is given its final name only
after encoding succeeds. That step never replaces a file: if one appeared at the name while the job
ran, the job fails for that item and the existing file is untouched.
