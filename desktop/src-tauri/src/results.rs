//! Saved results of one media item, as the preview's result choosers list them.
//!
//! Everything here is read from the job rows. The worker request says which
//! models and settings ran; the separate `ui_json` column says which quality,
//! content and recipe the user had chosen when the job was queued. Older jobs
//! have no `ui_json` and report `ui: None` ("Settings unavailable").

use std::path::Path;

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::types::CatalogManifest;

/// Refuse a result that is a link, or that sits below one. A result is a file the
/// engine wrote under a resolved output folder, so its recorded path is already its
/// real path; one that no longer is (the file or a folder above it became a link) could
/// lead anywhere, and resolving it would authorize its target. A file with a second
/// name could be another file the engine never wrote. A path that does not exist is
/// not a link.
pub fn ensure_not_linked(path: &Path) -> crate::error::AppResult<()> {
    let refused = || crate::error::AppError::Validation("output path was not authorized".into());
    let Ok(info) = std::fs::symlink_metadata(path) else {
        return Ok(());
    };
    if info.file_type().is_symlink() || std::fs::canonicalize(path).ok().as_deref() != Some(path) {
        return Err(refused());
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if info.is_file() && info.nlink() != 1 {
            return Err(refused());
        }
    }
    Ok(())
}

/// The columns `summary_from_row` reads, in its order.
pub const SUMMARY_COLUMNS: &str =
    "id, updated_at, status, output_path, error, elapsed_seconds, request_json, ui_json, (SELECT fps FROM media WHERE media.id=jobs.media_id), (SELECT duration_seconds FROM media WHERE media.id=jobs.media_id), partial_json";
/// How many columns `SUMMARY_COLUMNS` has: a query that appends its own reads them after.
pub const SUMMARY_COLUMN_COUNT: usize = 11;

/// Host-only labels recorded when a job is queued. They never enter the worker
/// request, so the protocol and the timing profile stay as they were.
#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq)]
pub struct ResultUi {
    #[serde(default)]
    pub quality: String,
    #[serde(default)]
    pub content: String,
    #[serde(default)]
    pub recipe_name: String,
    #[serde(default)]
    pub fixes: Vec<String>,
}

#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct ResultStage {
    pub kind: String,
    pub model_id: String,
    pub model_label: String,
}

#[derive(Clone, Debug, Serialize)]
pub struct ResultSummary {
    pub job_id: String,
    /// When the job reached this status (milliseconds since the epoch).
    pub completed_at: i64,
    /// completed, failed, cancelled or interrupted.
    pub status: String,
    /// upscale, restore, edit or video.
    pub kind: String,
    pub output_path: String,
    pub output_name: String,
    /// Current file size in bytes, or 0 when unavailable.
    pub size_bytes: u64,
    /// Completed and its file exists now.
    pub available: bool,
    pub model_id: String,
    pub model_label: String,
    #[serde(skip)]
    pub checkpoint_filename: String,
    pub output_duration_seconds: f64,
    pub output_fps: f64,
    #[serde(default)]
    pub partial: bool,
    #[serde(default)]
    pub kept_seconds: f64,
    /// Frames of a kept video part (exact, read from the file).
    #[serde(default)]
    pub kept_frames: u64,
    /// The finished tiles of a kept photo part, [x, y, width, height] in its pixels.
    #[serde(default)]
    pub finished_tiles: Vec<[u64; 4]>,
    #[serde(default)]
    pub total_tiles: u64,
    /// The recorded output scale; 0 when the request has none (Edit).
    pub scale: u64,
    /// The written instruction of an Edit; empty for other kinds.
    pub prompt: String,
    /// The longest output edge an Edit was asked for; 0 for other kinds.
    pub max_dimension: u64,
    /// Every recorded stage, the primary model included.
    pub stages: Vec<ResultStage>,
    /// Completion notes (such as a skipped fix) or the failure reason.
    pub notes: String,
    /// Run time, including model loading and saving; 0 when unknown.
    pub elapsed_seconds: f64,
    /// Device and Neural Engine preference recorded in the request, not the actual route.
    pub device: String,
    pub neural_engine: bool,
    pub ui: Option<ResultUi>,
}

