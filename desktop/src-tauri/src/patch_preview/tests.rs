use std::sync::mpsc;

use base64::Engine;

use super::*;
use crate::{
    database::NewJob,
    paths::AppPaths,
    types::{EngineInfo, FixStageSelection, WorkerEnvelope},
};

const NOISE: &str = "scunet_color_real_psnr";
const JPEG: &str = "fbcnn_color";
const UPSCALER: &str = "hat_s_x4";

struct Harness {
    _directory: tempfile::TempDir,
    state: Arc<AppState>,
    sent: mpsc::Receiver<String>,
}

impl Harness {
    fn new() -> Self {
        let directory = tempfile::tempdir().unwrap();
        let state = Arc::new(AppState::from_paths(AppPaths::under(directory.path())).unwrap());
        let image = directory.path().join("photo.png");
        std::fs::write(&image, "source").unwrap();
        state.grants.grant_media_file(&image);
        {
            let database = lock(&state.database).unwrap();
            database
                .insert_media("media", image.to_str().unwrap(), "photo.png", "image")
                .unwrap();
            database
                .update_media_probe(
                    image.to_str().unwrap(),
                    "image",
                    2000,
                    1500,
                    1,
                    0.0,
                    0.0,
                    "preview",
                    "",
                    "",
                    true,
                )
                .unwrap();
        }
        {
            let mut catalog = lock(&state.catalog).unwrap();
            for model in &mut catalog.models {
                if [NOISE, JPEG, UPSCALER].contains(&model.model_id.as_str()) {
                    model.installed = true;
                    model.installed_path = Some(image.to_string_lossy().into_owned());
                }
            }
        }
        lock(&state.capabilities).unwrap().system_ram_total = 8 << 30;
        *lock(&state.engine).unwrap() = Some(EngineInfo {
            protocol_version: 1,
            minimum_protocol_version: 1,
            engine_id: "test".into(),
            engine_version: "0".into(),
            features: vec![
                "patch_preview".into(),
                "image_multi_fix".into(),
                "stage_params_and_masks".into(),
                "fix_strength".into(),
            ],
            model_formats: Vec::new(),
            video_engines: Vec::new(),
        });
        let (sender, sent) = mpsc::channel();
        *lock(&state.worker.sender).unwrap() = Some(sender);
        Self {
            _directory: directory,
            state,
            sent,
        }
    }

    fn mode(&self, mode: &str) {
        lock(&self.state.settings).unwrap().preview_patch = mode.into();
    }

    fn messages(&self) -> Vec<Value> {
        self.sent
            .try_iter()
            .map(|line| serde_json::from_str(&line).unwrap())
            .collect()
    }

    fn ask(&self, slot: &str, input: StartBatchInput) -> PatchAsk {
        PatchAsk {
            media_id: "media".into(),
            slot: slot.into(),
            region: PatchRegion {
                x: 160,
                y: 96,
                width: 384,
                height: 384,
            },
            input,
            replace: None,
            stage_limit: None,
            force: false,
        }
    }

    fn patch(&self, slot: &str) -> PatchState {
        let runtime = lock(&self.state.runtime).unwrap();
        let patches = runtime.patches.clone().unwrap_or_default();
        if slot == "a" {
            patches.a
        } else {
            patches.b
        }
    }

    /// The engine answers a stage of the slot's current run.
    fn stage_done(&self, slot: &str, index: usize, cached: bool, seconds: f64) {
        let patch_id = lock(&self.state.patch).unwrap().slot(slot).patch_id.clone();
        let envelope = WorkerEnvelope {
            message_type: "patch_stage".into(),
            data: json!({
                "patch_id": patch_id, "stage_index": index, "stage_count": 3, "state": "done",
                "png_base64": base64::engine::general_purpose::STANDARD
                    .encode(format!("\u{89}PNG{index}{seconds}")),
                "width": 384, "height": 384, "elapsed_seconds": seconds, "cached": cached,
            }),
        };
        apply_message(&self.state, &envelope).unwrap();
    }
}

