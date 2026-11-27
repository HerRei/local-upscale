//! Pure job-planning and `StartBatchInput` construction matching `plan.ts` and `startInput.ts`.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

use crate::{
    editing::EditModel,
    results::ResultUi,
    steps::{StepDefinition, StepPlacement, StepRegistry},
    types::{
        CapabilityInfo, CatalogManifest, CatalogModel, CatalogVideoModel, DeviceInfo,
        FixStageSelection, StartBatchInput, StepStageSelection, UiSettings,
    },
};

#[cfg_attr(not(test), allow(dead_code))]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Fit {
    Runs,
    Heavy,
    TooHeavy,
    Unknown,
}

/// Hardware fit from the catalog's memory estimate against the device's total memory.
#[cfg_attr(not(test), allow(dead_code))]
pub fn fit_for(vram_estimate_mb: u64, device: Option<&DeviceInfo>) -> Fit {
    let Some(device) = device else {
        return Fit::Unknown;
    };
    if device.total_memory == 0 || vram_estimate_mb == 0 {
        return Fit::Unknown;
    }
    let needed = vram_estimate_mb.saturating_mul(1024 * 1024);
    if needed > device.total_memory {
        return Fit::TooHeavy;
    }
    if (needed as f64) > (device.total_memory as f64 * 2.0) / 3.0 {
        return Fit::Heavy;
    }
    Fit::Runs
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct PlanStage {
    pub kind: String,
    pub role: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub fix: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub remembered: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub missing: Option<bool>,
    pub label: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub video_model_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub interpolation_model_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub audio_model_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub edit_model_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub custom: Option<String>,
    pub installed: bool,
    pub can_download: bool,
    pub needs_file: bool,
    pub size_bytes: u64,
}

#[derive(Clone, Debug, Default)]
pub struct PlanOptions<'a> {
    pub face_model: Option<&'a CatalogModel>,
    pub face_enabled: bool,
    pub using_temporal_video: bool,
    pub selected_video_model: Option<&'a CatalogVideoModel>,
    pub edit_model: Option<&'a EditModel>,
    pub has_audio: Option<bool>,
}

#[cfg_attr(not(test), allow(dead_code))]
#[derive(Clone, Debug)]
pub struct StartRequest<'a> {
    pub media_ids: Vec<String>,
    pub task: &'a str,
    pub batch_mode: bool,
    pub recipe_name: &'a str,
}

#[cfg_attr(not(test), allow(dead_code))]
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct JobPlan {
    pub stages: Vec<PlanStage>,
    pub start_input: StartBatchInput,
}

pub fn effective_video_task(settings: &UiSettings) -> &'static str {
    if settings.video_task == "denoise" {
        "denoise"
    } else {
        "upscale"
    }
}

pub fn is_restore_task(settings: &UiSettings) -> bool {
    settings.task == "denoise" || (settings.task == "video" && settings.video_task == "denoise")
}

pub fn default_using_temporal_video(settings: &UiSettings) -> bool {
    settings.task == "video"
        && !is_restore_task(settings)
        && settings.selected_video_model_id != "frame_by_frame"
}

/// Problems a 1x checkpoint fixes, derived from purposes when the catalog omits `fixes`.
pub fn fixes_of(model: &CatalogModel) -> Vec<String> {
    if !model.fixes.is_empty() {
        return model.fixes.clone();
    }
    if model.native_scale != 1 || model.purposes.iter().any(|purpose| purpose == "face") {
        return Vec::new();
    }
    let mut found = Vec::new();
    if model.purposes.iter().any(|purpose| purpose == "denoise") {
        found.push("noise".into());
    }
    if model.purposes.iter().any(|purpose| purpose == "deblur") {
        found.push("blur".into());
    }
    if model.purposes.iter().any(|purpose| purpose == "colorize") {
        found.push("colour".into());
    }
    if model
        .purposes
        .iter()
        .any(|purpose| purpose == "restoration")
        && found.is_empty()
    {
        found.push("jpeg".into());
    }
    found
}

pub fn rights_allow_preset(model: &CatalogModel) -> bool {
    model.commercial_use_allowed == Some(true)
}

pub fn fix_slot(fix: &str, quality: &str) -> String {
    format!("fix/{fix}/{quality}")
}

pub fn fix_quality(quality: &str) -> &'static str {
    if quality == "quick" {
        "quick"
    } else {
        "best"
    }
}

pub fn fits_fix(model: &CatalogModel, fix: &str) -> bool {
    model.native_scale == 1
        && !model.purposes.iter().any(|purpose| purpose == "face")
        && fixes_of(model).iter().any(|entry| entry == fix)
}

pub fn remembered_fix_model<'a>(
    catalog: &'a CatalogManifest,
    fix: &str,
    quality: &str,
    pins: &BTreeMap<String, String>,
) -> Option<&'a CatalogModel> {
    let id = pins.get(&fix_slot(fix, quality))?;
    let model = catalog.models.iter().find(|entry| &entry.model_id == id)?;
    (fits_fix(model, fix) && rights_allow_preset(model)).then_some(model)
}

