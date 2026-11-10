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
    reserve_with_override(
        volume,
        &std::env::var("LOCALSR_DISK_RESERVE_BYTES").unwrap_or_default(),
    )
}

fn reserve_with_override(volume: &Volume, value: &str) -> u64 {
    let mut parsed = Some(0_u64);
    let attributes = value.chars().map(decimal_digit).collect::<Option<Vec<_>>>();
    if let Some(digits) = attributes.filter(|digits| !digits.is_empty()) {
        for digit in digits {
            parsed = parsed.and_then(|n| n.checked_mul(10)?.checked_add(u64::from(digit)));
        }
        return parsed.unwrap_or(u64::MAX);
    }
    if volume.system {
        SYSTEM_RESERVE
    } else {
        OTHER_RESERVE
    }
}

fn decimal_digit(character: char) -> Option<u32> {
    const ZEROES: &[u32] = &[
        0x30, 0x660, 0x6F0, 0x7C0, 0x966, 0x9E6, 0xA66, 0xAE6, 0xB66, 0xBE6, 0xC66, 0xCE6, 0xD66,
        0xDE6, 0xE50, 0xED0, 0xF20, 0x1040, 0x1090, 0x17E0, 0x1810, 0x1946, 0x19D0, 0x1A80, 0x1A90,
        0x1B50, 0x1BB0, 0x1C40, 0x1C50, 0xA620, 0xA8D0, 0xA900, 0xA9D0, 0xA9F0, 0xAA50, 0xABF0,
        0xFF10, 0x104A0, 0x10D30, 0x10D40, 0x11066, 0x110F0, 0x11136, 0x111D0, 0x112F0, 0x11450,
        0x114D0, 0x11650, 0x116C0, 0x116D0, 0x116DA, 0x11730, 0x118E0, 0x11950, 0x11BF0, 0x11C50,
        0x11D50, 0x11DA0, 0x11DE0, 0x11F50, 0x16130, 0x16A60, 0x16AC0, 0x16B50, 0x16D70, 0x1CCF0,
        0x1D7CE, 0x1D7D8, 0x1D7E2, 0x1D7EC, 0x1D7F6, 0x1E140, 0x1E2F0, 0x1E4F0, 0x1E5F1, 0x1E950,
        0x1FBF0,
    ];
    let code = character as u32;
    ZEROES
        .iter()
        .find_map(|zero| code.checked_sub(*zero).filter(|digit| *digit < 10))
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
    let calculate = || -> Option<u64> {
        let pixels = width.checked_mul(height)?;
        match format.to_ascii_lowercase().as_str() {
            "jpg" | "jpeg" => 2_u64.checked_mul(pixels)?.checked_add(SLACK),
            "heic" => pixels.checked_add(SLACK),
            "webp" => (if alpha { 3_u64 } else { 2 })
                .checked_mul(pixels)?
                .checked_add(SLACK),
            _ => {
                let raw = (if alpha { 4_u64 } else { 3 }).checked_mul(pixels)?;
                raw.checked_add(raw / 256)?
                    .checked_add(height)?
                    .checked_add(SLACK)
            }
        }
    };
    calculate().unwrap_or(u64::MAX)
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
        self.scratch.saturating_add(self.encoded)
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
        scratch: MEMMAP_CHANNELS
            .checked_mul(width)
            .and_then(|n| n.checked_mul(scale))
            .and_then(|n| n.checked_mul(height))
            .and_then(|n| n.checked_mul(scale))
            .unwrap_or(u64::MAX),
        encoded: encoded_bound(
            format,
            width.saturating_mul(saved),
            height.saturating_mul(saved),
            alpha,
        ),
    }
}

pub fn video_bytes(width: u64, height: u64, frames: u64, master: bool) -> u64 {
    let calculate = || -> Option<u64> {
        let pixels = width.checked_mul(height)?.checked_mul(frames.max(1))?;
        let encoded = (pixels / VIDEO_BYTES_PER_PIXEL_FRAME).checked_add(SLACK)?;
        let lossless = if master {
            (pixels as f64 * 0.9) as u64
        } else {
            0
        };
        encoded.checked_mul(2)?.checked_add(lossless)
    };
    calculate().unwrap_or(u64::MAX)
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
    if need != u64::MAX && volume.free.saturating_sub(reserve(volume)) >= need {
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
            if width == 0 || height == 0 {
                return None;
            }
            let kind = data["model_kind"].as_str().unwrap_or("spandrel_image");
            let (output_width, output_height) = if kind == "spandrel_image" {
                (width.saturating_mul(scale), height.saturating_mul(scale))
            } else if kind == "seedvr2" {
                let shortest = width.min(height);
                let target = data["target_resolution"].as_u64().unwrap_or(0);
                let target = if target == 0 { shortest } else { target };
                let grown = target as f64 / shortest as f64;
                (
                    (width as f64 * grown).round_ties_even() as u64,
                    (height as f64 * grown).round_ties_even() as u64,
                )
            } else {
                return None;
            };
            Some((
                folder.to_path_buf(),
                video_bytes(output_width, output_height, media.frame_count, false),
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
    fn oversized_video_is_refused_without_overflow() {
        let request = json!({"type": "video_job_request", "data": {
            "output_video_path": "/out/a.mp4", "output_scale": 4,
            "model_kind": "spandrel_image"}});
        let mut clip = media(1024, 1024);
        clip.frame_count = 1_u64 << 40;
        let (folder, need) = request_need(&request, Some(&clip), 4).unwrap();
        let volume = Volume {
            free: 8_100_000_000,
            total: 10_000_000_000,
            system: true,
        };
        assert!(refusal(&folder, need, &volume).is_some());
    }

    #[test]
    fn temporal_video_uses_target_resolution_for_disk_need() {
        let request = json!({"type": "video_job_request", "data": {
            "output_video_path": "/out/a.mp4", "output_scale": 4,
            "model_kind": "seedvr2", "target_resolution": 1080}});
        let mut clip = media(1920, 1080);
        clip.frame_count = 300;
        let (folder, need) = request_need(&request, Some(&clip), 4).unwrap();
        let volume = Volume {
            free: 8_100_000_000,
            total: 10_000_000_000,
            system: true,
        };
        assert!(refusal(&folder, need, &volume).is_none());
    }

    #[test]
    fn disk_reserve_override_accepts_zero_and_decimal_values() {
        let volume = Volume {
            free: 0,
            total: 0,
            system: true,
        };
        assert_eq!(reserve_with_override(&volume, "0"), 0);
        assert_eq!(reserve_with_override(&volume, "12345"), 12345);
        assert_eq!(reserve_with_override(&volume, "١٢"), 12);
        assert_eq!(
            reserve_with_override(&volume, "18446744073709551616"),
            u64::MAX
        );
        assert_eq!(reserve_with_override(&volume, "garbage"), SYSTEM_RESERVE);
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
