#[cfg(target_os = "linux")]
mod appimage_gstreamer;
mod cache_cleanup;
mod cancellation;
mod catalog;
mod commands;
mod compare;
mod crash_reports;
mod database;
mod disk_room;
mod distribution;
mod downloads;
mod editing;
mod engine_manager;
mod engine_payload;
mod error;
mod gpu_notice;
mod grants;
mod headless_smoke;
mod integrations;
mod launch;
#[cfg(any(target_os = "linux", test))]
mod media_server;
mod model_storage;
mod native_menu;
mod output_names;
mod paths;
mod pipe_lines;
mod process_priority;
mod process_tree;
mod programs;
mod queue_timing;
mod results;
mod settings;
mod state;
mod tray;
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

#[doc(hidden)]
pub fn fuzz_detect_gguf_edit_family(bytes: &[u8]) -> Option<&'static str> {
    editing::detect_gguf_edit_family_bytes(bytes)
}

#[doc(hidden)]
pub fn fuzz_parse_engine_manifest(bytes: &[u8]) -> std::io::Result<()> {
    engine_payload::parse_manifest(bytes).map(|_| ())
}

/// Files macOS asked us to open before the setup hook created the app state.
///
/// When the app is launched by opening a document, `application:openURLs:` is
/// delivered before Tauri's `Ready` event runs `setup`, so there is no managed
/// state yet. The intents wait here and are moved into the state in `setup`.
#[cfg(target_os = "macos")]
static EARLY_OPEN_INTENTS: std::sync::Mutex<Vec<LaunchIntent>> = std::sync::Mutex::new(Vec::new());

