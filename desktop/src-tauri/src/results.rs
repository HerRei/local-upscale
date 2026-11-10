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

/// The columns `summary_from_row` reads, in its order.
pub const SUMMARY_COLUMNS: &str =
    "id, updated_at, status, output_path, error, elapsed_seconds, request_json, ui_json";

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
    /// Completed and its file exists now.
    pub available: bool,
    pub model_id: String,
    pub model_label: String,
    /// The recorded output scale; 0 when the request has none (Edit).
    pub scale: u64,
    /// Every recorded stage, the primary model included.
    pub stages: Vec<ResultStage>,
    /// Completion notes (such as a skipped fix) or the failure reason.
    pub notes: String,
    /// Run time, including model loading and saving; 0 when unknown.
    pub elapsed_seconds: f64,
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
    let stages = data["stages"].as_array();
    let primary = stages.and_then(|stages| {
        stages
            .iter()
            .find(|stage| {
                !data["model_path"].is_null() && stage["model_path"] == data["model_path"]
            })
            .or_else(|| {
                stages
                    .iter()
                    .rev()
                    .find(|stage| matches!(stage["kind"].as_str(), Some("upscale" | "restore")))
            })
    });
    let id = primary
        .and_then(|stage| stage["model_id"].as_str())
        .or_else(|| data["model_id"].as_str())
        // A video engine job names its engine here and carries no image model.
        .or_else(|| data["video_model_id"].as_str())
        .filter(|id| !id.is_empty())
        .unwrap_or_default()
        .to_owned();
    let label = if id.is_empty() || id == "__custom__" {
        Path::new(data["model_path"].as_str().unwrap_or_default())
            .file_stem()
            .and_then(|name| name.to_str())
            .filter(|name| !name.is_empty())
            .unwrap_or("Unknown model")
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
            let restores = request["data"]["stages"]
                .as_array()
                .into_iter()
                .flatten()
                .any(|stage| stage["kind"] == "restore" && stage["model_id"] == primary.as_str());
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

/// Replace model ids with catalog names where the catalog knows them.
pub fn label_summary(catalog: &CatalogManifest, summary: &mut ResultSummary) {
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

pub fn summary_from_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<ResultSummary> {
    let request: Value = serde_json::from_str(&row.get::<_, String>(6)?).unwrap_or_default();
    let data = &request["data"];
    let status: String = row.get(2)?;
    let output_path: String = row.get(3)?;
    let (model_id, model_label) = request_model(&request);
    let ui_json: String = row.get(7)?;
    Ok(ResultSummary {
        job_id: row.get(0)?,
        completed_at: row.get(1)?,
        available: status == "completed"
            && !output_path.is_empty()
            && Path::new(&output_path).is_file(),
        status,
        kind: request_kind(&request).into(),
        output_name: Path::new(&output_path)
            .file_name()
            .and_then(|name| name.to_str())
            .unwrap_or_default()
            .to_owned(),
        output_path,
        model_id,
        model_label,
        scale: data["output_scale"].as_u64().unwrap_or(0),
        stages: data["stages"]
            .as_array()
            .into_iter()
            .flatten()
            .map(|stage| {
                let id = stage["model_id"].as_str().unwrap_or_default();
                ResultStage {
                    kind: stage["kind"].as_str().unwrap_or_default().to_owned(),
                    model_id: id.to_owned(),
                    model_label: if id.is_empty() {
                        "Unknown model".into()
                    } else {
                        id.to_owned()
                    },
                }
            })
            .collect(),
        notes: row.get(4)?,
        elapsed_seconds: row.get(5)?,
        ui: if ui_json.is_empty() {
            None
        } else {
            serde_json::from_str(&ui_json).ok()
        },
    })
}

#[cfg(test)]
mod tests {
    use serde_json::json;

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
}
