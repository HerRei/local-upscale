use super::*;

#[test]
fn a_request_that_is_not_json_does_not_block_the_rest_of_the_queue() {
    let directory = tempfile::tempdir().unwrap();
    let db = Database::open(&directory.path().join("queue.db")).unwrap();
    db.insert_media("m", "/tmp/m.png", "m", "image").unwrap();
    db.insert_job("bad", "m", "{ not json").unwrap();
    db.insert_job(
            "good",
            "m",
            r#"{"type":"job_request","data":{"output_path":"/out/a.png","model_path":"/models/x.pth"}}"#,
        )
        .unwrap();
    assert_eq!(
        db.unfinished_output_paths().unwrap(),
        [std::path::PathBuf::from("/out/a.png")]
    );
    assert!(db
        .unfinished_job_needs_model(Path::new("/models/x.pth"))
        .unwrap());
}

#[test]
fn a_stored_image_side_out_of_range_does_not_fail_the_media_list() {
    let directory = tempfile::tempdir().unwrap();
    let db = Database::open(&directory.path().join("queue.db")).unwrap();
    db.connection
            .execute(
                "INSERT INTO media (id, path, name, kind, width, height, created_at) VALUES ('m','/p','n','image', 9999999999, -4, 1)",
                [],
            )
            .unwrap();
    let media = db.list_media().unwrap();
    assert_eq!((media[0].width, media[0].height), (u32::MAX, 0));
}

#[test]
fn a_job_that_has_ended_cannot_be_changed_by_later_updates() {
    let directory = tempfile::tempdir().unwrap();
    let db = Database::open(&directory.path().join("queue.db")).unwrap();
    db.insert_media("m", "/tmp/m.png", "m", "image").unwrap();
    for (id, status) in [
        ("done", "completed"),
        ("broken", "failed"),
        ("stopped", "cancelled"),
    ] {
        db.insert_job(id, "m", "{}").unwrap();
        db.finish_job(id, status, "/out/first.png", "first")
            .unwrap();
        db.set_job_status(id, "running").unwrap();
        db.update_job_progress(id, 40.0).unwrap();
        db.finish_job(id, "failed", "/out/second.png", "second")
            .unwrap();
        let job = db
            .list_jobs()
            .unwrap()
            .into_iter()
            .find(|job| job.id == id)
            .unwrap();
        assert_eq!(job.status, status);
        assert_eq!(job.output_path, "/out/first.png");
        assert_eq!(job.error, "first");
    }
    assert!(db.job_has_ended("done").unwrap());
    db.insert_job("open", "m", "{}").unwrap();
    assert!(!db.job_has_ended("open").unwrap());
    assert!(!db.job_has_ended("unknown").unwrap());
}

#[test]
fn a_job_that_is_being_cancelled_stays_so_when_a_late_start_or_progress_arrives() {
    let directory = tempfile::tempdir().unwrap();
    let db = Database::open(&directory.path().join("queue.db")).unwrap();
    db.insert_media("m", "/tmp/m.png", "m", "image").unwrap();
    db.insert_job("j", "m", "{}").unwrap();
    db.set_job_status("j", "running").unwrap();
    db.mark_cancelling("j").unwrap();
    db.set_job_status("j", "running").unwrap();
    db.update_job_progress("j", 30.0).unwrap();
    let job = db.list_jobs().unwrap().remove(0);
    assert_eq!(job.status, "cancelling");
    db.finish_job("j", "cancelled", "", "Cancelled by the user.")
        .unwrap();
    assert_eq!(db.list_jobs().unwrap().remove(0).status, "cancelled");
}

#[test]
fn a_file_that_is_not_a_database_is_moved_aside_and_a_new_queue_made() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("queue.sqlite3");
    std::fs::write(&path, b"cut off by a sync tool\n".repeat(50)).unwrap();
    let (db, moved) = Database::open_or_set_aside(&path).unwrap();
    let moved = moved.unwrap();
    assert!(moved
        .file_name()
        .unwrap()
        .to_string_lossy()
        .starts_with("queue.damaged-"));
    assert!(moved.to_string_lossy().ends_with(".sqlite3"));
    assert_eq!(
        std::fs::read(&moved).unwrap(),
        b"cut off by a sync tool\n".repeat(50)
    );
    assert!(db.list_media().unwrap().is_empty());
    db.insert_media("m", "/tmp/m.png", "m", "image").unwrap();
}

#[test]
fn a_sound_queue_is_opened_as_it_is() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("queue.sqlite3");
    drop(Database::open(&path).unwrap());
    {
        let db = Database::open(&path).unwrap();
        db.insert_media("m", "/tmp/m.png", "m", "image").unwrap();
    }
    let (db, moved) = Database::open_or_set_aside(&path).unwrap();
    assert!(moved.is_none());
    assert_eq!(db.list_media().unwrap().len(), 1);
}

#[test]
fn a_queue_whose_tables_do_not_fit_is_moved_aside() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("queue.sqlite3");
    Connection::open(&path)
        .unwrap()
        .execute_batch("CREATE TABLE jobs (id TEXT); CREATE TABLE media (id TEXT);")
        .unwrap();
    let (db, moved) = Database::open_or_set_aside(&path).unwrap();
    assert!(moved.unwrap().is_file());
    assert!(db.list_jobs().unwrap().is_empty());
}

