"""Non-interactive LocalSR processing, benchmark, and watch-folder commands."""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from localsr.core.image_formats import SUPPORTED_INPUT_EXTENSIONS, is_video_input
from localsr.core.model_catalog import CATALOG_BY_ID, ModelStore
from localsr.core.output_writer import default_work_directory

WORKLOAD_MODEL_ID = "span_photo_x4"

WATCH_STATE_SCHEMA = "localsr-watch-state-v1"
IGNORED_PARTIAL_SUFFIXES = frozenset(
    {".tmp", ".part", ".partial", ".crdownload", ".download", ".swp"}
)


class AutomationError(RuntimeError):
    pass


class Reporter:
    def __init__(self, json_lines: bool = False) -> None:
        self.json_lines = json_lines

    def emit(self, event: str, **data: object) -> None:
        payload = {"event": event, **data}
        if self.json_lines:
            print(json.dumps(payload, sort_keys=True), flush=True)
        else:
            message = data.get("message") or data.get("output_path") or event.replace("_", " ")
            print(str(message), file=sys.stderr, flush=True)


def _atomic_json_write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


@dataclass(frozen=True)
class FileFingerprint:
    size: int
    modified_ns: int

    @classmethod
    def from_path(cls, path: Path) -> FileFingerprint:
        status = path.stat()
        return cls(size=int(status.st_size), modified_ns=int(status.st_mtime_ns))

    def key(self) -> str:
        return f"{self.size}:{self.modified_ns}"


