#!/usr/bin/env python3
"""Verify that a PyAV/FFmpeg runtime or a packaged tree honours the LocalSR codec policy.

The authoritative allowlist is ``packaging/ffmpeg/codec-policy.json``.

Modes (at least one is required; both may be combined):

``--python-env PYTHON``
    Run a probe inside that interpreter (it must be able to ``import av``) and check
    ``av.codecs_available`` against the policy, the FFmpeg license string of every
    library, and every library's configure line.

``--tree PATH``
    Walk a frozen worker, app bundle, wheel-extracted directory (or a ``.whl``/``.zip``
    file) and fail on ``forbidden_file_patterns``. Any libavcodec shared library found is
    scanned for embedded GPL/nonfree configuration markers.

A JSON report is printed to stdout; the exit status is 0 when every check passes, 1 when
a policy check fails and 2 when the verifier could not run (bad arguments or policy).
Standard library only; ``av`` is only imported inside the probed interpreter.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "packaging" / "ffmpeg" / "codec-policy.json"

# Byte markers that must never appear in a distributed libavcodec.
LIBAVCODEC_FORBIDDEN_MARKERS = (b"--enable-gpl", b"--enable-nonfree", b"libx264", b"libx265")
LIBAVCODEC_PATTERN = re.compile(r"(^|/)(lib)?avcodec[^/]*\.(dll|so[^/]*|dylib)$")

PROBE = r"""
import json, sys
out = {"python": sys.version.split()[0], "executable": sys.executable}
try:
    import av
except Exception as exc:  # noqa: BLE001 - report every import failure
    out["error"] = f"import av failed: {type(exc).__name__}: {exc}"
    print(json.dumps(out))
    raise SystemExit(0)
out["av_version"] = getattr(av, "__version__", None)
out["av_file"] = getattr(av, "__file__", None)
out["codecs_available"] = sorted(av.codecs_available)
out["library_versions"] = {k: list(v) for k, v in av.library_versions.items()}
meta = None
try:
    from av import _core
    meta = getattr(_core, "library_meta", None)
except Exception:  # noqa: BLE001
    meta = None
if meta is None:
    meta = getattr(av, "library_meta", None)
if meta is not None:
    out["library_meta"] = {
        name: {
            "version": list(m.get("version", ())),
            "configuration": m.get("configuration"),
            "license": m.get("license"),
        }
        for name, m in meta.items()
    }
modes = {}
for name in out["codecs_available"]:
    entry = []
    for mode, label in (("r", "decoder"), ("w", "encoder")):
        try:
            av.codec.Codec(name, mode)
        except Exception:  # noqa: BLE001
            continue
        entry.append(label)
    modes[name] = entry
