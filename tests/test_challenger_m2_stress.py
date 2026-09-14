from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QProcess, QSettings
from PySide6.QtWidgets import QApplication

from localsr.protocol.client import WorkerClient
from localsr.ui.main_window import MainWindow


@pytest.fixture(scope="module", autouse=True)
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


class MockProcessData:
    def __init__(self, data_bytes):
        self._bytes = data_bytes

    def data(self):
        return self._bytes


def feed_stdout(client: WorkerClient, raw_bytes: bytes):
    """Helper to feed raw stdout bytes into WorkerClient handle_stdout."""
    client.process.readAllStandardOutput = MagicMock(return_value=MockProcessData(raw_bytes))
    client.handle_stdout()


class TestWorkerClientProtocolStress:
    def test_non_dict_json_primitives(self, qapp):
        """Stress test: JSON primitives (int, str, bool, null, list) sent to stdout."""
        client = WorkerClient()
        logs = []
        client.log_received.connect(lambda d: logs.append(d))

        primitives = [
            b"123\n",
            b'"hello world"\n',
            b"true\n",
            b"false\n",
            b"null\n",
            b"[1, 2, 3]\n",
        ]

        for p in primitives:
            feed_stdout(client, p)

    def test_null_or_invalid_data_field(self, qapp):
        """Stress test: JSON dicts with null or non-dict 'data' field."""
        client = WorkerClient()

        payloads = [
            b'{"type": "job_completed", "data": null}\n',
            b'{"type": "job_completed", "data": "string_data"}\n',
            b'{"type": "job_completed", "data": 12345}\n',
            b'{"type": "job_cancelled", "data": null}\n',
            b'{"type": "job_failed", "data": null}\n',
            b'{"type": "job_started", "data": null}\n',
            b'{"type": "progress", "data": null}\n',
            b'{"type": "warning", "data": null}\n',
            b'{"type": "model_info", "data": null}\n',
        ]

        for payload in payloads:
            feed_stdout(client, payload)

    def test_non_utf8_binary_stdout(self, qapp):
        """Stress test: Non-UTF8 binary data on stdout."""
        client = WorkerClient()

        binary_payloads = [
            b"\x80\x81\x82\x90\xff\xfe\n",
            b"\xed\xa0\x80\n",  # invalid surrogate pair UTF-8
        ]

        for b in binary_payloads:
            feed_stdout(client, b)

    def test_non_string_or_missing_signal_arguments(self, qapp):
        """Stress test: Non-string job_id or error message fields in JSON payload."""
        client = WorkerClient()

        received_completed = []
        received_failed = []

        client.job_completed.connect(lambda j, r: received_completed.append((j, r)))
        client.job_failed.connect(lambda j, e: received_failed.append((j, e)))

        # Send integer job_id or non-string error
        payloads = [
            b'{"type": "job_completed", "data": {"job_id": 9999}}\n',
            b'{"type": "job_failed", "data": {"job_id": 123, "error": 456}}\n',
        ]

        for p in payloads:
            feed_stdout(client, p)

    def test_missing_fields(self, qapp):
        """Stress test: Protocol messages with missing expected fields."""
        client = WorkerClient()

        received_completed = []
        received_cancelled = []
        received_failed = []
        received_warning = []
        received_started = []

        client.job_completed.connect(lambda j, r: received_completed.append((j, r)))
        client.job_cancelled.connect(lambda j: received_cancelled.append(j))
        client.job_failed.connect(lambda j, e: received_failed.append((j, e)))
        client.warning.connect(lambda m: received_warning.append(m))
        client.job_started.connect(lambda j: received_started.append(j))

        # Test empty data objects
        feed_stdout(client, b'{"type": "job_completed", "data": {}}\n')
        feed_stdout(client, b'{"type": "job_cancelled", "data": {}}\n')
        feed_stdout(client, b'{"type": "job_failed", "data": {}}\n')
        feed_stdout(client, b'{"type": "warning", "data": {}}\n')
        feed_stdout(client, b'{"type": "job_started", "data": {}}\n')
        feed_stdout(client, b'{"type": "missing_type"}\n')

        assert len(received_completed) == 1
        assert received_completed[0][0] == ""  # default job_id

        assert len(received_cancelled) == 1
        assert received_cancelled[0] == ""

        assert len(received_failed) == 1
        assert received_failed[0] == ("", "")

        assert len(received_warning) == 1
        assert received_warning[0] == ""

        assert len(received_started) == 1
        assert received_started[0] == ""

    def test_rapid_and_chunked_messages(self, qapp):
        """Stress test: Rapid fire 1000 messages and chunked buffer splits."""
        client = WorkerClient()

        completed_count = 0

        def count_comp(j, r):
            nonlocal completed_count
            completed_count += 1

        client.job_completed.connect(count_comp)

        # 1. Rapid fire 1000 completion messages in a single stdout read
        big_payload = b"".join(
            [
                f'{{"type": "job_completed", "data": {{"job_id": "{i}"}}}}\n'.encode()
                for i in range(1000)
            ]
        )
        feed_stdout(client, big_payload)

        assert completed_count == 1000

        # 2. Chunked message split across 3 reads
        msg_str = '{"type": "job_completed", "data": {"job_id": "chunked_123"}}\n'
        chunk1 = msg_str[:15].encode("utf-8")
        chunk2 = msg_str[15:35].encode("utf-8")
        chunk3 = msg_str[35:].encode("utf-8")

        feed_stdout(client, chunk1)
        feed_stdout(client, chunk2)
        assert completed_count == 1000  # not complete yet

        feed_stdout(client, chunk3)
        assert completed_count == 1001  # now complete

    def test_shutdown_suppression_and_restart_logic(self, qapp):
        """Verify shutdown flags suppress worker error signals correctly."""
        client = WorkerClient()
        errors = []
        client.worker_error.connect(lambda e: errors.append(e))

        # Normal running state error
        client.handle_error(QProcess.Crashed)
        assert len(errors) == 1
        assert "Crashed" in errors[0]

        client.handle_finished(1, QProcess.NormalExit)
        assert len(errors) == 2
        assert "exited with code 1" in errors[1]

        # Exit code 0 should be suppressed
        client.handle_finished(0, QProcess.NormalExit)
        assert len(errors) == 2  # no new error emitted

        # Shutting down state should suppress all errors
        client._is_shutting_down = True
        client.handle_error(QProcess.Crashed)
        client.handle_finished(1, QProcess.NormalExit)
        assert len(errors) == 2  # still no new error emitted


