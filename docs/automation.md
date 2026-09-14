# Noninteractive processing and watch folders

Without a subcommand, the Python `localsr` entry point launches the separately installed
Tauri desktop; see the [launcher setup](../desktop/README.md#application-data). These explicit
subcommands run without a desktop installation and use the same model adapter, tiled inference, image writer, and
video pipeline as the worker.

Process one or more files sequentially and emit newline-delimited JSON progress/results:

```bash
localsr process photo.png clip.mkv --output ./enhanced --model span_photo_x4 \
  --device auto --scale 4 --format png --video-container mkv --json
```

Run the fixed local benchmark:

```bash
localsr benchmark --device auto --json
```

Watch a folder, waiting for each file's size and modification time to remain unchanged before
processing it:

```bash
localsr watch ./incoming --output ./enhanced --stable-seconds 2 --json
```

The watcher recursively discovers supported media, ignores hidden and common partial-download
files, deduplicates resolved paths, never descends into its chosen output tree, and never overwrites
a source or existing result. Successful fingerprints are atomically persisted in
`.localsr-watch-state.json` so restarts do not repeat completed work. `SIGINT`/`SIGTERM` request
cooperative cancellation. `--once` waits for the current files to become stable, attempts each once,
and exits nonzero if any failed.

Models are not downloaded implicitly by automation. A selected catalog model must already exist in
the LocalSR model library with its pinned size and SHA-256 digest. Use `localsr <command> --help` for
the complete bounded format, tiling, quality, and device options.