#[derive(Clone, Debug, Serialize)]
pub struct MediaResults {
    pub media_id: String,
    /// Finished attempts, newest first: completed ones and those that failed,
    /// were cancelled or were interrupted.
    pub results: Vec<ResultSummary>,
    /// All finished attempts of this media, also those beyond this page.
    pub total_count: u64,
    pub completed_count: u64,
    /// Completed results whose file exists.
    pub available_count: u64,
    /// The newest completed result whose file exists.
    pub default_result_id: Option<String>,
    /// Pass as `before` for the next page; None on the last page.
    pub next_before: Option<String>,
}

/// The primary model of a recorded request: its id and a fallback label.
pub fn request_model(request: &Value) -> (String, String) {
    let data = &request["data"];
    let model_path = data["model_path"].as_str().unwrap_or_default();
    let stages = data["stages"].as_array();
    let primary = stages.and_then(|stages| {
        stages
            .iter()
            .find(|stage| !model_path.is_empty() && stage["model_path"] == model_path)
            .or_else(|| {
                stages
                    .iter()
                    .rev()
                    .find(|stage| matches!(stage["kind"].as_str(), Some("upscale" | "restore")))
            })
            .or_else(|| stages.last())
    });
    let id = primary
        .and_then(|stage| stage["model_id"].as_str())
        .or_else(|| data["model_id"].as_str())
        // A video engine job names its engine here and carries no image model.
        .or_else(|| data["video_model_id"].as_str())
        .or_else(|| data["interpolation_model_id"].as_str())
        .or_else(|| {
            data["audio_denoise"]
                .as_bool()
                .filter(|active| *active)
                .and_then(|_| data["audio_denoise_model_id"].as_str())
        })
        .filter(|id| !id.is_empty())
        .unwrap_or_default()
        .to_owned();
    let label = if id.is_empty() || id == "__custom__" {
        Path::new(model_path)
            .file_stem()
            .and_then(|name| name.to_str())
            .filter(|name| !name.is_empty())
            .unwrap_or_else(|| {
                if data["deflicker"].as_bool() == Some(true) {
                    "De-flicker"
                } else if data["output_scale"].as_u64() == Some(1) {
                    "Video"
                } else {
                    "Unknown model"
                }
            })
            .to_owned()
    } else {
        id.clone()
    };
    (id, label)
}

pub fn request_kind(request: &Value) -> &'static str {
    match request["type"].as_str() {
        Some("edit_job_request") => "edit",
        Some("video_job_request") => "video",
        _ => {
            let primary = request_model(request).0;
            let stages = request["data"]["stages"].as_array();
            let restores =
                stages.into_iter().flatten().any(|stage| {
                    stage["kind"] == "restore" && stage["model_id"] == primary.as_str()
                }) || (request["data"]["output_scale"].as_u64() == Some(1)
                    && stages.is_some_and(|list| {
                        let steps = crate::steps::registry();
                        list.iter().all(|stage| {
                            stage["kind"]
                                .as_str()
                                .is_some_and(|kind| steps.is_fix_stage_kind(kind))
                        })
                    }));
            if restores {
                "restore"
            } else {
                "upscale"
            }
        }
    }
}

/// The name the library shows for a catalog model, without its role suffix.
pub fn catalog_label(catalog: &CatalogManifest, model_id: &str) -> Option<String> {
    let Some(model) = catalog
        .models
        .iter()
        .find(|model| model.model_id == model_id)
    else {
        // Video engines and frame-interpolation models are bundles with a plain name.
        return catalog
            .video_models
            .iter()
            .chain(&catalog.interpolation_models)
            .chain(&catalog.audio_models)
            .find(|model| model.model_id == model_id)
            .map(|model| model.name.clone());
    };
    if !model.display_name.is_empty() {
        return Some(model.display_name.clone());
    }
    let name = model.name.split(" — ").next().unwrap_or(&model.name);
    Some(
        name.replace(" 4x ", " ×4 ")
            .replace(" 2x ", " ×2 ")
            .replace(" 1x ", " ×1 "),
    )
}

fn video_checkpoint_id<'a>(catalog: &'a CatalogManifest, filename: &str) -> Option<&'a str> {
    catalog
        .models
        .iter()
        .find(|model| model.filename == filename)
        .map(|model| model.model_id.as_str())
}

