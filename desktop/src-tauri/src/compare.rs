//! Host-owned selections and bounded preview requests for the Compare tab.
use std::{fs, path::Path, time::SystemTime};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::{
    database::Database,
    error::{AppError, AppResult},
    types::{ComparisonPair, ComparisonSlot, PreviewOriginalRegion},
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
pub struct FileIdentity {
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
    pub media_kind: String,
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

// Resolve only paths owned by media in the database. Validation errors
// mean the selection disappeared; database failures remain real errors.
fn resolve(db: &Database, pick: &ComparePick) -> AppResult<Option<(String, FileIdentity)>> {
    let Some(media) = db.get_media(pick.media_id())? else {
        return Ok(None);
    };
    if media.kind != "image" && media.kind != "video" {
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
            "Choose at most four items to compare".into(),
        ));
    }
    let kinds = picks
        .iter()
        .map(|pick| {
            db.get_media(pick.media_id()).map(|media| {
                media.map(|m| m.kind).or_else(|| {
                    compare
                        .slots
                        .iter()
                        .find(|slot| &slot.pick == pick)
                        .map(|slot| slot.media_kind.clone())
                })
            })
        })
        .collect::<AppResult<Vec<_>>>()?;
    if kinds
        .iter()
        .flatten()
        .any(|kind| Some(kind) != kinds.iter().flatten().next())
    {
        return Err(AppError::Validation(
            "Compare images with images or videos with videos".into(),
        ));
    }
    let mut slots = Vec::with_capacity(picks.len());
    for (pick, kind) in picks.iter().zip(&kinds) {
        let mut resolved = resolve(db, pick)?;
        // A selection already in Compare can disappear while the user is
        // reordering picks. Keep it visible as missing, rather than failing the whole update.
        if resolved.is_none() && !compare.slots.iter().any(|slot| &slot.pick == pick) {
            return Err(AppError::Validation(
                "Choose available media or a completed result belonging to that media".into(),
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
            media_kind: kind.clone().unwrap_or_default(),
            status: match (&file, kept) {
                (None, _) => CompareStatus::Missing,
                (Some(_), Some(_)) => CompareStatus::Ready,
                (Some(_), None) if kind.as_deref() == Some("video") => CompareStatus::Ready,
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

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CompareVideoInput {
    pub path: String,
    pub is_result: bool,
    pub kept_frames: Option<u64>,
}

/// Host-resolved video inputs for the current Compare generation, aligned 1:1 with `compare.slots`.
pub fn video_inputs(
    db: &Database,
    compare: &mut CompareState,
    generation: u64,
) -> AppResult<Vec<Option<CompareVideoInput>>> {
    refresh(db, compare)?;
    if compare.generation != generation
        || compare.slots.is_empty()
        || compare.slots.iter().any(|slot| slot.media_kind != "video")
    {
        return Err(AppError::Validation(
            "Choose an available video in Compare".into(),
        ));
    }
    let mut inputs = Vec::with_capacity(compare.slots.len());
    for slot in &compare.slots {
        if slot.status != CompareStatus::Ready {
            inputs.push(None);
            continue;
        }
        let (is_result, kept_frames) = match &slot.pick {
            ComparePick::Original { .. } => (false, None),
            ComparePick::Output { media_id, job_id } => {
                (true, db.media_result_kept_frames(media_id, job_id)?)
            }
        };
        inputs.push(Some(CompareVideoInput {
            path: slot.path.clone(),
            is_result,
            kept_frames,
        }));
    }
    Ok(inputs)
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
    preview_root: &Path,
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
        match crate::preview_cache::save_jpeg_base64(preview_root, encoded) {
            Ok(url) => {
                slot.status = CompareStatus::Ready;
                slot.preview_data_url = url;
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
            }
            Err(_) => {
                slot.status = CompareStatus::Error;
                slot.error = "The preview could not be decoded.".into();
            }
        }
    } else {
        // An answer without a picture is no answer: the slot must not wait for another.
        slot.status = CompareStatus::Error;
        slot.error = "The engine returned no preview.".into();
    }
    Ok(())
}

#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
pub struct PairRegions {
    pub original: Option<Region>,
    pub showing: Option<Region>,
    pub against: Option<Region>,
}

fn validate_region(r: Region, width: u32, height: u32) -> AppResult<()> {
    if r.width == 0
        || r.height == 0
        || r.target_width == 0
        || r.target_height == 0
        || r.target_width > 2048
        || r.target_height > 2048
        || r.x.checked_add(r.width).is_none_or(|v| v > width)
        || r.y.checked_add(r.height).is_none_or(|v| v > height)
    {
        return Err(AppError::Validation("Invalid Compare region".into()));
    }
    Ok(())
}

fn region_request_message(
    path: &str,
    slot: &str,
    generation: u64,
    target_id: &str,
    request_id: u64,
    r: Region,
) -> Value {
    json!({
        "type": "region_request",
        "data": {
            "image_path": path,
            "slot": slot,
            "generation": generation,
            "target_id": target_id,
            "request_id": request_id,
            "x": r.x,
            "y": r.y,
            "width": r.width,
            "height": r.height,
            "target_width": r.target_width,
            "target_height": r.target_height,
        }
    })
}

fn apply_region_answer(
    requested: &mut Option<Region>,
    result: &mut Option<RegionResult>,
    data: &Value,
    failed: bool,
) {
    if let Some(bounds) = requested.take() {
        if !failed {
            if let Some(encoded) = data["png_base64"].as_str().filter(|s| !s.is_empty()) {
                *result = Some(RegionResult {
                    bounds,
                    data_url: format!("data:image/png;base64,{encoded}"),
                });
            }
        }
    }
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
            if slot.media_kind != "image" {
                return Err(AppError::Validation("Invalid Compare region".into()));
            }
            validate_region(*r, slot.width, slot.height)?;
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
            .filter(|_| slot.status == CompareStatus::Ready && slot.media_kind == "image");
        if let Some(r) = slot.requested_region {
            requests.push(region_request_message(
                &slot.path,
                &format!("slot{}", index + 1),
                compare.generation,
                slot.pick.target_id(),
                compare.region_request_id,
                r,
            ));
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
    apply_region_answer(&mut slot.requested_region, &mut slot.region, data, failed);
    Ok(())
}

/// The file a side of the comparison pair shows: `"original"` has none here (the caller
/// uses the media's own), `<job_id>#<index>` is a step file of the last lone job, any other
/// id a completed result of this media. One place for the three lookups of a pair's side.
pub fn resolve_pair_target(
    db: &Database,
    last_steps: Option<&crate::types::LastSteps>,
    media_id: &str,
    target: &str,
) -> AppResult<String> {
    if crate::step_results::is_step_target(target) {
        let last = last_steps.filter(|last| last.media_id == media_id);
        return crate::step_results::step_file(last, target);
    }
    db.media_result_output(media_id, target)
}

fn resolve_pair_slot(
    db: &Database,
    last_steps: Option<&crate::types::LastSteps>,
    media: &crate::types::MediaItem,
    slot: &ComparisonSlot,
) -> AppResult<(String, FileIdentity, u32, u32)> {
    if slot.target_id.is_empty() {
        return Err(AppError::Validation("Invalid Preview region".into()));
    }
    let (path, width, height) = if slot.target_id == "original" {
        (media.path.clone(), media.width, media.height)
    } else {
        let path = resolve_pair_target(db, last_steps, &media.id, &slot.target_id)?;
        if path != slot.output_path
            || slot.preview_data_url.is_empty()
            || slot.width == 0
            || slot.height == 0
        {
            return Err(AppError::Validation("Invalid Preview region".into()));
        }
        (path, slot.width, slot.height)
    };
    let identity = file_identity(&path)
        .ok_or_else(|| AppError::Validation("This result's file is no longer available".into()))?;
    Ok((path, identity, width, height))
}

/// Replace the desired regions for the main preview atomically. Only owned, unchanged files are read.
pub fn plan_pair_regions(
    db: &Database,
    last_steps: Option<&crate::types::LastSteps>,
    pair: &mut ComparisonPair,
    original: &mut PreviewOriginalRegion,
    media_id: &str,
    pair_generation: u64,
    regions: &PairRegions,
) -> AppResult<Vec<Value>> {
    let Some(media) = db.get_media(media_id)? else {
        if regions.original.is_some() || regions.showing.is_some() || regions.against.is_some() {
            return Err(AppError::Validation(
                "the selected image is no longer queued".into(),
            ));
        }
        clear_pair_regions(pair, original);
        return Ok(Vec::new());
    };
    if media.kind != "image" {
        return Ok(Vec::new());
    }
    if pair_generation > 0 && (pair.media_id != media_id || pair.generation != pair_generation) {
        return Ok(Vec::new());
    }
    if pair_generation == 0 && (regions.showing.is_some() || regions.against.is_some()) {
        return Ok(Vec::new());
    }

    let orig_identity = if let Some(r) = regions.original {
        validate_region(r, media.width, media.height)?;
        Some(file_identity(&media.path).ok_or_else(|| {
            AppError::Validation("The selected image is no longer available".into())
        })?)
    } else {
        None
    };

    let showing_resolved = if let Some(r) = regions.showing {
        let (path, id, width, height) = resolve_pair_slot(db, last_steps, &media, &pair.showing)?;
        validate_region(r, width, height)?;
        Some((path, id))
    } else {
        None
    };

    let against_resolved = if let Some(r) = regions.against {
        let (path, id, width, height) = resolve_pair_slot(db, last_steps, &media, &pair.against)?;
        validate_region(r, width, height)?;
        Some((path, id))
    } else {
        None
    };

    let mut requests = Vec::new();
    original.region = None;
    original.requested_region = None;
    if let (Some(r), Some(identity)) = (regions.original, orig_identity) {
        if original.media_id != media_id
            || original.path != media.path
            || original.file.as_ref() != Some(&identity)
        {
            original.media_id = media_id.to_owned();
            original.path = media.path.clone();
            original.file = Some(identity);
            original.generation = original
                .generation
                .checked_add(1)
                .ok_or_else(|| AppError::Validation("Preview request exhausted".into()))?;
        }
        original.request_id = original
            .request_id
            .checked_add(1)
            .ok_or_else(|| AppError::Validation("Preview request exhausted".into()))?;
        original.requested_region = Some(r);
        requests.push(region_request_message(
            &original.path,
            "original",
            original.generation,
            media_id,
            original.request_id,
            r,
        ));
    }

    if pair_generation > 0 && pair.media_id == media_id && pair.generation == pair_generation {
        pair.region_request_id = pair
            .region_request_id
            .checked_add(1)
            .ok_or_else(|| AppError::Validation("Preview request exhausted".into()))?;
        for (slot_name, slot, region_opt, resolved) in [
            (
                "showing",
                &mut pair.showing,
                regions.showing,
                showing_resolved,
            ),
            (
                "against",
                &mut pair.against,
                regions.against,
                against_resolved,
            ),
        ] {
            slot.region = None;
            slot.requested_region = None;
            if let (Some(r), Some((path, id))) = (region_opt, resolved) {
                slot.file = Some(id);
                slot.requested_region = Some(r);
                requests.push(region_request_message(
                    &path,
                    slot_name,
                    pair.generation,
                    &slot.target_id,
                    pair.region_request_id,
                    r,
                ));
            }
        }
    }
    Ok(requests)
}

pub fn apply_pair_region(
    db: &Database,
    last_steps: Option<&crate::types::LastSteps>,
    pair: &mut ComparisonPair,
    original: &mut PreviewOriginalRegion,
    data: &Value,
    failed: bool,
) -> AppResult<()> {
    match data["slot"].as_str() {
        Some("original") => {
            if original.generation == 0
                || original.request_id == 0
                || data["generation"].as_u64() != Some(original.generation)
                || data["request_id"].as_u64() != Some(original.request_id)
            {
                return Ok(());
            }
            let valid = db.get_media(&original.media_id)?.is_some_and(|media| {
                media.kind == "image"
                    && media.path == original.path
                    && file_identity(&original.path).as_ref() == original.file.as_ref()
            });
            if !valid {
                original.region = None;
                original.requested_region = None;
                return Ok(());
            }
            if data["target_id"].as_str() != Some(original.media_id.as_str())
                || data["image_path"].as_str() != Some(original.path.as_str())
            {
                return Ok(());
            }
            apply_region_answer(
                &mut original.requested_region,
                &mut original.region,
                data,
                failed,
            );
        }
        Some(slot_name @ ("showing" | "against")) => {
            if pair.generation == 0
                || pair.region_request_id == 0
                || data["generation"].as_u64() != Some(pair.generation)
                || data["request_id"].as_u64() != Some(pair.region_request_id)
            {
                return Ok(());
            }
            let slot = if slot_name == "showing" {
                &mut pair.showing
            } else {
                &mut pair.against
            };
            let expected_path = if slot.target_id == "original" {
                db.get_media(&pair.media_id)?
                    .filter(|m| m.kind == "image")
                    .map(|m| m.path)
            } else if !slot.target_id.is_empty() {
                match resolve_pair_target(db, last_steps, &pair.media_id, &slot.target_id) {
                    Ok(path) if path == slot.output_path => Some(path),
                    Ok(_) | Err(AppError::Validation(_)) => None,
                    Err(error) => return Err(error),
                }
            } else {
                None
            };
            let Some(path) =
                expected_path.filter(|path| file_identity(path).as_ref() == slot.file.as_ref())
            else {
                slot.region = None;
                slot.requested_region = None;
                return Ok(());
            };
            if data["target_id"].as_str() != Some(slot.target_id.as_str())
                || data["image_path"].as_str() != Some(path.as_str())
            {
                return Ok(());
            }
            apply_region_answer(&mut slot.requested_region, &mut slot.region, data, failed);
        }
        _ => {}
    }
    Ok(())
}

pub fn clear_pair_regions(pair: &mut ComparisonPair, original: &mut PreviewOriginalRegion) {
    original.region = None;
    original.requested_region = None;
    original.request_id = original.request_id.saturating_add(1);
    pair.showing.region = None;
    pair.showing.requested_region = None;
    pair.against.region = None;
    pair.against.requested_region = None;
    pair.region_request_id = pair.region_request_id.saturating_add(1);
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::database::Database;
    use serde_json::json;

    #[test]
    fn video_picks_use_owned_results_and_the_shared_playback_descriptor() {
        let dir = tempfile::tempdir().unwrap();
        let dir_root = std::fs::canonicalize(dir.path()).unwrap();
        let db = Database::open(&dir_root.as_path().join("queue.db")).unwrap();
        let original = dir_root.as_path().join("source.mp4");
        let output = dir_root.as_path().join("result.mp4");
        std::fs::write(&original, "source").unwrap();
        std::fs::write(&output, "result").unwrap();
        db.insert_media("v", original.to_str().unwrap(), "v", "video")
            .unwrap();
        db.insert_media("i", original.to_str().unwrap(), "i", "image")
            .unwrap();
        db.insert_job("done", "v", "{}").unwrap();
        db.finish_job("done", "completed", output.to_str().unwrap(), "")
            .unwrap();
        let source = ComparePick::Original {
            media_id: "v".into(),
        };
        let result = ComparePick::Output {
            media_id: "v".into(),
            job_id: "done".into(),
        };
        let picks = vec![
            source.clone(),
            result.clone(),
            source.clone(),
            result.clone(),
        ];
        let mut compare = CompareState::default();
        assert!(plan(&db, &mut compare, &picks).unwrap().is_empty());
        assert_eq!(compare.slots.len(), 4);
        for (index, expected) in [&original, &output, &original, &output].iter().enumerate() {
            assert_eq!(compare.slots[index].media_kind, "video");
            assert_eq!(compare.slots[index].status, CompareStatus::Ready);
            let generation = compare.generation;
            let resolved = video_inputs(&db, &mut compare, generation).unwrap();
            let input = resolved[index].as_ref().unwrap();
            assert_eq!(input.is_result, index % 2 == 1);
            assert_eq!(input.path, expected.to_string_lossy());
            assert_eq!(input.kept_frames, None);
        }
        assert!(plan(&db, &mut compare, &vec![source.clone(); 5]).is_err());
        assert!(plan(
            &db,
            &mut compare,
            &[
                source,
                ComparePick::Original {
                    media_id: "i".into()
                }
            ]
        )
        .is_err());
        assert!(plan(
            &db,
            &mut CompareState::default(),
            &[ComparePick::Output {
                media_id: "v".into(),
                job_id: "unknown".into()
            }]
        )
        .is_err());
        assert!(plan(
            &db,
            &mut CompareState::default(),
            &[ComparePick::Output {
                media_id: "i".into(),
                job_id: "done".into()
            }]
        )
        .is_err());
        let generation = compare.generation;
        assert!(video_inputs(&db, &mut compare, generation + 1).is_err());
        std::fs::remove_file(output).unwrap();
        assert!(plan(&db, &mut CompareState::default(), &[result]).is_err());
        let after = video_inputs(&db, &mut compare, generation).unwrap();
        assert!(after[0].is_some());
        assert!(after[1].is_none());
    }

    #[test]
    fn four_distinct_completed_videos_are_accepted_but_a_fifth_is_refused() {
        let dir = tempfile::tempdir().unwrap();
        let dir_root = std::fs::canonicalize(dir.path()).unwrap();
        let db = Database::open(&dir_root.as_path().join("queue.db")).unwrap();
        let mut picks = Vec::new();
        for index in 0..5 {
            let id = format!("video-{index}");
            let job = format!("job-{index}");
            let source = dir_root.as_path().join(format!("source-{index}.mp4"));
            let output = dir_root.as_path().join(format!("result-{index}.mp4"));
            std::fs::write(&source, "source").unwrap();
            std::fs::write(&output, "result").unwrap();
            db.insert_media(&id, source.to_str().unwrap(), &id, "video")
                .unwrap();
            db.insert_job(&job, &id, "{}").unwrap();
            db.finish_job(&job, "completed", output.to_str().unwrap(), "")
                .unwrap();
            picks.push(ComparePick::Output {
                media_id: id,
                job_id: job,
            });
        }
        let mut compare = CompareState::default();
        assert!(plan(&db, &mut compare, &picks[..4]).unwrap().is_empty());
        assert_eq!(compare.slots.len(), 4);
        assert!(plan(&db, &mut compare, &picks).is_err());
        assert_eq!(compare.slots.len(), 4);
        assert!(plan(
            &db,
            &mut CompareState::default(),
            &[ComparePick::Original {
                media_id: "unknown".into()
            }]
        )
        .is_err());
    }

    #[cfg(unix)]
    #[test]
    fn video_result_replaced_by_a_link_is_refused() {
        let dir = tempfile::tempdir().unwrap();
        let db = Database::open(&dir.path().join("queue.db")).unwrap();
        let source = dir.path().join("source.mp4");
        let output = dir.path().join("result.mp4");
        std::fs::write(&source, "source").unwrap();
        std::os::unix::fs::symlink(&source, &output).unwrap();
        db.insert_media("v", source.to_str().unwrap(), "v", "video")
            .unwrap();
        db.insert_job("done", "v", "{}").unwrap();
        db.finish_job("done", "completed", output.to_str().unwrap(), "")
            .unwrap();
        assert!(plan(
            &db,
            &mut CompareState::default(),
            &[ComparePick::Output {
                media_id: "v".into(),
                job_id: "done".into()
            }]
        )
        .is_err());
    }

    #[test]
    fn requests_four_owned_images_in_order_and_rejects_unowned_or_excess_picks() {
        let dir = tempfile::tempdir().unwrap();
        let dir_root = std::fs::canonicalize(dir.path()).unwrap();
        let db = Database::open(&dir_root.as_path().join("queue.db")).unwrap();
        let original = dir_root.as_path().join("original.png");
        let output = dir_root.as_path().join("output.png");
        std::fs::write(&original, "original").unwrap();
        std::fs::write(&output, "output").unwrap();
        db.insert_media("a", original.to_str().unwrap(), "a", "image")
            .unwrap();
        let other = dir_root.as_path().join("other.png");
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
        use base64::Engine;
        let dir = tempfile::tempdir().unwrap();
        let preview_root = dir.path().join("previews");
        let dir_root = std::fs::canonicalize(dir.path()).unwrap();
        let db = Database::open(&dir_root.as_path().join("queue.db")).unwrap();
        let original = dir_root.as_path().join("original.png");
        let output = dir_root.as_path().join("output.png");
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
        answer["jpeg_base64"] = json!(base64::engine::general_purpose::STANDARD.encode(b"pixels"));
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
            apply(&db, &mut compare, &preview_root, &stale, false).unwrap();
            assert_eq!(compare.slots[1].status, CompareStatus::Loading);
        }
        apply(&db, &mut compare, &preview_root, &answer, false).unwrap();
        assert_eq!(compare.slots[1].status, CompareStatus::Ready);
        let saved_path = crate::preview_cache::confined_preview_path(
            &preview_root,
            &compare.slots[1].preview_data_url,
        )
        .expect("slot preview must be a confined preview file URL");
        assert_eq!(std::fs::read(saved_path).unwrap(), b"pixels");
        assert_eq!(
            (compare.slots[1].width, compare.slots[1].preview_width),
            (1200, 600)
        );
        std::fs::remove_file(&output).unwrap();
        refresh(&db, &mut compare).unwrap();
        assert_eq!(compare.slots[1].status, CompareStatus::Missing);
        assert!(compare.slots[1].preview_data_url.is_empty());
        apply(&db, &mut compare, &preview_root, &answer, false).unwrap();
        assert_eq!(compare.slots[1].status, CompareStatus::Missing);
        db.remove_media_guarded("a", &[]).unwrap();
        refresh(&db, &mut compare).unwrap();
        assert_eq!(compare.slots[0].status, CompareStatus::Missing);
    }

    #[test]
    fn failed_preview_exposes_reason_and_a_new_generation_rejects_old_answers() {
        let dir = tempfile::tempdir().unwrap();
        let preview_root = dir.path().join("previews");
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
        apply(&db, &mut compare, &preview_root, &failure, true).unwrap();
        assert_eq!(compare.slots[0].status, CompareStatus::Error);
        assert_eq!(compare.slots[0].error, "bad image");
        plan(&db, &mut compare, &picks).unwrap();
        apply(&db, &mut compare, &preview_root, &failure, true).unwrap();
        assert_eq!(compare.slots[0].status, CompareStatus::Loading);
    }

    #[test]
    fn a_replaced_file_at_the_same_path_cannot_accept_an_old_answer() {
        use base64::Engine;
        let dir = tempfile::tempdir().unwrap();
        let preview_root = dir.path().join("previews");
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
        answer["jpeg_base64"] =
            json!(base64::engine::general_purpose::STANDARD.encode(b"old pixels"));
        apply(&db, &mut compare, &preview_root, &answer, false).unwrap();
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
        use base64::Engine;
        let dir = tempfile::tempdir().unwrap();
        let preview_root = dir.path().join("previews");
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
        answer["jpeg_base64"] = json!(base64::engine::general_purpose::STANDARD.encode(b"preview"));
        answer["width"] = json!(100);
        answer["height"] = json!(100);
        apply(&db, &mut compare, &preview_root, &answer, false).unwrap();
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
        let saved_path = crate::preview_cache::confined_preview_path(
            &preview_root,
            &compare.slots[0].preview_data_url,
        )
        .expect("slot preview must be a confined preview file URL");
        assert_eq!(std::fs::read(saved_path).unwrap(), b"preview");
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

    fn decode(db: &Database, compare: &mut CompareState, preview_root: &Path, request: &Value) {
        use base64::Engine;
        let mut answer = request["data"].clone();
        answer["jpeg_base64"] = json!(base64::engine::general_purpose::STANDARD.encode(b"pixels"));
        answer["width"] = json!(100);
        answer["height"] = json!(100);
        answer["preview_width"] = json!(50);
        answer["preview_height"] = json!(50);
        apply(db, compare, preview_root, &answer, false).unwrap();
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
        let preview_root = dir.path().join("previews");
        let (db, _) = one_image_db(dir.path(), &["a.png"]);
        let mut compare = CompareState::default();
        let requests = plan(&db, &mut compare, &[original("m0")]).unwrap();
        let mut answer = requests[0]["data"].clone();
        answer["jpeg_base64"] = json!("");
        apply(&db, &mut compare, &preview_root, &answer, false).unwrap();
        assert_eq!(compare.slots[0].status, CompareStatus::Error);
        assert!(!compare.slots[0].error.is_empty());
    }

    #[test]
    fn swapping_decoded_picks_keeps_their_previews_and_asks_for_nothing() {
        use base64::Engine;
        let dir = tempfile::tempdir().unwrap();
        let preview_root = dir.path().join("previews");
        let (db, _) = one_image_db(dir.path(), &["a.png", "b.png"]);
        let mut compare = CompareState::default();
        let picks = [original("m0"), original("m1")];
        let requests = plan(&db, &mut compare, &picks).unwrap();
        for request in &requests {
            decode(&db, &mut compare, &preview_root, request);
        }
        let expected_url = compare.slots[0].preview_data_url.clone();
        let swapped = [original("m1"), original("m0")];
        assert!(plan(&db, &mut compare, &swapped).unwrap().is_empty());
        assert_eq!(compare.slots[0].pick, swapped[0]);
        for slot in &compare.slots {
            assert_eq!(slot.status, CompareStatus::Ready);
            assert_eq!(slot.preview_data_url, expected_url);
            assert_eq!(
                (slot.width, slot.preview_width, slot.region.is_none()),
                (100, 50, true)
            );
        }
        // An answer of the earlier generation cannot reach the reordered slots.
        let mut late = requests[0]["data"].clone();
        late["jpeg_base64"] = json!(base64::engine::general_purpose::STANDARD.encode(b"late"));
        apply(&db, &mut compare, &preview_root, &late, false).unwrap();
        assert_eq!(compare.slots[0].preview_data_url, expected_url);
    }

    #[test]
    fn a_pick_that_was_still_loading_is_requested_again_after_a_swap() {
        let dir = tempfile::tempdir().unwrap();
        let preview_root = dir.path().join("previews");
        let (db, _) = one_image_db(dir.path(), &["a.png", "b.png"]);
        let mut compare = CompareState::default();
        let requests = plan(&db, &mut compare, &[original("m0"), original("m1")]).unwrap();
        decode(&db, &mut compare, &preview_root, &requests[0]);
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
        let preview_root = dir.path().join("previews");
        let (db, _) = one_image_db(dir.path(), &["a.png", "b.png"]);
        let mut compare = CompareState::default();
        let requests = plan(&db, &mut compare, &[original("m0"), original("m1")]).unwrap();
        decode(&db, &mut compare, &preview_root, &requests[0]);
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

    #[test]
    fn pair_regions_plan_for_original_and_outputs_and_reject_stale_or_failed_answers() {
        let dir = tempfile::tempdir().unwrap();
        let dir_root = std::fs::canonicalize(dir.path()).unwrap();
        let db = Database::open(&dir_root.join("queue.db")).unwrap();
        let source = dir_root.join("source.png");
        let output_a = dir_root.join("output_a.png");
        let output_b = dir_root.join("output_b.png");
        std::fs::write(&source, "source").unwrap();
        std::fs::write(&output_a, "output_a").unwrap();
        std::fs::write(&output_b, "output_b").unwrap();
        db.insert_media("m1", source.to_str().unwrap(), "source.png", "image")
            .unwrap();
        db.update_media_probe(
            source.to_str().unwrap(),
            "image",
            3264,
            4896,
            1,
            0.0,
            0.0,
            "thumb",
            "",
            "",
            true,
            "",
        )
        .unwrap();
        db.insert_job("job-a", "m1", "{}").unwrap();
        db.finish_job("job-a", "completed", output_a.to_str().unwrap(), "")
            .unwrap();
        db.insert_job("job-b", "m1", "{}").unwrap();
        db.finish_job("job-b", "completed", output_b.to_str().unwrap(), "")
            .unwrap();

        let mut pair = crate::types::ComparisonPair {
            media_id: "m1".into(),
            generation: 2,
            showing: crate::types::ComparisonSlot {
                target_id: "job-b".into(),
                output_path: output_b.to_string_lossy().into_owned(),
                preview_data_url: "asset://b".into(),
                width: 13056,
                height: 19584,
                preview_width: 1365,
                preview_height: 2048,
                ..Default::default()
            },
            against: crate::types::ComparisonSlot {
                target_id: "job-a".into(),
                output_path: output_a.to_string_lossy().into_owned(),
                preview_data_url: "asset://a".into(),
                width: 6528,
                height: 9792,
                preview_width: 1365,
                preview_height: 2048,
                ..Default::default()
            },
            ..Default::default()
        };
        let mut original = crate::types::PreviewOriginalRegion::default();
        let orig_region = Region {
            x: 100,
            y: 200,
            width: 300,
            height: 400,
            target_width: 600,
            target_height: 800,
        };
        let show_region = Region {
            x: 400,
            y: 800,
            width: 1200,
            height: 1600,
            target_width: 600,
            target_height: 800,
        };
        let against_region = Region {
            x: 200,
            y: 400,
            width: 600,
            height: 800,
            target_width: 600,
            target_height: 800,
        };

        let requests = plan_pair_regions(
            &db,
            None,
            &mut pair,
            &mut original,
            "m1",
            2,
            &PairRegions {
                original: Some(orig_region),
                showing: Some(show_region),
                against: Some(against_region),
            },
        )
        .unwrap();
        assert_eq!(requests.len(), 3);
        assert_eq!(requests[0]["data"]["slot"], "original");
        assert_eq!(requests[0]["data"]["target_id"], "m1");
        assert_eq!(requests[1]["data"]["slot"], "showing");
        assert_eq!(requests[1]["data"]["target_id"], "job-b");
        assert_eq!(requests[2]["data"]["slot"], "against");
        assert_eq!(requests[2]["data"]["target_id"], "job-a");

        // Stale request_id / generation / target_id / path are dropped.
        for (slot_idx, key, bad_val) in [
            (0, "request_id", json!(999)),
            (0, "generation", json!(999)),
            (0, "target_id", json!("other")),
            (1, "request_id", json!(999)),
            (1, "generation", json!(1)),
            (1, "target_id", json!("job-a")),
            (1, "image_path", json!("/other.png")),
            (2, "target_id", json!("job-b")),
        ] {
            let mut stale = requests[slot_idx]["data"].clone();
            stale[key] = bad_val;
            stale["png_base64"] = json!("stale");
            apply_pair_region(&db, None, &mut pair, &mut original, &stale, false).unwrap();
        }
        assert!(original.region.is_none());
        assert!(pair.showing.region.is_none());
        assert!(pair.against.region.is_none());

        // Valid answers populate all three sides.
        for (idx, tag) in ["orig", "show", "against"].iter().enumerate() {
            let mut ok = requests[idx]["data"].clone();
            ok["png_base64"] = json!(tag);
            apply_pair_region(&db, None, &mut pair, &mut original, &ok, false).unwrap();
        }
        assert_eq!(
            original.region.as_ref().unwrap().data_url,
            "data:image/png;base64,orig"
        );
        assert_eq!(
            pair.showing.region.as_ref().unwrap().data_url,
            "data:image/png;base64,show"
        );
        assert_eq!(
            pair.against.region.as_ref().unwrap().data_url,
            "data:image/png;base64,against"
        );

        // Coalescing: a newer plan drops prior answers and falls back cleanly on failure.
        let newer = plan_pair_regions(
            &db,
            None,
            &mut pair,
            &mut original,
            "m1",
            2,
            &PairRegions {
                original: Some(orig_region),
                showing: Some(show_region),
                against: None,
            },
        )
        .unwrap();
        assert!(original.region.is_none());
        assert!(pair.showing.region.is_none());
        assert!(pair.against.region.is_none());

        let mut old_show = requests[1]["data"].clone();
        old_show["png_base64"] = json!("old");
        apply_pair_region(&db, None, &mut pair, &mut original, &old_show, false).unwrap();
        assert!(pair.showing.region.is_none());

        apply_pair_region(&db, None, &mut pair, &mut original, &newer[1]["data"], true).unwrap();
        assert!(pair.showing.region.is_none());
        assert_eq!(pair.showing.preview_data_url, "asset://b");

        // Replaced output file cannot accept an answer.
        std::fs::remove_file(&output_b).unwrap();
        std::fs::write(&output_b, "tampered").unwrap();
        let newer2 = plan_pair_regions(
            &db,
            None,
            &mut pair,
            &mut original,
            "m1",
            2,
            &PairRegions {
                original: Some(orig_region),
                showing: None,
                against: None,
            },
        )
        .unwrap();
        let mut orig_ok = newer2[0]["data"].clone();
        orig_ok["png_base64"] = json!("fresh");
        apply_pair_region(&db, None, &mut pair, &mut original, &orig_ok, false).unwrap();
        assert_eq!(
            original.region.as_ref().unwrap().data_url,
            "data:image/png;base64,fresh"
        );
        clear_pair_regions(&mut pair, &mut original);
        assert!(original.region.is_none());
    }
}
