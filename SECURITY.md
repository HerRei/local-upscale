# Security Policy

## Reporting a vulnerability

Use the repository's private GitHub Security Advisory form. Do not open a public issue for a
checkpoint-loading, arbitrary-code-execution, path traversal, unsafe file replacement, or download
integrity vulnerability. While the repository remains private, that route is limited to people
with repository access; a general-public security contact is still a required beta decision.

## Model checkpoint warning

PyTorch pickle and TorchScript checkpoints may execute code while loading. LocalSR accepts custom
`.safetensors` normally, but blocks unverified `.pth`, `.pt`, and `.ckpt` files by default. Curated
downloads must match their pinned byte size and SHA-256 immediately before Spandrel sees them. The
`LOCALSR_ALLOW_UNVERIFIED_CHECKPOINTS=1` override is not a sandbox; use it only when you have
independently authenticated and trust the checkpoint as executable code.

SeedVR2's bundled text-conditioning embeddings are Safetensors and their expected tensor keys,
shapes, and dtype are validated before use. Normal Labs execution does not load bundled `.pt`
objects.

## Alpha download trust

Verify every portable archive against its release `.sha256` sidecar. Checksums detect corruption
but do not replace platform code signing: current alpha downloads are not Developer ID notarized or
Authenticode signed and must not be represented as public-beta builds.

## Supported versions

Security fixes currently target the latest commit on `main`; there is not yet a stable release
branch.
