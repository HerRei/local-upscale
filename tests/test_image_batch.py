"""The Mac photo batch with real image files and fake Core ML runners."""

import json
import os
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest
import torch
import torch.nn.functional as F
from PIL import Image

from localsr.core.coreml_runtime import CORE_ML_GPU, NEURAL_ENGINE
from localsr.core.model_adapter import NormalizedModelInfo
from localsr.worker import image_batch
from localsr.worker.image_batch import ImageBatchRunner, PowerSource, batch_settings, read_ac_power

pytestmark = pytest.mark.timeout(60)


def job(tmp_path, index, size=(19, 17)):
    source = tmp_path / f"{index}.png"
    if not source.exists():
        rng = np.random.default_rng(index)
        Image.fromarray(rng.integers(0, 256, (size[1], size[0], 3), dtype=np.uint8)).save(source)
    scratch = tmp_path / f"scratch-{index}"
    scratch.mkdir(exist_ok=True)
    return dict(
        job_id=str(index),
        image_path=str(source),
        model_path="span.pth",
        output_path=str(tmp_path / f"out-{index}.png"),
        output_format="png",
        device="mps",
        precision="fp32",
        tile_size=256,
        halo=16,
        jpeg_quality=98,
        preserve_metadata=True,
        safe_memory=True,
        neural_engine=True,
        preview_enabled=False,
        scratch_directory=str(scratch),
        output_temporary_directory=str(scratch),
    )


class Runner:
    """A native runner stand-in: nearest-neighbour 2x at a fixed input shape."""

    def __init__(self, unit, calls, behaviour):
        self.unit, self.calls, self.behaviour = unit, calls, behaviour

    def predict(self, inputs):
        image = inputs["image"]
        self.calls.append((self.unit, image.shape))
        output = F.interpolate(torch.from_numpy(np.array(image)), scale_factor=2, mode="nearest")
        return {"restored": self.behaviour(output, image) if self.behaviour else output}


class Setup:
    def __init__(
        self,
        tmp_path,
        architecture="SPAN",
        ac=True,
        lanes=True,
        behaviour=None,
        memory_budget=None,
    ):
        self.calls, self.messages, self.samples, self.order = [], [], [], []
        self.forgotten = []
        self.tmp_path = tmp_path
        self.cancel = threading.Event()
        test = self

        class Model:
            def set_cancel_event(self, event):
                test.order.append("cancel event")

            def batch_lanes(self, sample, shape, *, gpu):
                test.samples.append((tuple(sample.shape), shape, gpu))
                if not lanes:
                    return []
                units = [NEURAL_ENGINE] * 2 + ([CORE_ML_GPU] * 2 if gpu else [])
                return [Runner(unit, test.calls, behaviour) for unit in units]

            def forget_batch_unit(self, unit):
                test.forgotten.append(unit)

        def load_model(*_args, **_kwargs):
            test.order.append("load")

        self.engine = SimpleNamespace(
            release_models_except=lambda keep: test.order.append(("release except", keep)),
            load_model=load_model,
            active_model=Model(),
            neural_engine_enabled=False,
            status_callback=None,
        )
        info = NormalizedModelInfo(architecture, 2, 3, 3, True, True, 1, 1, "span.pth", [])
        self.adapter = SimpleNamespace(inspect=lambda path: info)
        self.power = PowerSource(read=ac if callable(ac) else (lambda: ac), interval=0)

        def emit(message):
            if message.__class__.__name__ == "JobStarted":
                test.order.append(("started", message.job_id))
            self.messages.append(json.loads(message.to_json()))

        self.runner = ImageBatchRunner(
            engine=self.engine,
            model_adapter=self.adapter,
            cancel_event=self.cancel,
            emit=emit,
            emit_preview=lambda _packet: None,
            power=self.power,
            memory_budget=memory_budget,
        )

    def run(self, jobs):
        return self.runner.run(jobs)

    def terminal(self):
        return sorted(
            (m["data"]["job_id"], m["type"])
            for m in self.messages
            if m["type"] in {"job_completed", "job_failed", "job_cancelled"}
        )

    def of(self, kind, job_id):
        return [
            m["data"] for m in self.messages if m["type"] == kind and m["data"]["job_id"] == job_id
        ]


