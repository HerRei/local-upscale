mod cancellation;
mod catalog;
mod commands;
mod database;
mod distribution;
mod downloads;
mod engine_payload;
mod error;
mod headless_smoke;
mod integrations;
mod launch;
#[cfg(any(target_os = "linux", test))]
mod media_server;
mod native_menu;
mod paths;
mod queue_timing;
mod settings;
mod state;
mod types;
mod update_storage;
mod updates;
mod video_preview;
mod worker;

use std::{
    env,
    ffi::OsString,
    path::{Path, PathBuf},
    sync::Arc,
    thread,
    time::Duration,
};

use launch::{ImmediateAction, LaunchIntent, ParsedLaunch};
use state::{lock, AppState};
use tauri::{Emitter, Manager};

pub(crate) fn compiled_context() -> tauri::Context<tauri::Wry> {
    tauri::generate_context!()
}

/// Files macOS asked us to open before the setup hook created the app state.
///
/// When the app is launched by opening a document, `application:openURLs:` is
/// delivered before Tauri's `Ready` event runs `setup`, so there is no managed
/// state yet. The intents wait here and are moved into the state in `setup`.
#[cfg(target_os = "macos")]
static EARLY_OPEN_INTENTS: std::sync::Mutex<Vec<LaunchIntent>> = std::sync::Mutex::new(Vec::new());