class TestMainWindowButtonStatesAndResets:
    def test_button_states_during_lifecycle(self, qtbot, tmp_path):
        """Verify button states throughout upscale, cancel, complete, and fail cycles."""
        settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
        win = MainWindow(start_worker=False, settings=settings)
        qtbot.addWidget(win)

        # Initial states
        assert not win.btn_upscale.isEnabled()
        assert not win.btn_cancel.isEnabled()
        assert not win.btn_open.isEnabled()
        assert not win.btn_reveal.isEnabled()

        # Set image and model to enable upscale button
        win.image_path = str(tmp_path / "test.png")
        win.model_path = str(tmp_path / "model.pth")
        win.image_w = 100
        win.image_h = 100
        win.model_scale = 2
        win.update_predict()

        assert win.btn_upscale.isEnabled()

        # 1. Job Started (Simulated start_upscale / on_job_started)
        win.on_job_started("job_1")
        assert not win.btn_upscale.isEnabled()
        assert win.btn_cancel.isEnabled()
        assert not win.btn_open.isEnabled()
        assert not win.btn_reveal.isEnabled()

        # 2. Job Completed
        win.on_job_completed("job_1", {"success": True})
        assert win.btn_upscale.isEnabled()
        assert not win.btn_cancel.isEnabled()
        assert win.btn_open.isEnabled()
        assert win.btn_reveal.isEnabled()

        # 3. Job Started again
        win.on_job_started("job_2")
        assert not win.btn_upscale.isEnabled()
        assert win.btn_cancel.isEnabled()

        # 4. Job Cancelled (when output file does NOT exist)
        win.on_job_cancelled("job_2")
        assert win.btn_upscale.isEnabled()
        assert not win.btn_cancel.isEnabled()
        assert not win.btn_open.isEnabled()
        assert not win.btn_reveal.isEnabled()

        # 5. Job Started again
        win.on_job_started("job_3")
        assert not win.btn_upscale.isEnabled()
        assert win.btn_cancel.isEnabled()

        # 6. Job Failed
        win.on_job_failed("job_3", "CUDA Out of Memory")
        assert win.btn_upscale.isEnabled()
        assert not win.btn_cancel.isEnabled()

    def test_cancellation_button_resets_when_output_file_exists(self, qtbot, tmp_path):
        """Verify button states on job cancellation when a partial/previous output file exists."""
        settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
        win = MainWindow(start_worker=False, settings=settings)
        qtbot.addWidget(win)

        out_file = tmp_path / "test_upscaled.png"
        out_file.write_text("dummy output")

        win.image_path = str(tmp_path / "test.png")
        win.model_path = str(tmp_path / "model.pth")
        win.image_w = 100
        win.image_h = 100
        win.model_scale = 2
        win.output_dir = str(tmp_path)
        win.last_output_path = str(out_file)
        win.update_predict()

        win.on_job_started("job_cancel_exists")
        assert not win.btn_upscale.isEnabled()
        assert win.btn_cancel.isEnabled()

        win.on_job_cancelled("job_cancel_exists")
        assert win.btn_upscale.isEnabled()
        assert not win.btn_cancel.isEnabled()
        assert win.btn_open.isEnabled()
        assert win.btn_reveal.isEnabled()
