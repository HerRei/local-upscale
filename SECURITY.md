# Security Policy

## Reporting a vulnerability

Use the repository's private GitHub Security Advisory form. Do not open a public issue for a
checkpoint-loading, arbitrary-code-execution, path traversal, unsafe file replacement, or download
integrity vulnerability.

## Model checkpoint warning

PyTorch `.pth` and `.pt` checkpoints may contain pickle data capable of executing code while being
loaded. Only use files from trusted sources. Prefer `.safetensors` for custom models when the
architecture supports it. LocalSR's curated downloads are pinned to exact URLs, byte sizes, and
SHA-256 digests, but custom checkpoints cannot be authenticated by LocalSR.

## Supported versions

Security fixes currently target the latest commit on `main`; there is not yet a stable release
branch.