def expected_output(path):
    pixels = np.array(Image.open(path))
    return pixels.repeat(2, axis=0).repeat(2, axis=1)


def test_each_photo_is_written_whole_with_its_own_progress_and_completion(tmp_path):
    setup = Setup(tmp_path)
    jobs = [job(tmp_path, i, size=(720 + 9 * i, 380)) for i in range(5)]
    started = time.monotonic()
    assert setup.run(jobs) == []
    wall = time.monotonic() - started
    assert setup.terminal() == [(str(i), "job_completed") for i in range(5)]
    for data in jobs:
        np.testing.assert_array_equal(
            np.array(Image.open(data["output_path"])), expected_output(data["image_path"])
        )
        assert len(setup.of("job_started", data["job_id"])) == 1
        progress = setup.of("progress", data["job_id"])
        assert progress[-1]["percentage"] == pytest.approx(100.0)
        assert progress[-1]["completed_tiles"] == progress[-1]["total_tiles"] == 6
        completed = setup.of("job_completed", data["job_id"])[0]
        assert completed["output_path"] == data["output_path"] and completed["notes"] == ""
        assert completed["inference_seconds"] > 0
        assert not list((tmp_path / f"scratch-{data['job_id']}").iterdir())
    # Elapsed times are each photo's share of the batch, so queue estimates add up.
    elapsed = [setup.of("job_completed", str(i))[0]["elapsed_seconds"] for i in range(5)]
    assert sum(elapsed) <= wall + 0.01
    # Every tile used the one fixed input shape, on both units.
    assert {shape for _, shape in setup.calls} == {(1, 3, 384, 384)}
    assert {unit for unit, _ in setup.calls} == {NEURAL_ENGINE, CORE_ML_GPU}
    assert setup.runner.lane_tile_counts == {
        NEURAL_ENGINE: sum(1 for unit, _ in setup.calls if unit == NEURAL_ENGINE),
        CORE_ML_GPU: sum(1 for unit, _ in setup.calls if unit == CORE_ML_GPU),
    }


def test_the_first_photo_is_announced_before_the_model_loads(tmp_path):
    setup = Setup(tmp_path)
    setup.run([job(tmp_path, i) for i in range(3)])
    assert setup.order.index(("started", "0")) < setup.order.index("load")
    assert setup.order.index("load") < setup.order.index(("started", "1"))
    assert ("release except", {"span.pth"}) in setup.order
    assert setup.engine.neural_engine_enabled is True


def test_battery_runs_the_neural_engine_lanes_only(tmp_path):
    setup = Setup(tmp_path, ac=False)
    assert setup.run([job(tmp_path, i) for i in range(3)]) == []
    # The GPU lanes are ready but idle, so they can join once the Mac is plugged in.
    assert [gpu for *_, gpu in setup.samples] == [True]
    assert {unit for unit, _ in setup.calls} == {NEURAL_ENGINE}
    assert setup.terminal() == [(str(i), "job_completed") for i in range(3)]


def test_gpu_lanes_join_a_batch_once_the_mac_is_plugged_in(tmp_path):
    power = {"ac": False}

    def plug_in(output, _image):
        power["ac"] = True
        time.sleep(0.005)
        return output

    setup = Setup(tmp_path, ac=lambda: power["ac"], behaviour=plug_in)
    assert setup.run([job(tmp_path, i, size=(800, 400)) for i in range(6)]) == []
    assert CORE_ML_GPU in {unit for unit, _ in setup.calls}
    assert setup.terminal() == [(str(i), "job_completed") for i in range(6)]


