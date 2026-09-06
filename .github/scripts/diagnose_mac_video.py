import os
import select
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def run_tests(target):
    command = [sys.executable, "-u", "-m", "pytest", target, "-vv", "-s", "-o", "faulthandler_timeout=30"]
    print("Running", command, flush=True)
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    started = last_output = time.monotonic()
    try:
        while True:
            readable, _, _ = select.select([process.stdout], [], [], 1)
            if readable:
                data = os.read(process.stdout.fileno(), 65536)
                if data:
                    sys.stdout.buffer.write(data)
                    sys.stdout.buffer.flush()
                    last_output = time.monotonic()
                elif process.poll() is not None:
                    return process.returncode
            now = time.monotonic()
            if now - last_output > 120 or now - started > 1200:
                print("\nIndependent watchdog: sampling the stalled test process", flush=True)
                with tempfile.TemporaryDirectory(prefix="localsr-native-stack-") as directory:
                    sample = Path(directory) / "sample.txt"
                    try:
                        subprocess.run(["/usr/bin/sample", str(process.pid), "1", "10", "-file", str(sample)], timeout=60, check=False)
                        if sample.exists():
                            print(sample.read_text()[:24000], flush=True)
                    except subprocess.TimeoutExpired:
                        print("Native sample timed out", flush=True)
                return 124
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()


code = run_tests("tests/test_video_timing.py")
if code == 0:
    code = run_tests("tests/")
raise SystemExit(code)
