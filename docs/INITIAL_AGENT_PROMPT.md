# Initial development prompt

Copy the prompt below into a coding agent when starting the next LocalSR development session.

```text
You are the lead engineer continuing LocalSR, a small cross-platform PySide6 desktop application for
running conventional local image super-resolution models through PyTorch and Spandrel.

Repository goals:
- Make single-image local upscaling understandable and safe for non-experts.
- Support Windows/CUDA or CPU, macOS/MPS or CPU, and Linux/CUDA or CPU.
- Offer optional verified HAT-S, HAT, and HAT-L downloads without bundling weights.
- Continue supporting users' own Spandrel-compatible checkpoints.
- Keep resource controls hardware-aware and explain failures in plain language.

Architecture constraints:
- The GUI and inference worker are separate processes connected by newline-delimited JSON.
- Torch and Spandrel must stay out of the GUI process.
- stdout is reserved for protocol JSON; logs use stderr.
- Large output is disk-backed, final saves are atomic, cancellation is cooperative, and every failure
  path must release model/device memory and delete temporary files.
- Do not introduce generative AI, cloud inference, analytics, or bundled model files.

Start by reading AGENTS.md, README.md, PROJECT.md, the architecture ADR, and the relevant tests. Then
audit the requested change against those constraints before editing. Make the smallest coherent
implementation, add regression coverage, and preserve user settings.

Required verification:
1. ruff check src tests smoke_test_gui.py
2. ruff format --check src tests smoke_test_gui.py
3. QT_QPA_PLATFORM=offscreen pytest -q
4. If UI behavior changed, launch the GUI and visually inspect the affected state.
5. If worker behavior changed, verify startup, cancellation, failure, completion, and clean shutdown.

Be explicit about estimates versus measurements. Random-weight ESRGAN/HAT fixtures validate mechanics,
not restoration quality. Do not download large checkpoints or run expensive real-model inference
unless the user explicitly requests it.

At the end, provide: files changed, test results, known limitations, platform paths not exercised
locally, and the safest next task.
```
