use std::any::TypeId;
use std::sync::atomic::{AtomicBool, Ordering};

use tauri::{
    image::Image,
    menu::{Menu, MenuBuilder, MenuItemBuilder},
    tray::TrayIconBuilder,
    AppHandle, Manager, Runtime,
};

use crate::state::lock;

pub const TRAY_ID: &str = "localsr-tray";

/// The icon style the tray was last drawn with.
static ICON_MONOCHROME: AtomicBool = AtomicBool::new(true);

const MENU_STATUS_ID: &str = "tray.status";
const MENU_WAITING_ID: &str = "tray.waiting";
const MENU_PAUSE_ID: &str = "tray.pause";
const MENU_RESUME_ID: &str = "tray.resume";
const MENU_REVEAL_LAST_ID: &str = "tray.reveal-last";
const MENU_SHOW_ID: &str = "tray.show";
const MENU_QUIT_ID: &str = "tray.quit";

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TraySnapshot {
    pub visible: bool,
    pub monochrome: bool,
    pub active_job_id: String,
    pub active_action: String,
    pub active_media_name: String,
    pub progress_percent: u32,
    pub waiting_count: usize,
    pub paused: bool,
    pub worker_status: String,
    pub last_output_path: String,
    pub title: Option<String>,
}

pub fn format_tray_title(active_job_id: &str, progress: f64) -> Option<String> {
    if active_job_id.is_empty() {
        return None;
    }
    Some(format!("{}%", clamp_progress_percent(progress)))
}

fn clamp_progress_percent(progress: f64) -> u32 {
    if !progress.is_finite() {
        return 0;
    }
    progress.round().clamp(0.0, 100.0) as u32
}

pub fn derive_action_label(status_title: &str, request_json: Option<&str>) -> String {
    let trimmed = status_title.trim();
    if trimmed.starts_with("Cancelling") {
        return "Cancelling".into();
    }
    if let Some((prefix, rest)) = trimmed.split_once(" · ") {
        if rest.starts_with("stage ") && !prefix.is_empty() {
            return prefix.to_owned();
        }
    }
    if matches!(
        trimmed,
        "" | "Queued" | "Processing" | "Enhancing" | "Ready"
    ) {
        if let Some(json) = request_json {
            if let Ok(value) = serde_json::from_str::<serde_json::Value>(json) {
                match value["type"].as_str().unwrap_or_default() {
                    "edit_job_request" => return "Editing".into(),
                    "video_job_request" => {
                        let task = value["data"]["task"]
                            .as_str()
                            .or_else(|| value["data"]["video_task"].as_str())
                            .unwrap_or_default();
                        return if task == "denoise" {
                            "Restoring".into()
                        } else {
                            "Enhancing".into()
                        };
                    }
                    "job_request" => {
                        let stages = value["data"]["stages"].as_array();
                        let has_upscale = stages.is_some_and(|list| {
                            list.iter().any(|stage| stage["kind"] == "upscale")
                        });
                        if has_upscale {
                            return "Upscaling".into();
                        }
                        if stages.is_some_and(|list| !list.is_empty())
                            || value["data"]["output_scale"].as_u64() == Some(1)
                        {
                            return "Restoring".into();
                        }
                        return "Upscaling".into();
                    }
                    _ => {}
                }
            }
        }
        return if trimmed == "Enhancing" {
            "Enhancing".into()
        } else {
            "Processing".into()
        };
    }
    trimmed.to_owned()
}

pub fn format_current_job_line(snapshot: &TraySnapshot) -> String {
    if !snapshot.active_job_id.is_empty() {
        if !snapshot.active_media_name.is_empty() {
            return format!(
                "{} {} · {}%",
                snapshot.active_action, snapshot.active_media_name, snapshot.progress_percent
            );
        }
        return format!(
            "{} · {}%",
            snapshot.active_action, snapshot.progress_percent
        );
    }
    if snapshot.paused {
        return "Queue paused".into();
    }
    if snapshot.waiting_count > 0 {
        return if snapshot.worker_status == "ready" {
            "Starting next job…".into()
        } else {
            "Waiting for engine…".into()
        };
    }
    match snapshot.worker_status.as_str() {
        "starting" | "negotiating" | "restarting" => "Starting engine…".into(),
        "failed" | "unavailable" => "Engine unavailable".into(),
        _ => "Idle".into(),
    }
}