pub fn resolve_audio_model<'a>(
    audio_models: &'a [CatalogVideoModel],
    model_id: &str,
) -> Option<&'a CatalogVideoModel> {
    if audio_models.is_empty() {
        return None;
    }
    if !model_id.is_empty() {
        if let Some(found) = audio_models.iter().find(|model| model.model_id == model_id) {
            return Some(found);
        }
    }
    audio_models.first()
}

pub fn interpolation_label(target: &str) -> Option<String> {
    match target {
        "x2" => Some("Interpolate 2×".into()),
        "x4" => Some("Interpolate 4×".into()),
        "fps60" => Some("Interpolate to 60 fps".into()),
        "fps120" => Some("Interpolate to 120 fps".into()),
        _ => None,
    }
}

pub fn listed_device_id(device_id: &str, devices: &[DeviceInfo]) -> String {
    if devices.is_empty() || devices.iter().any(|device| device.id == device_id) {
        return device_id.to_owned();
    }
    if devices.iter().any(|device| device.id == "cpu") {
        "cpu".into()
    } else {
        devices[0].id.clone()
    }
}

pub fn with_listed_device(settings: &UiSettings, capabilities: &CapabilityInfo) -> UiSettings {
    let device_id = listed_device_id(&settings.device_id, &capabilities.devices);
    if device_id == settings.device_id {
        return settings.clone();
    }
    let mut next = settings.clone();
    next.device_id = device_id;
    next
}

/// The media a task's step stages run on: photos, or frame-by-frame video frames.
pub fn step_stage_media(task: &str, using_temporal_video: bool) -> Option<&'static str> {
    match task {
        "upscale" | "denoise" => Some("image"),
        "video" if !using_temporal_video => Some("video"),
        _ => None,
    }
}

/// The step stages that run for these settings, in their order, with where each runs.
/// Steps this version does not know and steps for another media are left out.
pub fn active_step_stages<'a>(
    steps: &'a StepRegistry,
    settings: &'a UiSettings,
    using_temporal_video: bool,
) -> Vec<(&'a StepStageSelection, &'a StepDefinition, StepPlacement)> {
    let Some(media) = step_stage_media(&settings.task, using_temporal_video) else {
        return Vec::new();
    };
    settings
        .step_stages
        .iter()
        .filter_map(|selection| {
            let (step, placement) = steps.step_stage(&selection.step)?;
            step.media
                .iter()
                .any(|candidate| candidate == media)
                .then_some((selection, step, placement))
        })
        .collect()
}

/// Whether a catalog model is one of the models a step stage takes (its `model_task`).
pub fn serves_step(model: &CatalogModel, step: &StepDefinition) -> bool {
    step.model_task
        .as_deref()
        .is_some_and(|task| model.purposes.iter().any(|purpose| purpose == task))
}

/// The catalog model a step stage runs: the chosen one, when it serves the step's task.
pub fn step_stage_model<'a>(
    catalog: &'a CatalogManifest,
    step: &StepDefinition,
    selection: &StepStageSelection,
) -> Option<&'a CatalogModel> {
    let id = selection.chosen_model()?;
    catalog
        .models
        .iter()
        .find(|model| model.model_id == id && serves_step(model, step))
}

/// The format a job saves in: one that keeps the alpha a step sets (PNG, TIFF, WebP), or
/// PNG in place of any other.
pub fn output_format_keeping_alpha(
    steps: &StepRegistry,
    step_stages: &[StepStageSelection],
    format: &str,
) -> String {
    let sets_alpha = step_stages.iter().any(|selection| {
        steps
            .step_stage(&selection.step)
            .is_some_and(|(step, _)| step.output_alpha)
    });
    if sets_alpha && !matches!(format, "png" | "tif" | "webp") {
        "png".into()
    } else {
        format.to_owned()
    }
}

fn step_stage_row(
    catalog: &CatalogManifest,
    settings: &UiSettings,
    selection: &StepStageSelection,
    step: &StepDefinition,
) -> PlanStage {
    if step.needs_model {
        // Like a fix: the chosen model's row, or a row that waits for one.
        let model = step_stage_model(catalog, step, selection);
        return stage_for(
            catalog,
            settings,
            &step.id,
            "step",
            &step.label,
            model,
            None,
        )
        .unwrap_or_else(|| missing_stage(&step.id, "step", &step.label, None));
    }
    PlanStage {
        kind: step.id.clone(),
        role: "step".into(),
        fix: None,
        remembered: None,
        missing: None,
        label: step.label.clone(),
        model_id: None,
        video_model_id: None,
        interpolation_model_id: None,
        audio_model_id: None,
        edit_model_id: None,
        custom: None,
        installed: true,
        can_download: false,
        needs_file: false,
        size_bytes: 0,
    }
}

fn missing_stage(kind: &str, role: &str, label: &str, fix: Option<&str>) -> PlanStage {
    PlanStage {
        kind: kind.to_owned(),
        role: role.to_owned(),
        fix: fix.map(str::to_owned),
        remembered: None,
        missing: Some(true),
        label: label.to_owned(),
        model_id: None,
        video_model_id: None,
        interpolation_model_id: None,
        audio_model_id: None,
        edit_model_id: None,
        custom: None,
        installed: false,
        can_download: false,
        needs_file: false,
        size_bytes: 0,
    }
}