class WatchLedger:
    """Small, atomically persisted completion ledger for restart-safe watches."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.completed: dict[str, str] = {}
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            if document.get("schema") == WATCH_STATE_SCHEMA and isinstance(
                document.get("completed"), dict
            ):
                self.completed = {
                    str(key): str(value) for key, value in document["completed"].items()
                }
        except (FileNotFoundError, OSError, ValueError, TypeError):
            self.completed = {}

    def is_completed(self, path: Path, fingerprint: FileFingerprint) -> bool:
        return self.completed.get(str(path.resolve())) == fingerprint.key()

    def mark_completed(self, path: Path, fingerprint: FileFingerprint) -> None:
        self.completed[str(path.resolve())] = fingerprint.key()
        _atomic_json_write(
            self.path,
            {"schema": WATCH_STATE_SCHEMA, "completed": self.completed},
        )


@dataclass
class StableObservation:
    fingerprint: FileFingerprint
    unchanged_since: float


def discover_watch_files(input_directory: Path, output_directory: Path) -> list[Path]:
    """Return supported, non-partial files without descending into LocalSR output."""
    input_root = input_directory.resolve(strict=True)
    output_root = output_directory.resolve(strict=True)
    discovered: list[Path] = []
    seen: set[Path] = set()
    for candidate in input_root.rglob("*"):
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if not resolved.is_file() or resolved in seen:
            continue
        if resolved == output_root or output_root in resolved.parents:
            continue
        if resolved.name.startswith(".") or resolved.suffix.lower() in IGNORED_PARTIAL_SUFFIXES:
            continue
        if resolved.suffix.lower() not in SUPPORTED_INPUT_EXTENSIONS:
            continue
        seen.add(resolved)
        discovered.append(resolved)
    return sorted(discovered, key=lambda path: str(path).casefold())


def stable_watch_candidates(
    files: list[Path],
    *,
    ledger: WatchLedger,
    observations: dict[Path, StableObservation],
    stable_seconds: float,
    now: float,
) -> list[tuple[Path, FileFingerprint]]:
    current = set(files)
    for stale in set(observations) - current:
        observations.pop(stale, None)

    ready: list[tuple[Path, FileFingerprint]] = []
    for path in files:
        try:
            fingerprint = FileFingerprint.from_path(path)
        except OSError:
            observations.pop(path, None)
            continue
        if ledger.is_completed(path, fingerprint):
            observations.pop(path, None)
            continue
        previous = observations.get(path)
        if previous is None or previous.fingerprint != fingerprint:
            observations[path] = StableObservation(fingerprint, now)
            continue
        if now - previous.unchanged_since >= stable_seconds:
            ready.append((path, fingerprint))
    return ready


def select_device(requested: str) -> str:
    if requested != "auto":
        return requested
    from localsr.core.hardware import get_capability_report

    devices = get_capability_report()["devices"]
    return str(next((device["id"] for device in devices if device["id"] != "cpu"), "cpu"))


def _trusted_model(model_id: str) -> tuple[object, str]:
    model = CATALOG_BY_ID.get(model_id)
    if model is None:
        raise AutomationError(f"Unknown catalog model: {model_id}")
    store = ModelStore()
    if not store.is_installed(model):
        raise AutomationError(
            f"{model.name} is not installed with its expected SHA-256 digest. "
            "Download it from the LocalSR model library first."
        )
    return model, str(store.path_for(model))


def _unique_output_path(
    source: Path,
    output_directory: Path,
    *,
    output_format: str,
    scale: int,
    video_container: str,
) -> Path:
    suffix = f".{video_container}" if is_video_input(source) else f".{output_format}"
    base = f"{source.stem}_localsr_{scale}x"
    candidate = output_directory / f"{base}{suffix}"
    index = 1
    while candidate.exists() or candidate.resolve(strict=False) == source.resolve(strict=False):
        candidate = output_directory / f"{base}_{index}{suffix}"
        index += 1
    return candidate


class AutomationRunner:
    def __init__(self, reporter: Reporter, cancel_event: threading.Event | None = None) -> None:
        from localsr.core.inference import InferenceEngine
        from localsr.core.model_adapter import ModelAdapter

        self.reporter = reporter
        self.cancel_event = cancel_event or threading.Event()
        self.adapter = ModelAdapter()
        self.engine = InferenceEngine(self.adapter)

    def close(self) -> None:
        self.engine.release_all()
        self.adapter.release()

    def process(
        self,
        source: Path,
        output_directory: Path,
        *,
        model_id: str,
        device: str,
        output_scale: int,
        output_format: str,
        video_container: str,
        tile_size: int,
        halo: int,
        precision: str,
        preserve_metadata: bool,
        jpeg_quality: int,
        crf: int,
        video_codec: str = "av1",
        external_ffmpeg: str = "",
    ) -> dict[str, object]:
        source = source.resolve(strict=True)
        if not source.is_file() or source.suffix.lower() not in SUPPORTED_INPUT_EXTENSIONS:
            raise AutomationError(f"Unsupported input: {source}")
        output_directory.mkdir(parents=True, exist_ok=True)
        output_directory = output_directory.resolve(strict=True)
        model, model_path = _trusted_model(model_id)
        model_info = self.adapter.inspect(model_path)
        native_scale = int(model_info.scale)
        if not 1 <= output_scale <= native_scale:
            raise AutomationError(
                f"{model.name} supports output scales from 1× through {native_scale}×."
            )
        output = _unique_output_path(
            source,
            output_directory,
            output_format=output_format,
            scale=output_scale,
            video_container=video_container,
        )
        self.reporter.emit(
            "job_started",
            input_path=str(source),
            output_path=str(output),
            model_id=model_id,
            device=device,
        )

        started = time.monotonic()
        if is_video_input(source):
            from localsr.core.external_ffmpeg import ExternalFFmpegError, load_external_ffmpeg
            from localsr.core.video_pipeline import VideoJobConfig, run_video_job

            try:
                user_ffmpeg = load_external_ffmpeg(external_ffmpeg)
            except ExternalFFmpegError as error:
                raise AutomationError(str(error)) from error

            result = run_video_job(
                VideoJobConfig(
                    video_path=str(source),
                    model_path=model_path,
                    output_video_path=str(output),
                    model_info=model_info,
                    device_str=device,
                    precision_str=precision,
                    tile_size=tile_size,
                    halo=halo,
                    safe_memory=True,
                    container=video_container,
                    video_codec=video_codec,
                    crf=crf,
                    output_scale=output_scale,
                    external_ffmpeg=user_ffmpeg,
                ),
                self.engine,
                self.cancel_event,
                progress_cb=lambda done, total, elapsed: self.reporter.emit(
                    "progress",
                    input_path=str(source),
                    completed=done,
                    total=total,
                    percentage=(done / total * 100.0) if total else None,
                    elapsed_seconds=round(elapsed, 4),
                ),
            )
            elapsed = result.elapsed_seconds
        else:
            from localsr.core.image_io import ImageManager

            manager = ImageManager()
            image_data = manager.load(str(source))
            writer = None
            try:
                writer = self.engine.process_image(
                    img_data=image_data,
                    model_info=model_info,
                    model_path=model_path,
                    device_str=device,
                    precision_str=precision,
                    tile_size=tile_size,
                    halo=halo,
                    cancel_event=self.cancel_event,
                    progress_callback=lambda done, total, active: self.reporter.emit(
                        "progress",
                        input_path=str(source),
                        completed=done,
                        total=total,
                        percentage=done / max(1, total) * 100.0,
                        active_tile_size=active,
                    ),
                    safe_memory=True,
                    temporary_directory=str(default_work_directory()),
                )
                if self.cancel_event.is_set():
                    raise InterruptedError("processing cancelled")
                manager.save(
                    writer,
                    str(output),
                    format=output_format,
                    quality=jpeg_quality,
                    preserve_metadata=preserve_metadata,
                    icc_profile=image_data.get("icc_profile"),
                    safe_exif=image_data.get("safe_exif", {}),
                    scale=native_scale,
                    output_scale=output_scale,
                )
            finally:
                if writer is not None:
                    writer.cleanup()
            elapsed = time.monotonic() - started

        result_data = {
            "input_path": str(source),
            "output_path": str(output),
            "model_id": model_id,
            "device": device,
            "scale": output_scale,
            "elapsed_seconds": round(elapsed, 4),
        }
        self.reporter.emit("job_completed", **result_data)
        return result_data

    def benchmark(self, device: str) -> dict[str, object]:
        from localsr.core.benchmark import run_benchmark

        model, model_path = _trusted_model(WORKLOAD_MODEL_ID)
        model_info = self.adapter.inspect(model_path)
        result = run_benchmark(
            engine=self.engine,
            model_info=model_info,
            model_path=model_path,
            model_name=model.name,
            device=device,
            cancel_event=self.cancel_event,
            progress_callback=lambda done, total: self.reporter.emit(
                "benchmark_progress", completed=done, total=total, percentage=done / total * 100
            ),
        ).to_dict()
        self.reporter.emit("benchmark_completed", result=result)
        return result


@contextmanager
def cancellation_signals(cancel_event: threading.Event) -> Iterator[None]:
    previous: dict[int, object] = {}

    def cancel(_signal: int, _frame: object) -> None:
        cancel_event.set()

    if threading.current_thread() is threading.main_thread():
        for number in (signal.SIGINT, signal.SIGTERM):
            previous[number] = signal.getsignal(number)
            signal.signal(number, cancel)
    try:
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def _add_processing_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output", required=True, type=Path, help="Output directory.")
    parser.add_argument("--model", default=WORKLOAD_MODEL_ID, choices=sorted(CATALOG_BY_ID))
    parser.add_argument("--device", default="auto", help="auto, cpu, mps, cuda:0, or xpu:0.")
    parser.add_argument("--scale", type=int, default=4)
    parser.add_argument("--format", choices=["png", "jpg", "tif", "webp"], default="png")
    parser.add_argument("--video-container", choices=["mp4", "mkv"], default="mp4")
    parser.add_argument(
        "--video-codec",
        choices=["av1", "vp9", "ffv1", "h264", "hevc"],
        default="av1",
        help="H.264/HEVC are written by the FFmpeg given with --external-ffmpeg.",
    )
    parser.add_argument(
        "--external-ffmpeg",
        default="",
        help="An FFmpeg you installed, for formats LocalSR does not include.",
    )
    parser.add_argument("--tile-size", type=int, default=256)
    parser.add_argument("--halo", type=int, default=16)
    parser.add_argument("--precision", choices=["fp32", "fp16"], default="fp32")
    parser.add_argument("--no-metadata", action="store_true")
    parser.add_argument("--jpeg-quality", type=int, default=98)
    parser.add_argument("--crf", type=int, default=18)
    parser.add_argument("--json", action="store_true", help="Emit newline-delimited JSON events.")


def build_automation_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="localsr", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    process_parser = subparsers.add_parser("process", help="Process one or more media files.")
    process_parser.add_argument("files", nargs="+", type=Path)
    _add_processing_options(process_parser)

    benchmark_parser = subparsers.add_parser("benchmark", help="Run localsr-benchmark-v1.")
    benchmark_parser.add_argument("--device", default="auto")
    benchmark_parser.add_argument("--json", action="store_true")

    watch_parser = subparsers.add_parser("watch", help="Process stable files added to a folder.")
    watch_parser.add_argument("folder", type=Path)
    _add_processing_options(watch_parser)
    watch_parser.add_argument("--state-file", type=Path)
    watch_parser.add_argument("--stable-seconds", type=float, default=2.0)
    watch_parser.add_argument("--poll-seconds", type=float, default=1.0)
    watch_parser.add_argument("--once", action="store_true", help="Exit after the current scan.")
    return parser


def _processing_kwargs(args: argparse.Namespace, device: str) -> dict[str, object]:
    return {
        "model_id": args.model,
        "device": device,
        "output_scale": args.scale,
        "output_format": args.format,
        "video_container": args.video_container,
        "video_codec": args.video_codec,
        "external_ffmpeg": args.external_ffmpeg,
        "tile_size": args.tile_size,
        "halo": args.halo,
        "precision": args.precision,
        "preserve_metadata": not args.no_metadata,
        "jpeg_quality": args.jpeg_quality,
        "crf": args.crf,
    }


def _watch(
    args: argparse.Namespace,
    runner: AutomationRunner,
    device: str,
    *,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> int:
    input_directory = args.folder.resolve(strict=True)
    if not input_directory.is_dir():
        raise AutomationError(f"Watch path is not a directory: {input_directory}")
    args.output.mkdir(parents=True, exist_ok=True)
    output_directory = args.output.resolve(strict=True)
    state_file = args.state_file or output_directory / ".localsr-watch-state.json"
    ledger = WatchLedger(state_file)
    observations: dict[Path, StableObservation] = {}
    failed_once: set[Path] = set()
    runner.reporter.emit(
        "watch_started",
        folder=str(input_directory),
        output_directory=str(output_directory),
        state_file=str(state_file),
    )
    while not runner.cancel_event.is_set():
        files = discover_watch_files(input_directory, output_directory)
        candidates = stable_watch_candidates(
            files,
            ledger=ledger,
            observations=observations,
            stable_seconds=max(0.0, args.stable_seconds),
            now=clock(),
        )
        for path, fingerprint in candidates:
            if runner.cancel_event.is_set():
                break
            try:
                runner.process(path, output_directory, **_processing_kwargs(args, device))
            except InterruptedError:
                runner.cancel_event.set()
                runner.reporter.emit(
                    "cancelled",
                    input_path=str(path),
                    message="LocalSR watch processing was cancelled.",
                )
                break
            except Exception as error:  # noqa: BLE001
                runner.reporter.emit(
                    "job_failed", input_path=str(path), error=str(error), message=str(error)
                )
                observations.pop(path, None)
                failed_once.add(path)
                continue
            ledger.mark_completed(path, fingerprint)
            observations.pop(path, None)
        if args.once:
            pending = []
            for path in files:
                if path in failed_once:
                    continue
                try:
                    completed = ledger.is_completed(path, FileFingerprint.from_path(path))
                except OSError:
                    continue
                if not completed:
                    pending.append(path)
            if not pending:
                break
        sleeper(max(0.1, args.poll_seconds))
    runner.reporter.emit("watch_stopped", cancelled=runner.cancel_event.is_set())
    if runner.cancel_event.is_set():
        return 130
    return 1 if args.once and failed_once else 0


def run_automation_cli(argv: list[str]) -> int:
    args = build_automation_parser().parse_args(argv)
    reporter = Reporter(bool(args.json))
    cancel_event = threading.Event()
    runner = AutomationRunner(reporter, cancel_event)
    try:
        _validate_automation_args(args)
        with cancellation_signals(cancel_event):
            device = select_device(args.device)
            if args.command == "benchmark":
                runner.benchmark(device)
            elif args.command == "process":
                failed = False
                for source in args.files:
                    if cancel_event.is_set():
                        break
                    try:
                        runner.process(source, args.output, **_processing_kwargs(args, device))
                    except InterruptedError:
                        cancel_event.set()
                        reporter.emit("cancelled", message="LocalSR processing was cancelled.")
                        break
                    except Exception as error:  # noqa: BLE001 - isolate each requested input
                        failed = True
                        reporter.emit(
                            "job_failed",
                            input_path=str(source),
                            error=str(error),
                            message=str(error),
                        )
                if cancel_event.is_set():
                    return 130
                if failed:
                    return 1
            elif args.command == "watch":
                return _watch(args, runner, device)
        return 130 if cancel_event.is_set() else 0
    except (AutomationError, FileNotFoundError, ValueError, OSError) as error:
        reporter.emit("error", error=str(error), message=str(error))
        return 2
    except InterruptedError:
        reporter.emit("cancelled", message="LocalSR processing was cancelled.")
        return 130
    except Exception as error:  # noqa: BLE001 - CLI must return a machine-readable failure
        reporter.emit("error", error=str(error), message=f"LocalSR failed: {error}")
        return 1
    finally:
        runner.close()


def _validate_automation_args(args: argparse.Namespace) -> None:
    if args.command == "benchmark":
        return
    if args.scale < 1 or args.tile_size < 16 or args.halo < 0:
        raise AutomationError("Scale, tile size, and halo values are out of range.")
    if not 1 <= args.jpeg_quality <= 100 or not 0 <= args.crf <= 51:
        raise AutomationError("Image or video quality is out of range.")
    from localsr.core.media_codecs import validate_output

    try:
        validate_output(args.video_codec, args.video_container)
    except ValueError as error:
        raise AutomationError(str(error)) from error
    if args.video_codec in {"h264", "hevc"} and not args.external_ffmpeg:
        raise AutomationError(
            "H.264/HEVC export uses the FFmpeg installed on this computer; pass --external-ffmpeg."
        )
    if args.command == "watch":
        if (
            not math.isfinite(args.stable_seconds)
            or args.stable_seconds < 0
            or not math.isfinite(args.poll_seconds)
            or args.poll_seconds <= 0
        ):
            raise AutomationError("Watch timing values must be finite and non-negative.")
