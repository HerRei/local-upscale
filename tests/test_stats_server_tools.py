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


def test_visitor_chosen_keys_stop_growing_the_database_each_day(tmp_path, monkeypatch):
    monkeypatch.setattr(stats, "MAX_KEYS_PER_DAY", 3)
    store = stats.Store(tmp_path / "s.db")
    for number in range(10):
        uri = f"/hit?p=/page-{number}&r=site{number}.example.com"
        assert store.record(entry(uri, ip=f"203.0.113.{number}"), NOW)
        assert store.record(entry(f"/ping/update-check?v=1.{number}&t=mac&c=beta"), NOW)
    rows = store.db.execute("SELECT kind, key, requests FROM daily").fetchall()
    for kind in ("view", "referrer", "update_check"):
        keys = [row for row in rows if row[0] == kind]
        assert len(keys) <= 4
        assert sum(row[2] for row in keys) == 10
    # A key that is already counted keeps counting after the day's limit is reached.
    assert store.record(entry("/hit?p=/page-0"), NOW)
    assert store.db.execute(
        "SELECT requests FROM daily WHERE kind = 'view' AND key = '/page-0'"
    ).fetchone() == (2,)
    # The next day starts with a fresh allowance.
    assert store.record(entry("/hit?p=/page-new", ts=NOW + DAY), NOW + DAY)
    assert store.db.execute(
        "SELECT COUNT(*) FROM daily WHERE kind = 'view' AND key = '/page-new'"
    ).fetchone() == (1,)