fn input() -> StartBatchInput {
    let mut input: StartBatchInput = serde_json::from_value(json!({
        "media_ids": ["media"], "batch_mode": false, "task": "upscale",
        "model_id": UPSCALER, "video_model_id": "frame_by_frame",
        "fix_stages": [], "custom_model_path": "", "output_directory": "",
        "output_format": "png", "output_scale": 4, "preserve_metadata": true,
        "jpeg_quality": 98, "device": "cpu", "tile_size": 256, "halo": 32,
        "precision": "fp32", "safe_memory": false, "deflicker": false,
        "deflicker_window": 3, "video_container": "mp4", "video_crf": 18,
        "enable_face_model": false, "face_fidelity": 70, "enable_live_preview": true,
        "allow_unsafe_pickle_model": false,
    }))
    .unwrap();
    input.fix_stages = vec![FixStageSelection {
        fix: "noise".into(),
        model_id: NOISE.into(),
        ..Default::default()
    }];
    input
}

fn two_fixes() -> StartBatchInput {
    let mut input = input();
    input.fix_stages.insert(
        0,
        FixStageSelection {
            fix: "jpeg".into(),
            model_id: JPEG.into(),
            ..Default::default()
        },
    );
    input
}

#[test]
fn a_patch_is_refused_while_a_job_runs_or_waits_and_nothing_is_sent() {
    let harness = Harness::new();
    lock(&harness.state.runtime).unwrap().active_job_id = "job".into();
    request_checked(&harness.state, harness.ask("a", input())).unwrap();
    assert_eq!(harness.patch("a").state, "busy");
    assert!(harness.messages().is_empty());

    lock(&harness.state.runtime).unwrap().active_job_id.clear();
    lock(&harness.state.database)
        .unwrap()
        .admit_jobs(&["media"], |_| {
            Ok(vec![NewJob {
                id: "queued".into(),
                media_id: "media".into(),
                request_json: "{}".into(),
                ui_json: String::new(),
            }])
        })
        .unwrap();
    request_checked(&harness.state, harness.ask("b", input())).unwrap();
    assert_eq!(harness.patch("b").state, "busy");
    assert!(harness.messages().is_empty());
}

#[test]
fn a_valid_request_sends_the_stages_the_job_would() {
    let harness = Harness::new();
    request_checked(&harness.state, harness.ask("a", two_fixes())).unwrap();
    let sent = harness.messages();
    assert_eq!(sent.len(), 1);
    assert_eq!(sent[0]["type"], "patch_request");
    let data = &sent[0]["data"];
    assert_eq!(data["slot"], "a");
    assert_eq!(data["frame_index"], -1);
    assert_eq!(data["padding"], 32);
    assert_eq!(data["output_scale"], 4);
    assert_eq!(data["region"]["width"], 384);
    assert_eq!(harness.patch("a").state, "running");
    assert_eq!(harness.patch("a").steps.len(), 3);

    // The same input as a job: the stages of the queued request are the patch's.
    let state = &harness.state;
    lock(&state.runtime).unwrap().active_job_id.clear();
    crate::commands::start_jobs_checked(state, two_fixes()).unwrap();
    let queued = lock(&state.database)
        .unwrap()
        .next_queued_job()
        .unwrap()
        .unwrap();
    let job: Value = serde_json::from_str(&queued.request_json).unwrap();
    assert_eq!(job["data"]["stages"], data["stages"]);
}

fn add_video(harness: &Harness) {
    let clip = harness._directory.path().join("clip.mp4");
    std::fs::write(&clip, "source").unwrap();
    harness.state.grants.grant_media_file(&clip);
    let database = lock(&harness.state.database).unwrap();
    database
        .insert_media("clip", clip.to_str().unwrap(), "clip.mp4", "video")
        .unwrap();
    database
        .update_media_probe(
            clip.to_str().unwrap(),
            "video",
            1280,
            720,
            240,
            24.0,
            10.0,
            "preview",
            "",
            "",
            true,
        )
        .unwrap();
}

fn video_ask(harness: &Harness, input: StartBatchInput) -> PatchAsk {
    let mut ask = harness.ask("a", input);
    ask.media_id = "clip".into();
    ask
}

