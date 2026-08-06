# Architecture Research

## Comparable Open-Source Projects

| Project | GUI Language/Framework | Inference Engine | Process Model | Notes |
|---------|------------------------|------------------|---------------|-------|
| **Upscayl** | Electron + TypeScript | NCNN (C++) | Separate Processes | GUI spawns binary executables for inference. Excellent isolation, highly portable due to ncnn. Does not directly use PyTorch/Spandrel. |
| **chaiNNer** | Electron + React | Python (PyTorch) | Separate Processes | Communicates with Python backend via sockets/IPC. Robust separation. Large install footprint due to Electron + Python environment. |
| **QualityScaler**| Python + CustomTkinter | Python (PyTorch) | Shared Process / Threads | Simple architecture. Easy to build, but inference crashes can bring down the UI. Memory leaks in PyTorch can destabilize the application. |
| **ComfyUI** | Web (HTML/JS) | Python (PyTorch) | Client/Server | Server-based architecture. Great flexibility for nodes, but feels less like a native desktop app. Inference runs in the server process. |

## Feature Comparison

- **Model Loading:** chaiNNer uses Spandrel to load models natively in Python. Upscayl requires models to be converted to NCNN/NCNN-vulkan bin/param format.
- **Tiled Inference:** chaiNNer supports advanced tiled inference via PyTorch in Python. Upscayl uses NCNN's built-in tiling or external handling.
- **Progress & Cancellation:** Separate process models (Upscayl, chaiNNer) handle cancellation gracefully by killing the worker or sending a signal. QualityScaler relies on thread interruption, which is unsafe in PyTorch.
- **GPU Selection:** PyTorch handles MPS/CUDA internally, but MPS environment variables must be set before PyTorch initialization.

## Architecture Options for LocalSR

1. **Python + PySide6 (Single Process)**: Fast to build, but risky for OOM on MPS.
2. **Electron + TS + Python Worker**: Excellent isolation, but two languages and heavy dependencies.
3. **Tauri + Rust + Python Worker**: Smaller than Electron, but complex build system.
4. **Python + PySide6 + Python Worker (Subprocess)**: Keeps the language consistent (Python) while providing process isolation.

## Recommendation for LocalSR

I recommend **Python + PySide6 with a separate Python Inference Worker**.
- Provides the process isolation needed to survive MPS OOM crashes.
- Allows setting `PYTORCH_MPS_HIGH_WATERMARK_RATIO` on the worker before importing `torch`.
- Keeps the codebase in a single language, drastically simplifying development and future packaging.
