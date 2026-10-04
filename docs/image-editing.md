# Local Qwen image editing

**Edit** is a task in the Enhance pane, beside Upscale and Restore. Select an
image, choose Edit, write what should change, and press **Edit selected**. The
result is a new PNG in the output folder; the original stays intact. Progress,
cancellation and the before/after preview use the same queue as every other job.
Edit works on the selected image only; batches are not supported yet.

The model row under **Model** shows the chosen Qwen bundle with its license, its
download size and whether it fits this computer. **Change…** opens the model
library on the **Edit with a prompt** group, where every bundle lists what it
needs on a Mac (unified memory) and on Windows or Linux (GPU memory), whether it
fits the current hardware, and the largest edit size this computer starts with.
The **Edit size** control below the model row offers 512, 768 and 1024 px; sizes
above this computer's profile are disabled. **Advanced** holds the step count,
the seed, the output folder and the GPU. LocalSR picks the largest bundle of
Qwen Image Edit 2511 that fits when Edit is chosen for the first time; if none
fits, the smallest is shown with the reason it cannot start.

The catalog contains two families: Qwen Image Edit 2511 in six GGUF sizes and
FLUX.2 klein 4B in two sizes. FLUX.2 klein
is Black Forest Labs' distilled four-step editor (Apache-2.0, with a Qwen3-4B text
encoder and the FLUX.2 autoencoder); it is the bundle for 16 GB computers, where
the 20B Qwen editors do not fit, and LocalSR falls back to it automatically. The
bundled native runtime is stable-diffusion.cpp revision
`3f8527a46c54ecf4cb4ed6003da8e8982283c73c`. A Qwen bundle has four components
(diffusion transformer, text encoder, vision projection, VAE); a FLUX.2 klein bundle
has three, since it conditions on the encoded reference image instead of a vision
projector. Every component has a pinned upstream revision, exact byte size and
SHA-256 digest.
Downloads resume through LocalSR's existing downloader and are verified before
installation and again before inference. Components are shared within each model
family, so changing quantization does not redownload a matching encoder or VAE.

## Capacity profiles

These are starting profiles, **not measured fit guarantees**. GiB means 1024³
bytes. Available RAM, free VRAM, image dimensions and other apps decide whether a
job is admitted. The native process memory-maps the quantized weights and touches
them lazily, so only one phase is resident at a time: the text encoder and vision
projector while the prompt and reference are encoded, the transformer while the
image is sampled. It may segment GPU execution and never converts a GGUF model to
FP16/BF16 as a loading fallback.

`scripts/refresh_edit_catalog.py` derives the tiers from that phase model, so the
catalog, the worker's admission check and this table cannot drift apart:

- **Mac (unified memory):** the smallest Apple memory size where the heaviest
  phase plus the reserve fits in about 70% of memory (what an idle Mac has free),
  and the transformer with its working buffers stays under 60% of memory, below
  Apple's recommended Metal working set.
- **Dedicated GPU:** the smallest card whose memory holds the transformer with
  the usual driver overhead (87% of the nominal size). The encoders run from
  system RAM, and an 8 GB card still needs enough host RAM for them.

| Family | Size | Bundle | Transformer | Mac unified memory | GPU memory |
| --- | --- | ---: | ---: | ---: | ---: |
| Qwen Image Edit 2511 | Q2_K | 10.8 GiB | 7.0 GiB | 24 GiB | 8 GiB |
| Qwen Image Edit 2511 | Q3_K_S | 14.0 GiB | 8.6 GiB | 24 GiB | 12 GiB |
| Qwen Image Edit 2511 | Q4_K_M | 17.7 GiB | 12.3 GiB | 32 GiB | 16 GiB |
| Qwen Image Edit 2511 | Q5_K_M | 19.4 GiB | 14.0 GiB | 32 GiB | 20 GiB |
| Qwen Image Edit 2511 | Q6_K | 21.1 GiB | 15.7 GiB | 36 GiB | 20 GiB |
| Qwen Image Edit 2511 | Q8_0 | 25.7 GiB | 20.3 GiB | 48 GiB | 24 GiB |
| FLUX.2 klein 4B | Q4_0 | 4.9 GiB | 2.3 GiB | 16 GiB | 8 GiB |
| FLUX.2 klein 4B | Q8_0 | 8.3 GiB | 4.0 GiB | 16 GiB | 8 GiB |

