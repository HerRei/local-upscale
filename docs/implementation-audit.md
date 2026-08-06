# Implementation Audit

| Requirement | Current status | Relevant files | Defect or missing behaviour | Planned correction | Test that will verify it |
|---|---|---|---|---|---|
| Environment & dependencies | Missing some tools, PySide6 not installed in test venv | pyproject.toml | Venv setup didn't install GUI tools or pytest-qt. Python 3.14 used. | Enforce Python 3.11/3.12, add PySide6, pytest-qt, ruff. | `pytest` and `localsr` command work. |
| Cancellation while inference running | Broken | worker/server.py | `sys.stdin` is read synchronously; cancel request is blocked by inference. | Add dedicated stdin reader thread and `threading.Event` for immediate cancel check between tiles. | `test_worker_cancellation` |
| ICC colour management | Incorrect | core/image_io.py | Original ICC is copied blindly; no real conversion to sRGB properly. | Convert to sRGB, embed actual sRGB profile on output. Warning on bad ICC. | `test_icc_conversion` |
| EXIF metadata | Unsafe | core/image_io.py | Passthrough without orientation reset or dimension reset. | Reset orientation to 1, strip/recalc dimensions and thumbnails. | `test_exif_sanitization` |
| RGBA input | Alpha lost | core/image_io.py | Converted to RGB silently. | Extract alpha, process RGB, resize alpha with Lanczos, recombine. | `test_rgba_processing` |
| Out-of-memory retry | Missing | core/inference.py | Crashes outright. | Catch MPS/CUDA OOM, clean cache, reduce tile size, restart output writer. | `test_oom_recovery` |
| Memmap cleanup | Unsafe | core/output_writer.py | Temp files left on crash. | Context manager with atomic move and explicit `finally` cleanup. | `test_memmap_cleanup_exception` |
| Reflection padding on small images | Broken | core/inference.py | Padding fails if halo > dimension. | Ensure PyTorch padding limits aren't exceeded, or use custom reflect pad. | `test_halo_larger_than_image` |
| Spandrel attribute access | Guessed | core/inference.py | Assumed `size_requirements`, `scale`, `in_channels`. | Create dedicated `model_adapter.py` validating Spandrel v0.4+ APIs. | `test_model_adapter_properties` |
| Device detection | Hard-coded | ui/main_window.py | Shows CUDA everywhere. | Implement `device_manager.py` checking `torch.mps.is_available()`, etc. | `test_device_detection` |
| Settings persistence | Missing | ui/main_window.py | No QSettings. | Use `QSettings` to restore last used safe settings. | `test_settings_persistence` |
| Output dimensions prediction | Missing | ui/main_window.py | No predicted size shown. | Calculate dynamically upon image/model selection. | GUI smoke test |
| Output format selector | Missing | ui/main_window.py | Determined by manual filename extension. | Add explicit format dropdown (PNG, JPG, TIFF). | GUI smoke test |
| Protocol types & UUIDs | Weak | protocol/messages.py | job_1 used everywhere, loose parsing. | Enforce UUIDs, structured dataclasses, and strict types. | `test_protocol_parsing` |
| Progress updates | Inaccurate | worker/server.py | Reports completion prematurely, missing active tile size. | Calculate ETA correctly, send actual tile size. | `test_worker_progress` |
