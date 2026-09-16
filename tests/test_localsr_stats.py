from __future__ import annotations

import importlib.util
import json
import socket
import sqlite3
import sys
import threading
import time
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "_localsr_stats", ROOT / "packaging/hosting/stats/localsr_stats.py"
)
assert spec and spec.loader
stats = importlib.util.module_from_spec(spec)
sys.modules["_localsr_stats"] = stats
spec.loader.exec_module(stats)

BROWSER = "Mozilla/5.0 (Macintosh) Safari/605"
NOW = 1_789_500_000.0  # 2026-09-15 UTC


def entry(uri: str, status: int = 204, ip: str = "203.0.113.7", agent: str = BROWSER, ts=NOW):
    return {
        "msg": "handled request",
        "ts": ts,
        "status": status,
        "request": {
            "method": "GET",
            "uri": uri,
            "client_ip": ip,
            "remote_ip": "127.0.0.1",
            "headers": {"User-Agent": [agent]},
        },
    }


def daily(store) -> dict:
    return {
        (kind, key): (uniques, requests)
        for _, kind, key, uniques, requests in store.db.execute("SELECT * FROM daily")
    }


def test_visits_count_unique_visitors_and_every_view(tmp_path):
    store = stats.Store(tmp_path / "s.db")
    assert store.record(entry("/hit?p=/localsr/&r=news.ycombinator.com"), NOW)
    assert store.record(entry("/hit?p=/localsr/&r="), NOW)
    assert store.record(entry("/hit?p=/localsr/download/&r=herrei.github.io"), NOW)
    assert store.record(entry("/hit?p=/localsr/", ip="198.51.100.2"), NOW)
    counts = daily(store)
    assert counts[("visitor", "")] == (2, 4)
    assert counts[("view", "/localsr/")] == (2, 3)
    assert counts[("referrer", "news.ycombinator.com")] == (1, 1)
    assert ("referrer", "herrei.github.io") not in counts


def test_rejects_bots_bad_input_and_uncounted_requests(tmp_path):
    store = stats.Store(tmp_path / "s.db")
    rejected = [
        entry("/hit?p=/localsr/", agent="Googlebot/2.1"),
        entry("/hit?p=/localsr/", agent=""),
        entry("/hit?p=localsr"),
        entry("/hit?p=/a%0Ab"),
        entry("/hit?p=/localsr/", status=405),
        entry("/ping/update-check?v=0.1.2-beta&t=darwin&c=<script>"),
        entry("/releases/v0.1.1-beta/LocalSR.dmg", status=404),
        entry("/releases/v0.1.1-beta/LocalSR.dmg", agent="python-urllib/3.14"),
        entry("/wp-login.php", status=200),
        {**entry("/hit?p=/"), "msg": "other"},
        {**entry("/hit?p=/"), "request": {**entry("/hit?p=/")["request"], "method": "HEAD"}},
    ]
    assert not any(store.record(e, NOW) for e in rejected)
    assert daily(store) == {}


def test_downloads_and_update_checks_deduplicate_per_day(tmp_path):
    store = stats.Store(tmp_path / "s.db")
    dmg = "/releases/v0.1.1-beta/LocalSR-v0.1.1-beta-macOS-arm64.dmg"
    store.record(entry(dmg, 200), NOW)
    store.record(entry(dmg, 206), NOW)
    store.record(entry(dmg, 200, ts=NOW + 86400), NOW + 86400)
    ping = "/ping/update-check?v=0.1.2-beta&t=darwin-aarch64-mps-native&c=beta"
    store.record(entry(ping, agent="LocalSR/0.1.2-beta"), NOW)
    store.record(entry(ping, agent="LocalSR/0.1.2-beta"), NOW)
    rows = store.db.execute("SELECT day, kind, uniques, requests FROM daily ORDER BY day, kind")
    assert rows.fetchall() == [
        ("2026-09-15", "download", 1, 2),
        ("2026-09-15", "install", 1, 2),
        ("2026-09-15", "update_check", 1, 2),
        ("2026-09-16", "download", 1, 1),
    ]


