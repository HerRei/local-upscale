use super::*;
use crate::paths::AppPaths;
use crate::types::{RecipeStage, StepStageSelection};

#[test]
fn the_download_page_is_the_one_the_release_publishes() {
    let release: serde_json::Value =
        serde_json::from_str(include_str!("../../../../ci/public-beta-release.json")).unwrap();
    assert_eq!(release["download_page"], DOWNLOAD_PAGE_URL);
}

#[cfg(unix)]
#[test]
fn catalog_models_can_be_found_and_removed_through_a_linked_model_folder() {
    use std::os::unix::fs::symlink;

    let directory = tempfile::tempdir().unwrap();
    let mut paths = AppPaths::under(directory.path());
    let target = directory.path().join("other-volume-models");
    fs::create_dir_all(&target).unwrap();
    fs::create_dir_all(&paths.next_root).unwrap();
    symlink(&target, &paths.model_root).unwrap();

    let model = crate::catalog::bundled_catalog().unwrap().models[0].clone();
    let checkpoint = target.join(&model.filename);
    fs::File::create(&checkpoint)
        .unwrap()
        .set_len(model.size_bytes)
        .unwrap();
    let unrelated = target.join("my-own-file");
    fs::write(&unrelated, b"keep").unwrap();

    let state = Arc::new(AppState::from_paths(paths.clone()).unwrap());
    assert!(lock(&state.catalog).unwrap().models[0].installed);
    assert!(state.grants.is_app_owned(&checkpoint).unwrap());
    remove_model_checked(&state, &model.model_id).unwrap();
    assert!(!checkpoint.exists());
    assert_eq!(fs::read(&unrelated).unwrap(), b"keep");
    assert!(fs::symlink_metadata(&paths.model_root)
        .unwrap()
        .file_type()
        .is_symlink());
    assert!(!lock(&state.catalog).unwrap().models[0].installed);

    let bundle = lock(&state.catalog).unwrap().interpolation_models[0].clone();
    let bundle_dir = target.join(&bundle.family);
    fs::create_dir_all(&bundle_dir).unwrap();
    for file in &bundle.files {
        fs::File::create(bundle_dir.join(&file.filename))
            .unwrap()
            .set_len(file.size_bytes)
            .unwrap();
    }
    state.refresh_catalog().unwrap();
    assert!(remove_catalog_bundle(&state, &bundle.model_id).unwrap());
    for file in &bundle.files {
        assert!(!bundle_dir.join(&file.filename).exists());
    }
    assert!(unrelated.is_file());

    // A direct custom root has the same catalog and grant behavior.
    fs::File::create(&checkpoint)
        .unwrap()
        .set_len(model.size_bytes)
        .unwrap();
    paths.model_root = target.clone();
    let state = AppState::from_paths(paths.clone()).unwrap();
    assert!(lock(&state.catalog).unwrap().models[0].installed);
    assert!(state.grants.is_app_owned(&unrelated).unwrap());
    remove_model_checked(&state, &model.model_id).unwrap();
    assert!(!checkpoint.exists());
    assert!(unrelated.is_file());

    // Configured via settings.model_directory (both direct and symlinked) behaves identically.
    let link_setting = directory.path().join("settings-link");
    symlink(&target, &link_setting).unwrap();
    fs::File::create(&checkpoint)
        .unwrap()
        .set_len(model.size_bytes)
        .unwrap();
    let paths = AppPaths::under(&directory.path().join("settings-profile"));
    fs::create_dir_all(&paths.model_root).unwrap();
    crate::settings::save(
        &paths,
        &UiSettings {
            model_directory: link_setting.to_string_lossy().into_owned(),
            ..UiSettings::default()
        },
        &[],
    )
    .unwrap();
    let state = AppState::from_paths(paths).unwrap();
    assert!(lock(&state.catalog).unwrap().models[0].installed);
    assert!(state.grants.is_app_owned(&checkpoint).unwrap());
    remove_model_checked(&state, &model.model_id).unwrap();
    assert!(!checkpoint.exists());
    assert!(unrelated.is_file());
}

#[test]
fn save_settings_keeps_host_managed_model_directory_and_gpu_notice_dismissal() {
    let dir = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(dir.path());
    let custom = dir.path().join("custom-models");
    fs::create_dir_all(&custom).unwrap();
    let initial = UiSettings {
        model_directory: custom.to_string_lossy().into_owned(),
        gpu_download_notice_dismissed: true,
        ..UiSettings::default()
    };
    crate::settings::save(&paths, &initial, &[]).unwrap();

    let state = AppState::from_paths(paths.clone()).unwrap();
    save_settings_checked(
        &state,
        UiSettings {
            model_directory: "/tmp/should-not-overwrite".into(),
            gpu_download_notice_dismissed: false,
            output_scale: 2,
            ..UiSettings::default()
        },
    )
    .unwrap();

    let saved = lock(&state.settings).unwrap().clone();
    assert_eq!(saved.model_directory, custom.to_string_lossy());
    assert!(saved.gpu_download_notice_dismissed);
    assert_eq!(saved.output_scale, 2);
}

#[test]
fn multi_fix_requires_a_capable_engine_before_queueing() {
    let mut options = input("upscale");
    options.fix_stages = serde_json::from_value(json!([
        {"fix":"blur","model_id":"blur"}, {"fix":"noise","model_id":"noise"}
    ]))
    .unwrap();
    let mut engine: crate::types::EngineInfo = serde_json::from_value(json!({
            "protocol_version":1,"minimum_protocol_version":1,"engine_id":"test","engine_version":"test",
            "features":["image"],"model_formats":[],"video_engines":[]
        })).unwrap();
    assert!(validate_fix_engine(&options, Some(&engine)).is_err());
    engine.features.push("image_multi_fix".into());
    assert!(validate_fix_engine(&options, Some(&engine)).is_ok());
}

#[test]
fn output_bit_depth_16_requires_image_16bit_feature_and_png_or_tiff() {
    let mut options = input("upscale");
    options.output_bit_depth = 16;
    options.output_format = "png".into();

    let mut engine: crate::types::EngineInfo = serde_json::from_value(json!({
        "protocol_version": 1,
        "minimum_protocol_version": 1,
        "engine_id": "test",
        "engine_version": "test",
        "features": ["image"],
        "model_formats": [],
        "video_engines": []
    }))
    .unwrap();
    assert!(validate_bit_depth_engine(&options, Some(&engine)).is_err());
    assert!(validate_bit_depth_engine(&options, None).is_err());
    // Default 8-bit never requires the feature so old engines stay supported.
    assert!(validate_bit_depth_engine(&input("upscale"), Some(&engine)).is_ok());

    engine.features.push("image_16bit".into());
    assert!(validate_bit_depth_engine(&options, Some(&engine)).is_ok());

    for bad_fmt in ["jpg", "webp", "heic"] {
        options.output_format = bad_fmt.into();
        assert!(validate_start_input(&options, false).is_err(), "{bad_fmt}");
    }
    for good_fmt in ["png", "tif"] {
        options.output_format = good_fmt.into();
        assert!(validate_start_input(&options, false).is_ok(), "{good_fmt}");
    }
    // Missing output_bit_depth in saved v2 settings defaults to 8 without a schema bump.
    let v2_without_depth = json!({
        "schema_version": 2,
        "ui": {"output_format": "png"},
        "recipes": []
    });
    let dir = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(dir.path());
    fs::create_dir_all(&paths.next_root).unwrap();
    fs::write(&paths.settings, v2_without_depth.to_string()).unwrap();
    let (loaded_settings, _) = crate::settings::load(&paths);
    assert_eq!(loaded_settings.output_bit_depth, 8);
}

#[test]
fn multi_fix_serialization_preserves_five_stages_and_restore_primary_role() {
    let prefix: Vec<_> = [
        ("blur", "restore"),
        ("jpeg", "deblock"),
        ("noise", "restore"),
    ]
    .iter()
    .map(|(fix, kind)| ResolvedImageStage {
        kind,
        fix: (*fix).into(),
        model_id: (*fix).into(),
        path: format!("/models/{fix}"),
        params: None,
        mask_path: None,
    })
    .collect();
    let mut options = input("upscale");
    let stages = serde_json::to_value(image_pipeline_stages(
        &prefix,
        &[],
        "main",
        "/models/main",
        Some("/models/face"),
        "face",
        false,
        &options,
    ))
    .unwrap();
    let stages = stages.as_array().unwrap();
    assert_eq!(stages.len(), 5);
    assert_eq!(
        stages
            .iter()
            .map(|stage| stage["model_id"].as_str().unwrap())
            .collect::<Vec<_>>(),
        ["blur", "jpeg", "noise", "main", "face"]
    );
    assert_eq!(stages[3]["role"], "primary");
    options.task = "denoise".into();
    options.fix_stages =
        serde_json::from_value(json!([{"fix":"noise","model_id":"noise"}])).unwrap();
    let stages = serde_json::to_value(image_pipeline_stages(
        &prefix[..2],
        &[],
        "noise",
        "/models/noise",
        None,
        "",
        false,
        &options,
    ))
    .unwrap();
    let stages = stages.as_array().unwrap();
    assert_eq!(stages.len(), 3);
    assert_eq!(stages[2]["role"], "primary");
    assert_eq!(stages[2]["fix"], "noise");
}

#[test]
fn multi_fix_host_resolves_every_catalog_stage_and_rejects_invalid_choices() {
    use crate::types::FixStageSelection;
    let directory = tempfile::tempdir().unwrap();
    let paths = crate::paths::AppPaths::under(directory.path());
    fs::create_dir_all(&paths.next_root).unwrap();
    fs::create_dir_all(&paths.model_root).unwrap();
    let state = AppState::from_paths(paths).unwrap();
    {
        let mut catalog = lock(&state.catalog).unwrap();
        for model in &mut catalog.models {
            model.installed = true;
            model.installed_path = Some(format!("/models/{}", model.filename));
        }
    }
    let mut options = input("upscale");
    options.model_id = "hat_s_x4".into();
    options.fix_stages = [
        ("blur", "nafnet_gopro_deblur"),
        ("jpeg", "fbcnn_color"),
        ("noise", "scunet_color_real_psnr"),
    ]
    .iter()
    .map(|(fix, id)| FixStageSelection {
        fix: (*fix).into(),
        model_id: (*id).into(),
        ..Default::default()
    })
    .collect();
    match resolve_model_selection(&state, &options).unwrap() {
        ModelSelection::Image { preprocess, .. } => {
            assert_eq!(
                preprocess
                    .iter()
                    .map(|stage| stage.model_id.as_str())
                    .collect::<Vec<_>>(),
                [
                    "nafnet_gopro_deblur",
                    "fbcnn_color",
                    "scunet_color_real_psnr"
                ]
            );
            assert_eq!(preprocess[1].kind, "deblock");
            assert!(preprocess
                .iter()
                .all(|stage| stage.path.starts_with("/models/")));
        }
        _ => panic!("expected image plan"),
    }
    let valid = options.fix_stages.clone();
    for id in ["unknown", "hat_s_x4", "hat_s_x4_face", "fbcnn_color"] {
        options.fix_stages[0].model_id = id.into();
        assert!(
            resolve_model_selection(&state, &options).is_err(),
            "accepted {id} as blur"
        );
    }
    options.fix_stages = valid.clone();
    options.fix_stages[1].fix = "blur".into();
    assert!(resolve_model_selection(&state, &options).is_err());
    options.fix_stages = valid.clone();
    lock(&state.catalog)
        .unwrap()
        .models
        .iter_mut()
        .find(|m| m.model_id == "fbcnn_color")
        .unwrap()
        .installed = false;
    assert!(resolve_model_selection(&state, &options).is_err());
    lock(&state.catalog)
        .unwrap()
        .models
        .iter_mut()
        .find(|m| m.model_id == "fbcnn_color")
        .unwrap()
        .installed = true;
    options.task = "denoise".into();
    options.model_id = "scunet_color_real_psnr".into();
    match resolve_model_selection(&state, &options).unwrap() {
        ModelSelection::Image { preprocess, .. } => assert_eq!(preprocess.len(), 2),
        _ => panic!("expected restore plan"),
    }
    options.model_id = "fbcnn_color".into();
    assert!(
        resolve_model_selection(&state, &options).is_err(),
        "Restore primary must be the final fix"
    );
    options.task = "video".into();
    options.model_id = "hat_s_x4".into();
    options.video_model_id = "frame_by_frame".into();
    match resolve_model_selection(&state, &options).unwrap() {
        ModelSelection::Image { preprocess, .. } => assert_eq!(preprocess.len(), 3),
        _ => panic!("expected frame-by-frame video plan"),
    }
    options.video_model_id = "seedvr2_3b".into();
    assert!(resolve_model_selection(&state, &options).is_err());
}

#[test]
fn multi_fix_recipe_validation_accepts_bounded_orders_for_both_image_tasks() {
    let base = serde_json::json!({"id":"r", "name":"r", "task":"upscale", "model_id":"main", "output_scale":4, "tile_size":128, "halo":16, "precision":"fp32", "safe_memory":true});
    let mut recipe: Recipe = serde_json::from_value(base).unwrap();
    for task in ["upscale", "denoise"] {
        recipe.task = task.into();
        recipe.stages = serde_json::from_value(serde_json::json!([
            {"kind":"restore","model_id":"blur","fix":"blur"},
            {"kind":"deblock","model_id":"jpeg","fix":"jpeg"},
            {"kind":"restore","model_id":"noise","fix":"noise"}
        ]))
        .unwrap();
        if task == "upscale" {
            recipe.stages.extend(
                serde_json::from_value::<Vec<RecipeStage>>(serde_json::json!([
                    {"kind":"upscale","model_id":"main"}, {"kind":"face_restore","model_id":"face"}
                ]))
                .unwrap(),
            );
        }
        assert!(validate_recipe_stages(&recipe).is_ok());
        let good = recipe.stages.clone();
        recipe.stages.insert(0, good[0].clone());
        assert!(validate_recipe_stages(&recipe).is_err());
        recipe.stages = good;
        recipe.stages.swap(0, 2);
        assert!(validate_recipe_stages(&recipe).is_ok());
    }
}

#[test]
fn a_restore_recipe_ends_with_its_restore_model_and_only_restorations_name_a_fix() {
    let base = serde_json::json!({"id":"r", "name":"r", "task":"denoise", "model_id":"main", "output_scale":1, "tile_size":128, "halo":16, "precision":"fp32", "safe_memory":true});
    let mut recipe: Recipe = serde_json::from_value(base).unwrap();
    let stages = |value: Value| serde_json::from_value::<Vec<RecipeStage>>(value).unwrap();
    // A 0.3.5 Restore recipe: one restore stage, no fix named.
    recipe.stages = stages(json!([{"kind":"restore","model_id":"main"}]));
    assert!(validate_recipe_stages(&recipe).is_ok());
    // The host sends Restore's model as a restore stage, never as a deblock.
    recipe.stages = stages(json!([{"kind":"deblock","model_id":"jpeg","fix":"jpeg"}]));
    assert!(validate_recipe_stages(&recipe).is_err());
    recipe.stages = stages(json!([
        {"kind":"restore","model_id":"a"}, {"kind":"restore","model_id":"b"},
        {"kind":"restore","model_id":"c"}, {"kind":"restore","model_id":"d"},
        {"kind":"restore","model_id":"e"}
    ]));
    assert!(validate_recipe_stages(&recipe).is_err());
    recipe.task = "upscale".into();
    for invalid in [
        json!([{"kind":"upscale","model_id":"main","fix":"noise"}]),
        json!([
            {"kind":"upscale","model_id":"main"},
            {"kind":"face_restore","model_id":"face","fix":"blur"}
        ]),
    ] {
        recipe.stages = stages(invalid);
        assert!(validate_recipe_stages(&recipe).is_err());
    }
}

#[test]
fn set_preference_round_trips_all_keys_rejects_bad_input_and_rolls_back_on_error() {
    let directory = tempfile::tempdir().unwrap();
    let paths = crate::paths::AppPaths::under(directory.path());
    fs::create_dir_all(&paths.next_root).unwrap();
    fs::create_dir_all(&paths.model_root).unwrap();
    let state = AppState::from_paths(paths).unwrap();
    lock(&state.runtime).unwrap().active_job_id = "running".into();

    for (name, value) in [
        ("check_updates_at_launch", serde_json::json!(false)),
        ("anonymous_update_count", serde_json::json!(false)),
        ("crash_reports", serde_json::json!(false)),
        ("low_priority", serde_json::json!(true)),
        ("keep_awake", serde_json::json!(false)),
        ("show_in_menu_bar", serde_json::json!(false)),
        ("notify_on_finish", serde_json::json!(false)),
        ("completion_sound", serde_json::json!(true)),
        ("menu_bar_icon_monochrome", serde_json::json!(false)),
        ("colour_blind_palette", serde_json::json!(true)),
        ("auto_cache_cleanup", serde_json::json!(false)),
        ("stopped_job_result", serde_json::json!("keep")),
        ("appearance", serde_json::json!("light")),
        ("contrast", serde_json::json!("high")),
        ("viewer_background", serde_json::json!("grey")),
        ("accent", serde_json::json!("localsr")),
        ("interface_scale", serde_json::json!(125)),
    ] {
        apply_preference(&state, name, &value).unwrap();
    }

    let saved = crate::settings::load(&state.paths).0;
    assert!(!saved.check_updates_at_launch);
    assert!(!saved.anonymous_update_count);
    assert!(!saved.crash_reports);
    assert!(saved.low_priority);
    assert!(!saved.keep_awake);
    assert!(!saved.show_in_menu_bar);
    assert!(!saved.notify_on_finish);
    assert!(saved.completion_sound);
    assert!(!saved.menu_bar_icon_monochrome);
    assert!(saved.colour_blind_palette);
    assert!(!saved.auto_cache_cleanup);
    assert_eq!(saved.stopped_job_result, "keep");
    assert_eq!(saved.appearance, "light");
    assert_eq!(saved.contrast, "high");
    assert_eq!(saved.viewer_background, "grey");
    assert_eq!(saved.accent, "localsr");
    assert_eq!(saved.interface_scale, 125);

    apply_preference(&state, "stopped_job_result", &serde_json::json!("discard")).unwrap();
    assert_eq!(
        crate::settings::load(&state.paths).0.stopped_job_result,
        "discard"
    );

    // Reject unknown names and bad values.
    assert!(apply_preference(&state, "device_id", &serde_json::json!("cpu")).is_err());
    assert!(apply_preference(&state, "low_priority", &serde_json::json!("yes")).is_err());
    assert!(apply_preference(&state, "keep_awake", &serde_json::json!("yes")).is_err());
    assert!(apply_preference(&state, "stopped_job_result", &serde_json::json!("always")).is_err());
    assert!(apply_preference(&state, "stopped_job_result", &serde_json::json!(true)).is_err());
    assert!(apply_preference(&state, "appearance", &serde_json::json!("sepia")).is_err());
    assert!(apply_preference(&state, "appearance", &serde_json::json!(123)).is_err());
    assert!(apply_preference(&state, "contrast", &serde_json::json!("loud")).is_err());
    assert!(apply_preference(&state, "contrast", &serde_json::json!(true)).is_err());
    assert!(apply_preference(&state, "viewer_background", &serde_json::json!("blue")).is_err());
    assert!(apply_preference(&state, "accent", &serde_json::json!("red")).is_err());
    assert!(apply_preference(&state, "accent", &serde_json::json!(true)).is_err());
    assert!(apply_preference(&state, "interface_scale", &serde_json::json!(150)).is_err());
    assert!(apply_preference(&state, "interface_scale", &serde_json::json!("125")).is_err());
    let still_saved = crate::settings::load(&state.paths).0;
    assert_eq!(still_saved.contrast, "high");
    assert_eq!(still_saved.viewer_background, "grey");
    assert_eq!(still_saved.accent, "localsr");

    // An unreadable settings file refuses the write; memory goes back to match the disk.
    fs::write(&state.paths.settings, b"{ not json").unwrap();
    assert!(apply_preference(&state, "anonymous_update_count", &serde_json::json!(true)).is_err());
    assert!(apply_preference(&state, "appearance", &serde_json::json!("dark")).is_err());
    assert!(apply_preference(&state, "interface_scale", &serde_json::json!(100)).is_err());
    let mem = lock(&state.settings).unwrap();
    assert!(!mem.anonymous_update_count);
    assert_eq!(mem.appearance, "light");
    assert_eq!(mem.interface_scale, 125);
}

#[test]
fn a_full_settings_save_never_changes_host_owned_preferences() {
    let current = UiSettings {
        anonymous_update_count: false,
        appearance: "light".into(),
        colour_blind_palette: true,
        contrast: "high".into(),
        viewer_background: "white".into(),
        accent: "localsr".into(),
        interface_scale: 125,
        completion_sound: true,
        menu_bar_icon_monochrome: false,
        auto_cache_cleanup: false,
        keep_awake: false,
        stopped_job_result: "discard".into(),
        preview_patch: "manual".into(),
        preview_patch_shown: true,
        ..UiSettings::default()
    };
    // A window copy from an older refresh still has defaults for host-owned preferences.
    let mut incoming = UiSettings {
        tile_size: UiSettings::default().tile_size + 64,
        ..UiSettings::default()
    };
    keep_update_switches(&current, &mut incoming);
    assert!(!incoming.anonymous_update_count);
    assert!(incoming.check_updates_at_launch);
    assert_eq!(incoming.appearance, "light");
    assert!(incoming.colour_blind_palette);
    assert_eq!(incoming.contrast, "high");
    assert_eq!(incoming.viewer_background, "white");
    assert_eq!(incoming.accent, "localsr");
    assert_eq!(incoming.interface_scale, 125);
    assert!(incoming.completion_sound);
    assert!(!incoming.menu_bar_icon_monochrome);
    assert!(!incoming.auto_cache_cleanup);
    assert!(!incoming.keep_awake);
    assert_eq!(incoming.stopped_job_result, "discard");
    assert_eq!(incoming.preview_patch, "manual");
    assert!(incoming.preview_patch_shown);
    assert_eq!(incoming.tile_size, UiSettings::default().tile_size + 64);

    // Settings saved before 0.3.3 check at launch, and before 0.6.0 auto-clean cache / stopped_job_result.
    let mut saved = serde_json::to_value(UiSettings::default()).unwrap();
    let map = saved.as_object_mut().unwrap();
    map.remove("check_updates_at_launch");
    map.remove("crash_reports");
    map.remove("auto_cache_cleanup");
    map.remove("keep_awake");
    map.remove("stopped_job_result");
    let loaded = serde_json::from_value::<UiSettings>(saved).unwrap();
    assert!(loaded.check_updates_at_launch);
    assert!(!loaded.crash_reports);
    assert!(loaded.auto_cache_cleanup);
    assert!(loaded.keep_awake);
    assert_eq!(loaded.stopped_job_result, "ask");
}

#[test]
fn crash_reports_switch_saves_while_a_job_runs_and_full_save_keeps_it() {
    let directory = tempfile::tempdir().unwrap();
    let paths = crate::paths::AppPaths::under(directory.path());
    fs::create_dir_all(&paths.next_root).unwrap();
    fs::create_dir_all(&paths.model_root).unwrap();
    let state = AppState::from_paths(paths).unwrap();
    // New installs start with crash reports on; turning them off must stick.
    assert!(crate::settings::load(&state.paths).0.crash_reports);
    lock(&state.runtime).unwrap().active_job_id = "running".into();
    apply_preference(&state, "crash_reports", &serde_json::json!(false)).unwrap();
    let saved = crate::settings::load(&state.paths).0;
    assert!(!saved.crash_reports);
    let mut stale = UiSettings::default();
    keep_update_switches(&saved, &mut stale);
    assert!(!stale.crash_reports);
}

#[test]
fn setup_choices_save_while_a_job_runs_and_roll_back_on_error() {
    let directory = tempfile::tempdir().unwrap();
    let paths = crate::paths::AppPaths::under(directory.path());
    fs::create_dir_all(&paths.next_root).unwrap();
    fs::create_dir_all(&paths.model_root).unwrap();
    let state = AppState::from_paths(paths).unwrap();
    assert!(!crate::settings::load(&state.paths).0.privacy_choice_made);
    lock(&state.runtime).unwrap().active_job_id = "running".into();

    assert!(apply_privacy_choice(
        &state,
        SetupChoices {
            appearance: "sepia".into(),
            contrast: "system".into(),
            show_in_menu_bar: None,
            completion_sound: false,
            colour_blind_palette: false,
            check_at_launch: true,
            anonymous_count: false,
            crash_reports: false,
        },
    )
    .is_err());
    assert!(!lock(&state.settings).unwrap().privacy_choice_made);

    apply_privacy_choice(
        &state,
        SetupChoices {
            appearance: "light".into(),
            contrast: "system".into(),
            show_in_menu_bar: None,
            completion_sound: false,
            colour_blind_palette: true,
            check_at_launch: false,
            anonymous_count: true,
            crash_reports: true,
        },
    )
    .unwrap();
    let saved = crate::settings::load(&state.paths).0;
    assert!(saved.privacy_choice_made);
    assert_eq!(saved.appearance, "light");
    assert!(saved.colour_blind_palette);
    assert!(!saved.check_updates_at_launch);
    assert!(saved.anonymous_update_count);
    assert!(saved.crash_reports);

    fs::write(&state.paths.settings, b"{ not json").unwrap();
    assert!(apply_privacy_choice(
        &state,
        SetupChoices {
            appearance: "dark".into(),
            contrast: "system".into(),
            show_in_menu_bar: None,
            completion_sound: false,
            colour_blind_palette: false,
            check_at_launch: true,
            anonymous_count: false,
            crash_reports: false,
        },
    )
    .is_err());
    let mem = lock(&state.settings).unwrap();
    assert_eq!(mem.appearance, "light");
    assert!(mem.colour_blind_palette);
    assert!(!mem.check_updates_at_launch);
    assert!(mem.anonymous_update_count);
    assert!(mem.crash_reports);
}

#[test]
fn setup_choices_validate_and_preserve_notification_and_tray_preferences() {
    let dir = tempfile::tempdir().unwrap();
    let paths = crate::paths::AppPaths::under(dir.path());
    fs::create_dir_all(&paths.next_root).unwrap();
    fs::create_dir_all(&paths.model_root).unwrap();
    let state = AppState::from_paths(paths).unwrap();
    let choices = |contrast: &str, tray: Option<bool>, sound: bool| SetupChoices {
        appearance: "light".into(),
        colour_blind_palette: true,
        contrast: contrast.into(),
        check_at_launch: false,
        anonymous_count: false,
        crash_reports: false,
        show_in_menu_bar: tray,
        completion_sound: sound,
    };
    assert!(apply_privacy_choice(&state, choices("loud", Some(false), true)).is_err());
    assert!(!lock(&state.settings).unwrap().privacy_choice_made);
    assert!(!state.paths.settings.exists());
    lock(&state.settings).unwrap().notify_on_finish = false;
    apply_privacy_choice(&state, choices("high", Some(false), true)).unwrap();
    let saved = crate::settings::load(&state.paths).0;
    assert_eq!(saved.appearance, "light");
    assert!(saved.colour_blind_palette);
    assert_eq!(saved.contrast, "high");
    assert!(
        !saved.check_updates_at_launch && !saved.anonymous_update_count && !saved.crash_reports
    );
    assert!(
        !saved.show_in_menu_bar
            && saved.completion_sound
            && saved.notify_on_finish
            && saved.privacy_choice_made
    );
    lock(&state.settings).unwrap().notify_on_finish = false;
    apply_privacy_choice(&state, choices("standard", None, false)).unwrap();
    let saved = crate::settings::load(&state.paths).0;
    assert!(!saved.show_in_menu_bar && !saved.completion_sound && !saved.notify_on_finish);
    let before = lock(&state.settings).unwrap().clone();
    fs::write(&state.paths.settings, b"{ not json").unwrap();
    assert!(apply_privacy_choice(&state, choices("system", Some(true), true)).is_err());
    let after = lock(&state.settings).unwrap().clone();
    assert_eq!(
        serde_json::to_value(before).unwrap(),
        serde_json::to_value(after).unwrap()
    );
}

#[test]
fn face_companions_require_the_matching_installed_model_and_explicit_selection() {
    let directory = tempfile::tempdir().unwrap();
    let paths = crate::paths::AppPaths::under(directory.path());
    fs::create_dir_all(&paths.next_root).unwrap();
    fs::create_dir_all(&paths.model_root).unwrap();
    let state = AppState::from_paths(paths).unwrap();
    for (primary_id, face_id) in [
        ("hat_s_x4", "hat_s_x4_face"),
        ("hat_l_x4_imagenet", "hat_l_x4_face"),
    ] {
        {
            let mut catalog = lock(&state.catalog).unwrap();
            for model in &mut catalog.models {
                model.installed = model.model_id == primary_id || model.model_id == face_id;
                model.installed_path = model
                    .installed
                    .then(|| format!("/models/{}", model.filename));
            }
        }
        let mut options = input("upscale");
        options.model_id = primary_id.into();
        options.enable_face_model = false;
        assert!(matches!(
            resolve_model_selection(&state, &options).unwrap(),
            ModelSelection::Image {
                face_path: None,
                ..
            }
        ));
        options.enable_face_model = true;
        match resolve_model_selection(&state, &options).unwrap() {
            ModelSelection::Image {
                face_path,
                face_model_id,
                ..
            } => {
                assert_eq!(face_model_id, face_id);
                assert!(face_path.unwrap().starts_with("/models/"));
            }
            _ => panic!("expected an image model"),
        }
        {
            let mut catalog = lock(&state.catalog).unwrap();
            catalog
                .models
                .iter_mut()
                .find(|model| model.model_id == face_id)
                .unwrap()
                .installed = false;
        }
        let missing = resolve_model_selection(&state, &options).err().unwrap();
        assert!(missing
            .to_string()
            .contains("import the verified face checkpoint"));
        options.model_id = face_id.into();
        assert!(resolve_model_selection(&state, &options).is_err());
    }
}

#[test]
fn revisiting_video_comparisons_keeps_only_explicit_files_authorized() {
    let app = tauri::test::mock_app();
    let scope = tauri::scope::fs::Scope::new(
        &app,
        &tauri::utils::config::FsScope::AllowedPaths(Vec::new()),
    )
    .unwrap();
    let directory = tempfile::tempdir().unwrap();
    let files: Vec<_> = [
        "first.mov",
        "first-output.mp4",
        "second-output.mp4",
        "private.mp4",
    ]
    .iter()
    .map(|name| {
        let path = directory.path().join(name);
        fs::write(&path, b"test media").unwrap();
        fs::canonicalize(path).unwrap()
    })
    .collect();
    // Open A, start a job using A's output, inspect B, then return to A.
    for (original, enhanced) in [(0, 1), (1, 2), (0, 1), (1, 2)] {
        allow_video_preview_pair(&scope, &files[original], Some(&files[enhanced])).unwrap();
        assert!(scope.is_allowed(&files[original]));
        assert!(scope.is_allowed(&files[enhanced]));
        assert!(!scope.is_allowed(&files[3]));
        assert!(!scope.is_allowed(directory.path()));
    }
    // An unfinished video authorizes only its source.
    allow_video_preview_pair(&scope, &files[3], None).unwrap();
    assert!(scope.is_allowed(&files[3]));
    assert!(!scope.is_allowed(directory.path()));
    assert!(scope.forbidden_patterns().is_empty());
}

fn input(task: &str) -> StartBatchInput {
    StartBatchInput {
        name_by_model: false,
        ui: None,
        media_ids: vec!["media".into()],
        batch_mode: false,
        task: task.into(),
        video_task: "upscale".into(),
        model_id: "model".into(),
        video_model_id: "frame_by_frame".into(),
        fix_stages: Vec::new(),
        custom_model_path: String::new(),
        output_directory: String::new(),
        output_format: "png".into(),
        output_bit_depth: 8,
        output_scale: 4,
        preserve_metadata: true,
        jpeg_quality: 98,
        device: "cpu".into(),
        tile_size: 256,
        halo: 32,
        precision: "fp32".into(),
        safe_memory: false,
        deflicker: false,
        deflicker_window: 3,
        video_container: "mp4".into(),
        video_codec: "av1".into(),
        external_ffmpeg_path: String::new(),
        video_hdr_mode: "tone_map".into(),
        video_target_resolution: 0,
        video_low_memory: true,
        video_crf: 18,
        neural_engine: true,
        enable_face_model: false,
        face_fidelity: 70,
        enable_live_preview: true,
        allow_unsafe_pickle_model: false,
        video_interpolation: "off".into(),
        video_interpolation_order: "after_upscale".into(),
        video_slow_motion: false,
        video_clean_audio: false,
        video_audio_model: String::new(),
        step_stages: Vec::new(),
    }
}

