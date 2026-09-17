# LocalSR usage counts

Installed on the Mac mini since 2026-09-15. Counts four things and keeps only daily totals:

| What | Sent by | Request | Counted as |
| --- | --- | --- | --- |
| Website visits | `localsr/count.js` on herrei.github.io (skipped with DNT/GPC) | `GET /hit?p=<path>&r=<referring host>` | unique visitors per day, every page view, referring sites |
| Downloads (Mac mini) | anyone fetching update files or older betas | `GET /releases/...` (200/206) | one per file, per person, per day |
| Downloads (GitHub) | installers and source bundles on GitHub Releases | hourly read of the public releases API | growth of GitHub's per-file totals (every download) |
| App use | app builds after 0.1.1-beta, can be turned off in Software Update | `GET /ping/update-check?v=&t=&c=` | active installs per day, by version/target/channel |

The first three go to `https://macmini-ci.tail34a4e0.ts.net` (Tailscale Funnel → Caddy on 127.0.0.1:8788).
Since 2026-09-17 the download page links installers on GitHub, so `localsr-stats-github.timer`
runs `localsr_stats.py github` every hour; it stores each asset's last total in `github_assets` and
adds only the growth to `daily` under `github/<tag>/<file>`.

## How it works

- Caddy (`Caddyfile` here = the live `/opt/localsr-downloads/Caddyfile`) answers `/hit` and
  `/ping/update-check` with 204, and streams JSON log lines for counted paths only to
  `localsr_stats.py serve` on 127.0.0.1:8791. No access log is written to disk.
- The collector hashes IP + user agent with a random per-day salt to recognise repeats. Salts and
  hashes are deleted after two days; only the `daily` table (day, kind, key, uniques, requests)
  is kept, forever. Tests: `tests/test_localsr_stats.py`.
- Database: `/mnt/hdd/localsr-stats/stats.sqlite3`. Nightly totals-only backups (90 kept) in
  `/mnt/hdd/localsr-stats/backups/` via `localsr-stats-backup.timer`. Both are on the same disk:
  copy a backup off the mini now and then.
- If the mini or the collector is down, counts for that time are lost; downloads keep working.

## Dashboard

Tailnet only: `https://macmini-ci.tail34a4e0.ts.net:8443/` (JSON at `/daily.json`).
From a Mac on userspace Tailscale: `ssh -N -L 8792:127.0.0.1:8792 macmini-remote`, then open
`http://127.0.0.1:8792/`. It warns when nothing has been counted for 7 days.

## Install or update

```sh
scp localsr_stats.py *.service *.timer Caddyfile macmini-remote:/tmp/stats/
# on the mini:
sudo useradd --system --no-create-home --shell /usr/sbin/nologin localsr-stats  # once
sudo install -d -o localsr-stats -g localsr-stats -m 0750 /mnt/hdd/localsr-stats /mnt/hdd/localsr-stats/backups
sudo install -m 0644 /tmp/stats/localsr_stats.py /opt/localsr-stats/
sudo install -m 0644 /tmp/stats/*.service /tmp/stats/*.timer /etc/systemd/system/
/opt/localsr-downloads/caddy validate --config /tmp/stats/Caddyfile --adapter caddyfile
sudo install -m 0644 /tmp/stats/Caddyfile /opt/localsr-downloads/Caddyfile
sudo systemctl daemon-reload
sudo systemctl enable --now localsr-stats.service localsr-stats-backup.timer localsr-stats-github.timer
sudo systemctl restart localsr-stats localsr-downloads
sudo tailscale serve --bg --https=8443 http://127.0.0.1:8792   # once, tailnet only
```

The previous Caddyfile is kept as `/opt/localsr-downloads/Caddyfile.before-stats-20260915`.

## Keep in step

- Changing what is counted or kept means updating the website privacy page
  (`HerRei.github.io/localsr/privacy/`) and `docs/updates.md` in the same change.
- New site pages must load `count.js`; `localsr/tools/validate_site.py` fails otherwise.
- Installers moved to GitHub Releases on 2026-09-17; the hourly GitHub read keeps counting them.
  Moving other downloads needs the same, or their counts stop.
