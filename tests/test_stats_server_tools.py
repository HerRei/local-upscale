"""The usage counter's collector: failure handling and bounded growth."""

from __future__ import annotations

import sqlite3

import pytest
from test_localsr_stats import NOW, entry, stats

DAY = 86400


def test_a_failed_first_record_of_the_day_does_not_skip_the_day_s_pruning(tmp_path, monkeypatch):
    store = stats.Store(tmp_path / "s.db")
    assert store.record(entry("/hit?p=/localsr/"), NOW)
    assert store.db.execute("SELECT COUNT(*) FROM seen").fetchone()[0] > 0

    later = NOW + 3 * DAY
    real_salt = store._salt

    def failing(day):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(store, "_salt", failing)
    with pytest.raises(sqlite3.OperationalError):
        store.record(entry("/hit?p=/localsr/", ts=later), later)
    # The rollback also undid the deletions, so the old day is still there.
    assert store.db.execute("SELECT COUNT(*) FROM salts").fetchone()[0] == 1

    monkeypatch.setattr(store, "_salt", real_salt)
    assert store.record(entry("/hit?p=/localsr/", ip="198.51.100.2", ts=later), later)
    days = {row[0] for row in store.db.execute("SELECT day FROM salts")}
    assert days == {stats.day_of(later)}
    assert {row[0] for row in store.db.execute("SELECT day FROM seen")} == days