fn video_timing(request: &Value, source_fps: f64, source_duration: f64) -> (f64, f64) {
    if request_kind(request) != "video" {
        return (0.0, 0.0);
    }
    let data = &request["data"];
    let fps = data["fps"]
        .as_f64()
        .filter(|n| *n > 0.0)
        .unwrap_or(source_fps);
    let duration = if data["start_frame"].is_number() || data["end_frame"].is_number() {
        if source_fps <= 0.0 {
            0.0
        } else {
            let start = data["start_frame"].as_u64().unwrap_or(0) as f64 / source_fps;
            let end = data["end_frame"]
                .as_u64()
                .map(|n| (n + 1) as f64 / source_fps)
                .unwrap_or(source_duration);
            if source_duration > 0.0 {
                (end.min(source_duration) - start).max(0.0)
            } else if data["end_frame"].is_number() {
                (end - start).max(0.0)
            } else {
                0.0
            }
        }
    } else {
        source_duration
    };
    let output_fps = match data["interpolation"].as_str().unwrap_or("off") {
        "x2" => fps * 2.0,
        "x4" => fps * 4.0,
        "fps60" if fps > 0.0 && fps < 59.4 => 60.0,
        "fps120" if fps > 0.0 && fps < 118.8 => 120.0,
        _ => fps,
    };
    (duration.max(0.0), output_fps.max(0.0))
}

/// Replace model ids with catalog names where the catalog knows them.
pub fn label_summary(catalog: &CatalogManifest, summary: &mut ResultSummary) {
    if summary.kind == "video" && summary.model_id.is_empty() {
        if let Some(id) = video_checkpoint_id(catalog, &summary.checkpoint_filename) {
            summary.model_id = id.to_owned();
        }
    }
    let primary = if summary.kind == "edit" {
        crate::editing::model_name(&summary.model_id)
    } else {
        catalog_label(catalog, &summary.model_id)
    };
    if let Some(label) = primary {
        summary.model_label = label;
    }
    for stage in &mut summary.stages {
        if let Some(label) = catalog_label(catalog, &stage.model_id) {
            stage.model_label = label;
        }
    }
}

/// A field only an Edit request carries; the default for any other kind.
fn edit_field<T: Default>(request: &Value, name: &str, read: impl Fn(&Value) -> Option<T>) -> T {
    if request_kind(request) != "edit" {
        return T::default();
    }
    read(&request["data"][name]).unwrap_or_default()
}

fn stage_label(id: &str, path: Option<&str>) -> String {
    if id == "__custom__" {
        if let Some(stem) = path
            .and_then(|value| Path::new(value).file_stem())
            .and_then(|name| name.to_str())
            .filter(|name| !name.is_empty())
        {
            return stem.to_owned();
        }
    }
    if id.is_empty() {
        "Unknown model".into()
    } else {
        id.to_owned()
    }
}

fn request_stages(request: &Value) -> Vec<ResultStage> {
    let data = &request["data"];
    let mut stages: Vec<ResultStage> = data["stages"]
        .as_array()
        .into_iter()
        .flatten()
        .map(|stage| {
            let id = stage["model_id"].as_str().unwrap_or_default();
            let path = stage["model_path"]
                .as_str()
                .or_else(|| data["model_path"].as_str());
            ResultStage {
                kind: stage["kind"].as_str().unwrap_or_default().to_owned(),
                model_id: id.to_owned(),
                model_label: stage_label(id, path),
            }
        })
        .collect();
    if request["type"].as_str() == Some("video_job_request") {
        if stages.is_empty() {
            if let Some(id) = data["video_model_id"]
                .as_str()
                .filter(|id| !id.is_empty() && *id != "frame_by_frame")
            {
                stages.push(ResultStage {
                    kind: "video".into(),
                    model_id: id.to_owned(),
                    model_label: id.to_owned(),
                });
            }
        }
        let interpolation = data["interpolation"].as_str().unwrap_or("off");
        if !interpolation.is_empty() && interpolation != "off" {
            if let Some(id) = data["interpolation_model_id"]
                .as_str()
                .filter(|id| !id.is_empty())
            {
                let stage = ResultStage {
                    kind: "interpolate".into(),
                    model_id: id.to_owned(),
                    model_label: id.to_owned(),
                };
                if data["interpolation_order"].as_str() == Some("before_upscale") {
                    stages.insert(0, stage);
                } else {
                    stages.push(stage);
                }
            }
        }
        if data["audio_denoise"].as_bool() == Some(true) {
            if let Some(id) = data["audio_denoise_model_id"]
                .as_str()
                .filter(|id| !id.is_empty())
            {
                stages.push(ResultStage {
                    kind: "audio_denoise".into(),
                    model_id: id.to_owned(),
                    model_label: id.to_owned(),
                });
            }
        }
    }
    stages
}

