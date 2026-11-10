use serde_json::{json, Value};

/// Compare only immutable job configurations. Current inspector settings do
/// not describe jobs that were already queued with another model or device.
pub fn work_profile(
    request: &str,
    width: u32,
    height: u32,
    frames: u64,
    hdr: &str,
) -> (String, f64) {
    let Ok(mut message) = serde_json::from_str::<Value>(request) else {
        return (String::new(), 0.0);
    };
    // Editing has denoising steps and bounded output dimensions, not the source
    // image's restoration tiles. Do not borrow tile-based timing estimates.
    if message["type"] == "edit_job_request" {
        return (String::new(), 0.0);
    }
    let video = message["type"] == "video_job_request";
    let Some(data) = message.get_mut("data").and_then(Value::as_object_mut) else {
        return (String::new(), 0.0);
    };
    if width == 0 || height == 0 {
        return (String::new(), 0.0);
    }
    let tile = data
        .get("tile_size")
        .and_then(Value::as_u64)
        .unwrap_or(256)
        .max(1) as f64;
    let temporal = video
        && data
            .get("model_kind")
            .and_then(Value::as_str)
            .is_some_and(|kind| kind != "spandrel_image");
    let start = data
        .get("start_frame")
        .and_then(Value::as_u64)
        .unwrap_or(0)
        .min(frames);
    let end = data
        .get("end_frame")
        .and_then(Value::as_u64)
        .unwrap_or(frames)
        .min(frames);
    let count = if video {
        end.saturating_sub(start) as f64
    } else {
        1.0
    };
    if count <= 0.0 {
        return (String::new(), 0.0);
    }
    let units = if temporal {
        // Temporal memory plans depend on both input and output shape. Do not
        // extrapolate a small clip's speed to an unmeasured 4K configuration.
        data.insert("input_shape".into(), json!([width, height]));
        count
    } else {
        (f64::from(width) / tile).ceil() * (f64::from(height) / tile).ceil() * count
    };
    for field in [
        "job_id",
        "image_path",
        "video_path",
        "output_path",
        "output_video_path",
        "scratch_directory",
        "temporary_directory",
        "output_temporary_directory",
        "start_frame",
        "end_frame",
    ] {
        data.remove(field);
    }
    data.insert("input_hdr".into(), json!(hdr));
    (message.to_string(), units)
}

/// How many of the newest runs of one workload say what it typically takes.
const RECENT_RUNS: usize = 5;

/// Seconds per work unit that this workload typically takes, from earlier runs of it
/// (newest first, as `(work units, seconds)`), or `None` before two have been measured.
///
/// A single run can be far off: the first one loaded the model, one waited while the Mac
/// slept, a small image is mostly fixed costs. So runs much smaller than the largest are
/// left out and the lower middle value of the rest is taken, not the latest or the average.
pub fn typical_seconds_per_unit(runs: &[(f64, f64)]) -> Option<f64> {
    let recent: Vec<(f64, f64)> = runs
        .iter()
        .copied()
        .filter(|(units, seconds)| {
            units.is_finite() && seconds.is_finite() && *units > 0.0 && *seconds > 0.0
        })
        .take(RECENT_RUNS)
        .collect();
    if recent.len() < 2 {
        return None;
    }
    let largest = recent.iter().map(|run| run.0).fold(0.0, f64::max);
    let mut rates: Vec<f64> = recent
        .iter()
        .filter(|(units, _)| *units >= largest / 4.0)
        .map(|(units, seconds)| seconds / units)
        .collect();
    rates.sort_by(f64::total_cmp);
    Some(rates[(rates.len() - 1) / 2])
}

