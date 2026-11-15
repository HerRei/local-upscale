#!/usr/bin/env python
"""Export the two LocalSR mobile models and check them against PyTorch FP32.

    export_models.py            convert both checkpoints, write mobile/Models/
    export_models.py --check    verify the files in mobile/Models/ numerically

Run it with the model-library Python (torch, coremltools, spandrel):

    /Users/hermesheiniger/LocalSR-model-library/.venv/bin/python mobile/tools/export_models.py

Core ML runs in that interpreter. LiteRT conversion and the TFLite check run in a second
interpreter, mobile/tools/.venv-litert (litert-torch needs its own torch); this script creates
it with uv on first use and calls itself there through the hidden --litert-worker mode.
See README.md and mobile/DESIGN.md ("Models", "Device check").
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.dont_write_bytecode = True  # never write __pycache__ into the read-only source trees

TOOLS = Path(__file__).resolve().parent
MODELS_DIR = TOOLS.parent / "Models"
LITERT_VENV = TOOLS / ".venv-litert"
LITERT_PYTHON = LITERT_VENV / "bin" / "python"

TRAIN_SRC = Path(os.environ.get("LOCALSR_TRAIN", "/Users/hermesheiniger/Projects/LocalSR/localsr-train"))
ENGINE_SRC = Path(os.environ.get("LOCALSR_SRC", "/Users/hermesheiniger/Projects/LocalSR/LocalSR-038-integ/src"))
CANDIDATES = TRAIN_SRC / "candidates"

SIDE = 384
SCALE = 4
OUT_SIDE = SIDE * SCALE

# key -> (UI name, checkpoint stem, arch label)
MODELS = {
    "best": ("Best", "localsr_photo_pulse48_v1_x4", "PULSE-48"),
    "fast": ("Fast", "localsr_photo_span28_v1_x4", "SPAN-28"),
}

# Thresholds from DESIGN.md "Device check" and the task: CPU paths must be near-exact,
# the Neural Engine (FP16) is only held to the 45 dB bar the app uses at run time.
MIN_PSNR_CPU = 55.0
MIN_PSNR_ANE = 45.0


# ----------------------------------------------------------------------------- shared helpers

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_tree(path: Path) -> str:
    """Hash of a directory (.mlmodelc): sorted relative names and file contents."""
    h = hashlib.sha256()
    for p in sorted(q for q in path.rglob("*") if q.is_file()):
        h.update(p.relative_to(path).as_posix().encode() + b"\0")
        h.update(sha256_file(p).encode() + b"\0")
    return h.hexdigest()


def size_kb(path: Path) -> float:
    if path.is_dir():
        return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) / 1024
    return path.stat().st_size / 1024


def probe_array(side: int = SIDE):
    """Deterministic probe: smooth waves, noisy gradient, checkerboard (see export_tiles.py)."""
    import numpy as np

    y, x = np.mgrid[0:side, 0:side].astype(np.float32) / side
    rng = np.random.default_rng(3)
    r = 0.5 + 0.4 * np.sin(18 * x) * np.cos(11 * y)
    g = np.clip((x + y) / 2 + 0.05 * rng.standard_normal((side, side)), 0, 1)
    b = ((np.floor(x * 12) + np.floor(y * 12)) % 2) * 0.8 + 0.1
    return np.stack([r, g, b], axis=-1)  # HWC float in [0, 1]


def write_probe_png(path: Path) -> None:
    import numpy as np
    from PIL import Image

    img = Image.fromarray(np.round(probe_array() * 255).astype(np.uint8), "RGB")
    img.save(path, optimize=False)


def load_probe(path: Path):
    """probe.png decoded exactly as the app decodes it: uint8 / 255. Returns NCHW float32."""
    import numpy as np
    from PIL import Image

    a = np.asarray(Image.open(path).convert("RGB"), dtype=np.float32) / 255.0
    assert a.shape == (SIDE, SIDE, 3), a.shape
    return a.transpose(2, 0, 1)[None].copy()


def psnr(ref, test) -> float:
    import numpy as np

    mse = np.mean((np.asarray(ref, np.float64) - np.asarray(test, np.float64)) ** 2)
    return float("inf") if mse == 0 else float(10 * np.log10(1.0 / mse))


def load_torch_model(key: str):
    """Spandrel model on CPU, FP32, branch-fused for export (prepare_export_model)."""
    sys.path.insert(0, str(TRAIN_SRC))
    sys.path.insert(0, str(ENGINE_SRC))
    import spandrel
    from localsr.core.onnx_runtime import prepare_export_model
    from localsr_train.spandrel_pulse import register

    register()
    desc = spandrel.ModelLoader().load_from_file(CANDIDATES / f"{MODELS[key][1]}.safetensors")
    model = desc.model.cpu().float().eval()
    prepare_export_model(model, desc.architecture.name)
    return model


def torch_reference(key: str, tile_nchw):
    import torch

    model = load_torch_model(key)
    with torch.no_grad():
        return model(torch.from_numpy(tile_nchw)).numpy()  # [1,3,1536,1536] float32


def timed(fn, runs: int = 5):
    """Median milliseconds per call after one warm-up call; returns (ms, last result)."""
    out = fn()
    times = []
    for _ in range(runs):
        t = time.perf_counter()
        out = fn()
        times.append((time.perf_counter() - t) * 1000)
    times.sort()
    return times[len(times) // 2], out


# ----------------------------------------------------------------------------- Core ML (iOS)

def export_coreml(key: str, dest: Path) -> None:
    import coremltools as ct
    import numpy as np
    import torch

    model = load_torch_model(key)
    x = torch.from_numpy(load_probe(MODELS_DIR / "probe.png"))
    with torch.no_grad():
        traced = torch.jit.trace(model, x)
    # FP16 input and output tensors (iOS16+): no cast ops remain on the CPU around the ANE graph.
    ml = ct.convert(
        traced,
        convert_to="mlprogram",
        inputs=[ct.TensorType(name="image", shape=(1, 3, SIDE, SIDE), dtype=np.float16)],
        outputs=[ct.TensorType(name="out", dtype=np.float16)],
        compute_precision=ct.precision.FLOAT16,
        minimum_deployment_target=ct.target.iOS17,
    )
    stem = f"localsr_{key}"
    with tempfile.TemporaryDirectory(prefix="localsr-coreml-") as tmp:
        pkg = Path(tmp) / f"{stem}.mlpackage"
        ml.save(str(pkg))
        # Never W8 linear quantisation: it produces garbage on the Neural Engine (DESIGN.md).
        subprocess.run(["xcrun", "coremlcompiler", "compile", str(pkg), tmp], check=True,
                       stdout=subprocess.DEVNULL)
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(Path(tmp) / f"{stem}.mlmodelc", dest)
    del ml, traced, model


def check_coreml(key: str, path: Path, tile, ref) -> list[dict]:
    import coremltools as ct
    import numpy as np

    # The Python API hands FP16 outputs back as float32, so read the real I/O types from the
    # compiled model's metadata.json.
    meta = json.loads((path / "metadata.json").read_text())[0]
    schema = ([(s["name"], s["dataType"], json.loads(s["shape"])) for s in meta["inputSchema"]],
              [(s["name"], s["dataType"], json.loads(s["shape"])) for s in meta["outputSchema"]])
    assert schema == ([("image", "Float16", [1, 3, SIDE, SIDE])],
                      [("out", "Float16", [1, 3, OUT_SIDE, OUT_SIDE])]), schema
    rows = []
    x16 = tile.astype(np.float16)
    units = [("CPU", ct.ComputeUnit.CPU_ONLY, MIN_PSNR_CPU),
             ("ANE", ct.ComputeUnit.CPU_AND_NE, MIN_PSNR_ANE),
             ("GPU", ct.ComputeUnit.CPU_AND_GPU, None)]  # GPU: reported only
    for unit, cu, floor in units:
        t0 = time.perf_counter()
        m = ct.models.CompiledMLModel(str(path), compute_units=cu)
        load_ms = (time.perf_counter() - t0) * 1000
        ms, res = timed(lambda: m.predict({"image": x16})["out"], runs=5 if unit != "CPU" else 3)
        assert res.shape == (1, 3, OUT_SIDE, OUT_SIDE), res.shape
        rows.append(row(key, "Core ML", unit, res, ref, ms, path, floor, load_ms))
        del m
    return rows


# ----------------------------------------------------------------------------- LiteRT (Android)

def export_litert(key: str, dest: Path) -> None:
    """Runs inside .venv-litert. PyTorch NCHW module -> NHWC float32 I/O -> FP32 tflite -> FP16 weights."""
    import litert_torch
    import torch
    from ai_edge_quantizer import quantizer
    from ai_edge_quantizer.algorithm_manager import AlgorithmName
    from ai_edge_quantizer.recipe_manager import RecipeManager

    model = load_torch_model(key)
    nhwc = litert_torch.to_channel_last_io(model, args=[0], outputs=[0]).eval()
    sample = torch.rand(1, SIDE, SIDE, 3)
    with tempfile.TemporaryDirectory(prefix="localsr-litert-") as tmp:
        fp32 = Path(tmp) / "fp32.tflite"
        litert_torch.convert(nhwc, (sample,)).export(str(fp32))
        # Weight-only FLOAT_CASTING = FP16 constants + DEQUANTIZE ops; activations stay FP32 so the
        # model keeps float32 I/O. The GPU/NPU delegates fold the dequantize and compute in FP16.
        rm = RecipeManager()
        rm.add_weight_only_config(regex=".*", operation_name="*", num_bits=16,
                                  algorithm_key=AlgorithmName.FLOAT_CASTING)
        q = quantizer.Quantizer(str(fp32))
        q.load_quantization_recipe(rm.get_quantization_recipe())
        q.quantize().export_model(str(dest), overwrite=True)


def check_litert(key: str, path: Path, tile, ref) -> list[dict]:
    import numpy as np
    from ai_edge_litert.interpreter import Interpreter

    it = Interpreter(model_path=str(path), num_threads=4)
    it.allocate_tensors()
    i, o = it.get_input_details()[0], it.get_output_details()[0]
    assert list(i["shape"]) == [1, SIDE, SIDE, 3] and i["dtype"] == np.float32, i
    assert list(o["shape"]) == [1, OUT_SIDE, OUT_SIDE, 3] and o["dtype"] == np.float32, o
    n16 = sum(1 for t in it.get_tensor_details() if t["dtype"] == np.float16)
    assert n16 > 0, "no FP16 weight tensors in the model"
    nhwc = tile.transpose(0, 2, 3, 1).copy()

    def run():
        it.set_tensor(i["index"], nhwc)
        it.invoke()
        return it.get_tensor(o["index"]).copy()

    ms, res = timed(run, runs=3)
    return [row(key, "LiteRT", "CPU", res.transpose(0, 3, 1, 2), ref, ms, path, MIN_PSNR_CPU)]


# ----------------------------------------------------------------------------- check table

def row(key, fmt, unit, out, ref, ms, path, floor, load_ms=None) -> dict:
    import numpy as np

    out = np.asarray(out)
    finite = bool(np.isfinite(out).all())
    p = psnr(ref, out) if finite else float("nan")
    ok = finite and float(out.astype("float32").std()) > 1e-3
    if floor is not None:
        ok = ok and p >= floor
    return {"model": key, "format": fmt, "unit": unit, "psnr": p, "ms": ms,
            "kb": size_kb(path), "floor": floor, "ok": bool(ok), "load_ms": load_ms}


def print_table(rows: list[dict]) -> None:
    head = f"{'model':<6}{'format':<9}{'unit':<6}{'PSNR dB':>9}{'ms/tile':>10}{'size KB':>10}  status"
    print(head)
    print("-" * len(head))
    for r in rows:
        status = "info" if r["floor"] is None else ("ok" if r["ok"] else f"FAIL (< {r['floor']:g} dB)")
        if r["floor"] is None and not r["ok"]:
            status = "info (bad output)"
        print(f"{r['model']:<6}{r['format']:<9}{r['unit']:<6}{r['psnr']:>9.2f}{r['ms']:>10.1f}"
              f"{r['kb']:>10.0f}  {status}")


# ----------------------------------------------------------------------------- notices, manifest

def write_notice(dest: Path) -> None:
    pulse = json.loads((CANDIDATES / "localsr_photo_pulse48_v1_x4.json").read_text())
    span = json.loads((CANDIDATES / "localsr_photo_span28_v1_x4.json").read_text())
    mit = (CANDIDATES / "PULSE-pretrain-LICENSE.txt").read_text().strip()
    parts = [
        "LocalSR model notices",
        "=====================",
        "",
        "Best (localsr_photo_pulse48_v1_x4): PULSE-48 v1",
        "----------------------------------------------",
        pulse["notice"],
        "",
        f"Training chain: {pulse['training_chain']}",
        f"Training data: {', '.join(pulse['all_datasets'])} (CC0 only).",
        "",
        "PULSE-pretrain-LICENSE.txt:",
        "",
        mit,
        "",
        "Fast (localsr_photo_span28_v1_x4): SPAN-28 v1",
        "---------------------------------------------",
        "Trained by the LocalSR project from scratch (no upstream weights).",
        f"Training chain: {span['training_chain']}",
        f"Training data: {', '.join(span['all_datasets'])} (CC0 only).",
        f"Licence note: {span['licence_note']}.",
        "",
    ]
    dest.write_text("\n".join(parts))


def tool_versions() -> dict:
    import coremltools
    import torch

    v = {"coremltools": coremltools.__version__, "torch": torch.__version__}
    if LITERT_PYTHON.exists():
        code = ("from importlib.metadata import version as v;"
                "print(v('litert-torch'), v('ai-edge-litert'), v('ai-edge-quantizer'))")
        a, b, c = subprocess.run([str(LITERT_PYTHON), "-c", code], capture_output=True, text=True,
                                 check=True).stdout.split()
        v.update({"litert-torch": a, "ai-edge-litert": b, "ai-edge-quantizer": c})
    return v


def write_manifest(dest: Path) -> None:
    models = {}
    for key, (ui, stem, arch) in MODELS.items():
        ckpt = CANDIDATES / f"{stem}.safetensors"
        ios, andr = MODELS_DIR / "ios" / f"localsr_{key}.mlmodelc", MODELS_DIR / "android" / f"localsr_{key}.tflite"
        models[key] = {
            "name": ui,
            "arch": arch,
            "scale": SCALE,
            "source_checkpoint": ckpt.name,
            "source_sha256": sha256_file(ckpt),
            "ios": {
                "file": f"ios/{ios.name}",
                "sha256": sha256_tree(ios),  # tree hash: sorted relative names + file sha256s
                "layout": "NCHW",
                "input": {"name": "image", "dtype": "float16", "shape": [1, 3, SIDE, SIDE]},
                "output": {"name": "out", "dtype": "float16", "shape": [1, 3, OUT_SIDE, OUT_SIDE]},
                "weights": "float16",
                "minimum_deployment_target": "iOS17",
            },
            "android": {
                "file": f"android/{andr.name}",
                "sha256": sha256_file(andr),
                "layout": "NHWC",
                "input": {"dtype": "float32", "shape": [1, SIDE, SIDE, 3]},
                "output": {"dtype": "float32", "shape": [1, OUT_SIDE, OUT_SIDE, 3]},
                "weights": "float16",
            },
        }
    manifest = {
        "tile": SIDE,
        "scale": SCALE,
        "probe": {"file": "probe.png", "sha256": sha256_file(MODELS_DIR / "probe.png")},
        "notice": {"file": "NOTICE.txt", "sha256": sha256_file(MODELS_DIR / "NOTICE.txt")},
        "tools": tool_versions(),
        "models": models,
    }
    dest.write_text(json.dumps(manifest, indent=2) + "\n")


# ----------------------------------------------------------------------------- litert venv + worker

def ensure_litert_venv() -> None:
    if LITERT_PYTHON.exists():
        return
    uv = shutil.which("uv")
    if not uv:
        sys.exit("uv not found: install it (brew install uv) or create mobile/tools/.venv-litert "
                 "yourself (Python 3.11, pip install litert-torch spandrel==0.4.2)")
    subprocess.run([uv, "venv", "--python", "3.11", str(LITERT_VENV)], check=True)
    subprocess.run([uv, "pip", "install", "--python", str(LITERT_PYTHON), "litert-torch",
                    "spandrel==0.4.2"], check=True)


def call_worker(*args: str) -> None:
    ensure_litert_venv()
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    subprocess.run([str(LITERT_PYTHON), str(Path(__file__).resolve()), "--litert-worker", *args],
                   check=True, env=env)


def worker(argv: list[str]) -> None:
    mode, key = argv[0], argv[1]
    if mode == "export":
        export_litert(key, Path(argv[2]))
    elif mode == "check":
        tile = load_probe(MODELS_DIR / "probe.png")
        rows = check_litert(key, Path(argv[2]), tile, torch_reference(key, tile))
        Path(argv[3]).write_text(json.dumps(rows))
    else:
        raise SystemExit(f"unknown worker mode {mode}")


# ----------------------------------------------------------------------------- commands

def do_export() -> None:
    (MODELS_DIR / "ios").mkdir(parents=True, exist_ok=True)
    (MODELS_DIR / "android").mkdir(parents=True, exist_ok=True)
    write_probe_png(MODELS_DIR / "probe.png")
    write_notice(MODELS_DIR / "NOTICE.txt")
    for key in MODELS:  # one model at a time keeps RAM low
        print(f"[{key}] Core ML ...", flush=True)
        export_coreml(key, MODELS_DIR / "ios" / f"localsr_{key}.mlmodelc")
        print(f"[{key}] LiteRT ...", flush=True)
        call_worker("export", key, str(MODELS_DIR / "android" / f"localsr_{key}.tflite"))
    write_manifest(MODELS_DIR / "manifest.json")
    print("wrote", MODELS_DIR)


def do_check() -> int:
    probe = MODELS_DIR / "probe.png"
    if not probe.exists():
        sys.exit("mobile/Models/probe.png missing: run export_models.py first")
    tile = load_probe(probe)
    rows = []
    for key in MODELS:
        print(f"[{key}] PyTorch FP32 reference (CPU) ...", flush=True)
        ref = torch_reference(key, tile)
        rows += check_coreml(key, MODELS_DIR / "ios" / f"localsr_{key}.mlmodelc", tile, ref)
        del ref
        with tempfile.TemporaryDirectory(prefix="localsr-check-") as tmp:
            out = Path(tmp) / "rows.json"
            call_worker("check", key, str(MODELS_DIR / "android" / f"localsr_{key}.tflite"), str(out))
            rows += json.loads(out.read_text())
    print()
    print_table(rows)
    bad = [r for r in rows if r["floor"] is not None and not r["ok"]]
    print("\nCHECK", "FAILED" if bad else "PASSED")
    return 1 if bad else 0


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--tablet-worker":
        import tablet_models
        tablet_models.worker(*sys.argv[2:])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "--litert-worker":
        worker(sys.argv[2:])
        return
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="verify mobile/Models against PyTorch FP32")
    ap.add_argument("--set", choices=("phone", "tablet"), default="phone")
    args = ap.parse_args()
    if args.set == "tablet":
        import tablet_models
        sys.exit(tablet_models.run(args.check))
    sys.exit(do_check() if args.check else (do_export() or 0))


if __name__ == "__main__":
    main()