pub(crate) fn partial_output_seconds(requested: &Path, reported: &Path) -> Option<u64> {
    if requested.parent() != reported.parent() || requested.extension() != reported.extension() {
        return None;
    }
    let (Some(stem), Some(name)) = (
        requested.file_stem().and_then(|s| s.to_str()),
        reported.file_stem().and_then(|s| s.to_str()),
    ) else {
        return None;
    };
    let duration = name.strip_prefix(&format!("{stem}_partial_"))?;
    parse_minutes_seconds(without_copy_number(duration), 'm', 's')
}

/// `<name>_partial.<ext>` or `<name>_partial-N.<ext>` beside the requested photo: where the
/// engine saves the finished tiles of a stopped photo (`partial_image.partial_output_path`).
pub(crate) fn is_partial_photo_output(requested: &Path, reported: &Path) -> bool {
    if requested.parent() != reported.parent() || requested.extension() != reported.extension() {
        return false;
    }
    let (Some(stem), Some(name)) = (
        requested.file_stem().and_then(|s| s.to_str()),
        reported.file_stem().and_then(|s| s.to_str()),
    ) else {
        return false;
    };
    name.strip_prefix(&format!("{stem}_partial"))
        .is_some_and(|rest| rest.is_empty() || without_copy_number(rest).is_empty())
}

/// Drops the `-N` the engine appends when a kept part's name is already taken.
fn without_copy_number(text: &str) -> &str {
    match text.rsplit_once('-') {
        Some((head, number))
            if !number.is_empty() && number.bytes().all(|c| c.is_ascii_digit()) =>
        {
            head
        }
        _ => text,
    }
}

fn parse_minutes_seconds(text: &str, sep: char, suffix: char) -> Option<u64> {
    let body = if suffix == '\0' {
        text
    } else {
        text.strip_suffix(suffix)?
    };
    let (minutes, seconds) = body.split_once(sep)?;
    if minutes.is_empty()
        || !minutes.bytes().all(|c| c.is_ascii_digit())
        || seconds.len() != 2
        || !seconds.bytes().all(|c| c.is_ascii_digit())
    {
        return None;
    }
    let minutes = minutes.parse::<u64>().ok()?;
    let seconds = seconds.parse::<u64>().ok()?;
    if seconds >= 60 {
        return None;
    }
    Some(minutes.saturating_mul(60).saturating_add(seconds))
}

fn stopped_kept_seconds(notes: &str) -> Option<u64> {
    let rest = notes.strip_prefix("Stopped, kept ")?;
    let time = rest.split(['.', ' ']).next()?;
    parse_minutes_seconds(time, ':', '\0')
}