pub fn format_waiting_line(waiting_count: usize) -> String {
    match waiting_count {
        0 => "No files waiting".into(),
        1 => "1 file waiting".into(),
        count => format!("{count} files waiting"),
    }
}

pub fn pause_resume_item(paused: bool) -> (&'static str, &'static str) {
    if paused {
        (MENU_RESUME_ID, "Resume queue")
    } else {
        (MENU_PAUSE_ID, "Pause queue")
    }
}

/// Build a monochrome 36×36 template icon from the bundled RGBA app icon by
/// extracting the foreground crop marks and centre pixel against the cream card.
#[cfg(any(target_os = "macos", test))]
pub(crate) fn template_icon_from_rgba(
    rgba: &[u8],
    width: u32,
    height: u32,
) -> Option<Image<'static>> {
    if width == 0 || height == 0 || rgba.len() != (width as usize) * (height as usize) * 4 {
        return None;
    }
    let is_mark = |x: u32, y: u32| -> f32 {
        let index = ((y * width + x) * 4) as usize;
        let r = rgba[index];
        let g = rgba[index + 1];
        let b = rgba[index + 2];
        let a = rgba[index + 3];
        if a == 0 {
            return 0.0;
        }
        // The app icon draws dark crop marks and a terracotta centre pixel on a
        // cream card (RGB ~ 242, 239, 231). Any pixel clearly darker than cream
        // belongs to the foreground mark.
        let min_channel = r.min(g).min(b);
        if min_channel >= 195 {
            0.0
        } else {
            let coverage = ((195.0 - f32::from(min_channel)) / 60.0).clamp(0.0, 1.0);
            coverage * (f32::from(a) / 255.0)
        }
    };

    let mut min_x = width;
    let mut min_y = height;
    let mut max_x = 0u32;
    let mut max_y = 0u32;
    for y in 0..height {
        for x in 0..width {
            if is_mark(x, y) > 0.25 {
                min_x = min_x.min(x);
                min_y = min_y.min(y);
                max_x = max_x.max(x);
                max_y = max_y.max(y);
            }
        }
    }
    if min_x > max_x || min_y > max_y {
        return None;
    }

    let box_w = (max_x - min_x + 1) as f32;
    let box_h = (max_y - min_y + 1) as f32;
    let span = box_w.max(box_h);
    let pad = span * 0.14;
    let src_left = (min_x as f32) - (span - box_w) * 0.5 - pad;
    let src_top = (min_y as f32) - (span - box_h) * 0.5 - pad;
    let src_size = span + pad * 2.0;

    const OUT_SIZE: u32 = 36;
    let mut out = vec![0u8; (OUT_SIZE * OUT_SIZE * 4) as usize];
    let step = src_size / OUT_SIZE as f32;
    let samples = (step.ceil() as u32).clamp(1, 8);

    for oy in 0..OUT_SIZE {
        for ox in 0..OUT_SIZE {
            let mut sum = 0.0f32;
            let mut count = 0u32;
            for sy in 0..samples {
                for sx in 0..samples {
                    let fx = src_left + (ox as f32 + (sx as f32 + 0.5) / samples as f32) * step;
                    let fy = src_top + (oy as f32 + (sy as f32 + 0.5) / samples as f32) * step;
                    if fx >= 0.0 && fy >= 0.0 && fx < width as f32 && fy < height as f32 {
                        sum += is_mark(fx as u32, fy as u32);
                    }
                    count += 1;
                }
            }
            let alpha = ((sum / count as f32) * 255.0).round().clamp(0.0, 255.0) as u8;
            let idx = ((oy * OUT_SIZE + ox) * 4) as usize;
            out[idx] = 0;
            out[idx + 1] = 0;
            out[idx + 2] = 0;
            out[idx + 3] = alpha;
        }
    }

    Some(Image::new_owned(out, OUT_SIZE, OUT_SIZE))
}

