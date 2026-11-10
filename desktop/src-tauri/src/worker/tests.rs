use super::*;

#[test]
fn shutdown_supervisor_cleans_scratch() {
    let directory = tempfile::tempdir().unwrap();
    let worker = crate::state::WorkerControl::default();
    let mut message =
        json!({"type":"job_request", "data":{"output_path":directory.path().join("output.png")}});
    worker
        .cancellation
        .lock()
        .unwrap()
        .prepare("job", &mut message)
        .unwrap();
    let scratch = std::path::PathBuf::from(message["data"]["scratch_directory"].as_str().unwrap());
    std::fs::write(scratch.join("partial"), b"unfinished").unwrap();
    worker.shutting_down.store(true, Ordering::SeqCst);
    assert!(supervisor_should_return(&worker, 0));
    assert!(!scratch.exists(), "shutdown must remove scratch");
}

#[test]
fn the_apple_gpu_reports_whether_the_neural_engine_is_available() {
    let mut reported = serde_json::json!({
        "id": "mps",
        "type": "mps",
        "name": "Apple GPU (Metal)",
        "total_memory": 1,
        "free_memory": 1,
        "supports_fp16": true,
        "recommended_tile_sizes": [256],
        "neural_engine": true
    });
    assert!(
        serde_json::from_value::<DeviceInfo>(reported.clone())
            .unwrap()
            .neural_engine
    );
    reported.as_object_mut().unwrap().remove("neural_engine");
    assert!(
        !serde_json::from_value::<DeviceInfo>(reported)
            .unwrap()
            .neural_engine
    );
}

#[test]
fn a_new_installation_starts_on_the_gpu_but_not_on_directml() {
    let device = |id: &str| DeviceInfo {
        id: id.into(),
        ..DeviceInfo::default()
    };
    assert_eq!(
        first_run_device(&[device("cpu"), device("mps")]).as_deref(),
        Some("mps")
    );
    assert_eq!(
        first_run_device(&[device("cpu"), device("cuda:0")]).as_deref(),
        Some("cuda:0")
    );
    assert_eq!(
        first_run_device(&[device("cpu"), device("directml:0")]),
        None
    );
}

#[test]
fn video_stages_have_titles_including_frame_interpolation() {
    assert_eq!(
        video_stage_title("loading_interpolation"),
        "Loading frame interpolation"
    );
    assert_eq!(video_stage_title("saving"), "Saving video");
    assert_eq!(video_stage_title("something_new"), "Processing video");
}

#[test]
fn protocol_values_are_tolerant_of_missing_optional_fields() {
    let value = json!({"job_id": "job-1"});
    assert_eq!(string(&value, "job_id"), "job-1");
    assert_eq!(number(&value, "percentage"), 0.0);
    assert_eq!(integer(&value, "frame_count"), 0);
}

#[test]
fn worker_death_records_the_last_confirmed_video_stage() {
    let detail = worker_interruption_detail(
        "The inference engine exited with status 1",
        "Enhancing video · Labs",
    );
    assert!(detail.contains("Last confirmed stage: Enhancing video · Labs"));
    assert!(!detail.contains("post-processing"));
}

#[test]
fn worker_envelope_round_trips_protocol_type() {
    let envelope: WorkerEnvelope = serde_json::from_value(json!({
        "type": "worker_ready",
        "data": {}
    }))
    .unwrap();
    assert_eq!(envelope.message_type, "worker_ready");
    assert_eq!(
        serde_json::to_value(envelope).unwrap()["type"],
        "worker_ready"
    );
}

#[test]
fn high_frequency_messages_do_not_emit_duplicate_full_snapshots() {
    for message_type in [
        "progress",
        "tile_update",
        "video_frame_started",
        "video_frame_completed",
        "video_memory",
    ] {
        assert!(!should_emit_state_changed(message_type));
    }
    for message_type in ["job_started", "job_completed", "preview_ready", "warning"] {
        assert!(should_emit_state_changed(message_type));
    }
}

#[test]
fn high_frequency_worker_events_are_sampled_but_final_progress_is_forwarded() {
    let started = Instant::now();
    let mut gate = WorkerEventGate::default();
    let tile = |completed| WorkerEnvelope {
        message_type: "tile_update".into(),
        data: json!({
            "job_id": "job-1",
            "phase": "completed",
            "completed_tiles": completed,
            "total_tiles": 100,
            "jpeg_base64": "tile"
        }),
    };
    let progress = |completed| WorkerEnvelope {
        message_type: "progress".into(),
        data: json!({
            "job_id": "job-1",
            "completed_tiles": completed,
            "total_tiles": 100
        }),
    };

    assert!(gate.should_forward(&tile(1), started));
    assert!(!gate.should_forward(&tile(2), started + Duration::from_millis(5)));
    assert!(gate.should_forward(&tile(3), started + Duration::from_millis(80)));
    assert!(gate.should_forward(&tile(100), started + Duration::from_millis(81)));

    assert!(gate.should_forward(&progress(1), started));
    assert!(!gate.should_forward(&progress(2), started + Duration::from_millis(10)));
    assert!(gate.should_forward(&progress(100), started + Duration::from_millis(11)));

    let first_region = WorkerEnvelope {
        message_type: "tile_update".into(),
        data: json!({"job_id": "job-1", "phase": "started", "completed_tiles": 0}),
    };
    assert!(gate.should_forward(&first_region, started));
    // A new frame or VAE/DiT stage must establish its geometry even when
    // its first region arrives inside the normal animation throttle.
    assert!(gate.should_forward(&first_region, started + Duration::from_millis(1)));
    for index in 1..20 {
        for phase in ["started", "completed"] {
            let position = WorkerEnvelope {
                message_type: "tile_update".into(),
                data: json!({"job_id": "job-1", "phase": phase, "completed_tiles": index,
                        "output_x": index * 256, "jpeg_base64": ""}),
            };
            assert!(gate.should_forward(&position, started + Duration::from_millis(index)));
        }
    }
}

#[test]
fn ordinary_progress_preserves_the_active_image_stage() {
    let directory = tempfile::tempdir().unwrap();
    let state =
        Arc::new(AppState::from_paths(crate::paths::AppPaths::under(directory.path())).unwrap());
    let title_after_progress = |index: u32, count: u32| {
        for (kind, data) in [
            (
                "stage_started",
                json!({"job_id":"j", "stage_index":index, "stage_count":count, "stage_kind":"restore", "model_id":"noise"}),
            ),
            (
                "progress",
                json!({"job_id":"j", "percentage":45, "completed_tiles":1, "total_tiles":4}),
            ),
        ] {
            apply_worker_envelope(
                &state,
                &WorkerEnvelope {
                    message_type: kind.into(),
                    data,
                },
            )
            .unwrap();
        }
        lock(&state.runtime).unwrap().status_title.clone()
    };
    // Several fixes can share a kind; the title keeps which stage is running.
    assert_eq!(title_after_progress(2, 5), "Restoring · stage 3/5");
    // A one-stage job reads as before.
    assert_eq!(title_after_progress(0, 1), "Enhancing");
}

#[test]
fn selected_image_comparison_ignores_late_results_while_another_job_runs() {
    let directory = tempfile::tempdir().unwrap();
    let state =
        Arc::new(AppState::from_paths(crate::paths::AppPaths::under(directory.path())).unwrap());
    {
        let mut runtime = lock(&state.runtime).unwrap();
        runtime.active_job_id = "running-third".into();
        runtime.comparison_media_id = "second".into();
        runtime.comparison_output_path = "/results/second.png".into();
    }
    for (path, encoded) in [
        ("/results/first.png", "old"),
        ("/results/second.png", "correct"),
    ] {
        apply_worker_envelope(
            &state,
            &WorkerEnvelope {
                message_type: "preview_ready".into(),
                data: json!({"image_path": path, "jpeg_base64": encoded}),
            },
        )
        .unwrap();
    }
    apply_worker_envelope(
        &state,
        &WorkerEnvelope {
            message_type: "preview_ready".into(),
            data: json!({"image_path": "/results/first.png", "jpeg_base64": "late"}),
        },
    )
    .unwrap();
    let runtime = lock(&state.runtime).unwrap();
    assert_eq!(
        runtime.comparison_preview_data_url,
        "data:image/jpeg;base64,correct"
    );
    assert_eq!(runtime.comparison_media_id, "second");
    assert!(runtime.result_preview_data_url.is_empty());
    assert_eq!(runtime.active_job_id, "running-third");
}

#[test]
fn compare_answers_and_snapshots_revalidate_the_selected_job() {
    use crate::compare::{ComparePick, CompareStatus};
    let directory = tempfile::tempdir().unwrap();
    let paths = crate::paths::AppPaths::under(directory.path());
    let state = Arc::new(AppState::from_paths(paths.clone()).unwrap());
    let input = directory.path().join("input.png");
    let output = directory.path().join("output.png");
    std::fs::write(&input, "input").unwrap();
    std::fs::write(&output, "output").unwrap();
    let request = {
        let db = lock(&state.database).unwrap();
        db.insert_media("a", input.to_str().unwrap(), "a", "image")
            .unwrap();
        db.insert_job("job", "a", "{}").unwrap();
        db.finish_job("job", "completed", output.to_str().unwrap(), "")
            .unwrap();
        let mut runtime = lock(&state.runtime).unwrap();
        crate::compare::plan(
            &db,
            &mut runtime.compare,
            &[ComparePick::Output {
                media_id: "a".into(),
                job_id: "job".into(),
            }],
        )
        .unwrap()
        .remove(0)
    };
    let mut data = request["data"].clone();
    data["jpeg_base64"] = json!("decoded");
    apply_worker_envelope(
        &state,
        &WorkerEnvelope {
            message_type: "preview_ready".into(),
            data: data.clone(),
        },
    )
    .unwrap();
    let snapshot = state.snapshot().unwrap();
    assert_eq!(
        snapshot.runtime.compare.slots[0].status,
        CompareStatus::Ready
    );
    assert!(
        serde_json::to_value(&snapshot.runtime.compare).unwrap()["slots"][0]
            .get("path")
            .is_none()
    );
    rusqlite::Connection::open(&paths.database)
        .unwrap()
        .execute("DELETE FROM jobs WHERE id='job'", [])
        .unwrap();
    assert_eq!(
        state.snapshot().unwrap().runtime.compare.slots[0].status,
        CompareStatus::Missing
    );
    apply_worker_envelope(
        &state,
        &WorkerEnvelope {
            message_type: "preview_ready".into(),
            data,
        },
    )
    .unwrap();
    let snapshot = state.snapshot().unwrap();
    assert_eq!(
        snapshot.runtime.compare.slots[0].status,
        CompareStatus::Missing
    );
    assert!(snapshot.runtime.compare.slots[0]
        .preview_data_url
        .is_empty());
}

