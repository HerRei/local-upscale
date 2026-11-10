//! Host-owned selections and bounded preview requests for the Compare tab.
use std::{fs, time::SystemTime};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::{
    database::Database,
    error::{AppError, AppResult},
    worker::RESULT_PREVIEW_MAX_DIMENSION,
};

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum ComparePick {
    Original { media_id: String },
    Output { media_id: String, job_id: String },
}

impl ComparePick {
    fn media_id(&self) -> &str {
        match self {
            Self::Original { media_id } | Self::Output { media_id, .. } => media_id,
        }
    }
    fn target_id(&self) -> &str {
        match self {
            Self::Original { media_id } => media_id,
            Self::Output { job_id, .. } => job_id,
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum CompareStatus {
    Loading,
    Ready,
    Error,
    Missing,
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct FileIdentity {
    size: u64,
    modified: Option<SystemTime>,
    created: Option<SystemTime>,
    #[cfg(unix)]
    device: u64,
    #[cfg(unix)]
    inode: u64,
}

fn file_identity(path: &str) -> Option<FileIdentity> {
    let metadata = fs::metadata(path).ok()?;
    if !metadata.is_file() {
        return None;
    }
    #[cfg(unix)]
    use std::os::unix::fs::MetadataExt;
    Some(FileIdentity {
        size: metadata.len(),
        modified: metadata.modified().ok(),
        created: metadata.created().ok(),
        #[cfg(unix)]
        device: metadata.dev(),
        #[cfg(unix)]
        inode: metadata.ino(),
    })
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
pub struct Region {
    pub x: u32,
    pub y: u32,
    pub width: u32,
    pub height: u32,
    pub target_width: u32,
    pub target_height: u32,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
pub struct RegionResult {
    pub bounds: Region,
    pub data_url: String,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
pub struct CompareSlot {
    pub pick: ComparePick,
    pub status: CompareStatus,
    pub preview_data_url: String,
    pub error: String,
    pub width: u32,
    pub height: u32,
    pub preview_width: u32,
    pub preview_height: u32,
    pub region: Option<RegionResult>,
    #[serde(skip)]
    requested_region: Option<Region>,
    #[serde(skip)]
    path: String,
    #[serde(skip)]
    file: Option<FileIdentity>,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
pub struct CompareState {
    pub generation: u64,
    pub slots: Vec<CompareSlot>,
    #[serde(skip)]
    region_request_id: u64,
}

// Resolve only paths owned by an image in the database. Validation errors
// mean the selection disappeared; database failures remain real errors.
fn resolve(db: &Database, pick: &ComparePick) -> AppResult<Option<(String, FileIdentity)>> {
    let Some(media) = db.get_media(pick.media_id())? else {
        return Ok(None);
    };
    if media.kind != "image" {
        return Ok(None);
    }
    let path = match pick {
        ComparePick::Original { .. } => media.path,
        ComparePick::Output { media_id, job_id } => {
            match db.media_result_output(media_id, job_id) {
                Ok(path) => path,
                Err(AppError::Validation(_)) => return Ok(None),
                Err(error) => return Err(error),
            }
        }
    };
    Ok(file_identity(&path).map(|file| (path, file)))
}

pub fn plan(
    db: &Database,
    compare: &mut CompareState,
    picks: &[ComparePick],
) -> AppResult<Vec<Value>> {
    if picks.len() > 4 {
        return Err(AppError::Validation(
            "Choose at most four images to compare".into(),
        ));
    }
    let mut slots = Vec::with_capacity(picks.len());
    for pick in picks {
        let mut resolved = resolve(db, pick)?;
        // A selection already in Compare can disappear while the user is
        // reordering picks. Keep it visible as missing, rather than failing the whole update.
        if resolved.is_none() && !compare.slots.iter().any(|slot| &slot.pick == pick) {
            return Err(AppError::Validation(
                "Choose an available image or completed result belonging to that image".into(),
            ));
        }
        if let Some(previous) = compare.slots.iter().find(|slot| &slot.pick == pick) {
            if previous.status == CompareStatus::Missing
                || !resolved.as_ref().is_some_and(|(path, file)| {
                    path == &previous.path && Some(file) == previous.file.as_ref()
                })
            {
                resolved = None;
            }
        }
        let (path, file) = match resolved {
            Some((path, file)) => (path, Some(file)),
            None => (String::new(), None),
        };
        // A decoded, unchanged selection keeps its preview when picks are
        // reordered, so a swap does not decode and flash it again.
        let kept = compare.slots.iter().find(|slot| {
            &slot.pick == pick && slot.status == CompareStatus::Ready && file.is_some()
        });
        slots.push(CompareSlot {
            pick: pick.clone(),
            status: match (&file, kept) {
                (None, _) => CompareStatus::Missing,
                (Some(_), Some(_)) => CompareStatus::Ready,
                (Some(_), None) => CompareStatus::Loading,
            },
            path,
            file,
            preview_data_url: kept.map(|k| k.preview_data_url.clone()).unwrap_or_default(),
            error: String::new(),
            width: kept.map_or(0, |k| k.width),
            height: kept.map_or(0, |k| k.height),
            preview_width: kept.map_or(0, |k| k.preview_width),
            preview_height: kept.map_or(0, |k| k.preview_height),
            region: None,
            requested_region: None,
        });
    }
    let generation = compare
        .generation
        .checked_add(1)
        .ok_or_else(|| AppError::Validation("Compare generation exhausted".into()))?;
    let requests = slots.iter().enumerate().filter(|(_, slot)| slot.status == CompareStatus::Loading).map(|(index, slot)| json!({"type":"preview_request", "data": {"image_path":slot.path, "max_dimension":RESULT_PREVIEW_MAX_DIMENSION, "comparison":true, "slot":format!("slot{}", index + 1), "generation":generation, "target_id":slot.pick.target_id()}})).collect();
    *compare = CompareState {
        generation,
        slots,
        region_request_id: 0,
    };
    Ok(requests)
}

pub fn refresh(db: &Database, compare: &mut CompareState) -> AppResult<()> {
    for slot in &mut compare.slots {
        if slot.status != CompareStatus::Missing
            && !resolve(db, &slot.pick)?
                .as_ref()
                .is_some_and(|(path, file)| path == &slot.path && Some(file) == slot.file.as_ref())
        {
            slot.status = CompareStatus::Missing;
            slot.preview_data_url.clear();
            slot.region = None;
            slot.requested_region = None;
            slot.error.clear();
            slot.width = 0;
            slot.height = 0;
            slot.preview_width = 0;
            slot.preview_height = 0;
        }
    }
    Ok(())
}

/// Why an engine cannot prepare Compare pictures, or None when it can. An engine from
/// before Compare answers a picture request without the slot, generation and target
/// the host matches it by, so every box would wait for an answer that is dropped.
/// Region crops arrived together with the slots, so that feature marks an engine that
/// knows them.
pub fn engine_shortfall(features: &[String]) -> Option<&'static str> {
    (!features.iter().any(|feature| feature == "compare_regions")).then_some(
        "The inference engine is older than this app and cannot prepare Compare pictures. Update or reinstall LocalSR.",
    )
}

fn parse_slot_index(data: &Value) -> Option<usize> {
    match data["slot"].as_str() {
        Some("slot1") => Some(0),
        Some("slot2") => Some(1),
        Some("slot3") => Some(2),
        Some("slot4") => Some(3),
        _ => None,
    }
}

pub fn apply(
    db: &Database,
    compare: &mut CompareState,
    data: &Value,
    failed: bool,
) -> AppResult<()> {
    let Some(index) = parse_slot_index(data) else {
        return Ok(());
    };
    if data["generation"].as_u64() != Some(compare.generation) || compare.generation == 0 {
        return Ok(());
    }
    refresh(db, compare)?;
    let Some(slot) = compare.slots.get_mut(index) else {
        return Ok(());
    };
    if slot.status != CompareStatus::Loading
        || data["target_id"].as_str() != Some(slot.pick.target_id())
        || data["image_path"].as_str() != Some(slot.path.as_str())
    {
        return Ok(());
    }
    if failed {
        slot.status = CompareStatus::Error;
        slot.error = data["error_message"]
            .as_str()
            .filter(|s| !s.is_empty())
            .unwrap_or("The preview could not be decoded.")
            .into();
    } else if let Some(encoded) = data["jpeg_base64"].as_str().filter(|s| !s.is_empty()) {
        slot.status = CompareStatus::Ready;
        slot.preview_data_url = format!("data:image/jpeg;base64,{encoded}");
        let dimension = |name: &str| {
            data[name]
                .as_u64()
                .and_then(|n| u32::try_from(n).ok())
                .unwrap_or_default()
        };
        slot.width = dimension("width");
        slot.height = dimension("height");
        slot.preview_width = dimension("preview_width");
        slot.preview_height = dimension("preview_height");
    } else {
        // An answer without a picture is no answer: the slot must not wait for another.
        slot.status = CompareStatus::Error;
        slot.error = "The engine returned no preview.".into();
    }
    Ok(())
}

/// Replace the desired regions atomically. Only selected, unchanged files can be read.
pub fn plan_regions(
    db: &Database,
    compare: &mut CompareState,
    regions: &[Option<Region>],
) -> AppResult<Vec<Value>> {
    if regions.len() > compare.slots.len() {
        return Err(AppError::Validation("Invalid Compare regions".into()));
    }
    refresh(db, compare)?;
    for (index, region) in regions.iter().enumerate() {
        if let Some(r) = region {
            let slot = &compare.slots[index];
            if r.width == 0
                || r.height == 0
                || r.target_width == 0
                || r.target_height == 0
                || r.target_width > 2048
                || r.target_height > 2048
                || r.x.checked_add(r.width).is_none_or(|v| v > slot.width)
                || r.y.checked_add(r.height).is_none_or(|v| v > slot.height)
            {
                return Err(AppError::Validation("Invalid Compare region".into()));
            }
        }
    }
    compare.region_request_id = compare
        .region_request_id
        .checked_add(1)
        .ok_or_else(|| AppError::Validation("Compare request exhausted".into()))?;
    let mut requests = Vec::new();
    for (index, slot) in compare.slots.iter_mut().enumerate() {
        slot.region = None;
        slot.requested_region = regions
            .get(index)
            .copied()
            .flatten()
            .filter(|_| slot.status == CompareStatus::Ready);
        if let Some(r) = slot.requested_region {
            requests.push(json!({"type":"region_request", "data": {"image_path":slot.path, "slot":format!("slot{}", index + 1), "generation":compare.generation, "target_id":slot.pick.target_id(), "request_id":compare.region_request_id, "x":r.x, "y":r.y, "width":r.width, "height":r.height, "target_width":r.target_width, "target_height":r.target_height}}));
        }
    }
    Ok(requests)
}

pub fn apply_region(
    db: &Database,
    compare: &mut CompareState,
    data: &Value,
    failed: bool,
) -> AppResult<()> {
    let Some(index) = parse_slot_index(data) else {
        return Ok(());
    };
    if data["generation"].as_u64() != Some(compare.generation)
        || data["request_id"].as_u64() != Some(compare.region_request_id)
    {
        return Ok(());
    }
    refresh(db, compare)?;
    let Some(slot) = compare.slots.get_mut(index) else {
        return Ok(());
    };
    if slot.status != CompareStatus::Ready
        || data["target_id"].as_str() != Some(slot.pick.target_id())
        || data["image_path"].as_str() != Some(slot.path.as_str())
    {
        return Ok(());
    }
    if let Some(bounds) = slot.requested_region.take() {
        if !failed {
            if let Some(encoded) = data["png_base64"].as_str().filter(|s| !s.is_empty()) {
                slot.region = Some(RegionResult {
                    bounds,
                    data_url: format!("data:image/png;base64,{encoded}"),
                });
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::database::Database;
    use serde_json::json;

    #[test]
    fn requests_four_owned_images_in_order_and_rejects_unowned_or_excess_picks() {
        let dir = tempfile::tempdir().unwrap();
        let db = Database::open(&dir.path().join("queue.db")).unwrap();
        let original = dir.path().join("original.png");
        let output = dir.path().join("output.png");
        std::fs::write(&original, "original").unwrap();
        std::fs::write(&output, "output").unwrap();
        db.insert_media("a", original.to_str().unwrap(), "a", "image")
            .unwrap();
        let other = dir.path().join("other.png");
        std::fs::write(&other, "other").unwrap();
        db.insert_media("b", other.to_str().unwrap(), "b", "image")
            .unwrap();
        db.insert_job("job", "a", "{}").unwrap();
        db.finish_job("job", "completed", output.to_str().unwrap(), "")
            .unwrap();
        let picks = vec![
            ComparePick::Original {
                media_id: "a".into(),
            },
            ComparePick::Output {
                media_id: "a".into(),
                job_id: "job".into(),
            },
            ComparePick::Original {
                media_id: "b".into(),
            },
            ComparePick::Output {
                media_id: "a".into(),
                job_id: "job".into(),
            },
        ];
        let mut compare = CompareState::default();
        let requests = plan(&db, &mut compare, &picks).unwrap();
        assert_eq!(requests.len(), 4);
        for (index, request) in requests.iter().enumerate() {
            assert_eq!(request["data"]["slot"], format!("slot{}", index + 1));
            assert_eq!(request["data"]["generation"], compare.generation);
            assert_eq!(compare.slots[index].status, CompareStatus::Loading);
        }
        let before = compare.clone();
        assert!(plan(
            &db,
            &mut compare,
            &[ComparePick::Output {
                media_id: "b".into(),
                job_id: "job".into()
            }]
        )
        .is_err());
        assert!(plan(&db, &mut compare, &vec![picks[0].clone(); 5]).is_err());
        assert_eq!(compare, before);
    }

    #[test]
    fn matches_all_identity_fields_and_invalidates_removed_files_and_jobs() {
        let dir = tempfile::tempdir().unwrap();
        let db = Database::open(&dir.path().join("queue.db")).unwrap();
        let original = dir.path().join("original.png");
        let output = dir.path().join("output.png");
        std::fs::write(&original, "original").unwrap();
        std::fs::write(&output, "output").unwrap();
        db.insert_media("a", original.to_str().unwrap(), "a", "image")
            .unwrap();
        db.insert_job("job", "a", "{}").unwrap();
        db.finish_job("job", "completed", output.to_str().unwrap(), "")
            .unwrap();
        let mut compare = CompareState::default();
        let picks = vec![
            ComparePick::Original {
                media_id: "a".into(),
            },
            ComparePick::Output {
                media_id: "a".into(),
                job_id: "job".into(),
            },
        ];
        let requests = plan(&db, &mut compare, &picks).unwrap();
        let mut answer = requests[1]["data"].clone();
        answer["jpeg_base64"] = json!("pixels");
        answer["width"] = json!(1200);
        answer["height"] = json!(800);
        answer["preview_width"] = json!(600);
        answer["preview_height"] = json!(400);
        for (key, value) in [
            ("slot", json!("slot1")),
            ("generation", json!(99)),
            ("target_id", json!("other")),
            ("image_path", json!("/other")),
        ] {
            let mut stale = answer.clone();
            stale[key] = value;
            apply(&db, &mut compare, &stale, false).unwrap();
            assert_eq!(compare.slots[1].status, CompareStatus::Loading);
        }
        apply(&db, &mut compare, &answer, false).unwrap();
        assert_eq!(compare.slots[1].status, CompareStatus::Ready);
        assert_eq!(
            compare.slots[1].preview_data_url,
            "data:image/jpeg;base64,pixels"
        );
        assert_eq!(
            (compare.slots[1].width, compare.slots[1].preview_width),
            (1200, 600)
        );
        std::fs::remove_file(&output).unwrap();
        refresh(&db, &mut compare).unwrap();
        assert_eq!(compare.slots[1].status, CompareStatus::Missing);
        assert!(compare.slots[1].preview_data_url.is_empty());
        apply(&db, &mut compare, &answer, false).unwrap();
        assert_eq!(compare.slots[1].status, CompareStatus::Missing);
        db.remove_media_guarded("a", &[]).unwrap();
        refresh(&db, &mut compare).unwrap();
        assert_eq!(compare.slots[0].status, CompareStatus::Missing);
    }

    #[test]
    fn failed_preview_exposes_reason_and_a_new_generation_rejects_old_answers() {
        let dir = tempfile::tempdir().unwrap();
        let db = Database::open(&dir.path().join("queue.db")).unwrap();
        let path = dir.path().join("image.png");
        std::fs::write(&path, "image").unwrap();
        db.insert_media("a", path.to_str().unwrap(), "a", "image")
            .unwrap();
        let mut compare = CompareState::default();
        let picks = [ComparePick::Original {
            media_id: "a".into(),
        }];
        let first = plan(&db, &mut compare, &picks).unwrap();
        let mut failure = first[0]["data"].clone();
        failure["error_message"] = json!("bad image");
        apply(&db, &mut compare, &failure, true).unwrap();
        assert_eq!(compare.slots[0].status, CompareStatus::Error);
        assert_eq!(compare.slots[0].error, "bad image");
        plan(&db, &mut compare, &picks).unwrap();
        apply(&db, &mut compare, &failure, true).unwrap();
        assert_eq!(compare.slots[0].status, CompareStatus::Loading);
    }

    #[test]
    fn a_replaced_file_at_the_same_path_cannot_accept_an_old_answer() {
        let dir = tempfile::tempdir().unwrap();
        let db = Database::open(&dir.path().join("queue.db")).unwrap();
        let path = dir.path().join("image.png");
        std::fs::write(&path, "first").unwrap();
        db.insert_media("a", path.to_str().unwrap(), "a", "image")
            .unwrap();
        let mut compare = CompareState::default();
        let requests = plan(
            &db,
            &mut compare,
            &[ComparePick::Original {
                media_id: "a".into(),
            }],
        )
        .unwrap();
        std::fs::remove_file(&path).unwrap();
        std::fs::write(&path, "a different file").unwrap();
        let mut answer = requests[0]["data"].clone();
        answer["jpeg_base64"] = json!("old pixels");
        apply(&db, &mut compare, &answer, false).unwrap();
        assert_eq!(compare.slots[0].status, CompareStatus::Missing);
        assert!(compare.slots[0].preview_data_url.is_empty());
        assert!(plan(
            &db,
            &mut compare,
            &[ComparePick::Original {
                media_id: "a".into()
            }]
        )
        .unwrap()
        .is_empty());
        assert_eq!(compare.slots[0].status, CompareStatus::Missing);
    }
    #[test]
    fn regions_reject_stale_identity_and_fall_back_on_failure() {
        let dir = tempfile::tempdir().unwrap();
        let db = Database::open(&dir.path().join("queue.db")).unwrap();
        let path = dir.path().join("image.png");
        std::fs::write(&path, "image").unwrap();
        db.insert_media("a", path.to_str().unwrap(), "a", "image")
            .unwrap();
        let mut compare = CompareState::default();
        let preview = plan(
            &db,
            &mut compare,
            &[ComparePick::Original {
                media_id: "a".into(),
            }],
        )
        .unwrap();
        let mut answer = preview[0]["data"].clone();
        answer["jpeg_base64"] = json!("preview");
        answer["width"] = json!(100);
        answer["height"] = json!(100);
        apply(&db, &mut compare, &answer, false).unwrap();
        let region = Region {
            x: 1,
            y: 2,
            width: 10,
            height: 20,
            target_width: 20,
            target_height: 40,
        };
        let requests = plan_regions(&db, &mut compare, &[Some(region)]).unwrap();
        let mut response = requests[0]["data"].clone();
        response["png_base64"] = json!("crop");
        let mut stale = response.clone();
        stale["request_id"] = json!(999);
        apply_region(&db, &mut compare, &stale, false).unwrap();
        assert!(compare.slots[0].region.is_none());
        apply_region(&db, &mut compare, &response, false).unwrap();
        assert_eq!(
            compare.slots[0].region.as_ref().unwrap().data_url,
            "data:image/png;base64,crop"
        );
        let newer = plan_regions(&db, &mut compare, &[Some(region)]).unwrap();
        apply_region(&db, &mut compare, &response, false).unwrap();
        assert!(compare.slots[0].region.is_none());
        apply_region(&db, &mut compare, &newer[0]["data"], true).unwrap();
        assert!(compare.slots[0].region.is_none());
        assert_eq!(
            compare.slots[0].preview_data_url,
            "data:image/jpeg;base64,preview"
        );
    }

    fn one_image_db(dir: &std::path::Path, names: &[&str]) -> (Database, Vec<std::path::PathBuf>) {
        let db = Database::open(&dir.join("queue.db")).unwrap();
        let mut paths = Vec::new();
        for (index, name) in names.iter().enumerate() {
            let path = dir.join(name);
            std::fs::write(&path, name).unwrap();
            db.insert_media(&format!("m{index}"), path.to_str().unwrap(), name, "image")
                .unwrap();
            paths.push(path);
        }
        (db, paths)
    }

    fn original(id: &str) -> ComparePick {
        ComparePick::Original {
            media_id: id.into(),
        }
    }

    fn decode(db: &Database, compare: &mut CompareState, request: &Value) {
        let mut answer = request["data"].clone();
        answer["jpeg_base64"] = json!("pixels");
        answer["width"] = json!(100);
        answer["height"] = json!(100);
        answer["preview_width"] = json!(50);
        answer["preview_height"] = json!(50);
        apply(db, compare, &answer, false).unwrap();
    }

    #[test]
    fn an_engine_that_does_not_know_compare_is_named_instead_of_waited_for() {
        let old: Vec<String> = ["image", "progressive_preview", "face_aware"]
            .map(String::from)
            .to_vec();
        let current: Vec<String> = ["image", "compare_regions"].map(String::from).to_vec();
        assert!(engine_shortfall(&old).is_some_and(|reason| reason.contains("older")));
        assert_eq!(engine_shortfall(&current), None);
    }

    #[test]
    fn an_answer_without_a_picture_ends_the_wait_with_an_error() {
        let dir = tempfile::tempdir().unwrap();
        let (db, _) = one_image_db(dir.path(), &["a.png"]);
        let mut compare = CompareState::default();
        let requests = plan(&db, &mut compare, &[original("m0")]).unwrap();
        let mut answer = requests[0]["data"].clone();
        answer["jpeg_base64"] = json!("");
        apply(&db, &mut compare, &answer, false).unwrap();
        assert_eq!(compare.slots[0].status, CompareStatus::Error);
        assert!(!compare.slots[0].error.is_empty());
    }

    #[test]
    fn swapping_decoded_picks_keeps_their_previews_and_asks_for_nothing() {
        let dir = tempfile::tempdir().unwrap();
        let (db, _) = one_image_db(dir.path(), &["a.png", "b.png"]);
        let mut compare = CompareState::default();
        let picks = [original("m0"), original("m1")];
        let requests = plan(&db, &mut compare, &picks).unwrap();
        for request in &requests {
            decode(&db, &mut compare, request);
        }
        let swapped = [original("m1"), original("m0")];
        assert!(plan(&db, &mut compare, &swapped).unwrap().is_empty());
        assert_eq!(compare.slots[0].pick, swapped[0]);
        for slot in &compare.slots {
            assert_eq!(slot.status, CompareStatus::Ready);
            assert_eq!(slot.preview_data_url, "data:image/jpeg;base64,pixels");
            assert_eq!(
                (slot.width, slot.preview_width, slot.region.is_none()),
                (100, 50, true)
            );
        }
        // An answer of the earlier generation cannot reach the reordered slots.
        let mut late = requests[0]["data"].clone();
        late["jpeg_base64"] = json!("late");
        apply(&db, &mut compare, &late, false).unwrap();
        assert_eq!(
            compare.slots[0].preview_data_url,
            "data:image/jpeg;base64,pixels"
        );
    }

    #[test]
    fn a_pick_that_was_still_loading_is_requested_again_after_a_swap() {
        let dir = tempfile::tempdir().unwrap();
        let (db, _) = one_image_db(dir.path(), &["a.png", "b.png"]);
        let mut compare = CompareState::default();
        let requests = plan(&db, &mut compare, &[original("m0"), original("m1")]).unwrap();
        decode(&db, &mut compare, &requests[0]);
        let again = plan(&db, &mut compare, &[original("m1"), original("m0")]).unwrap();
        assert_eq!(again.len(), 1);
        assert_eq!(again[0]["data"]["slot"], "slot1");
        assert_eq!(again[0]["data"]["target_id"], "m1");
    }

    #[test]
    fn a_changed_pick_recovers_by_choosing_it_again() {
        let dir = tempfile::tempdir().unwrap();
        let (db, paths) = one_image_db(dir.path(), &["a.png"]);
        let mut compare = CompareState::default();
        let picks = [original("m0")];
        plan(&db, &mut compare, &picks).unwrap();
        std::fs::remove_file(&paths[0]).unwrap();
        std::fs::write(&paths[0], "a different file").unwrap();
        refresh(&db, &mut compare).unwrap();
        assert_eq!(compare.slots[0].status, CompareStatus::Missing);
        // The slot stays missing while it is picked; removing and picking it
        // again reads the file as it is now.
        plan(&db, &mut compare, &[]).unwrap();
        assert_eq!(plan(&db, &mut compare, &picks).unwrap().len(), 1);
        assert_eq!(compare.slots[0].status, CompareStatus::Loading);
    }

    #[test]
    fn regions_for_slots_that_are_not_ready_are_null_and_do_not_reject_the_batch() {
        let dir = tempfile::tempdir().unwrap();
        let (db, _) = one_image_db(dir.path(), &["a.png", "b.png"]);
        let mut compare = CompareState::default();
        let requests = plan(&db, &mut compare, &[original("m0"), original("m1")]).unwrap();
        decode(&db, &mut compare, &requests[0]);
        let region = Region {
            x: 0,
            y: 0,
            width: 10,
            height: 10,
            target_width: 20,
            target_height: 20,
        };
        let sent = plan_regions(&db, &mut compare, &[Some(region), None]).unwrap();
        assert_eq!(sent.len(), 1);
        assert_eq!(sent[0]["data"]["slot"], "slot1");
    }
}
