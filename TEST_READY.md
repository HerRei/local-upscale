# E2E Test Suite Ready

## Test Runner
- Full suite command: `.venv/bin/python -m pytest -q`
- Lint command: `.venv/bin/ruff check src tests`
- Verification status (2026-08-07): **93 passed, 1 skipped**; Ruff clean.
- The skip is an optional trusted external pretrained-checkpoint test. Real Spandrel ESRGAN/HAT mechanics are covered with generated lightweight checkpoints.

## Coverage Summary
| Tier | Count | Description |
|------|------:|-------------|
| 1. Feature Coverage | 5 | R1 atomic save, R2 protocol signals, R3 clean shutdown, R4 worker IPC, R5 HAT model |
| 2. Boundary & Corner | 5 | Zero quality, unknown protocol msgs, stdin EOF, odd dimensions, invalid model path |
| 3. Cross-Feature | 3 | Cancel then restart, multi-job sequential, invalid model then shutdown |
| 4. Real-World Application | 2 | Synthetic image tiling, 500x500 photograph tiling |
| **Requirement suite subtotal** | **15** | Additional unit, stress, architecture, subprocess, GUI, catalog, checksum, estimate, and hardware-control tests bring the full suite to 94 tests. |

## Feature Checklist
| Feature / Requirement | Tier 1 | Tier 2 | Tier 3 | Tier 4 |
|-----------------------|:------:|:------:|:------:|:------:|
| R1: Connect Output Saving | ✓ | ✓ | ✓ | ✓ |
| R2: Repair GUI Protocol | ✓ | ✓ | ✓ | ✓ |
| R3: Fix Worker Shutdown | ✓ | ✓ | ✓ | ✓ |
| R4: Genuine Integration Test | ✓ Full subprocess and GUI vertical slices | ✓ | ✓ | ✓ |
| R5: Model Architecture Verification | ✓ Real Spandrel HAT descriptor and inference | ✓ | ✓ | ✓ |
| R6: Catalog & Resource-Aware UX | ✓ Three pinned downloads plus custom files | ✓ Hash and partial-file failure paths | ✓ Worker capability IPC and constrained controls | ✓ Conservative preflight guidance |

## Quality Boundary

The generated ESRGAN and HAT checkpoints exercise the production Spandrel code path but have random weights. Visual-quality acceptance still requires a trusted pretrained checkpoint supplied by the user.
