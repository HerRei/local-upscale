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


def test_a_cut_off_download_leaves_nothing_behind_and_the_next_run_fetches_it(
    tmp_path, monkeypatch
):
    import hashlib

    content = b"the whole package"
    asset = {"name": "a.tar.gz", "digest": "sha256:" + hashlib.sha256(content).hexdigest()}
    attempts = []

    def fake_gh(*arguments):
        folder = Path(arguments[arguments.index("-D") + 1])
        attempts.append(folder)
        if len(attempts) == 1:
            (folder / "a.tar.gz").write_bytes(b"")  # gh created the file, then was cut off
            raise SystemExit("download interrupted")
        (folder / "a.tar.gz").write_bytes(content)
        return ""

    monkeypatch.setattr(fetch, "gh", fake_gh)
    try:
        fetch.fetch_assets("v0", tmp_path, [asset], "x/y")
    except SystemExit:
        pass
    assert sorted(p.name for p in tmp_path.iterdir()) == []
    assert fetch.fetch_assets("v0", tmp_path, [asset], "x/y") == 1
    assert (tmp_path / "a.tar.gz").read_bytes() == content


def test_a_download_with_the_wrong_digest_is_not_kept(tmp_path, monkeypatch):
    asset = {"name": "a.tar.gz", "digest": "sha256:" + "0" * 64}

    def fake_gh(*arguments):
        (Path(arguments[arguments.index("-D") + 1]) / "a.tar.gz").write_bytes(b"other")
        return ""

    monkeypatch.setattr(fetch, "gh", fake_gh)
    try:
        fetch.fetch_assets("v0", tmp_path, [asset], "x/y")
    except SystemExit as error:
        assert "does not match" in str(error)
    else:
        raise AssertionError("a download with the wrong digest was accepted")
    assert not (tmp_path / "a.tar.gz").exists()