def test_large_outputs_save_one_at_a_time_within_the_memory_budget(tmp_path, monkeypatch):
    lock = threading.Lock()
    counts = {"now": 0, "peak": 0}
    real_save = image_batch.save_output
    # With room for several saves, each one waits (bounded) for a second save to start, so
    # the overlap does not depend on how fast the next photo is inferred on a busy machine.
    pair = {"wait": False, "started": threading.Event()}

    def counting_save(*args):
        with lock:
            counts["now"] += 1
            counts["peak"] = max(counts["peak"], counts["now"])
            if counts["now"] >= 2:
                pair["started"].set()
        if pair["wait"]:
            pair["started"].wait(timeout=2)
        else:
            time.sleep(0.05)
        try:
            real_save(*args)
        finally:
            with lock:
                counts["now"] -= 1

    monkeypatch.setattr(image_batch, "save_output", counting_save)
    # Each 19x17 photo saves a 38x34 output: 8 bytes per pixel is 10336 bytes.
    one = image_batch.save_bytes(38, 34, scale=2, output_scale=2, alpha=False)
    assert one == 8 * 38 * 34
    setup = Setup(tmp_path, memory_budget=one + one // 2)
    assert setup.run([job(tmp_path, i) for i in range(5)]) == []
    assert setup.terminal() == [(str(i), "job_completed") for i in range(5)]
    assert counts["peak"] == 1
    counts["peak"] = 0
    pair["wait"] = True
    roomy = Setup(tmp_path / "roomy", memory_budget=100 * one)
    (tmp_path / "roomy").mkdir()
    assert roomy.run([job(tmp_path / "roomy", i) for i in range(5)]) == []
    assert counts["peak"] >= 2


def test_save_memory_counts_the_copies_pillow_holds():
    native = 16128 * 12096  # a 12 MP photo at 4x
    assert image_batch.save_bytes(16128, 12096, 4, 4, alpha=False) == 8 * native
    # Downscaled to 2x: the resized copy; alpha: the RGBA image and the alpha plane.
    final = 8064 * 6048
    assert image_batch.save_bytes(16128, 12096, 4, 2, alpha=False) == 8 * native + 4 * final
    assert image_batch.save_bytes(16128, 12096, 4, 4, alpha=True) == 8 * native + 5 * native


def test_the_default_memory_budget_is_a_quarter_of_the_memory(monkeypatch):
    memory = SimpleNamespace(total=16 * 2**30)
    monkeypatch.setattr(image_batch.psutil, "virtual_memory", lambda: memory)
    assert image_batch.default_memory_budget() == 4 * 2**30

    def unavailable():
        raise OSError("no memory information")

    monkeypatch.setattr(image_batch.psutil, "virtual_memory", unavailable)
    assert image_batch.default_memory_budget() == 2 * 2**30


def test_the_first_photo_is_released_once_it_is_inferred(tmp_path, monkeypatch):
    import gc
    import weakref

    loaded = []

    class Recording(image_batch.ImageManager):
        def load(self, path):
            image = super().load(path)
            loaded.append(weakref.ref(image["tensor"]))
            return image

    monkeypatch.setattr(image_batch, "ImageManager", Recording)
    seen = {}
    real_save = image_batch.save_output

    def check_then_save(*args):
        # A photo saves after its last tile: its decoded input must be gone by then.
        if args[4]["job_id"] == "0":
            gc.collect()
            seen["first"] = loaded[0]() is not None
        real_save(*args)

    monkeypatch.setattr(image_batch, "save_output", check_then_save)
    setup = Setup(tmp_path)
    assert setup.run([job(tmp_path, i, size=(400, 400)) for i in range(4)]) == []
    assert seen == {"first": False}


def test_a_retried_tile_keeps_progress_in_range_and_its_lane_is_not_reused(tmp_path):
    failures = []

    def fail_once(output, _image):
        if not failures:
            failures.append(True)
            raise RuntimeError("Error computing NN outputs")
        return output

    setup = Setup(tmp_path, behaviour=fail_once)
    assert setup.run([job(tmp_path, i, size=(800, 400)) for i in range(3)]) == []
    assert setup.terminal() == [(str(i), "job_completed") for i in range(3)]
    for message in setup.messages:
        if message["type"] == "tile_update" and message["data"]["phase"] == "started":
            assert message["data"]["completed_tiles"] < message["data"]["total_tiles"]
    # The failed runner leaves the model's cache, so the next batch loads a fresh one.
    assert len(setup.forgotten) == 1


def test_batch_output_matches_the_normal_runner_for_a_real_convolution(tmp_path):
    """Halo, windows and stitching agree with the per-photo path, not only for pointwise fakes."""
    from localsr.core.inference import InferenceEngine

    torch.manual_seed(3)
    network = torch.nn.Sequential(
        torch.nn.Conv2d(3, 8, 3, padding=1),
        torch.nn.Tanh(),
        torch.nn.Conv2d(8, 12, 3, padding=1),
        torch.nn.PixelShuffle(2),
        torch.nn.Sigmoid(),
    ).eval()

    def convolve(_output, image):
        with torch.no_grad():
            return network(torch.from_numpy(np.array(image)))

    setup = Setup(tmp_path, behaviour=convolve)
    jobs = [job(tmp_path, i, size=(731, 389)) for i in range(2)]
    assert setup.run(jobs) == []
    info = NormalizedModelInfo("SPAN", 2, 3, 3, True, True, 1, 1, "span.pth", [])
    engine = InferenceEngine(model_adapter=None)
    engine._loaded_models["span.pth"] = (network, torch.device("cpu"), torch.float32)
    engine._loaded_backend_ids["span.pth"] = "cpu"
    engine._memory_cap_safe["cpu"] = False
    for data in jobs:
        manager = image_batch.ImageManager()
        normal = engine.process_image(
            manager.load(data["image_path"]),
            info,
            "span.pth",
            "cpu",
            "fp32",
            256,
            16,
            threading.Event(),
            lambda *_: None,
            safe_memory=False,
            temporary_directory=str(tmp_path),
        )
        try:
            expected = np.transpose(np.asarray(normal.get_array()), (1, 2, 0)).astype(int)
        finally:
            normal.cleanup()
        actual = np.array(Image.open(data["output_path"])).astype(int)
        assert actual.shape == expected.shape
        assert np.abs(actual - expected).max() <= 1


def test_the_gate_sample_is_a_real_tile_at_the_lane_shape(tmp_path):
    setup = Setup(tmp_path, architecture="FBCNN")
    setup.run([job(tmp_path, i) for i in range(2)])
    assert setup.samples == [((1, 3, 576, 576), (576, 576), True)]


def test_a_photo_that_fails_to_decode_or_save_fails_alone(tmp_path, monkeypatch):
    setup = Setup(tmp_path)
    jobs = [job(tmp_path, i) for i in range(6)]
    for index in (0, 2):
        bad = tmp_path / f"corrupt-{index}.png"
        bad.write_bytes(b"not an image")
        jobs[index]["image_path"] = str(bad)
    (tmp_path / "out-4.png").write_bytes(b"previous output")
    real_replace = os.replace

    def replace(source, destination):
        if str(destination) == jobs[4]["output_path"]:
            raise OSError("disk full")
        real_replace(source, destination)

    monkeypatch.setattr("os.replace", replace)
    assert setup.run(jobs) == []
    assert setup.terminal() == [
        (str(i), "job_failed" if i in (0, 2, 4) else "job_completed") for i in range(6)
    ]
    assert "disk full" in setup.of("job_failed", "4")[0]["error_message"]
    assert (tmp_path / "out-4.png").read_bytes() == b"previous output"
    assert not any(any((tmp_path / f"scratch-{i}").iterdir()) for i in range(6))


@pytest.mark.parametrize(
    "behaviour",
    [
        lambda output, _image: torch.full_like(output, float("nan")).half(),
        lambda output, _image: (output * 0 + 9).half(),  # SPAN's extreme values
    ],
    ids=["non-finite", "unstable-span"],
)
def test_a_photo_the_lanes_cannot_render_returns_to_the_normal_runner(tmp_path, behaviour):
    def only_photo_one(output, image):
        # Photo 1 is the only one whose pixels are all 1.
        return behaviour(output, image) if np.all(image == 1.0) else output

    setup = Setup(tmp_path, behaviour=only_photo_one)
    jobs = [job(tmp_path, i) for i in range(4)]
    Image.new("RGB", (19, 17), (255, 255, 255)).save(jobs[1]["image_path"])
    assert setup.run(jobs) == [jobs[1]]
    assert setup.terminal() == [(str(i), "job_completed") for i in (0, 2, 3)]
    assert not os.path.exists(jobs[1]["output_path"])
    assert not list((tmp_path / "scratch-1").iterdir())


def test_without_accepted_lanes_every_photo_keeps_the_normal_runner(tmp_path):
    setup = Setup(tmp_path, lanes=False)
    jobs = [job(tmp_path, i) for i in range(3)]
    assert setup.run(jobs) == jobs
    assert setup.terminal() == []


def test_a_corrupt_first_photo_fails_and_the_next_one_supplies_the_gate_tile(tmp_path):
    setup = Setup(tmp_path, lanes=False)
    jobs = [job(tmp_path, i) for i in range(3)]
    (tmp_path / "bad.png").write_bytes(b"broken")
    jobs[0]["image_path"] = str(tmp_path / "bad.png")
    assert setup.run(jobs) == jobs[1:]
    assert setup.terminal() == [("0", "job_failed")]
    assert len(setup.samples) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"device": "cpu"},
        {"neural_engine": False},
        {"face_model_path": "face.pth"},
        {
            "stages": [
                {"kind": "restore", "model_id": "fix", "model_path": "fix.pth"},
                {"kind": "upscale", "model_id": "span", "model_path": "span.pth"},
            ]
        },
    ],
)
def test_fixes_faces_and_other_routes_keep_the_normal_runner(tmp_path, change):
    data = job(tmp_path, 0)
    assert batch_settings(data) is not None
    data.update(change)
    assert batch_settings(data) is None


