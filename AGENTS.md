# LocalSR Agent Instructions

Read `README.md`, `PROJECT.md`, and `docs/adr/0001-application-architecture.md` before making broad
changes.

Non-negotiable constraints:

- LocalSR performs conventional local super-resolution; do not add generative synthesis.
- Do not import Torch or Spandrel in the GUI process. Hardware/model work belongs in the worker.
- Keep IPC as newline-delimited JSON on stdout and send diagnostics to stderr.
- Preserve atomic saves, disk-backed output, cancellation, worker isolation, and cleanup on failure.
- Never bundle model weights. Curated downloads must remain optional, pinned, and hash-verified.
- Resource estimates must communicate uncertainty and may only become precise from measured local
  runs.
- Maintain Windows, macOS, and Linux behavior. Avoid platform shell commands in automated tests.
- Use Python 3.11, type new protocol fields, add regression tests, and run Ruff plus the full suite.

Before handing off, report what changed, exact validation results, untested platform/hardware paths,
and the safest next step. Never claim visual-quality validation from random-weight test fixtures.
