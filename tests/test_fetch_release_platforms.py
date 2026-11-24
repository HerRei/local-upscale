"""Issue #180: signing gathers packages from the draft release and reports from small artifacts."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import fetch_release_platforms as fetch  # noqa: E402

V = "LocalSR-v0.6.1-beta"


def test_reports_name_exactly_their_target_packages(tmp_path):
    for name in (
        f"{V}-Windows-CPU-x86_64.exe.smoke.json",
        f"{V}-Windows-CPU-x86_64.exe.sha256",
        f"{V}-Windows-CPU-x86_64.engine-payload.json",
        f"{V}-Windows-CPU-source-and-notices.tar.gz.json",
        "dependency-wheelhouse.json",
    ):
        (tmp_path / name).write_text("{}")
    assets = [
        {"name": n}
        for n in (
            f"{V}-Windows-CPU-x86_64.exe",
            f"{V}-Windows-CPU-x86_64.exe.sha256",
            f"{V}-Windows-CPU-x86_64.exe.sig",
            f"{V}-Windows-CPU-x86_64.engine.tar.gz.part-0001",
            f"{V}-Windows-CPU-x86_64.engine-update.json",
            f"{V}-Windows-CPU-source-and-notices.tar.gz",
            f"{V}-Windows-CUDA-x86_64.exe",
            f"{V}-Windows-CUDA-x86_64.engine.tar.gz.part-0001",
            f"{V}-macOS-arm64.dmg",
        )
    ]
    names = sorted(a["name"] for a in fetch.assets_for(fetch.package_stems(tmp_path), assets))
    assert names == sorted(
        [
            f"{V}-Windows-CPU-x86_64.exe",
            f"{V}-Windows-CPU-x86_64.exe.sha256",
            f"{V}-Windows-CPU-x86_64.engine.tar.gz.part-0001",
            f"{V}-Windows-CPU-x86_64.engine-update.json",
            f"{V}-Windows-CPU-source-and-notices.tar.gz",
        ]
    )


def test_a_report_copy_that_differs_from_the_release_is_refused(tmp_path):
    (tmp_path / "a.exe.sha256").write_text("abc  a.exe\n")
    asset = {"name": "a.exe.sha256", "digest": "sha256:" + "0" * 64}
    try:
        fetch.fetch_assets("v0", tmp_path, [asset], "x/y")
    except SystemExit as error:
        assert "differs" in str(error)
    else:
        raise AssertionError("a differing checksum file was accepted")
