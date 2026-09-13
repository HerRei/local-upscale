# Mac mini: builds and release downloads

Verified 13 September 2026. The Mac mini runs Linux and has two separate services:

| Service | Purpose | Exposure |
| --- | --- | --- |
| `localsr-artifact-server.service` | Authenticated CI artifact receiver rooted at `/mnt/hdd/ci-artifacts` | Existing private LAN service on port 8000 |
| `localsr-downloads.service` | Caddy 2.11.4 static download server | Loopback only: `127.0.0.1:8788` |

The download service was added without restarting the artifact receiver, runner, VMs or host. The receiver retained its PID and activation time during setup. No release build, checkout, cache or workflow was changed. Do not expose the CI receiver or its artifact root publicly.

## Storage and resource limits

- Program/configuration: `/opt/localsr-downloads/`.
- Public root: `/mnt/hdd/localsr-downloads/public/`.
- Private preparation: `/mnt/hdd/localsr-downloads/staging/`, mode 0700.
- Unit: `/etc/systemd/system/localsr-downloads.service`.
- DynamicUser, read-only system access, 20% of one CPU, 128 MiB memory, low CPU/IO weight, nice 15.
- GET/HEAD only; no directory listing, uploads, hidden files or partial-file downloads. No access log is configured.

The installed [Caddyfile](../packaging/hosting/Caddyfile) and [unit](../packaging/hosting/localsr-downloads.service) are recorded here. Boot startup is enabled; a host reboot was not tested because a release build is active.

## Tests and external access

Loopback checks passed: full download SHA-256, HEAD, byte ranges, resumed content, ETag/304, four concurrent requests, hidden/staging rejection and read-only methods. These do not establish public HTTPS speed or availability.

No public endpoint or Funnel is enabled. The public root contains only a status marker. The public address still needs selecting. A free Tailscale Funnel address avoids a new domain but has non-configurable bandwidth limits. A custom domain needs DNS and reachable HTTPS. [Funnel requirements](https://tailscale.com/docs/features/tailscale-funnel#requirements-and-limitations).

## Artifacts and publication blockers

The website's `v0.0.11-alpha` links point into private `HerRei/local-upscale`; unauthenticated requests return 404. Recovered copies of all three assets match the published hashes:

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| macOS ARM64 DMG | 282671268 | `a6bb566d43e5ca112138649d8ad2e027f4e6d34d5528794a177c1b9b1e84de52` |
| Windows x86-64 EXE | 189010338 | `a5bf250a77a179dc41639b6a0ee41139eb41bfb5f7a846be7e84b62539a98227` |
| Linux x86-64 AppImage | 456522232 | `fdb6678f53b6589c6498c714b4af92b18e6ba0f85be7e04afe1a87a85a952792` |

The latest locally installed Mac app identifies itself as `0.0.13-beta.1`. Packaged CPU/MPS processing and headless startup checks passed. It is an ad-hoc signed local preview, not a notarized `.13-alpha` release. Do not silently relabel its internal version or describe it as an accepted public beta.

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

Rollback: disable only `localsr-downloads.service`. Do not reset Tailscale, stop the artifact receiver, reboot or alter runner configuration. Retain published version directories for existing links and recovery.

Support: hermes.reisner@gmail.com.
