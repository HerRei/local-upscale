# Desktop integrations

The Tauri host owns file pickers, native menus, notifications, file associations
and opening completed results. Its commands validate paths and call the native
operating-system services; the Svelte webview has no general shell or filesystem
access. The inference worker remains a separate Python process.

## Entry points

`desktop/src-tauri/src/launch.rs` parses files, folders, `--preset`, `--recipe`
and `--auto-start`. The single-instance plugin forwards later launches to the
existing window. Directory expansion and duplicate handling live in `commands.rs`.
The frontend reconciles launch requests with the selected media type and cancels
automatic processing when a mixed selection needs a user choice.

The Python `localsr` command forwards desktop launches to this host. Its
`process`, `watch` and `benchmark` subcommands run directly through the Python
engine.
[Launcher configuration](../../desktop/README.md#application-data).

## Platform actions

`desktop/src-tauri/src/integrations.rs` provides the optional integrations:

- macOS: Finder Quick Actions and a command-line launcher.
- Windows: per-user Explorer context-menu entries and a recipe picker.
- Linux: desktop entries, Dolphin service-menu actions and Nautilus scripts.

Installation and removal are explicit commands in the app's System integrations
panel. On Linux, register an AppImage only after moving it to its permanent
location. The installer and manifest declare supported image/video associations;
registration does not imply that every input codec or model works on every GPU.
Store-specific delivery and update behavior are described in the
[MSIX guide](../microsoft-store.md).

The Qt Widgets client and its Python integration service have been removed.
Slint's separate dialogs, menu adapter, installer and worker bridge were
[retired in the beta](../slint-retirement.md).

## Media and state

The Rust host authorizes individual source/result paths. It owns the queue
database, settings, recipes and verified model downloads. Completed comparisons
stay associated with their media and output paths while another job runs.
Linux video playback uses a loopback media server with bounded file authorization;
this does not upload video to a remote service.

The application retains `com.localsr.desktop.next` and its `LocalSR/next` state
root. Models stay in the shared verified `LocalSR/models` cache. Slint retirement
does not remove an existing installation or delete its settings.

## Verification and limits

Rust tests cover argument parsing, directory expansion, integration scripts,
path authorization, settings and queue behavior. Svelte tests cover forwarded
launches, mixed media, batching, comparison ownership and cancellation. Python
launcher tests verify literal arguments, error handling and GUI-free CLI use.

Native file-manager behavior and installed packages require separate acceptance.
Recorded results and remaining observations are in the
[beta checklist](../beta-release-checklist.md) and
[hardware matrix](../beta-platform-matrix.md). Supported APIs and generated
scripts alone are not evidence for every desktop environment or graphics driver.