pub fn run() {
    #[cfg(target_os = "linux")]
    appimage_gstreamer::use_own_registry();
    // No UI, profile or worker: other updater processes query the file they are
    // about to replace, rather than trusting their own running package version.
    if env::args_os()
        .nth(1)
        .is_some_and(|arg| arg == "--headless-update-version")
    {
        println!("{}", env!("CARGO_PKG_VERSION"));
        return;
    }
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

    crash_reports::install_hook();
    if let Ok(paths) = paths::AppPaths::discover() {
        let enabled = settings::load(&paths).0.crash_reports;
        crash_reports::configure_hook(&paths, enabled);
        crash_reports::cleanup(&paths);
    }

    // A crash during an executable swap is rolled back before anything opens the profile.
    if updates::recover_interrupted_update() {
        return;
    }
    // And an engine installation that a crash killed between its two renames, beside it.
    if let Ok(executable) = env::current_exe() {
        engine_payload::recover_beside(&executable);
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
        .on_window_event(|window, event| {
            grant_dropped_window_paths(window.app_handle(), event);
            hide_instead_of_close(window, event);
        })
        .on_webview_event(|webview, event| {
            grant_dropped_webview_paths(webview.app_handle(), event);
        })
        .setup(move |app| {
            let state = Arc::new(AppState::new()?);
            if let Err(error) = cache_cleanup::cleanup_at_start(&state) {
                eprintln!("LocalSR could not clean the model cache: {error}");
            }
            // A Windows installer that finished while LocalSR was closed.
            updates::confirm_pending_install(
                &state.paths.next_root,
                &app.package_info().version.to_string(),
            );
            if !initial_intent.is_empty() {
                state.grants.grant_launch_files(&initial_intent.files);
                lock(&state.launch_intents)?.push(initial_intent.clone());
            }
            #[cfg(target_os = "macos")]
            if let Ok(mut early) = EARLY_OPEN_INTENTS.lock() {
                for intent in early.iter() {
                    state.grants.grant_launch_files(&intent.files);
                }
                lock(&state.launch_intents)?.append(&mut early);
            }
            app.manage(state.clone());
            if !smoke_test {
                show_startup_notices(app.handle(), &state);
            }
            *lock(&state.bundled_engine_path)? = worker::bundled_engine_executable(app.handle());
            native_menu::install(app, &lock(&state.recipes)?)?;
            tray::install(app.handle());
            fit_main_window(app.handle());
            let startup_state = state.clone();
            let startup_app = app.handle().clone();
            tauri::async_runtime::spawn_blocking(move || {
                if !crate::distribution::managed_by_store() {
                    engine_manager::recover_engine_files_at_startup(&startup_state);
                }
                if let Err(error) = worker::start_worker(startup_state.clone(), startup_app.clone())
                {
                    if let Ok(mut runtime) = startup_state.runtime.lock() {
                        runtime.worker = "failed".into();
                        runtime.status_title = "Engine unavailable".into();
                        runtime.status_detail = error.to_string();
                    }
                    crate::state::emit_state_changed(&startup_app);
                }
            });
            if smoke_test {
                start_smoke_monitor(app.handle().clone(), state);
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            updates::update_status,
            updates::skip_update,
            updates::open_update_discover,
            updates::open_store_updates,
            updates::check_update,
            updates::check_update_at_launch,
            updates::download_update,
            updates::install_update,
            updates::cancel_update,
            updates::discard_update,
            updates::recover_update_settings,
            engine_manager::refresh_engine_catalog,
            engine_manager::install_engine,
            engine_manager::cancel_engine_install,
            engine_manager::use_engine,
            engine_manager::verify_engine,
            engine_manager::remove_engine,
            engine_manager::choose_engine_manifest,
            engine_manager::import_engine,
            commands::open_bug_report,
            commands::list_crash_reports,
            commands::delete_crash_report,
            commands::delete_all_crash_reports,
            commands::record_frontend_error,
            cache_cleanup::cache_size,
            cache_cleanup::clear_cache,
            commands::bootstrap,
            commands::get_snapshot,
            commands::open_model_license,
            commands::take_launch_intents,
            commands::scan_media_folder,
            commands::add_media,
            commands::choose_media_files,
            commands::choose_media_folder,
            commands::choose_output_directory,
            commands::choose_model_directory,
            commands::set_model_directory,
            commands::reset_model_directory,
            commands::dismiss_gpu_download_notice,
            commands::dismiss_linux_menu_prompt,
            commands::install_linux_menu,
            commands::uninstall_linux_menu,
            commands::choose_custom_model,
            commands::checkpoint_consent,
            commands::choose_benchmark_export,
            commands::select_media,
            commands::remove_media,
            commands::clear_media,
            commands::clear_results,
            commands::save_settings,
            commands::set_preference,
            commands::set_run_low_priority,
            commands::set_privacy_choice,
            commands::save_recipe,
            commands::delete_recipe,
            commands::start_jobs,
            commands::open_edit_link,
            commands::start_edit,
            commands::start_benchmark,
            commands::export_benchmark,
            commands::prepare_video_comparison,
            commands::cancel_video_comparison,
            commands::request_image_comparison,
            commands::request_compare,
            commands::request_compare_regions,
            commands::clear_compare_regions,
            commands::retry_engine,
            commands::retry_job_on_cpu,
            commands::cancel_jobs,
            commands::cancel_jobs_for_media,
            commands::start_model_trials,
            commands::estimate_model_trials,
            commands::list_media_results,
            commands::list_all_media_results,
            commands::refresh_capabilities,
            commands::probe_path,
            commands::download_model,
            commands::cancel_download,
            commands::remove_model,
            commands::import_catalog_model,
            commands::open_result,
            commands::open_user_guide,
            commands::open_download_page,
            commands::open_custom_model_tutorial,
            commands::open_privacy_page,
            commands::reveal_result,
            commands::use_result_as_input,
            commands::open_output_directory,
            commands::detect_external_ffmpeg,
            commands::choose_external_ffmpeg,
            commands::choose_edit_runtime,
            commands::choose_edit_model_file,
            commands::save_custom_edit_model,
            commands::open_ffmpeg_download_page,
            commands::ffmpeg_install_hint,
            commands::open_terminal_with_install_command,
            commands::diagnostic_summary,
            commands::integration_status,
            commands::install_integrations,
            commands::uninstall_integrations,
        ])
        .build(compiled_context())
        .expect("failed to build LocalSR");

    application.run(|app, event| {
        if matches!(&event, tauri::RunEvent::Exit) {
            let state = app.state::<Arc<AppState>>();
            worker::shutdown(&state);
            // Keep the process alive until the supervisor has removed the job's scratch.
            let deadline = std::time::Instant::now() + std::time::Duration::from_secs(10);
            while state.worker.pid.load(std::sync::atomic::Ordering::SeqCst) != 0
                && std::time::Instant::now() < deadline
            {
                std::thread::sleep(std::time::Duration::from_millis(20));
            }
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

/// Tell the user once about what start-up had to work around.
fn show_startup_notices(app: &tauri::AppHandle, state: &AppState) {
    use tauri_plugin_dialog::{DialogExt, MessageDialogKind};

    let notices = state.take_startup_notices();
    if notices.is_empty() {
        return;
    }
    app.dialog()
        .message(notices.join("\n\n"))
        .title("LocalSR")
        .kind(MessageDialogKind::Warning)
        .show(|_| {});
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
    state.grants.grant_launch_files(&intent.files);
    if let Ok(mut pending) = state.launch_intents.lock() {
        pending.push(intent);
    }
    let _ = app.emit("launch-intent-available", ());
    focus_main_window(app);
}

/// Shrinks the window to the screen's work area. The configured 1380 × 860 is taller
/// than a 1366 × 768 laptop or a 1280 × 800 display, where the status bar with the
/// start button would sit under the taskbar or the Dock.
fn fit_main_window(app: &tauri::AppHandle) {
    let Some(window) = app.get_webview_window("main") else {
        return;
    };
    let (Ok(Some(monitor)), Ok(outer), Ok(inner)) = (
        window.current_monitor(),
        window.outer_size(),
        window.inner_size(),
    ) else {
        return;
    };
    let area = monitor.work_area();
    if outer.width <= area.size.width && outer.height <= area.size.height {
        return;
    }
    let frame_width = outer.width.saturating_sub(inner.width);
    let frame_height = outer.height.saturating_sub(inner.height);
    let width = inner.width.min(area.size.width.saturating_sub(frame_width));
    let height = inner
        .height
        .min(area.size.height.saturating_sub(frame_height));
    let _ = window.set_size(tauri::PhysicalSize::new(width, height));
    let left = area.position.x + (area.size.width.saturating_sub(width + frame_width) / 2) as i32;
    let top = area.position.y + (area.size.height.saturating_sub(height + frame_height) / 2) as i32;
    let _ = window.set_position(tauri::PhysicalPosition::new(left, top));
}

fn grant_dropped_window_paths(app: &tauri::AppHandle, event: &tauri::WindowEvent) {
    if let tauri::WindowEvent::DragDrop(tauri::DragDropEvent::Drop { paths, .. }) = event {
        if let Some(state) = app.try_state::<Arc<AppState>>() {
            state.grants.grant_dropped_media(paths);
        }
    }
}

fn grant_dropped_webview_paths(app: &tauri::AppHandle, event: &tauri::WebviewEvent) {
    if let tauri::WebviewEvent::DragDrop(tauri::DragDropEvent::Drop { paths, .. }) = event {
        if let Some(state) = app.try_state::<Arc<AppState>>() {
            state.grants.grant_dropped_media(paths);
        }
    }
}

/// On macOS, closing the window (Command-W or the close button) only hides it, as in other
/// single-window Mac apps: the queue keeps running, and the Dock icon, Window > LocalSR or
/// opening a file brings it back. Quit ends the app.
fn hide_instead_of_close(window: &tauri::Window, event: &tauri::WindowEvent) {
    #[cfg(target_os = "macos")]
    if let tauri::WindowEvent::CloseRequested { api, .. } = event {
        if window.label() == "main" {
            api.prevent_close();
            let _ = window.hide();
        }
    }
    #[cfg(not(target_os = "macos"))]
    let _ = (window, event);
}

pub(crate) fn focus_main_window(app: &tauri::AppHandle) {
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
                "app": "LocalSR",
                "version": env!("CARGO_PKG_VERSION"),
                "platform": env::consts::OS,
                "architecture": env::consts::ARCH,
                "worker": worker_status,
                "passed": exit_code == 0,
                "status_title": status_title,
                "status_detail": status_detail,
            });
            let _ = headless_smoke::write_report(Path::new(&report_path), &report);
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