/// Fills in each job's typical speed from the completed runs of its own workload in `jobs`
/// (newest first). A workload is the same model, device and settings.
pub fn attach_typical_speeds(jobs: &mut [crate::types::JobRecord]) {
    let mut runs: std::collections::HashMap<String, Vec<(f64, f64)>> = Default::default();
    for job in jobs.iter() {
        if job.status == "completed" && !job.work_key.is_empty() {
            runs.entry(job.work_key.clone())
                .or_default()
                .push((job.work_units, job.elapsed_seconds));
        }
    }
    for job in jobs.iter_mut() {
        job.seconds_per_unit = runs
            .get(&job.work_key)
            .and_then(|runs| typical_seconds_per_unit(runs))
            .unwrap_or(0.0);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn edit_steps_do_not_reuse_restoration_tile_estimates() {
        let request =
            json!({"type":"edit_job_request", "data":{"steps":40,"max_dimension":512}}).to_string();
        assert_eq!(
            work_profile(&request, 4000, 3000, 0, ""),
            (String::new(), 0.0)
        );
    }

    #[test]
    fn only_matching_models_and_devices_share_measured_work() {
        let first = json!({"type":"job_request","data":{"model_path":"nafnet.pth","device":"cuda:0","tile_size":256,"image_path":"a.png","output_path":"a-out.png"}});
        let (key, units) = work_profile(&first.to_string(), 1982, 1361, 1, "");
        assert_eq!(units, 48.0);
        let mut second = first.clone();
        second["data"]["image_path"] = json!("b.png");
        assert_eq!(key, work_profile(&second.to_string(), 1024, 1024, 1, "").0);
        second["data"]["device"] = json!("cpu");
        assert_ne!(key, work_profile(&second.to_string(), 1024, 1024, 1, "").0);
        second["data"]["model_path"] = json!("hat.pth");
        assert_ne!(key, work_profile(&second.to_string(), 1024, 1024, 1, "").0);
    }

    #[test]
    fn interpolated_video_does_not_borrow_plain_video_timing() {
        let plain = json!({"type":"video_job_request","data":{"model_kind":"spandrel_image","tile_size":256,"interpolation":"off"}});
        let mut doubled = plain.clone();
        doubled["data"]["interpolation"] = json!("x2");
        assert_ne!(
            work_profile(&plain.to_string(), 640, 360, 48, "").0,
            work_profile(&doubled.to_string(), 640, 360, 48, "").0
        );
    }

    #[test]
    fn video_work_obeys_trim_and_temporal_resolution() {
        let request = json!({"type":"video_job_request","data":{"model_kind":"seedvr2","start_frame":5,"end_frame":20}}).to_string();
        let small = work_profile(&request, 640, 480, 30, "HLG");
        assert_eq!(small.1, 15.0);
        assert_ne!(small.0, work_profile(&request, 3840, 2160, 30, "HLG").0);
        assert_eq!(work_profile(&request, 640, 480, 0, "").1, 0.0);
    }

    #[test]
    fn one_measured_run_is_not_enough_to_predict_another() {
        assert_eq!(typical_seconds_per_unit(&[]), None);
        assert_eq!(typical_seconds_per_unit(&[(4.0, 30.0)]), None);
    }

    #[test]
    fn a_cold_first_run_does_not_set_the_speed_for_later_ones() {
        // Newest first: two warm runs of 1 s per tile and the first run, which loaded the model.
        let typical = typical_seconds_per_unit(&[(48.0, 49.0), (48.0, 50.0), (48.0, 340.0)]);
        assert!((typical.unwrap() - 49.0 / 48.0).abs() < 0.1);
        // With only two runs the faster one stands in.
        let two = typical_seconds_per_unit(&[(48.0, 340.0), (48.0, 50.0)]);
        assert!((two.unwrap() - 50.0 / 48.0).abs() < 1e-9);
    }

    #[test]
    fn a_run_that_waited_for_the_mac_to_wake_is_ignored() {
        let typical = typical_seconds_per_unit(&[(48.0, 4000.0), (48.0, 50.0), (48.0, 48.0)]);
        assert!(typical.unwrap() < 1.1);
    }

    #[test]
    fn small_runs_whose_fixed_costs_dominate_do_not_scale_up_to_big_images() {
        // A 4-tile thumbnail takes 8 s of loading, decoding and saving; a 96-tile photo 50 s.
        let typical = typical_seconds_per_unit(&[(4.0, 8.0), (96.0, 50.0), (100.0, 52.0)]);
        assert!((typical.unwrap() - 0.52).abs() < 0.05);
    }

    #[test]
    fn unusable_runs_are_left_out() {
        assert_eq!(
            typical_seconds_per_unit(&[(f64::NAN, 5.0), (10.0, -1.0), (0.0, 3.0), (10.0, 10.0)]),
            None
        );
    }
}