fn stage_for(
    catalog: &CatalogManifest,
    settings: &UiSettings,
    kind: &str,
    role: &str,
    label: &str,
    model: Option<&CatalogModel>,
    fix: Option<&str>,
) -> Option<PlanStage> {
    let model = model?;
    let remembered = fix.is_some_and(|fix_kind| {
        remembered_fix_model(
            catalog,
            fix_kind,
            fix_quality(&settings.quality),
            &settings.preset_pins,
        )
        .is_some_and(|entry| entry.model_id == model.model_id)
    });
    Some(PlanStage {
        kind: kind.to_owned(),
        role: role.to_owned(),
        fix: fix.map(str::to_owned),
        remembered: Some(remembered),
        missing: None,
        label: label.to_owned(),
        model_id: Some(model.model_id.clone()),
        video_model_id: None,
        interpolation_model_id: None,
        audio_model_id: None,
        edit_model_id: None,
        custom: None,
        installed: model.installed,
        can_download: !model.installed && model.automated_download_allowed,
        needs_file: !model.installed && !model.automated_download_allowed,
        size_bytes: if model.installed { 0 } else { model.size_bytes },
    })
}

/// Ordered stages the current settings will run, matching `plan.ts::resolvePlan`.
pub fn resolve_plan(
    catalog: &CatalogManifest,
    steps: &StepRegistry,
    settings: &UiSettings,
    options: &PlanOptions<'_>,
) -> Vec<PlanStage> {
    let mut stages = Vec::new();
    if settings.task == "edit" {
        if let Some(model) = options.edit_model {
            stages.push(PlanStage {
                kind: "edit".into(),
                role: "primary".into(),
                fix: None,
                remembered: None,
                missing: None,
                label: "Edit".into(),
                model_id: None,
                video_model_id: None,
                interpolation_model_id: None,
                audio_model_id: None,
                edit_model_id: Some(model.model_id.clone()),
                custom: None,
                installed: model.installed,
                can_download: !model.installed && model.automated_download_allowed,
                needs_file: !model.installed && !model.automated_download_allowed,
                size_bytes: if model.installed {
                    0
                } else {
                    model.total_size_bytes
                },
            });
        }
        return stages;
    }

    let by_id =
        |id: &str| -> Option<&CatalogModel> { catalog.models.iter().find(|m| m.model_id == id) };
    let step_stages = active_step_stages(steps, settings, options.using_temporal_video);
    for (selection, step, placement) in &step_stages {
        if *placement == StepPlacement::BeforeFixes {
            stages.push(step_stage_row(catalog, settings, selection, step));
        }
    }
    let restoring = is_restore_task(settings);
    let fix_stages: &[FixStageSelection] = if !options.using_temporal_video
        && (settings.task == "upscale" || settings.task == "video" || restoring)
    {
        &settings.fix_stages
    } else {
        &[]
    };
    let chip = if restoring {
        fix_stages.last().map(|selection| selection.fix.as_str())
    } else {
        None
    };
    let no_restore_model =
        restoring && chip.is_none() && settings.selected_model_id != "__custom__";

    let prefix_fixes = if restoring && !fix_stages.is_empty() {
        &fix_stages[..fix_stages.len() - 1]
    } else {
        fix_stages
    };
    for selection in prefix_fixes {
        let model = by_id(&selection.model_id).filter(|found| fits_fix(found, &selection.fix));
        let kind = steps.fix_stage_kind(model.map_or("", |m| m.stage.as_str()), &selection.fix);
        let label = steps.fix_row_label(&selection.fix);
        stages.push(
            stage_for(
                catalog,
                settings,
                kind,
                "fix",
                label,
                model,
                Some(&selection.fix),
            )
            .unwrap_or_else(|| missing_stage(kind, "fix", label, Some(&selection.fix))),
        );
    }

    if no_restore_model {
        // Same size with no chip selected runs only video options.
    } else if let (true, Some(video)) = (options.using_temporal_video, options.selected_video_model)
    {
        let label = if settings.video_target_resolution > 0 {
            "Upscale".into()
        } else {
            format!("Upscale ×{}", settings.output_scale)
        };
        stages.push(PlanStage {
            kind: "video".into(),
            role: "primary".into(),
            fix: None,
            remembered: None,
            missing: None,
            label,
            model_id: None,
            video_model_id: Some(video.model_id.clone()),
            interpolation_model_id: None,
            audio_model_id: None,
            edit_model_id: None,
            custom: None,
            installed: video.installed,
            can_download: !video.installed && video.automated_download_allowed,
            needs_file: !video.installed && !video.automated_download_allowed,
            size_bytes: if video.installed {
                0
            } else {
                video.total_size_bytes
            },
        });
    } else if settings.selected_model_id == "__custom__" {
        let custom_set = !settings.custom_model_path.is_empty();
        stages.push(PlanStage {
            kind: if restoring { "restore" } else { "upscale" }.into(),
            role: "primary".into(),
            fix: None,
            remembered: None,
            missing: None,
            label: if restoring {
                "Fix".into()
            } else {
                format!("Upscale ×{}", settings.output_scale)
            },
            model_id: None,
            video_model_id: None,
            interpolation_model_id: None,
            audio_model_id: None,
            edit_model_id: None,
            custom: Some(settings.custom_model_path.clone()),
            installed: custom_set,
            can_download: false,
            needs_file: !custom_set,
            size_bytes: 0,
        });
    } else {
        let primary = by_id(&settings.selected_model_id);
        let restore_label = if let Some(c) = chip {
            steps.fix_row_label(c)
        } else if primary.is_some_and(|p| p.purposes.iter().any(|purp| purp == "colorize")) {
            steps.fix_row_label("colour")
        } else {
            "Fix"
        };
        let stage_fix = if restoring {
            match (primary, chip) {
                (Some(p), Some(c)) if fixes_of(p).iter().any(|f| f == c) => Some(c),
                _ => None,
            }
        } else {
            None
        };
        let label = if restoring {
            restore_label.to_owned()
        } else {
            format!("Upscale ×{}", settings.output_scale)
        };
        if let Some(stage) = stage_for(
            catalog,
            settings,
            if restoring { "restore" } else { "upscale" },
            "primary",
            &label,
            primary,
            stage_fix,
        ) {
            stages.push(stage);
        } else if let Some(c) = chip {
            stages.push(missing_stage(
                "restore",
                "primary",
                steps.fix_row_label(c),
                Some(c),
            ));
        }
    }

    if options.face_enabled && options.face_model.is_some() && !restoring && !no_restore_model {
        if let Some(stage) = stage_for(
            catalog,
            settings,
            "face_restore",
            "face",
            "Faces",
            options.face_model,
            None,
        ) {
            stages.push(stage);
        }
    }

    if settings.task == "video" && !options.using_temporal_video && settings.deflicker {
        stages.push(PlanStage {
            kind: "deflicker".into(),
            role: "step".into(),
            fix: None,
            remembered: None,
            missing: None,
            label: "De-flicker".into(),
            model_id: None,
            video_model_id: None,
            interpolation_model_id: None,
            audio_model_id: None,
            edit_model_id: None,
            custom: None,
            installed: true,
            can_download: false,
            needs_file: false,
            size_bytes: 0,
        });
    }

    let target = if settings.video_interpolation.is_empty() {
        "off"
    } else {
        settings.video_interpolation.as_str()
    };
    if settings.task == "video" && !options.using_temporal_video && target != "off" {
        if let (Some(interpolation), Some(label)) = (
            catalog.interpolation_models.first(),
            interpolation_label(target),
        ) {
            let stage = PlanStage {
                kind: "interpolate".into(),
                role: "interpolate".into(),
                fix: None,
                remembered: None,
                missing: None,
                label,
                model_id: None,
                video_model_id: None,
                interpolation_model_id: Some(interpolation.model_id.clone()),
                audio_model_id: None,
                edit_model_id: None,
                custom: None,
                installed: interpolation.installed,
                can_download: !interpolation.installed && interpolation.automated_download_allowed,
                needs_file: !interpolation.installed && !interpolation.automated_download_allowed,
                size_bytes: if interpolation.installed {
                    0
                } else {
                    interpolation.total_size_bytes
                },
            };
            if settings.video_interpolation_order == "before_upscale" {
                stages.insert(0, stage);
            } else {
                stages.push(stage);
            }
        }
    }

    for (selection, step, placement) in &step_stages {
        if *placement == StepPlacement::AfterPrimary {
            stages.push(step_stage_row(catalog, settings, selection, step));
        }
    }

    let audio_model = resolve_audio_model(&catalog.audio_models, &settings.video_audio_model);
    if settings.task == "video" && settings.video_clean_audio && options.has_audio != Some(false) {
        if let Some(audio) = audio_model {
            stages.insert(
                0,
                PlanStage {
                    kind: "audio_denoise".into(),
                    role: "audio_denoise".into(),
                    fix: None,
                    remembered: None,
                    missing: None,
                    label: "Clean up audio".into(),
                    model_id: None,
                    video_model_id: None,
                    interpolation_model_id: None,
                    audio_model_id: Some(audio.model_id.clone()),
                    edit_model_id: None,
                    custom: None,
                    installed: audio.installed,
                    can_download: !audio.installed && audio.automated_download_allowed,
                    needs_file: !audio.installed && !audio.automated_download_allowed,
                    size_bytes: if audio.installed {
                        0
                    } else {
                        audio.total_size_bytes
                    },
                },
            );
        }
    }

    stages
}

