# E2E Test Infra: LocalSR

## Test Philosophy
- Opaque-box, requirement-driven testing for requirements R1 through R5.
- Methodology: Category-Partition + BVA + Pairwise + Real-World Workload Testing across 4 Tiers.

## Feature Inventory & Test Coverage
| # | Requirement | Source | Tier 1 | Tier 2 | Tier 3 | Tier 4 |
|---|-------------|--------|:------:|:------:|:------:|:------:|
| R1 | Connect Output Saving | ORIGINAL_REQUEST §R1 | ✓ | ✓ | ✓ | ✓ |
| R2 | Repair GUI Protocol | ORIGINAL_REQUEST §R2 | ✓ | ✓ | ✓ | ✓ |
| R3 | Fix Worker Shutdown | ORIGINAL_REQUEST §R3 | ✓ | ✓ | ✓ | ✓ |
| R4 | Integration Test Runner | ORIGINAL_REQUEST §R4 | ✓ | ✓ | ✓ | ✓ |
| R5 | Real Model Verification | ORIGINAL_REQUEST §R5 | ✓ | ✓ | ✓ | ✓ |

## Test Architecture
- Test runner: `.venv/bin/pytest tests/test_e2e_requirements.py -v`
- Test suite file: `tests/test_e2e_requirements.py`
- Test fixtures: Pytest `tmp_path` fixtures for outputs/memmaps, subprocess worker management, PIL synthetic image generators.

## Coverage Thresholds
- Tier 1: 5 tests (R1 atomic save, R2 protocol signals, R3 clean exit, R4 worker integration, R5 model verification).
- Tier 2: 5 boundary tests (zero quality, unknown protocol msgs, stdin EOF, odd dimensions, invalid model path).
- Tier 3: 3 cross-feature tests (cancel then restart, multi-job sequence, invalid model then shutdown).
- Tier 4: 2 real-world workload tests (synthetic tiling test, 500x500 photo tiling verification).
