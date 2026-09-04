import hashlib
import json
import sys
from pathlib import Path

# Provide a fallback import for tests
try:
    from verify_macho_tree import macho_arches
except ImportError:
    from scripts.verify_macho_tree import macho_arches


def filter_binaries(binaries: list, target_arch: str, report_path: Path) -> list:
    if sys.platform != "darwin" or target_arch not in ("arm64", "x86_64"):
        return binaries

    filtered = []
    removed = []

    for dest, src, kind in binaries:
        if kind == "EXTENSION" or dest.endswith(".dylib") or dest.endswith(".so"):
            try:
                with open(src, "rb") as f:
                    header = f.read(4096)
                    archs = macho_arches(header)
            except Exception as e:
                raise ValueError(f"Failed to inspect Mach-O architectures for {src}: {e}") from e

            if archs is not None and target_arch not in archs:
                # We found a binary that lacks the target architecture!
                # Is it an OpenCV dependency? We only prune proven-unreferenced OpenCV deps.
                if "/cv2/" not in src.replace("\\", "/"):
                    raise ValueError(
                        f"Target-incompatible binary found outside of OpenCV scope: {src}. "
                        f"It has architectures {archs} but target is {target_arch}. "
                        "Pruning is restricted to proven OpenCV dependencies to fail closed."
                    )

                with open(src, "rb") as f:
                    digest = hashlib.sha256(f.read()).hexdigest()

                removed.append(
                    {
                        "dest": dest,
                        "src": src,
                        "architectures": sorted(list(archs)),
                        "digest": digest,
                        "reason": f"Mismatched architecture: {archs} does not contain {target_arch}",
                    }
                )
                continue

        filtered.append((dest, src, kind))

    if removed:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(removed, indent=2) + "\n", encoding="utf-8")

    return filtered