/// Pure `StartBatchInput` builder matching `startInput.ts::buildStartInput`.
#[cfg_attr(not(test), allow(dead_code))]
pub fn build_start_input(
    steps: &StepRegistry,
    capabilities: &CapabilityInfo,
    raw_settings: &UiSettings,
    has_audio: Option<bool>,
    request: &StartRequest<'_>,
) -> StartBatchInput {
    let settings = with_listed_device(raw_settings, capabilities);
    let video_task = effective_video_task(&settings);
    let restoring = is_restore_task(&settings);
    let using_temporal_video = default_using_temporal_video(&settings);
    let image_fixes =
        if !using_temporal_video && settings.task != "edit" && !settings.task.is_empty() {
            settings.fix_stages.clone()
        } else {
            Vec::new()
        };
    let no_restore_model =
        restoring && image_fixes.is_empty() && settings.selected_model_id != "__custom__";
    let audio_disabled = settings.task == "video" && has_audio == Some(false);
    let step_stages: Vec<StepStageSelection> =
        active_step_stages(steps, &settings, using_temporal_video)
            .into_iter()
            .map(|(selection, _, _)| selection.clone())
            .collect();

    StartBatchInput {
        name_by_model: false,
        ui: Some(ResultUi {
            quality: settings.quality.clone(),
            content: settings.content.clone(),
            recipe_name: request.recipe_name.to_owned(),
            fixes: settings.fixes.clone(),
        }),
        media_ids: request.media_ids.clone(),
        batch_mode: request.batch_mode,
        task: request.task.to_owned(),
        video_task: video_task.to_owned(),
        model_id: if no_restore_model {
            String::new()
        } else {
            settings.selected_model_id.clone()
        },
        video_model_id: settings.selected_video_model_id.clone(),
        fix_stages: if using_temporal_video {
            Vec::new()
        } else {
            image_fixes
        },
        output_format: output_format_keeping_alpha(steps, &step_stages, &settings.output_format),
        step_stages,
        custom_model_path: settings.custom_model_path.clone(),
        output_directory: settings.output_directory.clone(),
        output_scale: settings.output_scale,
        preserve_metadata: settings.preserve_metadata,
        jpeg_quality: settings.jpeg_quality,
        device: settings.device_id.clone(),
        tile_size: settings.tile_size,
        halo: settings.halo,
        precision: settings.precision.clone(),
        safe_memory: settings.safe_memory,
        neural_engine: settings.neural_engine,
        deflicker: if using_temporal_video {
            false
        } else {
            settings.deflicker
        },
        deflicker_window: settings.deflicker_window,
        video_container: settings.video_container.clone(),
        video_codec: if settings.video_codec.is_empty() {
            "auto".into()
        } else {
            settings.video_codec.clone()
        },
        external_ffmpeg_path: settings.external_ffmpeg_path.clone(),
        video_hdr_mode: if using_temporal_video || settings.video_hdr_mode.is_empty() {
            "tone_map".into()
        } else {
            settings.video_hdr_mode.clone()
        },
        video_target_resolution: settings.video_target_resolution,
        video_low_memory: settings.video_low_memory,
        video_crf: settings.video_crf,
        enable_face_model: false,
        face_fidelity: settings.face_fidelity,
        enable_live_preview: settings.enable_live_preview,
        allow_unsafe_pickle_model: settings.allow_unsafe_pickle_model,
        video_interpolation: if settings.task == "video" && !using_temporal_video {
            if settings.video_interpolation.is_empty() {
                "off".into()
            } else {
                settings.video_interpolation.clone()
            }
        } else {
            "off".into()
        },
        video_interpolation_order: if settings.video_interpolation_order.is_empty() {
            "after_upscale".into()
        } else {
            settings.video_interpolation_order.clone()
        },
        video_clean_audio: settings.task == "video"
            && !audio_disabled
            && settings.video_clean_audio,
        video_audio_model: settings.video_audio_model.clone(),
        output_bit_depth: settings.output_bit_depth,
    }
}

