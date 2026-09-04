import subprocess
from pathlib import Path


def thin_binaries(directory: Path, target_arch: str) -> None:
    if target_arch not in ("arm64", "x86_64"):
        return

    for path in directory.rglob("*"):
        if path.is_file() and (path.suffix in (".dylib", ".so") or not path.suffix):
            # Check if it's a Mach-O file
            try:
                with open(path, "rb") as f:
                    magic = f.read(4)
                if magic not in (b"\xca\xfe\xba\xbe", b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe"):
                    continue
            except Exception:
                continue

            # Check if it's already thin or if it contains multiple architectures
            try:
                out = subprocess.check_output(["lipo", "-info", str(path)], text=True)
                if (
                    "Non-fat file" in out
                    or f"architecture: {target_arch}" in out
                    and "Architectures in the fat file" not in out
                ):
                    continue
            except subprocess.CalledProcessError:
                continue

            print(f"Thinning {path.name} to {target_arch}...")
            subprocess.run(
                ["lipo", str(path), "-thin", target_arch, "-output", str(path)], check=True
            )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("target_arch", type=str)
    args = parser.parse_args()
    thin_binaries(args.directory, args.target_arch)
