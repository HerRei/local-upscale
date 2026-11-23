# Model cache cleanup

**Settings → Storage** shows and clears the compiled model cache, and includes
the **Remove unused caches automatically** switch (`auto_cache_cleanup`, on by
default) that gates the startup cleanup. On startup, when that switch is on,
LocalSR removes entries older than 30 days, then removes the oldest entries
until the total is at most 5 GiB. It uses the newest file access or modification
time available; access times are not updated on every filesystem. The cleanup
holds the queue scheduler lock and refuses to run during an active desktop job.
It checks for symbolic links and refuses a cache containing one. Environment
variable overrides for worker caches are not included. **Clear cache** remains
available as a manual action even when automatic cleanup is turned off.

The managed entries are:

- macOS: `~/Library/Caches/LocalSR/coreml/*/model.mlmodelc` and the individual
  model entries under `~/Library/Caches/localsr-worker/com.apple.e5rt.e5bundlecache`.
  These are compiled programs that can be generated again from the model. The
  Apple directory is framework-managed, so its exact internal layout is not a
  public LocalSR contract. Core ML verdict files beside the programs are kept.
- Windows: `%LOCALAPPDATA%/LocalSR/gpu-checks/npu-cache`; Linux:
  `${XDG_CACHE_HOME:-~/.cache}/LocalSR/gpu-checks/npu-cache`. These OpenVINO
  NPU blobs are compiled again if missing. The worker also trims them to 2 GiB
  as it compiles. GPU crash and preflight records in `gpu-checks` are kept.

Other storage is deliberately outside this cleanup:

- CUDA's JIT cache (`%APPDATA%/NVIDIA/ComputeCache` or `~/.nv/ComputeCache`)
  and ROCm's MIOpen kernel cache (`~/.cache/miopen`) can be shared with other
  applications. MIOpen's tuning database is separate and is not disposable
  compiled code. PyTorch Inductor and Triton may use shared system temporary
  folders if invoked. LocalSR does not configure private locations for these.
- The ONNX route uses DirectML and OpenVINO, not the TensorRT provider; only its
  NPU route explicitly enables the OpenVINO disk cache. Driver and framework
  versions may create additional caches whose location and lifetime are not
  controlled by LocalSR.
- Output memmaps, codec recovery frames, edit scratch, decoding and encoding
  temporary files, and playback copies can be in active use. Existing job
  cleanup owns them; playback copies are cleared at exit. Preview and compare
  image caches also exist in memory. Downloaded model files, partial engine
  downloads and update data are not disposable compiled programs.

See [Apple's cache directory guidance](https://developer.apple.com/documentation/foundation/using-the-file-system-effectively),
[NVIDIA's CUDA environment variables](https://docs.nvidia.com/cuda/archive/13.4.3/cuda-programming-guide/05-appendices/environment-variables.html),
[AMD's MIOpen kernel cache documentation](https://rocm.docs.amd.com/projects/MIOpen/en/latest/conceptual/cache.html),
[PyTorch's compile cache documentation](https://docs.pytorch.org/tutorials/recipes/torch_compile_caching_configuration_tutorial.html),
and [ONNX Runtime's OpenVINO provider documentation](https://onnxruntime.ai/docs/execution-providers/OpenVINO-ExecutionProvider.html).