#[test]
fn a_video_patch_uses_its_first_frame_and_leaves_out_deflicker_frames_and_audio() {
    let harness = Harness::new();
    add_video(&harness);
    let mut video = input();
    video.task = "video".into();
    video.video_model_id = "frame_by_frame".into();
    video.deflicker = true;
    video.video_interpolation = "2x".into();
    video.video_clean_audio = true;
    request_checked(&harness.state, video_ask(&harness, video)).unwrap();
    let sent = harness.messages();
    assert_eq!(sent.len(), 1);
    let data = &sent[0]["data"];
    assert_eq!(data["frame_index"], 0);
    assert!(data["image_path"].as_str().unwrap().ends_with("clip.mp4"));
    // The fix and the primary upscale: the same steps a photo gets.
    let stages = data["stages"].as_array().unwrap();
    assert_eq!(stages.len(), 2);
    assert!(stages
        .iter()
        .all(|stage| !["deflicker", "interpolate", "audio_denoise"]
            .contains(&stage["kind"].as_str().unwrap_or_default())));
    assert_eq!(data["output_scale"], 4);
}

#[test]
fn a_temporal_video_engine_gets_no_patch() {
    let harness = Harness::new();
    add_video(&harness);
    let mut video = input();
    video.task = "video".into();
    video.video_model_id = "seedvr2_3b".into();
    let error = request_checked(&harness.state, video_ask(&harness, video)).unwrap_err();
    assert!(error.to_string().contains("frame-by-frame"));
    assert!(harness.messages().is_empty());
}

#[test]
fn a_stage_limit_sends_that_many_and_a_missing_model_stops_the_run() {
    let harness = Harness::new();
    let mut ask = harness.ask("a", two_fixes());
    ask.stage_limit = Some(1);
    request_checked(&harness.state, ask).unwrap();
    let sent = harness.messages();
    assert_eq!(sent[0]["data"]["stages"].as_array().unwrap().len(), 1);
    let states: Vec<String> = harness
        .patch("a")
        .steps
        .iter()
        .map(|s| s.state.clone())
        .collect();
    assert_eq!(states, ["waiting", "waiting", "waiting"]);

    // Noise is not installed: the steps before it run, it is named, the rest wait.
    {
        let mut catalog = lock(&harness.state.catalog).unwrap();
        for model in &mut catalog.models {
            if model.model_id == NOISE {
                model.installed = false;
            }
        }
    }
    request_checked(&harness.state, harness.ask("a", two_fixes())).unwrap();
    let sent = harness.messages();
    let request = sent.iter().find(|m| m["type"] == "patch_request").unwrap();
    assert_eq!(request["data"]["stages"].as_array().unwrap().len(), 1);
    let steps = harness.patch("a").steps;
    assert_eq!(steps[1].state, "missing");
    assert_eq!(steps[1].model_id, NOISE);
    assert_eq!(steps[2].state, "waiting");
}

#[test]
fn a_second_request_for_the_slot_cancels_the_first() {
    let harness = Harness::new();
    request_checked(&harness.state, harness.ask("a", input())).unwrap();
    let first = harness.messages()[0]["data"]["patch_id"].clone();
    request_checked(&harness.state, harness.ask("a", two_fixes())).unwrap();
    let sent = harness.messages();
    assert_eq!(sent[0]["type"], "cancel_request");
    assert_eq!(sent[0]["data"]["job_id"], first);
    assert_eq!(sent[1]["type"], "patch_request");
    assert_ne!(sent[1]["data"]["patch_id"], first);
    // The cancelled run's answers no longer count.
    let late = WorkerEnvelope {
        message_type: "patch_failed".into(),
        data: json!({"patch_id": first, "error_message": "x"}),
    };
    apply_message(&harness.state, &late).unwrap();
    assert_eq!(harness.patch("a").state, "running");
}

#[test]
fn a_job_being_dispatched_stops_the_patches_and_marks_both_slots_busy() {
    let harness = Harness::new();
    request_checked(&harness.state, harness.ask("a", input())).unwrap();
    let id = harness.messages()[0]["data"]["patch_id"].clone();
    job_dispatched(&harness.state);
    let sent = harness.messages();
    assert_eq!(sent.len(), 1);
    assert_eq!(sent[0]["type"], "cancel_request");
    assert_eq!(sent[0]["data"]["job_id"], id);
    assert_eq!(harness.patch("a").state, "busy");
    assert_eq!(harness.patch("b").state, "busy");
}

