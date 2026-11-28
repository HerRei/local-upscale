//! The paint of Dust & scratches: the strokes the window sends, and the mask the host draws
//! from them for the worker. The worker never sees a stroke and the window never sees a mask.

use std::{
    fs,
    io::Write,
    path::{Path, PathBuf},
};

use flate2::{write::ZlibEncoder, Compression, Crc};
use serde::{Deserialize, Serialize};

use crate::error::{AppError, AppResult};

/// At most this many strokes on one photo.
pub const MAX_STROKES: usize = 2000;
/// At most this many coordinates over all strokes of one photo.
pub const MAX_NUMBERS: usize = 100_000;
const MAX_BRUSH: f64 = 1024.0;
/// Coordinates are held to this reach so a hostile number cannot overflow the drawing.
const REACH: f64 = 1.0e6;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum StrokeMode {
    Paint,
    Erase,
}

#[derive(Clone, Debug, PartialEq, Deserialize, Serialize)]
pub struct RepairStroke {
    pub mode: StrokeMode,
    /// The brush's width in source pixels.
    pub size: f64,
    /// x0, y0, x1, y1, ... in source pixels; two numbers are a dot.
    pub points: Vec<f64>,
}

/// The folder the host keeps the drawn masks in.
pub fn masks_root(next_root: &Path) -> PathBuf {
    next_root.join("masks")
}

/// Checks a photo's whole list of strokes.
pub fn validate_strokes(strokes: &[RepairStroke]) -> Result<(), String> {
    if strokes.len() > MAX_STROKES {
        return Err("There is too much paint on this photo. Clear some of it.".into());
    }
    let mut numbers = 0_usize;
    for stroke in strokes {
        numbers = numbers.saturating_add(stroke.points.len());
        if !(1.0..=MAX_BRUSH).contains(&stroke.size) {
            return Err("A brush size must be between 1 and 1024 pixels.".into());
        }
        if stroke.points.len() < 2
            || stroke.points.len() % 2 != 0
            || stroke.points.iter().any(|value| !value.is_finite())
        {
            return Err("A stroke needs pairs of finite coordinates.".into());
        }
    }
    if numbers > MAX_NUMBERS {
        return Err("There is too much paint on this photo. Clear some of it.".into());
    }
    Ok(())
}

/// The strokes that paint, which is what the window counts as marks.
pub fn count_marks(strokes: &[RepairStroke]) -> u32 {
    let marks = strokes
        .iter()
        .filter(|stroke| stroke.mode == StrokeMode::Paint)
        .count();
    u32::try_from(marks).unwrap_or(u32::MAX)
}

/// The mask of `strokes` for a photo of `width` x `height`: 255 where the centre of a pixel is
/// within half the brush of a paint stroke's line and no later erase stroke covers it, else 0.
pub fn draw_mask(strokes: &[RepairStroke], width: u32, height: u32) -> Vec<u8> {
    let (w, h) = (width as usize, height as usize);
    let mut pixels = vec![0_u8; w * h];
    for stroke in strokes {
        let value = if stroke.mode == StrokeMode::Paint {
            255
        } else {
            0
        };
        let radius = stroke.size / 2.0;
        let points: Vec<(f64, f64)> = stroke
            .points
            .chunks_exact(2)
            .map(|pair| (pair[0].clamp(-REACH, REACH), pair[1].clamp(-REACH, REACH)))
            .collect();
        match points.as_slice() {
            [] => {}
            [only] => fill_capsule(&mut pixels, w, h, *only, *only, radius, value),
            _ => {
                for pair in points.windows(2) {
                    fill_capsule(&mut pixels, w, h, pair[0], pair[1], radius, value);
                }
            }
        }
    }
    pixels
}