Default maximum edit size: 512 px below 36 GiB of unified memory, 768 px below
48 GiB, 1024 px above (dedicated GPUs: 512 px below 12 GiB, 768 px below 16 GiB).
The app always defaults to a 512 px edit, even on larger hardware; larger sizes
are explicit choices. A 16 GB Mac gets FLUX.2 klein 4B; below 16 GiB the app shows
the smallest bundle, says why it cannot start, and the worker refuses to load it.
Each model carries its own step count (40 for the Qwen editors, 4 for FLUX.2
klein) and guidance; **Steps** under Advanced overrides it for the chosen model
only. Dimensions are aligned to 32 pixels and the source is resized to the chosen
longest edge. Qwen Image 2.1 is not offered: its Qwen Research License allows
research and evaluation only. The backend still supports bundles that require
explicit license acceptance (a checkbox in the library before the download, and
again under the model row before an edit starts) should one be added. Lower quantizations can reduce edit fidelity; CPU offload also makes
small-GPU edits slower. No timing or quality guarantee is attached to these
starting profiles.

## Memory and lifecycle

- Cached restoration models are released before Qwen admission; the queue runs
  one inference job at a time.
- Current available host RAM is checked before any runtime starts, after bundle
  verification, and again in the guard immediately before the native process starts.
- Host memory reserve: the larger of 4 GiB or 10% of physical RAM. The estimate
  is the heaviest phase with 10% headroom: the encoders, or the transformer (on
  unified memory) with the VAE and a working set for activations (1.5 GiB on
  unified memory, 1 GiB beside a dedicated GPU, scaled with the edit size). The
  supervisor's process limit allows every mapped file to stay resident, which is
  normal when memory is plentiful.
- Dedicated VRAM reserve: the larger of 2 GiB or 15% of capacity; the native
  budget is capped at 80% of capacity and at current free VRAM minus the reserve.
  Metal uses at most 60% of physical unified memory as the native managed budget.
- CPU offload, mapped weights, disabled prefetch, two CPU threads, Flash
  Attention and tiled VAE encoding/decoding are enabled. Edit 2511 explicitly enables
  `qwen_image_zero_cond_t=true`, as required by the runtime's model documentation.
- A small supervisor checks host availability, pressure, swap growth and native
  RSS every 250 ms. It stops the child when the reserve is crossed, swap grows
  by more than 512 MiB, memory pressure becomes high, or estimated RSS is exceeded.
  Its stdin pipe also detects cancellation, worker restart or worker death.
- Native weights live only in the disposable process. On success, cancellation,
  pressure failure or runtime error the process is reaped and temporary files are
  removed. A verified output is published without overwriting an existing file.

These checks reduce risk; a user-space watchdog cannot provide an absolute OS-level
guarantee against sudden allocations by unrelated applications. The native VRAM
budget covers managed weights/runner buffers, not every allocation made by a driver.
The implementation never raises macOS working-set limits or probes fit by allocating
models until the system runs out of memory.

## Packaging and development

`scripts/build_tauri_preview.py` prepares the editing runtime automatically and
`packaging/tauri_worker.spec` includes it with the engine. Models remain optional
downloads. Apple Silicon uses a pinned source build with Metal; Windows CUDA uses
upstream's SHA-256-pinned native release and CUDA runtime archives. Linux CUDA/ROCm
editions use the same pinned native Vulkan release, requiring a working Vulkan
driver on the selected GPU. CPU and DirectML editions do not advertise editing
on an unsupported device. Inference on XPU/DirectML is not implemented in this
editing workflow.

Vulkan GPUs are matched by card name, not by PyTorch's device index. Ambiguous
identical GPUs are refused unless a single device is exposed to Vulkan using
`GGML_VK_VISIBLE_DEVICES`, so admission never checks one card and loads another.
When changing native build backends, use a fresh `--destination` directory to
avoid mixing incompatible runtime libraries.

To build the development Metal executable without model inference:

```sh
python scripts/build_edit_runtime.py --backend MPS
```

The desktop discovers `.cache/edit-runtime/sd-cli` in development and
`engine/_internal/edit/sd-cli` (`sd-cli.exe` on Windows) in packaged builds.
`LOCALSR_EDIT_RUNTIME` or **Choose runtime…** (shown under the model row when
no runtime is found) can supply a current native executable. The backend checks
its required CLI controls before using it. Removing a bundle from the library
keeps components that another installed size of the same family still uses.

`scripts/refresh_edit_catalog.py` regenerates the catalog from the explicitly
pinned model revisions using metadata only. Updating a family requires reviewing
the matching components, runtime support and model license together.

Validation for this implementation uses simulated capacity profiles, tiny fixture
files and disposable non-model child processes. No Qwen weights were downloaded,
loaded, or used to probe the development Mac's capacity. Real inference acceptance
on the Mac/GPU matrix remains a separate validation task.

Upstream references:

- https://huggingface.co/Qwen/Qwen-Image-Edit-2511
- https://huggingface.co/unsloth/Qwen-Image-Edit-2511-GGUF
- https://github.com/leejet/stable-diffusion.cpp/blob/3f8527a46c54ecf4cb4ed6003da8e8982283c73c/docs/qwen_image_edit.md