#[test]
fn a_finished_step_is_a_png_in_the_preview_folder() {
    let harness = Harness::new();
    request_checked(&harness.state, harness.ask("a", two_fixes())).unwrap();
    harness.stage_done("a", 0, false, 1.5);
    let step = harness.patch("a").steps[0].clone();
    assert_eq!(step.state, "done");
    assert_eq!((step.width, step.height), (384, 384));
    assert!(step.url.ends_with(".png"));
    let path =
        crate::preview_cache::confined_preview_path(&harness.state.paths.preview_root, &step.url)
            .unwrap();
    assert!(path.is_file());
    // The picture is referenced, so a sweep keeps it.
    let database = lock(&harness.state.database).unwrap();
    let runtime = lock(&harness.state.runtime).unwrap();
    crate::preview_cache::sweep_unreferenced(
        &harness.state.paths.preview_root,
        &database,
        Some(&runtime),
    )
    .unwrap();
    assert!(path.is_file());
    drop((database, runtime));

    let ended = WorkerEnvelope {
        message_type: "patch_completed".into(),
        data: json!({"patch_id": lock(&harness.state.patch).unwrap().slot("a").patch_id.clone(), "elapsed_seconds": 2.0}),
    };
    apply_message(&harness.state, &ended).unwrap();
    assert_eq!(harness.patch("a").state, "ready");
}

#[test]
fn media_without_a_grant_is_refused() {
    let harness = Harness::new();
    let other = harness._directory.path().join("other.png");
    std::fs::write(&other, "x").unwrap();
    {
        let database = lock(&harness.state.database).unwrap();
        database
            .insert_media("loose", other.to_str().unwrap(), "other.png", "image")
            .unwrap();
        database
            .update_media_probe(
                other.to_str().unwrap(),
                "image",
                2000,
                1500,
                1,
                0.0,
                0.0,
                "preview",
                "",
                "",
                true,
            )
            .unwrap();
    }
    let mut ask = harness.ask("a", input());
    ask.media_id = "loose".into();
    assert!(request_checked(&harness.state, ask).is_err());
    assert!(harness.messages().is_empty());
}

#[test]
fn the_setting_decides_whether_a_request_is_refused_waits_or_runs() {
    let harness = Harness::new();
    harness.mode("off");
    assert!(request_checked(&harness.state, harness.ask("a", input())).is_err());
    assert!(harness.messages().is_empty());

    harness.mode("manual");
    request_checked(&harness.state, harness.ask("a", input())).unwrap();
    assert_eq!(harness.patch("a").state, "stale");
    assert!(harness.messages().is_empty());

    harness.mode("live");
    request_checked(&harness.state, harness.ask("a", input())).unwrap();
    assert_eq!(harness.patch("a").state, "running");
    assert_eq!(harness.messages().len(), 1);
}

fn measured(harness: &Harness, noise: f64, upscale: f64) {
    let database = lock(&harness.state.database).unwrap();
    database.record_patch_seconds(NOISE, "cpu", noise).unwrap();
    database
        .record_patch_seconds(UPSCALER, "cpu", upscale)
        .unwrap();
}