@pytest.mark.parametrize(
    "jobs",
    [
        lambda tmp: [job(tmp, 0)],
        lambda tmp: [job(tmp, 0), {**job(tmp, 1), "model_path": "other.pth"}],
        lambda tmp: [job(tmp, 0), {**job(tmp, 1), "halo": 32}],
        lambda tmp: [job(tmp, 0), {**job(tmp, 1), "device": "cpu"}],
    ],
    ids=["single", "two-models", "two-settings", "one-not-eligible"],
)
def test_photos_that_do_not_share_one_recipe_keep_the_normal_runner(tmp_path, jobs):
    setup = Setup(tmp_path)
    jobs = jobs(tmp_path)
    assert setup.run(jobs) == jobs
    assert setup.messages == [] and "load" not in setup.order


def test_an_architecture_the_neural_engine_does_not_run_keeps_the_normal_runner(tmp_path):
    setup = Setup(tmp_path, architecture="HAT")
    jobs = [job(tmp_path, i) for i in range(3)]
    assert setup.run(jobs) == jobs
    assert "load" not in setup.order


def test_cancel_before_the_batch_cancels_every_photo(tmp_path):
    setup = Setup(tmp_path)
    setup.cancel.set()
    jobs = [job(tmp_path, i) for i in range(4)]
    assert setup.run(jobs) == []
    assert setup.terminal() == [(str(i), "job_cancelled") for i in range(4)]
    assert not setup.calls


