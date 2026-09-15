# Architecture Decision Record: 0001 - Application Architecture

Status: Superseded for the UI host by ADR 0002 and then ADR 0005. The isolated Python
inference-worker decision still stands.

## Context
We are building LocalSR, a desktop GUI for local image super-resolution using PyTorch and Spandrel. The primary constraint is running on Apple Silicon (MPS) with limited unified memory. PyTorch MPS inference for large images can easily exhaust unified memory, causing kernel panics, system instability, or application crashes.

## Options Considered
1. **Python + PySide6 (In-Process with QThread)**: GUI and PyTorch share a single process.
2. **Electron + Python Worker**: Web frontend with a Python backend communicating over IPC.
3. **Python + PySide6 + Isolated Python Worker (QProcess)**: Native-feeling Python GUI that spawns a separate Python process for PyTorch inference.

## Selected Architecture
**Python + PySide6 GUI with an Isolated Python Inference Worker (QProcess)** communicating via newline-delimited JSON.

## Reasons for the Selection
- **Crash Survivability**: If PyTorch exhausts memory and the OS kills the process (or PyTorch segfaults), only the worker dies. The GUI remains alive to report the error and can spawn a new worker.
- **Environment Isolation**: MPS memory limits (`PYTORCH_MPS_HIGH_WATERMARK_RATIO`) must be set before `torch` is imported. A separate worker allows the GUI to configure these variables dynamically per-job.
- **Memory Reclamation**: Terminating the worker guarantees 100% reclamation of PyTorch GPU memory caches, which are notoriously difficult to clear completely in a shared process.
- **Implementation Simplicity**: Using Python for both GUI and worker eliminates the need for Node.js, Electron, or Rust toolchains, keeping the project small and approachable.

## Trade-offs
- Requires designing a robust IPC protocol (JSON lines).
- Debugging requires monitoring two processes instead of one.
- Slightly higher latency for starting a job compared to an in-process thread.

## Consequences
- The GUI must robustly handle worker disconnections.
- Model loading must happen in the worker, meaning model metadata needs to be sent back to the GUI for display.
- Large images cannot be passed in memory; they must be written to disk or passed via shared memory. We will use disk-backed files.

## Conditions under which the decision should be revisited
- If JSON parsing over standard streams becomes a bottleneck for progress reporting (highly unlikely for tiling).
- If packaging PySide6 alongside PyTorch becomes prohibitively large (though this applies to Electron too).
