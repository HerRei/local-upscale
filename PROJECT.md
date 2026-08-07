# Project: LocalSR End-to-End Integration

## Architecture
LocalSR is a desktop super-resolution application built with PySide6 (Qt) and PyTorch / Spandrel.
- **UI Process**: `MainWindow` (`src/localsr/ui/main_window.py`) manages UI controls and communicates with `WorkerClient` (`src/localsr/protocol/client.py`).
- **Worker Subprocess**: Spawns `localsr.worker.__main__` (`WorkerServer` in `src/localsr/worker/server.py`) over stdin/stdout JSON IPC protocol.
- **Inference & I/O Engine**: `InferenceEngine` (`src/localsr/core/inference.py`), `ModelAdapter` (`src/localsr/core/model_adapter.py`), and `ImageManager` (`src/localsr/core/image_io.py`) handle image loading, tiling, model execution, memmap writer allocation (`OutputWriter`), and atomic final saving.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| F1.1 | ImageManager.save implementation | Delegate save() to save_from_writer() with proper parameters | M1 | R1 |
| F1.2 | Atomic file saving | Save output to destination.tmp before atomic os.replace rename | M1 | R1 |
| F1.3 | WorkerServer save parameter passing | Pass icc_profile, safe_exif, and scale from WorkerServer to save() | M1 | R1 |
| F1.4 | Memmap cleanup in finally block | Wrap process_image and save in try...finally calling out_file.cleanup() | M1 | R1 |
| F2.1 | WorkerClient signal expansion | Define PySide signals for worker_ready, job_started, job_completed, job_cancelled, job_failed, warning | M2 | R2 |
| F2.2 | Protocol message routing | Parse incoming stdout JSON messages in handle_stdout and emit specific signals & job_result | M2 | R2 |
| F2.3 | MainWindow signal connection | Connect all WorkerClient protocol signals in MainWindow | M2 | R2 |
| F2.4 | GUI button state management | Enable/disable btn_upscale, btn_cancel, btn_open, btn_reveal on job completion or cancellation | M2 | R2 |
| F3.1 | WorkerServer stdin EOF sentinel | Send shutdown_request on stdin EOF in reader_thread_func | M2 | R3 |
| F3.2 | WorkerClient shutdown state | Add _is_shutting_down boolean to suppress worker_error signals during clean shutdown | M2 | R3 |
| F3.3 | MainWindow worker crash distinction | Avoid restarting worker process when shutting down intentionally or on clean exit 0 | M2 | R3 |
| F4.1 | Worker subprocess launcher test | Test Popen spawning of worker process with sys.executable -u -m localsr.worker.__main__ | M3 | R4 |
| F4.2 | Worker startup signal test | Test worker_ready signal emission over stdin/stdout IPC | M3 | R4 |
| F4.3 | Dummy job output test | Test real 2x upscale output image creation with exact dimensions | M3 | R4 |
| F4.4 | Cancellation output prevention test | Test job cancellation preventing final output file creation | M3 | R4 |
| F4.5 | Post-cancel job execution test | Test successful execution of a subsequent job after cancellation | M3 | R4 |
| F4.6 | Shutdown and tempfile cleanup test | Test clean worker process exit with 0 leftover Python processes or .dat memmaps | M3 | R4 |
| F4.7 | Full GUI vertical slice | Test MainWindow → QProcess → worker → Spandrel → tiled inference → atomic output | M3 | R4 |
| F5.1 | HAT model architecture loading | Load HAT checkpoint via Spandrel ModelLoader under architecture "HAT" | M4 | R5 |
| F5.2 | Tile shape requirements | Apply Spandrel requirements plus model attention-window and square-input constraints | M4 | R5 |
| F5.3 | Synthetic 32x32 & 64x64 2x verification | Run 2x upscaling on synthetic 32x32 and 64x64 images and assert exact dimensions | M4 | R5 |
| F5.4 | Photograph-like 500x500 verification | Run HAT on a generated photograph-like image with tile_size=128, halo=16 and verify output | M4 | R5 |
| F6.1 | Curated HAT catalog | Offer HAT-S, HAT, and HAT-L ×4 with source, license, pinned URLs, sizes, and SHA-256 hashes | M5 | R6 |
| F6.2 | On-demand model installation | Stream to a partial file, support cancellation, verify size/hash, and atomically install outside the package | M5 | R6 |
| F6.3 | Bring-your-own model | Keep local Spandrel-compatible checkpoint selection alongside the catalog | M5 | R6 |
| F6.4 | Worker hardware discovery | Report MPS, CUDA, CPU, available system/device memory, FP16 support, and recommended tiles over IPC | M5 | R6 |
| F6.5 | Hardware-safe controls | Restrict device, precision, tile, halo, and Safe Memory Mode based on reported capability | M5 | R6 |
| F6.6 | Resource estimates and warnings | Estimate time/memory/disk/tiles, learn successful throughput, block unsafe starts, and explain runtime failures | M5 | R6 |
| F6.7 | Live resource feedback | Show calibrated time ranges and sample remaining RAM/VRAM/unified memory during inference | M5 | R6 |
| F7.1 | DNG RAW input | Probe DNG dimensions in the GUI and develop sensor data with LibRaw, camera white balance, orientation, and sRGB output in the worker | M6 | R7 |
| F8.1 | Multi-vendor GPU backends | Detect NVIDIA CUDA, AMD ROCm, and Intel XPU/iGPU devices exposed by PyTorch, apply hard allocator caps, and retain CPU fallback | M7 | R8 |
| F9.1 | Selectable output scale | Offer every integer output factor through the model's native scale, resize once with Lanczos when needed, and reflect the choice in estimates, dimensions, filenames, alpha, and metadata | M8 | R9 |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Output Saving & Memmap Cleanup | R1: ImageManager.save, atomic rename, WorkerServer parameter passing & finally out_file.cleanup | none | DONE |
| M2 | GUI Protocol Repair & Worker Shutdown | R2 & R3: WorkerClient signal routing, MainWindow button state updates, stdin EOF & clean shutdown | M1 | DONE |
| M3 | Genuine Integration Test Suite | R4: Subprocess IPC test suite verifying startup, dummy job 2x output, cancel, restart, cleanup | M1, M2 | DONE |
| M4 | Model Architecture Verification (HAT) | R5: real Spandrel HAT loading/inference with generated lightweight weights and tiled image checks | M1, M2, M3 | DONE |
| M5 | Model Catalog & Resource-Aware UX | R6: optional verified models, hardware capability IPC, constrained settings, estimates, and actionable failures | M1–M4 | DONE |
| M6 | Camera RAW Input | R7: cross-platform DNG selection, lightweight dimension probing, isolated LibRaw development, and regression coverage | M1–M5 | DONE |
| M7 | Multi-vendor GPU Safety | R8: ROCm/XPU discovery, shared-memory awareness, hard CUDA/ROCm/XPU/MPS allocator caps, advisory MPS pressure, and visible progress controls | M1–M6 | DONE |
| M8 | Output Scale Selection | R9: hardware-aware output-factor UI, native-model inference, high-quality final resizing, protocol propagation, and regression coverage | M1–M7 | DONE |
| M_E2E | Requirement-Driven E2E Test Suite | Dual Track: Independent opaque-box test suite for R1..R5 generating TEST_READY.md | none | DONE (TEST_READY.md published) |