#[test]
fn auto_runs_what_is_predicted_at_five_seconds_or_less() {
    // No timings: sent.
    let harness = Harness::new();
    request_checked(&harness.state, harness.ask("a", input())).unwrap();
    assert_eq!(harness.patch("a").state, "running");

    // 2 s and 2 s: sent.
    let harness = Harness::new();
    measured(&harness, 2.0, 2.0);
    request_checked(&harness.state, harness.ask("a", input())).unwrap();
    assert_eq!(harness.patch("a").state, "running");

    // 4 s and 3 s: waits, with the estimate.
    let harness = Harness::new();
    measured(&harness, 4.0, 3.0);
    request_checked(&harness.state, harness.ask("a", input())).unwrap();
    let patch = harness.patch("a");
    assert_eq!(patch.state, "stale");
    assert_eq!(patch.estimate_seconds, 7.0);
    assert!(harness.messages().is_empty());

    // force: sent in every mode but off.
    let mut ask = harness.ask("a", input());
    ask.force = true;
    request_checked(&harness.state, ask).unwrap();
    assert_eq!(harness.patch("a").state, "running");
    assert_eq!(harness.patch("a").estimate_seconds, 0.0);
    harness.mode("manual");
    let mut ask = harness.ask("a", input());
    ask.force = true;
    request_checked(&harness.state, ask).unwrap();
    assert_eq!(harness.patch("a").state, "running");
    harness.mode("off");
    let mut ask = harness.ask("a", input());
    ask.force = true;
    assert!(request_checked(&harness.state, ask).is_err());
}

#[test]
fn the_steps_already_shown_cost_nothing_again() {
    let harness = Harness::new();
    request_checked(&harness.state, harness.ask("a", input())).unwrap();
    harness.stage_done("a", 0, true, 0.0);
    // The first step would take 9 s, the second 4 s: only the second is still to run.
    measured(&harness, 9.0, 4.0);
    request_checked(&harness.state, harness.ask("a", input())).unwrap();
    assert_eq!(harness.patch("a").state, "running");
    // Another position shows nothing: both steps run, 13 s.
    let mut ask = harness.ask("a", input());
    ask.region.x = 320;
    request_checked(&harness.state, ask).unwrap();
    let patch = harness.patch("a");
    assert_eq!(patch.state, "stale");
    assert_eq!(patch.estimate_seconds, 13.0);
}

fn with_strength(mut input: StartBatchInput, strength: f64) -> StartBatchInput {
    input.fix_stages[0].params = Some(serde_json::Map::from_iter([(
        "strength".into(),
        json!(strength),
    )]));
    input
}

#[test]
fn a_strength_change_costs_nothing_by_itself() {
    let harness = Harness::new();
    request_checked(&harness.state, harness.ask("a", input())).unwrap();
    harness.stage_done("a", 0, false, 9.0);
    harness.stage_done("a", 1, false, 9.0);
    // The model would take 9 s and the upscaler 9 s; only the mix is new, and the upscaler
    // after it must run again because its input changed.
    measured(&harness, 9.0, 1.0);
    harness.messages();
    request_checked(
        &harness.state,
        harness.ask("a", with_strength(input(), 0.4)),
    )
    .unwrap();
    let patch = harness.patch("a");
    assert_eq!(patch.state, "running");
    assert_eq!(patch.estimate_seconds, 0.0);
    assert_eq!(
        harness.messages().last().unwrap()["data"]["stages"]
            .as_array()
            .unwrap()
            .len(),
        2
    );

    // A slow step after the mix: the request is cut after the mix and the rest waits.
    let harness = Harness::new();
    request_checked(&harness.state, harness.ask("a", input())).unwrap();
    harness.stage_done("a", 0, false, 9.0);
    harness.stage_done("a", 1, false, 9.0);
    measured(&harness, 9.0, 9.0);
    harness.messages();
    request_checked(
        &harness.state,
        harness.ask("a", with_strength(input(), 0.4)),
    )
    .unwrap();
    let patch = harness.patch("a");
    assert_eq!(patch.state, "running");
    assert_eq!(patch.estimate_seconds, 9.0);
    assert_eq!(
        harness.messages().last().unwrap()["data"]["stages"]
            .as_array()
            .unwrap()
            .len(),
        1
    );
    let patch_id = lock(&harness.state.patch)
        .unwrap()
        .slot("a")
        .patch_id
        .clone();
    apply_message(
        &harness.state,
        &WorkerEnvelope {
            message_type: "patch_completed".into(),
            data: json!({"patch_id": patch_id, "elapsed_seconds": 0.1}),
        },
    )
    .unwrap();
    assert_eq!(harness.patch("a").state, "stale");

    // Under Manual nothing is sent, as before.
    let harness = Harness::new();
    request_checked(&harness.state, harness.ask("a", input())).unwrap();
    harness.stage_done("a", 0, false, 9.0);
    harness.stage_done("a", 1, false, 9.0);
    measured(&harness, 9.0, 9.0);
    harness.mode("manual");
    harness.messages();
    request_checked(
        &harness.state,
        harness.ask("a", with_strength(input(), 0.4)),
    )
    .unwrap();
    assert_eq!(harness.patch("a").state, "stale");
    // The old run is cancelled; no new request goes out.
    assert!(harness
        .messages()
        .iter()
        .all(|message| message["type"] != "patch_request"));
}