/// Sets `value` on every pixel whose centre is within `radius` of the segment `a`-`b`. Each
/// row's covered stretch is one interval, found from the two end discs and the strip between.
fn fill_capsule(
    pixels: &mut [u8],
    w: usize,
    h: usize,
    a: (f64, f64),
    b: (f64, f64),
    radius: f64,
    value: u8,
) {
    let top = (a.1.min(b.1) - radius).floor().max(0.0) as usize;
    let bottom = (a.1.max(b.1) + radius).ceil().min(h as f64);
    if bottom <= 0.0 {
        return;
    }
    let (dx, dy) = (b.0 - a.0, b.1 - a.1);
    let length = dx.hypot(dy);
    for row in top..bottom as usize {
        let yc = row as f64 + 0.5;
        let mut lo = f64::INFINITY;
        let mut hi = f64::NEG_INFINITY;
        for centre in [a, b] {
            let across = radius * radius - (yc - centre.1).powi(2);
            if across >= 0.0 {
                let half = across.sqrt();
                lo = lo.min(centre.0 - half);
                hi = hi.max(centre.0 + half);
            }
        }
        if length > 0.0 {
            if let Some((strip_lo, strip_hi)) =
                strip_interval(a, (dx / length, dy / length), length, radius, yc)
            {
                lo = lo.min(strip_lo);
                hi = hi.max(strip_hi);
            }
        }
        if lo > hi {
            continue;
        }
        let first = (lo - 0.5).ceil().max(0.0);
        let last = (hi - 0.5).floor().min(w as f64 - 1.0);
        if first > last {
            continue;
        }
        pixels[row * w + first as usize..=row * w + last as usize].fill(value);
    }
}

/// The part of the row at height `yc` inside the strip of half-width `radius` around the
/// segment from `a` along `u` for `length`, as an interval of x.
fn strip_interval(
    a: (f64, f64),
    u: (f64, f64),
    length: f64,
    radius: f64,
    yc: f64,
) -> Option<(f64, f64)> {
    let ry = yc - a.1;
    let mut lo = f64::NEG_INFINITY;
    let mut hi = f64::INFINITY;
    // Along the segment: 0 <= (x - a.x) * u.x + ry * u.y <= length, and across it:
    // -radius <= -(x - a.x) * u.y + ry * u.x <= radius. Each is `low <= k * t + c <= high`
    // in t = x - a.x.
    for (k, c, low, high) in [
        (u.0, ry * u.1, 0.0, length),
        (-u.1, ry * u.0, -radius, radius),
    ] {
        if k.abs() < 1e-12 {
            if c < low || c > high {
                return None;
            }
            continue;
        }
        let (t0, t1) = ((low - c) / k, (high - c) / k);
        lo = lo.max(t0.min(t1));
        hi = hi.min(t0.max(t1));
    }
    (lo <= hi).then_some((a.0 + lo, a.0 + hi))
}

/// An 8-bit grey PNG of `pixels`.
fn encode_grey_png(pixels: &[u8], width: u32, height: u32) -> AppResult<Vec<u8>> {
    let mut raw = Vec::with_capacity(pixels.len() + height as usize);
    for row in pixels.chunks_exact(width as usize) {
        raw.push(0);
        raw.extend_from_slice(row);
    }
    let mut compressed = ZlibEncoder::new(Vec::new(), Compression::fast());
    compressed.write_all(&raw)?;
    let compressed = compressed.finish()?;

    fn chunk(out: &mut Vec<u8>, tag: &[u8; 4], data: &[u8]) {
        out.extend_from_slice(&u32::try_from(data.len()).unwrap_or(u32::MAX).to_be_bytes());
        out.extend_from_slice(tag);
        out.extend_from_slice(data);
        let mut crc = Crc::new();
        crc.update(tag);
        crc.update(data);
        out.extend_from_slice(&crc.sum().to_be_bytes());
    }
    let mut header = Vec::with_capacity(13);
    header.extend_from_slice(&width.to_be_bytes());
    header.extend_from_slice(&height.to_be_bytes());
    // 8 bits, grey, deflate, no filter beyond the per-row byte, not interlaced.
    header.extend_from_slice(&[8, 0, 0, 0, 0]);
    let mut png = vec![0x89, b'P', b'N', b'G', 0x0d, 0x0a, 0x1a, 0x0a];
    chunk(&mut png, b"IHDR", &header);
    chunk(&mut png, b"IDAT", &compressed);
    chunk(&mut png, b"IEND", &[]);
    Ok(png)
}

/// Writes the mask of `strokes` to `path` as an 8-bit grey PNG of the photo's size.
pub fn write_mask(path: &Path, strokes: &[RepairStroke], width: u32, height: u32) -> AppResult<()> {
    if width == 0 || height == 0 {
        return Err(AppError::Validation("the photo has no size".into()));
    }
    let png = encode_grey_png(&draw_mask(strokes, width, height), width, height)?;
    let directory = path
        .parent()
        .ok_or_else(|| AppError::Config("the mask has no folder".into()))?;
    fs::create_dir_all(directory)?;
    // Written whole beside its name and renamed, so a worker never reads half a mask.
    let partial = path.with_extension("png.part");
    fs::write(&partial, png)?;
    fs::rename(&partial, path)?;
    Ok(())
}

