"""Slint tests run out-of-process so macOS never mixes Qt and Winit delegates."""

import base64
import io
import os
import subprocess
import sys
import textwrap
from pathlib import Path

from PIL import Image

from localsr.ui.slint_preview import SlintPreviewBuffer

ROOT = Path(__file__).resolve().parents[1]


def run_slint_script(source: str) -> None:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_slint_interface_compiles():
    result = subprocess.run(
        [sys.executable, "-m", "localsr.ui.slint_check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_slint_preview_uses_aspect_correct_zoom_pan_and_completed_comparison():
    source = (ROOT / "src/localsr/ui/slint/main.slint").read_text(encoding="utf-8")

    assert "preview-aspect" in source
    assert "preview-pan-x" in source
    assert "preview-pan-y" in source
    assert "self.pan-start-x + self.mouse-x - self.pressed-x" in source
    assert "self.zoom-anchor-x" in source
    assert "double-clicked" in source
    assert "visible: root.live-result-ready" in source
    assert "visible: root.result-ready && !root.tile-active" in source


def test_primary_action_is_centered_under_the_inspector():
    source = (ROOT / "src/localsr/ui/slint/main.slint").read_text(encoding="utf-8")

    assert "x: parent.width - 304px;" in source
    assert "width: 304px;" in source
    assert "x: (parent.width - self.width) / 2;" in source
    assert 'text: root.can-cancel ? "Cancel" : root.action-text;' in source


def test_progressive_preview_starts_at_normal_source_brightness():
    encoded = io.BytesIO()
    Image.new("RGB", (40, 20), (100, 120, 140)).save(encoded, format="JPEG", quality=100)
    preview = SlintPreviewBuffer(maximum_dimension=100)
    try:
        assert preview.set_source_base64(base64.b64encode(encoded.getvalue()).decode("ascii"))
        progressive_path = preview.reset_progressive(80, 40)
        with Image.open(progressive_path) as image:
            red, green, blue = image.getpixel((10, 10))
        assert red > 80
        assert green > 100
        assert blue > 120
    finally:
        preview.close()


def test_settings_use_wheel_safe_desktop_combos():
    main_source = (ROOT / "src/localsr/ui/slint/main.slint").read_text(encoding="utf-8")
    component_source = (ROOT / "src/localsr/ui/slint/components.slint").read_text(encoding="utf-8")

    assert main_source.count("DesktopComboBox {") == 6
    assert "ComboBox, ListView" not in main_source
    assert "Never cycle a closed settings combo" in component_source
    assert "return reject;" in component_source


def test_slint_task_filters_catalog_models(tmp_path):
    run_slint_script(
        f"""
        from pathlib import Path
        from localsr.ui.slint_app import create_slint_application

        root = Path({str(tmp_path)!r})
        application = create_slint_application(
            start_worker=False,
            settings_path=root / "settings.json",
            model_root=root / "models",
        )
        assert application.ui.task_selected is False
        assert application.filtered_models == []
        assert all(model is None or model.native_scale > 1 for model in application.filtered_models)
        application.set_task(0)
        assert application.ui.task_selected is True
        assert all(model is None or model.native_scale > 1 for model in application.filtered_models)
        application.set_task(1)
        assert all(model is None or model.native_scale == 1 for model in application.filtered_models)
        application.set_task(2)
        assert all(model is None or model.native_scale > 1 for model in application.filtered_models)
        application.shutdown()
        """
    )


def test_missing_custom_model_falls_back_to_a_real_upscale_model(tmp_path):
    run_slint_script(
        f"""
        import json
        from pathlib import Path
        from localsr.ui.slint_app import create_slint_application

        root = Path({str(tmp_path)!r})
        settings = root / "settings.json"
        settings.write_text(json.dumps({{
            "selected_model_id": "__custom__",
            "custom_model_path": str(root / "missing.pth"),
            "output_scale": 1,
        }}), encoding="utf-8")
        application = create_slint_application(
            start_worker=False,
            settings_path=settings,
            model_root=root / "models",
        )

        application.ui.task_changed(0)
        assert application.filtered_models[int(application.ui.model_index)] is not None
        assert application.scale_values == [2, 3, 4]
        application.ui.scale_changed(0)
        assert application.output_scale == 2
        application.ui.scale_changed(2)
        assert application.output_scale == 4
        application.shutdown()
        """
    )


def test_choose_image_slint_callback_reaches_native_dialog_and_queue(tmp_path):
    run_slint_script(
        f"""
        from pathlib import Path
        from PIL import Image
        import localsr.ui.slint_app as slint_app

        root = Path({str(tmp_path)!r})
        image_path = root / "chosen.png"
        Image.new("RGB", (31, 19), "navy").save(image_path)
        calls = []
        slint_app.request_dialog = lambda mode, initial="": calls.append((mode, initial)) or [str(image_path)]
        application = slint_app.create_slint_application(
            start_worker=False,
            settings_path=root / "settings.json",
            model_root=root / "models",
        )

        application.ui.choose_images()
        assert calls[0][0] == "images"
        assert [item.path for item in application.images] == [str(image_path.resolve())]
        assert application.ui.image_ready is True
        application.shutdown()
        """
    )


def test_visible_configuration_callbacks_round_trip(tmp_path):
    run_slint_script(
        f"""
        from pathlib import Path
        from PIL import Image
        from localsr.protocol.messages import CapabilitiesRequest
        import localsr.ui.slint_app as slint_app

        root = Path({str(tmp_path)!r})
        image_path = root / "source.png"
        output_dir = root / "output"
        output_dir.mkdir()
        Image.new("RGB", (32, 24), "teal").save(image_path)
        dialog_calls = []

        def dialog(mode, initial=""):
            dialog_calls.append((mode, initial))
            return [str(output_dir)] if mode == "output" else []

        slint_app.request_dialog = dialog
        application = slint_app.create_slint_application(
            start_worker=False,
            settings_path=root / "settings.json",
            model_root=root / "models",
        )
        application.add_images([str(image_path)], replace=True)
        application.ui.task_changed(0)

        downloads = []
        application._start_model_download = lambda model: downloads.append(model)
        application.ui.model_action()
        assert downloads and downloads[0].model_id == "hat_s_x4"

        application.ui.scale_changed(0)
        assert application.output_scale == 2
        application.ui.scale_changed(2)
        assert application.output_scale == 4
        application.ui.format_changed(1)
        assert application.format_index == 1
        application.ui.jpeg_quality_changed(83)
        assert application.jpeg_quality == 83
        application.ui.preserve_metadata_changed(False)
        assert application.preserve_metadata is False
        application.ui.choose_output_directory()
        assert application.output_directory == str(output_dir)

        application.current_model_info = {{"half_supported": True}}
        application._on_capabilities({{
            "system_ram_total": 16 * 1024**3,
            "system_ram_available": 10 * 1024**3,
            "devices": [
                {{
                    "id": "cpu",
                    "type": "cpu",
                    "name": "CPU",
                    "total_memory": 16 * 1024**3,
                    "free_memory": 10 * 1024**3,
                    "supports_fp16": False,
                    "recommended_tile_sizes": [64, 128],
                }},
                {{
                    "id": "cuda:0",
                    "type": "cuda",
                    "name": "Test GPU",
                    "total_memory": 8 * 1024**3,
                    "free_memory": 7 * 1024**3,
                    "supports_fp16": True,
                    "recommended_tile_sizes": [64, 128, 256],
                }},
            ],
        }})
        application.ui.device_changed(1)
        assert application.device_id == "cuda:0"
        application.ui.tile_changed(0)
        assert application.tile_size == 64
        application.ui.halo_changed(0)
        assert application.halo == application.halo_values[0]
        application.ui.precision_changed(1)
        assert application.precision == "fp16"
        application.ui.safe_memory_changed(False)
        assert application.safe_memory is False

        requests = []
        application.worker.send_request = lambda request: requests.append(request) or True
        application.ui.refresh_hardware()
        assert isinstance(requests[-1], CapabilitiesRequest)

        application.ui.task_changed(1)
        assert application.task_index == 1
        application.ui.task_changed(2)
        assert application.task_index == 2
        application.shutdown()
        """
    )


def test_queue_and_result_action_callbacks_round_trip(tmp_path):
    run_slint_script(
        f"""
        from pathlib import Path
        from PIL import Image
        from localsr.protocol.messages import CancelRequest, JobRequest
        import localsr.ui.slint_app as slint_app

        root = Path({str(tmp_path)!r})
        image_dir = root / "images"
        image_dir.mkdir()
        first = image_dir / "first.png"
        second = image_dir / "second.png"
        Image.new("RGB", (24, 16), "navy").save(first)
        Image.new("RGB", (20, 12), "teal").save(second)

        def dialog(mode, initial=""):
            if mode == "images":
                return [str(first), str(second)]
            if mode == "folder":
                return [str(image_dir)]
            return []

        slint_app.request_dialog = dialog
        application = slint_app.create_slint_application(
            start_worker=False,
            settings_path=root / "settings.json",
            model_root=root / "models",
        )
        application.ui.batch_mode = True
        application.ui.choose_images()
        assert len(application.images) == 2
        application.ui.select_queue_item(1)
        assert application.selected_image_index == 1
        application.ui.remove_queue_item(0)
        assert len(application.images) == 1
        application.ui.clear_queue()
        assert application.images == []
        application.ui.choose_folder()
        assert len(application.images) == 2

        application.ui.task_changed(0)
        application.model_path = str(root / "model.pth")
        application.current_model_info = {{"scale": 4, "architecture": "Test"}}
        application.model_scale = 4
        application._configure_scales()
        application._update_estimate = lambda: None
        application.ui.can_start = True
        requests = []
        application.worker.send_request = lambda request: requests.append(request) or True
        application.ui.start_job()
        assert isinstance(requests[-1], JobRequest)
        application.ui.cancel_job()
        assert isinstance(requests[-1], CancelRequest)

        output = root / "finished.png"
        output.write_bytes(b"result")
        application.last_output_path = str(output)
        launches = []
        startfiles = []
        slint_app.subprocess.Popen = lambda command: launches.append(command)
        if slint_app.sys.platform == "win32":
            slint_app.os.startfile = lambda path: startfiles.append(path)
        application.ui.open_result()
        application.ui.reveal_result()
        if slint_app.sys.platform == "darwin":
            assert launches == [["open", str(output)], ["open", "-R", str(output)]]
        elif slint_app.sys.platform == "win32":
            assert startfiles == [str(output)]
            assert launches == [["explorer", "/select,", str(output)]]
        else:
            assert launches == [
                ["xdg-open", str(output)],
                ["xdg-open", str(output.parent)],
            ]
        application.ui.poll_backend()
        application.shutdown()
        """
    )


def test_quick_recipe_starts_after_installed_model_is_inspected(tmp_path):
    run_slint_script(
        f"""
        from pathlib import Path
        from PIL import Image
        from localsr.core.model_catalog import CATALOG_BY_ID
        from localsr.protocol.messages import JobRequest
        from localsr.ui.slint_app import create_slint_application

        root = Path({str(tmp_path)!r})
        image_path = root / "source.png"
        Image.new("RGB", (24, 16), "navy").save(image_path)
        application = create_slint_application(
            start_worker=False,
            settings_path=root / "settings.json",
            model_root=root / "models",
        )
        sent = []
        application.worker.send_request = lambda request: sent.append(request) or True
        application.add_images([str(image_path)], replace=True)
        application.set_task(1)

        model = CATALOG_BY_ID["denoise_realplksr_1x"]
        model_path = application.model_store.path_for(model)
        model_path.parent.mkdir(parents=True, exist_ok=True)
        with model_path.open("wb") as checkpoint:
            checkpoint.truncate(model.size_bytes)

        application.apply_automatic_setup(best=False)
        assert application.pending_auto_start is True
        assert application.profile_label == "Quick"
        application._on_model_info({{
            "filename": model.filename,
            "architecture": model.architecture,
            "scale": 1,
            "half_supported": False,
            "parameter_count": 1_000_000,
            "model_file_size": model.size_bytes,
        }})

        assert application.current_job_id
        assert isinstance(sent[-1], JobRequest)
        assert sent[-1].model_path == str(model_path)
        application.shutdown()
        """
    )


def test_best_recipe_downloads_then_starts_automatically(tmp_path):
    run_slint_script(
        f"""
        from pathlib import Path
        from PIL import Image
        from localsr.protocol.messages import JobRequest
        from localsr.ui.slint_app import create_slint_application

        root = Path({str(tmp_path)!r})
        image_path = root / "source.png"
        Image.new("RGB", (24, 16), "teal").save(image_path)
        application = create_slint_application(
            start_worker=False,
            settings_path=root / "settings.json",
            model_root=root / "models",
        )
        sent = []
        downloads = []
        application.worker.send_request = lambda request: sent.append(request) or True
        application._start_model_download = lambda model: downloads.append(model)
        application.add_images([str(image_path)], replace=True)
        application.set_task(1)

        application.apply_automatic_setup(best=True)
        assert downloads[0].model_id == "nafnet_sidd_width64"
        assert application.pending_auto_start is True
        model = downloads[0]
        model_path = application.model_store.path_for(model)
        model_path.parent.mkdir(parents=True, exist_ok=True)
        with model_path.open("wb") as checkpoint:
            checkpoint.truncate(model.size_bytes)

        application._on_download_completed({{
            "model_id": model.model_id,
            "path": str(model_path),
        }})
        application._on_model_info({{
            "filename": model.filename,
            "architecture": "NAFNet",
            "scale": 1,
            "half_supported": False,
            "parameter_count": 115_982_915,
            "model_file_size": model.size_bytes,
        }})

        assert application.current_job_id
        assert isinstance(sent[-1], JobRequest)
        assert sent[-1].model_path == str(model_path)
        application.shutdown()
        """
    )


def test_cancelled_recipe_download_resets_recipe_and_status(tmp_path):
    run_slint_script(
        f"""
        from pathlib import Path
        from PIL import Image
        from localsr.ui.slint_app import create_slint_application

        root = Path({str(tmp_path)!r})
        image_path = root / "source.png"
        Image.new("RGB", (24, 16), "teal").save(image_path)
        application = create_slint_application(
            start_worker=False,
            settings_path=root / "settings.json",
            model_root=root / "models",
        )
        application._start_model_download = lambda model: None
        application.add_images([str(image_path)], replace=True)
        application.set_task(0)
        application.apply_automatic_setup(best=False)
        model = application._selected_catalog_model()
        assert model is not None
        assert application.profile_label == "Quick"

        application._on_download_cancelled({{"model_id": model.model_id}})

        assert application.pending_preset is None
        assert application.pending_preset_label == ""
        assert application.pending_auto_start is False
        assert application.profile_label == "Manual"
        assert application.ui.profile_summary == "Manual"
        assert application.ui.status_title == "Download cancelled"
        assert "partial download was removed" in application.ui.status_detail
        application.shutdown()
        """
    )


def test_slint_queue_rejects_bad_file_without_reusing_previous_image(tmp_path):
    run_slint_script(
        f"""
        from pathlib import Path
        from PIL import Image
        from localsr.ui.slint_app import create_slint_application

        root = Path({str(tmp_path)!r})
        image_path = root / "good.png"
        Image.new("RGB", (31, 19), "navy").save(image_path)
        invalid_path = root / "bad.png"
        invalid_path.write_text("not an image", encoding="utf-8")
        application = create_slint_application(
            start_worker=False,
            settings_path=root / "settings.json",
            model_root=root / "models",
        )
        application.add_images([str(image_path), str(invalid_path)], replace=False)
        assert [item.path for item in application.images] == [str(image_path.resolve())]
        assert application.selected_image_index == 0
        assert "bad.png" in application.runtime_warning
        application.shutdown()
        """
    )


def test_slint_batch_dispatches_every_image_in_order(tmp_path):
    run_slint_script(
        f"""
        from pathlib import Path
        from PIL import Image
        from localsr.ui.slint_app import create_slint_application

        root = Path({str(tmp_path)!r})
        first = root / "first.png"
        second = root / "second.png"
        Image.new("RGB", (24, 16), "navy").save(first)
        Image.new("RGB", (20, 12), "teal").save(second)
        application = create_slint_application(
            start_worker=False,
            settings_path=root / "settings.json",
            model_root=root / "models",
        )
        sent = []
        application.worker.send_request = lambda request: sent.append(request) or True
        application.add_images([str(first), str(second)], replace=False)
        application.ui.batch_mode = True
        application.model_path = str(root / "model.pth")
        application.current_model_info = {{"scale": 4, "architecture": "Test"}}
        application._update_estimate = lambda: None
        application.ui.can_start = True

        application.start_jobs()
        first_job_id = application.current_job_id
        assert sent[-1].image_path == str(first.resolve())
        application._on_job_completed({{
            "job_id": first_job_id,
            "output_path": str(root / "first_upscaled.png"),
            "inference_seconds": 1.0,
        }})
        assert sent[-1].image_path == str(second.resolve())
        assert application.batch_current == 2
        application.shutdown()
        """
    )


def test_slint_preview_accepts_bounded_worker_jpeg(tmp_path):
    run_slint_script(
        f"""
        import base64
        import io
        from pathlib import Path
        from PIL import Image
        from localsr.ui.slint_app import create_slint_application

        root = Path({str(tmp_path)!r})
        image_path = root / "source.png"
        Image.new("RGB", (40, 20), "#315b78").save(image_path)
        encoded = io.BytesIO()
        Image.new("RGB", (40, 20), "#315b78").save(encoded, format="JPEG")
        application = create_slint_application(
            start_worker=False,
            settings_path=root / "settings.json",
            model_root=root / "models",
        )
        application.add_images([str(image_path)], replace=True)
        application._on_preview_ready({{
            "image_path": str(image_path.resolve()),
            "jpeg_base64": base64.b64encode(encoded.getvalue()).decode("ascii"),
        }})
        assert application.ui.image_ready is True
        assert application.ui.source_image.width == 40
        assert application.ui.source_image.height == 20
        application.shutdown()
        """
    )


def test_slint_comparison_appears_only_after_job_completion(tmp_path):
    run_slint_script(
        f"""
        import base64
        import io
        from pathlib import Path
        from PIL import Image
        from localsr.ui.slint_app import create_slint_application

        root = Path({str(tmp_path)!r})
        image_path = root / "source.png"
        output_path = root / "result.png"
        Image.new("RGB", (40, 20), "#315b78").save(image_path)
        application = create_slint_application(
            start_worker=False,
            settings_path=root / "settings.json",
            model_root=root / "models",
        )
        application.add_images([str(image_path)], replace=True)

        source = io.BytesIO()
        Image.new("RGB", (40, 20), "#315b78").save(source, format="JPEG")
        application._on_preview_ready({{
            "image_path": str(image_path.resolve()),
            "jpeg_base64": base64.b64encode(source.getvalue()).decode("ascii"),
        }})
        assert abs(application.ui.preview_aspect - 2.0) < 0.001

        application.current_job_id = "job-preview-state"
        reset = {{
            "job_id": application.current_job_id,
            "phase": "reset",
            "image_width": 80,
            "image_height": 40,
        }}
        application._on_tile_update(reset)
        assert application.ui.live_result_ready is True
        assert application.ui.result_ready is False

        started = {{
            **reset,
            "phase": "started",
            "output_x": 0,
            "output_y": 0,
            "output_width": 40,
            "output_height": 40,
        }}
        application._on_tile_update(started)
        assert application.ui.tile_active is True
        assert application.ui.result_ready is False

        tile = io.BytesIO()
        Image.new("RGB", (40, 40), "#d08040").save(tile, format="JPEG")
        completed = {{
            **started,
            "phase": "completed",
            "jpeg_base64": base64.b64encode(tile.getvalue()).decode("ascii"),
        }}
        application._on_tile_update(completed)
        assert application.ui.live_result_ready is True
        assert application.ui.result_ready is False
        assert application.ui.tile_active is False

        output_path.write_bytes(b"complete")
        application._on_job_completed({{
            "job_id": application.current_job_id,
            "output_path": str(output_path),
            "inference_seconds": 1.0,
        }})
        assert application.ui.result_ready is True
        assert application.ui.tile_active is False
        application.shutdown()
        """
    )


def test_slint_settings_are_saved_atomically(tmp_path):
    run_slint_script(
        f"""
        import json
        from pathlib import Path
        from localsr.ui.slint_app import SettingsStore

        path = Path({str(tmp_path)!r}) / "config" / "settings.json"
        settings = SettingsStore(path)
        settings.set("device_id", "mps")
        settings.set("tile_size", 128)
        settings.save()
        assert json.loads(path.read_text(encoding="utf-8")) == {{
            "device_id": "mps",
            "tile_size": 128,
        }}
        assert not path.with_suffix(".json.tmp").exists()
        """
    )
