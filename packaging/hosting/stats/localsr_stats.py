#!/usr/bin/env python3
"""Count LocalSR website visits, release downloads and app update checks.

Caddy streams its JSON access log to `serve` over loopback TCP. Nothing but
daily totals is kept: repeat requests are recognised with a salted hash whose
salt and hashes are deleted after two days, and IP addresses never reach disk.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import os
import re
import secrets
import socketserver
import sqlite3
import sys
import threading
import time
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

SCHEMA = """
CREATE TABLE IF NOT EXISTS salts(day TEXT PRIMARY KEY, salt BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS seen(
    day TEXT, kind TEXT, key TEXT, visitor TEXT,
    PRIMARY KEY(day, kind, key, visitor)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS daily(
    day TEXT, kind TEXT, key TEXT,
    uniques INTEGER NOT NULL DEFAULT 0, requests INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(day, kind, key)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS meta(name TEXT PRIMARY KEY, value TEXT NOT NULL);
"""
# Today and yesterday stay deduplicated so late log lines are not counted twice.
SEEN_DAYS = 2
BOT = re.compile(
    r"bot|crawl|spider|slurp|scan|monitor|preview|headless|lighthouse|facebookexternalhit"
    r"|curl|wget|python|go-http|java/|okhttp|libwww|httpclient",
    re.IGNORECASE,
)
TOKEN = re.compile(r"[A-Za-z0-9._+-]{1,80}")
HOSTNAME = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?")
OWN_HOSTS = {"herrei.github.io"}
MAX_LINE = 65536


def day_of(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.UTC).date().isoformat()


def shift(day: str, days: int) -> str:
    return (dt.date.fromisoformat(day) + dt.timedelta(days=days)).isoformat()


def classify(entry: dict) -> tuple[str, str, list[tuple[str, str]]] | None:
    """Return (client ip, user agent, [(kind, key)]) for a countable request."""
    if entry.get("msg") != "handled request":
        return None
    request = entry.get("request")
    if not isinstance(request, dict) or request.get("method") != "GET":
        return None
    ip = str(request.get("client_ip") or request.get("remote_ip") or "")
    headers = request.get("headers") if isinstance(request.get("headers"), dict) else {}
    agent = " ".join(str(v) for v in headers.get("User-Agent") or [])[:400]
    parts = urlsplit(str(request.get("uri") or ""))
    path = unquote(parts.path)
    query = parse_qs(parts.query, max_num_fields=8)
    status = entry.get("status")

    def arg(name: str) -> str:
        return (query.get(name) or [""])[0]

    if not ip:
        return None
    if path == "/hit" and status == 204:
        page = arg("p")
        if not agent or BOT.search(agent) or not page.startswith("/") or len(page) > 200:
            return None
        if any(ord(c) < 32 or ord(c) == 127 for c in page):
            return None
        counts = [("visitor", ""), ("view", page)]
        referrer = arg("r").lower()
        if referrer and referrer not in OWN_HOSTS and HOSTNAME.fullmatch(referrer):
            counts.append(("referrer", referrer))
    elif path == "/ping/update-check" and status == 204:
        values = [arg("v"), arg("t"), arg("c")]
        if not all(TOKEN.fullmatch(v) for v in values):
            return None
        counts = [("install", ""), ("update_check", " ".join(values))]
    elif path.startswith("/releases/") and status in (200, 206):
        if BOT.search(agent):
            return None
        counts = [("download", path.removeprefix("/releases/")[:300])]
    else:
        return None
    return ip, agent, counts


class Store:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()
        self.pruned_for = ""

    def _salt(self, day: str) -> bytes:
        self.db.execute(
            "INSERT OR IGNORE INTO salts VALUES (?, ?)", (day, secrets.token_bytes(32))
        )
        return self.db.execute("SELECT salt FROM salts WHERE day = ?", (day,)).fetchone()[0]

    def _prune(self, today: str) -> str:
        cutoff = shift(today, 1 - SEEN_DAYS)
        if self.pruned_for != today:
            self.db.execute("DELETE FROM seen WHERE day < ?", (cutoff,))
            self.db.execute("DELETE FROM salts WHERE day < ?", (cutoff,))
            self.pruned_for = today
        return cutoff

    def record(self, entry: dict, now: float | None = None) -> bool:
        found = classify(entry)
        if not found:
            return False
        now = time.time() if now is None else now
        try:
            ts = float(entry.get("ts") or now)
        except (TypeError, ValueError):
            ts = now
        ip, agent, counts = found
        today, day = day_of(now), day_of(min(ts, now))
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                if day < self._prune(today):
                    self.db.execute("COMMIT")
                    return False
                visitor = hashlib.sha256(
                    self._salt(day) + ip.encode() + b"\0" + agent.encode()
                ).hexdigest()[:32]
                for kind, key in counts:
                    new = self.db.execute(
                        "INSERT OR IGNORE INTO seen VALUES (?, ?, ?, ?)",
                        (day, kind, key, visitor),
                    ).rowcount
                    self.db.execute(
                        "INSERT INTO daily VALUES (?, ?, ?, ?, 1) ON CONFLICT(day, kind, key) "
                        "DO UPDATE SET uniques = uniques + excluded.uniques, requests = requests + 1",
                        (day, kind, key, new),
                    )
                self.db.execute(
                    "INSERT OR REPLACE INTO meta VALUES ('last_event', ?)", (str(int(now)),)
                )
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
        return True


class LogHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        store: Store = self.server.store  # type: ignore[attr-defined]
        while line := self.rfile.readline(MAX_LINE + 1):
            if len(line) > MAX_LINE:
                while line and not line.endswith(b"\n"):
                    line = self.rfile.readline(MAX_LINE + 1)
                continue
            try:
                entry = json.loads(line)
                if isinstance(entry, dict):
                    store.record(entry)
            except ValueError:
                continue
            except sqlite3.Error as error:
                print(f"localsr-stats: could not record: {error}", file=sys.stderr, flush=True)


class LogServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


def read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


DOWNLOAD_GROUPS = (
    ("Installers", re.compile(r"\.(dmg|exe|msi|msix|appimage|deb|rpm)$", re.I)),
    ("In-app updates", re.compile(r"\.app\.tar\.gz$|\.engine\.tar\.gz\.part-\d+$", re.I)),
    ("Source and notices", re.compile(r"source|sources|notices", re.I)),
)


def download_group(key: str) -> str | None:
    if re.search(r"\.sig$|SHA256SUMS$|\.json$|superseded", key):
        return None
    for name, pattern in DOWNLOAD_GROUPS:
        if pattern.search(key):
            return name
    return "Other"


def summary(db: sqlite3.Connection, today: str) -> dict:
    def rows(sql: str, *args) -> list[tuple]:
        return db.execute(sql, args).fetchall()

    month = shift(today, -29)
    daily: dict[str, dict[str, int]] = {
        shift(today, -i): {"visitors": 0, "views": 0, "downloads": 0, "installs": 0}
        for i in range(30)
    }
    totals = {"visitors": 0, "views": 0, "downloads": 0, "update_checks": 0}
    by_file: dict[str, list[int]] = {}
    for day, kind, key, uniques, requests in rows("SELECT * FROM daily"):
        slot = daily.get(day)
        if kind == "visitor":
            totals["visitors"] += uniques
            if slot:
                slot["visitors"] += uniques
        elif kind == "view":
            totals["views"] += requests
            if slot:
                slot["views"] += requests
        elif kind == "download" and download_group(key):
            totals["downloads"] += uniques
            by_file.setdefault(key, [0, 0])[0] += uniques
            if day >= month:
                by_file[key][1] += uniques
            if slot:
                slot["downloads"] += uniques
        elif kind == "update_check":
            totals["update_checks"] += requests
        elif kind == "install" and slot:
            slot["installs"] += uniques
    last = rows("SELECT value FROM meta WHERE name = 'last_event'")
    return {
        "today": today,
        "last_event": int(last[0][0]) if last else None,
        "totals": totals,
        "daily": [{"day": d, **v} for d, v in sorted(daily.items())],
        "downloads": sorted(
            ({"file": k, "group": download_group(k), "all_time": a, "last_30_days": m}
             for k, (a, m) in by_file.items()),
            key=lambda item: item["file"],
            reverse=True,
        ),
        "pages": rows(
            "SELECT key, SUM(requests), SUM(uniques) FROM daily WHERE kind = 'view' AND day >= ? "
            "GROUP BY key ORDER BY 2 DESC LIMIT 25", month),
        "referrers": rows(
            "SELECT key, SUM(uniques) FROM daily WHERE kind = 'referrer' AND day >= ? "
            "GROUP BY key ORDER BY 2 DESC LIMIT 25", month),
        "versions": rows(
            "SELECT key, SUM(uniques), MAX(day) FROM daily WHERE kind = 'update_check' "
            "AND day >= ? GROUP BY key ORDER BY 3 DESC, 2 DESC", shift(today, -6)),
    }


def render(data: dict, now: float) -> str:
    e = html.escape
    last = data["last_event"]
    stale = last is None or now - last > 7 * 86400
    last_text = (
        "never" if last is None
        else dt.datetime.fromtimestamp(last, dt.UTC).strftime("%Y-%m-%d %H:%M UTC")
    )
    peak = max([1] + [d["visitors"] for d in data["daily"]])

    def table(head: list[str], body: list) -> str:
        cells = "".join(
            "<tr>" + "".join(f"<td>{e(str(c))}</td>" for c in row) + "</tr>" for row in body
        )
        heads = "".join(f"<th>{e(h)}</th>" for h in head)
        return f"<table><thead><tr>{heads}</tr></thead><tbody>{cells or '<tr><td>None yet</td></tr>'}</tbody></table>"

    days = "".join(
        f"<tr><td>{d['day']}</td><td class=bar><span style='width:{80 * d['visitors'] // peak}%'></span>"
        f"{d['visitors']}</td><td>{d['views']}</td><td>{d['downloads']}</td><td>{d['installs']}</td></tr>"
        for d in reversed(data["daily"])
    )
    t = data["totals"]
    warning = (
        "<p class=warn>No counted request in the last 7 days. Check <code>localsr-stats</code>, "
        "the Caddy <code>log</code> block and Tailscale Funnel.</p>" if stale else ""
    )
    return f"""<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>LocalSR stats</title>
<style>
:root{{color-scheme:light dark;--bg:#f6f5f1;--fg:#1d1d1b;--mute:#6b6a64;--line:#dddbd2;--bar:#c9d8f0}}
@media(prefers-color-scheme:dark){{:root{{--bg:#17181a;--fg:#ececea;--mute:#9c9b95;--line:#2e2f33;--bar:#2c4468}}}}
body{{margin:0;padding:24px max(16px,4vw);background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif}}
h1{{margin:0 0 4px}}h2{{margin:32px 0 8px;font-size:16px}}.mute{{color:var(--mute)}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-top:20px}}
.tile{{border:1px solid var(--line);border-radius:10px;padding:12px}}.tile b{{display:block;font-size:26px;font-variant-numeric:tabular-nums}}
.wrap{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}}
td,th{{text-align:left;padding:5px 10px 5px 0;border-bottom:1px solid var(--line);white-space:nowrap}}
td.bar{{position:relative;min-width:140px}}td.bar span{{position:absolute;inset:4px auto 4px 0;background:var(--bar);z-index:-1;border-radius:3px}}
.warn{{border:1px solid #c58b00;border-radius:8px;padding:10px}}
</style>
<h1>LocalSR stats</h1><p class=mute>Last counted request: {e(last_text)} · all dates UTC ·
<a href="daily.json">JSON</a></p>{warning}
<div class=tiles>
<div class=tile><b>{t['visitors']}</b>website visitors, all time<br><span class=mute>unique per day, summed</span></div>
<div class=tile><b>{t['views']}</b>page views, all time</div>
<div class=tile><b>{t['downloads']}</b>downloads, all time<br><span class=mute>per file, per person, per day</span></div>
<div class=tile><b>{t['update_checks']}</b>app update checks, all time</div>
</div>
<h2>Last 30 days</h2><div class=wrap><table><thead><tr><th>Day</th><th>Visitors</th><th>Views</th>
<th>Downloads</th><th>Active installs</th></tr></thead><tbody>{days}</tbody></table></div>
<h2>Downloads by file</h2><div class=wrap>{table(["File", "Kind", "All time", "Last 30 days"], [(d["file"], d["group"], d["all_time"], d["last_30_days"]) for d in data["downloads"]])}</div>
<h2>App versions checking for updates, last 7 days</h2><p class=mute>Install-days: one per install per day.</p>
<div class=wrap>{table(["Version · target · channel", "Install-days", "Last seen"], data["versions"])}</div>
<h2>Pages, last 30 days</h2><div class=wrap>{table(["Page", "Views", "Visitors"], data["pages"])}</div>
<h2>Referring websites, last 30 days</h2><div class=wrap>{table(["Website", "Visitors"], data["referrers"])}</div>
</html>"""


def dashboard_handler(path: Path) -> type[BaseHTTPRequestHandler]:
    class Dashboard(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            route = urlsplit(self.path).path
            if route not in ("/", "/daily.json", "/healthz"):
                return self.reply(404, "text/plain", b"Not found")
            if route == "/healthz":
                return self.reply(200, "text/plain", b"ok")
            now = time.time()
            with closing(read_only(path)) as db:
                data = summary(db, day_of(now))
            if route == "/daily.json":
                body = json.dumps(data, indent=2).encode()
                return self.reply(200, "application/json", body)
            self.reply(200, "text/html; charset=utf-8", render(data, now).encode())

        def reply(self, status: int, kind: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            pass

    return Dashboard


def run(args: argparse.Namespace) -> None:
    store = Store(args.db)
    logs = LogServer(("127.0.0.1", args.log_port), LogHandler)
    logs.store = store  # type: ignore[attr-defined]
    web = ThreadingHTTPServer(("127.0.0.1", args.dashboard_port), dashboard_handler(args.db))
    web.daemon_threads = True
    threading.Thread(target=web.serve_forever, daemon=True).start()
    print(f"localsr-stats: logs on :{args.log_port}, dashboard on :{args.dashboard_port}", flush=True)
    logs.serve_forever()


def backup(args: argparse.Namespace) -> None:
    """Copy the totals only: salts and same-day hashes stay out of backups."""
    args.dir.mkdir(parents=True, exist_ok=True)
    target = args.dir / f"stats-{dt.datetime.now(dt.UTC):%Y%m%d}.sqlite3"
    partial = target.with_name(target.name + ".partial")
    partial.unlink(missing_ok=True)
    with closing(read_only(args.db)) as source, closing(sqlite3.connect(partial)) as copy:
        source.backup(copy)
        copy.execute("DELETE FROM seen")
        copy.execute("DELETE FROM salts")
        copy.commit()
        copy.execute("VACUUM")
    os.replace(partial, target)
    for old in sorted(args.dir.glob("stats-*.sqlite3"))[: -args.keep]:
        old.unlink()
    print(f"localsr-stats: wrote {target}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="receive Caddy logs and serve the dashboard")
    serve.add_argument("--db", type=Path, required=True)
    serve.add_argument("--log-port", type=int, default=8791)
    serve.add_argument("--dashboard-port", type=int, default=8792)
    save = sub.add_parser("backup", help="write a dated copy of the daily totals")
    save.add_argument("--db", type=Path, required=True)
    save.add_argument("--dir", type=Path, required=True)
    save.add_argument("--keep", type=int, default=90)
    args = parser.parse_args(argv)
    (run if args.command == "serve" else backup)(args)


if __name__ == "__main__":
    main()