pub fn summary_from_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<ResultSummary> {
    let request: Value = serde_json::from_str(&row.get::<_, String>(6)?).unwrap_or_default();
    let data = &request["data"];
    let status: String = row.get(2)?;
    let output_path: String = row.get(3)?;
    let notes: String = row.get(4)?;
    let kind = request_kind(&request);
    let (model_id, model_label) = request_model(&request);
    let ui_json: String = row.get(7)?;
    let (output_duration_seconds, output_fps) = video_timing(
        &request,
        row.get::<_, Option<f64>>(8)?.unwrap_or(0.0),
        row.get::<_, Option<f64>>(9)?.unwrap_or(0.0),
    );
    let partial_seconds = if kind == "video" && status == "completed" && !output_path.is_empty() {
        data["output_video_path"]
            .as_str()
            .filter(|path| !path.is_empty())
            .and_then(|requested| {
                partial_output_seconds(Path::new(requested), Path::new(&output_path))
            })
            .or_else(|| stopped_kept_seconds(&notes))
    } else {
        None
    };
    // Recorded when a stop kept the finished part (worker.rs partial_record); older rows
    // and queries without the column have none.
    let kept: Value = row
        .get::<_, Option<String>>(10)
        .ok()
        .flatten()
        .filter(|text| !text.is_empty())
        .and_then(|text| serde_json::from_str(&text).ok())
        .unwrap_or(Value::Null);
    let finished_tiles: Vec<[u64; 4]> = kept["finished_tiles"]
        .as_array()
        .map(|tiles| {
            tiles
                .iter()
                .filter_map(|tile| {
                    let values: Vec<u64> =
                        tile.as_array()?.iter().filter_map(Value::as_u64).collect();
                    <[u64; 4]>::try_from(values).ok()
                })
                .collect()
        })
        .unwrap_or_default();
    let total_tiles = kept["total_tiles"].as_u64().unwrap_or(0);
    let recorded_seconds = kept["kept_seconds"].as_f64().filter(|s| *s > 0.0);
    Ok(ResultSummary {
        job_id: row.get(0)?,
        completed_at: row.get(1)?,
        available: status == "completed"
            && !output_path.is_empty()
            && Path::new(&output_path).is_file(),
        status,
        kind: kind.into(),
        output_name: Path::new(&output_path)
            .file_name()
            .and_then(|name| name.to_str())
            .unwrap_or_default()
            .to_owned(),
        size_bytes: std::fs::metadata(&output_path)
            .map(|m| m.len())
            .unwrap_or(0),
        output_path,
        model_id,
        model_label,
        checkpoint_filename: Path::new(data["model_path"].as_str().unwrap_or_default())
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or_default()
            .to_owned(),
        output_duration_seconds,
        output_fps,
        partial: partial_seconds.is_some() || recorded_seconds.is_some() || total_tiles > 0,
        kept_seconds: recorded_seconds
            .or(partial_seconds.map(|s| s as f64))
            .unwrap_or(0.0),
        kept_frames: kept["kept_frames"].as_u64().unwrap_or(0),
        finished_tiles,
        total_tiles,
        scale: data["output_scale"].as_u64().unwrap_or(0),
        prompt: edit_field(&request, "prompt", |value| {
            value.as_str().map(str::to_owned)
        }),
        max_dimension: edit_field(&request, "max_dimension", Value::as_u64),
        stages: request_stages(&request),
        notes,
        elapsed_seconds: row.get(5)?,
        device: data["device"].as_str().unwrap_or_default().to_owned(),
        neural_engine: data["neural_engine"].as_bool().unwrap_or(false),
        ui: if ui_json.is_empty() {
            None
        } else {
            serde_json::from_str(&ui_json).ok()
        },
    })
}

#[cfg(test)]
mod tests {
    #[test]
    fn kept_parts_with_a_copy_number_are_recognised() {
        use crate::results::{is_partial_photo_output, partial_output_seconds};
        use std::path::Path;
        let video = Path::new("/out/clip_x4.mp4");
        assert_eq!(
            partial_output_seconds(video, Path::new("/out/clip_x4_partial_0m07s-2.mp4")),
            partial_output_seconds(video, Path::new("/out/clip_x4_partial_0m07s.mp4"))
        );
        assert!(
            partial_output_seconds(video, Path::new("/out/clip_x4_partial_0m07s.mp4")).is_some()
        );
        let photo = Path::new("/out/photo_x4.png");
        assert!(is_partial_photo_output(
            photo,
            Path::new("/out/photo_x4_partial.png")
        ));
        assert!(is_partial_photo_output(
            photo,
            Path::new("/out/photo_x4_partial-3.png")
        ));
        assert!(!is_partial_photo_output(
            photo,
            Path::new("/out/photo_x4_partial.jpg")
        ));
        assert!(!is_partial_photo_output(
            photo,
            Path::new("/elsewhere/photo_x4_partial.png")
        ));
        assert!(!is_partial_photo_output(
            photo,
            Path::new("/out/photo_x4_partialx.png")
        ));
        assert!(!is_partial_photo_output(
            photo,
            Path::new("/out/other_partial.png")
        ));
    }

    use serde_json::json;

