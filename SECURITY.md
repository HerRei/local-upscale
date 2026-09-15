# Security policy

## Reporting a vulnerability

Email [hermes.reisner@gmail.com](mailto:hermes.reisner@gmail.com) or use GitHub's
private vulnerability report on this repository. Include the LocalSR version,
your platform and the steps to reproduce. Please report anything involving code
execution, checkpoint loading, file access or download integrity privately
rather than in a public issue.

Do not include credentials, signing keys, private media or your queue database.

## What LocalSR trusts

**Model files.** Catalog downloads must match the size and SHA-256 recorded in the
catalog before they are loaded. Custom `.safetensors` files are accepted as they
are. Pickle-based checkpoints (`.pth`, `.pt`, `.ckpt`) can execute code while they
load, so unverified ones are refused unless you set
`LOCALSR_ALLOW_UNVERIFIED_CHECKPOINTS=1` for files whose publisher you trust.
SeedVR2's bundled conditioning tensors are Safetensors and are validated for
keys, shapes and dtype before use.

**Process boundaries.** Inference runs in a separate Python worker that talks to
the app over JSON Lines. The webview has no shell, filesystem or network API;
native operations go through the Rust host, which only grants access to the
files you select. The worker boundary exists for recovery from GPU and memory
failures; it is not a security sandbox.

**Releases and updates.** macOS builds are Developer ID signed and notarized.
Update downloads are verified by size, SHA-256 and a minisign signature before
they are installed, and settings, recipes and the queue are backed up first.
Microsoft Store builds receive their updates from the Store. Direct Windows
installers for the beta are not Authenticode signed; SmartScreen will warn.

Third-party dependencies are pinned in lock files and reviewed with Dependabot.
