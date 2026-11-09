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
    #[serde(skip)]
    path: String,
    #[serde(skip)]
    file: Option<FileIdentity>,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
pub struct CompareState {
    pub generation: u64,
    pub slots: Vec<CompareSlot>,
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
        slots.push(CompareSlot {
            pick: pick.clone(),
            status: if file.is_some() {
                CompareStatus::Loading
            } else {
                CompareStatus::Missing
            },
            path,
            file,
            preview_data_url: String::new(),
            error: String::new(),
            width: 0,
            height: 0,
            preview_width: 0,
            preview_height: 0,
        });
    }
    let generation = compare
        .generation
        .checked_add(1)
        .ok_or_else(|| AppError::Validation("Compare generation exhausted".into()))?;
    let requests = slots.iter().enumerate().filter(|(_, slot)| slot.status == CompareStatus::Loading).map(|(index, slot)| json!({"type":"preview_request", "data": {"image_path":slot.path, "max_dimension":RESULT_PREVIEW_MAX_DIMENSION, "comparison":true, "slot":format!("slot{}", index + 1), "generation":generation, "target_id":slot.pick.target_id()}})).collect();
    *compare = CompareState { generation, slots };
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
            slot.error.clear();
            slot.width = 0;
            slot.height = 0;
            slot.preview_width = 0;
            slot.preview_height = 0;
        }
    }
    Ok(())
}

pub fn apply(
    db: &Database,
    compare: &mut CompareState,
    data: &Value,
    failed: bool,
) -> AppResult<()> {
    let index = match data["slot"].as_str() {
        Some("slot1") => 0,
        Some("slot2") => 1,
        Some("slot3") => 2,
        Some("slot4") => 3,
        _ => return Ok(()),
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
        db.remove_media("a").unwrap();
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
}
