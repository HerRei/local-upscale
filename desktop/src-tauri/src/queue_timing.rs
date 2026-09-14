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

#[cfg(test)]
mod tests {
    use super::*;

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
    fn video_work_obeys_trim_and_temporal_resolution() {
        let request = json!({"type":"video_job_request","data":{"model_kind":"seedvr2","start_frame":5,"end_frame":20}}).to_string();
        let small = work_profile(&request, 640, 480, 30, "HLG");
        assert_eq!(small.1, 15.0);
        assert_ne!(small.0, work_profile(&request, 3840, 2160, 30, "HLG").0);
        assert_eq!(work_profile(&request, 640, 480, 0, "").1, 0.0);
    }
}
