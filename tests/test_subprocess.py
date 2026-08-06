import json
import os
import subprocess
import sys
import time

import pytest
from PIL import Image


def test_subprocess_e2e_and_cancellation(tmp_path):
    model_path = os.environ.get("LOCALSR_TEST_MODEL_PATH")
    if not model_path or not os.path.exists(model_path):
        pytest.skip("No real spandrel model provided. Set LOCALSR_TEST_MODEL_PATH.")

    # Prepare dummy image
    img_path = str(tmp_path / "test_img.png")
    Image.new("RGB", (64, 64), color="red").save(img_path)
    out_path = str(tmp_path / "out_img.png")

    proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "localsr.worker.__main__"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    ready = proc.stdout.readline()
    assert "worker_ready" in ready

    # 1. Test real job
    job_req = {
        "type": "job_request",
        "data": {
            "job_id": "job_1",
            "image_path": img_path,
            "model_path": model_path,
            "output_path": out_path,
            "output_format": "png",
            "device": "cpu",
            "tile_size": 64,
            "halo": 8,
            "precision": "fp32",
            "jpeg_quality": 100,
            "preserve_metadata": False,
            "safe_memory": False,
        },
    }
    proc.stdin.write(json.dumps(job_req) + "\n")
    proc.stdin.flush()

    job_completed = False
    for _ in range(50):
        line = proc.stdout.readline()
        if not line:
            break
        msg = json.loads(line.strip())
        if msg.get("type") == "job_completed" and msg["data"]["job_id"] == "job_1":
            job_completed = True
            break
        if msg.get("type") == "job_failed":
            pytest.fail(f"Job failed: {msg['data']}")

    assert job_completed
    assert os.path.exists(out_path)
    with Image.open(out_path) as out_img:
        assert out_img.size[0] > 64  # At least 2x upscale

    # 2. Test Cancellation
    out_cancelled = str(tmp_path / "out_cancelled.png")
    job_req_2 = job_req.copy()
    job_req_2["data"]["job_id"] = "job_2"
    job_req_2["data"]["output_path"] = out_cancelled

    proc.stdin.write(json.dumps(job_req_2) + "\n")
    proc.stdin.flush()
    time.sleep(0.5)  # Let it start

    cancel_req = {"type": "cancel_request", "data": {"job_id": "job_2"}}
    proc.stdin.write(json.dumps(cancel_req) + "\n")
    proc.stdin.flush()

    job_cancelled = False
    for _ in range(50):
        line = proc.stdout.readline()
        if not line:
            break
        msg = json.loads(line.strip())
        if msg.get("type") == "job_cancelled" and msg["data"]["job_id"] == "job_2":
            job_cancelled = True
            break

    assert job_cancelled
    assert not os.path.exists(out_cancelled)

    # 3. Clean Shutdown
    proc.stdin.write(json.dumps({"type": "shutdown_request"}) + "\n")
    proc.stdin.flush()
    proc.wait(timeout=5)
    assert proc.returncode == 0