/// The file name a photo's mask has at one revision of its paint.
pub fn mask_file_name(media_id: &str, revision: u32) -> String {
    format!("{media_id}-{revision}.png")
}

/// Deletes the drawn masks of a photo, except the one named `keep`.
pub fn remove_masks_of(root: &Path, media_id: &str, keep: Option<&str>) {
    let Ok(entries) = fs::read_dir(root) else {
        return;
    };
    let prefix = format!("{media_id}-");
    for entry in entries.flatten() {
        let name = entry.file_name();
        let Some(name) = name.to_str() else { continue };
        let ours = name.strip_prefix(&prefix).is_some_and(|rest| {
            let digits = rest.trim_end_matches(".png").trim_end_matches(".png.part");
            !digits.is_empty() && digits.bytes().all(|byte| byte.is_ascii_digit())
        });
        if ours && Some(name) != keep {
            let _ = fs::remove_file(entry.path());
        }
    }
}

/// Deletes every drawn mask.
pub fn remove_all_masks(root: &Path) {
    let _ = fs::remove_dir_all(root);
}

#[cfg(test)]
mod tests {
    use super::*;

    fn stroke(mode: StrokeMode, size: f64, points: &[f64]) -> RepairStroke {
        RepairStroke {
            mode,
            size,
            points: points.to_vec(),
        }
    }

    fn at(mask: &[u8], width: usize, x: usize, y: usize) -> u8 {
        mask[y * width + x]
    }

    /// The distance from a pixel's centre to the segment, by the definition.
    fn distance(x: usize, y: usize, a: (f64, f64), b: (f64, f64)) -> f64 {
        let (px, py) = (x as f64 + 0.5, y as f64 + 0.5);
        let (dx, dy) = (b.0 - a.0, b.1 - a.1);
        let squared = dx * dx + dy * dy;
        let t = if squared == 0.0 {
            0.0
        } else {
            (((px - a.0) * dx + (py - a.1) * dy) / squared).clamp(0.0, 1.0)
        };
        (px - (a.0 + t * dx)).hypot(py - (a.1 + t * dy))
    }

    #[test]
    fn a_dot_is_a_filled_circle() {
        let mask = draw_mask(&[stroke(StrokeMode::Paint, 10.0, &[20.0, 20.0])], 40, 40);
        for y in 0..40 {
            for x in 0..40 {
                let inside = distance(x, y, (20.0, 20.0), (20.0, 20.0)) <= 5.0;
                assert_eq!(at(&mask, 40, x, y), if inside { 255 } else { 0 }, "{x},{y}");
            }
        }
    }

    #[test]
    fn a_line_has_round_ends_and_joins() {
        let points = [10.0, 10.0, 50.0, 14.5, 50.0, 40.0];
        let mask = draw_mask(&[stroke(StrokeMode::Paint, 7.0, &points)], 64, 64);
        for y in 0..64 {
            for x in 0..64 {
                let near = distance(x, y, (10.0, 10.0), (50.0, 14.5)).min(distance(
                    x,
                    y,
                    (50.0, 14.5),
                    (50.0, 40.0),
                ));
                // Pixels on the border may fall either way by rounding.
                if (near - 3.5).abs() < 1e-6 {
                    continue;
                }
                assert_eq!(
                    at(&mask, 64, x, y),
                    if near < 3.5 { 255 } else { 0 },
                    "{x},{y}"
                );
            }
        }
        // The ends are round: the corner of the box around the first point stays clear.
        assert_eq!(at(&mask, 64, 7, 7), 0);
        assert_eq!(at(&mask, 64, 8, 10), 255);
        // Beside the line, past its half width, is clear.
        assert_eq!(at(&mask, 64, 30, 20), 0);
        // The bend is filled round, not cut square.
        assert_eq!(at(&mask, 64, 52, 12), 255);
        assert_eq!(at(&mask, 64, 53, 11), 0);
    }

    #[test]
    fn a_stroke_off_the_photo_is_held_to_its_edge() {
        let mask = draw_mask(
            &[stroke(StrokeMode::Paint, 6.0, &[-30.0, 5.0, 2.0, 5.0])],
            10,
            10,
        );
        assert_eq!(at(&mask, 10, 0, 5), 255);
        assert_eq!(at(&mask, 10, 6, 5), 0);
    }

