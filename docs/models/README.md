# Models

LocalSR downloads models when you choose them. The catalog records the author,
source, license, exact file size and SHA-256; each checkpoint is verified before
loading. Restoration weights are not included in installers.

## Image catalog

| Model | Native scale | Use | Download | License |
| --- | --- | --- | ---: | --- |
| SPAN NomosUni | 4× | Quick photo upscaling | 4.5 MB | CC BY 4.0 |
| HAT-S | 4× | General photo upscaling | 81.1 MB | Apache-2.0 |
| HAT-L ImageNet | 4× | Larger general photo model | 165.8 MB | Apache-2.0 |
| RealPLKSR NomosWebPhoto | 4× | Degraded web photos · Labs | 29.7 MB | CC BY 4.0 |
| RealPLKSR HFA2k | 4× | Anime and line art · Labs | 29.7 MB | CC BY 4.0 |
| Real-ESRGAN x2plus | 2× | Native 2× upscaling | 67.1 MB | BSD-3-Clause |
| RealPLKSR Denoise | 1× | Fast photo denoising | 29.6 MB | CC-BY-4.0 |
| NAFNet SIDD | 1× | Camera-noise removal | 464.2 MB | MIT |
| NAFNet GoPro | 1× | Motion deblurring | 271.8 MB | MIT |
| FBCNN Color | 1× | JPEG artifact removal | 287.8 MB | Apache-2.0 |
| HAT-S Face | 4× | Face companion · verified import | 40.5 MB | Rights unresolved |
| HAT-L Face | 4× | Large face companion · verified import | 165.7 MB | Rights unresolved |

The native scale is the model's trained output scale. A smaller requested output
can be resampled after inference. A 1× restoration model cleans an image without
enlarging it and can run before an upscaler.

**Quick** selects SPAN NomosUni; **Best** selects RealPLKSR NomosWebPhoto. HAT-S is
the smaller stock HAT option. Model choice changes speed, memory use and visual
results; compare a representative crop before processing a large folder.

## Author credit and checkpoint terms

SPAN NomosUni, RealPLKSR Denoise, NomosWebPhoto and HFA2k are models by
**Philip Hofmann (Phips / Phhofm)**. NomosWebPhoto and HFA2k explicitly declare
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) on their
[model](https://huggingface.co/Phips/4xNomosWebPhoto_RealPLKSR)
[cards](https://huggingface.co/Phips/4xHFA2k_ludvae_realplksr_dysample).
The license permits sharing and commercial use with attribution and its other
conditions. LocalSR downloads the publisher's checkpoint files unchanged.

Stock HAT is by XPixel Group. The separate LocalSR face companions retain their
own [HAT-S Face](hat-s-face.md) and [HAT-L Face](hat-l-face.md) cards and selection
evidence. Their checkpoint rights remain unresolved: only an exact verified
copy that the user is independently permitted to use can be imported. A stock
HAT license does not clear the face-tuned weights.

See [model licenses](../model-licenses.md) for sources, provenance and restrictions
for the full catalog, including NAFNet, FBCNN, Real-ESRGAN and the YuNet face detector.
Application licensing does not replace a model author's terms.

## Video and Labs

The frame-by-frame engine uses compatible image upscalers. **SeedVR2 3B FP16 and
FP8** are separate, optional Labs downloads for supported engines. They export
SDR and can require substantial RAM, GPU memory and time. A smaller FP8 file does
not guarantee that a high-resolution clip fits in memory.

Experimental HAT HDR preservation exports HLG/PQ, but these models were trained
on SDR. HDR quality remains unverified. For the exact codec, engine and feature
boundaries, read [video support](../video-support.md).

## Your own models

Supported `.safetensors` checkpoints can be imported. Unverified pickle or
TorchScript files are blocked by default; the explicit override is described in
[the security policy](../../SECURITY.md). Check your model's license, architecture,
scale and colour assumptions before using it.