    #[test]
    fn a_kept_part_reports_its_exact_frames_seconds_and_tiles() {
        let db = rusqlite::Connection::open_in_memory().unwrap();
        let read = |request: &str, output: &str, partial: &str| {
            db.query_row(
                "SELECT 'job', 1, 'completed', ?2, '', 0.0, ?1, '', 30.0, 2.0, ?3",
                [request, output, partial],
                super::summary_from_row,
            )
            .unwrap()
        };
        let video =
            r#"{"type":"video_job_request","data":{"output_video_path":"/o/clip_video_4x.mkv"}}"#;
        let kept = read(
            video,
            "/o/clip_video_4x_partial_0m00s.mkv",
            r#"{"kept_seconds":0.074,"kept_frames":2}"#,
        );
        assert!(kept.partial);
        assert_eq!(kept.kept_frames, 2);
        assert!((kept.kept_seconds - 0.074).abs() < 1e-9);
        let photo = r#"{"type":"job_request","data":{"output_path":"/o/p_upscaled_4x.png"}}"#;
        let tiles = read(
            photo,
            "/o/p_upscaled_4x_partial.png",
            r#"{"finished_tiles":[[0,0,512,512],[512,0,512,512]],"total_tiles":6}"#,
        );
        assert!(tiles.partial);
        assert_eq!(tiles.total_tiles, 6);
        assert_eq!(
            tiles.finished_tiles,
            vec![[0, 0, 512, 512], [512, 0, 512, 512]]
        );
        // A finished photo without a record is not partial.
        assert!(!read(photo, "/o/p_upscaled_4x.png", "").partial);
    }

