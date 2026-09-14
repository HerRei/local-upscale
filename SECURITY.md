# Security policy

## Report a vulnerability

Email [hermes.reisner@gmail.com](mailto:hermes.reisner@gmail.com), or use the
repository's private security advisory form if available to you. Include the
version, platform and steps needed to reproduce the issue. Send reports about
code execution, checkpoint loading, file access or download integrity privately.

Do not include credentials, signing keys, private media or a full queue database.

## Checkpoint trust

LocalSR accepts custom `.safetensors` files and blocks unverified pickle or
TorchScript checkpoints by default. Curated downloads must match the catalog's
byte size and SHA-256 immediately before loading.

`LOCALSR_ALLOW_UNVERIFIED_CHECKPOINTS=1` explicitly allows unverified `.pth`, `.pt`
and `.ckpt` files. Such files can execute code while loading; only enable it for
checkpoints whose publisher and contents you trust. The worker is isolated from
the interface for recovery, but is not a security sandbox.

SeedVR2's bundled conditioning assets use Safetensors. Their tensor keys, shapes
and dtype are validated before use.

## Release trust

Checksums detect changed or corrupted files. Public direct-download packages also
require signature verification; Store application updates are managed by Microsoft
Store. Earlier unsigned alpha packages are not substitutes for a signed beta.

The current beta is still under review. Known dependency issues and uncompleted
release checks are documented in the [limitations](KNOWN_LIMITATIONS.md) and
[dependency review](docs/beta-dependency-review.md). No stable release branch or
long-term security support period has been established.