#[cfg(target_os = "macos")]
fn tray_icon_image<R: Runtime>(app: &AppHandle<R>, monochrome: bool) -> Option<Image<'static>> {
    let icon = app.default_window_icon()?;
    if !monochrome {
        return Some(Image::new_owned(
            icon.rgba().to_vec(),
            icon.width(),
            icon.height(),
        ));
    }
    template_icon_from_rgba(icon.rgba(), icon.width(), icon.height()).or_else(|| {
        Some(Image::new_owned(
            icon.rgba().to_vec(),
            icon.width(),
            icon.height(),
        ))
    })
}

#[cfg(not(target_os = "macos"))]
fn tray_icon_image<R: Runtime>(app: &AppHandle<R>, _monochrome: bool) -> Option<Image<'static>> {
    app.default_window_icon()
        .map(|icon| Image::new_owned(icon.rgba().to_vec(), icon.width(), icon.height()))
}

fn build_tray_menu<R: Runtime>(
    app: &AppHandle<R>,
    snapshot: &TraySnapshot,
) -> tauri::Result<Menu<R>> {
    let current = MenuItemBuilder::with_id(MENU_STATUS_ID, format_current_job_line(snapshot))
        .enabled(false)
        .build(app)?;
    let waiting =
        MenuItemBuilder::with_id(MENU_WAITING_ID, format_waiting_line(snapshot.waiting_count))
            .enabled(false)
            .build(app)?;
    let (pause_id, pause_label) = pause_resume_item(snapshot.paused);
    let pause_resume = MenuItemBuilder::with_id(pause_id, pause_label).build(app)?;
    let show = MenuItemBuilder::with_id(MENU_SHOW_ID, "Show LocalSR").build(app)?;
    let quit = MenuItemBuilder::with_id(MENU_QUIT_ID, "Quit").build(app)?;

    let mut builder = MenuBuilder::new(app)
        .item(&current)
        .item(&waiting)
        .separator()
        .item(&pause_resume);
    if !snapshot.last_output_path.is_empty() {
        let reveal_label = if cfg!(target_os = "macos") {
            "Show Last Result in Finder"
        } else {
            "Show Last Result"
        };
        let reveal = MenuItemBuilder::with_id(MENU_REVEAL_LAST_ID, reveal_label).build(app)?;
        builder = builder.item(&reveal);
    }
    builder.separator().item(&show).item(&quit).build()
}

fn snapshot_from_state(state: &crate::state::AppState) -> Option<TraySnapshot> {
    let (visible, monochrome) = {
        let settings = lock(&state.settings).ok()?;
        (settings.show_in_menu_bar, settings.menu_bar_icon_monochrome)
    };
    if !visible {
        return Some(TraySnapshot {
            visible: false,
            monochrome,
            active_job_id: String::new(),
            active_action: String::new(),
            active_media_name: String::new(),
            progress_percent: 0,
            waiting_count: 0,
            paused: false,
            worker_status: String::new(),
            last_output_path: String::new(),
            title: None,
        });
    }
    let (active_job_id, status_title, progress, paused, worker_status, last_output_path) = {
        let runtime = lock(&state.runtime).ok()?;
        (
            runtime.active_job_id.clone(),
            runtime.status_title.clone(),
            runtime.progress,
            runtime.queue_paused,
            runtime.worker.clone(),
            runtime.last_output_path.clone(),
        )
    };
    let (waiting_count, active_info) = {
        let database = lock(&state.database).ok()?;
        let waiting = database.queued_job_count().unwrap_or(0);
        let active_info = if active_job_id.is_empty() {
            None
        } else {
            database.active_job_tray_info(&active_job_id).ok().flatten()
        };
        (waiting, active_info)
    };
    let (active_media_name, request_json) = match active_info {
        Some((name, json)) => (name, Some(json)),
        None => (String::new(), None),
    };
    let active_action = if active_job_id.is_empty() {
        String::new()
    } else {
        derive_action_label(&status_title, request_json.as_deref())
    };
    let title = if cfg!(target_os = "macos") {
        format_tray_title(&active_job_id, progress)
    } else {
        None
    };

    Some(TraySnapshot {
        visible: true,
        monochrome,
        active_job_id,
        active_action,
        active_media_name,
        progress_percent: clamp_progress_percent(progress),
        waiting_count,
        paused,
        worker_status,
        last_output_path,
        title,
    })
}