/// Computes both the UI plan rows and the `StartBatchInput` from pure inputs.
#[cfg_attr(not(test), allow(dead_code))]
pub fn plan_job(
    catalog: &CatalogManifest,
    steps: &StepRegistry,
    capabilities: &CapabilityInfo,
    settings: &UiSettings,
    options: &PlanOptions<'_>,
    request: &StartRequest<'_>,
) -> JobPlan {
    let effective = with_listed_device(settings, capabilities);
    JobPlan {
        stages: resolve_plan(catalog, steps, &effective, options),
        start_input: build_start_input(steps, capabilities, &effective, options.has_audio, request),
    }
}

#[cfg(test)]
mod tests {
    use std::collections::HashSet;

    use super::*;

    const PARITY_FIXTURE_JSON: &str = include_str!("../tests/fixtures/job_plan_cases.json");
    const EDIT_CATALOG_JSON: &str = include_str!("../../../src/localsr/core/edit_catalog.json");

    #[derive(Deserialize)]
    struct ParityFixture {
        schema_version: u32,
        cases: Vec<ParityCase>,
    }

    #[derive(Deserialize)]
    struct ParityCaseOptions {
        face_model_id: Option<String>,
        face_enabled: bool,
        using_temporal_video: bool,
        selected_video_model_id: Option<String>,
        edit_model_id: Option<String>,
        has_audio: Option<bool>,
    }

    #[derive(Deserialize)]
    struct ParityStartRequest {
        media_ids: Vec<String>,
        task: String,
        batch_mode: bool,
        recipe_name: String,
    }

    #[derive(Deserialize)]
    struct ParityCase {
        id: String,
        installed_ids: Vec<String>,
        /// Steps and catalog models that exist only in this case.
        #[serde(default)]
        extra_steps: Vec<StepDefinition>,
        #[serde(default)]
        extra_models: Vec<CatalogModel>,
        capabilities: CapabilityInfo,
        settings: UiSettings,
        options: ParityCaseOptions,
        start_request: ParityStartRequest,
        expected_plan: Vec<PlanStage>,
        expected_start_input: StartBatchInput,
    }