pub fn run() {
    if let Some(exit_code) = engine_payload::run_arguments(&env::args_os().collect::<Vec<_>>()) {
        std::process::exit(exit_code);
    }
    let cwd = env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    let parsed = launch::parse_os_arguments(env::args_os().skip(1), &cwd);
    if parsed.headless_smoke_test {
        let exit_code = headless_smoke::run();
        if exit_code != 0 {
            std::process::exit(exit_code);
        }
        return;
    }
    if let Some(exit_code) = run_immediate_action(parsed.action) {
        if exit_code != 0 {
            std::process::exit(exit_code);
        }
        return;
    }

    let initial_intent = parsed.intent;
    let smoke_test = parsed.smoke_test;
    let application = tauri::Builder::default()
        // This plugin must be registered first. A second file-manager launch
        // is folded into the existing queue instead of starting a competing
        // worker and SQLite connection.
        .plugin(tauri_plugin_single_instance::init(|app, argv, cwd| {
            let parsed = parse_forwarded_arguments(argv, &cwd);
            if parsed.action == ImmediateAction::Run {
                enqueue_launch_intent(app, parsed.intent);
            }
        }))
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .on_menu_event(|app, event| native_menu::dispatch(app, event.id().as_ref()))
        .setup(move |app| {
            let state = Arc::new(AppState::new()?);
            if !initial_intent.is_empty() {
                lock(&state.launch_intents)?.push(initial_intent.clone());
            }
            #[cfg(target_os = "macos")]
            if let Ok(mut early) = EARLY_OPEN_INTENTS.lock() {
                lock(&state.launch_intents)?.append(&mut early);
            }
            app.manage(state.clone());
            native_menu::install(app, &lock(&state.recipes)?)?;
            if let Err(error) = worker::start_worker(state.clone(), app.handle().clone()) {
                if let Ok(mut runtime) = state.runtime.lock() {
                    runtime.worker = "failed".into();
                    runtime.status_title = "Engine unavailable".into();
                    runtime.status_detail = error.to_string();
                }
            }
            if smoke_test {
                start_smoke_monitor(app.handle().clone(), state);
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            updates::update_status,
            updates::open_store_updates,
            updates::check_update,
            updates::download_update,
            updates::install_update,
            updates::cancel_update,
            updates::discard_update,
            updates::recover_update_settings,
            commands::bootstrap,
            commands::get_snapshot,
            commands::open_model_license,
            commands::take_launch_intents,
            commands::scan_media_folder,
            commands::add_media,
            commands::select_media,
            commands::remove_media,
            commands::clear_media,
            commands::save_settings,
            commands::save_recipe,
            commands::delete_recipe,
            commands::start_jobs,
            commands::start_benchmark,
            commands::export_benchmark,
            commands::prepare_video_comparison,
            commands::cancel_video_comparison,
            commands::request_image_comparison,
            commands::cancel_jobs,
            commands::refresh_capabilities,
            commands::probe_path,
            commands::download_model,
            commands::cancel_download,
            commands::remove_model,
            commands::import_catalog_model,
            commands::open_result,
            commands::open_user_guide,
            commands::reveal_result,
            commands::open_output_directory,
            commands::detect_external_ffmpeg,
            commands::open_ffmpeg_download_page,
            commands::ffmpeg_install_hint,
            commands::open_terminal_with_install_command,
            commands::diagnostic_summary,
            commands::integration_status,
            commands::install_integrations,
            commands::uninstall_integrations,
        ])
        .build(compiled_context())
        .expect("failed to build LocalSR Next Preview");

    application.run(|app, event| {
        if matches!(&event, tauri::RunEvent::Exit) {
            let state = app.state::<Arc<AppState>>();
            worker::shutdown(&state);
            #[cfg(target_os = "linux")]
            if let Ok(mut server) = state.media_server.lock() {
                server.take();
            };
        }
        #[cfg(target_os = "macos")]
        if let tauri::RunEvent::Opened { urls } = &event {
            let files = urls
                .iter()
                .filter_map(|url| url.to_file_path().ok())
                .map(|path| path.to_string_lossy().into_owned())
                .collect::<Vec<_>>();
            if !files.is_empty() {
                enqueue_launch_intent(
                    app,
                    LaunchIntent {
                        files,
                        ..LaunchIntent::default()
                    },
                );
            }
        }
        #[cfg(target_os = "macos")]
        if let tauri::RunEvent::Reopen { .. } = &event {
            focus_main_window(app);
        }
    });
}

fn run_immediate_action(action: ImmediateAction) -> Option<i32> {
    let result = match action {
        ImmediateAction::Run => return None,
        ImmediateAction::InstallIntegrations => integrations::install(),
        ImmediateAction::UninstallIntegrations => integrations::uninstall(),
    };
    match result {
        Ok(status) => {
            println!("{}", status.summary);
            Some(0)
        }
        Err(error) => {
            eprintln!("{error}");
            Some(1)
        }
    }
}

fn parse_forwarded_arguments(argv: Vec<String>, cwd: &str) -> ParsedLaunch {
    let arguments = argv.into_iter().skip(1).map(OsString::from);
    launch::parse_os_arguments(arguments, Path::new(cwd))
}

fn enqueue_launch_intent(app: &tauri::AppHandle, intent: LaunchIntent) {
    if intent.is_empty() {
        focus_main_window(app);
        return;
    }
    let Some(state) = app.try_state::<Arc<AppState>>() else {
        #[cfg(target_os = "macos")]
        if let Ok(mut early) = EARLY_OPEN_INTENTS.lock() {
            early.push(intent);
        }
        return;
    };
    if let Ok(mut pending) = state.launch_intents.lock() {
        pending.push(intent);
    }
    let _ = app.emit("launch-intent-available", ());
    focus_main_window(app);
}

fn focus_main_window(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

fn start_smoke_monitor(app: tauri::AppHandle, state: Arc<AppState>) {
    thread::spawn(move || {
        let mut exit_code = 1;
        let mut worker_status = "timeout".to_owned();
        let mut status_title = "Engine did not become ready".to_owned();
        let mut status_detail =
            "The packaged worker did not answer before the smoke-test timeout.".to_owned();
        // A freshly installed worker can need appreciably longer on cold,
        // CPU-only CI hosts while the dynamic libraries are first loaded.
        for _ in 0..720 {
            thread::sleep(Duration::from_millis(250));
            let Ok(runtime) = state.runtime.lock() else {
                break;
            };
            worker_status = runtime.worker.clone();
            status_title = runtime.status_title.clone();
            status_detail = runtime.status_detail.clone();
            if let Some(code) = smoke_terminal_exit_code(&runtime.worker) {
                exit_code = code;
                break;
            }
        }
        if let Some(report_path) = env::var_os("LOCALSR_SMOKE_REPORT") {
            let report = serde_json::json!({
                "app": "LocalSR Next Preview",
                "version": env!("CARGO_PKG_VERSION"),
                "platform": env::consts::OS,
                "architecture": env::consts::ARCH,
                "worker": worker_status,
                "passed": exit_code == 0,
                "status_title": status_title,
                "status_detail": status_detail,
            });
            let _ = write_smoke_report(Path::new(&report_path), &report);
        }
        app.exit(exit_code);
    });
}

fn smoke_terminal_exit_code(worker_status: &str) -> Option<i32> {
    match worker_status {
        "ready" => Some(0),
        "failed" => Some(1),
        // `unavailable` is recoverable: the worker supervisor changes to this
        // state before its bounded restart. A package smoke test must exercise
        // that recovery path instead of terminating the host first.
        _ => None,
    }
}

fn write_smoke_report(path: &Path, report: &serde_json::Value) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::write(path, serde_json::to_vec_pretty(report).unwrap_or_default())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn forwarded_single_instance_arguments_skip_the_executable() {
        let parsed = parse_forwarded_arguments(
            vec![
                "/Applications/LocalSR".into(),
                "--preset".into(),
                "best".into(),
                "image.png".into(),
            ],
            "/incoming",
        );
        assert_eq!(parsed.intent.preset.as_deref(), Some("best"));
        assert_eq!(
            parsed.intent.files,
            vec![Path::new("/incoming")
                .join("image.png")
                .to_string_lossy()
                .into_owned()]
        );
    }

    #[test]
    fn smoke_monitor_waits_for_recoverable_worker_restart() {
        assert_eq!(smoke_terminal_exit_code("starting"), None);
        assert_eq!(smoke_terminal_exit_code("unavailable"), None);
        assert_eq!(smoke_terminal_exit_code("ready"), Some(0));
        assert_eq!(smoke_terminal_exit_code("failed"), Some(1));
    }
}
