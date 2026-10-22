# Documentation images

## Before and after

The README uses `marseille-detail-input.png` and `marseille-detail-output.webp`:
the same crop before and after a 4× upscale with LocalSR's Quick preset,
SPAN NomosUni by Philip Hofmann (CC BY 4.0). The output is actual inference,
encoded as lossless WebP, with no added sharpening or colour changes.

Photo: **Tim Ziegelbaum**, Unité d’Habitation, Marseille,
[on Unsplash](https://unsplash.com/photos/modern-apartment-building-with-colorful-balconies-against-blue-sky-n4bofMDUS_Y),
used under the [Unsplash License](https://unsplash.com/license).
The 160 × 80 input detail corresponds to a 640 × 320 output detail.

These are the same verified assets used on the LocalSR website. Exact source,
crop, model checksum, device, settings and asset hashes are in
[`image-provenance.json`](image-provenance.json).

## Window and model library

`localsr-window.webp` and `localsr-model-library.webp` are unedited captures of
LocalSR v0.2.0-beta on macOS 26, taken on 22 October 2026 on an M1 Pro with
16 GB of memory, in a fresh profile, saved as lossless WebP. The window shows a
480 × 300 crop of the Marseille photo above after a 4× upscale with Quick
(SPAN NomosUni), original on the left and result on the right; the library
shows the photo upscalers and the Best model's license, source and memory
estimate.

## Earlier screenshot

`localsr-desktop.png` is the earlier, unedited capture of v0.1.1-beta, taken on
15 September 2026. Its Apollo 17 photograph is image **AS17-148-22727**, courtesy
of the Earth Science and Remote Sensing Unit, NASA Johnson Space Center
([source](https://svs.gsfc.nasa.gov/30613) ·
[media usage guidelines](https://www.nasa.gov/nasa-brand-center/images-and-media/)).
NASA does not endorse LocalSR. The README now uses the images above.