/// An app state with one ready image and `count` installed catalog models
/// matching `pick`, all pointing at a real file.
fn trial_state(
    directory: &Path,
    count: usize,
    pick: impl Fn(&CatalogModel) -> bool,
) -> (AppState, Vec<String>) {
    let state = AppState::from_paths(crate::paths::AppPaths::under(directory)).unwrap();
    let image = directory.join("photo.png");
    fs::write(&image, "source").unwrap();
    state.grants.grant_media_file(&image);
    {
        let db = lock(&state.database).unwrap();
        db.insert_media("media", image.to_str().unwrap(), "photo.png", "image")
            .unwrap();
        db.update_media_probe(
            image.to_str().unwrap(),
            "image",
            100,
            80,
            1,
            0.0,
            0.0,
            "preview",
            "",
            "",
            true,
            "",
        )
        .unwrap();
    }
    let ids: Vec<String> = {
        let mut catalog = lock(&state.catalog).unwrap();
        catalog
            .models
            .iter_mut()
            .filter(|m| pick(m))
            .take(count)
            .map(|m| {
                m.installed = true;
                m.installed_path = Some(image.to_string_lossy().into_owned());
                m.model_id.clone()
            })
            .collect()
    };
    assert_eq!(ids.len(), count, "the bundled catalog has enough models");
    (state, ids)
}

fn upscaler(model: &CatalogModel) -> bool {
    model.native_scale == 4 && !model.purposes.iter().any(|p| p == "face")
}

fn no_jobs(state: &AppState) -> bool {
    lock(&state.database)
        .unwrap()
        .list_jobs()
        .unwrap()
        .is_empty()
}

#[test]
fn model_trials_validate_every_candidate_before_admitting_any() {
    let directory = tempfile::tempdir().unwrap();
    let (state, ids) = trial_state(directory.path(), 2, upscaler);
    state.grants.grant_output_folder(directory.path());
    let mut base = input("upscale");
    base.output_directory = directory.path().to_string_lossy().into_owned();
    for invalid in [
        vec![ids[0].clone()],
        vec![ids[0].clone(), ids[0].clone()],
        vec![ids[0].clone(), "missing".into()],
        vec![ids[0].clone(), "__custom__".into()],
        vec![
            ids[0].clone(),
            ids[1].clone(),
            "a".into(),
            "b".into(),
            "c".into(),
        ],
    ] {
        assert!(admit_model_trials(&state, "media", &invalid, &base).is_err());
        assert!(no_jobs(&state));
    }
    let mut batch = base.clone();
    batch.batch_mode = true;
    let mut video = base.clone();
    video.task = "video".into();
    let mut other_media = base.clone();
    other_media.media_ids = vec!["elsewhere".into()];
    // Faces, a fix stage and a scale no candidate can keep.
    let mut faces = base.clone();
    faces.enable_face_model = true;
    let mut fix = base.clone();
    fix.fix_stages = vec![crate::types::FixStageSelection {
        fix: "blur".into(),
        model_id: "missing-fix".into(),
        ..Default::default()
    }];
    let mut scale = base.clone();
    scale.output_scale = 8;
    for refused in [batch, video, other_media, faces, fix, scale] {
        assert!(admit_model_trials(&state, "media", &ids, &refused).is_err());
        assert!(no_jobs(&state));
    }
    // The second checkpoint vanished: the first is not queued alone.
    lock(&state.catalog)
        .unwrap()
        .models
        .iter_mut()
        .find(|m| m.model_id == ids[1])
        .unwrap()
        .installed_path = Some("/missing-checkpoint.pth".into());
    assert!(admit_model_trials(&state, "media", &ids, &base).is_err());
    assert!(no_jobs(&state));
}

#[test]
fn model_trials_queue_ordinary_jobs_in_order_named_by_model() {
    let directory = tempfile::tempdir().unwrap();
    let (state, ids) = trial_state(directory.path(), 3, upscaler);
    state.grants.grant_output_folder(directory.path());
    let mut base = input("upscale");
    base.output_directory = directory.path().to_string_lossy().into_owned();
    base.model_id = ids[0].clone();
    base.halo = 0;
    base.ui = Some(ResultUi {
        quality: "best".into(),
        content: "photo".into(),
        recipe_name: "Mine".into(),
        fixes: vec![],
    });
    let jobs = admit_model_trials(&state, "media", &ids, &base).unwrap();
    assert_eq!(jobs.len(), 3);
    let db = lock(&state.database).unwrap();
    assert_eq!(db.next_queued_job().unwrap().unwrap().id, jobs[0]);
    // Snapshot order is newest first; the queue runs the reverse.
    let queued: Vec<String> = db
        .list_jobs()
        .unwrap()
        .into_iter()
        .rev()
        .map(|j| j.id)
        .collect();
    assert_eq!(queued, jobs);
    let requests = db.unfinished_requests().unwrap();
    let catalog = lock(&state.catalog).unwrap();
    for request in &requests {
        let data = &request["data"];
        assert!(data.get("ui").is_none() && data.get("name_by_model").is_none());
        let model_id = data["stages"][0]["model_id"].as_str().unwrap();
        let output = data["output_path"].as_str().unwrap();
        assert!(
            output.contains(&format!("_upscaled_4x_{}", model_slug(model_id))),
            "{output}"
        );
        let model = catalog
            .models
            .iter()
            .find(|m| m.model_id == model_id)
            .unwrap();
        assert!(data["halo"].as_u64().unwrap() >= u64::from(model.recommended_halo));
    }
    drop(catalog);
    drop(db);
    // The image now has work: a second submission is refused whole.
    assert!(admit_model_trials(&state, "media", &ids[..2], &base).is_err());
    assert_eq!(lock(&state.database).unwrap().list_jobs().unwrap().len(), 3);
    // The current model keeps its labels; another model reads Custom.
    let db = lock(&state.database).unwrap();
    for id in &jobs {
        db.finish_job(id, "failed", "", "stopped").unwrap();
    }
    let history = db.list_media_results("media", 50, None).unwrap();
    let ui_of = |job: &str| {
        history
            .results
            .iter()
            .find(|r| r.job_id == job)
            .and_then(|r| r.ui.clone())
            .unwrap()
    };
    assert_eq!(
        (ui_of(&jobs[0]).quality, ui_of(&jobs[0]).recipe_name),
        ("best".to_owned(), "Mine".to_owned())
    );
    assert_eq!(
        (ui_of(&jobs[1]).quality, ui_of(&jobs[1]).recipe_name),
        ("custom".to_owned(), String::new())
    );
    assert_eq!(ui_of(&jobs[1]).content, "photo");
}

#[test]
fn restore_trials_need_models_that_fix_the_selected_problem() {
    let directory = tempfile::tempdir().unwrap();
    let restorer =
        |m: &CatalogModel| m.native_scale == 1 && !m.purposes.iter().any(|p| p == "face");
    let (state, ids) = trial_state(directory.path(), 2, restorer);
    state.grants.grant_output_folder(directory.path());
    let catalog = lock(&state.catalog).unwrap().clone();
    let fixes = |id: &str| model_fixes(catalog.models.iter().find(|m| m.model_id == id).unwrap());
    let mut base = input("denoise");
    base.output_scale = 1;
    base.output_directory = directory.path().to_string_lossy().into_owned();
    // A problem one of the models does not fix refuses the whole trial.
    let missing = ["noise", "blur", "jpeg"]
        .into_iter()
        .find(|fix| !fixes(&ids[0]).iter().any(|f| f == fix))
        .expect("a restorer that does not fix everything");
    base.fix_stages = vec![crate::types::FixStageSelection {
        fix: missing.into(),
        model_id: ids[1].clone(),
        ..Default::default()
    }];
    assert!(admit_model_trials(&state, "media", &ids, &base).is_err());
    assert!(no_jobs(&state));
    // Without an active problem both run, named like Restore outputs.
    base.fix_stages.clear();
    base.ui = Some(ResultUi::default());
    admit_model_trials(&state, "media", &ids, &base).unwrap();
    let requests = lock(&state.database)
        .unwrap()
        .unfinished_requests()
        .unwrap();
    assert!(requests.iter().all(|r| r["data"]["output_path"]
        .as_str()
        .unwrap()
        .contains("_denoised_")));
}

#[test]
fn restore_trials_replace_the_last_fix_model_with_each_candidate() {
    let directory = tempfile::tempdir().unwrap();
    let restorer =
        |m: &CatalogModel| m.native_scale == 1 && !m.purposes.iter().any(|p| p == "face");
    let (state, ids) = trial_state(directory.path(), 2, restorer);
    state.grants.grant_output_folder(directory.path());
    let catalog = lock(&state.catalog).unwrap().clone();
    let fixes = |id: &str| model_fixes(catalog.models.iter().find(|m| m.model_id == id).unwrap());
    let Some(common) = ["noise", "blur", "jpeg"]
        .into_iter()
        .find(|fix| ids.iter().all(|id| fixes(id).iter().any(|f| f == fix)))
    else {
        return;
    };
    let mut base = input("denoise");
    base.output_scale = 1;
    base.model_id = ids[0].clone();
    base.output_directory = directory.path().to_string_lossy().into_owned();
    base.fix_stages = vec![crate::types::FixStageSelection {
        fix: common.into(),
        model_id: ids[0].clone(),
        ..Default::default()
    }];
    admit_model_trials(&state, "media", &ids, &base).unwrap();
    let requests = lock(&state.database)
        .unwrap()
        .unfinished_requests()
        .unwrap();
    assert_eq!(requests.len(), 2);
    for id in &ids {
        assert!(requests.iter().any(|r| {
            let stages = r["data"]["stages"].as_array().unwrap();
            stages.len() == 1 && stages[0]["model_id"] == id.as_str()
        }));
    }
}

#[test]
fn a_run_names_only_a_device_the_computer_reports() {
    let directory = tempfile::tempdir().unwrap();
    let state = AppState::from_paths(AppPaths::under(directory.path())).unwrap();
    lock(&state.capabilities).unwrap().system_ram_total = 8 << 30;
    assert_eq!(resolve_device(&state, "cpu").unwrap(), "cpu");
    let error = resolve_device(&state, "cuda:3").unwrap_err();
    assert!(error.to_string().contains("not available"));
}

#[test]
fn a_saved_device_that_is_gone_runs_on_the_cpu_and_stays_saved() {
    let directory = tempfile::tempdir().unwrap();
    let state = AppState::from_paths(AppPaths::under(directory.path())).unwrap();
    lock(&state.capabilities).unwrap().system_ram_total = 8 << 30;
    lock(&state.settings).unwrap().device_id = "cuda:1".into();
    assert_eq!(resolve_device(&state, "cuda:1").unwrap(), "cpu");
    assert_eq!(lock(&state.settings).unwrap().device_id, "cuda:1");
    assert!(resolve_device(&state, "cuda:2").is_err());
}

#[test]
fn no_device_is_judged_before_the_hardware_has_been_reported() {
    let directory = tempfile::tempdir().unwrap();
    let state = AppState::from_paths(AppPaths::under(directory.path())).unwrap();
    lock(&state.settings).unwrap().device_id = "mps".into();
    let error = resolve_device(&state, "mps").unwrap_err();
    assert!(error.to_string().contains("still detecting"));
}

#[test]
fn try_others_runs_a_gone_saved_device_on_the_cpu() {
    let directory = tempfile::tempdir().unwrap();
    let (state, ids) = trial_state(directory.path(), 2, upscaler);
    lock(&state.capabilities).unwrap().system_ram_total = 8 << 30;
    lock(&state.settings).unwrap().device_id = "cuda:1".into();
    let mut base = input("upscale");
    base.device = "cuda:1".into();
    let candidates = trial_candidates(&state, "media", &ids, &base).unwrap();
    assert!(candidates
        .iter()
        .all(|candidate| candidate.input.device == "cpu"));
}

#[test]
fn try_others_needs_two_to_four_models_and_an_engine_that_runs_the_fixes() {
    let directory = tempfile::tempdir().unwrap();
    let (state, ids) = trial_state(directory.path(), 2, upscaler);
    let base = input("upscale");
    let error = trial_candidates(&state, "media", &ids[..1], &base)
        .err()
        .unwrap();
    assert!(error.to_string().contains("2 to 4"));
    // Two fixes need an engine that runs several; this one is not known to.
    let mut several = base.clone();
    several.fix_stages = ["blur", "jpeg"]
        .iter()
        .map(|fix| crate::types::FixStageSelection {
            fix: (*fix).into(),
            model_id: ids[0].clone(),
            ..Default::default()
        })
        .collect();
    let error = trial_candidates(&state, "media", &ids, &several)
        .err()
        .unwrap();
    assert!(error.to_string().contains("one fix at a time"));
}

#[test]
fn trial_estimates_check_candidates_without_queueing_or_creating_folders() {
    let directory = tempfile::tempdir().unwrap();
    let (state, ids) = trial_state(directory.path(), 2, upscaler);
    let mut base = input("upscale");
    let output = directory.path().join("not-yet");
    state.grants.grant_output_folder(&output);
    base.output_directory = output.to_string_lossy().into_owned();
    let candidates = trial_candidates(&state, "media", &ids, &base).unwrap();
    assert_eq!(candidates.len(), 2);
    assert!(!output.exists());
    assert!(no_jobs(&state));
}

