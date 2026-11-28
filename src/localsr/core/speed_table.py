"""What each model step costs on this computer, learned from real jobs.

A step's share of a job's progress bar is its expected time, and the same model runs at very
different speeds on the Neural Engine, a GPU or the CPU, so no formula over image size
predicts it. After a step finishes, its measured seconds per megapixel (seconds per run for
a step that always processes one fixed-size image) are folded into a small table kept next to
the other caches; the next job reads it to weigh its steps. Standard library only.
"""

from __future__ import annotations

import json
import logging
import math
import os
import sys
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SPEED_TABLE_ENV = "LOCALSR_SPEED_TABLE"
FORMAT_VERSION = 1
MAX_ENTRIES = 300
# Each new measurement moves the stored speed this far toward itself.
SMOOTHING = 0.5


def speed_table_path() -> Path:
    override = os.environ.get(SPEED_TABLE_ENV)
    if override:
        return Path(override)
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches" / "LocalSR"
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "LocalSR"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "LocalSR"
    return base / "speeds.json"


def _entry_key(subject: str, backend: str, precision: str) -> str:
    return f"{subject}|{backend}|{precision}"


def _valid_entry(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    texts = all(isinstance(entry.get(name), str) for name in ("subject", "backend", "request"))
    speed, updated = entry.get("seconds"), entry.get("updated")
    numbers = all(
        isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)
        for value in (speed, updated)
    )
    return texts and numbers and speed > 0 and isinstance(entry.get("precision"), str)


class SpeedTable:
    """A bounded JSON table of measured speeds; every failure leaves the job unharmed."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path if path is not None else speed_table_path()

    def _load(self) -> dict[str, dict[str, Any]]:
        """The stored entries; a missing, broken or foreign file reads as empty."""
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(raw, dict) or raw.get("version") != FORMAT_VERSION:
            return {}
        entries = raw.get("entries")
        if not isinstance(entries, dict):
            return {}
        return {
            key: entry
            for key, entry in entries.items()
            if isinstance(key, str) and _valid_entry(entry)
        }

    def lookup(self, subject: str, request: str) -> float | None:
        """Seconds per unit of work last measured for this step under this request: the
        device, precision and settings the job asked for, whatever they resolved to."""
        best: dict[str, Any] | None = None
        for entry in self._load().values():
            if entry["subject"] != subject or entry["request"] != request:
                continue
            if best is None or entry["updated"] >= best["updated"]:
                best = entry
        return None if best is None else float(best["seconds"])

    def record(
        self, subject: str, backend: str, precision: str, request: str, seconds: float
    ) -> None:
        """Fold one measurement in. Never raises: a table that cannot be written is only a
        table that learns nothing."""
        if not (math.isfinite(seconds) and seconds > 0):
            return
        try:
            entries = self._load()
            key = _entry_key(subject, backend, precision)
            previous = entries.get(key)
            if previous is not None:
                seconds = previous["seconds"] * (1 - SMOOTHING) + seconds * SMOOTHING
            entries[key] = {
                "subject": subject,
                "backend": backend,
                "precision": precision,
                "request": request,
                "seconds": seconds,
                "updated": time.time(),
            }
            if len(entries) > MAX_ENTRIES:
                newest = sorted(entries.items(), key=lambda item: item[1]["updated"])
                entries = dict(newest[len(entries) - MAX_ENTRIES :])
            self._write({"version": FORMAT_VERSION, "entries": entries})
        except (OSError, ValueError, TypeError):
            logger.debug("Could not update the speed table", exc_info=True)

    def _write(self, value: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f".{os.getpid()}.tmp")
        try:
            temporary.write_text(json.dumps(value), encoding="utf-8")
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