#[test]
fn a_queue_that_cannot_be_opened_for_another_reason_is_left_where_it_is() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("queue.sqlite3");
    std::fs::create_dir(&path).unwrap();
    assert!(Database::open_or_set_aside(&path).is_err());
    assert!(path.is_dir());
    assert_eq!(std::fs::read_dir(directory.path()).unwrap().count(), 1);
}

#[test]
fn only_the_newest_damaged_copies_are_kept() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("queue.sqlite3");
    let mut moved = Vec::new();
    for round in 0..5 {
        std::fs::write(&path, format!("damaged {round}")).unwrap();
        moved.push(Database::open_or_set_aside(&path).unwrap().1.unwrap());
    }
    let kept: Vec<_> = moved.iter().filter(|file| file.exists()).collect();
    assert_eq!(kept.len(), KEPT_DAMAGED_FILES);
    assert!(moved[4].exists() && !moved[0].exists());
    assert_eq!(std::fs::read(&moved[4]).unwrap(), b"damaged 4");
}

fn job(id: &str, media: &str, request: &str, ui: &str) -> NewJob {
    NewJob {
        id: id.into(),
        media_id: media.into(),
        request_json: request.into(),
        ui_json: ui.into(),
    }
}

#[test]
fn result_outputs_are_authorized_for_their_own_media_only() {
    let directory = tempfile::tempdir().unwrap();
    let db = Database::open(&directory.path().join("queue.db")).unwrap();
    let path = directory.path().join("output.png");
    std::fs::write(&path, "preview").unwrap();
    let path = path.to_str().unwrap();
    for media in ["m", "other"] {
        db.insert_media(media, &format!("/tmp/{media}.png"), media, "image")
            .unwrap();
    }
    db.insert_job("done", "m", r#"{"type":"job_request"}"#)
        .unwrap();
    db.finish_job("done", "completed", path, "").unwrap();
    db.insert_job(
        "edit",
        "m",
        r#"{"type":"edit_job_request","data":{"model_id":"qwen"}}"#,
    )
    .unwrap();
    db.finish_job("edit", "completed", path, "").unwrap();
    db.insert_job("failed", "m", "{}").unwrap();
    db.finish_job("failed", "failed", "", "broken").unwrap();
    db.insert_job("queued", "m", "{}").unwrap();
    assert_eq!(db.media_result_output("m", "done").unwrap(), path);
    // An Edit result can be shown on its own; the UI decides about a split.
    assert_eq!(db.media_result_output("m", "edit").unwrap(), path);
    for (media, job) in [
        ("other", "done"),
        ("m", "failed"),
        ("m", "queued"),
        ("m", "nope"),
    ] {
        assert!(db.media_result_output(media, job).is_err(), "{media}/{job}");
    }
    std::fs::remove_file(path).unwrap();
    assert!(db.media_result_output("m", "done").is_err());
}

#[cfg(unix)]
#[test]
fn a_result_replaced_by_a_link_is_no_longer_available() {
    let directory = tempfile::tempdir().unwrap();
    let db = Database::open(&directory.path().join("queue.db")).unwrap();
    let output = directory.path().join("output.png");
    let private = directory.path().join("private.png");
    std::fs::write(&output, "result").unwrap();
    std::fs::write(&private, "private").unwrap();
    db.insert_media("m", "/tmp/m.png", "m", "image").unwrap();
    db.insert_job("done", "m", "{}").unwrap();
    db.finish_job("done", "completed", output.to_str().unwrap(), "")
        .unwrap();
    assert!(db.media_result_output("m", "done").is_ok());
    std::fs::remove_file(&output).unwrap();
    std::os::unix::fs::symlink(&private, &output).unwrap();
    assert!(db.media_result_output("m", "done").is_err());
}

#[test]
fn all_media_results_include_unavailable_and_older_outputs() {
    let directory = tempfile::tempdir().unwrap();
    let db = Database::open(&directory.path().join("queue.db")).unwrap();
    for media in ["m", "other"] {
        db.insert_media(media, &format!("/tmp/{media}.png"), media, "image")
            .unwrap();
    }
    let path = directory.path().join("kept.png");
    std::fs::write(&path, "pixels").unwrap();
    for index in 0..55 {
        let id = format!("job-{index}");
        db.insert_job(&id, "m", "{}").unwrap();
        db.finish_job(
            &id,
            "completed",
            if index == 0 {
                path.to_str().unwrap()
            } else {
                "/missing.png"
            },
            "",
        )
        .unwrap();
    }
    let groups = db.list_all_media_results().unwrap();
    let group = groups.iter().find(|g| g.media_id == "m").unwrap();
    assert_eq!(group.results.len(), 55);
    assert_eq!(group.available_count, 1);
    assert_eq!(group.default_result_id.as_deref(), Some("job-0"));
    assert!(groups
        .iter()
        .any(|g| g.media_id == "other" && g.results.is_empty()));
    std::fs::remove_file(path).unwrap();
    assert_eq!(
        db.list_all_media_results()
            .unwrap()
            .iter()
            .find(|g| g.media_id == "m")
            .unwrap()
            .available_count,
        0
    );
}

#[test]
fn history_is_paginated_independently_of_the_bounded_snapshot() {
    let directory = tempfile::tempdir().unwrap();
    let db = Database::open(&directory.path().join("queue.db")).unwrap();
    db.insert_media("m", "/tmp/history.png", "history.png", "image")
        .unwrap();
    let kept = directory.path().join("kept.png");
    std::fs::write(&kept, "pixels").unwrap();
    for index in 0..250 {
        let id = format!("result-{index:03}");
        db.insert_job(&id, "m", r#"{"type":"job_request","data":{"output_scale":2,"model_path":"/models/swinir.pth","stages":[{"kind":"upscale","model_id":"swinir","model_path":"/models/swinir.pth"}]}}"#).unwrap();
        let output = if index == 3 {
            kept.to_str().unwrap()
        } else {
            "/tmp/no-such-output.png"
        };
        db.finish_job(&id, "completed", output, "Blur fix skipped")
            .unwrap();
    }
    // Every job finished in the same millisecond: insertion order decides,
    // not the job id (result-249 sorts after result-25 as text).
    db.connection
        .execute("UPDATE jobs SET updated_at=100", [])
        .unwrap();
    let first = db.list_media_results("m", 50, None).unwrap();
    assert_eq!(first.total_count, 250);
    assert_eq!(first.completed_count, 250);
    assert_eq!(first.available_count, 1);
    assert_eq!(first.results.len(), 50);
    assert_eq!(first.results[0].job_id, "result-249");
    assert_eq!(first.results[0].notes, "Blur fix skipped");
    assert_eq!(first.results[0].kind, "upscale");
    assert_eq!(first.results[0].scale, 2);
    assert!(first.results[0].ui.is_none());
    assert!(!first.results[0].available);
    // The default is the newest result whose file exists, beyond this page.
    assert_eq!(first.default_result_id.as_deref(), Some("result-003"));
    let second = db
        .list_media_results("m", 50, first.next_before.as_deref())
        .unwrap();
    assert_eq!(second.results[0].job_id, "result-199");
    let mut before = second.next_before;
    let mut pages = 2;
    while let Some(cursor) = before {
        let page = db.list_media_results("m", 50, Some(&cursor)).unwrap();
        before = page.next_before;
        pages += 1;
    }
    assert_eq!(pages, 5);
    assert_eq!(db.list_jobs().unwrap().len(), 200);
}

#[test]
fn history_lists_failed_attempts_with_their_reason_but_never_as_default() {
    let directory = tempfile::tempdir().unwrap();
    let db = Database::open(&directory.path().join("queue.db")).unwrap();
    db.insert_media("m", "/tmp/a.png", "a.png", "image")
        .unwrap();
    let output = directory.path().join("a_upscaled_4x.png");
    std::fs::write(&output, "pixels").unwrap();
    db.insert_job("done", "m", "{}").unwrap();
    db.finish_job("done", "completed", output.to_str().unwrap(), "")
        .unwrap();
    db.insert_job("broken", "m", "{}").unwrap();
    db.finish_job("broken", "failed", "", "Out of memory")
        .unwrap();
    db.insert_job("running", "m", "{}").unwrap();
    db.set_job_status("running", "running").unwrap();
    let history = db.list_media_results("m", 50, None).unwrap();
    assert_eq!(
        history
            .results
            .iter()
            .map(|r| (r.job_id.as_str(), r.status.as_str(), r.notes.as_str()))
            .collect::<Vec<_>>(),
        vec![
            ("broken", "failed", "Out of memory"),
            ("done", "completed", "")
        ]
    );
    assert_eq!(history.default_result_id.as_deref(), Some("done"));
    assert_eq!((history.total_count, history.completed_count), (2, 1));
}

#[test]
fn admission_keeps_ui_metadata_out_of_worker_requests_and_rolls_back() {
    let directory = tempfile::tempdir().unwrap();
    let db = Database::open(&directory.path().join("queue.db")).unwrap();
    db.insert_media("m", "/tmp/a.png", "a.png", "image")
        .unwrap();
    let request =
        r#"{"type":"job_request","data":{"model_path":"swinir.pth","output_path":"/out/a.png"}}"#;
    let ui = r#"{"quality":"custom","content":"photo","recipe_name":"My recipe","fixes":["blur"]}"#;
    // A failing insert (a duplicate id) leaves nothing behind.
    assert!(db
        .admit_jobs(&["m"], |_| Ok(vec![
            job("one", "m", request, ui),
            job("one", "m", request, ui)
        ]))
        .is_err());
    assert!(db.list_jobs().unwrap().is_empty());
    // A failing build leaves nothing behind either.
    assert!(db
        .admit_jobs(&["m"], |_| Err(AppError::Validation("no".into())))
        .is_err());
    db.admit_jobs(&["m"], |reserved| {
        assert!(reserved.is_empty());
        Ok(vec![job("one", "m", request, ui)])
    })
    .unwrap();
    assert_eq!(db.next_queued_job().unwrap().unwrap().request_json, request);
    assert_eq!(db.list_jobs().unwrap()[0].model_label, "swinir");
    // The in-flight check is part of the same admission.
    assert!(db
        .admit_jobs(&["m"], |_| Ok(vec![job("two", "m", request, ui)]))
        .is_err());
    db.insert_media("n", "/tmp/n.png", "n.png", "image")
        .unwrap();
    db.admit_jobs(&["n"], |reserved| {
        assert!(reserved.contains(Path::new("/out/a.png")));
        Ok(vec![])
    })
    .unwrap();
    db.finish_job("one", "failed", "", "broken").unwrap();
    let row = db
        .list_media_results("m", 50, None)
        .unwrap()
        .results
        .remove(0);
    assert_eq!(row.ui.unwrap().recipe_name, "My recipe");
    assert_eq!(row.status, "failed");
    assert_eq!(row.notes, "broken");
}

#[test]
fn ui_metadata_leaves_the_timing_profile_alone() {
    let directory = tempfile::tempdir().unwrap();
    let db = Database::open(&directory.path().join("queue.db")).unwrap();
    for media in ["a", "b"] {
        db.insert_media(media, &format!("/tmp/{media}.png"), media, "image")
            .unwrap();
        db.update_media_probe(
            &format!("/tmp/{media}.png"),
            "image",
            512,
            512,
            1,
            0.0,
            0.0,
            "",
            "",
            "",
        )
        .unwrap();
    }
    let request = |media: &str| {
        format!(
            r#"{{"type":"job_request","data":{{"job_id":"{media}","image_path":"/tmp/{media}.png","output_path":"/out/{media}.png","model_path":"m.pth","device":"cpu","tile_size":256}}}}"#
        )
    };
    db.admit_jobs(&["a"], |_| {
        Ok(vec![job("a", "a", &request("a"), r#"{"quality":"best"}"#)])
    })
    .unwrap();
    db.admit_jobs(&["b"], |_| {
        Ok(vec![job(
            "b",
            "b",
            &request("b"),
            r#"{"quality":"quick","recipe_name":"Mine"}"#,
        )])
    })
    .unwrap();
    let jobs = db.list_jobs().unwrap();
    assert!(!jobs[0].work_key.is_empty());
    assert_eq!(jobs[0].work_key, jobs[1].work_key);
    assert_eq!(jobs[0].work_units, jobs[1].work_units);
}

#[test]
fn trials_estimate_only_measured_matching_requests() {
    let directory = tempfile::tempdir().unwrap();
    let db = Database::open(&directory.path().join("queue.db")).unwrap();
    db.insert_media("m", "/tmp/a.png", "a.png", "image")
        .unwrap();
    db.update_media_probe("/tmp/a.png", "image", 512, 512, 1, 0.0, 0.0, "", "", "")
        .unwrap();
    let request = r#"{"type":"job_request","data":{"model_path":"model.pth","device":"cpu","tile_size":256}}"#;
    let media = db.get_media("m").unwrap().unwrap();
    assert!(db
        .estimate_request_seconds(request, &media)
        .unwrap()
        .is_none());
    db.insert_job("j", "m", request).unwrap();
    db.finish_job("j", "completed", "out.png", "").unwrap();
    db.record_job_duration("j", 12.0).unwrap();
    // One run may have loaded the model: no estimate from it alone.
    assert!(db
        .estimate_request_seconds(request, &media)
        .unwrap()
        .is_none());
    db.insert_job("k", "m", request).unwrap();
    db.finish_job("k", "completed", "out2.png", "").unwrap();
    db.record_job_duration("k", 4.0).unwrap();
    assert_eq!(
        db.estimate_request_seconds(request, &media).unwrap(),
        Some(4.0)
    );
    assert!(db
        .estimate_request_seconds(&request.replace("cpu", "mps"), &media)
        .unwrap()
        .is_none());
}

#[test]
fn cancel_media_preserves_other_media_and_completed_outputs() {
    let directory = tempfile::tempdir().unwrap();
    let db = Database::open(&directory.path().join("queue.db")).unwrap();
    for media in ["m", "other"] {
        db.insert_media(media, &format!("/tmp/{media}.png"), media, "image")
            .unwrap();
    }
    for (id, media) in [
        ("done", "m"),
        ("active", "m"),
        ("pending", "m"),
        ("unrelated", "other"),
    ] {
        db.insert_job(id, media, "{}").unwrap();
    }
    db.finish_job("done", "completed", "/tmp/done.png", "")
        .unwrap();
    db.set_job_status("active", "running").unwrap();
    assert_eq!(
        db.cancel_jobs_for_media("m").unwrap().as_deref(),
        Some("active")
    );
    let jobs = db.list_jobs().unwrap();
    for (id, status) in [
        ("done", "completed"),
        ("active", "cancelling"),
        ("pending", "cancelled"),
        ("unrelated", "queued"),
    ] {
        assert_eq!(jobs.iter().find(|job| job.id == id).unwrap().status, status);
    }
    assert_eq!(db.next_queued_job().unwrap().unwrap().id, "unrelated");
    // A media without an active job has nothing to signal; asking twice
    // still names the job that is cancelling.
    assert_eq!(db.cancel_jobs_for_media("other").unwrap(), None);
    assert!(db.next_queued_job().unwrap().is_none());
    assert_eq!(
        db.cancel_jobs_for_media("m").unwrap().as_deref(),
        Some("active")
    );
}

#[test]
fn queued_multi_fix_snapshot_protects_each_stage_after_reopening() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("queue.db");
    let mut request = serde_json::json!({"data":{"stages":[
        {"model_path":"/models/blur"}, {"model_path":"/models/jpeg"}, {"model_path":"/models/noise"}, {"model_path":"/models/main"}, {"model_path":"/models/face"}
    ]}});
    {
        let db = Database::open(&path).unwrap();
        db.insert_media("m", "/tmp/a.png", "a.png", "image")
            .unwrap();
        db.insert_job("j", "m", &request.to_string()).unwrap();
    }
    request["data"]["stages"][0]["model_path"] = serde_json::json!("/models/changed");
    let db = Database::open(&path).unwrap();
    for model in ["blur", "jpeg", "noise", "main", "face"] {
        assert!(db
            .unfinished_job_needs_model(Path::new(&format!("/models/{model}")))
            .unwrap());
    }
    assert!(!db
        .unfinished_job_needs_model(Path::new("/models/changed"))
        .unwrap());
    assert_eq!(
        db.unfinished_requests().unwrap()[0]["data"]["stages"][0]["model_path"],
        "/models/blur"
    );
}

#[test]
fn the_queue_window_is_in_order_and_cancelling_spares_photos_that_never_started() {
    let directory = tempfile::tempdir().unwrap();
    let db = Database::open(&directory.path().join("queue.db")).unwrap();
    db.insert_media("m", "/tmp/a.png", "a.png", "image")
        .unwrap();
    for id in ["a", "b", "c", "d"] {
        db.insert_job(id, "m", "{}").unwrap();
    }
    db.set_job_status("a", "running").unwrap();
    let window: Vec<String> = db
        .queued_job_window(2)
        .unwrap()
        .into_iter()
        .map(|job| job.id)
        .collect();
    assert_eq!(window, ["b", "c"]);
    db.cancel_queued_jobs().unwrap();
    for id in ["a", "b"] {
        db.mark_cancelling(id).unwrap();
    }
    let status = |id: &str| {
        db.list_jobs()
            .unwrap()
            .into_iter()
            .find(|job| job.id == id)
            .unwrap()
            .status
    };
    assert_eq!(status("a"), "cancelling");
    assert_eq!(status("b"), "cancelled");
}

#[test]
fn unfinished_jobs_protect_all_model_paths() {
    let directory = tempfile::tempdir().unwrap();
    let db = Database::open(&directory.path().join("queue.db")).unwrap();
    db.insert_media("m", "/tmp/a.png", "a.png", "image")
        .unwrap();
    db.insert_job("j", "m", r#"{"data":{"model_path":"/models/main","face_model_path":"/models/face","stages":[{"model_path":"/models/stage"}]}}"#).unwrap();
    for path in ["/models/main", "/models/face", "/models/stage"] {
        assert!(
            db.unfinished_job_needs_model(Path::new(path)).unwrap(),
            "unfinished job must protect {path}"
        );
    }
    assert!(!db
        .unfinished_job_needs_model(Path::new("/models/other"))
        .unwrap());
    db.set_job_status("j", "completed").unwrap();
    assert!(!db
        .unfinished_job_needs_model(Path::new("/models/main"))
        .unwrap());
}

#[test]
fn hdr_migration_preserves_old_queue_and_retry_clears_failure() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("queue.sqlite3");
    let db = Database::open(&path).unwrap();
    db.insert_media("m1", "/tmp/portrait.mov", "portrait.mov", "video")
        .unwrap();
    db.insert_job("j1", "m1", r#"{"type":"video_job_request"}"#)
        .unwrap();
    // Recreate the pre-HDR schema with a real queued job and selected source.
    db.connection
        .execute("ALTER TABLE media DROP COLUMN hdr_format", [])
        .unwrap();
    db.connection
        .execute("ALTER TABLE media DROP COLUMN audio_warning", [])
        .unwrap();
    drop(db);
    let migrated = Database::open(&path).unwrap();
    assert_eq!(migrated.next_queued_job().unwrap().unwrap().id, "j1");
    let old = migrated.get_media("m1").unwrap().unwrap();
    assert!(old.selected);
    assert_eq!(old.hdr_format, "");
    migrated
        .fail_media_probe(&old.path, "Previous import failed")
        .unwrap();
    migrated.start_media_probe(&old.path).unwrap();
    let pending = migrated.get_media("m1").unwrap().unwrap();
    assert_eq!(pending.probe_status, "pending");
    assert!(pending.error.is_empty());
    migrated
        .update_media_probe(
            &old.path,
            "video",
            2160,
            3840,
            14047,
            59.94,
            234.33,
            "data:image/jpeg;base64,test",
            "HLG",
            "Standard audio will be kept.",
        )
        .unwrap();
    drop(migrated);
    let reopened = Database::open(&path).unwrap();
    let media = reopened.list_media().unwrap().remove(0);
    assert_eq!(media.hdr_format, "HLG");
    assert_eq!(media.audio_warning, "Standard audio will be kept.");
    assert_eq!(media.probe_status, "ready");
    assert_eq!((media.width, media.height), (2160, 3840));
    assert_eq!(
        reopened
            .get_media_by_path(&media.path)
            .unwrap()
            .unwrap()
            .hdr_format,
        "HLG"
    );
}

#[test]
fn queue_is_fifo_and_interrupted_jobs_are_recoverable() {
    let directory = tempfile::tempdir().unwrap();
    let db = Database::open(&directory.path().join("queue.sqlite3")).unwrap();
    db.insert_media("m1", "/tmp/a.png", "a.png", "image")
        .unwrap();
    db.insert_media("m2", "/tmp/b.png", "b.png", "image")
        .unwrap();
    db.insert_job("j1", "m1", r#"{"type":"job_request"}"#)
        .unwrap();
    db.insert_job("j2", "m2", r#"{"type":"job_request"}"#)
        .unwrap();
    assert_eq!(db.next_queued_job().unwrap().unwrap().id, "j1");
    db.set_job_status("j1", "running").unwrap();
    assert_eq!(db.next_queued_job().unwrap().unwrap().id, "j2");
    drop(db);

    let reopened = Database::open(&directory.path().join("queue.sqlite3")).unwrap();
    let jobs = reopened.list_jobs().unwrap();
    assert_eq!(
        jobs.iter().find(|job| job.id == "j1").unwrap().status,
        "interrupted"
    );
    assert_eq!(reopened.next_queued_job().unwrap().unwrap().id, "j2");
}

#[test]
fn queue_timings_migrate_and_large_batches_keep_all_pending_and_owned_results() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("queue.sqlite3");
    let db = Database::open(&path).unwrap();
    db.insert_media("old", "/tmp/old.png", "old.png", "image")
        .unwrap();
    db.insert_media("batch", "/tmp/batch.png", "batch.png", "image")
        .unwrap();
    db.insert_job("finished", "old", r#"{"type":"job_request","data":{}}"#)
        .unwrap();
    db.finish_job("finished", "completed", "/tmp/result.png", "")
        .unwrap();
    for index in 0..250 {
        db.insert_job(
            &format!("pending-{index}"),
            "batch",
            r#"{"type":"job_request","data":{}}"#,
        )
        .unwrap();
    }
    db.connection
        .execute("ALTER TABLE jobs DROP COLUMN elapsed_seconds", [])
        .unwrap();
    drop(db);
    let db = Database::open(&path).unwrap();
    let jobs = db.list_jobs().unwrap();
    assert_eq!(
        jobs.iter().filter(|job| job.status == "queued").count(),
        250
    );
    assert!(jobs
        .iter()
        .any(|job| job.id == "finished" && job.output_path == "/tmp/result.png"));
    assert!(db.is_completed_output("/tmp/result.png").unwrap());
    assert!(!db.is_completed_output("/tmp/batch.png").unwrap());
    assert!(!db.is_completed_output("").unwrap());
    assert_eq!(db.next_queued_job().unwrap().unwrap().id, "pending-0");
    db.record_job_duration("finished", 24.5).unwrap();
    db.record_job_duration("finished", f64::NAN).unwrap();
    db.record_job_duration("pending-0", 3.0).unwrap();
    drop(db);
    let jobs = Database::open(&path).unwrap().list_jobs().unwrap();
    assert_eq!(
        jobs.iter()
            .find(|job| job.id == "finished")
            .unwrap()
            .elapsed_seconds,
        24.5
    );
    assert_eq!(
        jobs.iter()
            .find(|job| job.id == "pending-0")
            .unwrap()
            .elapsed_seconds,
        0.0
    );
}

#[test]
fn queued_jobs_carry_the_typical_speed_of_their_comparable_runs() {
    let directory = tempfile::tempdir().unwrap();
    let db = Database::open(&directory.path().join("queue.db")).unwrap();
    db.insert_media("m", "/tmp/a.png", "a.png", "image")
        .unwrap();
    db.update_media_probe("/tmp/a.png", "image", 512, 512, 1, 0.0, 0.0, "", "", "")
        .unwrap();
    let request = r#"{"type":"job_request","data":{"model_path":"model.pth","device":"cpu","tile_size":256}}"#;
    // The first run loaded the model (340 s for 4 tiles), the others are warm.
    for (id, seconds) in [("first", 340.0), ("second", 4.0), ("third", 4.4)] {
        db.insert_job(id, "m", request).unwrap();
        db.finish_job(id, "completed", &format!("{id}.png"), "")
            .unwrap();
        db.record_job_duration(id, seconds).unwrap();
    }
    db.insert_job("next", "m", request).unwrap();
    let jobs = db.list_jobs().unwrap();
    let next = jobs.iter().find(|job| job.id == "next").unwrap();
    assert!((next.seconds_per_unit - 1.0).abs() < 0.11);
    let other = request.replace("cpu", "mps");
    db.insert_job("other", "m", &other).unwrap();
    let jobs = db.list_jobs().unwrap();
    assert_eq!(
        jobs.iter()
            .find(|job| job.id == "other")
            .unwrap()
            .seconds_per_unit,
        0.0
    );
}

#[test]
fn only_the_scratch_folders_recorded_for_unfinished_jobs_come_back_after_a_stop() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("queue.db");
    let request = |name: &str| {
        format!(r#"{{"type":"job_request","data":{{"output_path":"/out/{name}.png"}}}}"#)
    };
    {
        let db = Database::open(&path).unwrap();
        db.insert_media("m", "/tmp/m.png", "m", "image").unwrap();
        for id in ["running", "unrecorded", "done", "waiting"] {
            db.insert_job(id, "m", &request(id)).unwrap();
        }
        db.set_job_status("running", "running").unwrap();
        db.set_job_status("unrecorded", "running").unwrap();
        db.record_scratch("running", Path::new("/out/.localsr-job-1"))
            .unwrap();
        db.record_scratch("done", Path::new("/out/.localsr-job-2"))
            .unwrap();
        db.record_scratch("waiting", Path::new("/out/.localsr-job-3"))
            .unwrap();
        db.finish_job("done", "completed", "/out/done.png", "")
            .unwrap();
        // The host stops here.
    }
    let mut db = Database::open(&path).unwrap();
    let mut found: Vec<(String, String, String)> = db
        .take_interrupted_scratch()
        .into_iter()
        .map(|record| {
            (
                record.id,
                record.output.to_string_lossy().into_owned(),
                record.scratch.to_string_lossy().into_owned(),
            )
        })
        .collect();
    found.sort();
    assert_eq!(
        found,
        [
            (
                "running".into(),
                "/out/running.png".into(),
                "/out/.localsr-job-1".into()
            ),
            (
                "waiting".into(),
                "/out/waiting.png".into(),
                "/out/.localsr-job-3".into()
            ),
        ]
    );
    assert!(db.take_interrupted_scratch().is_empty());
    db.forget_scratch("running").unwrap();
    drop(db);
    let mut db = Database::open(&path).unwrap();
    let ids: Vec<String> = db
        .take_interrupted_scratch()
        .into_iter()
        .map(|record| record.id)
        .collect();
    assert_eq!(ids, ["waiting"]);
}

fn queue_of_four(db: &Database) {
    for n in 1..=4 {
        db.insert_media(&format!("m{n}"), &format!("/tmp/m{n}.png"), "m", "image")
            .unwrap();
    }
    db.insert_job("j1", "m1", "{}").unwrap();
    db.finish_job("j1", "completed", "/out/1.png", "").unwrap();
    db.insert_job("j2", "m2", "{}").unwrap();
    db.set_job_status("j2", "running").unwrap();
    db.insert_job("j3", "m3", "{}").unwrap();
}

#[test]
fn removing_a_queued_picture_takes_its_job_out_of_the_next_dispatch() {
    let directory = tempfile::tempdir().unwrap();
    let db = Database::open(&directory.path().join("queue.db")).unwrap();
    queue_of_four(&db);
    db.insert_job("j4", "m4", "{}").unwrap();
    db.remove_media_guarded("m3", &[]).unwrap();
    assert_eq!(db.next_queued_job().unwrap().unwrap().id, "j4");
    assert!(!db.list_jobs().unwrap().iter().any(|job| job.id == "j3"));
}

#[test]
fn removing_a_finished_picture_keeps_the_run_going() {
    let directory = tempfile::tempdir().unwrap();
    let db = Database::open(&directory.path().join("queue.db")).unwrap();
    queue_of_four(&db);
    db.remove_media_guarded("m1", &[]).unwrap();
    let jobs = db.list_jobs().unwrap();
    assert!(jobs
        .iter()
        .any(|job| job.id == "j2" && job.status == "running"));
    assert!(jobs
        .iter()
        .any(|job| job.id == "j3" && job.status == "queued"));
    assert!(db.has_inflight_jobs().unwrap());
}

#[test]
fn removing_a_running_picture_is_refused_with_the_sentence() {
    let directory = tempfile::tempdir().unwrap();
    let db = Database::open(&directory.path().join("queue.db")).unwrap();
    queue_of_four(&db);
    let error = db.remove_media_guarded("m2", &[]).unwrap_err();
    assert_eq!(
        error.to_string(),
        "Cannot remove while it is running. Cancel the run instead."
    );
    assert!(db.get_media("m2").unwrap().is_some());
}

#[test]
fn a_queued_photo_the_engine_already_holds_is_not_removed() {
    let directory = tempfile::tempdir().unwrap();
    let db = Database::open(&directory.path().join("queue.db")).unwrap();
    queue_of_four(&db);
    assert!(db
        .remove_media_guarded("m3", &["j2".to_string(), "j3".to_string()])
        .is_err());
    assert!(db.get_media("m3").unwrap().is_some());
}

#[test]
fn removal_never_leaves_a_job_for_a_missing_picture_and_selects_the_running_one() {
    let directory = tempfile::tempdir().unwrap();
    let db = Database::open(&directory.path().join("queue.db")).unwrap();
    queue_of_four(&db);
    db.select_media("m3").unwrap();
    db.remove_media_guarded("m3", &[]).unwrap();
    let orphans: i64 = db
        .connection
        .query_row(
            "SELECT COUNT(*) FROM jobs WHERE media_id NOT IN (SELECT id FROM media)",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(orphans, 0);
    let selected: Vec<String> = db
        .list_media()
        .unwrap()
        .into_iter()
        .filter(|media| media.selected)
        .map(|media| media.id)
        .collect();
    assert_eq!(selected, ["m2"]);
}

#[test]
fn a_finished_edit_is_listed_with_the_results_of_its_image() {
    let directory = tempfile::tempdir().unwrap();
    let db = Database::open(&directory.path().join("queue.db")).unwrap();
    db.insert_media("m", "/tmp/m.png", "m", "image").unwrap();
    let output = directory.path().join("m_localsr_edit_1234abcd.png");
    std::fs::write(&output, "pixels").unwrap();
    let request = serde_json::json!({"type": "edit_job_request", "data": {
        "job_id": "edit", "image_path": "/tmp/m.png", "output_path": output,
        "model_id": "flux2_klein_4b", "prompt": "make it dusk", "max_dimension": 512}});
    db.insert_job("edit", "m", &request.to_string()).unwrap();
    db.finish_job("edit", "completed", output.to_str().unwrap(), "")
        .unwrap();
    db.record_job_duration("edit", 48.0).unwrap();
    let groups = db.list_all_media_results().unwrap();
    let group = groups.iter().find(|g| g.media_id == "m").unwrap();
    assert_eq!(group.results.len(), 1);
    assert_eq!(group.results[0].kind, "edit");
    assert_eq!(group.results[0].prompt, "make it dusk");
    assert_eq!(group.results[0].max_dimension, 512);
    assert_eq!(group.results[0].elapsed_seconds, 48.0);
    assert_eq!(group.completed_count, 1);
    assert_eq!(group.available_count, 1);
}

#[test]
fn clearing_the_results_hides_them_for_good_and_keeps_files_and_speed_history() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("queue.db");
    let output = directory.path().join("kept.png");
    std::fs::write(&output, "pixels").unwrap();
    {
        let db = Database::open(&path).unwrap();
        db.insert_media("m", "/tmp/m.png", "m", "image").unwrap();
        db.insert_job("done", "m", r#"{"type":"job_request"}"#)
            .unwrap();
        db.finish_job("done", "completed", output.to_str().unwrap(), "")
            .unwrap();
        db.record_job_duration("done", 12.0).unwrap();
        db.hide_all_results(&[]).unwrap();
        let group = &db.list_all_media_results().unwrap()[0];
        assert!(group.results.is_empty() && group.default_result_id.is_none());
        let page = db.list_media_results("m", 50, None).unwrap();
        assert!(page.results.is_empty());
        assert_eq!((page.completed_count, page.total_count), (0, 0));
        assert!(db.media_result_output("m", "done").is_err());
        // The speed history reads the same job and is untouched.
        let jobs = db.list_jobs().unwrap();
        assert_eq!(jobs.len(), 1);
        assert_eq!(jobs[0].elapsed_seconds, 12.0);
    }
    assert!(output.is_file());
    // Still hidden after the list is read again from the file.
    let db = Database::open(&path).unwrap();
    assert!(db.list_all_media_results().unwrap()[0].results.is_empty());
    // A later result shows up as usual.
    db.insert_job("next", "m", "{}").unwrap();
    db.finish_job("next", "completed", output.to_str().unwrap(), "")
        .unwrap();
    assert_eq!(db.list_all_media_results().unwrap()[0].results.len(), 1);
}

#[test]
fn results_are_not_cleared_while_a_picture_is_being_worked_on() {
    let directory = tempfile::tempdir().unwrap();
    let db = Database::open(&directory.path().join("queue.db")).unwrap();
    db.insert_media("m", "/tmp/m.png", "m", "image").unwrap();
    db.insert_job("old", "m", "{}").unwrap();
    db.finish_job("old", "completed", "/x.png", "").unwrap();
    db.insert_job("now", "m", "{}").unwrap();
    db.set_job_status("now", "running").unwrap();
    let error = db.hide_all_results(&[]).unwrap_err().to_string();
    assert!(
        error.contains("Cannot remove while it is running"),
        "{error}"
    );
    assert_eq!(db.list_all_media_results().unwrap()[0].results.len(), 1);
    // A photo the engine still holds counts, whatever its row says.
    db.finish_job("now", "completed", "/y.png", "").unwrap();
    assert!(db.hide_all_results(&["now".to_owned()]).is_err());
    db.hide_all_results(&[]).unwrap();
}