    #[test]
    fn erase_takes_paint_away_and_later_paint_returns() {
        let paint = stroke(StrokeMode::Paint, 8.0, &[5.0, 20.0, 35.0, 20.0]);
        let erase = stroke(StrokeMode::Erase, 4.0, &[20.0, 5.0, 20.0, 35.0]);
        let again = stroke(StrokeMode::Paint, 2.0, &[20.0, 20.0]);
        let width = 40;
        let mask = draw_mask(&[paint.clone(), erase.clone()], 40, 40);
        assert_eq!(at(&mask, width, 10, 20), 255);
        assert_eq!(at(&mask, width, 20, 18), 0, "erased across the paint");
        assert_eq!(at(&mask, width, 20, 5), 0, "erase alone paints nothing");
        let mask = draw_mask(&[paint, erase, again], 40, 40);
        assert_eq!(
            at(&mask, width, 20, 20),
            255,
            "paint after the erase returns"
        );
        assert_eq!(at(&mask, width, 20, 17), 0);
    }

    #[test]
    fn the_mask_has_the_photos_size_and_one_channel() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("masks").join("m-1.png");
        write_mask(
            &path,
            &[stroke(StrokeMode::Paint, 6.0, &[10.0, 5.0])],
            23,
            11,
        )
        .unwrap();
        let bytes = fs::read(&path).unwrap();
        assert_eq!(
            &bytes[..8],
            &[0x89, b'P', b'N', b'G', 0x0d, 0x0a, 0x1a, 0x0a]
        );
        assert_eq!(&bytes[12..16], b"IHDR");
        assert_eq!(u32::from_be_bytes(bytes[16..20].try_into().unwrap()), 23);
        assert_eq!(u32::from_be_bytes(bytes[20..24].try_into().unwrap()), 11);
        // 8 bits, colour type 0 (grey).
        assert_eq!(&bytes[24..26], &[8, 0]);
        assert!(!path.with_extension("png.part").exists());
    }

    #[test]
    fn strokes_are_checked() {
        let good = stroke(StrokeMode::Paint, 5.0, &[1.0, 2.0]);
        assert!(validate_strokes(std::slice::from_ref(&good)).is_ok());
        for bad in [
            stroke(StrokeMode::Paint, 0.5, &[1.0, 2.0]),
            stroke(StrokeMode::Paint, 1025.0, &[1.0, 2.0]),
            stroke(StrokeMode::Paint, f64::NAN, &[1.0, 2.0]),
            stroke(StrokeMode::Paint, 5.0, &[1.0]),
            stroke(StrokeMode::Paint, 5.0, &[]),
            stroke(StrokeMode::Paint, 5.0, &[1.0, f64::INFINITY]),
        ] {
            assert!(validate_strokes(&[bad]).is_err());
        }
        assert!(validate_strokes(&vec![good.clone(); MAX_STROKES + 1]).is_err());
        let long = stroke(StrokeMode::Paint, 5.0, &vec![1.0; 60_000]);
        assert!(validate_strokes(std::slice::from_ref(&long)).is_ok());
        assert!(validate_strokes(&[long.clone(), long]).is_err());
    }

    #[test]
    fn marks_are_the_paint_strokes() {
        let strokes = [
            stroke(StrokeMode::Paint, 5.0, &[1.0, 2.0]),
            stroke(StrokeMode::Erase, 5.0, &[1.0, 2.0]),
            stroke(StrokeMode::Paint, 5.0, &[3.0, 4.0]),
        ];
        assert_eq!(count_marks(&strokes), 2);
    }

    #[test]
    fn old_masks_of_one_photo_are_removed_and_others_stay() {
        let directory = tempfile::tempdir().unwrap();
        for name in [
            "a-1.png",
            "a-2.png",
            "a-3.png.part",
            "ab-1.png",
            "b-1.png",
            "a-x.png",
        ] {
            fs::write(directory.path().join(name), b"x").unwrap();
        }
        remove_masks_of(directory.path(), "a", Some("a-2.png"));
        let mut left: Vec<String> = fs::read_dir(directory.path())
            .unwrap()
            .map(|entry| entry.unwrap().file_name().into_string().unwrap())
            .collect();
        left.sort();
        assert_eq!(left, ["a-2.png", "a-x.png", "ab-1.png", "b-1.png"]);
    }
}