#[test]
fn a_pair_decodes_each_result_once_and_never_the_original() {
    let directory = tempfile::tempdir().unwrap();
    let db = crate::database::Database::open(&directory.path().join("q.db")).unwrap();
    db.insert_media("photo", "/tmp/photo.png", "photo.png", "image")
        .unwrap();
    db.update_media_probe(
        "/tmp/photo.png",
        "image",
        640,
        480,
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
    let media = db.get_media("photo").unwrap().unwrap();
    let mut runtime = crate::types::RuntimeStatus::default();
    let sent = |requests: Vec<Value>| -> Vec<(String, String, u64)> {
        requests
            .iter()
            .map(|r| {
                let data = &r["data"];
                assert_eq!(data["max_dimension"], worker::RESULT_PREVIEW_MAX_DIMENSION);
                (
                    data["slot"].as_str().unwrap().to_owned(),
                    data["target_id"].as_str().unwrap().to_owned(),
                    data["generation"].as_u64().unwrap(),
                )
            })
            .collect()
    };
    // Result against Original: one decode, the Original keeps its size.
    let first = plan_pair_requests(&mut runtime, &media, ("a", "/a.png"), ("original", ""));
    assert_eq!(sent(first), vec![("showing".into(), "a".into(), 1)]);
    assert_eq!(
        (
            runtime.comparison_pair.against.width,
            runtime.comparison_pair.against.height
        ),
        (640, 480)
    );
    // Still loading: asking again sends it again under a new generation.
    let again = plan_pair_requests(&mut runtime, &media, ("a", "/a.png"), ("original", ""));
    assert_eq!(sent(again), vec![("showing".into(), "a".into(), 2)]);
    runtime.comparison_pair.showing.preview_data_url = "data:a".into();
    // Decoded: the same pair needs nothing.
    assert!(plan_pair_requests(&mut runtime, &media, ("a", "/a.png"), ("original", "")).is_empty());
    // A second result: only it is decoded; the first moves sides intact.
    let pair = plan_pair_requests(&mut runtime, &media, ("b", "/b.png"), ("a", "/a.png"));
    assert_eq!(sent(pair), vec![("showing".into(), "b".into(), 3)]);
    assert_eq!(runtime.comparison_pair.against.preview_data_url, "data:a");
    runtime.comparison_pair.showing.preview_data_url = "data:b".into();
    // Swapping both decoded sides decodes nothing.
    assert!(plan_pair_requests(&mut runtime, &media, ("a", "/a.png"), ("b", "/b.png")).is_empty());
    assert_eq!(runtime.comparison_pair.showing.preview_data_url, "data:a");
    assert_eq!(runtime.comparison_pair.against.preview_data_url, "data:b");
    // Another image never reuses these previews.
    db.insert_media("other", "/tmp/other.png", "other.png", "image")
        .unwrap();
    let other = db.get_media("other").unwrap().unwrap();
    let fresh = plan_pair_requests(&mut runtime, &other, ("a", "/a.png"), ("original", ""));
    assert_eq!(sent(fresh).len(), 1);
    assert!(runtime.comparison_pair.showing.preview_data_url.is_empty());
}

#[test]
fn trial_names_sanitize_models_and_keep_collision_counters() {
    let directory = tempfile::tempdir().unwrap();
    let mut base = input("upscale");
    base.name_by_model = true;
    base.model_id = "../SwinIR_M///".into();
    let mut reserved = ReservedOutputs::default();
    let first = unique_output_path(directory.path(), "/photo.png", &base, &mut reserved);
    assert_eq!(first.file_name().unwrap(), "photo_upscaled_4x_swinir-m.png");
    let second = unique_output_path(directory.path(), "/photo.png", &base, &mut reserved);
    assert_eq!(
        second.file_name().unwrap(),
        "photo_upscaled_4x_swinir-m_1.png"
    );
    assert_eq!(model_slug("///"), "model");
    assert!(model_slug(&"a".repeat(100)).len() <= 24);
}

#[test]
fn pending_output_is_reserved_across_submissions() {
    let directory = tempfile::tempdir().unwrap();
    let db = crate::database::Database::open(&directory.path().join("queue.db")).unwrap();
    db.insert_media("m", "/first/photo.png", "photo.png", "image")
        .unwrap();
    let options = input("upscale");
    let first = unique_output_path(
        directory.path(),
        "/first/photo.png",
        &options,
        &mut ReservedOutputs::default(),
    );
    db.insert_job(
        "j",
        "m",
        &serde_json::json!({"data":{"output_path":first}}).to_string(),
    )
    .unwrap();
    let mut reserved = db.unfinished_output_paths().unwrap().into_iter().collect();
    let second = unique_output_path(
        directory.path(),
        "/second/photo.png",
        &options,
        &mut reserved,
    );
    assert_ne!(first, second, "pending output must be reserved");
}

#[test]
fn output_names_do_not_overwrite_existing_or_batched_files() {
    let directory = tempfile::tempdir().unwrap();
    let options = input("upscale");
    fs::write(directory.path().join("photo_upscaled_4x.png"), b"old").unwrap();
    let mut reserved = ReservedOutputs::default();
    let first = unique_output_path(
        directory.path(),
        "/media/photo.png",
        &options,
        &mut reserved,
    );
    let second = unique_output_path(
        directory.path(),
        "/other/photo.jpg",
        &options,
        &mut reserved,
    );
    assert_eq!(first.file_name().unwrap(), "photo_upscaled_4x_1.png");
    assert_eq!(second.file_name().unwrap(), "photo_upscaled_4x_2.png");
    let mut video = input("video");
    video.video_model_id = "seedvr2_3b".into();
    video.video_target_resolution = 256;
    let smaller = unique_output_path(directory.path(), "/media/clip.mov", &video, &mut reserved);
    assert_eq!(smaller.file_name().unwrap(), "clip_video_256px.mp4");
    video.video_model_id = "frame_by_frame".into();
    let tiled = unique_output_path(directory.path(), "/media/clip.mov", &video, &mut reserved);
    assert_eq!(tiled.file_name().unwrap(), "clip_video_4x.mp4");
}

#[test]
fn unrecorded_existing_and_missing_outputs_have_the_same_error() {
    let directory = tempfile::tempdir().unwrap();
    let state =
        AppState::from_paths(crate::paths::AppPaths::under(&directory.path().join("app"))).unwrap();
    let recorded = directory.path().join("recorded.png");
    let existing = directory.path().join("existing.png");
    fs::write(&recorded, b"result").unwrap();
    fs::write(&existing, b"private").unwrap();
    lock(&state.runtime).unwrap().last_output_path = recorded.to_string_lossy().into_owned();
    let missing = directory.path().join("missing.png");
    let error = authorized_result(&state, &existing.to_string_lossy())
        .unwrap_err()
        .to_string();
    assert!(error.contains("output path was not authorized"));
    assert_eq!(
        error,
        authorized_result(&state, &missing.to_string_lossy())
            .unwrap_err()
            .to_string()
    );
}

#[cfg(unix)]
#[test]
fn a_recorded_output_that_became_a_link_is_not_authorized() {
    let directory = tempfile::tempdir().unwrap();
    let directory_root = std::fs::canonicalize(directory.path()).unwrap();
    let state = AppState::from_paths(crate::paths::AppPaths::under(
        &directory_root.as_path().join("app"),
    ))
    .unwrap();
    let output = directory_root.as_path().join("result.png");
    let private = directory_root.as_path().join("private.png");
    fs::write(&output, b"result").unwrap();
    fs::write(&private, b"private").unwrap();
    {
        let database = lock(&state.database).unwrap();
        database
            .insert_media("m", "/in/photo.png", "photo.png", "image")
            .unwrap();
        database.insert_job("j", "m", "{}").unwrap();
        database
            .finish_job("j", "completed", &output.to_string_lossy(), "")
            .unwrap();
    }
    let requested = output.to_string_lossy().into_owned();
    assert_eq!(
        authorized_result(&state, &requested).unwrap(),
        fs::canonicalize(&output).unwrap()
    );
    fs::remove_file(&output).unwrap();
    std::os::unix::fs::symlink(&private, &output).unwrap();
    assert!(authorized_result(&state, &requested).is_err());
}

#[cfg(unix)]
#[test]
fn playback_and_comparison_refuse_a_result_that_became_a_link() {
    let directory = tempfile::tempdir().unwrap();
    let directory_root = std::fs::canonicalize(directory.path()).unwrap();
    let state = AppState::from_paths(crate::paths::AppPaths::under(
        &directory_root.as_path().join("app"),
    ))
    .unwrap();
    let output = directory_root.as_path().join("result.png");
    let private = directory_root.as_path().join("private.png");
    fs::write(&output, b"result").unwrap();
    fs::write(&private, b"private").unwrap();
    {
        let database = lock(&state.database).unwrap();
        database
            .insert_media("m", "/in/photo.png", "photo.png", "image")
            .unwrap();
        database.insert_job("j", "m", "{}").unwrap();
        database
            .finish_job("j", "completed", &output.to_string_lossy(), "")
            .unwrap();
    }
    assert_eq!(
        canonical_result_video(output.clone()).unwrap(),
        fs::canonicalize(&output).unwrap()
    );
    fs::remove_file(&output).unwrap();
    std::os::unix::fs::symlink(&private, &output).unwrap();
    // A link is never followed to the file it names, whatever it names.
    let error = canonical_result_video(output.clone()).unwrap_err();
    assert!(error.to_string().contains("not authorized"), "{error}");
    let error = request_latest_comparison(&state, "m".into()).unwrap_err();
    assert!(error.to_string().contains("not authorized"), "{error}");
    assert!(lock(&state.runtime)
        .unwrap()
        .comparison_output_path
        .is_empty());
}

#[test]
fn open_launches_only_the_types_the_app_writes() {
    for name in [
        "result.png",
        "result.JPG",
        "result.tif",
        "result.webp",
        "result.heic",
        "clip.mp4",
        "clip.MKV",
        "clip.mov",
    ] {
        assert!(is_openable_output(Path::new(name)), "{name}");
    }
    for name in [
        "result.exe",
        "result.bat",
        "result.cmd",
        "result.lnk",
        "result.ps1",
        "result.msi",
        "result.scr",
        "result.com",
        "result.js",
        "result.app",
        "result.png.exe",
        "result.exe.",
        "result.exe ",
        "result",
        ".png",
    ] {
        assert!(!is_openable_output(Path::new(name)), "{name}");
    }
}

#[cfg(unix)]
#[test]
fn importing_a_folder_follows_in_folder_links_and_blocks_escapes_and_linked_dirs() {
    use std::os::unix::fs::symlink;
    let directory = tempfile::tempdir().unwrap();
    let state = AppState::from_paths(AppPaths::under(&directory.path().join("app"))).unwrap();
    let chosen = directory.path().join("chosen");
    let elsewhere = directory.path().join("elsewhere");
    fs::create_dir_all(chosen.join("real-folder")).unwrap();
    fs::create_dir_all(&elsewhere).unwrap();
    fs::write(chosen.join("own.png"), b"own").unwrap();
    fs::write(chosen.join("real-folder").join("inner.png"), b"inner").unwrap();
    fs::write(elsewhere.join("linked.jpg"), b"linked").unwrap();
    fs::write(elsewhere.join("nested.jpg"), b"nested").unwrap();
    fs::write(elsewhere.join("notes.txt"), b"text").unwrap();
    // A link inside the granted folder to a sibling inside the same folder stays within the grant.
    symlink(
        chosen.join("real-folder").join("inner.png"),
        chosen.join("in-folder.png"),
    )
    .unwrap();
    // A link pointing outside the granted folder must not escape the folder grant.
    symlink(elsewhere.join("linked.jpg"), chosen.join("escape.jpg")).unwrap();
    // A link to a folder is not followed, a link that loops cannot hang the scan,
    // and neither can a link named like media that points at something else.
    symlink(&elsewhere, chosen.join("folder-link")).unwrap();
    symlink(&chosen, chosen.join("loop")).unwrap();
    symlink(elsewhere.join("notes.txt"), chosen.join("disguised.png")).unwrap();
    symlink(
        directory.path().join("missing.png"),
        chosen.join("dangling.png"),
    )
    .unwrap();

    state.grants.grant_media_folder(&chosen);
    let found =
        normalize_media_paths_checked(vec![chosen.to_string_lossy().into_owned()], &state.grants)
            .unwrap();
    let names: Vec<String> = found
        .iter()
        .map(|(path, _)| path.file_name().unwrap().to_string_lossy().into_owned())
        .collect();
    assert_eq!(names, ["inner.png", "own.png"]);
    let listed = scan_media_folder_checked(&state, &chosen.to_string_lossy()).unwrap();
    assert_eq!(listed.len(), 2);
    assert!(listed.iter().all(|path| !path.contains("escape")
        && !path.contains("nested")
        && !path.contains("notes")));
}

#[test]
fn names_that_differ_only_by_case_are_one_name() {
    let directory = tempfile::tempdir().unwrap();
    let options = input("upscale");
    let mut reserved = ReservedOutputs::default();
    let first = unique_output_path(directory.path(), "/day1/A.jpg", &options, &mut reserved);
    let second = unique_output_path(directory.path(), "/day2/a.jpg", &options, &mut reserved);
    let third = unique_output_path(directory.path(), "/day3/Ä.jpg", &options, &mut reserved);
    let fourth = unique_output_path(directory.path(), "/day4/ä.jpg", &options, &mut reserved);
    let names: Vec<String> = [&first, &second, &third, &fourth]
        .iter()
        .map(|path| path.to_string_lossy().to_uppercase().to_lowercase())
        .collect();
    let distinct: HashSet<&String> = names.iter().collect();
    assert_eq!(distinct.len(), 4, "{names:?}");
}

#[test]
fn benchmark_export_is_unique_atomic_and_never_clobbers() {
    let directory = tempfile::tempdir().unwrap();
    let destination = directory.path().join("result.json");
    let unrelated = directory.path().join("result.json.tmp");
    fs::write(&unrelated, b"user-owned").unwrap();

    write_benchmark_export(&destination, br#"{"score":42}"#).unwrap();
    assert_eq!(fs::read(&destination).unwrap(), br#"{"score":42}"#);
    assert_eq!(fs::read(&unrelated).unwrap(), b"user-owned");
    assert!(directory.path().read_dir().unwrap().all(|entry| !entry
        .unwrap()
        .file_name()
        .to_string_lossy()
        .contains("localsr-benchmark")));

    let error = write_benchmark_export(&destination, b"replacement").unwrap_err();
    assert!(error.to_string().contains("already exists"));
    assert_eq!(fs::read(&destination).unwrap(), br#"{"score":42}"#);
}

#[test]
fn extension_matching_is_case_insensitive_and_bounded() {
    assert_eq!(media_kind(Path::new("A.DNG")), Some("image"));
    assert_eq!(media_kind(Path::new("pixel.BMP")), Some("image"));
    assert_eq!(media_kind(Path::new("DSCF0001.RAF")), Some("image"));
    assert_eq!(media_kind(Path::new("IMG_0001.CR3")), Some("image"));
    assert_eq!(media_kind(Path::new("photo.avif")), Some("image"));
    let heic = media_kind(Path::new("IMG_0001.HEIC"));
    assert_eq!(heic, cfg!(target_os = "macos").then_some("image"));
    assert_eq!(media_kind(Path::new("clip.MKV")), Some("video"));
    assert_eq!(media_kind(Path::new("notes.txt")), None);
}

#[test]
fn launch_directories_expand_supported_files_without_recursing() {
    let directory = tempfile::tempdir().unwrap();
    let state = AppState::from_paths(AppPaths::under(&directory.path().join("app"))).unwrap();
    let incoming = directory.path().join("incoming");
    fs::create_dir_all(&incoming).unwrap();
    fs::write(incoming.join("B.MP4"), b"video").unwrap();
    fs::write(incoming.join("a.png"), b"image").unwrap();
    fs::write(incoming.join("notes.txt"), b"ignored").unwrap();
    fs::create_dir(incoming.join("nested")).unwrap();
    fs::write(incoming.join("nested/hidden.png"), b"ignored").unwrap();

    state.grants.grant_media_folder(&incoming);
    let normalized =
        normalize_media_paths_checked(vec![incoming.to_string_lossy().into()], &state.grants)
            .expect("directory should expand");

    assert_eq!(normalized.len(), 2);
    assert_eq!(normalized[0].0.file_name().unwrap(), "a.png");
    assert_eq!(normalized[0].1, "image");
    assert_eq!(normalized[1].0.file_name().unwrap(), "B.MP4");
    assert_eq!(normalized[1].1, "video");
}

#[test]
fn launch_path_expansion_deduplicates_and_rejects_explicit_unknown_files() {
    let directory = tempfile::tempdir().unwrap();
    let state = AppState::from_paths(AppPaths::under(&directory.path().join("app"))).unwrap();
    let image = directory.path().join("photo.png");
    let text = directory.path().join("notes.txt");
    fs::write(&image, b"image").unwrap();
    fs::write(&text, b"text").unwrap();
    state.grants.grant_media_file(&image);
    state.grants.grant_media_file(&text);

    let duplicated = normalize_media_paths_checked(
        vec![
            image.to_string_lossy().into(),
            image.to_string_lossy().into(),
        ],
        &state.grants,
    )
    .unwrap();
    assert_eq!(duplicated.len(), 1);
    assert!(
        normalize_media_paths_checked(vec![text.to_string_lossy().into()], &state.grants).is_err()
    );
}

#[test]
fn image_jobs_request_a_bounded_nonblocking_progressive_preview() {
    let media = crate::types::MediaItem {
        id: "media".into(),
        path: "/input/photo.png".into(),
        name: "photo.png".into(),
        kind: "image".into(),
        width: 100,
        height: 80,
        frame_count: 1,
        fps: 0.0,
        duration_seconds: 0.0,
        preview_data_url: String::new(),
        probe_status: "ready".into(),
        hdr_format: String::new(),
        audio_warning: String::new(),
        has_audio: true,
        field_order: String::new(),
        repair_marks: 0,
        repair_rev: 0,
        error: String::new(),
        selected: true,
    };
    let selection = ModelSelection::Image {
        approved_checkpoint_sha256: None,
        model_id: "model".into(),
        path: "/models/upscaler.safetensors".into(),
        preprocess: Vec::new(),
        step_models: Vec::new(),
        face_path: None,
        face_model_id: String::new(),
        native_scale: 4,
        skip_upscale: false,
        interpolation: None,
        audio_denoise: None,
    };

    let message = build_job_message(
        "job",
        &media,
        Path::new("/output/photo.png"),
        Path::new("/app-owned/work"),
        &input("upscale"),
        &selection,
    )
    .unwrap();

    assert_eq!(message["data"]["preview_enabled"], true);
    assert_eq!(message["data"]["preview_max_fps"], 2.0);
    assert_eq!(message["data"]["preview_max_dimension"], 320);
    assert_eq!(message["data"]["face_fidelity"], 0.7);
    assert_eq!(message["data"]["scratch_directory"], "/app-owned/work");
    assert_eq!(message["data"]["stages"].as_array().unwrap().len(), 1);
    assert_eq!(message["data"]["stages"][0]["kind"], "upscale");
    assert_eq!(message["data"]["neural_engine"], true);

    // Probed average FPS is metadata, not a request to retime a VFR clip.
    let mut video = media.clone();
    video.kind = "video".into();
    video.fps = 29.97;
    let video_message = build_job_message(
        "video-job",
        &video,
        Path::new("/output/clip.mp4"),
        Path::new("/app-owned/work"),
        &input("video"),
        &selection,
    )
    .unwrap();
    assert_eq!(video_message["type"], "video_job_request");
    assert!(video_message["data"]["fps"].is_null());
    assert_eq!(video_message["data"]["neural_engine"], true);
    let mut without_neural_engine = input("video");
    without_neural_engine.neural_engine = false;
    let gpu_message = build_job_message(
        "gpu-job",
        &video,
        Path::new("/output/gpu.mp4"),
        Path::new("/app-owned/work"),
        &without_neural_engine,
        &selection,
    )
    .unwrap();
    assert_eq!(gpu_message["data"]["neural_engine"], false);
    assert_eq!(video_message["data"]["hdr_mode"], "tone_map");
    let mut hdr_input = input("video");
    hdr_input.video_hdr_mode = "preserve".into();
    let hdr_message = build_job_message(
        "hdr-job",
        &video,
        Path::new("/output/hdr.mp4"),
        Path::new("/app-owned/work"),
        &hdr_input,
        &selection,
    )
    .unwrap();
    assert_eq!(hdr_message["data"]["hdr_mode"], "preserve");

    let temporal = ModelSelection::Temporal {
        model_id: "seedvr2_3b".into(),
        engine_kind: "seedvr2".into(),
        bundle_dir: "/models/seedvr2".into(),
        temporal_window: 9,
        temporal_overlap: 2,
        audio_denoise: None,
    };
    let temporal_message = build_job_message(
        "temporal-job",
        &video,
        Path::new("/output/clip.mp4"),
        Path::new("/app-owned/work"),
        &input("video"),
        &temporal,
    )
    .unwrap();
    assert!(temporal_message["data"]["fps"].is_null());
    assert_eq!(temporal_message["data"]["video_low_memory"], true);
    assert_eq!(temporal_message["data"]["hdr_mode"], "tone_map");
    let mut smaller = input("video");
    smaller.video_target_resolution = 512;
    smaller.video_low_memory = false;
    let smaller_message = build_job_message(
        "small-job",
        &video,
        Path::new("/output/small.mp4"),
        Path::new("/app-owned/work"),
        &smaller,
        &temporal,
    )
    .unwrap();
    assert_eq!(smaller_message["data"]["target_resolution"], 512);
    assert_eq!(smaller_message["data"]["video_low_memory"], false);
    let mut old_settings = serde_json::to_value(UiSettings::default()).unwrap();
    old_settings
        .as_object_mut()
        .unwrap()
        .remove("neural_engine");
    assert!(
        serde_json::from_value::<UiSettings>(old_settings)
            .unwrap()
            .neural_engine
    );
    let mut old_memory_settings = serde_json::to_value(UiSettings::default()).unwrap();
    old_memory_settings
        .as_object_mut()
        .unwrap()
        .remove("video_low_memory");
    assert!(
        serde_json::from_value::<UiSettings>(old_memory_settings)
            .unwrap()
            .video_low_memory
    );
    let mut old_settings = serde_json::to_value(UiSettings::default()).unwrap();
    old_settings
        .as_object_mut()
        .unwrap()
        .remove("video_target_resolution");
    assert_eq!(
        serde_json::from_value::<UiSettings>(old_settings)
            .unwrap()
            .video_target_resolution,
        0
    );
}

#[test]
fn image_recipe_serializes_restore_upscale_and_fused_face_in_order() {
    let media = crate::types::MediaItem {
        id: "media".into(),
        path: "/input/photo.png".into(),
        name: "photo.png".into(),
        kind: "image".into(),
        width: 100,
        height: 80,
        frame_count: 1,
        fps: 0.0,
        duration_seconds: 0.0,
        preview_data_url: String::new(),
        probe_status: "ready".into(),
        hdr_format: String::new(),
        audio_warning: String::new(),
        has_audio: true,
        field_order: String::new(),
        repair_marks: 0,
        repair_rev: 0,
        error: String::new(),
        selected: true,
    };
    let selection = ModelSelection::Image {
        approved_checkpoint_sha256: None,
        model_id: "upscale".into(),
        path: "/models/upscale.pth".into(),
        preprocess: vec![ResolvedImageStage {
            kind: "deblock",
            fix: "jpeg".into(),
            model_id: "fbcnn_color".into(),
            path: "/models/fbcnn.pth".into(),
            params: None,
            mask_path: None,
        }],
        step_models: Vec::new(),
        face_path: Some("/models/face.pth".into()),
        face_model_id: "hat_l_x4_face".into(),
        native_scale: 4,
        skip_upscale: false,
        interpolation: None,
        audio_denoise: None,
    };
    let mut options = input("upscale");
    options.enable_face_model = true;
    options.face_fidelity = 65;
    let message = build_job_message(
        "job",
        &media,
        Path::new("/output/photo.png"),
        Path::new("/app-owned/work"),
        &options,
        &selection,
    )
    .unwrap();

    let stages = message["data"]["stages"].as_array().unwrap();
    assert_eq!(
        stages
            .iter()
            .map(|stage| stage["kind"].as_str().unwrap())
            .collect::<Vec<_>>(),
        vec!["deblock", "upscale", "face_restore"]
    );
    assert_eq!(stages[2]["model_id"], "hat_l_x4_face");
    assert_eq!(stages[2]["model_path"], "/models/face.pth");
    assert_eq!(stages[2]["execution"], "fused-with-upscale");
    assert_eq!(stages[2]["fidelity"], 0.65);
}

#[test]
fn install_hints_follow_the_distribution() {
    let fedora = "NAME=\"Fedora Linux\"\nID=fedora\nID_LIKE=\"rhel centos\"\n";
    assert_eq!(
        install_hint_for("linux", fedora).command,
        "sudo dnf install ffmpeg"
    );
    assert!(install_hint_for("linux", fedora)
        .note
        .contains("RPM Fusion"));
    let mint = "ID=linuxmint\nID_LIKE=\"ubuntu debian\"\n";
    assert_eq!(
        install_hint_for("linux", mint).command,
        "sudo apt install ffmpeg"
    );
    assert_eq!(install_hint_for("linux", mint).system, "Ubuntu");
    assert_eq!(
        install_hint_for("linux", "ID=arch\n").command,
        "sudo pacman -S ffmpeg"
    );
    assert_eq!(
        install_hint_for("linux", "").command,
        "sudo apt install ffmpeg"
    );
    assert_eq!(install_hint_for("macos", "").command, "brew install ffmpeg");
    assert_eq!(
        install_hint_for("windows", "").command,
        "winget install Gyan.FFmpeg"
    );
}

#[test]
fn video_codec_settings_follow_the_media_policy() {
    let mut video = input("video");
    assert!(validate_start_input(&video, false).is_ok());
    video.video_codec = "ffv1".into();
    assert!(
        validate_start_input(&video, false).is_err(),
        "FFV1 requires MKV"
    );
    video.video_container = "mkv".into();
    assert!(validate_start_input(&video, false).is_ok());
    video.video_codec = "h264".into();
    video.video_container = "mp4".into();
    assert!(
        validate_start_input(&video, false).is_err(),
        "H.264 export needs the system codecs or an FFmpeg"
    );
    assert!(
        validate_start_input(&video, true).is_ok(),
        "the system codecs or a found FFmpeg write H.264"
    );
    video.external_ffmpeg_path = "/opt/homebrew/bin/ffmpeg".into();
    assert!(validate_start_input(&video, false).is_ok());
    video.video_codec = "x264".into();
    assert!(validate_start_input(&video, false).is_err());
}

#[test]
fn heic_mov_and_prores_are_accepted_on_macos_only() {
    let mac = cfg!(target_os = "macos");
    let mut photo = input("upscale");
    photo.output_format = "heic".into();
    assert_eq!(validate_start_input(&photo, false).is_ok(), mac);
    photo.output_format = "webp".into();
    assert!(validate_start_input(&photo, false).is_ok());

    let mut video = input("video");
    video.video_container = "mov".into();
    video.video_codec = "hevc".into();
    assert_eq!(validate_start_input(&video, true).is_ok(), mac);
    video.video_codec = "prores".into();
    assert_eq!(validate_start_input(&video, true).is_ok(), mac);
    // MOV holds HEVC or ProRes; ProRes needs MOV, on every platform.
    for (container, codec) in [("mov", "av1"), ("mov", "h264"), ("mp4", "prores")] {
        video.video_container = container.into();
        video.video_codec = codec.into();
        assert!(
            validate_start_input(&video, true).is_err(),
            "{container} with {codec}"
        );
    }
    let settings = UiSettings {
        output_format: "heic".into(),
        ..UiSettings::default()
    };
    assert_eq!(validate_settings(&settings).is_ok(), mac);
}

#[test]
fn heic_and_mov_options_need_the_engines_announcement() {
    let announced = json!({
        "image_output": ["heic"],
        "system_codecs": {"encode": ["h264", "hevc", "prores", "aac"]},
    });
    let silent = json!({"image_output": [], "system_codecs": null});
    let mut photo = input("upscale");
    photo.output_format = "heic".into();
    assert!(validate_system_outputs(&photo, &announced).is_ok());
    assert!(validate_system_outputs(&photo, &silent).is_err());
    assert!(validate_system_outputs(&photo, &Value::Null).is_err());
    photo.output_format = "png".into();
    assert!(validate_system_outputs(&photo, &silent).is_ok());

    let mut video = input("video");
    video.video_container = "mov".into();
    video.video_codec = "prores".into();
    assert!(validate_system_outputs(&video, &announced).is_ok());
    assert!(validate_system_outputs(&video, &silent).is_err());
    // A leftover HEIC choice does not stop a video job.
    video.output_format = "heic".into();
    video.video_container = "mp4".into();
    video.video_codec = "av1".into();
    assert!(validate_system_outputs(&video, &silent).is_ok());
}

#[test]
fn exports_take_the_extension_of_the_chosen_format() {
    let mut reserved = ReservedOutputs::default();
    let directory = tempfile::tempdir().unwrap();
    let mut photo = input("upscale");
    photo.output_format = "heic".into();
    let path = unique_output_path(directory.path(), "/in/photo.jpg", &photo, &mut reserved);
    assert_eq!(path.extension().and_then(|e| e.to_str()), Some("heic"));
    let mut video = input("video");
    video.video_container = "mov".into();
    let path = unique_output_path(directory.path(), "/in/clip.mp4", &video, &mut reserved);
    assert_eq!(path.extension().and_then(|e| e.to_str()), Some("mov"));
}

#[test]
fn recipe_stage_validation_accepts_legacy_and_rejects_ambiguous_order() {
    let legacy = Recipe {
        id: "legacy".into(),
        name: "Legacy".into(),
        task: "upscale".into(),
        video_task: String::new(),
        model_id: "model".into(),
        output_scale: 4,
        tile_size: 256,
        halo: 32,
        precision: "fp32".into(),
        safe_memory: true,
        video_model_id: String::new(),
        custom_model_path: String::new(),
        output_format: "png".into(),
        output_bit_depth: None,
        preserve_metadata: Some(true),
        jpeg_quality: Some(98),
        deflicker: None,
        deflicker_window: None,
        video_container: String::new(),
        video_codec: String::new(),
        video_hdr_mode: "tone_map".into(),
        video_target_resolution: 0,
        video_low_memory: true,
        video_crf: None,
        enable_face_model: None,
        face_fidelity: None,
        stages: Vec::new(),
        quality: String::new(),
        content: String::new(),
        fixes: Vec::new(),
        video_interpolation: String::new(),
        video_interpolation_order: String::new(),
        video_slow_motion: false,
        video_clean_audio: false,
        video_audio_model: String::new(),
    };
    assert!(validate_recipe_stages(&legacy).is_ok());

    let mut invalid = legacy;
    invalid.stages = vec![
        RecipeStage {
            kind: "upscale".into(),
            model_id: "first".into(),
            fidelity: None,
            fix: None,
            ..Default::default()
        },
        RecipeStage {
            kind: "upscale".into(),
            model_id: "second".into(),
            fidelity: None,
            fix: None,
            ..Default::default()
        },
    ];
    assert!(validate_recipe_stages(&invalid).is_err());
}

#[test]
fn verified_catalog_import_is_atomic_and_idempotent() {
    let directory = tempfile::tempdir().unwrap();
    let source = directory.path().join("downloaded.pth");
    let destination = directory.path().join("models").join("model.pth");
    fs::create_dir(destination.parent().unwrap()).unwrap();
    fs::write(&source, b"trusted checkpoint").unwrap();
    let source = fs::canonicalize(source).unwrap();
    let digest = catalog::sha256_file(&source).unwrap();

    import_verified_catalog_file(&source, &destination, 18, &digest).unwrap();
    assert_eq!(fs::read(&destination).unwrap(), b"trusted checkpoint");
    assert!(directory
        .path()
        .join("models")
        .read_dir()
        .unwrap()
        .all(|entry| !entry
            .unwrap()
            .file_name()
            .to_string_lossy()
            .ends_with(".tmp")));

    import_verified_catalog_file(&source, &destination, 18, &digest).unwrap();
    assert_eq!(fs::read(&destination).unwrap(), b"trusted checkpoint");
}

#[test]
fn verified_catalog_import_rejects_conflict_and_bad_digest() {
    let directory = tempfile::tempdir().unwrap();
    let source = directory.path().join("downloaded.pth");
    let destination = directory.path().join("models").join("model.pth");
    fs::create_dir(destination.parent().unwrap()).unwrap();
    fs::write(&source, b"trusted checkpoint").unwrap();
    let source = fs::canonicalize(source).unwrap();

    assert!(import_verified_catalog_file(&source, &destination, 18, &"0".repeat(64)).is_err());
    assert!(!destination.exists());

    fs::write(&destination, b"conflicting bytes").unwrap();
    let digest = catalog::sha256_file(&source).unwrap();
    assert!(import_verified_catalog_file(&source, &destination, 18, &digest).is_err());
    assert_eq!(fs::read(&destination).unwrap(), b"conflicting bytes");
}

fn video_media() -> crate::types::MediaItem {
    crate::types::MediaItem {
        id: "media".into(),
        path: "/input/clip.mp4".into(),
        name: "clip.mp4".into(),
        kind: "video".into(),
        width: 640,
        height: 360,
        frame_count: 48,
        fps: 23.976,
        duration_seconds: 2.0,
        preview_data_url: String::new(),
        probe_status: "ready".into(),
        hdr_format: String::new(),
        audio_warning: String::new(),
        has_audio: true,
        field_order: String::new(),
        repair_marks: 0,
        repair_rev: 0,
        error: String::new(),
        selected: true,
    }
}

#[test]
fn double_rate_reaches_the_worker_as_a_deinterlace_stage_with_no_upscale() {
    let mut options = input("video");
    options.video_task = "denoise".into();
    options.model_id = String::new();
    options.output_scale = 1;
    options.step_stages = vec![crate::types::StepStageSelection {
        step: "deinterlace".into(),
        params: json!({"mode": "field", "parity": "auto"})
            .as_object()
            .cloned(),
        mask_path: None,
        model_id: None,
    }];
    let selection = ModelSelection::Image {
        model_id: String::new(),
        path: String::new(),
        approved_checkpoint_sha256: None,
        preprocess: Vec::new(),
        step_models: Vec::new(),
        face_path: None,
        face_model_id: String::new(),
        native_scale: 1,
        skip_upscale: true,
        interpolation: None,
        audio_denoise: None,
    };
    let message = build_job_message(
        "job",
        &video_media(),
        Path::new("/output/clip.mp4"),
        Path::new("/app-owned/work"),
        &options,
        &selection,
    )
    .unwrap();
    let stages = message["data"]["stages"].as_array().unwrap();
    assert_eq!(stages.len(), 1, "{message}");
    assert_eq!(stages[0]["kind"], "deinterlace");
    assert_eq!(stages[0]["params"]["mode"], "field");
    assert_eq!(stages[0]["params"]["parity"], "auto");
}

#[test]
fn frame_interpolation_reaches_the_worker_with_its_downloaded_model() {
    let directory = tempfile::tempdir().unwrap();
    let paths = crate::paths::AppPaths::under(directory.path());
    fs::create_dir_all(&paths.next_root).unwrap();
    fs::create_dir_all(&paths.model_root).unwrap();
    let state = AppState::from_paths(paths).unwrap();
    {
        let mut catalog = lock(&state.catalog).unwrap();
        for model in &mut catalog.models {
            model.installed = model.model_id == "span_photo_x4";
            model.installed_path = model.installed.then(|| "/models/span.pth".into());
        }
    }
    let mut options = input("video");
    options.model_id = "span_photo_x4".into();
    options.video_interpolation = "x2".into();
    options.video_interpolation_order = "before_upscale".into();
    let missing = resolve_model_selection(&state, &options).err().unwrap();
    assert!(missing
        .to_string()
        .contains("download the frame interpolation model first"));
    {
        let mut catalog = lock(&state.catalog).unwrap();
        let rife = catalog
            .interpolation_models
            .iter_mut()
            .find(|model| model.model_id == "rife_4_26")
            .unwrap();
        rife.installed = true;
        rife.installed_path = Some("/models/rife_4_26".into());
    }
    let selection = resolve_model_selection(&state, &options).unwrap();
    let work = Path::new("/app-owned/work");
    let output = Path::new("/output/clip.mp4");
    let message =
        build_job_message("job", &video_media(), output, work, &options, &selection).unwrap();
    assert_eq!(message["data"]["interpolation"], "x2");
    assert_eq!(message["data"]["interpolation_order"], "before_upscale");
    assert_eq!(message["data"]["interpolation_model_id"], "rife_4_26");
    assert_eq!(
        message["data"]["interpolation_bundle_dir"],
        "/models/rife_4_26"
    );

    options.video_interpolation = "off".into();
    let plain = resolve_model_selection(&state, &options).unwrap();
    let message =
        build_job_message("plain", &video_media(), output, work, &options, &plain).unwrap();
    assert_eq!(message["data"]["interpolation"], "off");
    assert!(message["data"]["interpolation_model_id"].is_null());
    assert!(message["data"]["interpolation_bundle_dir"].is_null());

    // A photo job ignores a leftover video setting.
    let mut photo = input("upscale");
    photo.model_id = "span_photo_x4".into();
    photo.video_interpolation = "x2".into();
    let selection = resolve_model_selection(&state, &photo).unwrap();
    let mut image = video_media();
    image.kind = "image".into();
    let message = build_job_message(
        "photo",
        &image,
        Path::new("/output/photo.png"),
        work,
        &photo,
        &selection,
    )
    .unwrap();
    assert_eq!(message["type"], "job_request");
    assert!(message["data"].get("interpolation").is_none());
}

#[test]
fn audio_denoise_reaches_the_worker_and_skips_videos_without_audio() {
    let directory = tempfile::tempdir().unwrap();
    let paths = crate::paths::AppPaths::under(directory.path());
    fs::create_dir_all(&paths.next_root).unwrap();
    fs::create_dir_all(&paths.model_root).unwrap();
    let state = AppState::from_paths(paths).unwrap();
    {
        let mut catalog = lock(&state.catalog).unwrap();
        for model in &mut catalog.models {
            model.installed = model.model_id == "span_photo_x4";
            model.installed_path = model.installed.then(|| "/models/span.pth".into());
        }
    }
    let mut options = input("video");
    options.model_id = "span_photo_x4".into();
    options.video_clean_audio = true;
    let missing = resolve_model_selection(&state, &options).err().unwrap();
    assert!(missing
        .to_string()
        .contains("download the audio cleanup model first"));
    {
        let mut catalog = lock(&state.catalog).unwrap();
        for model in &mut catalog.audio_models {
            model.installed = true;
            model.installed_path = Some(format!("/models/{}", model.family));
        }
    }
    let selection = resolve_model_selection(&state, &options).unwrap();
    let work = Path::new("/app-owned/work");
    let output = Path::new("/output/clip.mp4");
    let with_audio =
        build_job_message("job", &video_media(), output, work, &options, &selection).unwrap();
    assert_eq!(with_audio["data"]["audio_denoise"], true);
    assert_eq!(
        with_audio["data"]["audio_denoise_model_id"],
        "deepfilternet3"
    );
    assert_eq!(
        with_audio["data"]["audio_denoise_bundle_dir"],
        "/models/deepfilternet3"
    );

    // Selecting DPDFNet sends its id and bundle path; unknown id falls back to default.
    let mut dpdf_options = options.clone();
    dpdf_options.video_audio_model = "dpdfnet8_48khz_hr".into();
    let dpdf_selection = resolve_model_selection(&state, &dpdf_options).unwrap();
    let with_dpdf = build_job_message(
        "job-dpdf",
        &video_media(),
        output,
        work,
        &dpdf_options,
        &dpdf_selection,
    )
    .unwrap();
    assert_eq!(
        with_dpdf["data"]["audio_denoise_model_id"],
        "dpdfnet8_48khz_hr"
    );
    assert_eq!(
        with_dpdf["data"]["audio_denoise_bundle_dir"],
        "/models/dpdfnet8_48khz_hr"
    );

    let mut unknown_options = options.clone();
    unknown_options.video_audio_model = "removed_audio_model".into();
    let fallback_selection = resolve_model_selection(&state, &unknown_options).unwrap();
    let with_fallback = build_job_message(
        "job-fallback",
        &video_media(),
        output,
        work,
        &unknown_options,
        &fallback_selection,
    )
    .unwrap();
    assert_eq!(
        with_fallback["data"]["audio_denoise_model_id"],
        "deepfilternet3"
    );

    let mut silent = video_media();
    silent.has_audio = false;
    let without_audio =
        build_job_message("silent", &silent, output, work, &options, &selection).unwrap();
    assert_eq!(without_audio["data"]["audio_denoise"], false);
    assert!(without_audio["data"]["audio_denoise_model_id"].is_null());
    assert!(without_audio["data"]["audio_denoise_bundle_dir"].is_null());

    // Default off when disabled in settings and backward-compatible when omitted.
    let defaults = UiSettings::default();
    assert!(!defaults.video_clean_audio);
    assert!(defaults.video_audio_model.is_empty());
    let mut older = serde_json::to_value(&defaults).unwrap();
    older.as_object_mut().unwrap().remove("video_clean_audio");
    older.as_object_mut().unwrap().remove("video_audio_model");
    let parsed: UiSettings = serde_json::from_value(older).unwrap();
    assert!(!parsed.video_clean_audio);
    assert!(parsed.video_audio_model.is_empty());

    let older_recipe: crate::types::Recipe = serde_json::from_value(serde_json::json!({
        "id": "r1",
        "name": "Old video recipe",
        "task": "video",
        "model_id": "span_photo_x4",
        "output_scale": 4,
        "tile_size": 256,
        "halo": 32,
        "precision": "fp32",
        "safe_memory": false
    }))
    .unwrap();
    assert!(older_recipe.video_audio_model.is_empty());
}

#[test]
fn temporal_jobs_always_send_interpolation_off() {
    let temporal = ModelSelection::Temporal {
        model_id: "seedvr2_3b".into(),
        engine_kind: "seedvr2".into(),
        bundle_dir: "/models/seedvr2".into(),
        temporal_window: 9,
        temporal_overlap: 2,
        audio_denoise: None,
    };
    let message = build_job_message(
        "temporal",
        &video_media(),
        Path::new("/output/clip.mp4"),
        Path::new("/app-owned/work"),
        &input("video"),
        &temporal,
    )
    .unwrap();
    assert_eq!(message["data"]["interpolation"], "off");
}

#[test]
fn interpolation_settings_are_validated_and_default_to_off() {
    let defaults = UiSettings::default();
    assert_eq!(defaults.video_interpolation, "off");
    assert_eq!(defaults.video_interpolation_order, "after_upscale");
    let mut older = serde_json::to_value(UiSettings::default()).unwrap();
    let object = older.as_object_mut().unwrap();
    object.remove("video_interpolation");
    object.remove("video_interpolation_order");
    let older: UiSettings = serde_json::from_value(older).unwrap();
    assert_eq!(older.video_interpolation, "off");
    assert_eq!(older.video_interpolation_order, "after_upscale");

    for target in ["off", "x2", "x4", "fps60", "fps120"] {
        for order in ["after_upscale", "before_upscale"] {
            assert!(
                interpolation_choice_is_valid(target, order),
                "{target} {order}"
            );
        }
    }
    assert!(!interpolation_choice_is_valid("x3", "after_upscale"));
    assert!(!interpolation_choice_is_valid("x2", "sideways"));
    let mut settings = UiSettings {
        video_interpolation: "x8".into(),
        ..UiSettings::default()
    };
    assert!(validate_settings(&settings).is_err());
    settings.video_interpolation = "fps60".into();
    assert!(validate_settings(&settings).is_ok());

    let mut video = input("video");
    video.video_interpolation = "x2".into();
    assert!(validate_start_input(&video, false).is_ok());
    video.video_model_id = "seedvr2_3b".into();
    let error = validate_start_input(&video, false).err().unwrap();
    assert!(error.to_string().contains("Frame-by-frame"));
    // Photos never interpolate, so a leftover video setting does not block them.
    let mut photo = input("upscale");
    photo.video_interpolation = "x2".into();
    assert!(validate_start_input(&photo, false).is_ok());
}

#[test]
fn hdr_preservation_refuses_interpolation_only_for_hdr_sources() {
    let selection = ModelSelection::Image {
        approved_checkpoint_sha256: None,
        model_id: "span_photo_x4".into(),
        path: "/models/span.pth".into(),
        preprocess: Vec::new(),
        step_models: Vec::new(),
        face_path: None,
        face_model_id: String::new(),
        native_scale: 4,
        skip_upscale: false,
        interpolation: Some(ResolvedBundle {
            model_id: "rife_4_26".into(),
            bundle_dir: "/models/rife_4_26".into(),
        }),
        audio_denoise: None,
    };
    let mut options = input("video");
    options.video_interpolation = "x2".into();
    options.video_hdr_mode = "preserve".into();
    let work = Path::new("/app-owned/work");
    let output = Path::new("/output/clip.mp4");
    // The saved HDR choice stays "preserve" for an SDR clip; it has no effect there.
    assert!(build_job_message("sdr", &video_media(), output, work, &options, &selection).is_ok());
    let mut hdr = video_media();
    hdr.hdr_format = "HLG".into();
    let error = build_job_message("hdr", &hdr, output, work, &options, &selection)
        .err()
        .unwrap();
    assert!(error.to_string().contains("HDR"));
    options.video_hdr_mode = "tone_map".into();
    assert!(build_job_message("tone", &hdr, output, work, &options, &selection).is_ok());
}

#[test]
fn the_editing_runtime_is_the_one_the_host_found() {
    let directory = tempfile::tempdir().unwrap();
    let state = AppState::from_paths(AppPaths::under(directory.path())).unwrap();
    let found = directory.path().join("sd-cli");
    let planted = directory.path().join("planted-tool");
    fs::write(&found, b"found").unwrap();
    fs::write(&planted, b"planted").unwrap();
    // Nothing found at start-up: no program can be named.
    assert!(edit_runtime(&state, planted.to_str().unwrap()).is_err());
    *lock(&state.edit_runtime).unwrap() = found.to_string_lossy().into_owned();
    assert_eq!(
        edit_runtime(&state, "").unwrap(),
        fs::canonicalize(&found).unwrap()
    );
    assert_eq!(
        edit_runtime(&state, found.to_str().unwrap()).unwrap(),
        fs::canonicalize(&found).unwrap()
    );
    assert!(edit_runtime(&state, planted.to_str().unwrap()).is_err());
    assert!(edit_runtime(&state, "/nonexistent/sd-cli").is_err());
}

#[test]
fn an_editing_runtime_chosen_in_the_host_dialog_is_accepted() {
    let directory = tempfile::tempdir().unwrap();
    let state = AppState::from_paths(AppPaths::under(directory.path())).unwrap();
    let chosen = directory.path().join("chosen-sd-cli");
    let planted = directory.path().join("planted-tool");
    fs::write(&chosen, b"chosen").unwrap();
    fs::write(&planted, b"planted").unwrap();
    // The build has no runtime: only the host's own picker can supply one.
    assert!(edit_runtime(&state, chosen.to_str().unwrap()).is_err());
    assert!(edit_runtime(&state, "").is_err());
    state.edit_programs.grant(chosen.to_str().unwrap());
    assert_eq!(
        edit_runtime(&state, chosen.to_str().unwrap()).unwrap(),
        fs::canonicalize(&chosen).unwrap()
    );
    assert!(edit_runtime(&state, planted.to_str().unwrap()).is_err());
    // A program granted for FFmpeg is not an editing runtime.
    state.programs.grant(planted.to_str().unwrap());
    assert!(edit_runtime(&state, planted.to_str().unwrap()).is_err());
    // Another valid runtime the user picked later is accepted next to the found one.
    let found = directory.path().join("sd-cli");
    fs::write(&found, b"found").unwrap();
    *lock(&state.edit_runtime).unwrap() = found.to_string_lossy().into_owned();
    assert_eq!(
        edit_runtime(&state, chosen.to_str().unwrap()).unwrap(),
        fs::canonicalize(&chosen).unwrap()
    );
}

#[test]
fn a_batch_names_only_an_ffmpeg_the_host_obtained() {
    let directory = tempfile::tempdir().unwrap();
    let state = AppState::from_paths(AppPaths::under(directory.path())).unwrap();
    let mut input = input("upscale");
    assert!(validate_start(&input, &state).is_ok());
    input.external_ffmpeg_path = "/tmp/planted-tool".into();
    assert!(validate_start(&input, &state).is_err());
    state.programs.grant("/tmp/planted-tool");
    assert!(validate_start(&input, &state).is_ok());
}

#[test]
fn a_long_source_name_is_shortened_so_the_result_name_fits_the_file_system() {
    let directory = tempfile::tempdir().unwrap();
    let mut video = input("video");
    video.video_container = "mkv".into();
    let mut named = input("upscale");
    named.name_by_model = true;
    for (stem, task) in [
        ("a".repeat(300), input("upscale")),
        ("写".repeat(200), input("upscale")),
        ("🎞".repeat(120), input("denoise")),
        ("é".repeat(127) + "x", named),
        ("v".repeat(255), video),
    ] {
        let source = format!("/in/{stem}.png");
        let mut reserved = ReservedOutputs::default();
        let mut names = Vec::new();
        for _ in 0..3 {
            let path = unique_output_path(directory.path(), &source, &task, &mut reserved);
            names.push(path.file_name().unwrap().to_str().unwrap().to_owned());
        }
        let ending = match task.task.as_str() {
            "denoise" => "_denoised",
            "video" => "_video_4x",
            _ => "_upscaled_4x",
        };
        for (index, name) in names.iter().enumerate() {
            assert!(name.len() <= 255, "{} bytes: {name}", name.len());
            assert!(name.contains(ending));
            let extension = if task.task == "video" { ".mkv" } else { ".png" };
            assert!(name.ends_with(extension), "{name}");
            if index > 0 {
                assert!(name[..name.len() - extension.len()].ends_with(&format!("_{index}")));
            }
        }
        assert_eq!(names.iter().collect::<HashSet<_>>().len(), 3);
    }
}

/// What Windows counts: UTF-16 units of the whole path.
fn path_units(path: &Path) -> usize {
    path.to_string_lossy().encode_utf16().count()
}

#[test]
fn the_whole_path_of_a_result_stays_within_the_windows_limit() {
    let mut deep = String::from("C:\\Users\\hermes\\Pictures");
    while deep.len() < 150 {
        deep.push_str("\\Holiday");
    }
    let directory = PathBuf::from(deep);
    for (stem, task) in [
        ("a".repeat(250), input("upscale")),
        ("\u{1f39e}".repeat(100), input("denoise")),
        ("v".repeat(250), input("video")),
    ] {
        let source = format!("/in/{stem}.png");
        let mut reserved = ReservedOutputs::default();
        let mut names = Vec::new();
        for _ in 0..3 {
            let path = unique_output_path_within(
                &directory,
                &source,
                &task,
                &mut reserved,
                Some(WINDOWS_PATH_LIMIT),
            );
            // The terminating NUL counts against the limit.
            assert!(path_units(&path) < WINDOWS_PATH_LIMIT, "{}", path.display());
            names.push(path.file_name().unwrap().to_string_lossy().into_owned());
        }
        assert_eq!(names.iter().collect::<HashSet<_>>().len(), 3, "{names:?}");
        assert!(names.iter().all(|name| name.contains("_upscaled_")
            || name.contains("_denoised")
            || name.contains("_video_")));
    }
    // Without a limit the name is only cut for the file system's own length.
    let mut reserved = ReservedOutputs::default();
    let long = format!("/in/{}.png", "a".repeat(100));
    let path = unique_output_path_within(&directory, &long, &input("upscale"), &mut reserved, None);
    assert!(path_units(&path) > WINDOWS_PATH_LIMIT - 60);
}

#[test]
fn a_folder_too_deep_for_a_sensible_name_is_refused_with_its_name() {
    let task = input("upscale");
    let shallow = PathBuf::from("C:\\Users\\hermes\\Pictures");
    assert!(check_output_room(&shallow, &task, Some(WINDOWS_PATH_LIMIT)).is_ok());
    // No limit, no refusal, however deep.
    let deep = PathBuf::from(format!("C:\\{}", "d".repeat(400)));
    assert!(check_output_room(&deep, &task, None).is_ok());
    // The scratch folder and temporary name the engine makes below the folder need room.
    let cramped = PathBuf::from(format!("C:\\{}", "d".repeat(190)));
    for folder in [&deep, &cramped] {
        let error = check_output_room(folder, &task, Some(WINDOWS_PATH_LIMIT)).unwrap_err();
        let message = error.to_string();
        assert!(message.contains(&*folder.to_string_lossy()), "{message}");
        assert!(message.contains("shorter"), "{message}");
    }
    // Room for a name of 16 characters and the ending, and the engine's temporary.
    let fits = PathBuf::from(format!("C:\\{}", "d".repeat(150)));
    assert!(check_output_room(&fits, &task, Some(WINDOWS_PATH_LIMIT)).is_ok());
    // The extended-length prefix is not part of the path other tools see.
    let prefixed = PathBuf::from(format!("\\\\?\\C:\\{}", "d".repeat(150)));
    assert!(check_output_room(&prefixed, &task, Some(WINDOWS_PATH_LIMIT)).is_ok());
    assert_eq!(
        path_units_without_prefix(&PathBuf::from("\\\\?\\UNC\\server\\share")),
        "\\\\server\\share".len()
    );
}

#[test]
fn a_name_that_already_fits_is_left_as_it_is() {
    let directory = tempfile::tempdir().unwrap();
    let mut reserved = ReservedOutputs::default();
    let path = unique_output_path(
        directory.path(),
        "/in/holiday.png",
        &input("upscale"),
        &mut reserved,
    );
    assert_eq!(path.file_name().unwrap(), "holiday_upscaled_4x.png");
}

#[test]
fn a_benchmark_can_be_exported_under_the_longest_file_name() {
    let directory = tempfile::tempdir().unwrap();
    let destination = directory.path().join(format!("{}.json", "n".repeat(250)));
    write_benchmark_export(&destination, b"{}").unwrap();
    assert_eq!(fs::read(&destination).unwrap(), b"{}");
    assert_eq!(fs::read_dir(directory.path()).unwrap().count(), 1);
}

#[test]
fn a_volume_without_hard_links_gets_a_copy_that_never_replaces_a_file() {
    let directory = tempfile::tempdir().unwrap();
    let no_links =
        |_: &Path, _: &Path| -> std::io::Result<()> { Err(std::io::ErrorKind::Unsupported.into()) };
    let temporary = directory.path().join("temporary");
    fs::write(&temporary, b"new").unwrap();
    let destination = directory.path().join("export.json");
    publish_without_replacing(&temporary, &destination, no_links).unwrap();
    assert_eq!(fs::read(&destination).unwrap(), b"new");
    let error = publish_without_replacing(&temporary, &destination, no_links).unwrap_err();
    assert!(error.to_string().contains("already exists"));
    assert_eq!(fs::read(&destination).unwrap(), b"new");
}

#[test]
fn a_relative_output_folder_is_refused_and_not_created() {
    let directory = tempfile::tempdir().unwrap();
    let state = AppState::from_paths(AppPaths::under(directory.path())).unwrap();
    let name = format!("relative-output-{}", std::process::id());
    let outcome = prepare_output_directory(&state, &format!("{name}/inner"));
    let created = Path::new(&name).exists();
    let _ = fs::remove_dir_all(&name);
    assert!(outcome.is_err());
    assert!(!created);
    let absolute = directory.path().join("made/here");
    assert!(prepare_output_directory(&state, absolute.to_str().unwrap()).is_err());
    assert!(!absolute.exists());
    state.grants.grant_output_folder(&absolute);
    assert!(prepare_output_directory(&state, absolute.to_str().unwrap()).is_ok());
    assert!(absolute.is_dir());
    assert_eq!(
        prepare_output_directory(&state, "  ").unwrap(),
        fs::canonicalize(&state.paths.default_output).unwrap()
    );
}

/// A settings file that cannot be read makes every write fail, as a file the user
/// has to recover first does.
fn state_that_cannot_save(directory: &Path) -> AppState {
    let paths = AppPaths::under(directory);
    fs::create_dir_all(&paths.next_root).unwrap();
    fs::write(&paths.settings, b"{ cut off").unwrap();
    AppState::from_paths(paths).unwrap()
}

fn recipe_named(id: &str) -> Recipe {
    serde_json::from_value(serde_json::json!({
        "id": id, "name": id, "task": "upscale", "model_id": "m", "output_scale": 2,
        "tile_size": 256, "halo": 16, "precision": "fp32", "safe_memory": false
    }))
    .unwrap()
}

#[test]
fn a_failed_write_leaves_the_settings_and_recipes_as_they_were() {
    let directory = tempfile::tempdir().unwrap();
    let state = state_that_cannot_save(directory.path());
    let before = lock(&state.settings).unwrap().tile_size;
    let mut changed = lock(&state.settings).unwrap().clone();
    changed.tile_size = if before == 64 { 128 } else { 64 };
    assert!(replace_settings(&state, changed).is_err());
    assert_eq!(lock(&state.settings).unwrap().tile_size, before);
    assert!(store_recipe(&state, recipe_named("r1")).is_err());
    assert!(lock(&state.recipes).unwrap().is_empty());
    lock(&state.recipes).unwrap().push(recipe_named("kept"));
    lock(&state.settings).unwrap().recipe_pins = vec!["kept".into()];
    lock(&state.settings).unwrap().recipe_recent = vec!["kept".into()];
    assert!(remove_recipe(&state, "kept").is_err());
    assert_eq!(lock(&state.recipes).unwrap().len(), 1);
    assert_eq!(lock(&state.settings).unwrap().recipe_pins, ["kept"]);
    assert_eq!(lock(&state.settings).unwrap().recipe_recent, ["kept"]);
}

#[test]
fn a_written_recipe_is_kept_replaced_and_removed() {
    let directory = tempfile::tempdir().unwrap();
    let state = AppState::from_paths(AppPaths::under(directory.path())).unwrap();
    store_recipe(&state, recipe_named("r1")).unwrap();
    let mut renamed = recipe_named("r1");
    renamed.name = "renamed".into();
    store_recipe(&state, renamed).unwrap();
    assert_eq!(lock(&state.recipes).unwrap().len(), 1);
    assert_eq!(lock(&state.recipes).unwrap()[0].name, "renamed");
    remove_recipe(&state, "r1").unwrap();
    assert!(lock(&state.recipes).unwrap().is_empty());
}

#[test]
fn pins_and_recent_survive_a_settings_save_from_the_window_and_a_restart() {
    let directory = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(directory.path());
    let state = AppState::from_paths(paths.clone()).unwrap();
    for id in ["a", "b"] {
        store_recipe(&state, recipe_named(id)).unwrap();
    }
    // The window sends its whole settings object, as `api.saveSettings` does.
    let mut sent = serde_json::to_value(UiSettings::default()).unwrap();
    sent["recipe_pins"] = json!(["b", "workflow.old-scan"]);
    sent["recipe_recent"] = json!(["a", "b"]);
    save_settings_checked(&state, serde_json::from_value(sent).unwrap()).unwrap();

    let restarted = AppState::from_paths(paths).unwrap();
    let settings = lock(&restarted.settings).unwrap().clone();
    assert_eq!(settings.recipe_pins, ["b", "workflow.old-scan"]);
    assert_eq!(settings.recipe_recent, ["a", "b"]);
    let snapshot = serde_json::to_value(&settings).unwrap();
    assert_eq!(snapshot["recipe_pins"], json!(["b", "workflow.old-scan"]));
}

#[test]
fn deleting_a_recipe_takes_its_id_out_of_the_pins_and_the_recent_list() {
    let directory = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(directory.path());
    let state = AppState::from_paths(paths.clone()).unwrap();
    for id in ["a", "b"] {
        store_recipe(&state, recipe_named(id)).unwrap();
    }
    save_settings_checked(
        &state,
        UiSettings {
            recipe_pins: vec!["a".into(), "workflow.old-scan".into(), "b".into()],
            recipe_recent: vec!["b".into(), "a".into()],
            ..UiSettings::default()
        },
    )
    .unwrap();
    delete_recipe_checked(&state, "a").unwrap();
    for settings in [
        lock(&state.settings).unwrap().clone(),
        crate::settings::load(&paths).0,
    ] {
        assert_eq!(settings.recipe_pins, ["workflow.old-scan", "b"]);
        assert_eq!(settings.recipe_recent, ["b"]);
    }
}

#[test]
fn pins_and_recent_are_bounded_lists_of_different_short_ids() {
    let check = |pins: Vec<String>, recent: Vec<String>| {
        validate_settings(&UiSettings {
            recipe_pins: pins,
            recipe_recent: recent,
            ..UiSettings::default()
        })
    };
    let ids = |count: usize| -> Vec<String> { (0..count).map(|i| format!("r{i}")).collect() };
    assert!(check(ids(20), ids(12)).is_ok());
    assert!(check(vec!["x".repeat(64)], vec!["x".repeat(64)]).is_ok());
    for (pins, recent) in [
        (ids(21), Vec::new()),
        (Vec::new(), ids(13)),
        (vec!["a".into(), "a".into()], Vec::new()),
        (Vec::new(), vec!["a".into(), "a".into()]),
        (vec!["x".repeat(65)], Vec::new()),
        (Vec::new(), vec!["x".repeat(65)]),
        (vec![String::new()], Vec::new()),
    ] {
        assert!(
            check(pins.clone(), recent.clone()).is_err(),
            "{pins:?} {recent:?}"
        );
    }
}

#[test]
fn a_pinned_workflow_does_not_count_toward_the_hundred_recipes() {
    let directory = tempfile::tempdir().unwrap();
    let state = AppState::from_paths(AppPaths::under(directory.path())).unwrap();
    lock(&state.recipes)
        .unwrap()
        .extend((0..100).map(|i| recipe_named(&format!("r{i}"))));
    assert!(store_recipe(&state, recipe_named("r100")).is_err());
    save_settings_checked(
        &state,
        UiSettings {
            recipe_pins: vec!["workflow.old-scan".into(), "r0".into()],
            ..UiSettings::default()
        },
    )
    .unwrap();
    assert_eq!(
        lock(&state.settings).unwrap().recipe_pins,
        ["workflow.old-scan", "r0"]
    );
    assert_eq!(lock(&state.recipes).unwrap().len(), 100);
}

#[cfg(target_os = "linux")]
#[test]
fn a_file_whose_name_is_not_valid_text_is_left_out_of_a_folder() {
    use std::os::unix::ffi::OsStrExt;
    let directory = tempfile::tempdir().unwrap();
    let state = AppState::from_paths(AppPaths::under(&directory.path().join("app"))).unwrap();
    let incoming = directory.path().join("incoming");
    fs::create_dir_all(&incoming).unwrap();
    fs::write(incoming.join("good.png"), b"x").unwrap();
    let odd = incoming.join(std::ffi::OsStr::from_bytes(b"holiday-\xff.png"));
    fs::write(odd, b"x").unwrap();
    state.grants.grant_media_folder(&incoming);
    let folder = incoming.to_string_lossy().into_owned();
    let scanned = scan_media_folder_checked(&state, &folder).unwrap();
    assert_eq!(scanned.len(), 1);
    assert!(scanned[0].ends_with("good.png"));
    let normalized = normalize_media_paths_checked(vec![folder], &state.grants).unwrap();
    assert_eq!(normalized.len(), 1);
    assert!(Path::new(&scanned[0]).exists());
}

#[test]
fn a_video_that_is_gone_is_reported_as_no_longer_available() {
    let directory = tempfile::tempdir().unwrap();
    let present = directory.path().join("clip.mp4");
    fs::write(&present, b"x").unwrap();
    assert_eq!(
        canonical_video(present.clone()).unwrap(),
        fs::canonicalize(&present).unwrap()
    );
    for gone in [
        directory.path().join("missing.mp4"),
        directory.path().to_path_buf(),
    ] {
        let error = canonical_video(gone).unwrap_err();
        assert!(error.to_string().contains("no longer available"), "{error}");
    }
}

#[test]
fn start_requests_from_an_older_window_default_to_no_interpolation() {
    let mut request = serde_json::json!({
        "media_ids": ["media"], "batch_mode": false, "task": "video", "model_id": "model",
        "video_model_id": "frame_by_frame", "fix_stages": [], "custom_model_path": "",
        "output_directory": "", "output_format": "png", "output_scale": 4,
        "preserve_metadata": true, "jpeg_quality": 98, "device": "cpu", "tile_size": 256,
        "halo": 32, "precision": "fp32", "safe_memory": false, "deflicker": false,
        "deflicker_window": 3, "video_container": "mp4", "video_crf": 18,
        "enable_face_model": false, "face_fidelity": 70, "enable_live_preview": true,
        "allow_unsafe_pickle_model": false
    });
    let parsed: StartBatchInput = serde_json::from_value(request.clone()).unwrap();
    assert_eq!(parsed.video_interpolation, "off");
    assert_eq!(parsed.video_interpolation_order, "after_upscale");
    request["video_interpolation"] = "fps120".into();
    let parsed: StartBatchInput = serde_json::from_value(request).unwrap();
    assert_eq!(parsed.video_interpolation, "fps120");
}

#[test]
fn a_start_that_cannot_be_written_is_refused_before_anything_is_queued() {
    let selection = ModelSelection::Image {
        approved_checkpoint_sha256: None,
        model_id: "model".into(),
        path: "/models/m.pth".into(),
        preprocess: Vec::new(),
        step_models: Vec::new(),
        face_path: None,
        face_model_id: String::new(),
        native_scale: 4,
        skip_upscale: false,
        interpolation: None,
        audio_denoise: None,
    };
    let photo = crate::types::MediaItem {
        id: "media".into(),
        path: "/in/phone.jpg".into(),
        name: "phone.jpg".into(),
        kind: "image".into(),
        width: 3024,
        height: 4032,
        frame_count: 1,
        fps: 0.0,
        duration_seconds: 0.0,
        preview_data_url: String::new(),
        probe_status: "ready".into(),
        hdr_format: String::new(),
        audio_warning: String::new(),
        has_audio: true,
        field_order: String::new(),
        repair_marks: 0,
        repair_rev: 0,
        error: String::new(),
        selected: true,
    };
    let folder = Path::new("/Users/a/Desktop");
    crate::disk_room::set_test_volume(Some(crate::disk_room::Volume {
        free: 4_700_000_000,
        total: 500_000_000_000,
        system: true,
    }));
    let refused = refuse_when_disk_is_short(&input("upscale"), &selection, &photo, folder);
    // 8 GB must stay free on this volume; one photo at 4x needs 1.2 GB more.
    match refused {
        Err(AppError::Validation(text)) => assert_eq!(
            text,
            "Not enough free disk space in \"/Users/a/Desktop\": about 1.2 GB is needed, but only 4.7 GB is free, and 8.0 GB must stay free so the computer keeps working."
        ),
        other => panic!("not refused: {other:?}"),
    }
    crate::disk_room::set_test_volume(Some(crate::disk_room::Volume {
        free: 1_173_856_576 + 8_000_000_000,
        total: 500_000_000_000,
        system: true,
    }));
    assert!(refuse_when_disk_is_short(&input("upscale"), &selection, &photo, folder).is_ok());
    crate::disk_room::set_test_volume(None);
}

fn state_with_result(directory: &Path, file: &str, contents: &[u8]) -> (AppState, PathBuf) {
    let root = std::fs::canonicalize(directory).unwrap();
    let state = AppState::from_paths(crate::paths::AppPaths::under(&root.join("app"))).unwrap();
    let output = root.join(file);
    fs::write(&output, contents).unwrap();
    {
        let database = lock(&state.database).unwrap();
        database
            .insert_media("m", "/in/photo.png", "photo.png", "image")
            .unwrap();
        database.insert_job("j", "m", "{}").unwrap();
        database
            .finish_job("j", "completed", &output.to_string_lossy(), "")
            .unwrap();
    }
    (state, output)
}

#[test]
fn a_result_becomes_one_new_input_and_the_original_stays() {
    let directory = tempfile::tempdir().unwrap();
    let directory_root = std::fs::canonicalize(directory.path()).unwrap();
    let (state, output) =
        state_with_result(directory_root.as_path(), "photo_upscaled_4x.png", b"png");
    let requested = output.to_string_lossy().into_owned();
    let (id, to_probe) = add_result_as_input(&state, &requested).unwrap();
    let canonical = fs::canonicalize(&output)
        .unwrap()
        .to_string_lossy()
        .into_owned();
    assert_eq!(to_probe.as_deref(), Some(canonical.as_str()));
    let (again, to_probe) = add_result_as_input(&state, &requested).unwrap();
    assert_eq!(again, id);
    assert!(to_probe.is_none());
    let media = lock(&state.database).unwrap().list_media().unwrap();
    assert_eq!(media.len(), 2);
    let original = media.iter().find(|item| item.id == "m").unwrap();
    assert_eq!(original.path, "/in/photo.png");
    assert!(!original.selected);
    let added = media.iter().find(|item| item.id == id).unwrap();
    assert!(added.selected);
    assert_eq!(added.name, "photo_upscaled_4x.png");
}

#[test]
fn only_an_authorised_existing_result_becomes_an_input() {
    let directory = tempfile::tempdir().unwrap();
    let (state, output) = state_with_result(directory.path(), "result.png", b"png");
    let stranger = directory.path().join("stranger.png");
    fs::write(&stranger, b"png").unwrap();
    assert!(add_result_as_input(&state, &stranger.to_string_lossy()).is_err());
    assert!(add_result_as_input(&state, "unknown-id").is_err());
    fs::remove_file(&output).unwrap();
    assert!(add_result_as_input(&state, &output.to_string_lossy()).is_err());
    assert_eq!(
        lock(&state.database).unwrap().list_media().unwrap().len(),
        1
    );
}

#[test]
fn a_result_that_cannot_be_a_source_is_refused() {
    let directory = tempfile::tempdir().unwrap();
    let directory_root = std::fs::canonicalize(directory.path()).unwrap();
    let (state, output) = state_with_result(directory_root.as_path(), "notes.txt", b"text");
    let error = add_result_as_input(&state, &output.to_string_lossy()).unwrap_err();
    assert!(error.to_string().contains("cannot be used as an input"));
    assert_eq!(
        lock(&state.database).unwrap().list_media().unwrap().len(),
        1
    );
}

#[cfg(unix)]
#[test]
fn a_result_that_became_a_link_is_not_added() {
    let directory = tempfile::tempdir().unwrap();
    let (state, output) = state_with_result(directory.path(), "result.png", b"png");
    let private = directory.path().join("private.png");
    fs::write(&private, b"private").unwrap();
    fs::remove_file(&output).unwrap();
    std::os::unix::fs::symlink(&private, &output).unwrap();
    assert!(add_result_as_input(&state, &output.to_string_lossy()).is_err());
    assert_eq!(
        lock(&state.database).unwrap().list_media().unwrap().len(),
        1
    );
}

#[test]
fn switching_the_preview_patch_off_tells_the_engine_to_release_the_colour_model() {
    let directory = tempfile::tempdir().unwrap();
    let paths = crate::paths::AppPaths::under(directory.path());
    fs::create_dir_all(&paths.next_root).unwrap();
    fs::create_dir_all(&paths.model_root).unwrap();
    let state = AppState::from_paths(paths).unwrap();
    let (sender, receiver) = std::sync::mpsc::channel();
    *lock(&state.worker.sender).unwrap() = Some(sender);
    let release = |expected: bool| {
        let sent: Vec<Value> = receiver
            .try_iter()
            .map(|line| serde_json::from_str(&line).unwrap())
            .collect();
        assert_eq!(
            sent.iter()
                .any(|message| message["type"] == "patch_release"),
            expected
        );
    };

    // An engine without the feature never kept a model.
    apply_preference(&state, "preview_patch_shown", &serde_json::json!(false)).unwrap();
    release(false);

    *lock(&state.engine).unwrap() = Some(crate::types::EngineInfo {
        protocol_version: 1,
        minimum_protocol_version: 1,
        engine_id: "test".into(),
        engine_version: "1".into(),
        features: vec!["patch_preview".into()],
        model_formats: vec![],
        video_engines: vec![],
    });
    apply_preference(&state, "preview_patch_shown", &serde_json::json!(true)).unwrap();
    release(false);
    apply_preference(&state, "preview_patch_shown", &serde_json::json!(false)).unwrap();
    release(true);
    apply_preference(&state, "preview_patch_shown", &serde_json::json!(true)).unwrap();
    release(false);
    apply_preference(&state, "preview_patch", &serde_json::json!("off")).unwrap();
    release(true);
}

#[test]
fn low_priority_switch_saves_while_a_job_runs_and_notifies_the_engine() {
    let directory = tempfile::tempdir().unwrap();
    let paths = crate::paths::AppPaths::under(directory.path());
    fs::create_dir_all(&paths.next_root).unwrap();
    fs::create_dir_all(&paths.model_root).unwrap();
    let state = AppState::from_paths(paths).unwrap();
    assert!(!crate::settings::load(&state.paths).0.low_priority);

    let (sender, receiver) = std::sync::mpsc::channel();
    *lock(&state.worker.sender).unwrap() = Some(sender);
    lock(&state.runtime).unwrap().active_job_id = "running".into();

    apply_preference(&state, "low_priority", &serde_json::json!(true)).unwrap();
    let saved = crate::settings::load(&state.paths).0;
    assert!(saved.low_priority);

    let message: Value = serde_json::from_str(&receiver.try_recv().unwrap()).unwrap();
    assert_eq!(message["type"], "set_low_priority");
    assert_eq!(message["data"]["low_priority"], true);

    let mut stale = UiSettings::default();
    keep_update_switches(&saved, &mut stale);
    assert!(stale.low_priority);

    // Older settings JSON without low_priority defaults to false.
    let mut old = serde_json::to_value(UiSettings::default()).unwrap();
    old.as_object_mut().unwrap().remove("low_priority");
    let loaded = serde_json::from_value::<UiSettings>(old).unwrap();
    assert!(!loaded.low_priority);
}

#[test]
fn menu_bar_and_finish_notification_switches_save_while_running_and_default_on() {
    let directory = tempfile::tempdir().unwrap();
    let paths = crate::paths::AppPaths::under(directory.path());
    fs::create_dir_all(&paths.next_root).unwrap();
    fs::create_dir_all(&paths.model_root).unwrap();
    let state = AppState::from_paths(paths).unwrap();
    assert!(crate::settings::load(&state.paths).0.show_in_menu_bar);
    assert!(crate::settings::load(&state.paths).0.notify_on_finish);

    lock(&state.runtime).unwrap().active_job_id = "running".into();
    apply_preference(&state, "show_in_menu_bar", &serde_json::json!(false)).unwrap();
    apply_preference(&state, "notify_on_finish", &serde_json::json!(false)).unwrap();
    let saved = crate::settings::load(&state.paths).0;
    assert!(!saved.show_in_menu_bar);
    assert!(!saved.notify_on_finish);

    let mut stale = UiSettings::default();
    keep_update_switches(&saved, &mut stale);
    assert!(!stale.show_in_menu_bar);
    assert!(!stale.notify_on_finish);

    let mut old = serde_json::to_value(UiSettings::default()).unwrap();
    let map = old.as_object_mut().unwrap();
    map.remove("show_in_menu_bar");
    map.remove("notify_on_finish");
    let loaded = serde_json::from_value::<UiSettings>(old).unwrap();
    assert!(loaded.show_in_menu_bar);
    assert!(loaded.notify_on_finish);
}

#[test]
fn custom_model_tutorial_url_points_to_the_exact_help_anchor() {
    assert_eq!(
        CUSTOM_MODEL_TUTORIAL_URL,
        "https://herrei.github.io/localsr/help/#custom-model"
    );
}

#[test]
fn video_restore_and_fix_first_build_stages_names_and_reject_temporal() {
    let directory = tempfile::tempdir().unwrap();
    let paths = crate::paths::AppPaths::under(directory.path());
    fs::create_dir_all(&paths.next_root).unwrap();
    fs::create_dir_all(&paths.model_root).unwrap();
    let state = AppState::from_paths(paths).unwrap();
    // Mark required models installed in the catalog.
    {
        let mut catalog = lock(&state.catalog).unwrap();
        for id in ["fbcnn_color", "scunet_color_real_psnr", "span_photo_x4"] {
            let entry = catalog
                .models
                .iter_mut()
                .find(|m| m.model_id == id)
                .unwrap();
            entry.installed = true;
            entry.installed_path = Some(format!("/models/{id}.pth"));
        }
    }

    // 1) Video Restore with prefix deblock fix + primary restore at 1x.
    let mut restore_input = input("video");
    restore_input.video_task = "denoise".into();
    restore_input.model_id = "scunet_color_real_psnr".into();
    restore_input.output_scale = 4; // clamped to 1 for video restore
    restore_input.fix_stages = vec![
        crate::types::FixStageSelection {
            fix: "jpeg".into(),
            model_id: "fbcnn_color".into(),
            ..Default::default()
        },
        crate::types::FixStageSelection {
            fix: "noise".into(),
            model_id: "scunet_color_real_psnr".into(),
            ..Default::default()
        },
    ];
    let selection = resolve_model_selection(&state, &restore_input).unwrap();
    let msg = build_job_message(
        "vid-restore",
        &video_media(),
        Path::new("/output/clip_video_restored.mp4"),
        Path::new("/app-owned/work"),
        &restore_input,
        &selection,
    )
    .unwrap();
    assert_eq!(msg["type"], "video_job_request");
    assert_eq!(msg["data"]["output_scale"], 1);
    assert_eq!(msg["data"]["stages"].as_array().unwrap().len(), 2);
    assert_eq!(msg["data"]["stages"][0]["kind"], "deblock");
    assert_eq!(msg["data"]["stages"][0]["role"], "fix");
    assert_eq!(msg["data"]["stages"][1]["kind"], "restore");
    assert_eq!(msg["data"]["stages"][1]["role"], "primary");
    assert_eq!(output_ending(&restore_input).0, "_video_restored");

    // 2) Video Upscale at 1x with fix-first stage skips the SR model stage.
    let mut upscale_1x = input("video");
    upscale_1x.video_task = "upscale".into();
    upscale_1x.model_id = "span_photo_x4".into();
    upscale_1x.output_scale = 1;
    upscale_1x.fix_stages = vec![crate::types::FixStageSelection {
        fix: "jpeg".into(),
        model_id: "fbcnn_color".into(),
        ..Default::default()
    }];
    let selection_1x = resolve_model_selection(&state, &upscale_1x).unwrap();
    let msg_1x = build_job_message(
        "vid-1x",
        &video_media(),
        Path::new("/output/clip_video_upscaled_1x.mp4"),
        Path::new("/app-owned/work"),
        &upscale_1x,
        &selection_1x,
    )
    .unwrap();
    assert_eq!(msg_1x["data"]["output_scale"], 1);
    assert_eq!(msg_1x["data"]["model_path"], "");
    assert_eq!(msg_1x["data"]["stages"].as_array().unwrap().len(), 1);
    assert_eq!(msg_1x["data"]["stages"][0]["kind"], "deblock");
    assert_eq!(output_ending(&upscale_1x).0, "_video_1x");

    // 2b) At 1x Upscale, an uninstalled SR model is not required, while empty 1x work is refused.
    {
        let mut catalog = lock(&state.catalog).unwrap();
        let entry = catalog
            .models
            .iter_mut()
            .find(|m| m.model_id == "span_photo_x4")
            .unwrap();
        entry.installed = false;
        entry.installed_path = None;
    }
    assert!(resolve_model_selection(&state, &upscale_1x).is_ok());
    let mut empty_1x = upscale_1x.clone();
    empty_1x.fix_stages.clear();
    empty_1x.deflicker = false;
    empty_1x.video_interpolation = "off".into();
    empty_1x.video_clean_audio = false;
    assert!(validate_start_input(&empty_1x, true).is_err());

    // 2c) Temporal engine at Match 1x scale also skips the video model and requires other work.
    let mut temporal_1x_empty = empty_1x.clone();
    temporal_1x_empty.video_model_id = "seedvr2_3b".into();
    temporal_1x_empty.video_target_resolution = 0;
    assert!(validate_start_input(&temporal_1x_empty, true).is_err());

    // 3) Temporal SeedVR2 rejects video_task == "denoise" and rejects fix_stages.
    let mut bad_restore = restore_input.clone();
    bad_restore.video_model_id = "seedvr2_3b".into();
    bad_restore.fix_stages.clear();
    assert!(resolve_model_selection(&state, &bad_restore).is_err());

    let mut bad_fixes = upscale_1x.clone();
    bad_fixes.video_model_id = "seedvr2_3b".into();
    assert!(resolve_model_selection(&state, &bad_fixes).is_err());

    // 4) Video disk check uses 1x for video Restore rather than 4x.
    let big_video = crate::types::MediaItem {
        id: "vid".into(),
        path: "/in/clip.mp4".into(),
        name: "clip.mp4".into(),
        kind: "video".into(),
        width: 1920,
        height: 1080,
        frame_count: 10,
        fps: 24.0,
        duration_seconds: 0.4,
        preview_data_url: String::new(),
        probe_status: "ready".into(),
        hdr_format: String::new(),
        has_audio: true,
        field_order: String::new(),
        repair_marks: 0,
        repair_rev: 0,
        audio_warning: String::new(),
        error: String::new(),
        selected: true,
    };
    let folder = Path::new("/Users/a/Desktop");
    crate::disk_room::set_test_volume(Some(crate::disk_room::Volume {
        free: 9_000_000_000,
        total: 500_000_000_000,
        system: true,
    }));
    // 1x restore passes while 4x upscale requires 16x pixel workspace.
    assert!(refuse_when_disk_is_short(&restore_input, &selection, &big_video, folder).is_ok());
    crate::disk_room::set_test_volume(None);

    // 5) Recipe validation accepts video Restore and video Fix-first recipes.
    let video_restore_recipe: Recipe = serde_json::from_value(serde_json::json!({
        "id": "vr",
        "name": "Video Restore",
        "task": "video",
        "video_task": "denoise",
        "model_id": "scunet_color_real_psnr",
        "video_model_id": "frame_by_frame",
        "output_scale": 1,
        "tile_size": 256,
        "halo": 16,
        "precision": "fp32",
        "safe_memory": true,
        "fixes": ["jpeg"],
        "stages": [
            {"kind": "deblock", "model_id": "fbcnn_color", "role": "fix", "fix": "jpeg"},
            {"kind": "restore", "model_id": "scunet_color_real_psnr", "role": "primary"}
        ]
    }))
    .unwrap();
    assert!(validate_recipe_stages(&video_restore_recipe).is_ok());

    // 6) No-chip video Restore runs only video/audio options and rejects when nothing is on.
    let mut no_chip_restore = input("video");
    no_chip_restore.video_task = "denoise".into();
    no_chip_restore.model_id.clear();
    no_chip_restore.fix_stages.clear();
    no_chip_restore.output_scale = 1;
    no_chip_restore.deflicker = false;
    no_chip_restore.video_interpolation = "off".into();
    no_chip_restore.video_clean_audio = false;
    assert!(validate_start_input(&no_chip_restore, true).is_err());

    no_chip_restore.deflicker = true;
    assert!(validate_start_input(&no_chip_restore, true).is_ok());
    let no_chip_selection = resolve_model_selection(&state, &no_chip_restore).unwrap();
    let no_chip_msg = build_job_message(
        "vid-restore-deflicker",
        &video_media(),
        Path::new("/output/clip_video_restored.mp4"),
        Path::new("/app-owned/work"),
        &no_chip_restore,
        &no_chip_selection,
    )
    .unwrap();
    assert_eq!(no_chip_msg["data"]["model_path"], "");
    assert_eq!(no_chip_msg["data"]["output_scale"], 1);
    assert_eq!(no_chip_msg["data"]["stages"], serde_json::json!([]));
    assert_eq!(no_chip_msg["data"]["deflicker"], true);
}

#[test]
fn ungranted_paths_are_refused_for_every_path_taking_command() {
    let dir = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(&dir.path().join("app"));
    fs::create_dir_all(&paths.next_root).unwrap();
    fs::create_dir_all(&paths.model_root).unwrap();
    fs::create_dir_all(&paths.work_root).unwrap();
    fs::create_dir_all(&paths.default_output).unwrap();

    let ungranted_dir = dir.path().join("ungranted_dir");
    let ungranted_img = dir.path().join("ungranted.png");
    let ungranted_vid = dir.path().join("ungranted.mp4");
    let ungranted_model = dir.path().join("ungranted.safetensors");
    let ungranted_export = dir.path().join("ungranted_bench.json");
    let ungranted_exe = dir.path().join("ungranted_tool");
    fs::create_dir_all(&ungranted_dir).unwrap();
    fs::write(&ungranted_img, b"img").unwrap();
    fs::write(&ungranted_vid, b"vid").unwrap();
    fs::write(&ungranted_model, b"model").unwrap();
    fs::write(&ungranted_exe, b"bin").unwrap();

    let (state, trial_ids) = trial_state(dir.path(), 2, upscaler);
    lock(&state.capabilities).unwrap().system_ram_total = 8 << 30;
    lock(&state.runtime).unwrap().worker = "ready".into();
    *lock(&state.latest_benchmark).unwrap() = Some(
        serde_json::from_value(json!({
            "workload_version": "localsr-benchmark-v1",
            "backend": "cpu",
            "device": "cpu",
            "model_id": "span_photo_x4",
            "model_name": "Quick",
            "scale": 4,
            "input_width": 128,
            "input_height": 128,
            "warmup_count": 1,
            "measured_frame_count": 5,
            "median_inference_ms": 40.0,
            "p95_inference_ms": 48.0,
            "end_to_end_fps": 25.0,
            "processed_megapixels_per_second": 0.4,
            "total_elapsed_seconds": 0.2,
            "peak_memory_bytes": null,
            "score": 409.6
        }))
        .unwrap(),
    );

    // Prepare an ungranted video row in DB and an edit bundle so start_edit / video comparison
    // reach the path grant checks.
    let edit_model = crate::editing::catalog(&state.paths)
        .unwrap()
        .into_iter()
        .next()
        .unwrap();
    let edit_bundle_dir = state.paths.model_root.join(&edit_model.family);
    fs::create_dir_all(&edit_bundle_dir).unwrap();
    for file in &edit_model.files {
        let handle = fs::File::create(edit_bundle_dir.join(&file.filename)).unwrap();
        handle.set_len(file.size_bytes).unwrap();
    }
    *lock(&state.engine).unwrap() = Some(crate::types::EngineInfo {
        protocol_version: 1,
        minimum_protocol_version: 1,
        engine_id: "test".into(),
        engine_version: "1".into(),
        features: vec!["image_edit".into()],
        model_formats: vec![],
        video_engines: vec![],
    });
    lock(&state.capabilities)
        .unwrap()
        .devices
        .push(crate::types::DeviceInfo {
            id: "mps".into(),
            device_type: "mps".into(),
            name: "Apple GPU".into(),
            ..Default::default()
        });
    let edit_runtime_file = dir.path().join("sd-cli");
    fs::write(&edit_runtime_file, b"rt").unwrap();
    *lock(&state.edit_runtime).unwrap() = edit_runtime_file.to_string_lossy().into_owned();

    {
        let db = lock(&state.database).unwrap();
        db.insert_media(
            "ungranted-vid",
            ungranted_vid.to_str().unwrap(),
            "u.mp4",
            "video",
        )
        .unwrap();
    }

    let app = tauri::test::mock_app();
    let state = Arc::new(state);
    app.manage(state.clone());

    let catalog_model_id = lock(&state.catalog).unwrap().models[0].model_id.clone();
    let ungranted_original = vec![crate::compare::ComparePick::Original {
        media_id: "ungranted-vid".into(),
    }];
    let compare_generation = {
        let database = lock(&state.database).unwrap();
        let mut runtime = lock(&state.runtime).unwrap();
        crate::compare::plan(&database, &mut runtime.compare, &ungranted_original).unwrap();
        runtime.compare.generation
    };

    type Case = (&'static str, AppResult<()>);
    let cases: Vec<Case> = vec![
        (
            "scan_media_folder",
            scan_media_folder_checked(&state, &ungranted_dir.to_string_lossy()).map(drop),
        ),
        (
            "add_media",
            add_media_checked(
                &state,
                vec![ungranted_img.to_string_lossy().into_owned()],
                false,
            )
            .map(drop),
        ),
        (
            "save_settings(output_directory)",
            save_settings_checked(
                &state,
                UiSettings {
                    output_directory: ungranted_dir.to_string_lossy().into_owned(),
                    ..UiSettings::default()
                },
            ),
        ),
        (
            "save_settings(custom_model_path)",
            save_settings_checked(
                &state,
                UiSettings {
                    custom_model_path: ungranted_model.to_string_lossy().into_owned(),
                    ..UiSettings::default()
                },
            ),
        ),
        (
            "save_settings(external_ffmpeg_path)",
            save_settings_checked(
                &state,
                UiSettings {
                    external_ffmpeg_path: ungranted_exe.to_string_lossy().into_owned(),
                    ..UiSettings::default()
                },
            ),
        ),
        (
            "save_recipe(custom_model_path)",
            save_recipe_checked(
                &state,
                Recipe {
                    custom_model_path: ungranted_model.to_string_lossy().into_owned(),
                    ..recipe_named("r")
                },
            ),
        ),
        (
            "start_jobs(output_directory)",
            start_jobs_checked(
                &state,
                StartBatchInput {
                    model_id: trial_ids[0].clone(),
                    output_directory: ungranted_dir.to_string_lossy().into_owned(),
                    ..input("upscale")
                },
            ),
        ),
        (
            "start_jobs(custom_model_path)",
            start_jobs_checked(
                &state,
                StartBatchInput {
                    model_id: "__custom__".into(),
                    custom_model_path: ungranted_model.to_string_lossy().into_owned(),
                    ..input("upscale")
                },
            ),
        ),
        (
            "start_jobs(external_ffmpeg_path)",
            start_jobs_checked(
                &state,
                StartBatchInput {
                    model_id: trial_ids[0].clone(),
                    external_ffmpeg_path: ungranted_exe.to_string_lossy().into_owned(),
                    ..input("upscale")
                },
            ),
        ),
        (
            "start_edit(output_directory)",
            start_edit_checked(
                &state,
                crate::editing::StartEditInput {
                    media_id: "media".into(),
                    model_id: edit_model.model_id.clone(),
                    prompt: "Brighten".into(),
                    runtime_path: String::new(),
                    device: "mps".into(),
                    max_dimension: 512,
                    steps: 20,
                    seed: 1,
                    accepted_terms: true,
                    output_directory: ungranted_dir.to_string_lossy().into_owned(),
                    edit_text_encoder: "auto".into(),
                    edit_vae: "auto".into(),
                    edit_vram_limit_mb: None,
                    edit_allow_any_size: None,
                },
            ),
        ),
        (
            "start_edit(runtime_path)",
            start_edit_checked(
                &state,
                crate::editing::StartEditInput {
                    media_id: "media".into(),
                    model_id: edit_model.model_id.clone(),
                    prompt: "Brighten".into(),
                    runtime_path: ungranted_exe.to_string_lossy().into_owned(),
                    device: "mps".into(),
                    max_dimension: 512,
                    steps: 20,
                    seed: 1,
                    accepted_terms: true,
                    output_directory: String::new(),
                    edit_text_encoder: "auto".into(),
                    edit_vae: "auto".into(),
                    edit_vram_limit_mb: None,
                    edit_allow_any_size: None,
                },
            ),
        ),
        (
            "start_model_trials(output_directory)",
            admit_model_trials(
                &state,
                "media",
                &trial_ids,
                &StartBatchInput {
                    model_id: trial_ids[0].clone(),
                    output_directory: ungranted_dir.to_string_lossy().into_owned(),
                    ..input("upscale")
                },
            )
            .map(drop),
        ),
        (
            "start_model_trials(external_ffmpeg_path)",
            admit_model_trials(
                &state,
                "media",
                &trial_ids,
                &StartBatchInput {
                    model_id: trial_ids[0].clone(),
                    external_ffmpeg_path: ungranted_exe.to_string_lossy().into_owned(),
                    ..input("upscale")
                },
            )
            .map(drop),
        ),
        (
            "estimate_model_trials(output_directory)",
            estimate_model_trials(
                app.state(),
                "media".into(),
                trial_ids.clone(),
                StartBatchInput {
                    model_id: trial_ids[0].clone(),
                    output_directory: ungranted_dir.to_string_lossy().into_owned(),
                    ..input("upscale")
                },
            )
            .map(drop),
        ),
        (
            "estimate_model_trials(custom_model_path)",
            estimate_model_trials(
                app.state(),
                "media".into(),
                trial_ids.clone(),
                StartBatchInput {
                    model_id: "__custom__".into(),
                    custom_model_path: ungranted_model.to_string_lossy().into_owned(),
                    ..input("upscale")
                },
            )
            .map(drop),
        ),
        (
            "export_benchmark",
            export_benchmark(app.state(), ungranted_export.to_string_lossy().into_owned()),
        ),
        (
            "probe_path",
            probe_path_checked(&state, &ungranted_img.to_string_lossy()).map(drop),
        ),
        (
            "import_catalog_model",
            import_catalog_model_checked(
                &state,
                &catalog_model_id,
                &ungranted_model.to_string_lossy(),
                true,
            ),
        ),
        (
            "open_result",
            open_result(app.state(), ungranted_img.to_string_lossy().into_owned()),
        ),
        (
            "reveal_result",
            reveal_result(app.state(), ungranted_img.to_string_lossy().into_owned()),
        ),
        (
            "use_result_as_input",
            add_result_as_input(&state, &ungranted_img.to_string_lossy()).map(drop),
        ),
        (
            "prepare_output_directory",
            prepare_output_directory(&state, &ungranted_dir.to_string_lossy()).map(drop),
        ),
        (
            "prepare_video_comparison",
            resolve_video_comparison_inputs(&state, "ungranted-vid", None).map(drop),
        ),
        (
            "request_compare",
            request_compare_checked(&state, &ungranted_original),
        ),
        (
            "request_compare_regions",
            request_compare_regions_checked(&state, compare_generation, &[None]),
        ),
    ];

    for (name, outcome) in cases {
        assert!(
            outcome.is_err(),
            "{name} unexpectedly accepted ungranted path"
        );
    }

    // open_output_directory refuses an ungranted settings.output_directory as well.
    lock(&state.settings).unwrap().output_directory = ungranted_dir.to_string_lossy().into_owned();
    assert!(open_output_directory(app.state()).is_err());

    assert!(!ungranted_export.exists());
}

#[test]
fn parent_traversal_and_symlink_escape_are_refused_by_commands() {
    let dir = tempfile::tempdir().unwrap();
    let state = Arc::new(AppState::from_paths(AppPaths::under(&dir.path().join("app"))).unwrap());
    let app = tauri::test::mock_app();
    app.manage(state.clone());

    let granted = dir.path().join("granted");
    let outside = dir.path().join("outside");
    fs::create_dir_all(&granted).unwrap();
    fs::create_dir_all(&outside).unwrap();
    let secret_img = outside.join("secret.png");
    let secret_model = outside.join("secret.safetensors");
    fs::write(&secret_img, b"img").unwrap();
    fs::write(&secret_model, b"model").unwrap();

    state.grants.grant_media_folder(&granted);
    state.grants.grant_output_folder(&granted);

    // `..` escape attempts.
    assert!(add_media_checked(
        &state,
        vec![granted
            .join("..")
            .join("outside")
            .join("secret.png")
            .to_string_lossy()
            .into_owned()],
        false,
    )
    .is_err());
    assert!(scan_media_folder_checked(
        &state,
        &granted.join("..").join("outside").to_string_lossy(),
    )
    .is_err());
    assert!(save_settings_checked(
        &state,
        UiSettings {
            output_directory: granted
                .join("new_sub")
                .join("..")
                .join("..")
                .join("outside")
                .to_string_lossy()
                .into_owned(),
            ..UiSettings::default()
        },
    )
    .is_err());

    #[cfg(unix)]
    {
        use std::os::unix::fs::symlink;
        let escaped_img = granted.join("escape.png");
        let escaped_dir = granted.join("escape_dir");
        symlink(&secret_img, &escaped_img).unwrap();
        symlink(&outside, &escaped_dir).unwrap();

        assert!(add_media_checked(
            &state,
            vec![escaped_img.to_string_lossy().into_owned()],
            false,
        )
        .is_err());
        assert!(save_settings_checked(
            &state,
            UiSettings {
                output_directory: escaped_dir.join("child").to_string_lossy().into_owned(),
                ..UiSettings::default()
            },
        )
        .is_err());
        // export_benchmark refuses symlink destination outside export grant.
        let export_target = granted.join("export.json");
        state.grants.grant_export_file(&export_target);
        let escape_export = granted.join("escape_export.json");
        symlink(outside.join("escape.json"), &escape_export).unwrap();
        assert!(
            export_benchmark(app.state(), escape_export.to_string_lossy().into_owned(),).is_err()
        );
    }
}

#[test]
fn persisted_grants_survive_restart_and_removal_drops_them() {
    let dir = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(&dir.path().join("app"));
    let photo1 = dir.path().join("photo1.png");
    let photo2 = dir.path().join("photo2.png");
    let out_dir1 = dir.path().join("out1");
    let out_dir2 = dir.path().join("out2");
    let model1 = dir.path().join("model1.safetensors");
    let model2 = dir.path().join("model2.safetensors");
    fs::write(&photo1, b"p1").unwrap();
    fs::write(&photo2, b"p2").unwrap();
    fs::create_dir_all(&out_dir1).unwrap();
    fs::create_dir_all(&out_dir2).unwrap();
    fs::write(&model1, b"m1").unwrap();
    fs::write(&model2, b"m2").unwrap();

    // First session: grant and persist media, output directory, custom model, and recipe model.
    {
        let state = AppState::from_paths(paths.clone()).unwrap();

        state.grants.grant_media_files(&[&photo1, &photo2]);
        add_media_checked(
            &state,
            vec![
                photo1.to_string_lossy().into_owned(),
                photo2.to_string_lossy().into_owned(),
            ],
            false,
        )
        .unwrap();

        state.grants.grant_output_folder(&out_dir1);
        state.grants.grant_model_file(&model1);
        save_settings_checked(
            &state,
            UiSettings {
                output_directory: out_dir1.to_string_lossy().into_owned(),
                custom_model_path: model1.to_string_lossy().into_owned(),
                ..UiSettings::default()
            },
        )
        .unwrap();

        state.grants.grant_model_file(&model2);
        save_recipe_checked(
            &state,
            Recipe {
                custom_model_path: model2.to_string_lossy().into_owned(),
                ..recipe_named("r-model2")
            },
        )
        .unwrap();
    }

    // Restart: new AppState from disk must inherit persisted grants.
    let restarted = AppState::from_paths(paths.clone()).unwrap();

    assert!(restarted.grants.check_media_input(&photo1).is_ok());
    assert!(restarted.grants.check_media_input(&photo2).is_ok());
    assert!(restarted.grants.check_output_folder(&out_dir1).is_ok());
    assert!(restarted.grants.check_model_file(&model1).is_ok());
    assert!(restarted.grants.check_model_file(&model2).is_ok());

    // Removing photo1 drops photo1's grant while keeping photo2.
    let photo1_id = lock(&restarted.database)
        .unwrap()
        .list_media()
        .unwrap()
        .into_iter()
        .find(|m| m.path == fs::canonicalize(&photo1).unwrap().to_string_lossy())
        .unwrap()
        .id;
    remove_media_checked(&restarted, &photo1_id).unwrap();
    assert!(restarted.grants.check_media_input(&photo1).is_err());
    assert!(restarted.grants.check_media_input(&photo2).is_ok());

    // Changing output_directory and clearing custom_model_path drops old grants.
    restarted.grants.grant_output_folder(&out_dir2);
    save_settings_checked(
        &restarted,
        UiSettings {
            output_directory: out_dir2.to_string_lossy().into_owned(),
            custom_model_path: String::new(),
            ..UiSettings::default()
        },
    )
    .unwrap();
    assert!(restarted.grants.check_output_folder(&out_dir1).is_err());
    assert!(restarted.grants.check_output_folder(&out_dir2).is_ok());
    assert!(restarted.grants.check_model_file(&model1).is_err());
    // model2 is still referenced by the recipe.
    assert!(restarted.grants.check_model_file(&model2).is_ok());

    // Deleting the recipe drops model2's grant.
    delete_recipe_checked(&restarted, "r-model2").unwrap();
    assert!(restarted.grants.check_model_file(&model2).is_err());

    // clear_media drops photo2's grant.
    clear_media_checked(&restarted).unwrap();
    assert!(restarted.grants.check_media_input(&photo2).is_err());

    // Restart again: none of the removed items come back.
    drop(restarted);
    let after_removal = AppState::from_paths(paths).unwrap();
    assert!(after_removal.grants.check_media_input(&photo1).is_err());
    assert!(after_removal.grants.check_media_input(&photo2).is_err());
    assert!(after_removal.grants.check_output_folder(&out_dir1).is_err());
    assert!(after_removal.grants.check_model_file(&model1).is_err());
    assert!(after_removal.grants.check_model_file(&model2).is_err());
    assert!(after_removal.grants.check_output_folder(&out_dir2).is_ok());
}

#[test]
fn host_flows_for_drop_launch_folder_import_and_result_reuse_work_end_to_end() {
    let dir = tempfile::tempdir().unwrap();
    let dir_root = std::fs::canonicalize(dir.path()).unwrap();
    let paths = AppPaths::under(&dir_root.as_path().join("app"));
    let state = AppState::from_paths(paths).unwrap();

    // 1. Drag and drop a file and a folder.
    let dropped_file = dir_root.as_path().join("dropped.png");
    let dropped_folder = dir_root.as_path().join("dropped_folder");
    fs::create_dir_all(&dropped_folder).unwrap();
    let inside_dropped = dropped_folder.join("clip.mp4");
    fs::write(&dropped_file, b"img").unwrap();
    fs::write(&inside_dropped, b"vid").unwrap();
    state
        .grants
        .grant_dropped_media(&[dropped_file.clone(), dropped_folder.clone()]);
    add_media_checked(
        &state,
        vec![
            dropped_file.to_string_lossy().into_owned(),
            dropped_folder.to_string_lossy().into_owned(),
        ],
        false,
    )
    .unwrap();

    // 2. Open With / launch intent files.
    let opened_file = dir_root.as_path().join("opened.jpg");
    fs::write(&opened_file, b"jpg").unwrap();
    state
        .grants
        .grant_launch_files(&[opened_file.to_string_lossy().into_owned()]);
    add_media_checked(
        &state,
        vec![opened_file.to_string_lossy().into_owned()],
        false,
    )
    .unwrap();

    // 3. Add Folder flow (scan + add + LocalSR Results output folder).
    let scan_dir = dir_root.as_path().join("album");
    fs::create_dir_all(&scan_dir).unwrap();
    let album_photo = scan_dir.join("a.png");
    fs::write(&album_photo, b"album").unwrap();
    state.grants.grant_media_folder(&scan_dir);
    state
        .grants
        .grant_output_folder(&scan_dir.join("LocalSR Results"));
    let scanned = scan_media_folder_checked(&state, &scan_dir.to_string_lossy()).unwrap();
    assert_eq!(scanned.len(), 1);
    add_media_checked(&state, scanned, false).unwrap();
    save_settings_checked(
        &state,
        UiSettings {
            output_directory: scan_dir
                .join("LocalSR Results")
                .to_string_lossy()
                .into_owned(),
            ..UiSettings::default()
        },
    )
    .unwrap();
    assert!(
        prepare_output_directory(&state, &scan_dir.join("LocalSR Results").to_string_lossy(),)
            .is_ok()
    );

    // 4. App-owned completed output: use_result_as_input grants it and persists across restart.
    let output_file = scan_dir.join("LocalSR Results").join("a_upscaled_4x.png");
    fs::write(&output_file, b"upscaled").unwrap();
    let media_id = lock(&state.database)
        .unwrap()
        .list_media()
        .unwrap()
        .into_iter()
        .find(|m| m.path == fs::canonicalize(&album_photo).unwrap().to_string_lossy())
        .unwrap()
        .id;
    {
        let db = lock(&state.database).unwrap();
        db.insert_job("job-done", &media_id, "{}").unwrap();
        db.finish_job("job-done", "completed", &output_file.to_string_lossy(), "")
            .unwrap();
    }
    let (new_input_id, _) = add_result_as_input(&state, &output_file.to_string_lossy()).unwrap();
    assert!(!new_input_id.is_empty());
    assert!(state.grants.check_media_input(&output_file).is_ok());

    // 5. Compare works for granted media and completed outputs.
    let mut runtime = lock(&state.runtime).unwrap();
    let database = lock(&state.database).unwrap();
    assert!(crate::compare::plan(
        &database,
        &mut runtime.compare,
        &[
            crate::compare::ComparePick::Original {
                media_id: media_id.clone(),
            },
            crate::compare::ComparePick::Output {
                media_id,
                job_id: "job-done".into(),
            },
        ],
    )
    .is_ok());
}

#[cfg(unix)]
#[test]
fn compare_refuses_an_original_that_is_ungranted_or_became_a_link() {
    let directory = tempfile::tempdir().unwrap();
    let root = fs::canonicalize(directory.path()).unwrap();
    let state = AppState::from_paths(AppPaths::under(&root.join("app"))).unwrap();
    let photo = root.join("photo.png");
    let stranger = root.join("stranger.png");
    let private = root.join("private.png");
    for file in [&photo, &stranger, &private] {
        fs::write(file, b"png").unwrap();
    }
    state.grants.grant_media_file(&photo);
    *lock(&state.engine).unwrap() = Some(crate::types::EngineInfo {
        protocol_version: 1,
        minimum_protocol_version: 1,
        engine_id: "test".into(),
        engine_version: "1".into(),
        features: vec!["compare_regions".into()],
        model_formats: vec![],
        video_engines: vec![],
    });
    {
        let database = lock(&state.database).unwrap();
        database
            .insert_media("m", photo.to_str().unwrap(), "photo.png", "image")
            .unwrap();
        database
            .insert_media("s", stranger.to_str().unwrap(), "stranger.png", "image")
            .unwrap();
    }
    let original = |id: &str| crate::compare::ComparePick::Original {
        media_id: id.into(),
    };

    // Never granted: refused before anything is planned or sent.
    let error = request_compare_checked(&state, &[original("s")]).unwrap_err();
    assert!(error.to_string().contains("dialog"), "{error}");
    assert!(lock(&state.runtime).unwrap().compare.slots.is_empty());

    // Granted, then swapped for a link to a file nobody granted.
    request_compare_checked(&state, &[original("m")]).unwrap();
    let generation = lock(&state.runtime).unwrap().compare.generation;
    fs::remove_file(&photo).unwrap();
    std::os::unix::fs::symlink(&private, &photo).unwrap();
    assert!(request_compare_regions_checked(&state, generation, &[None]).is_err());
    assert!(request_compare_checked(&state, &[original("m")]).is_err());
    assert_eq!(lock(&state.runtime).unwrap().compare.generation, generation);
}

#[cfg(unix)]
#[test]
fn a_result_below_a_folder_that_became_a_link_is_refused() {
    let directory = tempfile::tempdir().unwrap();
    let root = fs::canonicalize(directory.path()).unwrap();
    let state = AppState::from_paths(AppPaths::under(&root.join("app"))).unwrap();
    let app = tauri::test::mock_app();
    let state = Arc::new(state);
    app.manage(state.clone());
    let folder = root.join("out");
    let output = folder.join("result.png");
    fs::create_dir_all(&folder).unwrap();
    fs::write(&output, b"result").unwrap();
    let recorded = output.to_string_lossy().into_owned();
    {
        let database = lock(&state.database).unwrap();
        database
            .insert_media("m", "/in/photo.png", "photo.png", "image")
            .unwrap();
        database.insert_job("j", "m", "{}").unwrap();
        database
            .finish_job("j", "completed", &recorded, "")
            .unwrap();
    }
    lock(&state.runtime).unwrap().last_output_path = recorded.clone();
    assert!(authorized_result(&state, &recorded).is_ok());

    // The folder is moved away and a link to a private folder takes its place.
    let elsewhere = root.join("private");
    fs::create_dir_all(&elsewhere).unwrap();
    fs::write(elsewhere.join("result.png"), b"private").unwrap();
    fs::rename(&folder, root.join("moved")).unwrap();
    std::os::unix::fs::symlink(&elsewhere, &folder).unwrap();

    assert!(open_result(app.state(), recorded.clone()).is_err());
    assert!(reveal_result(app.state(), recorded.clone()).is_err());
    assert!(add_result_as_input(&state, &recorded).is_err());
    assert!(probe_path_checked(&state, &recorded).is_err());
    assert_eq!(
        lock(&state.database).unwrap().list_media().unwrap().len(),
        1
    );
    assert!(state
        .grants
        .check_media_input(&elsewhere.join("result.png"))
        .is_err());
}

#[cfg(unix)]
#[test]
fn a_result_that_became_a_second_name_for_another_file_is_refused() {
    let directory = tempfile::tempdir().unwrap();
    let (state, output) = state_with_result(directory.path(), "result.png", b"png");
    let private = fs::canonicalize(directory.path())
        .unwrap()
        .join("private.png");
    fs::write(&private, b"private").unwrap();
    fs::remove_file(&output).unwrap();
    fs::hard_link(&private, &output).unwrap();
    let recorded = output.to_string_lossy().into_owned();
    lock(&state.runtime).unwrap().last_output_path = recorded.clone();
    assert!(add_result_as_input(&state, &recorded).is_err());
    assert!(probe_path_checked(&state, &recorded).is_err());
}

#[cfg(unix)]
#[test]
fn the_target_of_a_last_output_that_became_a_link_is_not_a_result() {
    let directory = tempfile::tempdir().unwrap();
    let root = fs::canonicalize(directory.path()).unwrap();
    let state = Arc::new(AppState::from_paths(AppPaths::under(&root.join("app"))).unwrap());
    let app = tauri::test::mock_app();
    app.manage(state.clone());
    let private = root.join("private.png");
    let output = root.join("result.png");
    fs::write(&private, b"private").unwrap();
    std::os::unix::fs::symlink(&private, &output).unwrap();
    lock(&state.runtime).unwrap().last_output_path = output.to_string_lossy().into_owned();
    let target = private.to_string_lossy().into_owned();

    assert!(open_result(app.state(), target.clone()).is_err());
    assert!(reveal_result(app.state(), target.clone()).is_err());
    assert!(add_result_as_input(&state, &target).is_err());
    assert!(probe_path_checked(&state, &target).is_err());
    assert!(lock(&state.database)
        .unwrap()
        .list_media()
        .unwrap()
        .is_empty());
    assert!(state.grants.check_media_input(&private).is_err());
}

#[cfg(unix)]
#[test]
fn custom_edit_model_requires_grants_checks_sd_cli_and_persists_paths_in_place() {
    use std::os::unix::fs::PermissionsExt;

    let directory = tempfile::tempdir().unwrap();
    let root = fs::canonicalize(directory.path()).unwrap();
    let paths = AppPaths::under(&root.join("app"));
    let state = AppState::from_paths(paths.clone()).unwrap();

    let diff = root.join("custom-diff.gguf");
    let te = root.join("custom-te.gguf");
    let vae = root.join("custom-vae.safetensors");
    for file in [&diff, &te, &vae] {
        fs::write(file, b"gguf").unwrap();
    }
    let sd_cli = root.join("sd-cli");
    fs::write(
        &sd_cli,
        "#!/bin/sh\necho '[INFO] Version: Flux.2 klein '\necho '[VERBOSE] model metadata validated'\n",
    )
    .unwrap();
    let mut perms = fs::metadata(&sd_cli).unwrap().permissions();
    perms.set_mode(0o755);
    fs::set_permissions(&sd_cli, perms).unwrap();
    *lock(&state.edit_runtime).unwrap() = sd_cli.to_string_lossy().into_owned();

    let custom = crate::types::CustomEditModel {
        family: "flux2-klein".into(),
        diffusion_path: diff.to_string_lossy().into_owned(),
        text_encoder_path: te.to_string_lossy().into_owned(),
        vision_path: String::new(),
        vae_path: vae.to_string_lossy().into_owned(),
    };

    // Un-granted files cannot be saved or persisted.
    assert!(save_custom_edit_model_checked(&state, custom.clone(), "").is_err());

    state.grants.grant_model_file(&diff);
    state.grants.grant_model_file(&te);
    state.grants.grant_model_file(&vae);
    save_custom_edit_model_checked(&state, custom.clone(), "").unwrap();

    let snap = state.snapshot().unwrap();
    assert_eq!(snap.settings.edit_model_id, "__custom__");
    assert_eq!(snap.settings.custom_edit_model, custom);
    assert_eq!(
        snap.edit.custom_model.as_ref().unwrap().family,
        "flux2-klein"
    );

    // Persisted custom edit files remain granted after reload and can queue an edit job.
    let reloaded = AppState::from_paths(paths).unwrap();
    *lock(&reloaded.edit_runtime).unwrap() = sd_cli.to_string_lossy().into_owned();
    lock(&reloaded.runtime).unwrap().worker = "ready".into();
    *lock(&reloaded.engine).unwrap() = Some(crate::types::EngineInfo {
        protocol_version: 1,
        minimum_protocol_version: 1,
        engine_id: "test".into(),
        engine_version: "1".into(),
        features: vec!["image_edit".into()],
        model_formats: vec![],
        video_engines: vec![],
    });
    lock(&reloaded.capabilities)
        .unwrap()
        .devices
        .push(crate::types::DeviceInfo {
            id: "mps".into(),
            device_type: "mps".into(),
            name: "Apple GPU".into(),
            ..Default::default()
        });
    let photo = root.join("photo.png");
    fs::write(&photo, b"png").unwrap();
    reloaded.grants.grant_media_file(&photo);
    {
        let db = lock(&reloaded.database).unwrap();
        db.insert_media("m1", photo.to_str().unwrap(), "photo.png", "image")
            .unwrap();
        db.update_media_probe(
            photo.to_str().unwrap(),
            "image",
            512,
            512,
            1,
            0.0,
            0.0,
            "",
            "",
            "",
            false,
            "",
        )
        .unwrap();
    }
    start_edit_checked(
        &reloaded,
        crate::editing::StartEditInput {
            media_id: "m1".into(),
            model_id: "__custom__".into(),
            prompt: "Add snow".into(),
            runtime_path: String::new(),
            device: "mps".into(),
            max_dimension: 512,
            steps: 4,
            seed: 42,
            accepted_terms: false,
            output_directory: String::new(),
            edit_text_encoder: "auto".into(),
            edit_vae: "auto".into(),
            edit_vram_limit_mb: None,
            edit_allow_any_size: None,
        },
    )
    .unwrap();
}

#[test]
fn edit_placement_overrides_are_gated_by_engine_feature() {
    let dir = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(&dir.path().join("app"));
    let state = AppState::from_paths(paths).unwrap();
    let edit_model = crate::editing::catalog_in(&state.model_root())
        .unwrap()
        .into_iter()
        .find(|m| m.model_id == "flux2_klein_4b_q4_0")
        .unwrap();
    let bundle_dir = state.paths.model_root.join(&edit_model.family);
    fs::create_dir_all(&bundle_dir).unwrap();
    for file in &edit_model.files {
        fs::File::create(bundle_dir.join(&file.filename))
            .unwrap()
            .set_len(file.size_bytes)
            .unwrap();
    }
    let sd_cli = dir.path().join("sd-cli");
    fs::write(&sd_cli, b"rt").unwrap();
    *lock(&state.edit_runtime).unwrap() = sd_cli.to_string_lossy().into_owned();
    lock(&state.runtime).unwrap().worker = "ready".into();
    *lock(&state.engine).unwrap() = Some(crate::types::EngineInfo {
        protocol_version: 1,
        minimum_protocol_version: 1,
        engine_id: "old".into(),
        engine_version: "0.6.2".into(),
        features: vec!["image_edit".into()],
        model_formats: vec![],
        video_engines: vec![],
    });
    lock(&state.capabilities)
        .unwrap()
        .devices
        .push(crate::types::DeviceInfo {
            id: "cuda:0".into(),
            device_type: "cuda".into(),
            name: "RTX".into(),
            ..Default::default()
        });
    let photo = dir.path().join("photo.png");
    fs::write(&photo, b"png").unwrap();
    state.grants.grant_media_file(&photo);
    {
        let db = lock(&state.database).unwrap();
        db.insert_media("m1", photo.to_str().unwrap(), "photo.png", "image")
            .unwrap();
        db.update_media_probe(
            photo.to_str().unwrap(),
            "image",
            512,
            512,
            1,
            0.0,
            0.0,
            "",
            "",
            "",
            false,
            "",
        )
        .unwrap();
    }

    let make_input = |te: &str, vae: &str| crate::editing::StartEditInput {
        media_id: "m1".into(),
        model_id: edit_model.model_id.clone(),
        prompt: "Add snow".into(),
        runtime_path: String::new(),
        device: "cuda:0".into(),
        max_dimension: 512,
        steps: 4,
        seed: 42,
        accepted_terms: false,
        output_directory: String::new(),
        edit_text_encoder: te.into(),
        edit_vae: vae.into(),
        edit_vram_limit_mb: None,
        edit_allow_any_size: None,
    };

    // Old engine without edit_placement: auto succeeds and omits placement fields from the wire.
    start_edit_checked(&state, make_input("auto", "auto")).unwrap();
    let queued = lock(&state.database)
        .unwrap()
        .next_queued_job()
        .unwrap()
        .unwrap();
    let wire: Value = serde_json::from_str(&queued.request_json).unwrap();
    assert!(wire["data"].get("edit_text_encoder").is_none());
    assert!(wire["data"].get("edit_vae").is_none());
    lock(&state.database)
        .unwrap()
        .finish_job(&queued.id, "completed", "", "")
        .unwrap();

    // Old engine refuses explicit overrides.
    assert!(start_edit_checked(&state, make_input("cpu", "auto")).is_err());
    assert!(start_edit_checked(&state, make_input("auto", "gpu")).is_err());

    // Engine with edit_placement accepts overrides and serializes them.
    lock(&state.engine)
        .unwrap()
        .as_mut()
        .unwrap()
        .features
        .push("edit_placement".into());
    start_edit_checked(&state, make_input("cpu", "gpu")).unwrap();
    let next = lock(&state.database)
        .unwrap()
        .next_queued_job()
        .unwrap()
        .unwrap();
    let latest: Value = serde_json::from_str(&next.request_json).unwrap();
    assert_eq!(latest["data"]["edit_text_encoder"], "cpu");
    assert_eq!(latest["data"]["edit_vae"], "gpu");
}

#[test]
fn edit_expert_settings_validate_and_flow_from_settings_to_request() {
    let dir = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(&dir.path().join("app"));
    let state = AppState::from_paths(paths).unwrap();
    let edit_model = crate::editing::catalog_in(&state.model_root())
        .unwrap()
        .into_iter()
        .find(|m| m.model_id == "flux2_klein_4b_q4_0")
        .unwrap();
    let bundle_dir = state.paths.model_root.join(&edit_model.family);
    fs::create_dir_all(&bundle_dir).unwrap();
    for file in &edit_model.files {
        fs::File::create(bundle_dir.join(&file.filename))
            .unwrap()
            .set_len(file.size_bytes)
            .unwrap();
    }
    let sd_cli = dir.path().join("sd-cli");
    fs::write(&sd_cli, b"rt").unwrap();
    *lock(&state.edit_runtime).unwrap() = sd_cli.to_string_lossy().into_owned();
    lock(&state.runtime).unwrap().worker = "ready".into();
    *lock(&state.engine).unwrap() = Some(crate::types::EngineInfo {
        protocol_version: 1,
        minimum_protocol_version: 1,
        engine_id: "old".into(),
        engine_version: "0.6.2".into(),
        features: vec!["image_edit".into(), "edit_placement".into()],
        model_formats: vec![],
        video_engines: vec![],
    });
    lock(&state.capabilities)
        .unwrap()
        .devices
        .push(crate::types::DeviceInfo {
            id: "cuda:0".into(),
            device_type: "cuda".into(),
            name: "RTX 3060".into(),
            total_memory: 12 * 1024 * 1024 * 1024,
            free_memory: 11 * 1024 * 1024 * 1024,
            ..Default::default()
        });
    let photo = dir.path().join("photo.png");
    fs::write(&photo, b"png").unwrap();
    state.grants.grant_media_file(&photo);
    {
        let db = lock(&state.database).unwrap();
        db.insert_media("m1", photo.to_str().unwrap(), "photo.png", "image")
            .unwrap();
        db.update_media_probe(
            photo.to_str().unwrap(),
            "image",
            512,
            512,
            1,
            0.0,
            0.0,
            "",
            "",
            "",
            false,
            "",
        )
        .unwrap();
    }

    // Settings validation rejects non-zero VRAM limits under 1024 MiB and invalid placements.
    let bad_settings = UiSettings {
        edit_vram_limit_mb: 512,
        ..UiSettings::default()
    };
    assert!(validate_settings(&bad_settings).is_err());
    assert!(validate_settings(&UiSettings {
        edit_text_encoder: "tpu".into(),
        ..UiSettings::default()
    })
    .is_err());
    assert!(validate_settings(&UiSettings {
        edit_vae: "tpu".into(),
        ..UiSettings::default()
    })
    .is_err());
    let ok_settings = UiSettings {
        edit_text_encoder: "cpu".into(),
        edit_vae: "gpu".into(),
        edit_vram_limit_mb: 9830,
        edit_allow_any_size: true,
        ..UiSettings::default()
    };
    save_settings_checked(&state, ok_settings).unwrap();

    let base_input = || crate::editing::StartEditInput {
        media_id: "m1".into(),
        model_id: edit_model.model_id.clone(),
        prompt: "Add snow".into(),
        runtime_path: String::new(),
        device: "cuda:0".into(),
        max_dimension: 1024,
        steps: 4,
        seed: 42,
        accepted_terms: false,
        output_directory: String::new(),
        edit_text_encoder: "auto".into(),
        edit_vae: "auto".into(),
        edit_vram_limit_mb: None,
        edit_allow_any_size: None,
    };

    // Old engine without edit_expert refuses non-default settings overrides.
    assert!(start_edit_checked(&state, base_input()).is_err());

    // Old engine succeeds when defaults are active and omits expert fields from the wire.
    save_settings_checked(&state, UiSettings::default()).unwrap();
    start_edit_checked(&state, base_input()).unwrap();
    let queued = lock(&state.database)
        .unwrap()
        .next_queued_job()
        .unwrap()
        .unwrap();
    let default_wire: Value = serde_json::from_str(&queued.request_json).unwrap();
    assert!(default_wire["data"].get("edit_vram_limit_mb").is_none());
    assert!(default_wire["data"].get("edit_allow_any_size").is_none());
    lock(&state.database)
        .unwrap()
        .finish_job(&queued.id, "completed", "", "")
        .unwrap();

    // Engine with edit_expert enforces 1024..total_vram_mb and forwards settings into the request.
    lock(&state.engine)
        .unwrap()
        .as_mut()
        .unwrap()
        .features
        .push("edit_expert".into());
    save_settings_checked(
        &state,
        UiSettings {
            edit_vram_limit_mb: 12 * 1024 + 1,
            edit_allow_any_size: true,
            ..UiSettings::default()
        },
    )
    .unwrap();
    assert!(start_edit_checked(&state, base_input()).is_err());

    save_settings_checked(
        &state,
        UiSettings {
            edit_vram_limit_mb: 9830,
            edit_allow_any_size: true,
            ..UiSettings::default()
        },
    )
    .unwrap();
    start_edit_checked(&state, base_input()).unwrap();
    let next = lock(&state.database)
        .unwrap()
        .next_queued_job()
        .unwrap()
        .unwrap();
    let wire: Value = serde_json::from_str(&next.request_json).unwrap();
    assert_eq!(wire["data"]["edit_vram_limit_mb"], 9830);
    assert_eq!(wire["data"]["edit_allow_any_size"], true);
}

#[test]
fn colour_fix_is_resolved_for_photos_and_video() {
    use crate::types::FixStageSelection;

    let directory = tempfile::tempdir().unwrap();
    let paths = crate::paths::AppPaths::under(directory.path());
    fs::create_dir_all(&paths.next_root).unwrap();
    fs::create_dir_all(&paths.model_root).unwrap();
    let state = AppState::from_paths(paths).unwrap();
    {
        let mut catalog = lock(&state.catalog).unwrap();
        for model in &mut catalog.models {
            model.installed = true;
            model.installed_path = Some(format!("/models/{}", model.filename));
        }
    }

    let mut photo_upscale = input("upscale");
    photo_upscale.model_id = "span_photo_x4".into();
    photo_upscale.fix_stages = vec![FixStageSelection {
        fix: "colour".into(),
        model_id: "ddcolor_modelscope".into(),
        ..Default::default()
    }];
    match resolve_model_selection(&state, &photo_upscale).unwrap() {
        ModelSelection::Image { preprocess, .. } => {
            assert_eq!(preprocess.len(), 1);
            assert_eq!(preprocess[0].fix, "colour");
            assert_eq!(preprocess[0].model_id, "ddcolor_modelscope");
        }
        _ => panic!("expected photo upscale plan with colour fix"),
    }

    let mut photo_restore = input("denoise");
    photo_restore.output_scale = 1;
    photo_restore.model_id = "ddcolor_modelscope".into();
    photo_restore.fix_stages = vec![FixStageSelection {
        fix: "colour".into(),
        model_id: "ddcolor_modelscope".into(),
        ..Default::default()
    }];
    match resolve_model_selection(&state, &photo_restore).unwrap() {
        ModelSelection::Image {
            model_id,
            preprocess,
            ..
        } => {
            assert_eq!(model_id, "ddcolor_modelscope");
            assert!(preprocess.is_empty());
        }
        _ => panic!("expected photo restore plan with ddcolor"),
    }

    let mut video_job = input("video");
    video_job.model_id = "span_photo_x4".into();
    video_job.video_model_id = "frame_by_frame".into();
    video_job.fix_stages = vec![FixStageSelection {
        fix: "colour".into(),
        model_id: "ddcolor_modelscope".into(),
        ..Default::default()
    }];
    match resolve_model_selection(&state, &video_job).unwrap() {
        ModelSelection::Image { preprocess, .. } => {
            assert_eq!(preprocess.len(), 1);
            assert_eq!(preprocess[0].fix, "colour");
        }
        _ => panic!("expected video plan with colour fix"),
    }
    video_job.video_task = "denoise".into();
    video_job.output_scale = 1;
    video_job.model_id = "ddcolor_modelscope".into();
    match resolve_model_selection(&state, &video_job).unwrap() {
        ModelSelection::Image {
            model_id,
            preprocess,
            ..
        } => {
            assert_eq!(model_id, "ddcolor_modelscope");
            assert!(preprocess.is_empty());
        }
        _ => panic!("expected video restore with colour"),
    }
    let recipe: Recipe = serde_json::from_value(json!({
        "id": "colour-video", "name": "Colour video", "task": "video",
        "video_task": "denoise", "video_model_id": "frame_by_frame",
        "model_id": "ddcolor_modelscope", "output_scale": 1,
        "tile_size": 128, "halo": 16, "precision": "fp32", "safe_memory": true,
        "stages": [{"kind":"restore", "model_id":"ddcolor_modelscope", "fix":"colour"}]
    }))
    .unwrap();
    assert!(validate_recipe_stages(&recipe).is_ok());
}

#[test]
fn custom_checkpoint_consent_is_persisted_by_digest_and_changes_require_new_consent() {
    let dir = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(dir.path());
    let checkpoint = dir.path().join("my_model.pth");
    fs::write(&checkpoint, b"first").unwrap();
    let state = AppState::from_paths(paths.clone()).unwrap();
    state.grants.grant_model_file(&checkpoint);
    assert!(!custom_checkpoint_trusted(&state, &checkpoint).unwrap());
    set_custom_checkpoint_consent(&state, &checkpoint, true).unwrap();
    assert!(custom_checkpoint_trusted(&state, &checkpoint).unwrap());
    let state = AppState::from_paths(paths).unwrap();
    state.grants.grant_model_file(&checkpoint);
    assert!(custom_checkpoint_trusted(&state, &checkpoint).unwrap());
    fs::write(&checkpoint, b"changed").unwrap();
    assert!(!custom_checkpoint_trusted(&state, &checkpoint).unwrap());
}

#[test]
fn custom_checkpoint_job_requires_matching_approval_and_safe_format_does_not() {
    let dir = tempfile::tempdir().unwrap();
    let state = AppState::from_paths(AppPaths::under(dir.path())).unwrap();
    let checkpoint = dir.path().join("custom.ckpt");
    fs::write(&checkpoint, b"first").unwrap();
    state.grants.grant_model_file(&checkpoint);
    let mut request = input("upscale");
    request.model_id = "__custom__".into();
    request.custom_model_path = checkpoint.to_string_lossy().into_owned();
    request.allow_unsafe_pickle_model = true;
    assert!(resolve_model_selection(&state, &request).is_err());
    set_custom_checkpoint_consent(&state, &checkpoint, true).unwrap();
    let selected = resolve_model_selection(&state, &request).unwrap();
    match selected {
        ModelSelection::Image {
            approved_checkpoint_sha256,
            ..
        } => {
            assert_eq!(
                approved_checkpoint_sha256,
                Some(catalog::sha256_file(&checkpoint).unwrap())
            );
        }
        _ => panic!("expected image model"),
    }
    request.allow_unsafe_pickle_model = false;
    assert!(resolve_model_selection(&state, &request).is_err());
    request.allow_unsafe_pickle_model = true;
    fs::write(&checkpoint, b"changed").unwrap();
    assert!(resolve_model_selection(&state, &request).is_err());
    let safe = dir.path().join("safe.safetensors");
    fs::write(&safe, b"fixture").unwrap();
    state.grants.grant_model_file(&safe);
    request.custom_model_path = safe.to_string_lossy().into_owned();
    request.allow_unsafe_pickle_model = false;
    assert!(resolve_model_selection(&state, &request).is_ok());
}

#[test]
fn saved_custom_model_names_and_grants_survive_restart() {
    let dir = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(dir.path());
    let file = dir.path().join("my_model.pth");
    fs::write(&file, b"fixture").unwrap();
    let state = AppState::from_paths(paths.clone()).unwrap();
    state.grants.grant_model_file(&file);
    let mut settings = lock(&state.settings).unwrap().clone();
    settings.custom_models.push(crate::types::CustomModel {
        path: file.to_string_lossy().into_owned(),
        name: "My model".into(),
    });
    save_settings_checked(&state, settings).unwrap();
    let restarted = AppState::from_paths(paths).unwrap();
    assert_eq!(
        lock(&restarted.settings).unwrap().custom_models[0].name,
        "My model"
    );
    assert!(restarted.grants.check_model_file(&file).is_ok());
}

#[test]
fn remembered_pickle_consent_requires_an_engine_that_checks_the_digest() {
    let selection = ModelSelection::Image {
        model_id: "__custom__".into(),
        path: "/models/checkpoint.pth".into(),
        approved_checkpoint_sha256: Some("a".repeat(64)),
        preprocess: vec![],
        step_models: Vec::new(),
        face_path: None,
        face_model_id: String::new(),
        native_scale: 4,
        skip_upscale: false,
        interpolation: None,
        audio_denoise: None,
    };
    let mut engine = crate::types::EngineInfo {
        protocol_version: 1,
        minimum_protocol_version: 1,
        engine_id: "test".into(),
        engine_version: "old".into(),
        features: vec!["image".into()],
        model_formats: vec![],
        video_engines: vec![],
    };
    assert!(validate_checkpoint_engine(&selection, Some(&engine)).is_err());
    engine.features.push("checkpoint_sha256_consent".into());
    assert!(validate_checkpoint_engine(&selection, Some(&engine)).is_ok());
}

#[test]
fn retry_job_on_cpu_queues_only_the_failed_job_on_cpu_without_changing_device_id() {
    let dir = tempfile::tempdir().unwrap();
    let photo = dir.path().join("photo.png");
    let out_dir = dir.path().join("out");
    fs::write(&photo, b"fixture").unwrap();
    fs::create_dir_all(&out_dir).unwrap();

    let paths = AppPaths::under(&dir.path().join("app"));
    let state = AppState::from_paths(paths).unwrap();
    {
        let mut settings = lock(&state.settings).unwrap();
        settings.device_id = "mps".into();
        settings.neural_engine = true;
        settings.output_directory = out_dir.to_string_lossy().into_owned();
        settings.output_scale = 2;
    }
    state.grants.grant_media_file(&photo);
    state.grants.grant_output_folder(&out_dir);
    {
        let mut caps = lock(&state.capabilities).unwrap();
        caps.system_ram_total = 16 * 1024 * 1024 * 1024;
        caps.devices = vec![
            crate::types::DeviceInfo {
                id: "mps".into(),
                device_type: "mps".into(),
                name: "Apple GPU".into(),
                total_memory: 8 * 1024 * 1024 * 1024,
                free_memory: 4 * 1024 * 1024 * 1024,
                supports_fp16: true,
                is_integrated: true,
                recommended_tile_sizes: vec![128, 256],
                neural_engine: true,
            },
            crate::types::DeviceInfo {
                id: "cpu".into(),
                device_type: "cpu".into(),
                name: "CPU".into(),
                total_memory: 16 * 1024 * 1024 * 1024,
                free_memory: 8 * 1024 * 1024 * 1024,
                supports_fp16: false,
                is_integrated: false,
                recommended_tile_sizes: vec![128, 256],
                neural_engine: false,
            },
        ];
    }

    let media_id = Uuid::new_v4().to_string();
    let failed_job_id = Uuid::new_v4().to_string();
    let out_file = out_dir.join("photo_upscaled_2x.png");
    let request_json = json!({
        "type": "job_request",
        "data": {
            "job_id": failed_job_id,
            "image_path": photo.to_string_lossy(),
            "model_path": "/models/span.safetensors",
            "output_path": out_file.to_string_lossy(),
            "output_format": "png",
            "device": "mps",
            "neural_engine": true,
            "output_scale": 2
        }
    })
    .to_string();
    {
        let db = lock(&state.database).unwrap();
        let photo_str = photo.to_str().unwrap();
        db.insert_media(&media_id, photo_str, "photo.png", "image")
            .unwrap();
        db.update_media_probe(
            photo_str, "image", 64, 64, 1, 0.0, 0.0, "preview", "", "", false, "",
        )
        .unwrap();
        db.insert_job(&failed_job_id, &media_id, &request_json)
            .unwrap();
        db.finish_job(&failed_job_id, "failed", "", "GPU driver reset")
            .unwrap();
    }

    retry_job_on_cpu_checked(&state, &failed_job_id).unwrap();

    // App-wide device_id and neural_engine stay unchanged.
    {
        let settings = lock(&state.settings).unwrap();
        assert_eq!(settings.device_id, "mps");
        assert!(settings.neural_engine);
    }

    let queued = lock(&state.database)
        .unwrap()
        .next_queued_job()
        .unwrap()
        .expect("retried job should be queued");
    assert_ne!(queued.id, failed_job_id);
    let queued_req: Value = serde_json::from_str(&queued.request_json).unwrap();
    assert_eq!(queued_req["data"]["job_id"], queued.id);
    assert_eq!(queued_req["data"]["device"], "cpu");
    assert_eq!(queued_req["data"]["neural_engine"], false);
    assert_eq!(queued_req["data"]["output_scale"], 2);
    assert_eq!(
        queued_req["data"]["output_path"],
        out_file.to_string_lossy().as_ref()
    );

    // Retrying a job that is not failed is rejected.
    let err = retry_job_on_cpu_checked(&state, &queued.id).unwrap_err();
    assert!(err.to_string().contains("failed"));
}

#[test]
fn cancel_jobs_and_cancel_jobs_for_media_forward_keep_partial_and_cancel_queued() {
    use std::sync::mpsc;

    for keep_partial in [false, true] {
        let directory = tempfile::tempdir().unwrap();
        let state = AppState::from_paths(AppPaths::under(directory.path())).unwrap();
        let (sender, receiver) = mpsc::channel();
        *lock(&state.worker.sender).unwrap() = Some(sender);

        {
            let db = lock(&state.database).unwrap();
            db.insert_media("m1", "/in/clip1.mp4", "clip1.mp4", "video")
                .unwrap();
            db.insert_media("m2", "/in/clip2.mp4", "clip2.mp4", "video")
                .unwrap();
            db.insert_job(
                "j1",
                "m1",
                &json!({"type": "video_job_request", "data": {"job_id": "j1"}}).to_string(),
            )
            .unwrap();
            db.set_job_status("j1", "running").unwrap();
            db.insert_job(
                "j2",
                "m2",
                &json!({"type": "video_job_request", "data": {"job_id": "j2"}}).to_string(),
            )
            .unwrap();
        }
        lock(&state.runtime).unwrap().active_job_id = "j1".into();
        lock(&state.worker.cancellation).unwrap().job_ids = vec!["j1".into()];

        cancel_jobs_checked(&state, keep_partial).unwrap();

        let sent: Value = serde_json::from_str(&receiver.try_recv().unwrap()).unwrap();
        assert_eq!(sent["type"], "cancel_request");
        assert_eq!(sent["data"]["job_id"], "j1");
        assert_eq!(sent["data"]["keep_partial"], keep_partial);

        let jobs = lock(&state.database).unwrap().list_jobs().unwrap();
        assert_eq!(
            jobs.iter().find(|j| j.id == "j1").unwrap().status,
            "cancelling"
        );
        assert_eq!(
            jobs.iter().find(|j| j.id == "j2").unwrap().status,
            "cancelled"
        );
    }

    for keep_partial in [false, true] {
        let directory = tempfile::tempdir().unwrap();
        let state = AppState::from_paths(AppPaths::under(directory.path())).unwrap();
        let (sender, receiver) = mpsc::channel();
        *lock(&state.worker.sender).unwrap() = Some(sender);

        {
            let db = lock(&state.database).unwrap();
            db.insert_media("m1", "/in/clip1.mp4", "clip1.mp4", "video")
                .unwrap();
            db.insert_media("m2", "/in/clip2.mp4", "clip2.mp4", "video")
                .unwrap();
            db.insert_job(
                "j1",
                "m1",
                &json!({"type": "video_job_request", "data": {"job_id": "j1"}}).to_string(),
            )
            .unwrap();
            db.set_job_status("j1", "running").unwrap();
            db.insert_job(
                "j1-q",
                "m1",
                &json!({"type": "video_job_request", "data": {"job_id": "j1-q"}}).to_string(),
            )
            .unwrap();
            db.insert_job(
                "j2",
                "m2",
                &json!({"type": "video_job_request", "data": {"job_id": "j2"}}).to_string(),
            )
            .unwrap();
        }
        lock(&state.runtime).unwrap().active_job_id = "j1".into();

        cancel_jobs_for_media_checked(&state, "m1", keep_partial).unwrap();

        let sent: Value = serde_json::from_str(&receiver.try_recv().unwrap()).unwrap();
        assert_eq!(sent["type"], "cancel_request");
        assert_eq!(sent["data"]["job_id"], "j1");
        assert_eq!(sent["data"]["keep_partial"], keep_partial);

        let jobs = lock(&state.database).unwrap().list_jobs().unwrap();
        assert_eq!(
            jobs.iter().find(|j| j.id == "j1").unwrap().status,
            "cancelling"
        );
        assert_eq!(
            jobs.iter().find(|j| j.id == "j1-q").unwrap().status,
            "cancelled"
        );
        assert_eq!(jobs.iter().find(|j| j.id == "j2").unwrap().status, "queued");
    }
}

#[test]
fn retry_on_cpu_rechecks_the_approval_of_an_own_checkpoint() {
    let dir = tempfile::tempdir().unwrap();
    let checkpoint = dir.path().join("mine.pth");
    fs::write(&checkpoint, b"pickled weights").unwrap();
    let paths = AppPaths::under(&dir.path().join("app"));
    let state = AppState::from_paths(paths).unwrap();
    let digest = catalog::sha256_file(&checkpoint).unwrap();
    let request = |approved: Option<&str>| {
        json!({"type": "job_request", "data": {
            "model_path": checkpoint.to_string_lossy(),
            "allow_unverified_checkpoint": approved.is_some(),
            "approved_checkpoint_sha256": approved,
        }})
    };
    // No own checkpoint in the request: nothing to check.
    let catalog_job =
        json!({"type": "job_request", "data": {"model_path": "/models/span.safetensors"}});
    assert!(check_retry_checkpoint_consent(&state, &catalog_job).is_ok());

    // Not granted any more: refused.
    lock(&state.settings).unwrap().trusted_checkpoint_sha256 = vec![digest.clone()];
    assert!(check_retry_checkpoint_consent(&state, &request(Some(&digest))).is_err());

    state.grants.grant_model_file(&checkpoint);
    assert!(check_retry_checkpoint_consent(&state, &request(Some(&digest))).is_ok());

    // Approval withdrawn: refused.
    lock(&state.settings)
        .unwrap()
        .trusted_checkpoint_sha256
        .clear();
    let error = check_retry_checkpoint_consent(&state, &request(Some(&digest))).unwrap_err();
    assert!(error.to_string().contains("Confirm your own model again"));

    // The file changed since it was approved: refused, even if the new file is trusted.
    fs::write(&checkpoint, b"other weights").unwrap();
    let changed = catalog::sha256_file(&checkpoint).unwrap();
    lock(&state.settings).unwrap().trusted_checkpoint_sha256 = vec![digest.clone(), changed];
    assert!(check_retry_checkpoint_consent(&state, &request(Some(&digest))).is_err());

    // A flag without a digest is never enough.
    let mut flag_only = request(None);
    flag_only["data"]["allow_unverified_checkpoint"] = json!(true);
    assert!(check_retry_checkpoint_consent(&state, &flag_only).is_err());
}

#[test]
fn low_priority_from_advanced_lasts_for_the_session_and_a_new_default_ends_it() {
    let dir = tempfile::tempdir().unwrap();
    let state = AppState::from_paths(AppPaths::under(&dir.path().join("app"))).unwrap();
    assert!(!crate::worker::effective_low_priority(&state));
    set_run_low_priority_checked(&state, true);
    assert!(crate::worker::effective_low_priority(&state));
    // The saved default is untouched.
    assert!(!lock(&state.settings).unwrap().low_priority);
    // Choosing a default in Settings ends the session choice.
    apply_preference(&state, "low_priority", &json!(false)).unwrap();
    assert!(lock(&state.run_low_priority).unwrap().is_none());
    assert!(!crate::worker::effective_low_priority(&state));
}

#[test]
fn benchmark_preset_candidates_pick_quick_and_best_from_catalog() {
    let manifest: crate::types::CatalogManifest =
        serde_json::from_str(include_str!("../../resources/model-catalog.json")).unwrap();
    let picks = benchmark_preset_candidates(&manifest.models);
    assert_eq!(picks.len(), 2);
    assert_eq!(picks[0].0, "quick");
    assert_eq!(picks[0].1.model_id, "localsr_quick_x4_v1");
    assert_eq!(picks[1].0, "best");
    assert_eq!(picks[1].1.model_id, "realplksr_nomoswebphoto_x4");
}

#[test]
fn stage_params_and_masks_are_forwarded_and_gated_by_engine_feature() {
    use crate::types::FixStageSelection;

    let stage_with_params = FixStageSelection {
        fix: "noise".into(),
        model_id: "scunet_color_real_psnr".into(),
        params: Some(serde_json::Map::from_iter([(
            "strength".into(),
            json!(0.5),
        )])),
        mask_path: Some("/tmp/mask.png".into()),
    };
    let old_engine = crate::types::EngineInfo {
        protocol_version: 1,
        minimum_protocol_version: 1,
        engine_id: "test".into(),
        engine_version: "0.6.0".into(),
        features: vec!["image".into(), "image_multi_fix".into()],
        model_formats: vec![],
        video_engines: vec![],
    };
    assert!(validate_stage_params_engine(
        std::slice::from_ref(&stage_with_params),
        Some(&old_engine)
    )
    .is_err());
    assert!(validate_stage_params_engine(std::slice::from_ref(&stage_with_params), None).is_err());

    // Without params or masks, an older engine is still accepted.
    let plain_stage = FixStageSelection {
        fix: "noise".into(),
        model_id: "scunet_color_real_psnr".into(),
        params: None,
        mask_path: None,
    };
    assert!(validate_stage_params_engine(&[plain_stage], Some(&old_engine)).is_ok());

    let mut new_engine = old_engine;
    new_engine.features.push(
        crate::steps::registry()
            .limits()
            .stage_params_feature
            .clone(),
    );
    new_engine.features.push("fix_strength".into());
    assert!(validate_stage_params_engine(
        std::slice::from_ref(&stage_with_params),
        Some(&new_engine)
    )
    .is_ok());

    let resolved = vec![ResolvedImageStage {
        kind: "restore",
        fix: "noise".into(),
        model_id: "scunet_color_real_psnr".into(),
        path: "/models/scunet.pth".into(),
        params: stage_with_params.params,
        mask_path: stage_with_params.mask_path,
    }];
    let serialized = serde_json::to_value(image_pipeline_stages(
        &resolved,
        &[],
        "main",
        "/models/main.pth",
        None,
        "",
        false,
        &input("upscale"),
    ))
    .unwrap();
    let serialized = serialized.as_array().unwrap();
    assert_eq!(serialized[0]["params"], json!({"strength": 0.5}));
    assert_eq!(serialized[0]["mask_path"], "/tmp/mask.png");
    // Primary stage without params/mask_path omits those keys cleanly.
    assert!(serialized[1].get("params").is_none());
    assert!(serialized[1].get("mask_path").is_none());
}

fn strength_stage(strength: Option<f64>) -> crate::types::FixStageSelection {
    crate::types::FixStageSelection {
        fix: "noise".into(),
        model_id: "scunet_color_real_psnr".into(),
        params: strength
            .map(|value| serde_json::Map::from_iter([("strength".to_owned(), json!(value))])),
        mask_path: None,
    }
}

#[test]
fn a_strength_needs_an_engine_that_lists_it() {
    let engine = |features: &[&str]| crate::types::EngineInfo {
        protocol_version: 1,
        minimum_protocol_version: 1,
        engine_id: "test".into(),
        engine_version: "0.7.0".into(),
        features: features.iter().map(|f| (*f).to_owned()).collect(),
        model_formats: vec![],
        video_engines: vec![],
    };
    let params_only = engine(&["image", "image_multi_fix", "stage_params_and_masks"]);
    let with_strength = engine(&[
        "image",
        "image_multi_fix",
        "stage_params_and_masks",
        "fix_strength",
    ]);
    let light = [strength_stage(Some(0.4))];
    let message = validate_stage_params_engine(&light, Some(&params_only))
        .unwrap_err()
        .to_string();
    assert!(
        message.contains("This engine cannot change a fix's strength. Update LocalSR."),
        "{message}"
    );
    assert!(validate_stage_params_engine(&light, None).is_err());
    assert!(validate_stage_params_engine(&light, Some(&with_strength)).is_ok());
    // Without a strength, or at the whole fix, any engine is accepted.
    let older = engine(&["image"]);
    assert!(validate_stage_params_engine(&[strength_stage(None)], Some(&older)).is_ok());
    assert!(validate_stage_params_engine(&[strength_stage(Some(1.0))], Some(&params_only)).is_ok());
}

#[test]
fn a_recipe_keeps_a_fix_strength() {
    let recipe = |strength: serde_json::Value| -> Recipe {
        serde_json::from_value(json!({
            "id": "r", "name": "Light noise", "task": "upscale", "model_id": "model",
            "output_scale": 4, "tile_size": 256, "halo": 32, "precision": "fp32",
            "safe_memory": true,
            "stages": [
                {"kind": "restore", "model_id": "scunet_color_real_psnr", "fix": "noise",
                 "params": {"strength": strength}},
                {"kind": "upscale", "model_id": "model"}
            ],
        }))
        .unwrap()
    };
    let saved = recipe(json!(0.4));
    assert!(validate_recipe_stages(&saved).is_ok());
    let again: Recipe = serde_json::from_str(&serde_json::to_string(&saved).unwrap()).unwrap();
    assert_eq!(
        again.stages[0].params.as_ref().unwrap()["strength"],
        json!(0.4)
    );
    assert!(validate_recipe_stages(&recipe(json!(2))).is_err());
    assert!(validate_recipe_stages(&recipe(json!(-0.1))).is_err());
}

#[test]
fn the_request_carries_the_strength() {
    let resolved = |stage: &crate::types::FixStageSelection| ResolvedImageStage {
        kind: "restore",
        fix: stage.fix.clone(),
        model_id: stage.model_id.clone(),
        path: "/models/scunet.pth".into(),
        params: stage.params.clone(),
        mask_path: None,
    };
    let stages_for = |stage: crate::types::FixStageSelection| {
        let mut denoise = input("denoise");
        denoise.fix_stages = vec![stage.clone()];
        // Restore's own model is the last fix, and is the primary stage as well.
        let mut upscale = input("upscale");
        upscale.fix_stages = vec![stage.clone()];
        let fix = serde_json::to_value(image_pipeline_stages(
            &[resolved(&stage)],
            &[],
            "main",
            "/models/main.pth",
            None,
            "",
            false,
            &upscale,
        ))
        .unwrap();
        let primary = serde_json::to_value(image_pipeline_stages(
            &[],
            &[],
            "scunet_color_real_psnr",
            "/models/scunet.pth",
            None,
            "",
            false,
            &denoise,
        ))
        .unwrap();
        (fix, primary)
    };
    let (fix, primary) = stages_for(strength_stage(Some(0.4)));
    assert_eq!(fix[0]["params"], json!({"strength": 0.4}));
    assert_eq!(primary[0]["role"], "primary");
    assert_eq!(primary[0]["params"], json!({"strength": 0.4}));
    let (fix, primary) = stages_for(strength_stage(None));
    assert!(fix[0].get("params").is_none());
    assert!(primary[0].get("params").is_none());
}

fn grain_stage(params: serde_json::Value, mask_path: Option<&str>) -> StepStageSelection {
    StepStageSelection {
        step: "grain".into(),
        params: params.as_object().cloned(),
        mask_path: mask_path.map(str::to_owned),
        model_id: None,
    }
}

#[test]
fn step_stages_are_checked_against_the_registry_and_their_media() {
    let valid = grain_stage(json!({"strength": 100, "seed": 7}), Some("/in/mask.png"));
    assert!(validate_step_stages(std::slice::from_ref(&valid), Some("image")).is_ok());
    assert!(validate_step_stages(std::slice::from_ref(&valid), None).is_ok());
    for params in [
        json!({"strength": 101}),
        json!({"strength": -1}),
        json!({"strength": 2.5}),
        json!({"seed": 2_147_483_648_u64}),
        json!({"grain_size": 1}),
    ] {
        assert!(
            validate_step_stages(&[grain_stage(params.clone(), None)], Some("image")).is_err(),
            "{params}"
        );
    }
    let unknown = StepStageSelection {
        step: "noise".into(),
        ..StepStageSelection::default()
    };
    for stages in [
        vec![valid.clone(), grain_stage(json!({}), None)],
        vec![unknown],
        vec![grain_stage(json!({}), Some(""))],
    ] {
        assert!(validate_step_stages(&stages, Some("image")).is_err());
    }
    let video = validate_step_stages(std::slice::from_ref(&valid), Some("video"))
        .unwrap_err()
        .to_string();
    assert!(video.contains("Film grain"), "{video}");

    let mut settings = UiSettings {
        step_stages: vec![grain_stage(json!({"strength": 300}), None)],
        ..UiSettings::default()
    };
    assert!(validate_settings(&settings).is_err());
    settings.step_stages = vec![valid];
    assert!(validate_settings(&settings).is_ok());
}

#[test]
fn step_stages_need_their_engine_feature_and_a_start_that_can_run_them() {
    let engine = |features: &[&str]| crate::types::EngineInfo {
        protocol_version: 1,
        minimum_protocol_version: 1,
        engine_id: "test".into(),
        engine_version: "0.7.0".into(),
        features: features
            .iter()
            .map(|feature| (*feature).to_owned())
            .collect(),
        model_formats: vec![],
        video_engines: vec![],
    };
    let plain = grain_stage(json!(null), None);
    let tuned = grain_stage(json!({"strength": 40}), None);
    let without_grain = engine(&["image", "stage_params_and_masks"]);
    let refusal = validate_step_stages_engine(std::slice::from_ref(&plain), Some(&without_grain))
        .unwrap_err()
        .to_string();
    assert!(refusal.contains("LOCALSR_DEV_STEPS=1"), "{refusal}");
    assert!(validate_step_stages_engine(std::slice::from_ref(&plain), None).is_err());
    let grain_only = engine(&["image", "image_grain"]);
    assert!(validate_step_stages_engine(std::slice::from_ref(&plain), Some(&grain_only)).is_ok());
    assert!(validate_step_stages_engine(std::slice::from_ref(&tuned), Some(&grain_only)).is_err());
    let full = engine(&["image", "image_grain", "stage_params_and_masks"]);
    assert!(validate_step_stages_engine(&[tuned], Some(&full)).is_ok());
    assert!(validate_step_stages_engine(&[], Some(&without_grain)).is_ok());

    // Upscale at 1× with only a step is work; a temporal video model runs no steps.
    let mut one_times = input("upscale");
    one_times.output_scale = 1;
    assert!(validate_start_input(&one_times, true).is_err());
    one_times.step_stages = vec![plain.clone()];
    assert!(validate_start_input(&one_times, true).is_ok());
    let mut temporal = input("video");
    temporal.video_model_id = "seedvr2_3b".into();
    temporal.video_target_resolution = 1080;
    temporal.step_stages = vec![plain];
    assert!(validate_start_input(&temporal, true).is_err());
}

fn photo_media() -> crate::types::MediaItem {
    crate::types::MediaItem {
        id: "media".into(),
        path: "/in/photo.png".into(),
        name: "photo.png".into(),
        kind: "image".into(),
        width: 64,
        height: 48,
        frame_count: 1,
        fps: 0.0,
        duration_seconds: 0.0,
        preview_data_url: String::new(),
        probe_status: "ready".into(),
        hdr_format: String::new(),
        audio_warning: String::new(),
        has_audio: false,
        field_order: String::new(),
        repair_marks: 0,
        repair_rev: 0,
        error: String::new(),
        selected: true,
    }
}

/// The test cut-out step (`steps::testing::cutout`) with `model_id` chosen.
fn cutout_stage(model_id: Option<&str>) -> StepStageSelection {
    StepStageSelection {
        step: "test_cutout".into(),
        model_id: model_id.map(str::to_owned),
        ..StepStageSelection::default()
    }
}

/// A catalog model for task `task`, made from a shipped 1x model.
fn task_model(id: &str, task: &str, installed: bool) -> CatalogModel {
    let mut model = crate::catalog::bundled_catalog()
        .unwrap()
        .models
        .into_iter()
        .find(|model| model.model_id == "denoise_realplksr_1x")
        .unwrap();
    model.model_id = id.into();
    model.filename = format!("{id}.safetensors");
    model.purposes = vec![task.into()];
    model.fixes = Vec::new();
    model.installed = installed;
    model.installed_path = installed.then(|| format!("/models/{id}.safetensors"));
    model
}

/// An app state whose catalog also has cut-out models: one installed, one not, and an
/// installed one of another task.
fn cutout_state(directory: &Path) -> AppState {
    let state = AppState::from_paths(AppPaths::under(directory)).unwrap();
    {
        let mut catalog = lock(&state.catalog).unwrap();
        for model in &mut catalog.models {
            model.installed = true;
            model.installed_path = Some(format!("/models/{}", model.filename));
        }
        catalog.models.extend([
            task_model("cutout_ready", "test_cutout", true),
            task_model("cutout_missing", "test_cutout", false),
            task_model("other_task", "test_other", true),
        ]);
    }
    state
}

#[test]
fn a_step_stage_names_a_model_exactly_when_its_step_needs_one() {
    crate::steps::testing::with_steps(vec![crate::steps::testing::cutout()], || {
        let ready = cutout_stage(Some("cutout_ready"));
        assert!(validate_step_stages(std::slice::from_ref(&ready), Some("image")).is_ok());
        // Saved settings may wait for a choice, as a fix's may; a start may not.
        for waiting in [cutout_stage(None), cutout_stage(Some(""))] {
            assert!(validate_step_stages(std::slice::from_ref(&waiting), None).is_ok());
            let refusal = validate_step_stages(&[waiting], Some("image"))
                .unwrap_err()
                .to_string();
            assert!(
                refusal.contains("Choose a model for Test cut-out"),
                "{refusal}"
            );
        }
        let too_long = cutout_stage(Some(&"m".repeat(65)));
        assert!(validate_step_stages(&[too_long], None).is_err());
        let mut grain = grain_stage(json!({}), None);
        grain.model_id = Some("cutout_ready".into());
        let refusal = validate_step_stages(std::slice::from_ref(&grain), None)
            .unwrap_err()
            .to_string();
        assert!(refusal.contains("without a model"), "{refusal}");
        assert!(validate_settings(&UiSettings {
            step_stages: vec![grain],
            ..UiSettings::default()
        })
        .is_err());
        assert!(validate_step_stages(&[ready], Some("video")).is_err());
    });
}

#[test]
fn a_step_model_is_resolved_like_a_fix_model_and_travels_with_its_stage() {
    crate::steps::testing::with_steps(vec![crate::steps::testing::cutout()], || {
        let directory = tempfile::tempdir().unwrap();
        let state = cutout_state(directory.path());
        let mut options = input("upscale");
        options.model_id = "span_photo_x4".into();
        options.fix_stages = vec![crate::types::FixStageSelection {
            fix: "noise".into(),
            model_id: "scunet_color_real_psnr".into(),
            ..Default::default()
        }];
        options.step_stages = vec![cutout_stage(Some("cutout_ready"))];
        let selection = resolve_model_selection(&state, &options).unwrap();
        let ModelSelection::Image {
            step_models,
            preprocess,
            ..
        } = &selection
        else {
            panic!("expected an image plan");
        };
        assert_eq!(
            step_models,
            &vec![ResolvedStepModel {
                step: "test_cutout".into(),
                model_id: "cutout_ready".into(),
                path: "/models/cutout_ready.safetensors".into(),
            }]
        );
        assert_eq!(preprocess.len(), 1);

        for (model_id, message) in [
            (None, "choose a model for Test cut-out"),
            (Some("unknown"), "choose a model for Test cut-out"),
            (
                Some("cutout_missing"),
                "download and verify the model for Test cut-out",
            ),
            (
                Some("other_task"),
                "Test cut-out must use one of its own models",
            ),
            (
                Some("scunet_color_real_psnr"),
                "must use one of its own models",
            ),
        ] {
            options.step_stages = vec![cutout_stage(model_id)];
            let refusal = resolve_model_selection(&state, &options)
                .err()
                .unwrap()
                .to_string();
            assert!(refusal.contains(message), "{model_id:?}: {refusal}");
        }

        // The stage carries the model as a fix stage carries its own: id and catalog path,
        // which the worker checks against the catalog's SHA-256 before reading it.
        options.step_stages = vec![cutout_stage(Some("cutout_ready"))];
        let message = build_job_message(
            "job",
            &photo_media(),
            Path::new("/out/photo.png"),
            Path::new("/work"),
            &options,
            &resolve_model_selection(&state, &options).unwrap(),
        )
        .unwrap();
        let stages = message["data"]["stages"].as_array().unwrap();
        assert_eq!(
            stages[0],
            json!({
                "kind": "test_cutout",
                "step": "test_cutout",
                "model_id": "cutout_ready",
                "model_path": "/models/cutout_ready.safetensors"
            })
        );
        let fix_file = lock(&state.catalog)
            .unwrap()
            .models
            .iter()
            .find(|model| model.model_id == "scunet_color_real_psnr")
            .unwrap()
            .filename
            .clone();
        assert_eq!(stages[1]["model_id"], "scunet_color_real_psnr");
        assert_eq!(stages[1]["model_path"], format!("/models/{fix_file}"));
        assert!(message["data"]["approved_checkpoint_sha256"].is_null());
        assert_eq!(message["data"]["allow_unverified_checkpoint"], false);
    });
}

#[test]
fn a_photo_job_with_a_matte_step_saves_in_a_format_that_keeps_the_alpha() {
    crate::steps::testing::with_steps(vec![crate::steps::testing::cutout()], || {
        for (format, saved) in [
            ("jpg", "png"),
            ("heic", "png"),
            ("png", "png"),
            ("tif", "tif"),
            ("webp", "webp"),
        ] {
            let mut photo = input("upscale");
            photo.output_format = format.into();
            photo.step_stages = vec![cutout_stage(Some("cutout_ready"))];
            keep_alpha_output_format(&mut photo);
            assert_eq!(photo.output_format, saved, "{format}");
            assert_eq!(output_ending(&photo).1, saved);
        }
        let mut plain = input("upscale");
        plain.output_format = "jpg".into();
        plain.step_stages = vec![grain_stage(json!({}), None)];
        keep_alpha_output_format(&mut plain);
        assert_eq!(plain.output_format, "jpg");
    });
}

#[test]
fn a_cut_out_alone_is_named_cutout() {
    let background = StepStageSelection {
        step: "remove_background".into(),
        model_id: Some("birefnet_general".into()),
        ..StepStageSelection::default()
    };
    let mut cutout = input("denoise");
    cutout.model_id = String::new();
    cutout.fix_stages = Vec::new();
    cutout.step_stages = vec![background.clone()];
    cutout.output_format = "png".into();
    assert_eq!(output_ending(&cutout), ("_cutout".to_owned(), "png"));

    let mut with_fix = cutout.clone();
    with_fix.fix_stages = vec![crate::types::FixStageSelection {
        fix: "noise".into(),
        model_id: "denoise_realplksr_1x".into(),
        ..Default::default()
    }];
    assert_eq!(output_ending(&with_fix).0, "_denoised");

    let mut upscale = input("upscale");
    upscale.output_scale = 4;
    upscale.step_stages = vec![background];
    assert_eq!(output_ending(&upscale).0, "_upscaled_4x");

    let mut grain_only = input("denoise");
    grain_only.model_id = String::new();
    grain_only.step_stages = vec![grain_stage(json!({}), None)];
    assert_eq!(output_ending(&grain_only).0, "_denoised");
}

#[test]
fn restore_with_only_a_step_plans_a_one_times_job_without_a_main_model() {
    crate::steps::testing::with_steps(vec![crate::steps::testing::cutout()], || {
        let directory = tempfile::tempdir().unwrap();
        let state = cutout_state(directory.path());
        let mut alone = input("denoise");
        alone.model_id = String::new();
        alone.output_scale = 1;
        let refusal = validate_start_input(&alone, true).unwrap_err().to_string();
        assert!(refusal.contains("Nothing to run"), "{refusal}");

        for stage in [
            grain_stage(json!({}), None),
            cutout_stage(Some("cutout_ready")),
        ] {
            alone.step_stages = vec![stage];
            assert!(validate_start_input(&alone, true).is_ok());
            let selection = resolve_model_selection(&state, &alone).unwrap();
            let ModelSelection::Image {
                model_id,
                path,
                skip_upscale,
                native_scale,
                ..
            } = &selection
            else {
                panic!("expected an image plan");
            };
            assert!(*skip_upscale && model_id.is_empty() && path.is_empty());
            assert_eq!(*native_scale, 1);
            let message = build_job_message(
                "job",
                &photo_media(),
                Path::new("/out/photo_denoised.png"),
                Path::new("/work"),
                &alone,
                &selection,
            )
            .unwrap();
            assert_eq!(message["data"]["model_path"], "");
            assert_eq!(message["data"]["output_scale"], 1);
            let stages = message["data"]["stages"].as_array().unwrap();
            assert_eq!(stages.len(), 1);
            assert_eq!(stages[0]["step"], alone.step_stages[0].step);
        }
    });
}

#[test]
fn a_stage_mask_must_be_a_file_the_user_granted() {
    let directory = tempfile::tempdir().unwrap();
    let state = AppState::from_paths(AppPaths::under(directory.path())).unwrap();
    let mask = std::fs::canonicalize(directory.path())
        .unwrap()
        .join("mask.png");
    fs::write(&mask, b"png").unwrap();
    let mask_text = mask.to_string_lossy().into_owned();

    let mut stepped = input("upscale");
    stepped.step_stages = vec![grain_stage(json!({}), Some(&mask_text))];
    let mut fixed = input("upscale");
    fixed.fix_stages = vec![crate::types::FixStageSelection {
        fix: "noise".into(),
        model_id: "scunet_color_real_psnr".into(),
        mask_path: Some(mask_text.clone()),
        ..Default::default()
    }];
    assert!(validate_start(&stepped, &state).is_err());
    assert!(check_stage_masks_granted(&fixed, &state).is_err());
    state.grants.grant_media_file(&mask);
    assert!(validate_start(&stepped, &state).is_ok());
    assert!(check_stage_masks_granted(&fixed, &state).is_ok());
    state.grants.revoke_media_file(&mask);
    assert!(validate_start(&stepped, &state).is_err());
}

#[test]
fn image_pipeline_stages_place_step_stages_by_their_phase() {
    let mut photo = input("upscale");
    photo.step_stages = vec![grain_stage(
        json!({"strength": 40, "seed": 7}),
        Some("/in/mask.png"),
    )];
    let fix = vec![ResolvedImageStage {
        kind: "restore",
        fix: "noise".into(),
        model_id: "scunet_color_real_psnr".into(),
        path: "/models/scunet.pth".into(),
        params: None,
        mask_path: None,
    }];
    let kinds = |stages: &[crate::protocol::PipelineStageRequest]| {
        stages
            .iter()
            .map(|stage| stage.kind.clone().unwrap_or_default())
            .collect::<Vec<_>>()
    };
    let stages = image_pipeline_stages(
        &fix,
        &[],
        "span_photo_x4",
        "/models/span.pth",
        Some("/models/face.pth"),
        "hat_l_x4_face",
        false,
        &photo,
    );
    assert_eq!(
        kinds(&stages),
        vec!["restore", "upscale", "face_restore", "grain"]
    );
    let grain = serde_json::to_value(&stages[3]).unwrap();
    assert_eq!(
        grain,
        json!({
            "kind": "grain",
            "step": "grain",
            "params": {"strength": 40, "seed": 7},
            "mask_path": "/in/mask.png"
        })
    );
    let one_times = image_pipeline_stages(&fix, &[], "", "", None, "", true, &photo);
    assert_eq!(kinds(&one_times), vec!["restore", "grain"]);
}

#[test]
fn typed_job_requests_match_fixtures_and_validate_against_schema() {
    let schema: serde_json::Value = serde_json::from_str(include_str!(
        "../../../../protocol/worker-protocol.schema.json"
    ))
    .unwrap();
    let validator = jsonschema::validator_for(&schema).unwrap();
    let fixtures: serde_json::Value = serde_json::from_str(include_str!(
        "../../../../tests/fixtures/job_request_samples.json"
    ))
    .unwrap();

    let image_media = |path: &str, name: &str| crate::types::MediaItem {
        id: "media".into(),
        path: path.into(),
        name: name.into(),
        kind: "image".into(),
        width: 1920,
        height: 1080,
        frame_count: 1,
        fps: 0.0,
        duration_seconds: 0.0,
        preview_data_url: String::new(),
        probe_status: "ready".into(),
        hdr_format: String::new(),
        audio_warning: String::new(),
        has_audio: false,
        field_order: String::new(),
        repair_marks: 0,
        repair_rev: 0,
        error: String::new(),
        selected: true,
    };

    let mut upscale_input = input("upscale");
    upscale_input.model_id = "span_photo_x4".into();
    upscale_input.enable_face_model = true;
    upscale_input.face_fidelity = 65;
    let upscale_selection = ModelSelection::Image {
        model_id: "span_photo_x4".into(),
        path: "/models/span.safetensors".into(),
        approved_checkpoint_sha256: None,
        preprocess: vec![
            ResolvedImageStage {
                kind: "restore",
                fix: "blur".into(),
                model_id: "nafnet_gopro_deblur".into(),
                path: "/models/nafnet.pth".into(),
                params: None,
                mask_path: None,
            },
            ResolvedImageStage {
                kind: "deblock",
                fix: "jpeg".into(),
                model_id: "fbcnn_color".into(),
                path: "/models/fbcnn.pth".into(),
                params: None,
                mask_path: None,
            },
        ],
        step_models: Vec::new(),
        face_path: Some("/models/face.pth".into()),
        face_model_id: "hat_l_x4_face".into(),
        native_scale: 4,
        skip_upscale: false,
        interpolation: None,
        audio_denoise: None,
    };

    let mut restore_input = input("denoise");
    restore_input.model_id = "scunet_color_real_psnr".into();
    restore_input.output_scale = 1;
    restore_input.fix_stages = vec![
        crate::types::FixStageSelection {
            fix: "jpeg".into(),
            model_id: "fbcnn_color".into(),
            ..Default::default()
        },
        crate::types::FixStageSelection {
            fix: "noise".into(),
            model_id: "scunet_color_real_psnr".into(),
            ..Default::default()
        },
    ];
    let restore_selection = ModelSelection::Image {
        model_id: "scunet_color_real_psnr".into(),
        path: "/models/scunet.pth".into(),
        approved_checkpoint_sha256: None,
        preprocess: vec![ResolvedImageStage {
            kind: "deblock",
            fix: "jpeg".into(),
            model_id: "fbcnn_color".into(),
            path: "/models/fbcnn.pth".into(),
            params: None,
            mask_path: None,
        }],
        step_models: Vec::new(),
        face_path: None,
        face_model_id: String::new(),
        native_scale: 1,
        skip_upscale: false,
        interpolation: None,
        audio_denoise: None,
    };

    let mut params_input = input("upscale");
    params_input.model_id = "span_photo_x4".into();
    params_input.output_scale = 2;
    let params_selection = ModelSelection::Image {
        model_id: "span_photo_x4".into(),
        path: "/models/span.safetensors".into(),
        approved_checkpoint_sha256: None,
        preprocess: vec![ResolvedImageStage {
            kind: "restore",
            fix: "noise".into(),
            model_id: "scunet_color_real_psnr".into(),
            path: "/models/scunet.pth".into(),
            params: Some(serde_json::Map::from_iter([(
                "strength".into(),
                json!(0.5),
            )])),
            mask_path: Some("/in/mask.png".into()),
        }],
        step_models: Vec::new(),
        face_path: None,
        face_model_id: String::new(),
        native_scale: 4,
        skip_upscale: false,
        interpolation: None,
        audio_denoise: None,
    };

    let mut grain_input = input("upscale");
    grain_input.model_id = "span_photo_x4".into();
    grain_input.output_scale = 2;
    grain_input.step_stages = vec![grain_stage(
        json!({"strength": 40, "seed": 7}),
        Some("/in/mask.png"),
    )];
    let grain_selection = ModelSelection::Image {
        model_id: "span_photo_x4".into(),
        path: "/models/span.safetensors".into(),
        approved_checkpoint_sha256: None,
        preprocess: vec![ResolvedImageStage {
            kind: "restore",
            fix: "noise".into(),
            model_id: "scunet_color_real_psnr".into(),
            path: "/models/scunet.pth".into(),
            params: None,
            mask_path: None,
        }],
        step_models: Vec::new(),
        face_path: None,
        face_model_id: String::new(),
        native_scale: 4,
        skip_upscale: false,
        interpolation: None,
        audio_denoise: None,
    };

    let work = Path::new("/work/scratch");
    // Restore with nothing but a step that brings its catalog model, as background removal
    // alone will run; the step exists only in this test (`steps::testing::cutout`).
    let mut step_alone_input = input("denoise");
    step_alone_input.model_id = String::new();
    step_alone_input.output_scale = 1;
    step_alone_input.step_stages = vec![cutout_stage(Some("test_cutout_model"))];
    let step_alone_selection = ModelSelection::Image {
        model_id: String::new(),
        path: String::new(),
        approved_checkpoint_sha256: None,
        preprocess: Vec::new(),
        step_models: vec![ResolvedStepModel {
            step: "test_cutout".into(),
            model_id: "test_cutout_model".into(),
            path: "/models/test-cutout.safetensors".into(),
        }],
        face_path: None,
        face_model_id: String::new(),
        native_scale: 1,
        skip_upscale: true,
        interpolation: None,
        audio_denoise: None,
    };
    let step_alone_sample =
        crate::steps::testing::with_steps(vec![crate::steps::testing::cutout()], || {
            build_job_message(
                "sample-image-step-alone",
                &image_media("/in/portrait.png", "portrait.png"),
                Path::new("/out/portrait_denoised.png"),
                work,
                &step_alone_input,
                &step_alone_selection,
            )
            .unwrap()
        });

    let mut fbf_video_input = input("video");
    fbf_video_input.model_id = "span_photo_x4".into();
    fbf_video_input.deflicker = true;
    fbf_video_input.deflicker_window = 5;
    fbf_video_input.video_interpolation = "x2".into();
    fbf_video_input.video_interpolation_order = "before_upscale".into();
    fbf_video_input.video_clean_audio = true;
    let fbf_video_selection = ModelSelection::Image {
        model_id: "span_photo_x4".into(),
        path: "/models/span.safetensors".into(),
        approved_checkpoint_sha256: None,
        preprocess: Vec::new(),
        step_models: Vec::new(),
        face_path: None,
        face_model_id: String::new(),
        native_scale: 4,
        skip_upscale: false,
        interpolation: Some(ResolvedBundle {
            model_id: "rife_4_26".into(),
            bundle_dir: "/models/rife_4_26".into(),
        }),
        audio_denoise: Some(ResolvedBundle {
            model_id: "deepfilternet3".into(),
            bundle_dir: "/models/deepfilternet3".into(),
        }),
    };

    let mut slow_video_input = input("video");
    slow_video_input.model_id = "span_photo_x4".into();
    slow_video_input.video_interpolation = "x2".into();
    slow_video_input.video_slow_motion = true;
    let slow_video_selection = ModelSelection::Image {
        model_id: "span_photo_x4".into(),
        path: "/models/span.safetensors".into(),
        approved_checkpoint_sha256: None,
        preprocess: Vec::new(),
        step_models: Vec::new(),
        face_path: None,
        face_model_id: String::new(),
        native_scale: 4,
        skip_upscale: false,
        interpolation: Some(ResolvedBundle {
            model_id: "rife_4_26".into(),
            bundle_dir: "/models/rife_4_26".into(),
        }),
        audio_denoise: None,
    };

    let mut temporal_input = input("video");
    temporal_input.video_model_id = "seedvr2_3b".into();
    temporal_input.video_target_resolution = 1080;
    temporal_input.video_low_memory = true;
    let temporal_selection = ModelSelection::Temporal {
        model_id: "seedvr2_3b".into(),
        engine_kind: "seedvr2".into(),
        bundle_dir: "/models/seedvr2".into(),
        temporal_window: 9,
        temporal_overlap: 2,
        audio_denoise: None,
    };
    let mut silent_video = video_media();
    silent_video.has_audio = false;

    let catalog_edit_model = crate::editing::EditModel {
        model_id: "qwen_edit_2511_q2_k".into(),
        name: "Qwen Image Edit 2511".into(),
        family: "qwen-image-edit-2511".into(),
        quantization: "Q2_K".into(),
        files: Vec::new(),
        min_unified_memory_gb: 24,
        min_vram_gb: 8,
        default_steps: 40,
        cfg_scale: 2.5,
        author: String::new(),
        license_name: String::new(),
        license_url: String::new(),
        source_url: String::new(),
        commercial_use_allowed: None,
        attribution_required: false,
        terms_acceptance_required: false,
        automated_download_allowed: true,
        installed: true,
        total_size_bytes: 0,
    };
    let catalog_edit_input = crate::editing::StartEditInput {
        media_id: "media".into(),
        model_id: "qwen_edit_2511_q2_k".into(),
        prompt: "Replace the sky with sunset clouds.".into(),
        runtime_path: "/bin/sd-cli".into(),
        device: "mps".into(),
        max_dimension: 512,
        steps: 40,
        seed: 42,
        accepted_terms: false,
        output_directory: "/out".into(),
        edit_text_encoder: "auto".into(),
        edit_vae: "auto".into(),
        edit_vram_limit_mb: None,
        edit_allow_any_size: None,
    };
    let custom_edit_model = crate::editing::EditModel {
        model_id: "__custom__".into(),
        family: "flux2-klein".into(),
        ..catalog_edit_model.clone()
    };
    let custom_edit_input = crate::editing::StartEditInput {
        media_id: "media".into(),
        model_id: "__custom__".into(),
        prompt: "Make it warmer.".into(),
        runtime_path: "/bin/sd-cli".into(),
        device: "mps".into(),
        max_dimension: 768,
        steps: 4,
        seed: 7,
        accepted_terms: false,
        output_directory: "/out".into(),
        edit_text_encoder: "auto".into(),
        edit_vae: "auto".into(),
        edit_vram_limit_mb: None,
        edit_allow_any_size: None,
    };
    let custom_files = std::collections::BTreeMap::from([
        ("diffusion".into(), "/custom/flux.gguf".into()),
        ("text_encoder".into(), "/custom/te.gguf".into()),
        ("vae".into(), "/custom/vae.safetensors".into()),
    ]);

    let generated = [
        (
            "image_upscale_fixes_and_face",
            build_job_message(
                "sample-image-upscale",
                &image_media("/in/photo.png", "photo.png"),
                Path::new("/out/photo_upscaled_4x.png"),
                work,
                &upscale_input,
                &upscale_selection,
            )
            .unwrap(),
        ),
        (
            "image_same_size_restore",
            build_job_message(
                "sample-image-restore",
                &image_media("/in/noisy.png", "noisy.png"),
                Path::new("/out/noisy_denoised.png"),
                work,
                &restore_input,
                &restore_selection,
            )
            .unwrap(),
        ),
        (
            "image_stage_params_and_mask",
            build_job_message(
                "sample-image-stage-params",
                &image_media("/in/masked.png", "masked.png"),
                Path::new("/out/masked_upscaled_2x.png"),
                work,
                &params_input,
                &params_selection,
            )
            .unwrap(),
        ),
        (
            "image_modelless_grain_step",
            build_job_message(
                "sample-image-grain",
                &image_media("/in/grain.png", "grain.png"),
                Path::new("/out/grain_upscaled_2x.png"),
                work,
                &grain_input,
                &grain_selection,
            )
            .unwrap(),
        ),
        ("image_step_with_model_alone", step_alone_sample),
        (
            "video_interpolation_deflicker_audio",
            build_job_message(
                "sample-video-fbf",
                &video_media(),
                Path::new("/out/clip_video_4x.mp4"),
                work,
                &fbf_video_input,
                &fbf_video_selection,
            )
            .unwrap(),
        ),
        (
            "video_slow_motion",
            build_job_message(
                "sample-video-slow",
                &video_media(),
                Path::new("/out/clip_video_4x_slow.mp4"),
                work,
                &slow_video_input,
                &slow_video_selection,
            )
            .unwrap(),
        ),
        (
            "video_temporal_model",
            build_job_message(
                "sample-video-temporal",
                &silent_video,
                Path::new("/out/clip_video_1080px.mp4"),
                work,
                &temporal_input,
                &temporal_selection,
            )
            .unwrap(),
        ),
        (
            "edit_catalog_model",
            serde_json::to_value(build_edit_request(
                "sample-edit-catalog",
                "/in/photo.png",
                Path::new("/out/photo_localsr_edit_sample.png"),
                &catalog_edit_model,
                "/models/qwen-image-edit-2511".into(),
                Path::new("/bin/sd-cli"),
                &catalog_edit_input,
                None,
                0,
                false,
            ))
            .unwrap(),
        ),
        (
            "edit_custom_model",
            serde_json::to_value(build_edit_request(
                "sample-edit-custom",
                "/in/photo.png",
                Path::new("/out/photo_localsr_edit_custom.png"),
                &custom_edit_model,
                String::new(),
                Path::new("/bin/sd-cli"),
                &custom_edit_input,
                Some(custom_files),
                0,
                false,
            ))
            .unwrap(),
        ),
    ];

    assert_eq!(
        generated.len(),
        fixtures.as_object().unwrap().len(),
        "fixture sample count must match Rust sample set"
    );
    for (name, sample) in generated {
        let errors: Vec<String> = validator
            .iter_errors(&sample)
            .map(|error| error.to_string())
            .collect();
        assert!(errors.is_empty(), "{name} failed schema: {errors:?}");
        assert_eq!(sample, fixtures[name], "{name} drifted from fixture");
        let round_trip: crate::protocol::WorkerJobRequest =
            serde_json::from_value(sample.clone()).unwrap();
        assert_eq!(serde_json::to_value(round_trip).unwrap(), sample, "{name}");
    }
}

#[test]
fn plan_job_checked_resolves_stages_from_app_state() {
    let directory = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(directory.path());
    let state = AppState::from_paths(paths).unwrap();

    let upscale_settings = UiSettings {
        task: "upscale".into(),
        selected_model_id: "span_photo_x4".into(),
        output_scale: 4,
        fix_stages: vec![crate::types::FixStageSelection {
            fix: "jpeg".into(),
            model_id: "fbcnn_color".into(),
            ..Default::default()
        }],
        ..UiSettings::default()
    };
    let upscale_plan =
        plan_job_checked(&state, upscale_settings, PlanJobOptions::default()).unwrap();
    assert_eq!(upscale_plan.len(), 2);
    assert_eq!(upscale_plan[0].kind, "deblock");
    assert_eq!(upscale_plan[0].model_id.as_deref(), Some("fbcnn_color"));
    assert_eq!(upscale_plan[1].kind, "upscale");
    assert_eq!(upscale_plan[1].model_id.as_deref(), Some("span_photo_x4"));

    let temporal_settings = UiSettings {
        task: "video".into(),
        video_task: "upscale".into(),
        selected_video_model_id: "flashvsr_v11".into(),
        video_clean_audio: true,
        output_scale: 4,
        ..UiSettings::default()
    };
    let temporal_plan =
        plan_job_checked(&state, temporal_settings, PlanJobOptions::default()).unwrap();
    assert_eq!(
        temporal_plan
            .iter()
            .map(|stage| stage.kind.as_str())
            .collect::<Vec<_>>(),
        vec!["audio_denoise", "video"]
    );

    let edit_settings = UiSettings {
        task: "edit".into(),
        edit_model_id: "qwen_edit_2511_q4_k_m".into(),
        ..UiSettings::default()
    };
    let edit_plan = plan_job_checked(&state, edit_settings, PlanJobOptions::default()).unwrap();
    assert_eq!(edit_plan.len(), 1);
    assert_eq!(edit_plan[0].kind, "edit");
    assert_eq!(
        edit_plan[0].edit_model_id.as_deref(),
        Some("qwen_edit_2511_q4_k_m")
    );
}

#[test]
fn stay_awake_follows_queue_activity_preference_and_shutdown() {
    let directory = tempfile::tempdir().unwrap();
    let paths = crate::paths::AppPaths::under(directory.path());
    fs::create_dir_all(&paths.next_root).unwrap();
    fs::create_dir_all(&paths.model_root).unwrap();
    let state = AppState::from_paths(paths).unwrap();
    assert!(!state.stay_awake.is_held());

    {
        let database = lock(&state.database).unwrap();
        database
            .insert_media("m1", "/in/photo.png", "photo.png", "image")
            .unwrap();
        database
            .insert_job(
                "j1",
                "m1",
                r#"{"type":"job_request","data":{"output_path":"/out/photo.png"}}"#,
            )
            .unwrap();
    }
    crate::stay_awake::sync(&state);
    assert!(state.stay_awake.is_held());

    apply_preference(&state, "keep_awake", &json!(false)).unwrap();
    assert!(!state.stay_awake.is_held());

    apply_preference(&state, "keep_awake", &json!(true)).unwrap();
    assert!(state.stay_awake.is_held());

    cancel_jobs_checked(&state, false).unwrap();
    assert!(!state.stay_awake.is_held());

    lock(&state.database)
        .unwrap()
        .insert_job(
            "j2",
            "m1",
            r#"{"type":"job_request","data":{"output_path":"/out/photo2.png"}}"#,
        )
        .unwrap();
    crate::stay_awake::sync(&state);
    assert!(state.stay_awake.is_held());

    crate::worker::shutdown(&state);
    assert!(!state.stay_awake.is_held());
}

#[test]
fn precision_auto_validates_and_falls_back_to_fp32_without_engine_feature() {
    let mut settings = UiSettings::default();
    assert_eq!(settings.precision, "auto");
    assert!(validate_settings(&settings).is_ok());

    for valid in ["auto", "fp32", "fp16", "bf16"] {
        settings.precision = valid.into();
        assert!(validate_settings(&settings).is_ok(), "{valid}");
    }
    settings.precision = "fp8".into();
    assert!(validate_settings(&settings).is_err());

    let mut auto_input = input("upscale");
    auto_input.precision = "auto".into();
    let selection = ModelSelection::Image {
        model_id: "span_photo_x4".into(),
        path: "/models/span.pth".into(),
        approved_checkpoint_sha256: None,
        preprocess: vec![],
        step_models: Vec::new(),
        face_path: None,
        face_model_id: String::new(),
        native_scale: 4,
        skip_upscale: false,
        interpolation: None,
        audio_denoise: None,
    };
    let media = crate::types::MediaItem {
        id: "media".into(),
        path: "/in/photo.png".into(),
        name: "photo.png".into(),
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
        has_audio: false,
        field_order: String::new(),
        repair_marks: 0,
        repair_rev: 0,
        error: String::new(),
        selected: true,
    };

    // Older engine (no engine info or missing "precision_auto") receives "fp32" instead of "auto".
    let no_engine_msg = build_job_message_for_engine(
        "j1",
        &media,
        Path::new("/out/a.png"),
        Path::new("/work"),
        &auto_input,
        &selection,
        None,
    )
    .unwrap();
    assert_eq!(no_engine_msg["data"]["precision"], "fp32");

    let mut engine = crate::types::EngineInfo {
        protocol_version: 1,
        minimum_protocol_version: 1,
        engine_id: "test".into(),
        engine_version: "0.6.2".into(),
        features: vec!["image".into()],
        model_formats: vec![],
        video_engines: vec![],
    };
    let old_engine_msg = build_job_message_for_engine(
        "j2",
        &media,
        Path::new("/out/a.png"),
        Path::new("/work"),
        &auto_input,
        &selection,
        Some(&engine),
    )
    .unwrap();
    assert_eq!(old_engine_msg["data"]["precision"], "fp32");

    // Capable engine receives "auto".
    engine.features.push("precision_auto".into());
    let new_engine_msg = build_job_message_for_engine(
        "j3",
        &media,
        Path::new("/out/a.png"),
        Path::new("/work"),
        &auto_input,
        &selection,
        Some(&engine),
    )
    .unwrap();
    assert_eq!(new_engine_msg["data"]["precision"], "auto");

    // Explicit fp16 is unchanged even for an older engine.
    let mut fp16_input = input("upscale");
    fp16_input.precision = "fp16".into();
    let fp16_msg = build_job_message_for_engine(
        "j4",
        &media,
        Path::new("/out/a.png"),
        Path::new("/work"),
        &fp16_input,
        &selection,
        None,
    )
    .unwrap();
    assert_eq!(fp16_msg["data"]["precision"], "fp16");
}

#[cfg(unix)]
#[test]
fn preview_regions_refuses_ungranted_originals_and_non_owned_outputs() {
    let directory = tempfile::tempdir().unwrap();
    let root = fs::canonicalize(directory.path()).unwrap();
    let state = AppState::from_paths(AppPaths::under(&root.join("app"))).unwrap();
    let photo = root.join("photo.png");
    let stranger = root.join("stranger.png");
    let private = root.join("private.png");
    let output = root.join("out.png");
    let other_output = root.join("other_out.png");
    for file in [&photo, &stranger, &private, &output, &other_output] {
        fs::write(file, b"png").unwrap();
    }
    state.grants.grant_media_file(&photo);
    *lock(&state.engine).unwrap() = Some(crate::types::EngineInfo {
        protocol_version: 1,
        minimum_protocol_version: 1,
        engine_id: "test".into(),
        engine_version: "1".into(),
        features: vec!["compare_regions".into(), "preview_regions".into()],
        model_formats: vec![],
        video_engines: vec![],
    });
    {
        let db = lock(&state.database).unwrap();
        db.insert_media("m", photo.to_str().unwrap(), "photo.png", "image")
            .unwrap();
        db.update_media_probe(
            photo.to_str().unwrap(),
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
        db.insert_media("s", stranger.to_str().unwrap(), "stranger.png", "image")
            .unwrap();
        db.update_media_probe(
            stranger.to_str().unwrap(),
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
        db.insert_job("job-m", "m", "{}").unwrap();
        db.finish_job("job-m", "completed", output.to_str().unwrap(), "")
            .unwrap();
        db.insert_job("job-s", "s", "{}").unwrap();
        db.finish_job("job-s", "completed", other_output.to_str().unwrap(), "")
            .unwrap();
    }
    let region = crate::compare::Region {
        x: 0,
        y: 0,
        width: 100,
        height: 100,
        target_width: 200,
        target_height: 200,
    };

    // Ungranted original media is refused.
    assert!(request_preview_regions_checked(
        &state,
        "s",
        0,
        &crate::compare::PairRegions {
            original: Some(region),
            showing: None,
            against: None,
        },
    )
    .is_err());

    // Unknown media is refused.
    assert!(request_preview_regions_checked(
        &state,
        "missing",
        0,
        &crate::compare::PairRegions {
            original: Some(region),
            showing: None,
            against: None,
        },
    )
    .is_err());

    // Non-owned job output (job-s belongs to media "s", not "m") is refused.
    {
        let mut runtime = lock(&state.runtime).unwrap();
        runtime.comparison_pair = crate::types::ComparisonPair {
            media_id: "m".into(),
            generation: 1,
            showing: crate::types::ComparisonSlot {
                target_id: "job-s".into(),
                output_path: other_output.to_string_lossy().into_owned(),
                preview_data_url: "asset://other".into(),
                width: 3264,
                height: 4896,
                preview_width: 1365,
                preview_height: 2048,
                ..Default::default()
            },
            against: crate::types::ComparisonSlot {
                target_id: "original".into(),
                width: 3264,
                height: 4896,
                ..Default::default()
            },
            ..Default::default()
        };
    }
    assert!(request_preview_regions_checked(
        &state,
        "m",
        1,
        &crate::compare::PairRegions {
            original: None,
            showing: Some(region),
            against: None,
        },
    )
    .is_err());

    // Granted original + owned job output succeed; symlink replacement is refused.
    {
        let mut runtime = lock(&state.runtime).unwrap();
        runtime.comparison_pair.showing.target_id = "job-m".into();
        runtime.comparison_pair.showing.output_path = output.to_string_lossy().into_owned();
    }
    assert!(request_preview_regions_checked(
        &state,
        "m",
        1,
        &crate::compare::PairRegions {
            original: Some(region),
            showing: Some(region),
            against: None,
        },
    )
    .is_ok());

    fs::remove_file(&photo).unwrap();
    std::os::unix::fs::symlink(&private, &photo).unwrap();
    assert!(request_preview_regions_checked(
        &state,
        "m",
        1,
        &crate::compare::PairRegions {
            original: Some(region),
            showing: None,
            against: None,
        },
    )
    .is_err());

    // An engine without preview regions skips without error after auth passes.
    fs::remove_file(&photo).unwrap();
    fs::write(&photo, b"png").unwrap();
    lock(&state.engine)
        .unwrap()
        .as_mut()
        .unwrap()
        .features
        .clear();
    assert!(request_preview_regions_checked(
        &state,
        "m",
        1,
        &crate::compare::PairRegions {
            original: Some(region),
            showing: Some(region),
            against: None,
        },
    )
    .is_ok());
    assert!(clear_preview_regions_checked(&state).is_ok());
}

#[cfg(unix)]
#[test]
fn preview_regions_go_only_to_an_engine_that_has_them() {
    let directory = tempfile::tempdir().unwrap();
    let root = fs::canonicalize(directory.path()).unwrap();
    let state = AppState::from_paths(AppPaths::under(&root.join("app"))).unwrap();
    let photo = root.join("photo.png");
    fs::write(&photo, b"png").unwrap();
    state.grants.grant_media_file(&photo);
    {
        let db = lock(&state.database).unwrap();
        db.insert_media("m", photo.to_str().unwrap(), "photo.png", "image")
            .unwrap();
        db.update_media_probe(
            photo.to_str().unwrap(),
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
    }
    let (sender, receiver) = std::sync::mpsc::channel();
    *lock(&state.worker.sender).unwrap() = Some(sender);
    let region = crate::compare::Region {
        x: 0,
        y: 0,
        width: 100,
        height: 100,
        target_width: 200,
        target_height: 200,
    };
    let regions = crate::compare::PairRegions {
        original: Some(region),
        showing: None,
        against: None,
    };
    let engine = |features: &[&str]| crate::types::EngineInfo {
        protocol_version: 1,
        minimum_protocol_version: 1,
        engine_id: "test".into(),
        engine_version: "1".into(),
        features: features.iter().map(|f| (*f).to_string()).collect(),
        model_formats: vec![],
        video_engines: vec![],
    };

    // A 0.6 engine knows Compare regions but would drop these slots unanswered.
    *lock(&state.engine).unwrap() = Some(engine(&["compare_regions"]));
    request_preview_regions_checked(&state, "m", 0, &regions).unwrap();
    clear_preview_regions_checked(&state).unwrap();
    assert!(receiver.try_recv().is_err());

    *lock(&state.engine).unwrap() = Some(engine(&["compare_regions", "preview_regions"]));
    request_preview_regions_checked(&state, "m", 0, &regions).unwrap();
    let sent: serde_json::Value = serde_json::from_str(&receiver.try_recv().unwrap()).unwrap();
    assert_eq!(sent["data"]["slot"], "original");
}

#[test]
fn background_models_cannot_be_used_for_primary_or_fix_tasks() {
    let model = crate::catalog::bundled_catalog()
        .unwrap()
        .models
        .into_iter()
        .find(|model| model.model_id == "birefnet_general")
        .unwrap();
    for task in ["denoise", "upscale", "video"] {
        assert!(validate_model_for_task(&model, task).is_err());
    }
    for fix in ["blur", "noise", "jpeg", "colour"] {
        assert!(!crate::job_plan::fits_fix(&model, fix));
    }
}

fn estimate_state() -> (tempfile::TempDir, Arc<AppState>) {
    const MODELS: [&str; 3] = ["scunet_color_real_psnr", "fbcnn_color", "hat_s_x4"];
    let directory = tempfile::tempdir().unwrap();
    let state = Arc::new(AppState::from_paths(AppPaths::under(directory.path())).unwrap());
    let checkpoint = directory.path().join("model.pth");
    fs::write(&checkpoint, "weights").unwrap();
    for id in ["a", "b", "c"] {
        let image = directory.path().join(format!("{id}.png"));
        fs::write(&image, "source").unwrap();
        state.grants.grant_media_file(&image);
        let database = lock(&state.database).unwrap();
        database
            .insert_media(id, image.to_str().unwrap(), &format!("{id}.png"), "image")
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
                "",
            )
            .unwrap();
    }
    for model in &mut lock(&state.catalog).unwrap().models {
        if MODELS.contains(&model.model_id.as_str()) {
            model.installed = true;
            model.installed_path = Some(checkpoint.to_string_lossy().into_owned());
        }
    }
    lock(&state.capabilities).unwrap().system_ram_total = 8 << 30;
    *lock(&state.engine).unwrap() = Some(crate::types::EngineInfo {
        protocol_version: 1,
        minimum_protocol_version: 1,
        engine_id: "test".into(),
        engine_version: "0".into(),
        features: vec!["image_multi_fix".into(), "stage_params_and_masks".into()],
        model_formats: vec![],
        video_engines: vec![],
    });
    (directory, state)
}

fn estimate_input(scale: u32) -> StartBatchInput {
    StartBatchInput {
        model_id: "hat_s_x4".into(),
        output_scale: scale,
        fix_stages: vec![
            crate::types::FixStageSelection {
                fix: "jpeg".into(),
                model_id: "fbcnn_color".into(),
                ..Default::default()
            },
            crate::types::FixStageSelection {
                fix: "noise".into(),
                model_id: "scunet_color_real_psnr".into(),
                ..Default::default()
            },
        ],
        ..input("upscale")
    }
}

#[test]
fn estimate_start_waits_for_two_measured_runs_of_the_same_work() {
    let (_directory, state) = estimate_state();
    let ask = |ids: &[&str], scale: u32| {
        estimate_start_checked(
            &state,
            ids.iter().map(|id| (*id).to_owned()).collect(),
            estimate_input(scale),
        )
        .unwrap()
    };
    assert_eq!(ask(&["c"], 4), None);

    // The same two-fix plan Start queues, run and measured on two other photos.
    for (media, seconds) in [("a", 12.0), ("b", 4.0)] {
        start_jobs_checked(
            &state,
            StartBatchInput {
                media_ids: vec![media.into()],
                ..estimate_input(4)
            },
        )
        .unwrap();
        let job = lock(&state.database)
            .unwrap()
            .next_queued_job()
            .unwrap()
            .unwrap();
        let database = lock(&state.database).unwrap();
        database
            .finish_job(&job.id, "completed", "out.png", "")
            .unwrap();
        database.record_job_duration(&job.id, seconds).unwrap();
        drop(database);
        if media == "a" {
            assert_eq!(ask(&["c"], 4), None, "one run is not enough");
        }
    }
    assert_eq!(ask(&["c"], 4), Some(4.0));
    // A batch is the sum of its items, and any item without a number removes the total.
    assert_eq!(ask(&["a", "b", "c"], 4), Some(12.0));
    assert_eq!(ask(&["c", "missing"], 4), None);
    // Another scale is other work: no number.
    assert_eq!(ask(&["c"], 2), None);
    // Settings Start would refuse give an error, not a number.
    assert!(estimate_start_checked(
        &state,
        vec!["c".into()],
        StartBatchInput {
            model_id: "no_such_model".into(),
            ..estimate_input(4)
        }
    )
    .is_err());
}

#[test]
fn slow_motion_needs_two_or_four_times_and_an_engine_that_lists_it() {
    let mut slow = input("video");
    slow.video_slow_motion = true;
    for target in ["off", "fps60", "fps120"] {
        slow.video_interpolation = target.into();
        let error = validate_start_input(&slow, true).unwrap_err().to_string();
        assert!(error.contains("Slow motion needs 2"), "{target}: {error}");
    }
    for target in ["x2", "x4"] {
        slow.video_interpolation = target.into();
        validate_start_input(&slow, true).unwrap();
    }
    slow.video_model_id = "seedvr2_3b".into();
    assert!(validate_start_input(&slow, true).is_err());

    let mut photo = input("upscale");
    photo.video_slow_motion = true;
    photo.video_interpolation = "x2".into();
    assert!(validate_start_input(&photo, true).is_err());

    let mut wanted = input("video");
    wanted.video_slow_motion = true;
    wanted.video_interpolation = "x2".into();
    let mut engine = crate::types::EngineInfo {
        protocol_version: 1,
        minimum_protocol_version: 1,
        engine_id: "test".into(),
        engine_version: "0".into(),
        features: vec!["video_interpolation".into()],
        model_formats: Vec::new(),
        video_engines: Vec::new(),
    };
    let refused = validate_slow_motion_engine(&wanted, Some(&engine))
        .unwrap_err()
        .to_string();
    assert_eq!(
        refused,
        "This engine cannot make slow motion. Update LocalSR."
    );
    assert!(validate_slow_motion_engine(&wanted, None).is_err());
    engine.features.push(SLOW_MOTION_FEATURE.into());
    validate_slow_motion_engine(&wanted, Some(&engine)).unwrap();
    wanted.video_slow_motion = false;
    validate_slow_motion_engine(&wanted, None).unwrap();
}

#[test]
fn the_video_request_carries_slow_motion() {
    let work = Path::new("/app-owned/work");
    let selection = |audio: bool| ModelSelection::Image {
        model_id: "span_photo_x4".into(),
        path: "/models/span.safetensors".into(),
        approved_checkpoint_sha256: None,
        preprocess: Vec::new(),
        step_models: Vec::new(),
        face_path: None,
        face_model_id: String::new(),
        native_scale: 4,
        skip_upscale: false,
        interpolation: Some(ResolvedBundle {
            model_id: "rife_4_26".into(),
            bundle_dir: "/models/rife_4_26".into(),
        }),
        audio_denoise: audio.then(|| ResolvedBundle {
            model_id: "deepfilternet3".into(),
            bundle_dir: "/models/deepfilternet3".into(),
        }),
    };
    let mut slow = input("video");
    slow.video_interpolation = "x4".into();
    slow.video_slow_motion = true;
    slow.video_clean_audio = true;
    let message = build_job_message(
        "slow",
        &video_media(),
        Path::new("/out/clip.mp4"),
        work,
        &slow,
        &selection(true),
    )
    .unwrap();
    assert_eq!(message["data"]["slow_motion"], true);
    assert_eq!(message["data"]["interpolation"], "x4");
    assert_eq!(message["data"]["audio_denoise"], false);
    slow.video_slow_motion = false;
    let normal = build_job_message(
        "normal",
        &video_media(),
        Path::new("/out/clip.mp4"),
        work,
        &slow,
        &selection(true),
    )
    .unwrap();
    assert!(normal["data"].get("slow_motion").is_none());
    assert_eq!(normal["data"]["audio_denoise"], true);
}

#[test]
fn a_slow_motion_result_is_named_slow() {
    let mut slow = input("video");
    slow.output_scale = 2;
    assert_eq!(output_ending(&slow).0, "_video_2x");
    slow.video_slow_motion = true;
    assert_eq!(output_ending(&slow).0, "_video_2x_slow");
}

#[test]
fn slow_motion_settings_default_to_off_and_a_recipe_without_it_loads_false() {
    assert!(!UiSettings::default().video_slow_motion);
    let mut older = serde_json::to_value(UiSettings::default()).unwrap();
    older.as_object_mut().unwrap().remove("video_slow_motion");
    let parsed: UiSettings = serde_json::from_value(older).unwrap();
    assert!(!parsed.video_slow_motion);
    let recipe: crate::types::Recipe = serde_json::from_value(serde_json::json!({
        "id": "r", "name": "n", "task": "video", "model_id": "m", "output_scale": 2,
        "tile_size": 256, "halo": 32, "precision": "fp32", "safe_memory": true,
        "stages": [], "video_interpolation": "x2"
    }))
    .unwrap();
    assert!(!recipe.video_slow_motion);
}

mod repair {
    use super::*;
    use crate::repair_mask::{RepairStroke, StrokeMode};

    fn paint(x: f64, y: f64) -> RepairStroke {
        RepairStroke {
            mode: StrokeMode::Paint,
            size: 12.0,
            points: vec![x, y, x + 30.0, y + 4.0],
        }
    }

    fn erase(x: f64, y: f64) -> RepairStroke {
        RepairStroke {
            mode: StrokeMode::Erase,
            size: 6.0,
            points: vec![x, y],
        }
    }

    fn repairing_state() -> (tempfile::TempDir, Arc<AppState>) {
        let (directory, state) = estimate_state();
        let video = directory.path().join("clip.mp4");
        fs::write(&video, "source").unwrap();
        state.grants.grant_media_file(&video);
        {
            let database = lock(&state.database).unwrap();
            database
                .insert_media("clip", video.to_str().unwrap(), "clip.mp4", "video")
                .unwrap();
            database
                .update_media_probe(
                    video.to_str().unwrap(),
                    "video",
                    640,
                    480,
                    100,
                    25.0,
                    4.0,
                    "preview",
                    "",
                    "",
                    true,
                    "",
                )
                .unwrap();
        }
        *lock(&state.engine).unwrap() = Some(crate::types::EngineInfo {
            protocol_version: 1,
            minimum_protocol_version: 1,
            engine_id: "test".into(),
            engine_version: "0".into(),
            features: vec![
                "image_multi_fix".into(),
                "stage_params_and_masks".into(),
                "image_repair".into(),
            ],
            model_formats: vec![],
            video_engines: vec![],
        });
        (directory, state)
    }

    fn marks(state: &AppState, id: &str) -> (u32, u32) {
        let media = lock(&state.database)
            .unwrap()
            .get_media(id)
            .unwrap()
            .unwrap();
        (media.repair_marks, media.repair_rev)
    }

    fn repair_step() -> StepStageSelection {
        StepStageSelection {
            step: "repair".into(),
            ..StepStageSelection::default()
        }
    }

    /// The restore-alone input of Dust & scratches over `ids`.
    fn repair_alone(ids: &[&str]) -> StartBatchInput {
        StartBatchInput {
            media_ids: ids.iter().map(|id| (*id).to_owned()).collect(),
            batch_mode: true,
            model_id: String::new(),
            output_scale: 1,
            step_stages: vec![repair_step()],
            ..input("denoise")
        }
    }

    fn queued_requests(state: &AppState) -> Vec<serde_json::Value> {
        let mut requests = Vec::new();
        loop {
            let database = lock(&state.database).unwrap();
            let Some(job) = database.next_queued_job().unwrap() else {
                break;
            };
            database
                .finish_job(&job.id, "completed", "out.png", "")
                .unwrap();
            requests.push(serde_json::from_str(&job.request_json).unwrap());
        }
        requests
    }

    fn mask_files(state: &AppState) -> Vec<String> {
        let root = crate::repair_mask::masks_root(&state.paths.next_root);
        let mut names: Vec<String> = fs::read_dir(root)
            .map(|entries| {
                entries
                    .map(|entry| entry.unwrap().file_name().into_string().unwrap())
                    .collect()
            })
            .unwrap_or_default();
        names.sort();
        names
    }

    #[test]
    fn paint_is_kept_per_photo() {
        let (_directory, state) = repairing_state();
        set_repair_strokes_checked(&state, "a", &[paint(10.0, 10.0), paint(50.0, 50.0)]).unwrap();
        assert_eq!(marks(&state, "a"), (2, 1));
        // An erase stroke alone is no mark.
        set_repair_strokes_checked(&state, "b", &[erase(5.0, 5.0)]).unwrap();
        assert_eq!(marks(&state, "b"), (0, 1));
        assert_eq!(marks(&state, "c"), (0, 0));
        assert_eq!(get_repair_strokes_checked(&state, "a").unwrap().len(), 2);
        assert_eq!(get_repair_strokes_checked(&state, "b").unwrap().len(), 1);
        assert!(get_repair_strokes_checked(&state, "c").unwrap().is_empty());
        set_repair_strokes_checked(&state, "a", &[paint(10.0, 10.0)]).unwrap();
        assert_eq!(marks(&state, "a"), (1, 2));
        set_repair_strokes_checked(&state, "a", &[]).unwrap();
        assert_eq!(marks(&state, "a"), (0, 3));
        assert!(get_repair_strokes_checked(&state, "a").unwrap().is_empty());
        assert!(get_repair_strokes_checked(&state, "missing").is_err());
    }

    #[test]
    fn paint_is_refused_while_a_job_waits() {
        let (_directory, state) = repairing_state();
        set_repair_strokes_checked(&state, "a", &[paint(1.0, 1.0)]).unwrap();
        start_jobs_checked(
            &state,
            StartBatchInput {
                media_ids: vec!["c".into()],
                ..estimate_input(4)
            },
        )
        .unwrap();
        let error = set_repair_strokes_checked(&state, "a", &[paint(1.0, 1.0), paint(9.0, 9.0)])
            .unwrap_err()
            .to_string();
        assert!(
            error.contains("Marks can be changed when the queue is done."),
            "{error}"
        );
        assert_eq!(get_repair_strokes_checked(&state, "a").unwrap().len(), 1);
        assert_eq!(marks(&state, "a"), (1, 1));
    }

    #[test]
    fn too_much_paint_is_refused() {
        let (_directory, state) = repairing_state();
        let many = vec![paint(1.0, 1.0); crate::repair_mask::MAX_STROKES + 1];
        let wide = RepairStroke {
            mode: StrokeMode::Paint,
            size: 5.0,
            points: vec![1.0; crate::repair_mask::MAX_NUMBERS + 2],
        };
        let huge_brush = RepairStroke {
            size: 1025.0,
            ..paint(1.0, 1.0)
        };
        let not_finite = RepairStroke {
            points: vec![1.0, f64::NAN],
            ..paint(1.0, 1.0)
        };
        for bad in [many, vec![wide], vec![huge_brush], vec![not_finite]] {
            assert!(set_repair_strokes_checked(&state, "a", &bad).is_err());
        }
        assert_eq!(marks(&state, "a"), (0, 0));
        assert!(get_repair_strokes_checked(&state, "a").unwrap().is_empty());
    }

    #[test]
    fn paint_on_a_video_is_refused() {
        let (_directory, state) = repairing_state();
        assert!(set_repair_strokes_checked(&state, "clip", &[paint(1.0, 1.0)]).is_err());
        assert!(set_repair_strokes_checked(&state, "missing", &[paint(1.0, 1.0)]).is_err());
        assert_eq!(marks(&state, "clip"), (0, 0));
    }

    #[test]
    fn removing_a_photo_removes_its_mask_file() {
        let (_directory, state) = repairing_state();
        for id in ["a", "b"] {
            set_repair_strokes_checked(&state, id, &[paint(10.0, 10.0)]).unwrap();
            let path = repair_mask_path(&state, id).unwrap().unwrap();
            assert!(path.is_file(), "{path:?}");
        }
        assert_eq!(mask_files(&state), ["a-1.png", "b-1.png"]);
        // A newer state of the paint replaces the older file on its first use.
        set_repair_strokes_checked(&state, "a", &[paint(20.0, 20.0)]).unwrap();
        repair_mask_path(&state, "a").unwrap().unwrap();
        assert_eq!(mask_files(&state), ["a-2.png", "b-1.png"]);
        assert!(repair_mask_path(&state, "c").unwrap().is_none());
        remove_media_checked(&state, "a").unwrap();
        assert_eq!(mask_files(&state), ["b-1.png"]);
        clear_media_checked(&state).unwrap();
        assert!(mask_files(&state).is_empty());
    }

    #[test]
    fn a_painted_photo_gets_the_stage_and_an_unpainted_one_does_not() {
        let (_directory, state) = repairing_state();
        set_repair_strokes_checked(&state, "a", &[paint(10.0, 10.0)]).unwrap();
        let mut both = estimate_input(4);
        both.media_ids = vec!["a".into(), "b".into()];
        both.batch_mode = true;
        both.step_stages = vec![repair_step()];
        start_jobs_checked(&state, both).unwrap();
        let requests = queued_requests(&state);
        assert_eq!(requests.len(), 2);
        let steps = |request: &serde_json::Value| -> Vec<String> {
            request["data"]["stages"]
                .as_array()
                .unwrap()
                .iter()
                .map(|stage| {
                    stage["step"]
                        .as_str()
                        .or_else(|| stage["fix"].as_str())
                        .or_else(|| stage["kind"].as_str())
                        .unwrap()
                        .to_owned()
                })
                .collect()
        };
        let painted = &requests[0];
        assert!(painted["data"]["image_path"]
            .as_str()
            .unwrap()
            .ends_with("a.png"));
        // First of all, ahead of the fixes, with the host's own mask.
        assert_eq!(steps(painted)[0], "repair");
        let mask = painted["data"]["stages"][0]["mask_path"].as_str().unwrap();
        assert!(mask.ends_with("masks/a-1.png") || mask.ends_with("masks\\a-1.png"));
        assert!(Path::new(mask).is_file());
        let plain = &requests[1];
        assert!(!steps(plain).contains(&"repair".to_owned()));
        assert_eq!(steps(plain).len(), steps(painted).len() - 1);
    }

    #[test]
    fn a_start_of_repair_alone_queues_only_painted_photos() {
        let (_directory, state) = repairing_state();
        let refused = start_jobs_checked(&state, repair_alone(&["a", "b", "c"]))
            .unwrap_err()
            .to_string();
        assert!(
            refused.contains("Paint over the marks on a photo first."),
            "{refused}"
        );
        assert!(queued_requests(&state).is_empty());
        set_repair_strokes_checked(&state, "b", &[paint(10.0, 10.0)]).unwrap();
        start_jobs_checked(&state, repair_alone(&["a", "b", "c"])).unwrap();
        let requests = queued_requests(&state);
        assert_eq!(requests.len(), 1);
        assert!(requests[0]["data"]["image_path"]
            .as_str()
            .unwrap()
            .ends_with("b.png"));
        assert!(requests[0]["data"]["output_path"]
            .as_str()
            .unwrap()
            .ends_with("b_repaired.png"));
        assert_eq!(requests[0]["data"]["stages"][0]["step"], "repair");
    }

    #[test]
    fn a_mask_path_from_the_window_is_refused() {
        let (_directory, state) = repairing_state();
        set_repair_strokes_checked(&state, "a", &[paint(10.0, 10.0)]).unwrap();
        let mut sent = repair_alone(&["a"]);
        sent.step_stages[0].mask_path = Some("/somewhere/mask.png".into());
        let error = start_jobs_checked(&state, sent).unwrap_err().to_string();
        assert!(
            error.contains("The mask of Dust & scratches is painted in LocalSR."),
            "{error}"
        );
        assert!(validate_step_stages(&[repair_step()], Some("image")).is_ok());
        assert!(validate_step_stages(&[repair_step()], Some("video")).is_err());
        assert!(queued_requests(&state).is_empty());
    }

    #[test]
    fn an_engine_without_the_feature_cannot_start_it() {
        let (_directory, state) = repairing_state();
        set_repair_strokes_checked(&state, "a", &[paint(10.0, 10.0)]).unwrap();
        lock(&state.engine).unwrap().as_mut().unwrap().features =
            vec!["image_multi_fix".into(), "stage_params_and_masks".into()];
        assert!(start_jobs_checked(&state, repair_alone(&["a"])).is_err());
        // A listed feature without the mask feature is not enough either.
        lock(&state.engine).unwrap().as_mut().unwrap().features = vec!["image_repair".into()];
        assert!(start_jobs_checked(&state, repair_alone(&["a"])).is_err());
    }

    #[test]
    fn the_repair_alone_is_named_repaired() {
        let alone = repair_alone(&["a"]);
        assert_eq!(output_ending(&alone), ("_repaired".to_owned(), "png"));
        let mut with_fix = alone.clone();
        with_fix.fix_stages = vec![crate::types::FixStageSelection {
            fix: "noise".into(),
            model_id: "denoise_realplksr_1x".into(),
            ..Default::default()
        }];
        assert_eq!(output_ending(&with_fix).0, "_denoised");
        let mut upscaled = alone;
        upscaled.task = "upscale".into();
        upscaled.model_id = "span_photo_x4".into();
        upscaled.output_scale = 2;
        assert_eq!(output_ending(&upscaled).0, "_upscaled_2x");
    }

    #[test]
    fn repair_comes_before_the_background_whatever_order_it_was_ticked_in() {
        let mut ticked = input("upscale");
        ticked.step_stages = vec![
            StepStageSelection {
                step: "remove_background".into(),
                model_id: Some("birefnet_general".into()),
                ..StepStageSelection::default()
            },
            StepStageSelection {
                step: "repair".into(),
                mask_path: Some("/masks/a-1.png".into()),
                ..StepStageSelection::default()
            },
        ];
        let stages = image_pipeline_stages(&[], &[], "span", "/m/span", None, "", false, &ticked);
        let sent: Vec<_> = stages
            .iter()
            .map(|stage| stage.step.clone().or_else(|| stage.kind.clone()).unwrap())
            .collect();
        assert_eq!(sent, ["repair", "remove_background", "upscale"]);
    }
}
