# LocalSR Mac mini download host

Updated 13 September 2026. The active infrastructure record is in [macmini-infra](https://github.com/HerRei/macmini-infra/blob/main/docs/localsr-downloads.md).

# Mac mini: builds and release downloads

Verified 13 September 2026. The Mac mini runs Linux and has two separate services:

| Service | Purpose | Exposure |
| --- | --- | --- |
| `localsr-artifact-server.service` | Authenticated CI artifact receiver rooted at `/mnt/hdd/ci-artifacts` | Existing private LAN service on port 8000 |
| `localsr-downloads.service` | Caddy 2.11.4 static download server | Loopback `127.0.0.1:8788`, reached publicly through Tailscale Funnel on HTTPS |

The download service was added without restarting the artifact receiver, runner, VMs or host. The receiver retained its PID and activation time during setup. No release build, checkout, cache or workflow was changed. Do not expose the CI receiver or its artifact root publicly.

## Storage and resource limits

- Program/configuration: `/opt/localsr-downloads/`.
- Public root: `/mnt/hdd/localsr-downloads/public/`.
- Private preparation: `/mnt/hdd/localsr-downloads/staging/`, mode 0700.
- Unit: `/etc/systemd/system/localsr-downloads.service`.
- DynamicUser, read-only system access, 20% of one CPU, 128 MiB memory, low CPU/IO weight, nice 15.
- GET/HEAD only; no directory listing, uploads, hidden files or partial-file downloads. No access log is configured.

The current [Caddyfile](https://github.com/HerRei/macmini-infra/blob/main/services/observed/localsr-public-https-20260913/Caddyfile) and [unit](https://github.com/HerRei/macmini-infra/blob/main/services/observed/localsr-downloads-20260913/localsr-downloads.service) are recorded here. Boot startup is enabled; a host reboot was not tested because a release build is active.

## Tests and external access

Public address: **https://macmini-ci.tail34a4e0.ts.net/**. Ordinary visitors need no GitHub or Tailscale account. The endpoint uses background Tailscale Funnel to proxy only the read-only download root. Port 8000 and the private CI receiver remain separate.

Public HTTPS acceptance from an external client passed: full 1 MiB download and SHA-256, HEAD, byte ranges, two-part resume, conditional requests, four concurrent downloads, no directory listing, hidden/staging rejection and rejection of POST. The bounded test took 5.383 seconds in total; this is not a guaranteed download speed or sustained-load capacity measurement. Its disposable fixture was removed.

A real routing issue was corrected: a Caddy site limited to the loopback hostname returned an empty response to the forwarded public hostname. The current site accepts the forwarded host while its socket remains bound to loopback. Only the new download service was restarted.

Funnel is configured with `tailscale funnel --bg --https=443 http://127.0.0.1:8788`. Both `tailscaled.service` and the download unit are enabled for boot. Background configuration persists independently of the SSH connection. No host or Tailscale restart was used to test persistence. Service uptime still depends on the Mac mini, its HDD, power, internet connection and Tailscale. Funnel has non-configurable bandwidth limits and no download-throughput guarantee. [Official Funnel documentation](https://tailscale.com/docs/features/tailscale-funnel).

The public root currently contains only a status marker. Binary downloads remain in private staging until their distribution materials are complete. [Public HTTPS evidence](https://github.com/HerRei/macmini-infra/blob/main/snapshots/2026-09-13-localsr-public-https.json) is separate from the earlier loopback-only snapshot.

## Persistence and tested recovery

The root system service is enabled under `multi-user.target`, so it is independent of an SSH session. `RequiresMountsFor` orders startup after the HDD path is mounted. `Restart=on-failure` restarts failed processes after five seconds; bounded restart-rate protection avoids an endless failure loop.

A controlled SIGKILL targeted **only the new Caddy service process**. Systemd automatically restarted it with a new PID, incremented its restart counter, and restored successful HTTP requests within the bounded test. The host boot ID and existing CI receiver PID remained unchanged. No VM, runner, CI receiver or host was restarted. Manual restart of only the download unit also passed.

This verifies service failure recovery and boot enablement. It does **not** prove host reboot, firmware power recovery, disk failure recovery, or sustained public availability. No host reboot or power cycle was performed. Existing host playbooks still represent the original baseline and do not install this additive service; use the separately recorded unit/configuration when planning its reconstruction.

The [separately dated evidence](https://github.com/HerRei/macmini-infra/blob/main/snapshots/2026-09-13-localsr-download-service.json) retains exact upstream binary/archive and installed configuration hashes without changing the September 6 baseline.

## Artifacts and publication blockers

The website's `v0.0.11-alpha` links point into private `HerRei/local-upscale`; unauthenticated requests return 404. Recovered copies of all three assets match the published hashes:

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| macOS ARM64 DMG | 282671268 | `a6bb566d43e5ca112138649d8ad2e027f4e6d34d5528794a177c1b9b1e84de52` |
| Windows x86-64 EXE | 189010338 | `a5bf250a77a179dc41639b6a0ee41139eb41bfb5f7a846be7e84b62539a98227` |
| Linux x86-64 AppImage | 456522232 | `fdb6678f53b6589c6498c714b4af92b18e6ba0f85be7e04afe1a87a85a952792` |

The latest locally installed Mac app identifies itself as `0.0.13-beta.1`. Packaged CPU/MPS processing and headless startup checks passed. The installed app remains an ad-hoc local preview. A separate `.13-alpha` DMG is now Developer ID signed, Apple notarized and stapled. Gatekeeper accepted both the DMG and its mounted app; the mounted host/worker handshake and six CPU plus six MPS processing/recovery checks passed. Its release metadata explicitly documents the unchanged preview engine version. Do not describe it as the completed public beta.

The signed DMG is `LocalSR-v0.0.13-alpha-macOS-arm64.dmg`, 454153968 bytes, SHA-256 `2c5d3df5fc6828c1a60f1e4d3ba222398183c747742deb961e054ebf60086cc3`. It is retained privately for release preparation. Apple notarization is a software-trust check, not verification of third-party redistribution rights.

Restoring old public downloads and publishing the new binary require the actual redistribution/source obligations to be satisfied. The beta native-codec review remains open: bundled FFmpeg/x264/x265 components impose source/license requirements. An MIT app notice or an alpha label does not replace them. [FFmpeg licensing guidance](https://ffmpeg.org/legal.html).

Only reviewed immutable packages and their required sources/notices belong under `public/releases/<version>/`. Keep checkpoints, signing keys, media, credentials and CI state outside that root. Generate download hashes/update signatures from actual files; private preparation paths are not download URLs.

## Operation

Read-only status:

```sh
systemctl status localsr-downloads.service --no-pager
curl --fail http://127.0.0.1:8788/status.txt
systemctl show localsr-artifact-server.service -p MainPID -p ActiveEnterTimestamp
```

Validate changes before restarting only this service:

```sh
/opt/localsr-downloads/caddy validate --config /opt/localsr-downloads/Caddyfile --adapter caddyfile
sudo systemctl restart localsr-downloads.service
```

To withdraw public access, run `sudo tailscale funnel --https=443 off`. To stop the download server, disable only `localsr-downloads.service`. Do not reset Tailscale, stop the artifact receiver, reboot or alter runner configuration. Retain published version directories for existing links and recovery.

Support: hermes.reisner@gmail.com.