    #[test]
    fn rust_job_plan_matches_typescript_parity_fixture_across_all_cases() {
        let fixture: ParityFixture = serde_json::from_str(PARITY_FIXTURE_JSON).unwrap();
        assert_eq!(fixture.schema_version, 1);
        assert!(
            fixture.cases.len() >= 300,
            "expected at least 300 parity cases, got {}",
            fixture.cases.len()
        );

        let base_catalog = crate::catalog::bundled_catalog().unwrap();
        let base_edit_models: Vec<EditModel> = serde_json::from_str(EDIT_CATALOG_JSON).unwrap();
        let steps = crate::steps::registry();

        for case in &fixture.cases {
            let installed: HashSet<&str> = case.installed_ids.iter().map(String::as_str).collect();
            let mut catalog = base_catalog.clone();
            catalog.models.extend(case.extra_models.iter().cloned());
            let case_steps = steps.with_steps(case.extra_steps.clone());
            let steps = &case_steps;
            for model in &mut catalog.models {
                model.installed = installed.contains(model.model_id.as_str());
            }
            for model in catalog
                .video_models
                .iter_mut()
                .chain(catalog.interpolation_models.iter_mut())
                .chain(catalog.audio_models.iter_mut())
            {
                model.total_size_bytes = model.files.iter().map(|file| file.size_bytes).sum();
                model.installed = installed.contains(model.model_id.as_str());
            }
            let mut edit_models = base_edit_models.clone();
            for model in &mut edit_models {
                model.total_size_bytes = model.files.iter().map(|file| file.size_bytes).sum();
                model.installed = installed.contains(model.model_id.as_str());
            }

            let face_model = case
                .options
                .face_model_id
                .as_deref()
                .and_then(|id| catalog.models.iter().find(|m| m.model_id == id));
            let selected_video_model = case
                .options
                .selected_video_model_id
                .as_deref()
                .and_then(|id| catalog.video_models.iter().find(|m| m.model_id == id));
            let edit_model = case
                .options
                .edit_model_id
                .as_deref()
                .and_then(|id| edit_models.iter().find(|m| m.model_id == id));

            let options = PlanOptions {
                face_model,
                face_enabled: case.options.face_enabled,
                using_temporal_video: case.options.using_temporal_video,
                selected_video_model,
                edit_model,
                has_audio: case.options.has_audio,
            };
            let request = StartRequest {
                media_ids: case.start_request.media_ids.clone(),
                task: &case.start_request.task,
                batch_mode: case.start_request.batch_mode,
                recipe_name: &case.start_request.recipe_name,
            };

            let actual = plan_job(
                &catalog,
                steps,
                &case.capabilities,
                &case.settings,
                &options,
                &request,
            );
            assert_eq!(
                actual.stages, case.expected_plan,
                "plan mismatch on case {}",
                case.id
            );
            assert_eq!(
                actual.start_input, case.expected_start_input,
                "start_input mismatch on case {}",
                case.id
            );
        }
    }

    #[test]
    fn fit_for_and_purpose_fallbacks_match_ts_helpers() {
        let device = DeviceInfo {
            id: "mps".into(),
            device_type: "mps".into(),
            name: "Apple GPU".into(),
            total_memory: 8 * 1024 * 1024 * 1024,
            ..DeviceInfo::default()
        };
        assert_eq!(fit_for(1200, Some(&device)), Fit::Runs);
        assert_eq!(fit_for(6000, Some(&device)), Fit::Heavy);
        assert_eq!(fit_for(9000, Some(&device)), Fit::TooHeavy);
        assert_eq!(fit_for(0, Some(&device)), Fit::Unknown);
        assert_eq!(fit_for(1200, None), Fit::Unknown);

        let mut model = crate::catalog::bundled_catalog()
            .unwrap()
            .models
            .into_iter()
            .find(|m| m.model_id == "fbcnn_color")
            .unwrap();
        model.fixes.clear();
        assert_eq!(fixes_of(&model), vec!["jpeg"]);
        model.purposes = vec!["denoise".into(), "deblur".into(), "colorize".into()];
        assert_eq!(fixes_of(&model), vec!["noise", "blur", "colour"]);
        model.purposes.push("face".into());
        assert!(fixes_of(&model).is_empty());
    }