out["codec_modes"] = modes
print(json.dumps(out))
"""


@dataclass
class Failure:
    check: str
    message: str
    items: list[str]


class VerifierError(RuntimeError):
    """The verifier could not run (exit status 2)."""


def load_policy(path: Path) -> dict:
    try:
        policy = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise VerifierError(f"Cannot read codec policy {path}: {exc}") from exc
    required = (
        "expected_license",
        "configure_forbidden_flags",
        "decoders",
        "encoders",
        "forbidden_codec_patterns",
        "forbidden_file_patterns",
    )
    missing = [key for key in required if key not in policy]
    if missing:
        raise VerifierError(f"Codec policy {path} is missing keys: {missing}")
    return policy


def allowed_codec_names(policy: dict) -> set[str]:
    """Policy decoder/encoder names plus their runtime spellings (e.g. libvpx-vp9)."""
    names = set(policy["decoders"]) | set(policy["encoders"])
    runtime = policy.get("runtime_codec_names", {})
    return names | {runtime[name] for name in names if name in runtime}


def check_codecs(codecs: list[str] | set[str], policy: dict) -> list[Failure]:
    failures = []
    patterns = [re.compile(pattern) for pattern in policy["forbidden_codec_patterns"]]
    forbidden = []
    for name in sorted(codecs):
        hits = [pattern.pattern for pattern in patterns if pattern.search(name)]
        if hits:
            forbidden.append(f"{name} (matches {', '.join(hits)})")
    if forbidden:
        failures.append(
            Failure(
                "forbidden_codecs",
                f"{len(forbidden)} available codec(s) match forbidden_codec_patterns",
                forbidden,
            )
        )
    allowed = allowed_codec_names(policy)
    unknown = sorted(name for name in codecs if name not in allowed)
    if unknown:
        failures.append(
            Failure(
                "unlisted_codecs",
                f"{len(unknown)} available codec(s) are not in the policy decoders/encoders",
                unknown,
            )
        )
    return failures


def forbidden_configure_flags(configuration: str, policy: dict) -> list[str]:
    """Return forbidden flags present as whole tokens (``--flag`` or ``--flag=...``)."""
    flags = set(policy["configure_forbidden_flags"])
    found = []
    for token in configuration.split():
        token = token.strip("'\"")
        if token.split("=", 1)[0] in flags:
            found.append(token)
    return sorted(set(found))


def check_library_meta(meta: dict | None, policy: dict) -> list[Failure]:
    if not meta:
        return [
            Failure(
                "library_meta",
                "PyAV did not expose FFmpeg library metadata (av._core.library_meta)",
                [],
            )
        ]
    failures = []
    expected = policy["expected_license"]
    bad_license = sorted(
        f"{name}: {entry.get('license')!r}"
        for name, entry in meta.items()
        if entry.get("license") != expected
    )
    if bad_license:
        failures.append(
            Failure("license", f"FFmpeg library license is not {expected!r}", bad_license)
        )
    bad_flags = []
    for name, entry in sorted(meta.items()):
        configuration = entry.get("configuration")
        if configuration is None:
            bad_flags.append(f"{name}: configuration string unavailable")
            continue
        found = forbidden_configure_flags(configuration, policy)
        if found:
            bad_flags.append(f"{name}: {' '.join(found)}")
    if bad_flags:
        failures.append(
            Failure("configure_flags", "FFmpeg was configured with forbidden flags", bad_flags)
        )
    return failures


def evaluate_probe(probe: dict, policy: dict) -> tuple[list[Failure], dict]:
    """Check the JSON emitted by the probe script."""
    if "error" in probe:
        return [Failure("import", probe["error"], [])], {"python": probe.get("executable")}
    failures = check_codecs(probe.get("codecs_available", []), policy)
    failures += check_library_meta(probe.get("library_meta"), policy)
    details = {
        "python": probe.get("executable"),
        "av_version": probe.get("av_version"),
        "av_file": probe.get("av_file"),
        "library_versions": probe.get("library_versions"),
        "licenses": {
            name: entry.get("license") for name, entry in (probe.get("library_meta") or {}).items()
        },
        "configuration": (probe.get("library_meta") or {})
        .get("libavcodec", {})
        .get("configuration"),
        "codecs_available": probe.get("codecs_available"),
        "codec_modes": probe.get("codec_modes"),
    }
    return failures, details


def run_probe(python: str, timeout: int = 120) -> dict:
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    try:
        result = subprocess.run(
            [python, "-I", "-c", PROBE],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VerifierError(f"Cannot run probe with {python}: {exc}") from exc
    lines = [line for line in result.stdout.splitlines() if line.startswith("{")]
    if result.returncode != 0 or not lines:
        raise VerifierError(
            f"Probe failed in {python} (exit {result.returncode}): {result.stderr.strip()[-2000:]}"
        )
    return json.loads(lines[-1])


def scan_library_bytes(data: bytes) -> list[str]:
    """Return forbidden markers embedded in a libavcodec binary, with context."""
    found = []
    for marker in LIBAVCODEC_FORBIDDEN_MARKERS:
        index = data.find(marker)
        # "--disable-libx264" in a configuration string is not a use of x264.
        while index >= 0 and data[max(0, index - 10) : index] == b"--disable-":
            index = data.find(marker, index + 1)
        if index >= 0:
            start = max(0, index - 40)
            context = data[start : index + len(marker) + 40]
            printable = context.decode("latin-1").replace("\x00", " ")
            printable = re.sub(r"[^\x20-\x7e]", ".", printable)
            found.append(f"{marker.decode()} (context: {printable.strip()!r})")
    return found


def scan_tree(root: Path, policy: dict) -> tuple[list[Failure], dict]:
    patterns = [re.compile(pattern) for pattern in policy["forbidden_file_patterns"]]
    forbidden_files: list[str] = []
    library_hits: list[str] = []
    libavcodec_files: list[str] = []
    file_count = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for filename in sorted(filenames):
            path = Path(dirpath) / filename
            relative = path.relative_to(root).as_posix()
            file_count += 1
            hits = [pattern.pattern for pattern in patterns if pattern.search(relative)]
            if hits:
                forbidden_files.append(f"{relative} (matches {', '.join(hits)})")
            if LIBAVCODEC_PATTERN.search(relative) and not path.is_symlink():
                libavcodec_files.append(relative)
                try:
                    markers = scan_library_bytes(path.read_bytes())
                except OSError as exc:
                    markers = [f"unreadable: {exc}"]
                library_hits += [f"{relative}: {marker}" for marker in markers]
    failures = []
    if forbidden_files:
        failures.append(
            Failure(
                "forbidden_files",
                f"{len(forbidden_files)} file(s) match forbidden_file_patterns",
                forbidden_files,
            )
        )
    if library_hits:
        failures.append(
            Failure(
                "libavcodec_markers",
                "libavcodec embeds GPL/nonfree configuration or x264/x265 markers",
                library_hits,
            )
        )
    details = {"root": str(root), "files": file_count, "libavcodec": libavcodec_files}
    return failures, details


def verify_tree(path: Path, policy: dict) -> tuple[list[Failure], dict]:
    if not path.exists():
        raise VerifierError(f"--tree path does not exist: {path}")
    if path.is_file():
        if not zipfile.is_zipfile(path):
            raise VerifierError(f"--tree file is not a wheel/zip archive: {path}")
        with tempfile.TemporaryDirectory(prefix="codec-allowlist-") as temporary:
            with zipfile.ZipFile(path) as archive:
                archive.extractall(temporary)
            failures, details = scan_tree(Path(temporary), policy)
            details["root"] = str(path)
            return failures, details
    return scan_tree(path, policy)


def build_report(sections: dict[str, tuple[list[Failure], dict]], policy_path: Path) -> dict:
    failures = [
        {"mode": mode, **asdict(failure)}
        for mode, (mode_failures, _details) in sections.items()
        for failure in mode_failures
    ]
    return {
        "passed": not failures,
        "policy": str(policy_path),
        "failures": failures,
        "details": {mode: details for mode, (_failures, details) in sections.items()},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--python-env", metavar="PYTHON", help="Interpreter whose PyAV to probe")
    parser.add_argument("--tree", type=Path, help="Directory or wheel to scan for forbidden files")
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY, help="codec-policy.json")
    parser.add_argument("--output", type=Path, help="Also write the JSON report to this file")
    args = parser.parse_args(argv)
    if not args.python_env and not args.tree:
        parser.error("pass --python-env and/or --tree")
    try:
        policy = load_policy(args.policy)
        sections: dict[str, tuple[list[Failure], dict]] = {}
        if args.python_env:
            sections["python_env"] = evaluate_probe(run_probe(args.python_env), policy)
        if args.tree:
            sections["tree"] = verify_tree(args.tree, policy)
    except VerifierError as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, indent=2))
        print(f"error: {exc}", file=sys.stderr)
        return 2
    report = build_report(sections, args.policy)
    text = json.dumps(report, indent=2)
    print(text)
    if args.output:
        args.output.write_text(text + "\n")
    for failure in report["failures"]:
        print(f"FAIL [{failure['mode']}] {failure['check']}: {failure['message']}", file=sys.stderr)
        for item in failure["items"][:50]:
            print(f"    - {item}", file=sys.stderr)
        if len(failure["items"]) > 50:
            print(f"    ... {len(failure['items']) - 50} more", file=sys.stderr)
    print("PASS" if report["passed"] else "FAIL: codec allowlist violated", file=sys.stderr)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
