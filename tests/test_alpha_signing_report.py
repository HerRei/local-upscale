from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "write_alpha_signing_report", ROOT / "scripts" / "write_alpha_signing_report.py"
)
assert SPEC and SPEC.loader
signing = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(signing)


def test_alpha_reports_never_claim_production_signing() -> None:
    macos = signing.report("macos")
    windows = signing.report("windows")

    assert macos["status"] == "ad-hoc-alpha"
    assert macos["production_signed"] is False
    assert macos["notarized"] is False
    assert macos["gatekeeper_accepted"] is False
    assert windows["status"] == "unsigned-alpha"
    assert windows["production_signed"] is False
    assert windows["authenticode"] is False
    assert macos["warning"] and windows["warning"]