    #[test]
    fn deflicker_stage_matches_frame_by_frame_video_order_and_omits_temporal_and_photo() {
        let catalog = crate::catalog::bundled_catalog().unwrap();
        let steps = crate::steps::registry();

        let base_video = UiSettings {
            task: "video".into(),
            video_task: "upscale".into(),
            selected_video_model_id: "frame_by_frame".into(),
            selected_model_id: "span_photo_x4".into(),
            output_scale: 4,
            deflicker: true,
            video_interpolation: "x2".into(),
            video_interpolation_order: "after_upscale".into(),
            ..UiSettings::default()
        };
        let after_plan = resolve_plan(&catalog, steps, &base_video, &PlanOptions::default());
        assert_eq!(
            after_plan
                .iter()
                .map(|stage| stage.kind.as_str())
                .collect::<Vec<_>>(),
            vec!["upscale", "deflicker", "interpolate"]
        );
        assert_eq!(
            after_plan[1],
            PlanStage {
                kind: "deflicker".into(),
                role: "step".into(),
                fix: None,
                remembered: None,
                missing: None,
                label: "De-flicker".into(),
                model_id: None,
                video_model_id: None,
                interpolation_model_id: None,
                audio_model_id: None,
                edit_model_id: None,
                custom: None,
                installed: true,
                can_download: false,
                needs_file: false,
                size_bytes: 0,
            }
        );

        let before_video = UiSettings {
            video_interpolation_order: "before_upscale".into(),
            ..base_video.clone()
        };
        let before_plan = resolve_plan(&catalog, steps, &before_video, &PlanOptions::default());
        assert_eq!(
            before_plan
                .iter()
                .map(|stage| stage.kind.as_str())
                .collect::<Vec<_>>(),
            vec!["interpolate", "upscale", "deflicker"]
        );

        let seedvr2 = catalog
            .video_models
            .iter()
            .find(|model| model.model_id == "seedvr2_3b")
            .unwrap();
        let temporal_plan = resolve_plan(
            &catalog,
            steps,
            &UiSettings {
                selected_video_model_id: "seedvr2_3b".into(),
                ..base_video.clone()
            },
            &PlanOptions {
                using_temporal_video: true,
                selected_video_model: Some(seedvr2),
                ..PlanOptions::default()
            },
        );
        assert!(temporal_plan.iter().all(|stage| stage.kind != "deflicker"));

        let photo_plan = resolve_plan(
            &catalog,
            steps,
            &UiSettings {
                task: "upscale".into(),
                ..base_video
            },
            &PlanOptions::default(),
        );
        assert!(photo_plan.iter().all(|stage| stage.kind != "deflicker"));
    }

    /// A catalog model for a test-only step's task, made from a shipped 1x model.
    fn task_model(id: &str, task: &str, installed: bool, downloadable: bool) -> CatalogModel {
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
        model.size_bytes = 4_321;
        model.installed = installed;
        model.automated_download_allowed = downloadable;
        model
    }

    #[test]
    fn a_step_with_a_model_plans_like_a_fix_and_a_matte_step_keeps_a_transparent_format() {
        use crate::types::StepStageSelection;

        let steps = crate::steps::registry().with_steps(vec![crate::steps::testing::cutout()]);
        let mut catalog = crate::catalog::bundled_catalog().unwrap();
        catalog.models.extend([
            task_model("cutout_ready", "test_cutout", true, true),
            task_model("cutout_download", "test_cutout", false, true),
            task_model("cutout_manual", "test_cutout", false, false),
            task_model("other_task", "test_other", true, true),
        ]);
        for model in &mut catalog.models {
            if model.model_id == "span_photo_x4" {
                model.installed = true;
            }
        }
        let cutout = |model_id: Option<&str>| StepStageSelection {
            step: "test_cutout".into(),
            model_id: model_id.map(str::to_owned),
            ..StepStageSelection::default()
        };
        let settings = |model_id: Option<&str>, format: &str| UiSettings {
            task: "upscale".into(),
            selected_model_id: "span_photo_x4".into(),
            output_scale: 4,
            output_format: format.into(),
            step_stages: vec![cutout(model_id)],
            ..UiSettings::default()
        };
        let request = StartRequest {
            media_ids: vec!["m".into()],
            task: "upscale",
            batch_mode: false,
            recipe_name: "",
        };
        let plan = |settings: &UiSettings| {
            plan_job(
                &catalog,
                &steps,
                &CapabilityInfo::default(),
                settings,
                &PlanOptions::default(),
                &request,
            )
        };

        let ready = plan(&settings(Some("cutout_ready"), "jpg"));
        assert_eq!(
            ready
                .stages
                .iter()
                .map(|stage| stage.kind.as_str())
                .collect::<Vec<_>>(),
            vec!["test_cutout", "upscale"]
        );
        let row = &ready.stages[0];
        assert_eq!(
            (
                row.role.as_str(),
                row.label.as_str(),
                row.model_id.as_deref()
            ),
            ("step", "Test cut-out", Some("cutout_ready"))
        );
        assert!(row.installed && !row.can_download && row.missing.is_none());
        assert_eq!(ready.start_input.output_format, "png");
        assert_eq!(
            ready.start_input.step_stages,
            vec![cutout(Some("cutout_ready"))]
        );

        let download = &plan(&settings(Some("cutout_download"), "png")).stages[0];
        assert!(!download.installed && download.can_download && !download.needs_file);
        assert_eq!(download.size_bytes, 4_321);
        let manual = &plan(&settings(Some("cutout_manual"), "png")).stages[0];
        assert!(!manual.installed && !manual.can_download && manual.needs_file);
        for missing in [None, Some(""), Some("unknown"), Some("other_task")] {
            let row = &plan(&settings(missing, "png")).stages[0];
            assert_eq!(row.missing, Some(true), "{missing:?}");
            assert!(!row.installed && row.model_id.is_none() && row.fix.is_none());
        }

        for (format, saved) in [
            ("jpg", "png"),
            ("heic", "png"),
            ("png", "png"),
            ("tif", "tif"),
            ("webp", "webp"),
        ] {
            assert_eq!(
                plan(&settings(Some("cutout_ready"), format))
                    .start_input
                    .output_format,
                saved
            );
            assert_eq!(
                output_format_keeping_alpha(&steps, &[cutout(None)], format),
                saved
            );
        }
        assert_eq!(output_format_keeping_alpha(&steps, &[], "jpg"), "jpg");
        let grain = StepStageSelection {
            step: "grain".into(),
            ..StepStageSelection::default()
        };
        assert_eq!(output_format_keeping_alpha(&steps, &[grain], "jpg"), "jpg");

        // Restore with nothing picked runs the step alone.
        let alone = UiSettings {
            task: "denoise".into(),
            selected_model_id: String::new(),
            output_scale: 1,
            ..settings(Some("cutout_ready"), "jpg")
        };
        let alone_plan = plan_job(
            &catalog,
            &steps,
            &CapabilityInfo::default(),
            &alone,
            &PlanOptions::default(),
            &StartRequest {
                task: "denoise",
                ..request.clone()
            },
        );
        assert_eq!(
            alone_plan
                .stages
                .iter()
                .map(|stage| stage.kind.as_str())
                .collect::<Vec<_>>(),
            vec!["test_cutout"]
        );
        assert_eq!(alone_plan.start_input.model_id, "");
        assert!(alone_plan.start_input.fix_stages.is_empty());
        assert_eq!(alone_plan.start_input.output_format, "png");
    }