#[test]
fn pair_previews_reject_stale_success_and_failure_even_for_the_same_path() {
    let directory = tempfile::tempdir().unwrap();
    let state =
        Arc::new(AppState::from_paths(crate::paths::AppPaths::under(directory.path())).unwrap());
    {
        let mut runtime = lock(&state.runtime).unwrap();
        runtime.comparison_pair.generation = 2;
        runtime.comparison_pair.showing.target_id = "job-a".into();
        runtime.comparison_pair.showing.output_path = "/a.png".into();
        runtime.comparison_pair.against.target_id = "job-b".into();
        runtime.comparison_pair.against.output_path = "/b.png".into();
    }
    for (slot, target, path) in [
        ("showing", "job-a", "/a.png"),
        ("against", "job-b", "/b.png"),
    ] {
        apply_worker_envelope(&state, &WorkerEnvelope { message_type:"preview_ready".into(), data:json!({"slot":slot,"target_id":target,"generation":2,"image_path":path,"jpeg_base64":"correct","width":800,"height":600,"preview_width":400,"preview_height":300}) }).unwrap();
        for generation in [1, 3] {
            for kind in ["preview_ready", "preview_failed"] {
                apply_worker_envelope(&state, &WorkerEnvelope { message_type:kind.into(), data:json!({"slot":slot,"target_id":target,"generation":generation,"image_path":path,"jpeg_base64":"stale","error_message":"stale"}) }).unwrap();
            }
        }
    }
    let runtime = lock(&state.runtime).unwrap();
    for slot in [
        &runtime.comparison_pair.showing,
        &runtime.comparison_pair.against,
    ] {
        assert_eq!(slot.preview_data_url, "data:image/jpeg;base64,correct");
        assert!(slot.error.is_empty());
        assert_eq!(
            (
                slot.width,
                slot.height,
                slot.preview_width,
                slot.preview_height
            ),
            (800, 600, 400, 300)
        );
    }
}

#[test]
fn pair_previews_need_the_same_target_and_file_and_show_failures_per_side() {
    let directory = tempfile::tempdir().unwrap();
    let state =
        Arc::new(AppState::from_paths(crate::paths::AppPaths::under(directory.path())).unwrap());
    {
        let mut runtime = lock(&state.runtime).unwrap();
        runtime.comparison_pair.generation = 4;
        runtime.comparison_pair.media_id = "photo".into();
        runtime.comparison_pair.showing.target_id = "original".into();
        runtime.comparison_pair.against.target_id = "job-b".into();
        runtime.comparison_pair.against.output_path = "/b.png".into();
    }
    let answer = |kind: &str, data: Value| {
        apply_worker_envelope(
            &state,
            &WorkerEnvelope {
                message_type: kind.into(),
                data,
            },
        )
        .unwrap()
    };
    // The Original side is never filled from the result decoder.
    answer(
        "preview_ready",
        json!({"slot":"showing","target_id":"original","generation":4,"image_path":"","jpeg_base64":"x"}),
    );
    // Another target, or another file under the right target, is dropped.
    answer(
        "preview_ready",
        json!({"slot":"against","target_id":"job-c","generation":4,"image_path":"/b.png","jpeg_base64":"x"}),
    );
    answer(
        "preview_ready",
        json!({"slot":"against","target_id":"job-b","generation":4,"image_path":"/c.png","jpeg_base64":"x"}),
    );
    {
        let runtime = lock(&state.runtime).unwrap();
        assert!(runtime.comparison_pair.showing.preview_data_url.is_empty());
        assert!(runtime.comparison_pair.against.preview_data_url.is_empty());
        // A slot answer never fills the automatic or single comparison.
        assert!(runtime.comparison_preview_data_url.is_empty());
    }
    answer(
        "preview_failed",
        json!({"slot":"against","target_id":"job-b","generation":4,"image_path":"/b.png","error_message":"truncated file"}),
    );
    let runtime = lock(&state.runtime).unwrap();
    assert_eq!(runtime.comparison_pair.against.error, "truncated file");
    assert!(runtime.comparison_pair.against.preview_data_url.is_empty());
}

#[test]
fn completion_and_comparison_previews_share_one_bound() {
    let image = WorkerEnvelope {
        message_type: "job_completed".into(),
        data: json!({"job_id": "j", "output_path": "/output/enhanced.png"}),
    };
    assert_eq!(
        completion_preview_request(&idle_state(), &image).unwrap()["data"]["max_dimension"],
        RESULT_PREVIEW_MAX_DIMENSION
    );
}