    #[test]
    fn saved_results_expose_the_requested_device_when_recorded() {
        let db = rusqlite::Connection::open_in_memory().unwrap();
        let read = |request: &str| {
            db.query_row(
                "SELECT 'job', 1, 'completed', '', '', 0.0, ?1, '', NULL, NULL",
                [request],
                super::summary_from_row,
            )
            .unwrap()
        };
        assert_eq!(
            read(r#"{"data":{"device":"cuda:0","neural_engine":false}}"#).device,
            "cuda:0"
        );
        assert_eq!(read(r#"{"data":{}}"#).device, "");
        assert!(read(r#"{"data":{"device":"mps","neural_engine":true}}"#).neural_engine);
    }

    #[test]
    fn video_results_include_interpolation_and_audio_models_in_order() {
        let db = rusqlite::Connection::open_in_memory().unwrap();
        let read = |order: &str| {
            let request = json!({"type":"video_job_request", "data": {
                "interpolation":"x2", "interpolation_order": order,
                "interpolation_model_id":"rife", "audio_denoise":true,
                "audio_denoise_model_id":"audio", "stages":[{"kind":"upscale", "model_id":"image"}]
            }})
            .to_string();
            db.query_row(
                "SELECT 'job', 1, 'completed', '', '', 0.0, ?1, '', NULL, NULL",
                [request],
                super::summary_from_row,
            )
            .unwrap()
        };
        let ids = |order| {
            read(order)
                .stages
                .into_iter()
                .map(|s| s.model_id)
                .collect::<Vec<_>>()
        };
        assert_eq!(ids("before_upscale"), ["rife", "image", "audio"]);
        assert_eq!(ids("after_upscale"), ["image", "rife", "audio"]);
    }

    #[test]
    fn temporal_video_and_custom_checkpoint_stages_keep_their_labels() {
        let db = rusqlite::Connection::open_in_memory().unwrap();
        let read = |request: serde_json::Value| {
            db.query_row(
                "SELECT 'job', 1, 'completed', '', '', 0.0, ?1, '', NULL, NULL",
                [request.to_string()],
                super::summary_from_row,
            )
            .unwrap()
        };
        let temporal = read(json!({"type":"video_job_request", "data": {
            "video_model_id": "seedvr2_3b", "audio_denoise": true,
            "audio_denoise_model_id": "deepfilternet3"
        }}));
        assert_eq!(
            temporal
                .stages
                .iter()
                .map(|s| s.model_id.as_str())
                .collect::<Vec<_>>(),
            ["seedvr2_3b", "deepfilternet3"]
        );
        let custom = read(json!({"type":"job_request", "data": {
            "model_path": "/models/my-fine.safetensors",
            "stages": [{"kind":"upscale", "model_id":"__custom__", "model_path":"/models/my-fine.safetensors"}]
        }}));
        assert_eq!(custom.stages[0].model_label, "my-fine");
    }

    #[test]
    fn video_engine_jobs_are_labelled_with_the_engine_name() {
        let directory = tempfile::tempdir().unwrap();
        let catalog =
            crate::catalog::load_catalog(&crate::paths::AppPaths::under(directory.path())).unwrap();
        let engine = &catalog.video_models[0];
        let request = json!({"type": "video_job_request", "data": {
            "model_path": "", "video_model_id": engine.model_id}});
        let (id, _) = super::request_model(&request);
        assert_eq!(id, engine.model_id);
        assert_eq!(
            super::catalog_label(&catalog, &id).as_deref(),
            Some(engine.name.as_str())
        );
        let interpolation = &catalog.interpolation_models[0];
        assert_eq!(
            super::catalog_label(&catalog, &interpolation.model_id).as_deref(),
            Some(interpolation.name.as_str())
        );
        assert_eq!(super::catalog_label(&catalog, "no-such-model"), None);
    }

    #[test]
    fn custom_labels_use_the_checkpoint_name_and_primary_stages_match_the_recorded_path() {
        assert_eq!(
            super::request_model(
                &json!({"data":{"model_path":"/models/my-fine.safetensors","stages":[
                    {"kind":"restore","model_path":"/models/fix.pth","model_id":"fix"},
                    {"kind":"upscale","model_path":"/models/my-fine.safetensors","model_id":"__custom__"}
                ]}})
            ),
            ("__custom__".into(), "my-fine".into())
        );
        assert_eq!(
            super::request_model(&serde_json::Value::Null),
            (String::new(), "Unknown model".into())
        );
    }

    #[test]
    fn video_timing_uses_source_and_interpolation() {
        let request = json!({"type":"video_job_request","data":{"interpolation":"fps60"}});
        assert_eq!(super::video_timing(&request, 15.0, 16.4), (16.4, 60.0));
        assert_eq!(super::video_timing(&request, 59.94, 16.4), (16.4, 59.94));
        let plain = json!({"type":"video_job_request","data":{}});
        assert_eq!(super::video_timing(&plain, 15.0, 16.4), (16.4, 15.0));
        let trimmed = json!({"type":"video_job_request","data":{"start_frame":15,"end_frame":75}});
        assert!((super::video_timing(&trimmed, 15.0, 16.4).0 - 61.0 / 15.0).abs() < 1e-9);
    }

    #[test]
    fn video_checkpoint_matches_catalog_filename() {
        let directory = tempfile::tempdir().unwrap();
        let catalog =
            crate::catalog::load_catalog(&crate::paths::AppPaths::under(directory.path())).unwrap();
        let model = &catalog.models[0];
        assert_eq!(
            super::video_checkpoint_id(&catalog, &model.filename),
            Some(model.model_id.as_str())
        );
        assert_eq!(super::video_checkpoint_id(&catalog, "custom.pth"), None);
    }

    #[test]
    fn kinds_tell_restore_from_upscale_and_edit_by_the_primary_stage() {
        let restore = json!({"type":"job_request","data":{"model_path":"/n.pth","stages":[
            {"kind":"restore","model_id":"nafnet","model_path":"/n.pth"}]}});
        let fixed_upscale = json!({"type":"job_request","data":{"model_path":"/u.pth","stages":[
            {"kind":"restore","model_id":"nafnet","model_path":"/n.pth"},
            {"kind":"upscale","model_id":"swinir","model_path":"/u.pth"}]}});
        let edit = json!({"type":"edit_job_request","data":{"model_id":"qwen_edit"}});
        assert_eq!(super::request_kind(&restore), "restore");
        assert_eq!(super::request_kind(&fixed_upscale), "upscale");
        assert_eq!(super::request_model(&fixed_upscale).0, "swinir");
        assert_eq!(super::request_kind(&edit), "edit");
        assert_eq!(super::request_model(&edit).0, "qwen_edit");
    }

    #[test]
    fn partial_video_results_are_flagged_with_kept_duration() {
        let db = rusqlite::Connection::open_in_memory().unwrap();
        let req = json!({
            "type": "video_job_request",
            "data": {"output_video_path": "/out/clip.mp4"}
        })
        .to_string();
        let partial = db
            .query_row(
                "SELECT 'job-partial', 1, 'completed', '/out/clip_partial_0m42s.mp4', 'Stopped, kept 0:42', 12.0, ?1, '', 24.0, 90.0",
                [&req],
                super::summary_from_row,
            )
            .unwrap();
        assert!(partial.partial);
        assert_eq!(partial.kept_seconds, 42.0);
        assert_eq!(partial.output_duration_seconds, 90.0);

        let complete = db
            .query_row(
                "SELECT 'job-full', 1, 'completed', '/out/clip.mp4', '', 20.0, ?1, '', 24.0, 90.0",
                [&req],
                super::summary_from_row,
            )
            .unwrap();
        assert!(!complete.partial);
        assert_eq!(complete.kept_seconds, 0.0);
    }
}
