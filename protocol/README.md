# LocalSR worker protocol

The desktop host and inference engine exchange one JSON object per line over
stdin/stdout. Protocol version 1 preserves every message used by the Slint app
and adds an explicit handshake plus media probing for the Tauri host.

Compatibility rules:

- Workers always emit `worker_ready` first, so the released Slint client keeps
  working.
- New hosts send `handshake_request` and refuse incompatible engine versions.
- Job IDs are host-generated UUIDs. Only one worker job runs at a time; queueing
  and crash recovery belong to the host.
- Arbitrary worker stdout is never interpreted as a shell command.
- Model paths and media bytes stay on the local machine.
- Catalog-pinned checkpoints are verified by size and SHA-256. A custom
  pickle-based checkpoint is accepted only when that individual job carries
  the explicit `allow_unverified_checkpoint` acknowledgement.

The JSON Schema is the language-neutral contract. Update its
`x-protocol-version`, the Python constants, Rust constant, and compatibility
tests together for a breaking protocol change.