#[test]
fn a_completed_job_keeps_its_note_on_the_status_line() {
    let directory = tempfile::tempdir().unwrap();
    let state =
        Arc::new(AppState::from_paths(crate::paths::AppPaths::under(directory.path())).unwrap());
    let completed = |notes: Value| WorkerEnvelope {
        message_type: "job_completed".into(),
        data: json!({"job_id": "job", "output_path": "/out.png", "notes": notes}),
    };
    {
        let database = lock(&state.database).unwrap();
        database
            .insert_media("media", "/in.png", "in.png", "image")
            .unwrap();
        database
            .insert_job("job", "media", r#"{"type":"job_request","data":{}}"#)
            .unwrap();
    }
    apply_worker_envelope(&state, &completed(json!("The fix was skipped."))).unwrap();
    assert_eq!(
        lock(&state.runtime).unwrap().status_detail,
        "The fix was skipped."
    );
    // Kept with the job too, where the next refresh and the history read it.
    let jobs = lock(&state.database).unwrap().list_jobs().unwrap();
    let job = jobs.iter().find(|job| job.id == "job").unwrap();
    assert_eq!(
        (job.status.as_str(), job.error.as_str()),
        ("completed", "The fix was skipped.")
    );
    apply_worker_envelope(&state, &completed(Value::Null)).unwrap();
    assert_eq!(
        lock(&state.runtime).unwrap().status_detail,
        "The output was written successfully."
    );
}

#[test]
fn the_engine_route_note_outlives_progress_until_the_job_ends() {
    let directory = tempfile::tempdir().unwrap();
    let state =
        Arc::new(AppState::from_paths(crate::paths::AppPaths::under(directory.path())).unwrap());
    let envelope = |message_type: &str, data: Value| WorkerEnvelope {
        message_type: message_type.into(),
        data,
    };
    let note = "SPAN does not run on Intel AI Boost (NPU), so it runs on the CPU.";
    apply_worker_envelope(&state, &envelope("job_started", json!({"job_id": "job"}))).unwrap();
    // A note for another job (a late message) changes nothing.
    apply_worker_envelope(
        &state,
        &envelope(
            "engine_status",
            json!({"job_id": "old", "message": "HAT runs on the GPU."}),
        ),
    )
    .unwrap();
    assert!(lock(&state.runtime).unwrap().hardware_note.is_empty());
    apply_worker_envelope(
        &state,
        &envelope("engine_status", json!({"job_id": "job", "message": note})),
    )
    .unwrap();
    assert_eq!(lock(&state.runtime).unwrap().status_detail, note);
    apply_worker_envelope(
        &state,
        &envelope(
            "progress",
            json!({"job_id": "job", "percentage": 12.0, "completed_tiles": 3, "total_tiles": 25}),
        ),
    )
    .unwrap();
    {
        let runtime = lock(&state.runtime).unwrap();
        assert_eq!(runtime.status_detail, "3 of 25 tiles");
        assert_eq!(runtime.hardware_note, note);
    }
    // The snapshot the interface refreshes from carries the note.
    let snapshot = serde_json::to_value(&*lock(&state.runtime).unwrap()).unwrap();
    assert_eq!(snapshot["hardware_note"], note);
    apply_worker_envelope(
        &state,
        &envelope(
            "job_failed",
            json!({"job_id": "job", "error_message": "The engine stopped."}),
        ),
    )
    .unwrap();
    assert!(lock(&state.runtime).unwrap().hardware_note.is_empty());
}

#[test]
fn a_stale_route_note_never_reaches_a_benchmark() {
    let directory = tempfile::tempdir().unwrap();
    let state =
        Arc::new(AppState::from_paths(crate::paths::AppPaths::under(directory.path())).unwrap());
    // A forced cancellation killed the engine mid-job; the next one is ready.
    lock(&state.runtime).unwrap().hardware_note =
        "RealESRGAN runs on Intel UHD Graphics 620 (DirectML), but slower than on the CPU here."
            .into();
    apply_worker_envelope(
        &state,
        &WorkerEnvelope {
            message_type: "benchmark_started".into(),
            data: json!({"job_id": "bench", "workload_version": "localsr-benchmark-v2"}),
        },
    )
    .unwrap();
    let runtime = lock(&state.runtime).unwrap();
    assert_eq!(runtime.active_job_id, "bench");
    assert!(runtime.hardware_note.is_empty());
}

#[test]
fn abandoning_or_replacing_the_active_job_drops_its_route_note() {
    // The supervisor path (a killed or crashed engine) and the dispatcher use these.
    let mut runtime = RuntimeStatus {
        active_job_id: "job".into(),
        hardware_note: "SPAN runs on the NPU.".into(),
        ..RuntimeStatus::default()
    };
    assert_eq!(runtime.take_active_job(), "job");
    assert!(runtime.active_job_id.is_empty() && runtime.hardware_note.is_empty());
    runtime.hardware_note = "SPAN runs on the NPU.".into();
    runtime.begin_active_job("next".into());
    assert_eq!(runtime.active_job_id, "next");
    assert!(runtime.hardware_note.is_empty());
}

#[test]
fn eta_suffix_is_compact_and_omits_unknown_values() {
    assert_eq!(eta_suffix(0.0), "");
    assert_eq!(eta_suffix(f64::NAN), "");
    assert_eq!(eta_suffix(7.4), " · ETA 7s");
    assert_eq!(eta_suffix(65.0), " · ETA 1:05");
}

#[test]
fn completion_requests_a_full_preview_for_the_finished_output() {
    let image = WorkerEnvelope {
        message_type: "job_completed".into(),
        data: json!({"output_path": "/output/enhanced.png"}),
    };
    assert_eq!(
        completion_preview_request(&idle_state(), &image),
        Some(json!({
            "type": "preview_request",
            "data": {"image_path": "/output/enhanced.png", "max_dimension": 2048}
        }))
    );

    let video = WorkerEnvelope {
        message_type: "video_job_completed".into(),
        data: json!({"output_path": "/output/enhanced.mp4"}),
    };
    let state = idle_state();
    assert_eq!(
        completion_preview_request(&state, &video),
        Some(json!({
            "type": "media_probe_request",
            "data": {"media_path": "/output/enhanced.mp4", "max_dimension": 2048, "external_ffmpeg": ""}
        }))
    );
    // A user-chosen FFmpeg goes along: the video it encoded may need it to open.
    lock(&state.settings).unwrap().external_ffmpeg_path = "/opt/tools/ffmpeg".into();
    assert_eq!(
        completion_preview_request(&state, &video).unwrap()["data"]["external_ffmpeg"],
        "/opt/tools/ffmpeg"
    );

    let incomplete = WorkerEnvelope {
        message_type: "job_completed".into(),
        data: json!({}),
    };
    assert_eq!(completion_preview_request(&idle_state(), &incomplete), None);
}

#[test]
fn legacy_capability_devices_default_to_discrete_memory() {
    let capability: CapabilityInfo = serde_json::from_value(json!({
        "system_ram_total": 16,
        "system_ram_available": 8,
        "system_memory_pressure_percent": 0.0,
        "system_memory_pressure_level": "unknown",
        "system_compressed_memory": 0,
        "system_swap_total": 0,
        "system_swap_used": 0,
        "devices": [{
            "id": "cpu",
            "type": "cpu",
            "name": "CPU",
            "total_memory": 16,
            "free_memory": 8,
            "supports_fp16": false,
            "recommended_tile_sizes": [64]
        }]
    }))
    .unwrap();

    assert!(!capability.devices[0].is_integrated);
}

#[test]
fn a_lost_gpu_holds_dispatch_until_the_engine_restarts() {
    let mut runtime = RuntimeStatus {
        worker: "ready".into(),
        ..RuntimeStatus::default()
    };
    hold_until_restart(&mut runtime, &json!({"job_id": "a", "error_message": "x"}));
    assert_eq!(runtime.worker, "ready");
    hold_until_restart(
        &mut runtime,
        &json!({"job_id": "a", "error_message": "x", "restart_required": true}),
    );
    assert_eq!(runtime.worker, "restarting");
}

#[test]
fn an_exited_engine_takes_no_more_work() {
    let mut runtime = RuntimeStatus {
        worker: "ready".into(),
        ..RuntimeStatus::default()
    };
    mark_exited(&mut runtime);
    assert_eq!(runtime.worker, "unavailable");
    runtime.worker = "restarting".into();
    mark_exited(&mut runtime);
    assert_eq!(runtime.worker, "restarting");
}

#[test]
fn only_a_native_fault_counts_as_a_crash() {
    #[cfg(unix)]
    use std::os::unix::process::ExitStatusExt;
    #[cfg(windows)]
    use std::os::windows::process::ExitStatusExt;
    use std::process::ExitStatus;

    #[cfg(unix)]
    let (clean, restart, failure, killed, faults) = (
        ExitStatus::from_raw(0),
        ExitStatus::from_raw(75 << 8),
        ExitStatus::from_raw(1 << 8),
        ExitStatus::from_raw(9), // SIGKILL, as from the OOM killer
        vec![ExitStatus::from_raw(11), ExitStatus::from_raw(6)], // SIGSEGV, SIGABRT
    );
    #[cfg(windows)]
    let (clean, restart, failure, killed, faults) = (
        ExitStatus::from_raw(0),
        ExitStatus::from_raw(75),
        ExitStatus::from_raw(2),
        ExitStatus::from_raw(1), // TerminateProcess from Task Manager
        vec![ExitStatus::from_raw(0xC000_0005), ExitStatus::from_raw(3)],
    );
    for status in [clean, restart, failure, killed] {
        assert!(!engine_crashed(&Ok(status)), "{status:?}");
    }
    for status in faults {
        assert!(engine_crashed(&Ok(status)), "{status:?}");
    }
    assert!(!engine_crashed(&Err(std::io::Error::other("no status"))));
}

#[test]
fn counters_at_the_largest_value_do_not_overflow() {
    for (kind, data) in [
        (
            "video_tile_progress",
            json!({"frame_index": u64::MAX, "total_frames": u64::MAX, "total_tiles": 1}),
        ),
        (
            "video_frame_started",
            json!({"frame_index": u64::MAX, "total_frames": 1}),
        ),
        (
            "video_stage_progress",
            json!({"job_id": "", "stage": "x", "frame_index": u64::MAX, "total_frames": 5}),
        ),
        (
            "stage_started",
            json!({"stage_index": u64::MAX, "stage_count": 1}),
        ),
    ] {
        let directory = tempfile::tempdir().unwrap();
        let state = Arc::new(
            AppState::from_paths(crate::paths::AppPaths::under(directory.path())).unwrap(),
        );
        apply_worker_envelope(&state, &envelope(kind, data)).unwrap();
        assert!(
            state.runtime.lock().is_ok(),
            "{kind} poisoned the runtime lock"
        );
    }
}

#[test]
fn percentages_and_times_from_the_engine_stay_in_range() {
    let directory = tempfile::tempdir().unwrap();
    let state = batch_state(directory.path());
    {
        let mut runtime = lock(&state.runtime).unwrap();
        runtime.active_job_id = "a".into();
    }
    for data in [
        json!({"job_id": "a", "percentage": 1.0e9, "elapsed_seconds": -50.0,
                   "estimated_remaining_seconds": -1.0e12}),
        json!({"job_id": "a", "percentage": -5.0}),
    ] {
        apply_worker_envelope(&state, &envelope("progress", data)).unwrap();
        let runtime = lock(&state.runtime).unwrap();
        assert!(
            (0.0..=100.0).contains(&runtime.progress),
            "{}",
            runtime.progress
        );
        assert!(runtime.elapsed_seconds >= 0.0);
        assert!(runtime.estimated_remaining_seconds >= 0.0);
    }
    apply_worker_envelope(
        &state,
        &envelope(
            "video_tile_progress",
            json!({"job_id": "a", "frame_index": 90, "total_frames": 10,
                       "estimated_remaining_seconds": -3.0}),
        ),
    )
    .unwrap();
    let runtime = lock(&state.runtime).unwrap();
    assert_eq!(runtime.progress, 100.0);
    assert_eq!(runtime.estimated_remaining_seconds, 0.0);
}

#[test]
fn an_image_side_larger_than_32_bits_saturates() {
    assert_eq!(
        count32(&json!({"width": 5_000_000_000_u64}), "width"),
        u32::MAX
    );
    assert_eq!(count32(&json!({"width": 4096}), "width"), 4096);
    assert_eq!(count32(&json!({}), "width"), 0);
}

#[test]
fn an_engine_that_starts_and_stops_again_and_again_is_given_up_on() {
    let count = AtomicU32::new(0);
    let quick = Duration::from_secs(3);
    let delays: Vec<_> = (0..5)
        .map(|_| restart_delay(&count, quick, false, true))
        .collect();
    assert_eq!(
        delays,
        [
            Some(Duration::from_secs(1)),
            Some(Duration::from_secs(2)),
            Some(Duration::from_secs(3)),
            None,
            None
        ]
    );
}

#[test]
fn an_engine_that_ran_for_a_while_or_finished_a_job_starts_the_count_over() {
    let count = AtomicU32::new(0);
    let quick = Duration::from_secs(3);
    assert!(restart_delay(&count, quick, false, true).is_some());
    assert!(restart_delay(&count, quick, false, true).is_some());
    assert_eq!(
        restart_delay(&count, STABLE_RUN, false, true),
        Some(Duration::from_secs(1))
    );
    assert!(restart_delay(&count, quick, false, true).is_some());
    assert!(restart_delay(&count, quick, false, true).is_some());
    assert_eq!(
        restart_delay(&count, quick, true, true),
        Some(Duration::from_secs(1))
    );
}

#[test]
fn a_stop_the_host_asked_for_never_counts_as_a_failed_start() {
    let count = AtomicU32::new(0);
    for _ in 0..10 {
        assert_eq!(
            restart_delay(&count, Duration::from_secs(1), false, false),
            Some(Duration::from_secs(1))
        );
    }
    assert_eq!(count.load(Ordering::SeqCst), 0);
}

#[test]
fn the_engine_handshake_does_not_clear_the_failed_starts() {
    let directory = tempfile::tempdir().unwrap();
    let state =
        Arc::new(AppState::from_paths(crate::paths::AppPaths::under(directory.path())).unwrap());
    state.worker.restart_count.store(2, Ordering::SeqCst);
    let (sender, _receiver) = mpsc::channel();
    *lock(&state.worker.sender).unwrap() = Some(sender);
    let info = json!({"protocol_version": PROTOCOL_VERSION, "minimum_protocol_version": 1,
            "engine_id": "e", "engine_version": "1", "features": [], "model_formats": [],
            "video_engines": []});
    apply_worker_envelope(&state, &envelope("engine_info", info)).unwrap();
    assert_eq!(state.worker.restart_count.load(Ordering::SeqCst), 2);
}

#[test]
fn an_engine_that_does_not_exit_after_asking_for_a_restart_is_stopped() {
    let start = Instant::now();
    let mut since = None;
    assert!(!restart_overdue(false, &mut since, start));
    assert!(!restart_overdue(true, &mut since, start));
    assert!(!restart_overdue(
        true,
        &mut since,
        start + RESTART_EXIT_GRACE / 2
    ));
    assert!(restart_overdue(
        true,
        &mut since,
        start + RESTART_EXIT_GRACE
    ));
    // A fresh request starts its own grace period.
    assert!(!restart_overdue(
        false,
        &mut since,
        start + RESTART_EXIT_GRACE * 2
    ));
    assert!(!restart_overdue(
        true,
        &mut since,
        start + RESTART_EXIT_GRACE * 2
    ));
}

#[test]
fn capability_contract_preserves_every_supported_backend_identifier() {
    let device_specs = [
        ("mps", "mps", true),
        ("cpu", "cpu", false),
        ("cuda:0", "cuda", false),
        ("cuda:0", "rocm", false),
        ("xpu:0", "xpu", true),
        ("directml:0", "directml", true),
        ("openvino:8680677d06", "openvino", true),
        ("openvino:80d1d11eb7", "npu", true),
    ];

    for (id, backend, integrated) in device_specs {
        let capability: CapabilityInfo = serde_json::from_value(json!({
            "system_ram_total": 16,
            "system_ram_available": 8,
            "system_memory_pressure_percent": 0.0,
            "system_memory_pressure_level": "low",
            "system_compressed_memory": 0,
            "system_swap_total": 0,
            "system_swap_used": 0,
            "devices": [{
                "id": id,
                "type": backend,
                "name": backend,
                "total_memory": 16,
                "free_memory": 8,
                "supports_fp16": backend != "cpu",
                "is_integrated": integrated,
                "recommended_tile_sizes": [64, 128]
            }]
        }))
        .unwrap();

        assert_eq!(capability.devices[0].id, id);
        assert_eq!(capability.devices[0].device_type, backend);
        assert_eq!(capability.devices[0].is_integrated, integrated);
    }
}
fn photo_job(id: &str) -> Value {
    json!({"type": "job_request", "data": {
        "job_id": id, "image_path": format!("/in/{id}.png"),
        "output_path": format!("/out/{id}.png"), "scratch_directory": "/work",
        "model_path": "/models/span.pth", "device": "mps", "neural_engine": true,
        "output_format": "png", "tile_size": 256, "halo": 16, "face_model_path": null,
        "stages": [{"kind": "upscale", "model_id": "span", "model_path": "/models/span.pth"}]
    }})
}

fn pending(messages: &[Value]) -> Vec<crate::database::PendingJob> {
    messages
        .iter()
        .map(|message| crate::database::PendingJob {
            id: string(&message["data"], "job_id"),
            request_json: message.to_string(),
        })
        .collect()
}

#[test]
fn queued_photos_that_share_one_recipe_go_out_as_one_batch_in_queue_order() {
    let mut other = photo_job("d");
    other["data"]["halo"] = json!(32);
    let window = pending(&[
        photo_job("a"),
        photo_job("b"),
        photo_job("c"),
        other,
        photo_job("e"),
    ]);
    let (ids, message) = next_dispatch(&window, |_| true).unwrap().unwrap();
    // Never past a job with another recipe: the queue keeps its order.
    assert_eq!(ids, ["a", "b", "c"]);
    assert_eq!(message["type"], "image_batch_request");
    let jobs = message["data"]["jobs"].as_array().unwrap();
    assert_eq!(jobs.len(), 3);
    assert_eq!(jobs[1], photo_job("b")["data"]);
    let mut jpeg = photo_job("f");
    jpeg["data"]["output_format"] = json!("jpg");
    let mut jpeg_too = photo_job("g");
    jpeg_too["data"]["output_format"] = json!("jpg");
    let (ids, _) = next_dispatch(&pending(&[jpeg, jpeg_too]), |_| true)
        .unwrap()
        .unwrap();
    assert_eq!(ids, ["f", "g"]);
}

#[test]
fn a_batch_never_exceeds_its_limit() {
    let window: Vec<Value> = (0..MAX_BATCH_PHOTOS + 3)
        .map(|index| photo_job(&format!("p{index}")))
        .collect();
    let (ids, message) = next_dispatch(&pending(&window), |_| true).unwrap().unwrap();
    assert_eq!(ids.len(), MAX_BATCH_PHOTOS);
    assert_eq!(
        message["data"]["jobs"].as_array().unwrap().len(),
        MAX_BATCH_PHOTOS
    );
}

#[test]
fn every_other_job_is_sent_as_before() {
    let changes: [(&str, Value); 6] = [
        ("device", json!("cpu")),
        ("neural_engine", json!(false)),
        ("face_model_path", json!("/models/face.pth")),
        (
            "stages",
            json!([{"kind": "restore"}, {"kind": "upscale", "model_path": "/models/span.pth"}]),
        ),
        (
            "stages",
            json!([{"kind": "upscale"}, {"kind": "face_restore", "model_path": "/f.pth"}]),
        ),
        ("model_path", json!("/models/hat.pth")),
    ];
    for (field, value) in changes {
        let mut first = photo_job("a");
        first["data"][field] = value.clone();
        let mut second = photo_job("b");
        second["data"][field] = value;
        let window = pending(&[first.clone(), second]);
        let (ids, message) = next_dispatch(&window, |path| path == "/models/span.pth")
            .unwrap()
            .unwrap();
        assert_eq!(ids, ["a"], "{field}");
        assert_eq!(message, first, "{field}");
    }
    for kind in ["video_job_request", "edit_job_request"] {
        let mut first = photo_job("a");
        first["type"] = json!(kind);
        let mut second = photo_job("b");
        second["type"] = json!(kind);
        let (ids, message) = next_dispatch(&pending(&[first.clone(), second]), |_| true)
            .unwrap()
            .unwrap();
        assert_eq!((ids.len(), message), (1, first));
    }
    // A single photo keeps the plain job request.
    let (ids, message) = next_dispatch(&pending(&[photo_job("a")]), |_| true)
        .unwrap()
        .unwrap();
    assert_eq!((ids.len(), message), (1, photo_job("a")));
    assert!(next_dispatch(&[], |_| true).unwrap().is_none());
}

fn batch_state(directory: &std::path::Path) -> Arc<AppState> {
    let state = Arc::new(AppState::from_paths(crate::paths::AppPaths::under(directory)).unwrap());
    let output = directory.join("out");
    std::fs::create_dir_all(&output).unwrap();
    let db = lock(&state.database).unwrap();
    for id in ["a", "b", "c"] {
        let mut job = photo_job(id);
        job["data"]["output_path"] = json!(output.join(format!("{id}.png")));
        db.insert_media(id, &format!("/in/{id}.png"), id, "image")
            .unwrap();
        db.insert_job(id, id, &job.to_string()).unwrap();
    }
    drop(db);
    state
}

fn status(state: &AppState, id: &str) -> String {
    let jobs = lock(&state.database).unwrap().list_jobs().unwrap();
    jobs.into_iter().find(|job| job.id == id).unwrap().status
}

fn idle_state() -> AppState {
    let directory = tempfile::tempdir().unwrap();
    AppState::from_paths(crate::paths::AppPaths::under(directory.path())).unwrap()
}

fn envelope(kind: &str, data: Value) -> WorkerEnvelope {
    WorkerEnvelope {
        message_type: kind.into(),
        data,
    }
}

#[test]
fn a_dispatched_batch_shows_its_first_photo_and_keeps_the_others_queued() {
    let directory = tempfile::tempdir().unwrap();
    let state = batch_state(directory.path());
    ready_for_batches(&state);
    let (sender, receiver) = mpsc::channel();
    *lock(&state.worker.sender).unwrap() = Some(sender);

    assert!(begin_dispatch(&state, true).unwrap());
    let sent: Value = serde_json::from_str(&receiver.try_recv().unwrap()).unwrap();
    let runtime = lock(&state.runtime).unwrap().clone();
    assert_eq!(runtime.active_job_id, "a");
    assert_eq!(sent["type"], "image_batch_request");
    assert_eq!(runtime.active_batch_job_ids, ["a", "b", "c"]);
    // Each photo owns its scratch directory next to its output.
    let jobs = sent["data"]["jobs"].as_array().unwrap();
    let scratch: Vec<_> = jobs
        .iter()
        .map(|job| PathBuf::from(job["scratch_directory"].as_str().unwrap()))
        .collect();
    assert!(scratch.iter().all(|path| path.is_dir()));
    assert_eq!(
        scratch
            .iter()
            .collect::<std::collections::HashSet<_>>()
            .len(),
        3
    );
    // Only the photo on screen is starting; the others wait as queued photos.
    assert_eq!(status(&state, "a"), "starting");
    assert_eq!(status(&state, "b"), "queued");
    // Nothing else is dispatched while the batch runs.
    assert!(!begin_dispatch(&state, true).unwrap());
    lock(&state.worker.cancellation).unwrap().finish().unwrap();
}

#[test]
fn hosts_other_than_a_mac_send_one_job_at_a_time() {
    let directory = tempfile::tempdir().unwrap();
    let state = batch_state(directory.path());
    ready_for_batches(&state);
    let (sender, receiver) = mpsc::channel();
    *lock(&state.worker.sender).unwrap() = Some(sender);
    assert!(begin_dispatch(&state, false).unwrap());
    let sent: Value = serde_json::from_str(&receiver.try_recv().unwrap()).unwrap();
    assert_eq!(sent["type"], "job_request");
    assert!(lock(&state.runtime)
        .unwrap()
        .active_batch_job_ids
        .is_empty());
    lock(&state.worker.cancellation).unwrap().finish().unwrap();
}

#[test]
fn a_photo_whose_scratch_folder_cannot_be_made_ends_the_batch_before_it() {
    let directory = tempfile::tempdir().unwrap();
    let state = batch_state(directory.path());
    {
        // The third photo writes to a folder that no longer exists (an ejected drive).
        let db = lock(&state.database).unwrap();
        let mut job = photo_job("c");
        job["data"]["output_path"] = json!(directory.path().join("gone").join("c.png"));
        db.cancel_queued_jobs().unwrap();
        for id in ["a2", "b2", "c2"] {
            db.insert_media(id, &format!("/in/{id}.png"), id, "image")
                .unwrap();
        }
        let mut a = photo_job("a2");
        a["data"]["output_path"] = json!(directory.path().join("out").join("a2.png"));
        let mut b = photo_job("b2");
        b["data"]["output_path"] = json!(directory.path().join("out").join("b2.png"));
        job["data"]["job_id"] = json!("c2");
        db.insert_job("a2", "a2", &a.to_string()).unwrap();
        db.insert_job("b2", "b2", &b.to_string()).unwrap();
        db.insert_job("c2", "c2", &job.to_string()).unwrap();
    }
    ready_for_batches(&state);
    let (sender, receiver) = mpsc::channel();
    *lock(&state.worker.sender).unwrap() = Some(sender);
    assert!(begin_dispatch(&state, true).unwrap());
    let sent: Value = serde_json::from_str(&receiver.try_recv().unwrap()).unwrap();
    assert_eq!(sent["type"], "image_batch_request");
    assert_eq!(sent["data"]["jobs"].as_array().unwrap().len(), 2);
    assert_eq!(
        lock(&state.runtime).unwrap().active_batch_job_ids,
        ["a2", "b2"]
    );
    // The healthy photos run; the other waits for its own turn.
    assert_eq!(status(&state, "c2"), "queued");
    lock(&state.worker.cancellation).unwrap().finish().unwrap();
}

#[test]
fn a_job_whose_scratch_folder_cannot_be_made_fails_and_the_queue_goes_on() {
    let directory = tempfile::tempdir().unwrap();
    let state = batch_state(directory.path());
    {
        // The first job writes to a folder that no longer exists (an ejected drive).
        let db = lock(&state.database).unwrap();
        db.cancel_queued_jobs().unwrap();
        for id in ["bad", "worse", "good"] {
            db.insert_media(id, &format!("/in/{id}.png"), id, "image")
                .unwrap();
            let mut job = photo_job(id);
            let folder = if id == "good" { "out" } else { "gone" };
            job["data"]["output_path"] =
                json!(directory.path().join(folder).join(format!("{id}.png")));
            db.insert_job(id, id, &job.to_string()).unwrap();
        }
    }
    ready_for_batches(&state);
    let (sender, receiver) = mpsc::channel();
    *lock(&state.worker.sender).unwrap() = Some(sender);
    assert!(begin_dispatch(&state, false).unwrap());
    let sent: Value = serde_json::from_str(&receiver.try_recv().unwrap()).unwrap();
    assert_eq!(sent["data"]["job_id"], "good");
    for id in ["bad", "worse"] {
        assert_eq!(status(&state, id), "failed");
    }
    let jobs = lock(&state.database).unwrap().list_jobs().unwrap();
    assert!(jobs
        .iter()
        .find(|job| job.id == "bad")
        .unwrap()
        .error
        .contains("No such file"));
    assert_eq!(lock(&state.runtime).unwrap().active_job_id, "good");
    lock(&state.worker.cancellation).unwrap().finish().unwrap();
}

#[test]
fn a_benchmark_result_that_cannot_be_read_fails_the_benchmark_and_not_the_engine() {
    let directory = tempfile::tempdir().unwrap();
    let state =
        Arc::new(AppState::from_paths(crate::paths::AppPaths::under(directory.path())).unwrap());
    lock(&state.runtime)
        .unwrap()
        .begin_active_job("benchmark-1".into());
    for result in [json!({"invalid": true}), json!(null)] {
        let message = envelope(
            "benchmark_completed",
            json!({"job_id": "benchmark-1", "result": result}),
        );
        apply_worker_envelope(&state, &message).unwrap();
        let runtime = lock(&state.runtime).unwrap();
        assert!(runtime.active_job_id.is_empty());
        assert_eq!(runtime.status_title, "Benchmark failed");
        assert_ne!(runtime.worker, "failed");
        assert!(lock(&state.latest_benchmark).unwrap().is_none());
        drop(runtime);
        lock(&state.runtime)
            .unwrap()
            .begin_active_job("benchmark-1".into());
    }
}

#[test]
fn frame_messages_of_a_video_share_one_status_title() {
    let directory = tempfile::tempdir().unwrap();
    let state = batch_state(directory.path());
    lock(&state.runtime).unwrap().active_job_id = "a".into();
    let mut titles = Vec::new();
    for (kind, data) in [
        (
            "video_frame_started",
            json!({"frame_index": 0, "total_frames": 10}),
        ),
        (
            "video_tile_progress",
            json!({"frame_index": 0, "total_frames": 10, "completed_tiles": 1, "total_tiles": 4}),
        ),
        (
            "video_frame_completed",
            json!({"job_id": "a", "frames_processed": 1, "total_frames": 10}),
        ),
    ] {
        apply_worker_envelope(&state, &envelope(kind, data)).unwrap();
        titles.push(lock(&state.runtime).unwrap().status_title.clone());
    }
    assert!(
        titles.windows(2).all(|pair| pair[0] == pair[1]),
        "{titles:?}"
    );
}

#[test]
fn the_finished_notice_waits_until_the_queue_is_done() {
    let directory = tempfile::tempdir().unwrap();
    let state = batch_state(directory.path());
    let done = json!({"job_id": "a"});
    assert_eq!(finished_notice(&state, &done), None);
    lock(&state.database).unwrap().cancel_queued_jobs().unwrap();
    assert_eq!(
        finished_notice(&state, &done),
        Some("Your enhanced media is ready.")
    );
    assert!(
        finished_notice(&state, &json!({"notes": "Blur fix skipped"}))
            .unwrap()
            .contains("A fix was skipped")
    );
}

fn ready_for_batches(state: &AppState) {
    *lock(&state.engine).unwrap() = Some(
        serde_json::from_value(json!({"protocol_version": 1, "minimum_protocol_version": 1,
                "engine_id": "e", "engine_version": "1", "features": ["image", "image_batch_v1"],
                "model_formats": [], "video_engines": []}))
        .unwrap(),
    );
    for model in &mut lock(&state.catalog).unwrap().models {
        if model.architecture == "SPAN" {
            model.installed = true;
            model.installed_path = Some("/models/span.pth".into());
        }
    }
    lock(&state.runtime).unwrap().worker = "ready".into();
}

fn running_batch(directory: &std::path::Path) -> Arc<AppState> {
    let state = batch_state(directory);
    let mut runtime = lock(&state.runtime).unwrap();
    runtime.worker = "ready".into();
    runtime.active_job_id = "a".into();
    runtime.active_batch_job_ids = vec!["a".into(), "b".into(), "c".into()];
    drop(runtime);
    state
}

#[test]
fn the_next_photo_of_a_batch_does_not_inherit_the_last_one_s_timing() {
    let directory = tempfile::tempdir().unwrap();
    let state = running_batch(directory.path());
    let apply = |kind: &str, data: Value| apply_worker_envelope(&state, &envelope(kind, data));
    apply("job_started", json!({"job_id": "a"})).unwrap();
    apply(
        "progress",
        json!({"job_id": "a", "percentage": 90.0, "elapsed_seconds": 40.0,
               "estimated_remaining_seconds": 5.0, "completed_tiles": 9, "total_tiles": 10}),
    )
    .unwrap();
    apply(
        "job_completed",
        json!({"job_id": "a", "output_path": "/out/a.png", "elapsed_seconds": 41.0}),
    )
    .unwrap();
    let runtime = lock(&state.runtime).unwrap();
    assert_eq!(runtime.active_job_id, "b");
    assert_eq!(runtime.elapsed_seconds, 0.0);
    assert_eq!(runtime.estimated_remaining_seconds, 0.0);
}

#[test]
fn a_batch_shows_one_photo_while_the_others_finish_in_the_background() {
    let directory = tempfile::tempdir().unwrap();
    let state = running_batch(directory.path());
    let apply = |kind: &str, data: Value| apply_worker_envelope(&state, &envelope(kind, data));
    apply("job_started", json!({"job_id": "a"})).unwrap();
    apply("progress", json!({"job_id": "a", "percentage": 90.0})).unwrap();
    // The next photo starts as the first one's last tiles finish: it is shown now.
    apply("job_started", json!({"job_id": "b"})).unwrap();
    apply("progress", json!({"job_id": "b", "percentage": 20.0})).unwrap();
    apply("progress", json!({"job_id": "a", "percentage": 100.0})).unwrap();
    apply(
        "job_completed",
        json!({"job_id": "a", "output_path": "/out/a.png", "elapsed_seconds": 1.5}),
    )
    .unwrap();
    {
        let runtime = lock(&state.runtime).unwrap();
        assert_eq!(runtime.active_job_id, "b");
        assert_eq!(runtime.progress, 20.0);
        assert_eq!(runtime.status_title, "Enhancing");
        assert_eq!(runtime.active_batch_job_ids, ["b", "c"]);
        assert_eq!(runtime.last_output_path, "/out/a.png");
    }
    assert_eq!(status(&state, "a"), "completed");
    assert_eq!(status(&state, "b"), "running");
    assert_eq!(status(&state, "c"), "queued");
    // A photo that fails to open before it starts fails alone.
    apply(
        "job_failed",
        json!({"job_id": "c", "error_message": "cannot identify image file"}),
    )
    .unwrap();
    assert_eq!(status(&state, "c"), "failed");
    assert_eq!(lock(&state.runtime).unwrap().active_job_id, "b");
    apply(
        "job_completed",
        json!({"job_id": "b", "output_path": "/out/b.png"}),
    )
    .unwrap();
    let runtime = lock(&state.runtime).unwrap();
    assert!(runtime.active_job_id.is_empty() && runtime.active_batch_job_ids.is_empty());
    assert_eq!(runtime.status_title, "Complete");
    assert_eq!(runtime.last_output_path, "/out/b.png");
}

#[test]
fn messages_about_work_the_host_never_sent_change_nothing() {
    let directory = tempfile::tempdir().unwrap();
    let state =
        Arc::new(AppState::from_paths(crate::paths::AppPaths::under(directory.path())).unwrap());
    for (kind, data) in [
        (
            "job_completed",
            json!({"job_id": "ghost", "output_path": "/etc/hosts"}),
        ),
        (
            "video_job_completed",
            json!({"job_id": "ghost", "output_path": "/etc/hosts"}),
        ),
        ("job_completed", json!({"output_path": "/etc/hosts"})),
        ("job_started", json!({"job_id": "ghost"})),
        ("progress", json!({"job_id": "ghost", "percentage": 5.0})),
        (
            "benchmark_started",
            json!({"job_id": "ghost", "workload_version": "v2"}),
        ),
    ] {
        assert!(
            !accept_envelope(&state, &envelope(kind, data)).unwrap(),
            "{kind}"
        );
    }
    let runtime = lock(&state.runtime).unwrap();
    assert!(runtime.last_output_path.is_empty());
    assert!(runtime.active_job_id.is_empty());
}

#[test]
fn a_finished_job_ignores_every_later_message() {
    let directory = tempfile::tempdir().unwrap();
    let state = running_batch(directory.path());
    let own = directory.path().join("out").join("a.png");
    let done = envelope(
        "job_completed",
        json!({"job_id": "a", "output_path": own.to_string_lossy()}),
    );
    assert!(accept_envelope(&state, &envelope("job_started", json!({"job_id": "a"}))).unwrap());
    assert!(accept_envelope(&state, &done).unwrap());
    for (kind, data) in [
        ("progress", json!({"job_id": "a", "percentage": 10.0})),
        ("job_started", json!({"job_id": "a"})),
        (
            "job_failed",
            json!({"job_id": "a", "error_message": "late"}),
        ),
        ("job_cancelled", json!({"job_id": "a"})),
    ] {
        assert!(
            !accept_envelope(&state, &envelope(kind, data)).unwrap(),
            "{kind}"
        );
    }
    assert!(!accept_envelope(&state, &done).unwrap());
    assert_eq!(status(&state, "a"), "completed");
    let jobs = lock(&state.database).unwrap().list_jobs().unwrap();
    let job = jobs.iter().find(|job| job.id == "a").unwrap();
    assert_eq!(job.output_path, own.to_string_lossy());
}

#[test]
fn a_job_that_ended_in_the_queue_keeps_that_state_when_the_engine_reports_later() {
    let directory = tempfile::tempdir().unwrap();
    let state = running_batch(directory.path());
    // The user cancelled the third photo before the engine reached it.
    lock(&state.database)
        .unwrap()
        .finish_job("c", "cancelled", "", "Cancelled by the user.")
        .unwrap();
    let own = directory.path().join("out").join("c.png");
    for (kind, data) in [
        ("job_started", json!({"job_id": "c"})),
        ("progress", json!({"job_id": "c", "percentage": 50.0})),
        (
            "job_completed",
            json!({"job_id": "c", "output_path": own.to_string_lossy()}),
        ),
        (
            "job_failed",
            json!({"job_id": "c", "error_message": "late"}),
        ),
    ] {
        assert!(
            !accept_envelope(&state, &envelope(kind, data)).unwrap(),
            "{kind}"
        );
    }
    assert_eq!(status(&state, "c"), "cancelled");
    assert!(lock(&state.runtime).unwrap().last_output_path.is_empty());
}

#[test]
fn a_completion_must_name_the_output_the_host_requested() {
    let directory = tempfile::tempdir().unwrap();
    let state = running_batch(directory.path());
    let requested = directory.path().join("out").join("a.png");
    let done = |path: &std::path::Path| {
        envelope(
            "job_completed",
            json!({"job_id": "a", "output_path": path.to_string_lossy()}),
        )
    };
    assert!(accept_envelope(&state, &envelope("job_started", json!({"job_id": "a"}))).unwrap());
    // Another file the engine can read, and a sibling that was never requested.
    for forged in [
        std::path::Path::new("/etc/hosts"),
        &directory.path().join("out").join("b.png"),
        &directory.path().join("out").join("A.png"),
        &directory.path().join("a.png"),
    ] {
        assert!(!accept_envelope(&state, &done(forged)).unwrap());
    }
    {
        let runtime = lock(&state.runtime).unwrap();
        assert!(runtime.last_output_path.is_empty());
        assert_eq!(runtime.active_job_id, "a");
    }
    assert_eq!(status(&state, "a"), "running");
    assert!(accept_envelope(&state, &done(&requested)).unwrap());
    assert_eq!(status(&state, "a"), "completed");
    assert_eq!(
        lock(&state.runtime).unwrap().last_output_path,
        requested.to_string_lossy()
    );
    // The same completion again is a message about a job that has ended.
    assert!(!accept_envelope(&state, &done(&requested)).unwrap());
}

#[test]
fn a_completion_with_the_wrong_output_fails_its_job_and_the_engine_restarts() {
    let directory = tempfile::tempdir().unwrap();
    let state = running_batch(directory.path());
    let wrong = directory.path().join("a.png");
    let done = |id: &str, path: &std::path::Path| {
        envelope(
            "job_completed",
            json!({"job_id": id, "output_path": path.to_string_lossy()}),
        )
    };
    // Messages about jobs that were never dispatched stay ignored.
    assert!(refuse_wrong_result(&state, &done("ghost", &wrong))
        .unwrap()
        .is_none());
    // A correct completion is not touched.
    let own = directory.path().join("out").join("a.png");
    assert!(refuse_wrong_result(&state, &done("a", &own))
        .unwrap()
        .is_none());
    assert!(accept_envelope(&state, &envelope("job_started", json!({"job_id": "a"}))).unwrap());
    let failure = refuse_wrong_result(&state, &done("a", &wrong))
        .unwrap()
        .expect("the active job must fail");
    assert_eq!(failure.message_type, "job_failed");
    assert_eq!(failure.data["job_id"], "a");
    assert_eq!(failure.data["restart_required"], true);
    assert!(accept_envelope(&state, &failure).unwrap());
    let jobs = lock(&state.database).unwrap().list_jobs().unwrap();
    let job = jobs.iter().find(|job| job.id == "a").unwrap();
    assert_eq!(job.status, "failed");
    assert_eq!(
        job.error,
        "The engine reported a result the app did not ask for."
    );
    assert!(job.output_path.is_empty());
    {
        let runtime = lock(&state.runtime).unwrap();
        assert_ne!(runtime.active_job_id, "a");
        assert!(runtime.last_output_path.is_empty());
        assert_eq!(runtime.worker, "restarting");
    }
    // The failed job has ended: a second report about it is ignored.
    assert!(refuse_wrong_result(&state, &done("a", &wrong))
        .unwrap()
        .is_none());
    // An off-screen batch photo fails the same way and the batch goes on.
    let failure = refuse_wrong_result(&state, &done("c", &own))
        .unwrap()
        .expect("a batch member must fail");
    assert!(accept_envelope(&state, &failure).unwrap());
    assert_eq!(status(&state, "c"), "failed");
    assert_eq!(status(&state, "b"), "queued");
}

#[test]
fn a_batch_photo_completes_only_with_its_own_output() {
    let directory = tempfile::tempdir().unwrap();
    let state = running_batch(directory.path());
    let own = |id: &str| directory.path().join("out").join(format!("{id}.png"));
    let done = |id: &str, path: &std::path::Path| {
        envelope(
            "job_completed",
            json!({"job_id": id, "output_path": path.to_string_lossy()}),
        )
    };
    // c off screen reports the file of a.
    assert!(!accept_envelope(&state, &done("c", &own("a"))).unwrap());
    assert_eq!(status(&state, "c"), "queued");
    assert!(lock(&state.runtime).unwrap().last_output_path.is_empty());
    assert!(accept_envelope(&state, &done("c", &own("c"))).unwrap());
    assert_eq!(status(&state, "c"), "completed");
    assert_eq!(
        lock(&state.runtime).unwrap().last_output_path,
        own("c").to_string_lossy()
    );
}

#[test]
fn a_video_completion_must_name_the_requested_video() {
    let directory = tempfile::tempdir().unwrap();
    let state =
        Arc::new(AppState::from_paths(crate::paths::AppPaths::under(directory.path())).unwrap());
    let output = directory.path().join("clip_video_4x.mp4");
    {
        let database = lock(&state.database).unwrap();
        database
            .insert_media("m", "/in/clip.mp4", "clip.mp4", "video")
            .unwrap();
        let request = json!({"type": "video_job_request", "data": {
                "job_id": "v", "output_video_path": output.to_string_lossy()}});
        database.insert_job("v", "m", &request.to_string()).unwrap();
    }
    lock(&state.runtime).unwrap().begin_active_job("v".into());
    let done = |path: &str| {
        envelope(
            "video_job_completed",
            json!({"job_id": "v", "output_path": path, "frames_processed": 3}),
        )
    };
    assert!(!accept_envelope(&state, &done("/etc/hosts")).unwrap());
    assert!(accept_envelope(&state, &done(&output.to_string_lossy())).unwrap());
    assert_eq!(status(&state, "v"), "completed");
}

#[test]
fn a_failed_item_keeps_the_engines_reason_in_the_history() {
    let directory = tempfile::tempdir().unwrap();
    let state = running_batch(directory.path());
    let reason = "The output file appeared while the photo was processed; it was not replaced.";
    assert!(accept_envelope(
        &state,
        &envelope(
            "job_failed",
            json!({"job_id": "b", "error_message": reason})
        )
    )
    .unwrap());
    let jobs = lock(&state.database).unwrap().list_jobs().unwrap();
    let job = jobs.iter().find(|job| job.id == "b").unwrap();
    assert_eq!(
        (job.status.as_str(), job.error.as_str()),
        ("failed", reason)
    );
    assert!(job.output_path.is_empty());
    assert_eq!(lock(&state.runtime).unwrap().active_job_id, "a");
}

#[test]
fn when_the_shown_photo_ends_first_the_batch_stays_on_screen() {
    let directory = tempfile::tempdir().unwrap();
    let state = running_batch(directory.path());
    apply_worker_envelope(&state, &envelope("job_started", json!({"job_id": "a"}))).unwrap();
    apply_worker_envelope(
        &state,
        &envelope(
            "job_failed",
            json!({"job_id": "a", "error_message": "disk full"}),
        ),
    )
    .unwrap();
    let runtime = lock(&state.runtime).unwrap();
    // Still busy: settings stay locked and nothing else is dispatched.
    assert_eq!(runtime.active_job_id, "b");
    assert_eq!(runtime.active_batch_job_ids, ["b", "c"]);
    // The failure stays on the status line until the next photo starts.
    assert_eq!(runtime.status_title, "Could not finish");
    assert_eq!(runtime.status_detail, "disk full");
    drop(runtime);
    assert_eq!(status(&state, "a"), "failed");
    apply_worker_envelope(&state, &envelope("job_started", json!({"job_id": "b"}))).unwrap();
    apply_worker_envelope(
        &state,
        &envelope(
            "job_completed",
            json!({"job_id": "b", "output_path": "/out/b.png"}),
        ),
    )
    .unwrap();
    assert_eq!(lock(&state.runtime).unwrap().status_title, "Processing");
}

#[test]
fn the_last_photo_of_a_batch_gets_its_full_preview() {
    let directory = tempfile::tempdir().unwrap();
    let state = running_batch(directory.path());
    let apply = |kind: &str, data: Value| apply_worker_envelope(&state, &envelope(kind, data));
    apply("job_started", json!({"job_id": "a"})).unwrap();
    apply("job_started", json!({"job_id": "b"})).unwrap();
    // a saved off screen: no full preview is requested for it.
    assert!(is_background_photo(&lock(&state.runtime).unwrap(), "a"));
    apply(
        "job_completed",
        json!({"job_id": "a", "output_path": "/out/a.png"}),
    )
    .unwrap();
    apply("job_cancelled", json!({"job_id": "c"})).unwrap();
    // b is on screen when it completes, so its preview is requested and shown.
    assert!(!is_background_photo(&lock(&state.runtime).unwrap(), "b"));
    let done = envelope(
        "job_completed",
        json!({"job_id": "b", "output_path": "/out/b.png"}),
    );
    assert!(completion_preview_request(&state, &done).is_some());
    apply_worker_envelope(&state, &done).unwrap();
    apply(
        "preview_ready",
        json!({"image_path": "/out/b.png", "jpeg_base64": "full-b"}),
    )
    .unwrap();
    assert_eq!(
        lock(&state.runtime).unwrap().result_preview_data_url,
        "data:image/jpeg;base64,full-b"
    );
}

#[test]
fn a_photo_saved_off_screen_drops_a_preview_that_is_not_its_own() {
    let directory = tempfile::tempdir().unwrap();
    let state = running_batch(directory.path());
    apply_worker_envelope(&state, &envelope("job_started", json!({"job_id": "b"}))).unwrap();
    apply_worker_envelope(&state, &envelope("job_started", json!({"job_id": "a"}))).unwrap();
    lock(&state.runtime).unwrap().result_preview_data_url = "a-pixels".into();
    apply_worker_envelope(
        &state,
        &envelope(
            "job_completed",
            json!({"job_id": "b", "output_path": "/out/b.png"}),
        ),
    )
    .unwrap();
    // last_output_path now names b, so no picture of a may stay beside it.
    let runtime = lock(&state.runtime).unwrap();
    assert_eq!(runtime.last_output_path, "/out/b.png");
    assert!(runtime.result_preview_data_url.is_empty());
}

#[test]
fn a_background_photo_that_lost_the_gpu_holds_the_queue_for_the_restart() {
    let directory = tempfile::tempdir().unwrap();
    let state = running_batch(directory.path());
    apply_worker_envelope(
        &state,
        &envelope(
            "job_failed",
            json!({"job_id": "b", "error_message": "device lost", "restart_required": true}),
        ),
    )
    .unwrap();
    let runtime = lock(&state.runtime).unwrap();
    assert_eq!(runtime.worker, "restarting");
    assert_eq!(runtime.active_job_id, "a");
}

#[test]
fn after_a_crash_the_started_batch_photos_run_again_alone_once() {
    let directory = tempfile::tempdir().unwrap();
    let state = batch_state(directory.path());
    let ids: Vec<String> = ["a", "b", "c"].map(String::from).to_vec();
    {
        let db = lock(&state.database).unwrap();
        db.set_job_status("a", "running").unwrap();
        db.set_job_status("b", "running").unwrap();
        settle_stopped_jobs(&db, &ids, &[], "The engine stopped.");
    }
    // Any of the started photos may have crashed the engine: each runs alone next.
    for id in ["a", "b", "c"] {
        assert_eq!(status(&state, id), "queued");
    }
    let window = lock(&state.database)
        .unwrap()
        .queued_job_window(MAX_BATCH_PHOTOS)
        .unwrap();
    let (sent, message) = next_dispatch(&window, |_| true).unwrap().unwrap();
    assert_eq!(sent, ["a"]);
    assert_eq!(message["type"], "job_request");
    // The hint stays with the desktop; the engine gets the photo's own request.
    assert!(message["data"].get("run_alone").is_none());
    // Run alone, a photo that crashes the engine again is interrupted.
    {
        let db = lock(&state.database).unwrap();
        db.set_job_status("a", "running").unwrap();
        settle_stopped_jobs(&db, &["a".to_string()], &[], "The engine stopped.");
    }
    assert_eq!(status(&state, "a"), "interrupted");
}

#[test]
fn after_an_engine_stop_unstarted_batch_photos_are_queued_again() {
    let directory = tempfile::tempdir().unwrap();
    let state = batch_state(directory.path());
    let ids: Vec<String> = ["a", "b", "c"].map(String::from).to_vec();
    {
        let db = lock(&state.database).unwrap();
        db.set_job_status("a", "running").unwrap();
        settle_stopped_jobs(&db, &ids, &[], "The engine stopped.");
    }
    // Even one started photo may not be the cause: the photo decoded ahead of it
    // had not started yet. It runs again alone; the others stay queued.
    assert_eq!(status(&state, "a"), "queued");
    assert_eq!(status(&state, "b"), "queued");
    let window = lock(&state.database)
        .unwrap()
        .queued_job_window(MAX_BATCH_PHOTOS)
        .unwrap();
    let (sent, _) = next_dispatch(&window, |_| true).unwrap().unwrap();
    assert_eq!(sent, ["a"]);
    let rest: Vec<_> = window.into_iter().skip(1).collect();
    let (sent, _) = next_dispatch(&rest, |_| true).unwrap().unwrap();
    assert_eq!(sent, ["b", "c"]);
    {
        let db = lock(&state.database).unwrap();
        db.set_job_status("b", "running").unwrap();
        settle_stopped_jobs(&db, &ids, &ids, "The engine stopped.");
    }
    // A cancellation that had to stop the engine cancels every photo it covered.
    assert_eq!(status(&state, "b"), "cancelled");
    assert_eq!(status(&state, "c"), "cancelled");
}

#[test]
fn a_crash_while_cancelling_a_batch_never_leaves_a_photo_cancelling() {
    let directory = tempfile::tempdir().unwrap();
    let state = batch_state(directory.path());
    let ids: Vec<String> = ["a", "b", "c"].map(String::from).to_vec();
    {
        let db = lock(&state.database).unwrap();
        db.set_job_status("a", "cancelling").unwrap();
        db.set_job_status("b", "running").unwrap();
        settle_stopped_jobs(&db, &ids, &[], "The engine stopped.");
    }
    assert_eq!(status(&state, "a"), "interrupted");
    assert_eq!(status(&state, "b"), "queued");
}

#[test]
fn the_scratch_folders_made_for_a_dispatch_are_recorded_with_their_jobs() {
    let directory = tempfile::tempdir().unwrap();
    let state = batch_state(directory.path());
    let mut message = json!({"type": "image_batch_request", "data": {"jobs": [
        photo_job("a")["data"].clone(), photo_job("b")["data"].clone()]}});
    for (index, id) in ["a", "b"].iter().enumerate() {
        message["data"]["jobs"][index]["job_id"] = json!(id);
        message["data"]["jobs"][index]["output_path"] =
            json!(directory.path().join("out").join(format!("{id}.png")));
    }
    lock(&state.worker.cancellation)
        .unwrap()
        .prepare("a", &mut message)
        .unwrap();
    record_scratch_folders(&state).unwrap();
    // A later start reads them back for the jobs the stopped host left.
    let mut database = crate::database::Database::open(&state.paths.database).unwrap();
    let mut found: Vec<(String, std::path::PathBuf)> = database
        .take_interrupted_scratch()
        .into_iter()
        .map(|record| (record.id, record.scratch))
        .collect();
    found.sort();
    let made: Vec<std::path::PathBuf> = message["data"]["jobs"]
        .as_array()
        .unwrap()
        .iter()
        .map(|job| std::path::PathBuf::from(job["scratch_directory"].as_str().unwrap()))
        .collect();
    assert_eq!(
        found,
        [
            ("a".to_owned(), made[0].clone()),
            ("b".to_owned(), made[1].clone())
        ]
    );
    lock(&state.worker.cancellation).unwrap().finish().unwrap();
}

#[cfg(unix)]
#[test]
fn a_result_that_is_a_link_gets_no_automatic_preview() {
    let directory = tempfile::tempdir().unwrap();
    let private = directory.path().join("private.png");
    std::fs::write(&private, b"private").unwrap();
    let link = directory.path().join("result.png");
    std::os::unix::fs::symlink(&private, &link).unwrap();
    for kind in ["job_completed", "video_job_completed"] {
        let done = envelope(
            kind,
            json!({"job_id": "a", "output_path": link.to_string_lossy()}),
        );
        assert_eq!(
            completion_preview_request(&idle_state(), &done),
            None,
            "{kind}"
        );
    }
    let plain = directory.path().join("plain.png");
    std::fs::write(&plain, b"result").unwrap();
    let done = envelope(
        "job_completed",
        json!({"job_id": "a", "output_path": plain.to_string_lossy()}),
    );
    assert!(completion_preview_request(&idle_state(), &done).is_some());
}

fn given_up_state() -> (tempfile::TempDir, Arc<AppState>) {
    let directory = tempfile::tempdir().unwrap();
    let state =
        Arc::new(AppState::from_paths(crate::paths::AppPaths::under(directory.path())).unwrap());
    {
        let mut runtime = lock(&state.runtime).unwrap();
        runtime.worker = "failed".into();
        runtime.status_title = "Engine unavailable".into();
        runtime.engine_retry = true;
    }
    state.worker.restart_count.store(4, Ordering::SeqCst);
    (directory, state)
}

#[test]
fn a_retry_is_accepted_once_after_the_host_gave_up_and_resets_the_count() {
    let (_directory, state) = given_up_state();
    begin_engine_retry(&state).unwrap();
    assert_eq!(state.worker.restart_count.load(Ordering::SeqCst), 0);
    assert!(!lock(&state.runtime).unwrap().engine_retry);
    // A second call while the engine starts changes nothing.
    state.worker.restart_count.store(2, Ordering::SeqCst);
    assert!(begin_engine_retry(&state).is_err());
    assert_eq!(state.worker.restart_count.load(Ordering::SeqCst), 2);
}

#[test]
fn a_retry_is_refused_in_every_other_state_and_changes_nothing() {
    let (_directory, state) = given_up_state();
    for worker in [
        "starting",
        "negotiating",
        "ready",
        "unavailable",
        "restarting",
    ] {
        lock(&state.runtime).unwrap().worker = worker.into();
        assert!(begin_engine_retry(&state).is_err(), "{worker}");
    }
    // A failed engine that was not given up on: the protocol mismatch.
    lock(&state.runtime).unwrap().engine_retry = false;
    apply_worker_envelope(
        &state,
        &envelope("protocol_error", json!({"message": "too old"})),
    )
    .unwrap();
    {
        let runtime = lock(&state.runtime).unwrap();
        assert_eq!(runtime.status_title, "Engine incompatible");
        assert_eq!(runtime.worker, "failed");
    }
    assert!(begin_engine_retry(&state).is_err());
    assert_eq!(state.worker.restart_count.load(Ordering::SeqCst), 4);
    assert_eq!(
        lock(&state.runtime).unwrap().status_title,
        "Engine incompatible"
    );
}

fn short_volume() -> crate::disk_room::Volume {
    // 4.7 GB free on a 500 GB system volume, as on the incident's Mac.
    crate::disk_room::Volume {
        free: 4_700_000_000,
        total: 500_000_000_000,
        system: true,
    }
}

#[test]
fn a_job_that_does_not_fit_fails_before_it_is_sent_and_stops_the_queue_once() {
    let directory = tempfile::tempdir().unwrap();
    let state = batch_state(directory.path());
    ready_for_batches(&state);
    let (sender, receiver) = mpsc::channel();
    *lock(&state.worker.sender).unwrap() = Some(sender);
    crate::disk_room::set_test_volume(Some(short_volume()));
    assert!(begin_dispatch(&state, true).unwrap());
    crate::disk_room::set_test_volume(None);
    // Nothing reached the engine, and no scratch folder was made for it.
    assert!(receiver.try_recv().is_err());
    assert!(std::fs::read_dir(directory.path().join("out"))
        .unwrap()
        .next()
        .is_none());
    let jobs = lock(&state.database).unwrap().list_jobs().unwrap();
    let error = |id: &str| jobs.iter().find(|job| job.id == id).unwrap().error.clone();
    assert_eq!(status(&state, "a"), "failed");
    assert!(error("a").starts_with("Not enough free disk space in "));
    assert!(error("a").contains("must stay free so the computer keeps working."));
    // The jobs behind it end as not started, once, instead of failing the same way.
    for id in ["b", "c"] {
        assert_eq!(status(&state, id), "cancelled");
        assert_eq!(error(id), crate::disk_room::NOT_STARTED);
    }
    let runtime = lock(&state.runtime).unwrap();
    assert_eq!(runtime.status_title, "Could not finish");
    assert!(runtime
        .status_detail
        .starts_with("The queue stopped: the disk is full. Not enough free disk space in "));
    assert!(runtime.active_job_id.is_empty());
}

#[test]
fn space_is_checked_again_before_every_job_of_a_queue() {
    let directory = tempfile::tempdir().unwrap();
    let state = batch_state(directory.path());
    ready_for_batches(&state);
    let (sender, receiver) = mpsc::channel();
    *lock(&state.worker.sender).unwrap() = Some(sender);
    assert!(begin_dispatch(&state, false).unwrap());
    let sent: Value = serde_json::from_str(&receiver.try_recv().unwrap()).unwrap();
    assert_eq!(sent["data"]["job_id"], "a");
    // The disk fills while "a" runs; "b" is the next to go out.
    apply_worker_envelope(&state, &envelope("job_started", json!({"job_id": "a"}))).unwrap();
    apply_worker_envelope(
        &state,
        &envelope(
            "job_completed",
            json!({"job_id": "a", "output_path": directory.path().join("out").join("a.png")}),
        ),
    )
    .unwrap();
    lock(&state.worker.cancellation).unwrap().finish().unwrap();
    crate::disk_room::set_test_volume(Some(short_volume()));
    begin_dispatch(&state, false).unwrap();
    crate::disk_room::set_test_volume(None);
    assert!(receiver.try_recv().is_err());
    // The finished result is kept; the next job failed and the one after it was not started.
    assert_eq!(status(&state, "a"), "completed");
    assert_eq!(status(&state, "b"), "failed");
    assert_eq!(status(&state, "c"), "cancelled");
}

#[test]
fn a_disk_that_fills_during_a_batch_stops_the_queue_once_and_keeps_finished_photos() {
    let directory = tempfile::tempdir().unwrap();
    let state = running_batch(directory.path());
    let (sender, receiver) = mpsc::channel();
    *lock(&state.worker.sender).unwrap() = Some(sender);
    lock(&state.worker.cancellation).unwrap().job_ids = vec!["a".into(), "b".into(), "c".into()];
    let apply = |kind: &str, data: Value| apply_worker_envelope(&state, &envelope(kind, data));
    apply("job_started", json!({"job_id": "a"})).unwrap();
    apply("job_started", json!({"job_id": "b"})).unwrap();
    apply("job_started", json!({"job_id": "c"})).unwrap();
    apply(
        "job_completed",
        json!({"job_id": "a", "output_path": "/out/a.png"}),
    )
    .unwrap();
    let reason = "No space was left on the disk to write in \"/out\" (0 KB free).";
    let failure = |id: &str| {
        envelope(
            "job_failed",
            json!({"job_id": id, "error_message": reason, "disk_full": true}),
        )
    };
    assert!(accept_envelope(&state, &failure("b")).unwrap());
    stop_if_disk_full(&state, &failure("b"));
    // "c" still runs: the engine is asked to stop the batch, as for a cancel.
    assert_eq!(status(&state, "a"), "completed");
    assert_eq!(status(&state, "b"), "failed");
    assert_eq!(status(&state, "c"), "cancelling");
    let stop: Value = serde_json::from_str(&receiver.try_recv().unwrap()).unwrap();
    assert_eq!(stop["type"], "cancel_request");
    assert_eq!(stop["data"]["job_id"], "c");
    // Another photo failing for the same reason asks for nothing more.
    stop_if_disk_full(&state, &failure("c"));
    assert!(receiver.try_recv().is_err());
    {
        let runtime = lock(&state.runtime).unwrap();
        assert_eq!(runtime.status_title, "Could not finish");
        assert_eq!(
            runtime.status_detail,
            format!("The queue stopped: the disk is full. {reason}")
        );
    }
    // The photo the stop ended says why, and the status line keeps the reason.
    apply("job_cancelled", json!({"job_id": "c"})).unwrap();
    let jobs = lock(&state.database).unwrap().list_jobs().unwrap();
    let ended = jobs.iter().find(|job| job.id == "c").unwrap();
    assert_eq!(
        (ended.status.as_str(), ended.error.as_str()),
        ("cancelled", "Stopped because the disk is full.")
    );
    let runtime = lock(&state.runtime).unwrap();
    assert_eq!(runtime.status_title, "Could not finish");
    assert!(runtime.status_detail.starts_with("The queue stopped"));
}

#[test]
fn the_next_job_that_is_sent_ends_the_stop() {
    let directory = tempfile::tempdir().unwrap();
    let state = batch_state(directory.path());
    ready_for_batches(&state);
    let (sender, receiver) = mpsc::channel();
    *lock(&state.worker.sender).unwrap() = Some(sender);
    stop_queue_for_full_disk(&state, "full").unwrap();
    assert!(state.worker.disk_stop.load(Ordering::SeqCst));
    lock(&state.database)
        .unwrap()
        .insert_media("d", "/in/d.png", "d", "image")
        .unwrap();
    let mut job = photo_job("d");
    job["data"]["output_path"] = json!(directory.path().join("out").join("d.png"));
    lock(&state.database)
        .unwrap()
        .insert_job("d", "d", &job.to_string())
        .unwrap();
    assert!(begin_dispatch(&state, false).unwrap());
    assert_eq!(
        serde_json::from_str::<Value>(&receiver.try_recv().unwrap()).unwrap()["data"]["job_id"],
        "d"
    );
    assert!(!state.worker.disk_stop.load(Ordering::SeqCst));
    lock(&state.worker.cancellation).unwrap().finish().unwrap();
}