def test_cancel_during_the_batch_stops_and_cleans_every_photo(tmp_path):
    def cancel_on_photo_two(output, image):
        if np.all(image == 1.0):
            setup.cancel.set()
        return output

    setup = Setup(tmp_path, behaviour=cancel_on_photo_two)
    jobs = [job(tmp_path, i, size=(400, 400)) for i in range(6)]
    Image.new("RGB", (400, 400), (255, 255, 255)).save(jobs[2]["image_path"])
    assert setup.run(jobs) == []
    statuses = dict(setup.terminal())
    assert len(statuses) == 6
    assert all(statuses[str(i)] == "job_cancelled" for i in range(2, 6))
    for i in range(6):
        assert not list((tmp_path / f"scratch-{i}").iterdir())
        if statuses[str(i)] == "job_cancelled":
            assert not os.path.exists(jobs[i]["output_path"])


def test_the_power_source_is_read_at_most_once_per_interval():
    reads = []
    now = [0.0]

    def read():
        reads.append(now[0])
        return len(reads) == 1

    power = PowerSource(read=read, interval=5.0, clock=lambda: now[0])
    assert power.on_ac() is True
    now[0] = 4.9
    assert power.on_ac() is True
    now[0] = 5.0
    assert power.on_ac() is False
    assert reads == [0.0, 5.0]


@pytest.mark.parametrize(
    "stdout, expected",
    [
        ("Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t100%; charged;\n", True),
        ("Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1)\t80%;\n", False),
        ("unrecognized", False),
    ],
)
def test_only_positive_evidence_of_ac_power_enables_the_gpu_lane(monkeypatch, stdout, expected):
    monkeypatch.setattr(
        image_batch.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=stdout)
    )
    assert read_ac_power() is expected


def test_an_unreadable_power_source_counts_as_battery(monkeypatch):
    def fail(*_args, **_kwargs):
        raise OSError("pmset missing")

    monkeypatch.setattr(image_batch.subprocess, "run", fail)
    assert read_ac_power() is False
