# Release hosting for review

Updated 13 September 2026. The user subsequently authorized a local Mac build/install and installing separate Mac mini download hosting, while explicitly protecting the ongoing release build. Caddy is now installed as a separate service at `127.0.0.1:8788`, rooted at `/mnt/hdd/localsr-downloads/public`. Only a status marker is served. Router, firewall, Tailscale and DNS configuration remain unchanged. Public address and redistribution clearance remain pending; no download feed was activated. Other platform builds remain paused.

Actual deployed configuration and checks: [Mac mini operations](https://github.com/HerRei/local-upscale/blob/docs/mac-mini-download-host-20260913/docs/mac-mini-download-host.md) and local `build/macmini-hosting-20260913/`. The initial proposal below is retained for context; its proposed `/mnt/hdd/localsr-releases` directory was not used.

## Proposed arrangement

The user reaffirmed the Mac mini as the preferred host after reviewing the
independent handoff. Managed hosting is only a contingency if public reachability,
upload speed or service availability proves inadequate. No new hosting purchase
is assumed. This preference does not authorize deployment or relax the build pause.

Keep the website and public project/release pages on GitHub. Serve large immutable
packages from a dedicated Mac mini directory through Caddy, under an approved
public HTTPS name. The draft defaults to loopback HTTP and is not an installer:

- [Caddyfile](../packaging/hosting/Caddyfile)
- [systemd service](../packaging/hosting/localsr-downloads.service)
- [environment example](../packaging/hosting/environment.example)

Planned storage is `/mnt/hdd/localsr-releases/public/releases/<version>/<target>/`.
Keep private keys, working checkouts, build caches and staging **outside** this
tree. The download user has read-only access; only approved exact files enter it.
Before staging, reject symlinks, hard links to private files, special files and
any path outside the approved artifact allowlist. Caddy file serving is not a
symlink sandbox. Copy regular files into new staging storage, verify the complete
inventory, then move it into the public tree. No directory listing, uploads,
proxying or application access logs are configured.
Persistent Caddy certificate state lives separately under `/var/lib/localsr-downloads`.
Do not reuse the GitHub runner user, its directories, or a release signing key.

Publish immutable packages and corresponding sources first; verify size, hash,
signature, HTTPS and resumption from outside the home network. Update feeds only
after those exact files pass. Replace feed files atomically; retain previously
published versions for recovery. A signed update is still rejected when its
platform, architecture, backend, channel, package kind or worker protocol does
not match. Store application/engine updates remain managed by Microsoft Store.

## Limits checked against the publishers

GitHub permits up to 1,000 assets per release, each **under 2 GiB**. It documents
no total release-size or release-bandwidth limit. The retained 6,237,354,488-byte
ROCm and 5,089,909,240-byte CUDA AppImages exceed the per-file limit. These are
older review artifacts, not final downloads. [GitHub release limits](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases).

Microsoft currently lists a 25 GB maximum for Windows 10/11 MSIX packages and
bundles. Its Store re-signs MSIX/AppX after certification, without requiring a
purchased CA certificate for the submission. That does not sign direct EXE/MSI
downloads. The Store draft still needs a matching manifest, final package, WACK
review, listing and certification. [Store package requirements](https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/msix/app-package-requirements).

## Operational conditions to decide

Public HTTPS requires a chosen domain, appropriate DNS, reachable HTTPS and a
persistent certificate store. Ordinary HTTP/TLS ACME challenges need inbound
80/443 as applicable; a DNS challenge can issue certificates without inbound
validation but does not make an otherwise unreachable download server reachable.
CGNAT, ISP restrictions and router reachability remain unmeasured. No public
Tailscale sharing or tunnel is configured. [Caddy HTTPS requirements](https://caddyserver.com/docs/automatic-https).

The unit waits for the release filesystem/network and restarts after a service
failure, with a bounded restart rate. Enabling it at boot, host reboot recovery,
power-loss behavior, certificate renewal, public monitoring and WAN availability
require a later approved host test. Local process restart tests cannot establish
those properties. Caddy's administrative listener is disabled; deploy/reload by
an explicit service restart after config validation. [Caddy service guidance](https://caddyserver.com/docs/running#using-the-service).

Home upload bandwidth is shared by all downloads and other household use. For
the retained ROCm file, ideal transfer times are about 42 minutes at 20 Mbit/s,
17 minutes at 50 Mbit/s and 8 minutes at 100 Mbit/s, before overhead. Four equal
concurrent downloads take roughly four times as long at the same total uplink.
These are size/rate calculations, not measurements. After deployment approval, measure one
and four concurrent external downloads, interrupted/resumed transfers and a
reboot before recommending the service to public testers.

There is no new hosting purchase. Costs remain a domain if needed, electricity,
existing ISP service/possible public-IP fee and storage/backup. Recommendation:
use this dedicated directory only if external tests establish adequate speed and
availability. Otherwise choose managed object storage/CDN rather than depending
on an unattended home build machine during university; provider and budget need
your decision. Keep the website able to show an unavailable download honestly.

## Update and recovery scope

The updater streams and verifies complete files; it does not currently resume
an interrupted application-update transfer. It deletes unsuccessful partial
updates and can re-use a previously verified complete download. Browser/curl
range resume is a separate hosting capability. Model downloads have their own
resume path. No recovery guarantee should imply that uninstall preserves the
Windows MSIX profile: the retained test observed deletion and verified explicit
backup restoration. Native installation/restart/recovery with final packages
remains required on every edition. See [update implementation](local-updates.md).

Local hosting evidence and remaining WAN/service checks are recorded in
`build/beta-review/rollout-20260913/hosting-test.json`; that test uses loopback and
disposable data only. No hosting go-live is implied by a passing local test.