fn apply_snapshot<R: Runtime>(app: &AppHandle<R>, snapshot: &TraySnapshot) {
    if !snapshot.visible {
        let _ = app.remove_tray_by_id(TRAY_ID);
        return;
    }
    let Ok(menu) = build_tray_menu(app, snapshot) else {
        return;
    };
    if let Some(tray) = app.tray_by_id(TRAY_ID) {
        // The icon style changes rarely: only swap the image when it did.
        if ICON_MONOCHROME.swap(snapshot.monochrome, Ordering::SeqCst) != snapshot.monochrome {
            if let Some(icon) = tray_icon_image(app, snapshot.monochrome) {
                let _ = tray.set_icon(Some(icon));
            }
            #[cfg(target_os = "macos")]
            let _ = tray.set_icon_as_template(snapshot.monochrome);
        }
        let _ = tray.set_menu(Some(menu));
        #[cfg(target_os = "macos")]
        let _ = tray.set_title(snapshot.title.as_deref());
        return;
    }
    let mut builder = TrayIconBuilder::with_id(TRAY_ID)
        .tooltip("LocalSR")
        .menu(&menu)
        .show_menu_on_left_click(true);
    ICON_MONOCHROME.store(snapshot.monochrome, Ordering::SeqCst);
    if let Some(icon) = tray_icon_image(app, snapshot.monochrome) {
        builder = builder.icon(icon);
        #[cfg(target_os = "macos")]
        {
            builder = builder.icon_as_template(snapshot.monochrome);
        }
    }
    #[cfg(target_os = "macos")]
    if let Some(title) = snapshot.title.as_deref() {
        builder = builder.title(title);
    }
    let _ = builder.build(app);
}

pub fn install<R: Runtime>(app: &AppHandle<R>) {
    sync(app);
}

pub fn sync<R: Runtime>(app: &AppHandle<R>) {
    if TypeId::of::<R>() != TypeId::of::<tauri::Wry>() {
        return;
    }
    let Some(state) = app.try_state::<std::sync::Arc<crate::state::AppState>>() else {
        return;
    };
    let Some(snapshot) = snapshot_from_state(&state) else {
        return;
    };
    {
        let Ok(mut cached) = state.tray_state.lock() else {
            return;
        };
        if cached.as_ref() == Some(&snapshot) {
            return;
        }
        *cached = Some(snapshot.clone());
    }
    let handle = app.clone();
    let _ = app.run_on_main_thread(move || {
        apply_snapshot(&handle, &snapshot);
    });
}