def test_salts_and_hashes_are_deleted_after_two_days(tmp_path):
    store = stats.Store(tmp_path / "s.db")
    store.record(entry("/hit?p=/"), NOW)
    store.record(entry("/hit?p=/", ts=NOW + 86400), NOW + 86400)
    assert store.db.execute("SELECT COUNT(*) FROM salts").fetchone()[0] == 2
    store.record(entry("/hit?p=/", ts=NOW + 2 * 86400), NOW + 2 * 86400)
    days = {r[0] for r in store.db.execute("SELECT day FROM salts UNION SELECT day FROM seen")}
    assert days == {"2026-09-16", "2026-09-17"}
    # A late line for a pruned day must not create a fresh salt and double count.
    assert not store.record(entry("/hit?p=/", ts=NOW), NOW + 2 * 86400)
    assert (
        store.db.execute("SELECT SUM(uniques) FROM daily WHERE kind='visitor'").fetchone()[0] == 3
    )


def test_ip_addresses_never_reach_the_database(tmp_path):
    path = tmp_path / "s.db"
    store = stats.Store(path)
    store.record(entry("/hit?p=/", ip="203.0.113.99"), NOW)
    store.db.close()
    for suffix in ("", "-wal"):
        file = Path(str(path) + suffix)
        if file.exists():
            assert b"203.0.113.99" not in file.read_bytes()
            assert BROWSER.encode() not in file.read_bytes()


def test_dashboard_escapes_visitor_supplied_text(tmp_path):
    store = stats.Store(tmp_path / "s.db")
    store.record(entry("/hit?p=/<img src=x onerror=alert(1)>"), NOW)
    page = stats.render(stats.summary(store.db, stats.day_of(NOW)), NOW)
    assert "<img src=x" not in page
    assert "&lt;img src=x" in page
    assert "No counted request in the last 7 days" not in page


def test_summary_groups_downloads(tmp_path):
    store = stats.Store(tmp_path / "s.db")
    for name in (
        "LocalSR-v0.1.1-beta-macOS-arm64.dmg",
        "LocalSR-v0.1.1-beta-macOS-arm64.app.tar.gz",
        "LocalSR-v0.1.1-beta-macOS-arm64.app.tar.gz.sig",
        "SHA256SUMS",
    ):
        store.record(entry(f"/releases/v0.1.1-beta/{name}", 200), NOW)
    data = stats.summary(store.db, stats.day_of(NOW))
    assert data["totals"]["downloads"] == 2
    assert {d["group"] for d in data["downloads"]} == {"Installers", "In-app updates"}


def test_backup_keeps_totals_but_not_hashes(tmp_path):
    db = tmp_path / "s.db"
    store = stats.Store(db)
    store.record(entry("/hit?p=/"), NOW)
    stats.backup(Namespace(db=db, dir=tmp_path / "backups", keep=2))
    (copy,) = (tmp_path / "backups").glob("stats-*.sqlite3")
    with sqlite3.connect(copy) as con:
        assert con.execute("SELECT COUNT(*) FROM daily").fetchone()[0] == 2
        assert con.execute("SELECT COUNT(*) FROM seen").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM salts").fetchone()[0] == 0


def test_log_stream_end_to_end(tmp_path):
    store = stats.Store(tmp_path / "s.db")
    server = stats.LogServer(("127.0.0.1", 0), stats.LogHandler)
    server.store = store
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with socket.create_connection(server.server_address) as conn:
            conn.sendall(b"not json\n" + b"x" * (stats.MAX_LINE + 10) + b"\n")
            conn.sendall(json.dumps(entry("/hit?p=/localsr/", ts=time.time())).encode() + b"\n")
        for _ in range(100):
            if store.db.execute("SELECT COUNT(*) FROM daily").fetchone()[0]:
                break
            time.sleep(0.02)
        assert (
            store.db.execute("SELECT SUM(requests) FROM daily WHERE kind='view'").fetchone()[0] == 1
        )
    finally:
        server.shutdown()
        server.server_close()