#[test]
fn timings_are_the_mean_of_the_last_three_real_runs() {
    let harness = Harness::new();
    {
        let database = lock(&harness.state.database).unwrap();
        for seconds in [2.0, 4.0, 6.0] {
            database
                .record_patch_seconds(NOISE, "cpu", seconds)
                .unwrap();
        }
        assert_eq!(database.patch_seconds(NOISE, "cpu").unwrap(), Some(4.0));
        assert_eq!(database.patch_seconds(NOISE, "mps").unwrap(), None);
        assert_eq!(
            database
                .estimate_request_seconds("{}", &sample_media())
                .unwrap(),
            None
        );
    }
    request_checked(&harness.state, harness.ask("a", input())).unwrap();
    // An answer from the cache changes nothing.
    harness.stage_done("a", 0, true, 30.0);
    // A real run does.
    harness.stage_done("a", 0, false, 8.0);
    let database = lock(&harness.state.database).unwrap();
    assert_eq!(database.patch_seconds(NOISE, "cpu").unwrap(), Some(6.0));
    // Four runs: the oldest drops out.
    database.record_patch_seconds(NOISE, "cpu", 10.0).unwrap();
    assert_eq!(database.patch_seconds(NOISE, "cpu").unwrap(), Some(8.0));
}

fn sample_media() -> crate::types::MediaItem {
    crate::types::MediaItem {
        id: "m".into(),
        path: "p".into(),
        name: "n".into(),
        kind: "image".into(),
        width: 100,
        height: 100,
        frame_count: 1,
        fps: 0.0,
        duration_seconds: 0.0,
        preview_data_url: String::new(),
        probe_status: "ready".into(),
        hdr_format: String::new(),
        audio_warning: String::new(),
        has_audio: true,
        error: String::new(),
        selected: false,
    }
}

#[test]
fn variant_b_swaps_one_model_and_leaves_a_alone() {
    let harness = Harness::new();
    let mut ask = harness.ask("b", input());
    ask.replace = Some(PatchReplace {
        slot: "b".into(),
        fix: "noise".into(),
        model_id: JPEG.into(),
    });
    // JPEG is not a noise model, so the swap is refused the way Start refuses it.
    assert!(request_checked(&harness.state, ask).is_err());
    let mut ask = harness.ask("a", input());
    ask.replace = Some(PatchReplace {
        slot: "b".into(),
        fix: "upscale".into(),
        model_id: "hat_l_x4_imagenet".into(),
    });
    request_checked(&harness.state, ask).unwrap();
    assert_eq!(harness.patch("a").steps[1].model_id, UPSCALER);
    // B's own request carries the other model; it is not installed, so the run stops there.
    let mut ask = harness.ask("b", input());
    ask.replace = Some(PatchReplace {
        slot: "b".into(),
        fix: "upscale".into(),
        model_id: "hat_l_x4_imagenet".into(),
    });
    request_checked(&harness.state, ask).unwrap();
    let steps = harness.patch("b").steps;
    assert_eq!(steps[1].model_id, "hat_l_x4_imagenet");
    assert_eq!(steps[1].state, "missing");
}

#[test]
fn cancelling_a_slot_stops_its_run() {
    let harness = Harness::new();
    request_checked(&harness.state, harness.ask("a", input())).unwrap();
    let id = harness.messages()[0]["data"]["patch_id"].clone();
    cancel_checked(&harness.state, "a").unwrap();
    let sent = harness.messages();
    assert_eq!(sent[0]["data"]["job_id"], id);
    assert_eq!(harness.patch("a").state, "idle");
    assert!(cancel_checked(&harness.state, "c").is_err());
}