pub fn handle_menu_event(app: &AppHandle, id: &str) -> bool {
    match id {
        MENU_PAUSE_ID => {
            let app = app.clone();
            tauri::async_runtime::spawn_blocking(move || {
                if let Some(state) = app.try_state::<std::sync::Arc<crate::state::AppState>>() {
                    crate::worker::pause_queue(&state, &app);
                }
            });
            true
        }
        MENU_RESUME_ID => {
            let app = app.clone();
            tauri::async_runtime::spawn_blocking(move || {
                if let Some(state) = app.try_state::<std::sync::Arc<crate::state::AppState>>() {
                    if let Err(error) = crate::worker::resume_queue(&state, &app) {
                        eprintln!("LocalSR: could not resume queue from tray: {error}");
                    }
                }
            });
            true
        }
        MENU_REVEAL_LAST_ID => {
            let app = app.clone();
            tauri::async_runtime::spawn_blocking(move || {
                if let Some(state) = app.try_state::<std::sync::Arc<crate::state::AppState>>() {
                    let path = lock(&state.runtime)
                        .map(|runtime| runtime.last_output_path.clone())
                        .unwrap_or_default();
                    if !path.is_empty() {
                        if let Err(error) = crate::commands::reveal_result_path(&state, &path) {
                            eprintln!("LocalSR: could not reveal result from tray: {error}");
                        }
                    }
                }
            });
            true
        }
        MENU_SHOW_ID => {
            crate::focus_main_window(app);
            true
        }
        MENU_QUIT_ID => {
            app.exit(0);
            true
        }
        _ => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_snapshot() -> TraySnapshot {
        TraySnapshot {
            visible: true,
            monochrome: true,
            active_job_id: String::new(),
            active_action: String::new(),
            active_media_name: String::new(),
            progress_percent: 0,
            waiting_count: 0,
            paused: false,
            worker_status: "ready".into(),
            last_output_path: String::new(),
            title: None,
        }
    }

    #[test]
    fn tray_title_shows_integer_percentage_while_running_and_clears_when_idle() {
        assert_eq!(format_tray_title("", 42.0), None);
        assert_eq!(format_tray_title("job-1", 42.4), Some("42%".into()));
        assert_eq!(format_tray_title("job-1", 42.6), Some("43%".into()));
        assert_eq!(format_tray_title("job-1", 150.0), Some("100%".into()));
        assert_eq!(format_tray_title("job-1", -3.0), Some("0%".into()));
        assert_eq!(format_tray_title("job-1", f64::NAN), Some("0%".into()));
    }

    #[test]
    fn current_job_and_waiting_lines_format_running_paused_and_idle_states() {
        let mut snap = sample_snapshot();
        assert_eq!(format_current_job_line(&snap), "Idle");
        assert_eq!(format_waiting_line(0), "No files waiting");

        snap.active_job_id = "job-1".into();
        snap.active_action = "Upscaling".into();
        snap.active_media_name = "IMG_1234.jpg".into();
        snap.progress_percent = 42;
        snap.waiting_count = 1;
        assert_eq!(
            format_current_job_line(&snap),
            "Upscaling IMG_1234.jpg · 42%"
        );
        assert_eq!(format_waiting_line(1), "1 file waiting");

        snap.waiting_count = 3;
        assert_eq!(format_waiting_line(3), "3 files waiting");

        snap.active_job_id.clear();
        snap.paused = true;
        assert_eq!(format_current_job_line(&snap), "Queue paused");
        assert_eq!(pause_resume_item(false), (MENU_PAUSE_ID, "Pause queue"));
        assert_eq!(pause_resume_item(true), (MENU_RESUME_ID, "Resume queue"));
    }

    #[test]
    fn action_label_derives_upscaling_restoring_and_editing_from_request_or_stage() {
        assert_eq!(
            derive_action_label(
                "Enhancing",
                Some(r#"{"type":"job_request","data":{"stages":[{"kind":"upscale"}]}}"#)
            ),
            "Upscaling"
        );
        assert_eq!(
            derive_action_label(
                "Enhancing",
                Some(r#"{"type":"job_request","data":{"stages":[{"kind":"restore"}]}}"#)
            ),
            "Restoring"
        );
        assert_eq!(
            derive_action_label(
                "Processing",
                Some(r#"{"type":"edit_job_request","data":{}}"#)
            ),
            "Editing"
        );
        assert_eq!(
            derive_action_label("Removing JPEG artifacts · stage 1/2", None),
            "Removing JPEG artifacts"
        );
        assert_eq!(derive_action_label("Saving video", None), "Saving video");
        assert_eq!(derive_action_label("Cancelling", None), "Cancelling");
    }

    #[test]
    fn template_icon_extracts_dark_foreground_mark_from_cream_app_icon() {
        // 8x8 cream card with a 2x2 dark pixel in the centre.
        let mut rgba = Vec::with_capacity(8 * 8 * 4);
        for y in 0..8 {
            for x in 0..8 {
                if (3..=4).contains(&x) && (3..=4).contains(&y) {
                    rgba.extend_from_slice(&[20, 24, 17, 255]);
                } else {
                    rgba.extend_from_slice(&[242, 239, 231, 255]);
                }
            }
        }
        let icon = template_icon_from_rgba(&rgba, 8, 8).expect("template icon");
        assert_eq!(icon.width(), 36);
        assert_eq!(icon.height(), 36);
        // Corners should be transparent; centre should have solid black RGB and non-zero alpha.
        let out = icon.rgba();
        assert_eq!(out[3], 0);
        let centre = ((18 * 36 + 18) * 4) as usize;
        assert_eq!(out[centre], 0);
        assert_eq!(out[centre + 1], 0);
        assert_eq!(out[centre + 2], 0);
        assert!(out[centre + 3] > 200);
    }
}