    #[test]
    fn step_stages_join_the_plan_and_start_input_where_their_media_runs() {
        use crate::types::StepStageSelection;

        let catalog = crate::catalog::bundled_catalog().unwrap();
        let steps = crate::steps::registry();
        let capabilities = CapabilityInfo::default();
        let grain = StepStageSelection {
            step: "grain".into(),
            params: serde_json::json!({"strength": 40, "seed": 7})
                .as_object()
                .cloned(),
            mask_path: Some("/in/mask.png".into()),
            model_id: None,
        };
        let unknown = StepStageSelection {
            step: "tool_from_a_newer_version".into(),
            ..StepStageSelection::default()
        };
        let face = catalog
            .models
            .iter()
            .find(|model| model.model_id == "hat_l_x4_face")
            .unwrap();
        let photo = UiSettings {
            task: "upscale".into(),
            selected_model_id: "span_photo_x4".into(),
            output_scale: 4,
            fix_stages: vec![FixStageSelection {
                fix: "noise".into(),
                model_id: "denoise_realplksr_1x".into(),
                ..FixStageSelection::default()
            }],
            step_stages: vec![unknown.clone(), grain.clone()],
            ..UiSettings::default()
        };
        let request = |task: &'static str| StartRequest {
            media_ids: vec!["m".into()],
            task,
            batch_mode: false,
            recipe_name: "",
        };
        let with_face = PlanOptions {
            face_model: Some(face),
            face_enabled: true,
            ..PlanOptions::default()
        };

        let plan = plan_job(
            &catalog,
            steps,
            &capabilities,
            &photo,
            &with_face,
            &request("upscale"),
        );
        assert_eq!(
            plan.stages
                .iter()
                .map(|stage| stage.kind.as_str())
                .collect::<Vec<_>>(),
            vec!["restore", "upscale", "face_restore", "grain"]
        );
        assert_eq!(
            plan.stages[3],
            PlanStage {
                kind: "grain".into(),
                role: "step".into(),
                fix: None,
                remembered: None,
                missing: None,
                label: "Film grain".into(),
                model_id: None,
                video_model_id: None,
                interpolation_model_id: None,
                audio_model_id: None,
                edit_model_id: None,
                custom: None,
                installed: true,
                can_download: false,
                needs_file: false,
                size_bytes: 0,
            }
        );
        assert_eq!(plan.start_input.step_stages, vec![grain.clone()]);

        let restore = UiSettings {
            task: "denoise".into(),
            selected_model_id: "denoise_realplksr_1x".into(),
            output_scale: 1,
            ..photo.clone()
        };
        let restore_plan = plan_job(
            &catalog,
            steps,
            &capabilities,
            &restore,
            &PlanOptions::default(),
            &request("denoise"),
        );
        assert_eq!(
            restore_plan
                .stages
                .iter()
                .map(|stage| stage.kind.as_str())
                .collect::<Vec<_>>(),
            vec!["restore", "grain"]
        );
        assert_eq!(restore_plan.start_input.step_stages, vec![grain.clone()]);

        // Grain is for photos: a frame-by-frame video, a temporal model and Edit run none.
        for (settings, options, task) in [
            (
                UiSettings {
                    task: "video".into(),
                    selected_video_model_id: "frame_by_frame".into(),
                    ..photo.clone()
                },
                PlanOptions::default(),
                "video",
            ),
            (
                UiSettings {
                    task: "edit".into(),
                    ..photo.clone()
                },
                PlanOptions::default(),
                "edit",
            ),
        ] {
            let other = plan_job(
                &catalog,
                steps,
                &capabilities,
                &settings,
                &options,
                &request(task),
            );
            assert!(
                other.stages.iter().all(|stage| stage.kind != "grain"),
                "{task}"
            );
            assert!(other.start_input.step_stages.is_empty(), "{task}");
        }
        let temporal = UiSettings {
            task: "video".into(),
            selected_video_model_id: "seedvr2_3b".into(),
            ..photo
        };
        assert!(
            build_start_input(steps, &capabilities, &temporal, None, &request("video"))
                .step_stages
                .is_empty()
        );
    }
}