## Verification Boundary
- The integration suite constructs lightweight ESRGAN and HAT state-dict checkpoints and loads them through the real Spandrel API. This verifies architecture detection, worker IPC, tiling, cancellation, saving, and shutdown without downloading untrusted files.
- Generated checkpoints contain random weights; they do not demonstrate visual restoration quality.
- The optional external-checkpoint test remains skipped unless `LOCALSR_TEST_MODEL_PATH` points to a trusted pretrained Spandrel-compatible model.

## Interface Contracts
### Worker Subprocess IPC Protocol (GUI ↔ Worker)
- **Transport**: Stdin / Stdout JSON lines.
- **Messages**:
  - `{"type": "worker_ready"}`
  - `{"type": "job_started", "job_id": str}`
  - `{"type": "job_completed", "job_id": str}`
  - `{"type": "job_cancelled", "job_id": str}`
  - `{"type": "job_failed", "job_id": str, "error": str}`
  - `{"type": "warning", "message": str}`
  - `{"type": "shutdown_request"}`

### Image IO Contract
- `ImageManager.save(output_writer, destination_path, format, quality, preserve_metadata, icc_profile, safe_exif, scale)`
- Writes to `destination_path + ".tmp"`, flushes, and performs `os.replace(tmp_path, destination_path)`.

## Code Layout
- `src/localsr/core/image_io.py`: ImageManager, image loading & atomic saving.
- `src/localsr/core/inference.py`: InferenceEngine, tiling, memmap allocation.
- `src/localsr/core/model_adapter.py`: ModelAdapter, Spandrel integration.
- `src/localsr/core/model_catalog.py`: Curated model metadata, model storage, verified downloads.
- `src/localsr/core/hardware.py`: Worker-side device and memory capability discovery.
- `src/localsr/core/estimator.py`: Conservative resource/time preflight calculations.
- `src/localsr/worker/server.py`: WorkerServer, job execution loop, memmap cleanup.
- `src/localsr/protocol/client.py`: WorkerClient, QProcess management, signal emission.
- `src/localsr/ui/main_window.py`: MainWindow UI logic, button states.
- `src/localsr/ui/model_download.py`: Cancellable background model download worker.
- `tests/`: Unit & integration tests (`test_integration.py`, `test_hat_model.py`, `test_e2e_requirements.py`).
