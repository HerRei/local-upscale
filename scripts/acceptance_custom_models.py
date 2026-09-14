#!/usr/bin/env python3
"""Check custom checkpoint loading and recovery through an actual worker."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
import torch
from acceptance_worker import Worker
from PIL import Image
from safetensors.torch import save_file

from localsr.core.model_adapter import ModelAdapter
from localsr.core.model_catalog import CATALOG_BY_ID, file_matches_checksum
from localsr.protocol.messages import JobRequest


def verify(executable, model_root, report_path, devices, source_root=None):
    model = CATALOG_BY_ID["span_photo_x4"]
    checkpoint = model_root / model.filename
    if not file_matches_checksum(checkpoint, model.size_bytes, model.sha256):
        raise ValueError("The acceptance fixture must be the verified SPAN checkpoint")
    if os.environ.get("LOCALSR_ALLOW_UNVERIFIED_CHECKPOINTS"):
        raise ValueError("Run acceptance without the unsafe checkpoint override")
    report = {"passed": False, "packaged_worker": source_root is None, "checks": []}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="custom-model-acceptance-") as temporary:
        root = Path(temporary)
        adapter = ModelAdapter(allow_unverified_checkpoints=False)
        adapter.inspect(str(checkpoint))
        custom = root / "custom model.safetensors"
        save_file(
            {
                k: v.detach().cpu().contiguous()
                for k, v in adapter.parsed.model.state_dict().items()
            },
            custom,
        )
        del adapter
        image = root / "odd input.png"
        pixels = np.broadcast_to(np.arange(65, dtype=np.uint8)[None, :, None] + 70, (47, 65, 3))
        Image.fromarray(pixels.copy()).save(image)
        corrupt = root / "corrupt.safetensors"
        corrupt.write_bytes(b"not a checkpoint")
        unverified = root / "unverified.pth"
        shutil.copyfile(checkpoint, unverified)
        worker = Worker(executable, root, source_root=source_root)
        outputs = []

        def send(path, device, name):
            output = root / f"{name}.png"
            request = JobRequest(
                job_id=name,
                image_path=str(image),
                model_path=str(path),
                output_path=str(output),
                output_format="PNG",
                device=device,
                tile_size=64,
                halo=16,
                precision="fp32",
                jpeg_quality=95,
                preserve_metadata=True,
                safe_memory=True,
                output_scale=4,
            )
            worker.send("job_request", json.loads(request.to_json())["data"])
            return output

        try:
            worker.until("worker_ready")
            for device in devices:
                output = send(custom, device, "custom-" + device.replace(":", "-"))
                worker.until("job_completed", timeout=180)
                with Image.open(output) as result:
                    assert result.size == (260, 188)
                    rgb = np.asarray(result.convert("RGB"), dtype=np.int16).copy()
                    assert rgb.std() > 10 and 60 < rgb.mean() < 150
                    outputs.append(rgb)
                report["checks"].append(
                    {"name": "custom-safetensors", "device": device, "passed": True}
                )
            for other in outputs[1:]:
                assert np.abs(outputs[0] - other).max() <= 1
            for name, path in (("corrupt-safetensors", corrupt), ("unverified-pickle", unverified)):
                output = send(path, devices[-1], name)
                failure = worker.until("job_failed", timeout=180)
                assert not output.exists()
                if name == "unverified-pickle":
                    assert "blocks unverified" in failure["error_message"]
                report["checks"].append({"name": name, "passed": True, "failure": failure})
            output = send(custom, devices[-1], "recovery")
            worker.until("job_completed", timeout=180)
            assert output.is_file()
            report["checks"].append({"name": "same-worker-recovery", "passed": True})
            report["passed"] = True
        except Exception as error:
            report["error"] = repr(error)
            raise
        finally:
            worker.close()
            shutil.copyfile(root / "worker.log", report_path.with_suffix(".worker.log"))
            report_path.write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path, default=Path(sys.executable))
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--device", action="append", dest="devices", required=True)
    args = parser.parse_args()
    torch.set_num_threads(2)
    print(
        json.dumps(
            verify(args.worker, args.model_root, args.report, args.devices, args.source_root)
        )
    )
