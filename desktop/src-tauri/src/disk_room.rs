//! Room on the output's volume, so a job that cannot be written is refused before it queues.
//!
//! The engine decides for itself with `src/localsr/core/disk_space.py`; this is the same
//! estimate and reserve, from what the host knows before the job runs. Both are tested
//! against `tests/fixtures/disk_estimate_cases.json`. The host cannot see an image's
//! transparency or the codec a video job resolves to, so it never asks for more than
//! the engine will: the engine's check is the exact one.

use std::path::{Path, PathBuf};

use serde_json::Value;

use crate::types::MediaItem;

/// The system volume keeps this much free, other volumes less: swap can grow by that much.
const SYSTEM_RESERVE: u64 = 8_000_000_000;
const OTHER_RESERVE: u64 = 1_000_000_000;
/// Headers, an ICC profile and EXIF around the pixels.
const SLACK: u64 = 1 << 20;
/// The output memmap holds three uint8 samples per pixel.
const MEMMAP_CHANNELS: u64 = 3;
const VIDEO_BYTES_PER_PIXEL_FRAME: u64 = 20;

/// What a job the stopped queue did not start says; the status line says the rest.
pub const NOT_STARTED: &str = "Not started: the queue stopped because the disk is full.";

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Volume {
    pub free: u64,
    pub total: u64,
    pub system: bool,
}

pub fn reserve(volume: &Volume) -> u64 {
    if volume.system {
        SYSTEM_RESERVE
    } else {
        OTHER_RESERVE
    }
}

/// Decimal units, as the app's other size messages.
pub fn format_size(bytes: u64) -> String {
    if bytes >= 1_000_000_000 {
        format!("{:.1} GB", bytes as f64 / 1e9)
    } else if bytes >= 1_000_000 {
        format!("{:.0} MB", bytes as f64 / 1e6)
    } else {
        format!("{:.0} KB", bytes as f64 / 1e3)
    }
}

pub fn encoded_bound(format: &str, width: u64, height: u64, alpha: bool) -> u64 {
    let pixels = width * height;
    match format.to_ascii_lowercase().as_str() {
        "jpg" | "jpeg" => 2 * pixels + SLACK,
        "heic" => pixels + SLACK,
        "webp" => (if alpha { 3 } else { 2 }) * pixels + SLACK,
        _ => {
            let raw = (if alpha { 4 } else { 3 }) * pixels;
            raw + raw / 256 + height + SLACK
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct ImageNeed {
    /// The output memmap, at the model's native scale.
    pub scratch: u64,
    /// The encoded temporary; the final name links to it.
    pub encoded: u64,
}

impl ImageNeed {
    pub fn total(&self) -> u64 {
        self.scratch + self.encoded
    }
}

pub fn image_need(
    width: u64,
    height: u64,
    scale: u64,
    output_scale: u64,
    format: &str,
    alpha: bool,
) -> ImageNeed {
    let saved = if output_scale == 0 {
        scale
    } else {
        output_scale
    };
    ImageNeed {
        scratch: MEMMAP_CHANNELS * width * scale * height * scale,
        encoded: encoded_bound(format, width * saved, height * saved, alpha),
    }
}

pub fn video_bytes(width: u64, height: u64, frames: u64, master: bool) -> u64 {
    let frames = frames.max(1);
    let encoded = width * height * frames / VIDEO_BYTES_PER_PIXEL_FRAME + SLACK;
    let lossless = if master {
        (width * height * frames) as f64 * 0.9
    } else {
        0.0
    };
    2 * encoded + lossless as u64
}

/// The output folder, not the hidden per-job folder the host makes inside it.
fn shown(folder: &Path) -> &Path {
    match folder.file_name().and_then(|name| name.to_str()) {
        Some(name) if name.starts_with(".localsr-job-") => folder.parent().unwrap_or(folder),
        _ => folder,
    }
}

/// The sentence for a job that needs `need` bytes in `folder`, or None when it fits.
pub fn refusal(folder: &Path, need: u64, volume: &Volume) -> Option<String> {
    if volume.free.saturating_sub(reserve(volume)) >= need {
        return None;
    }
    Some(format!(
        "Not enough free disk space in \"{}\": about {} is needed, but only {} is free, and {} must stay free so the computer keeps working.",
        shown(folder).display(),
        format_size(need),
        format_size(volume.free),
        format_size(reserve(volume)),
    ))
}

#[cfg(test)]
thread_local! {
    /// Pretend every volume looks like this; unset, tests see a roomy disk.
    static VOLUME: std::cell::Cell<Option<Volume>> = const { std::cell::Cell::new(None) };
}

#[cfg(test)]
pub fn set_test_volume(volume: Option<Volume>) {
    VOLUME.with(|cell| cell.set(volume));
}

/// The nearest folder at or above `folder` that exists: an output folder may be made later.
fn existing(folder: &Path) -> &Path {
    let mut path = folder;
    while !path.exists() {
        match path.parent() {
            Some(parent) => path = parent,
            None => break,
        }
    }
    path
}

fn on_system_volume(path: &Path) -> bool {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let device = |path: &Path| std::fs::metadata(path).ok().map(|info| info.dev());
        let Some(here) = device(path) else {
            return false;
        };
        let home = directories::BaseDirs::new().and_then(|dirs| device(dirs.home_dir()));
        home == Some(here) || device(Path::new("/")) == Some(here)
    }
    #[cfg(not(unix))]
    {
        let drive = |path: &Path| {
            std::fs::canonicalize(path).ok().and_then(|path| {
                path.components()
                    .next()
                    .map(|part| part.as_os_str().to_string_lossy().to_lowercase())
            })
        };
        let home = directories::BaseDirs::new().and_then(|dirs| drive(dirs.home_dir()));
        home.is_some() && home == drive(path)
    }
}

#[cfg_attr(test, allow(dead_code))]
fn real_volume(folder: &Path) -> std::io::Result<Volume> {
    let path = existing(folder);
    Ok(Volume {
        free: fs2::available_space(path)?,
        total: fs2::total_space(path)?,
        system: on_system_volume(path),
    })
}

pub fn volume_of(folder: &Path) -> std::io::Result<Volume> {
    #[cfg(test)]
    {
        let _ = folder;
        Ok(VOLUME.with(|cell| cell.get()).unwrap_or(Volume {
            free: u64::MAX / 2,
            total: u64::MAX / 2,
            system: false,
        }))
    }
    #[cfg(not(test))]
    real_volume(folder)
}

/// The sentence refusing a job that needs `need` bytes in `folder`. A volume that cannot
/// be read does not stop the job: the engine checks again and its writes report a full disk.
pub fn check(folder: &Path, need: u64) -> Option<String> {
    refusal(folder, need, &volume_of(folder).ok()?)
}

/// The folder a stored request writes into and the bytes it needs there at its peak, from
/// what the host knows: the source's size in `media` and the model's native scale. None
/// for a request that writes no large file or whose size cannot be known here.
pub fn request_need(
    request: &Value,
    media: Option<&MediaItem>,
    native_scale: u64,
) -> Option<(PathBuf, u64)> {
    let data = &request["data"];
    let media = media?;
    let (width, height) = (u64::from(media.width), u64::from(media.height));
    let output_scale = data["output_scale"].as_u64().unwrap_or(0);
    match request["type"].as_str()? {
        "job_request" => {
            let folder = Path::new(data["output_path"].as_str()?).parent()?;
            let need = image_need(
                width,
                height,
                native_scale.max(1),
                output_scale,
                data["output_format"].as_str().unwrap_or("png"),
                false,
            );
            Some((folder.to_path_buf(), need.total()))
        }
        "video_job_request" => {
            let folder = Path::new(data["output_video_path"].as_str()?).parent()?;
            let scale = if output_scale == 0 {
                native_scale.max(1)
            } else {
                output_scale
            };
            Some((
                folder.to_path_buf(),
                video_bytes(width * scale, height * scale, media.frame_count, false),
            ))
        }
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn cases() -> Value {
        serde_json::from_str(include_str!(
            "../../../tests/fixtures/disk_estimate_cases.json"
        ))
        .unwrap()
    }

    fn number(value: &Value) -> u64 {
        value.as_u64().unwrap()
    }

    #[test]
    fn the_estimate_and_the_reserve_agree_with_the_engine() {
        let cases = cases();
        for case in cases["images"].as_array().unwrap() {
            let need = image_need(
                number(&case["width"]),
                number(&case["height"]),
                number(&case["scale"]),
                number(&case["output_scale"]),
                case["format"].as_str().unwrap(),
                case["alpha"].as_bool().unwrap(),
            );
            assert_eq!(
                (need.scratch, need.encoded),
                (number(&case["scratch"]), number(&case["encoded"])),
                "{case}"
            );
        }
        for case in cases["reserves"].as_array().unwrap() {
            let volume = Volume {
                free: 0,
                total: number(&case["total"]),
                system: case["system"].as_bool().unwrap(),
            };
            assert_eq!(reserve(&volume), number(&case["reserve"]), "{case}");
        }
        for case in cases["videos"].as_array().unwrap() {
            let bytes = video_bytes(
                number(&case["width"]),
                number(&case["height"]),
                number(&case["frames"]),
                case["master"].as_bool().unwrap(),
            );
            assert_eq!(bytes, number(&case["bytes"]), "{case}");
        }
        for case in cases["sizes"].as_array().unwrap() {
            assert_eq!(
                format_size(number(&case["bytes"])),
                case["text"].as_str().unwrap(),
                "{case}"
            );
        }
    }

    #[test]
    fn a_real_folder_has_a_volume_and_a_missing_one_takes_its_parent_s() {
        let directory = tempfile::tempdir().unwrap();
        let volume = real_volume(&directory.path().join("not").join("made").join("yet")).unwrap();
        assert!(volume.total > 0 && volume.free <= volume.total);
    }

    fn media(width: u32, height: u32) -> MediaItem {
        serde_json::from_value(json!({"id": "m", "path": "/in/a.jpg", "name": "a.jpg",
            "kind": "image", "width": width, "height": height, "frame_count": 1, "fps": 0.0,
            "duration_seconds": 0.0, "preview_data_url": "", "probe_status": "ready",
            "error": "", "selected": false}))
        .unwrap()
    }

    #[test]
    fn a_phone_photo_at_4x_png_needs_the_memmap_and_the_encoded_file() {
        let request = json!({"type": "job_request", "data": {
            "output_path": "/out/a.png", "output_format": "png", "output_scale": 4}});
        let (folder, bytes) = request_need(&request, Some(&media(3024, 4032)), 4).unwrap();
        assert_eq!(folder, Path::new("/out"));
        assert_eq!(bytes, 1_173_856_576);
        // The memmap is at the model's native scale even when a smaller one is saved.
        let half = json!({"type": "job_request", "data": {
            "output_path": "/out/a.png", "output_format": "png", "output_scale": 2}});
        let (_, bytes) = request_need(&half, Some(&media(3024, 4032)), 4).unwrap();
        assert_eq!(bytes, 585_252_864 + 147_941_392);
        assert!(request_need(&json!({"type": "edit_job_request", "data": {}}), None, 4).is_none());
    }

    #[test]
    fn a_job_fits_when_its_need_stays_inside_free_space_minus_the_reserve() {
        let need = 1_173_856_576;
        let system = |free| Volume {
            free,
            total: 500_000_000_000,
            system: true,
        };
        assert!(refusal(Path::new("/out"), need, &system(need + 8_000_000_000)).is_none());
        assert!(refusal(Path::new("/out"), need, &system(need + 8_000_000_000 - 1)).is_some());
        let other = Volume {
            free: need + 1_000_000_000,
            total: 4_000_000_000_000,
            system: false,
        };
        assert!(refusal(Path::new("/out"), need, &other).is_none());
    }

    #[test]
    fn the_refusal_is_one_sentence_naming_the_folder_the_need_and_what_is_free() {
        let volume = Volume {
            free: 4_700_000_000,
            total: 500_000_000_000,
            system: true,
        };
        let folder =
            Path::new("/Users/a/Pictures/.localsr-job-0b8b2b39-6c1f-4d54-8f0e-5a5f2e2b8c11");
        assert_eq!(
            refusal(folder, 1_173_856_576, &volume).unwrap(),
            "Not enough free disk space in \"/Users/a/Pictures\": about 1.2 GB is needed, but only 4.7 GB is free, and 8.0 GB must stay free so the computer keeps working."
        );
    }
}
