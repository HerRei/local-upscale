use std::{fs, path::Path};

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::{
    error::{AppError, AppResult},
    paths::AppPaths,
    types::{
        is_valid_recipe_list_id, BenchmarkResult, CatalogManifest, Recipe, UiSettings,
        MAX_RECIPE_PINS, MAX_RECIPE_RECENT,
    },
};

#[derive(Clone, Debug, Deserialize, Serialize)]
struct SettingsDocument {
    schema_version: u32,
    ui: UiSettings,
    recipes: Vec<Recipe>,
}

/// The copy a Windows save keeps while it replaces the file (see `replace_settings_file`).
fn previous_copy(paths: &AppPaths) -> std::path::PathBuf {
    paths.settings.with_extension("json.previous")
}

/// The file the settings come from: the settings file, or, when a save stopped between
/// its renames and left none, the previous copy. Whatever this names is protected: it is
/// never replaced while it cannot be read.
fn effective_document_path(paths: &AppPaths) -> Option<std::path::PathBuf> {
    [paths.settings.clone(), previous_copy(paths)]
        .into_iter()
        .find(|path| path.exists())
}

pub fn has_existing_settings(paths: &AppPaths) -> bool {
    effective_document_path(paths).is_some() || paths.legacy_settings.exists()
}

pub fn load(paths: &AppPaths) -> (UiSettings, Vec<Recipe>) {
    // A save that stopped between its two renames leaves no settings file but the
    // previous copy, which holds the last complete settings.
    let document = effective_document_path(paths).and_then(|path| read_document(&path));
    if let Some(mut document) = document {
        adopt_automatic_video_format(&mut document.ui);
        let catalog = crate::catalog::bundled_catalog().unwrap_or_else(|_| CatalogManifest {
            schema_version: 0,
            catalog_revision: String::new(),
            models: Vec::new(),
            video_models: Vec::new(),
            interpolation_models: Vec::new(),
            audio_models: Vec::new(),
        });
        keep_runnable_step_stages(&mut document.ui, &catalog);
        keep_valid_recipe_lists(&mut document.ui);
        return (document.ui, document.recipes);
    }

    let mut settings = UiSettings {
        output_directory: paths.default_output.to_string_lossy().into_owned(),
        ..UiSettings::default()
    };
    if paths.legacy_settings.exists() || effective_document_path(paths).is_some() {
        settings.privacy_choice_made = true;
    }
    if !is_future_schema(paths) {
        if let Ok(contents) = fs::read_to_string(&paths.legacy_settings) {
            if let Ok(value) = serde_json::from_str::<Value>(&contents) {
                import_legacy_settings(&mut settings, &value);
            }
        }
    }
    (settings, Vec::new())
}

/// AV1 was the default before "auto" picked the fastest encoder (the Mac's
/// hardware HEVC). Move that default once; a later explicit AV1 choice stays.
fn adopt_automatic_video_format(settings: &mut UiSettings) {
    if !settings.automatic_video_format {
        if settings.video_codec == "av1" {
            settings.video_codec = "auto".into();
        }
        settings.automatic_video_format = true;
    }
}

/// A newer LocalSR may have saved steps, parameter values or step models this version does
/// not know; keeping them would make every later save of these settings fail its validation,
/// or leave a step waiting for a model this catalog cannot offer.
fn keep_runnable_step_stages(settings: &mut UiSettings, catalog: &CatalogManifest) {
    let steps = crate::steps::registry();
    let mut seen = std::collections::HashSet::new();
    settings.step_stages.retain(|stage| {
        let Ok(step) = steps.validate_step_stage(
            &stage.step,
            stage.params.as_ref(),
            stage.mask_path.as_deref(),
        ) else {
            return false;
        };
        let model_known = match stage.chosen_model() {
            None => true,
            Some(_) if !step.needs_model => false,
            Some(_) => crate::job_plan::step_stage_model(catalog, step, stage).is_some(),
        };
        model_known && seen.insert(stage.step.clone())
    });
}

/// A newer LocalSR may allow longer lists or other ids; keeping them would make every later
/// save of these settings fail its validation.
fn keep_valid_recipe_lists(settings: &mut UiSettings) {
    for (list, limit) in [
        (&mut settings.recipe_pins, MAX_RECIPE_PINS),
        (&mut settings.recipe_recent, MAX_RECIPE_RECENT),
    ] {
        let mut seen = std::collections::HashSet::new();
        list.retain(|id| is_valid_recipe_list_id(id) && seen.insert(id.clone()));
        list.truncate(limit);
    }
}

fn read_document(path: &Path) -> Option<SettingsDocument> {
    let contents = fs::read_to_string(path).ok()?;
    parse_document(&contents)
}

fn parse_document(contents: &str) -> Option<SettingsDocument> {
    let mut value: Value = serde_json::from_str(contents).ok()?;
    match value.get("schema_version")?.as_u64()? {
        1 => migrate_v1(&mut value)?,
        SCHEMA_VERSION => {}
        // A newer LocalSR wrote this; never read or overwrite it with defaults.
        _ => return None,
    }
    pin_the_first_recipes_of_an_older_file(&mut value);
    serde_json::from_value(value).ok()
}

/// Before pins, the Presets menu gave its shortcuts to the first recipes in storage order.
/// A file from then pins those, so Cmd+3 to Cmd+9 keep their targets. An empty list that
/// was saved stays empty.
fn pin_the_first_recipes_of_an_older_file(value: &mut Value) {
    let first: Vec<Value> = value
        .get("recipes")
        .and_then(Value::as_array)
        .map(|recipes| {
            recipes
                .iter()
                .take(crate::native_menu::SHORTCUT_PINS)
                .filter_map(|recipe| recipe.get("id")?.as_str())
                .map(|id| Value::String(id.to_owned()))
                .collect()
        })
        .unwrap_or_default();
    if let Some(ui) = value.get_mut("ui").and_then(Value::as_object_mut) {
        ui.entry("recipe_pins").or_insert(Value::Array(first));
    }
}

/// Schema 2 replaced the single `preprocess_model_id` with the ordered `fix_stages`.
const SCHEMA_VERSION: u64 = 2;

/// Version 1 ran at most one fix: the resolved `preprocess_model_id` before an upscale, or
/// in Restore the selected model when a chip was on. Only that becomes a fix stage; the old
/// chip list is not an execution list, so nothing else is made up from it. Edit left the
/// image task's setup in place, so it migrates as the task it came from.
fn migrate_v1(value: &mut Value) -> Option<()> {
    let ui = value.get_mut("ui")?.as_object_mut()?;
    if !ui.contains_key("fix_stages") {
        let text = |key: &str| ui.get(key).and_then(Value::as_str).unwrap_or("").to_owned();
        let chips: Vec<String> = ui
            .get("fixes")
            .and_then(Value::as_array)
            .map(|fixes| {
                fixes
                    .iter()
                    .filter_map(Value::as_str)
                    .filter(|fix| crate::steps::registry().is_valid_fix_stage(fix))
                    .map(str::to_owned)
                    .collect()
            })
            .unwrap_or_default();
        let catalog = crate::catalog::bundled_catalog().ok()?;
        let model_fixes = |id: &str| -> Vec<String> {
            catalog
                .models
                .iter()
                .find(|model| model.model_id == id && model.native_scale == 1)
                .map(|model| model.fixes.clone())
                .unwrap_or_default()
        };
        // Restore's model was the fix itself when a chip it fixes was on.
        let restore = || {
            let id = text("selected_model_id");
            let fixes = model_fixes(&id);
            chips
                .iter()
                .find(|chip| fixes.contains(chip))
                .map(|fix| (fix.clone(), id))
        };
        let upscale = || {
            let id = text("preprocess_model_id");
            let fixes = model_fixes(&id);
            // The chip names the problem; a model that fixes it, or a model the catalog
            // no longer lists, keeps that name. Otherwise the model's own problem does.
            let fix = chips
                .iter()
                .find(|chip| fixes.is_empty() || fixes.contains(chip))
                .or_else(|| fixes.first())
                .cloned();
            fix.filter(|_| !id.is_empty()).map(|fix| (fix, id))
        };
        let stage = match text("task").as_str() {
            "video" => None,
            "denoise" => restore(),
            "upscale" => upscale(),
            // Edit kept the image task's model and chips for the way back. Only Restore
            // selects a 1x model, so that model says which task Edit came from.
            _ if !model_fixes(&text("selected_model_id")).is_empty() => restore(),
            _ => upscale(),
        };
        let stages = stage
            .map(|(fix, model_id)| serde_json::json!([{"fix": fix, "model_id": model_id}]))
            .unwrap_or_else(|| serde_json::json!([]));
        ui.insert("fix_stages".into(), stages);
    }
    // The chips now show the fixes that run, plus Faces.
    let mut fixes: Vec<Value> = ui
        .get("fix_stages")?
        .as_array()?
        .iter()
        .map(|stage| stage.get("fix").cloned().unwrap_or(Value::Null))
        .collect();
    if ui
        .get("fixes")
        .and_then(Value::as_array)
        .is_some_and(|old| old.iter().any(|fix| fix == "faces"))
    {
        fixes.push(Value::String("faces".into()));
    }
    ui.insert("fixes".into(), Value::Array(fixes));
    ui.remove("preprocess_model_id");
    value["schema_version"] = Value::from(SCHEMA_VERSION);
    Some(())
}

fn import_legacy_settings(settings: &mut UiSettings, value: &Value) {
    let Some(object) = value.as_object() else {
        return;
    };
    if let Some(value) = object.get("selected_model_id").and_then(Value::as_str) {
        settings.selected_model_id = value.into();
    }
    if let Some(value) = object.get("custom_model_path").and_then(Value::as_str) {
        settings.custom_model_path = value.into();
    }
    if let Some(value) = object.get("output_directory").and_then(Value::as_str) {
        settings.output_directory = value.into();
    }
    for (key, target) in [
        ("output_scale", &mut settings.output_scale),
        ("jpeg_quality", &mut settings.jpeg_quality),
        ("tile_size", &mut settings.tile_size),
        ("halo", &mut settings.halo),
    ] {
        if let Some(value) = object.get(key).and_then(Value::as_u64) {
            *target = value as u32;
        }
    }
    for (key, target) in [
        ("preserve_metadata", &mut settings.preserve_metadata),
        ("safe_memory", &mut settings.safe_memory),
    ] {
        if let Some(value) = object.get(key).and_then(Value::as_bool) {
            *target = value;
        }
    }
    if let Some(value) = object.get("device_id").and_then(Value::as_str) {
        settings.device_id = value.into();
    }
    if let Some(value) = object.get("precision").and_then(Value::as_str) {
        settings.precision = value.into();
    }
    if let Some(index) = object.get("format_index").and_then(Value::as_u64) {
        settings.output_format = ["png", "jpg", "tif", "webp"]
            .get(index as usize)
            .copied()
            .unwrap_or("png")
            .into();
    }
}

pub fn save(paths: &AppPaths, settings: &UiSettings, recipes: &[Recipe]) -> AppResult<()> {
    // Never overwrite an unreadable or future-version document with defaults. A version-1
    // file is kept by the profile backup taken when this version first started.
    if is_future_schema(paths) {
        return Ok(());
    }
    if needs_recovery(paths) {
        return Err(AppError::Config("Settings need recovery. The original file has been preserved; open Check for Updates… and press Recover settings before saving changes.".into()));
    }
    fs::create_dir_all(&paths.next_root)?;
    let document = SettingsDocument {
        schema_version: SCHEMA_VERSION as u32,
        ui: settings.clone(),
        recipes: recipes.to_vec(),
    };
    let bytes = serde_json::to_vec_pretty(&document)?;
    let temporary = paths.settings.with_extension("json.tmp");
    fs::write(&temporary, bytes)?;
    replace_settings_file(&temporary, &paths.settings)?;
    Ok(())
}

pub fn is_future_schema(paths: &AppPaths) -> bool {
    effective_document_path(paths)
        .and_then(|path| fs::read_to_string(path).ok())
        .and_then(|contents| serde_json::from_str::<Value>(&contents).ok())
        .and_then(|value| value.get("schema_version")?.as_u64())
        .is_some_and(|version| version > SCHEMA_VERSION)
}

pub fn needs_recovery(paths: &AppPaths) -> bool {
    !is_future_schema(paths)
        && effective_document_path(paths).is_some_and(|path| read_document(&path).is_none())
}

pub fn recover_defaults(paths: &AppPaths) -> AppResult<()> {
    let Some(unreadable) = effective_document_path(paths).filter(|_| needs_recovery(paths)) else {
        return Ok(());
    };
    let backup = paths
        .next_root
        .join(format!("settings.recovery-{}.json", uuid::Uuid::new_v4()));
    fs::rename(unreadable, backup)?;
    Ok(())
}

pub fn load_benchmark(paths: &AppPaths) -> Option<BenchmarkResult> {
    let contents = fs::read_to_string(&paths.benchmark).ok()?;
    serde_json::from_str(&contents).ok()
}

pub fn save_benchmark(paths: &AppPaths, result: &BenchmarkResult) -> AppResult<()> {
    fs::create_dir_all(&paths.next_root)?;
    let temporary = paths.benchmark.with_extension("json.tmp");
    fs::write(&temporary, serde_json::to_vec_pretty(result)?)?;
    replace_settings_file(&temporary, &paths.benchmark)?;
    Ok(())
}

#[cfg(not(windows))]
fn replace_settings_file(temporary: &Path, destination: &Path) -> std::io::Result<()> {
    fs::rename(temporary, destination)
}

#[cfg(windows)]
fn replace_settings_file(temporary: &Path, destination: &Path) -> std::io::Result<()> {
    // Windows does not replace an existing destination with rename(). Keep a
    // recoverable previous copy until the new document is in place.
    let backup = destination.with_extension("json.previous");
    let had_destination = destination.is_file();
    if had_destination {
        if backup.exists() {
            fs::remove_file(&backup)?;
        }
        fs::rename(destination, &backup)?;
    }
    if let Err(error) = fs::rename(temporary, destination) {
        if had_destination {
            let _ = fs::rename(&backup, destination);
        }
        return Err(error);
    }
    if had_destination {
        let _ = fs::remove_file(backup);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn privacy_choice_is_pending_only_for_a_fresh_install() {
        let directory = tempfile::tempdir().unwrap();
        let paths = AppPaths::under(directory.path());
        assert!(!load(&paths).0.privacy_choice_made);
        fs::create_dir_all(paths.settings.parent().unwrap()).unwrap();
        fs::write(
            &paths.settings,
            r#"{"schema_version":2,"ui":{},"recipes":[]}"#,
        )
        .unwrap();
        assert!(load(&paths).0.privacy_choice_made);
    }

    #[test]
    fn completion_sound_defaults_off_and_round_trips_in_schema_two() {
        let directory = tempfile::tempdir().unwrap();
        let paths = AppPaths::under(directory.path());
        fs::create_dir_all(&paths.next_root).unwrap();
        fs::write(
            &paths.settings,
            r#"{"schema_version":2,"ui":{},"recipes":[]}"#,
        )
        .unwrap();
        let (mut settings, recipes) = load(&paths);
        assert!(!settings.completion_sound);
        settings.completion_sound = true;
        save(&paths, &settings, &recipes).unwrap();
        assert!(load(&paths).0.completion_sound);
    }

    #[test]
    fn menu_bar_and_finish_notifications_default_on_and_round_trip() {
        let directory = tempfile::tempdir().unwrap();
        let paths = AppPaths::under(directory.path());
        assert!(load(&paths).0.show_in_menu_bar);
        assert!(load(&paths).0.notify_on_finish);

        fs::create_dir_all(&paths.next_root).unwrap();
        fs::write(
            &paths.settings,
            r#"{"schema_version":2,"ui":{},"recipes":[]}"#,
        )
        .unwrap();
        let (mut settings, recipes) = load(&paths);
        assert!(settings.show_in_menu_bar);
        assert!(settings.notify_on_finish);
        assert!(settings.menu_bar_icon_monochrome);
        assert!(settings.auto_cache_cleanup);

        settings.show_in_menu_bar = false;
        settings.notify_on_finish = false;
        settings.menu_bar_icon_monochrome = false;
        settings.auto_cache_cleanup = false;
        save(&paths, &settings, &recipes).unwrap();
        let (reloaded, _) = load(&paths);
        assert!(!reloaded.show_in_menu_bar);
        assert!(!reloaded.notify_on_finish);
        assert!(!reloaded.menu_bar_icon_monochrome);
        assert!(!reloaded.auto_cache_cleanup);
    }

    #[test]
    fn appearance_defaults_round_trips_and_rejects_unknown_values() {
        let defaults: UiSettings = serde_json::from_str("{}").unwrap();
        assert_eq!(defaults.appearance, "dark");
        assert!(!defaults.colour_blind_palette);
        let settings = UiSettings {
            appearance: "system".into(),
            colour_blind_palette: true,
            ..defaults
        };
        let restored: UiSettings =
            serde_json::from_str(&serde_json::to_string(&settings).unwrap()).unwrap();
        assert_eq!(restored.appearance, "system");
        assert!(restored.colour_blind_palette);
        let unknown: UiSettings = serde_json::from_str(r#"{"appearance":"sepia"}"#).unwrap();
        assert_eq!(unknown.appearance, "dark");
    }

    #[test]
    fn contrast_and_viewer_background_default_round_trip_and_reject_unknown_values() {
        let defaults: UiSettings = serde_json::from_str("{}").unwrap();
        assert_eq!(defaults.contrast, "system");
        assert_eq!(defaults.viewer_background, "checker");
        let settings = UiSettings {
            contrast: "high".into(),
            viewer_background: "grey".into(),
            ..defaults
        };
        let restored: UiSettings =
            serde_json::from_str(&serde_json::to_string(&settings).unwrap()).unwrap();
        assert_eq!(restored.contrast, "high");
        assert_eq!(restored.viewer_background, "grey");
        let unknown: UiSettings =
            serde_json::from_str(r#"{"contrast":"max","viewer_background":"pink"}"#).unwrap();
        assert_eq!(unknown.contrast, "system");
        assert_eq!(unknown.viewer_background, "checker");
    }

    #[test]
    fn accent_defaults_round_trips_and_rejects_unknown_values() {
        let defaults: UiSettings = serde_json::from_str("{}").unwrap();
        assert_eq!(defaults.accent, "system");
        let settings = UiSettings {
            accent: "localsr".into(),
            ..defaults
        };
        let restored: UiSettings =
            serde_json::from_str(&serde_json::to_string(&settings).unwrap()).unwrap();
        assert_eq!(restored.accent, "localsr");
        let unknown: UiSettings = serde_json::from_str(r#"{"accent":"pink"}"#).unwrap();
        assert_eq!(unknown.accent, "system");
    }

    #[test]
    fn preview_patch_settings_default_for_older_files_and_refuse_unknown_values() {
        let older: UiSettings = serde_json::from_str("{}").unwrap();
        assert_eq!(older.preview_patch, "auto");
        assert!(!older.preview_patch_shown);
        let unknown: UiSettings = serde_json::from_str(r#"{"preview_patch":"sometimes"}"#).unwrap();
        assert_eq!(unknown.preview_patch, "auto");
        let chosen: UiSettings =
            serde_json::from_str(r#"{"preview_patch":"off","preview_patch_shown":true}"#).unwrap();
        assert_eq!(chosen.preview_patch, "off");
        assert!(chosen.preview_patch_shown);
    }

    #[test]
    fn edit_expert_settings_default_load_from_older_files_and_round_trip() {
        let defaults: UiSettings = serde_json::from_str("{}").unwrap();
        assert_eq!(defaults.edit_text_encoder, "auto");
        assert_eq!(defaults.edit_vae, "auto");
        assert_eq!(defaults.edit_vram_limit_mb, 0);
        assert!(!defaults.edit_allow_any_size);

        let settings = UiSettings {
            edit_text_encoder: "cpu".into(),
            edit_vae: "gpu".into(),
            edit_vram_limit_mb: 8192,
            edit_allow_any_size: true,
            ..defaults
        };
        let restored: UiSettings =
            serde_json::from_str(&serde_json::to_string(&settings).unwrap()).unwrap();
        assert_eq!(restored.edit_text_encoder, "cpu");
        assert_eq!(restored.edit_vae, "gpu");
        assert_eq!(restored.edit_vram_limit_mb, 8192);
        assert!(restored.edit_allow_any_size);
    }

    #[test]
    fn new_installs_start_with_the_count_and_crash_reports_on_and_saved_choices_stay() {
        assert!(UiSettings::default().anonymous_update_count);
        assert!(UiSettings::default().crash_reports);
        let directory = tempfile::tempdir().unwrap();
        let paths = AppPaths::under(directory.path());
        assert!(load(&paths).0.anonymous_update_count);
        fs::create_dir_all(&paths.next_root).unwrap();
        fs::write(
            &paths.settings,
            r#"{"schema_version":2,"ui":{"anonymous_update_count":false},"recipes":[]}"#,
        )
        .unwrap();
        let (saved, _) = load(&paths);
        assert!(!saved.anonymous_update_count);
        // A settings file from before crash reports existed: its user never saw the choice.
        assert!(!saved.crash_reports);
    }

    #[test]
    fn settings_are_read_from_the_previous_copy_when_a_save_stopped_between_its_renames() {
        let directory = tempfile::tempdir().unwrap();
        let paths = AppPaths::under(directory.path());
        let saved = UiSettings {
            tile_size: 192,
            ..UiSettings::default()
        };
        save(&paths, &saved, &[]).unwrap();
        fs::rename(&paths.settings, previous_copy(&paths)).unwrap();
        assert_eq!(load(&paths).0.tile_size, 192);
        // A settings file that exists but cannot be read is never replaced by the old copy.
        fs::write(&paths.settings, b"{ cut off").unwrap();
        assert_ne!(load(&paths).0.tile_size, 192);
        assert!(needs_recovery(&paths));
    }

    #[test]
    fn v1_fix_migration_preserves_choices_and_explicit_empty_lists() {
        for (id, fix) in [
            ("fbcnn_color", "jpeg"),
            ("nafnet_gopro_deblur", "blur"),
            ("scunet_color_real_psnr", "noise"),
        ] {
            for explicit_empty in [false, true] {
                let directory = tempfile::tempdir().unwrap();
                let paths = AppPaths::under(directory.path());
                fs::create_dir_all(&paths.next_root).unwrap();
                let mut ui =
                    serde_json::json!({"preprocess_model_id": id, "fixes": ["noise", "blur"]});
                if explicit_empty {
                    ui["fix_stages"] = serde_json::json!([]);
                }
                let original =
                    serde_json::json!({"schema_version": 1, "ui": ui, "recipes": []}).to_string();
                fs::write(&paths.settings, &original).unwrap();
                let (settings, recipes) = load(&paths);
                let value = serde_json::to_value(&settings).unwrap();
                assert_eq!(
                    value["fix_stages"],
                    if explicit_empty {
                        serde_json::json!([])
                    } else {
                        serde_json::json!([{"fix": fix, "model_id": id}])
                    }
                );
                assert!(value.get("preprocess_model_id").is_none());
                save(&paths, &settings, &recipes).unwrap();
                let written: Value =
                    serde_json::from_slice(&fs::read(&paths.settings).unwrap()).unwrap();
                assert_eq!(written["schema_version"], 2);
                assert_eq!(
                    serde_json::to_value(load(&paths).0).unwrap()["fix_stages"],
                    value["fix_stages"]
                );
            }
        }
    }

    fn migrated_ui(ui: Value) -> Value {
        let directory = tempfile::tempdir().unwrap();
        let paths = AppPaths::under(directory.path());
        fs::create_dir_all(&paths.next_root).unwrap();
        fs::write(
            &paths.settings,
            serde_json::json!({"schema_version": 1, "ui": ui, "recipes": []}).to_string(),
        )
        .unwrap();
        serde_json::to_value(load(&paths).0).unwrap()
    }

    #[test]
    fn v1_migration_runs_only_what_ran_before() {
        // 0.3.5 sent its fix only to Upscale: a stale ID next to Restore or Video never ran.
        let restore = migrated_ui(serde_json::json!({
            "task": "denoise", "selected_model_id": "scunet_color_real_psnr",
            "fixes": ["noise"], "preprocess_model_id": "fbcnn_color"
        }));
        assert_eq!(
            restore["fix_stages"],
            serde_json::json!([{"fix": "noise", "model_id": "scunet_color_real_psnr"}])
        );
        let video = migrated_ui(serde_json::json!({
            "task": "video", "selected_model_id": "span_photo_x4",
            "fixes": ["jpeg", "faces"], "preprocess_model_id": "fbcnn_color"
        }));
        assert_eq!(video["fix_stages"], serde_json::json!([]));
        assert_eq!(video["fixes"], serde_json::json!(["faces"]));
        // Restore without a chip has no fix; its model is a plain restore choice.
        let plain = migrated_ui(serde_json::json!({
            "task": "denoise", "selected_model_id": "scunet_color_real_psnr", "fixes": []
        }));
        assert_eq!(plain["fix_stages"], serde_json::json!([]));
        // Edit leaves the Upscale setup alone for the way back.
        let edit = migrated_ui(serde_json::json!({
            "task": "edit", "selected_model_id": "span_photo_x4",
            "fixes": ["jpeg", "faces"], "preprocess_model_id": "fbcnn_color"
        }));
        assert_eq!(
            edit["fix_stages"],
            serde_json::json!([{"fix": "jpeg", "model_id": "fbcnn_color"}])
        );
        assert_eq!(edit["fixes"], serde_json::json!(["jpeg", "faces"]));
        // Edit after Restore: 0.3.5 kept Restore's model and chip for the way back. Only
        // Restore selects a 1x model, so that is where Edit came from.
        let restore_then_edit = migrated_ui(serde_json::json!({
            "task": "edit", "selected_model_id": "fbcnn_color",
            "fixes": ["jpeg"], "preprocess_model_id": ""
        }));
        assert_eq!(
            restore_then_edit["fix_stages"],
            serde_json::json!([{"fix": "jpeg", "model_id": "fbcnn_color"}])
        );
        assert_eq!(restore_then_edit["fixes"], serde_json::json!(["jpeg"]));
        // A model the catalog no longer lists cannot say what it fixed; the chip still can.
        let unknown = migrated_ui(serde_json::json!({
            "task": "upscale", "fixes": ["blur"], "preprocess_model_id": "retired_model"
        }));
        assert_eq!(
            unknown["fix_stages"],
            serde_json::json!([{"fix": "blur", "model_id": "retired_model"}])
        );
    }

    #[test]
    fn the_first_start_of_this_version_keeps_the_v1_file_before_saving_v2() {
        let directory = tempfile::tempdir().unwrap();
        let paths = AppPaths::under(directory.path());
        fs::create_dir_all(&paths.next_root).unwrap();
        fs::create_dir_all(&paths.model_root).unwrap();
        let original = serde_json::json!({"schema_version": 1, "ui": {
            "task": "upscale", "fixes": ["noise"], "preprocess_model_id": "scunet_color_real_psnr"
        }, "recipes": []})
        .to_string();
        fs::write(&paths.settings, &original).unwrap();
        let state = crate::state::AppState::from_paths(paths.clone()).unwrap();
        let settings = state.settings.lock().unwrap().clone();
        save(&paths, &settings, &[]).unwrap();
        let written: Value = serde_json::from_slice(&fs::read(&paths.settings).unwrap()).unwrap();
        assert_eq!(written["schema_version"], 2);
        let kept: Vec<_> = fs::read_dir(paths.next_root.join("backups"))
            .unwrap()
            .map(|entry| fs::read_to_string(entry.unwrap().path().join("settings.json")).unwrap())
            .collect();
        assert_eq!(kept, [original]);
    }

    #[test]
    fn v1_restore_keeps_its_resolved_fix_and_does_not_invent_extra_stages() {
        let directory = tempfile::tempdir().unwrap();
        let paths = AppPaths::under(directory.path());
        fs::create_dir_all(&paths.next_root).unwrap();
        fs::write(&paths.settings, serde_json::json!({"schema_version":1,"ui":{
            "task":"denoise","selected_model_id":"fbcnn_color","fixes":["jpeg"],"preprocess_model_id":""
        },"recipes":[]}).to_string()).unwrap();
        let value = serde_json::to_value(load(&paths).0).unwrap();
        assert_eq!(
            value["fix_stages"],
            serde_json::json!([{"fix":"jpeg","model_id":"fbcnn_color"}])
        );
        assert_eq!(value["fixes"], serde_json::json!(["jpeg"]));
    }

    #[test]
    fn step_stages_this_version_cannot_run_are_dropped_on_load_and_empty_ones_are_not_written() {
        let directory = tempfile::tempdir().unwrap();
        let paths = AppPaths::under(directory.path());
        fs::create_dir_all(&paths.next_root).unwrap();
        fs::write(
            &paths.settings,
            r#"{"schema_version":2,"ui":{},"recipes":[]}"#,
        )
        .unwrap();
        let (settings, recipes) = load(&paths);
        assert!(settings.step_stages.is_empty());
        save(&paths, &settings, &recipes).unwrap();
        let written: Value =
            serde_json::from_str(&fs::read_to_string(&paths.settings).unwrap()).unwrap();
        assert!(written["ui"].get("step_stages").is_none());

        fs::write(
            &paths.settings,
            r#"{"schema_version":2,"ui":{"step_stages":[
                {"step":"tool_from_a_newer_version"},
                {"step":"grain","params":{"strength":40,"seed":7},"mask_path":"/in/mask.png"},
                {"step":"grain","params":{"strength":20}},
                {"step":"grain","params":{"strength":250}},
                {"step":"upscale"}
            ]},"recipes":[]}"#,
        )
        .unwrap();
        let (settings, recipes) = load(&paths);
        assert_eq!(
            serde_json::to_value(&settings.step_stages).unwrap(),
            serde_json::json!([
                {"step": "grain", "params": {"strength": 40, "seed": 7}, "mask_path": "/in/mask.png"}
            ])
        );
        save(&paths, &settings, &recipes).unwrap();
        assert_eq!(load(&paths).0.step_stages, settings.step_stages);
    }

    #[test]
    fn step_entries_keep_only_models_this_catalog_offers_for_the_step() {
        use crate::types::StepStageSelection;

        crate::steps::testing::with_steps(vec![crate::steps::testing::cutout()], || {
            let stage = |step: &str, model_id: Option<&str>| StepStageSelection {
                step: step.into(),
                model_id: model_id.map(str::to_owned),
                ..StepStageSelection::default()
            };
            let mut catalog = crate::catalog::bundled_catalog().unwrap();
            let mut cutout_model = catalog.models[0].clone();
            cutout_model.model_id = "cutout_model".into();
            cutout_model.purposes = vec!["test_cutout".into()];
            catalog.models.push(cutout_model);

            for (entry, kept) in [
                (stage("test_cutout", Some("cutout_model")), true),
                // Waits for a choice, as a fix without a model does.
                (stage("test_cutout", None), true),
                (stage("test_cutout", Some("from_a_newer_catalog")), false),
                (stage("test_cutout", Some("span_photo_x4")), false),
                (stage("grain", Some("cutout_model")), false),
                (stage("grain", None), true),
            ] {
                let mut settings = UiSettings {
                    step_stages: vec![entry.clone()],
                    ..UiSettings::default()
                };
                keep_runnable_step_stages(&mut settings, &catalog);
                assert_eq!(settings.step_stages.len(), usize::from(kept), "{entry:?}");
            }

            // Loading uses this version's catalog, which has no cut-out model.
            let directory = tempfile::tempdir().unwrap();
            let paths = AppPaths::under(directory.path());
            fs::create_dir_all(&paths.next_root).unwrap();
            fs::write(
                &paths.settings,
                r#"{"schema_version":2,"ui":{"step_stages":[
                    {"step":"test_cutout","model_id":"cutout_model"},
                    {"step":"grain","params":{"strength":40}}
                ]},"recipes":[]}"#,
            )
            .unwrap();
            assert_eq!(
                serde_json::to_value(load(&paths).0.step_stages).unwrap(),
                serde_json::json!([{"step": "grain", "params": {"strength": 40}}])
            );
        });
    }

    #[test]
    fn future_settings_are_never_overwritten() {
        let directory = tempfile::tempdir().unwrap();
        let paths = AppPaths::under(directory.path());
        fs::create_dir_all(&paths.next_root).unwrap();
        let original = r#"{"schema_version":3,"ui":{},"recipes":[]}"#;
        fs::write(&paths.settings, original).unwrap();
        assert!(!needs_recovery(&paths));
        let (settings, recipes) = load(&paths);
        assert!(save(&paths, &settings, &recipes).is_ok());
        recover_defaults(&paths).unwrap();
        assert_eq!(fs::read_to_string(&paths.settings).unwrap(), original);
    }

    #[test]
    fn future_previous_copy_is_preserved_without_recovery() {
        let directory = tempfile::tempdir().unwrap();
        let paths = AppPaths::under(directory.path());
        fs::create_dir_all(&paths.next_root).unwrap();
        let original = r#"{"schema_version":3,"ui":{},"recipes":[]}"#;
        fs::write(previous_copy(&paths), original).unwrap();
        assert!(!needs_recovery(&paths));
        let (settings, recipes) = load(&paths);
        save(&paths, &settings, &recipes).unwrap();
        recover_defaults(&paths).unwrap();
        assert_eq!(fs::read_to_string(previous_copy(&paths)).unwrap(), original);
        assert!(!paths.settings.exists());
    }

    #[test]
    fn legacy_import_does_not_mutate_legacy_file() {
        let directory = tempfile::tempdir().unwrap();
        let paths = AppPaths::under(directory.path());
        fs::create_dir_all(paths.legacy_settings.parent().unwrap()).unwrap();
        let legacy = r#"{"selected_model_id":"hat_s_x4","format_index":1,"tile_size":128}"#;
        fs::write(&paths.legacy_settings, legacy).unwrap();
        let (settings, recipes) = load(&paths);
        assert_eq!(settings.selected_model_id, "hat_s_x4");
        assert_eq!(settings.output_format, "jpg");
        save(&paths, &settings, &recipes).unwrap();
        assert_eq!(fs::read_to_string(&paths.legacy_settings).unwrap(), legacy);

        let mut changed = settings.clone();
        changed.output_scale = 2;
        save(&paths, &changed, &recipes).unwrap();
        let (loaded, _) = load(&paths);
        assert_eq!(loaded.output_scale, 2);
    }

    #[test]
    fn the_old_av1_default_becomes_automatic_once() {
        let mut older: UiSettings = serde_json::from_value(serde_json::json!({
            "video_codec": "av1"
        }))
        .unwrap();
        assert!(!older.automatic_video_format);
        adopt_automatic_video_format(&mut older);
        assert_eq!(older.video_codec, "auto");
        older.video_codec = "av1".into();
        adopt_automatic_video_format(&mut older);
        assert_eq!(older.video_codec, "av1");
        let mut vp9: UiSettings =
            serde_json::from_value(serde_json::json!({ "video_codec": "vp9" })).unwrap();
        adopt_automatic_video_format(&mut vp9);
        assert_eq!(vp9.video_codec, "vp9");
        assert_eq!(UiSettings::default().video_codec, "auto");
    }

    #[test]
    fn recipes_from_older_previews_gain_safe_optional_defaults() {
        let recipe: Recipe = serde_json::from_value(serde_json::json!({
            "id": "old-recipe",
            "name": "Old recipe",
            "task": "upscale",
            "model_id": "span_photo_x4",
            "output_scale": 4,
            "tile_size": 128,
            "halo": 16,
            "precision": "fp32",
            "safe_memory": true
        }))
        .unwrap();

        assert!(recipe.video_model_id.is_empty());
        assert!(recipe.custom_model_path.is_empty());
        assert_eq!(recipe.preserve_metadata, None);
        assert_eq!(recipe.video_crf, None);
    }
}

#[cfg(test)]
mod recovery_tests {
    use super::*;
    #[test]
    fn unreadable_settings_are_preserved_until_explicit_recovery() {
        let root = tempfile::tempdir().unwrap();
        let paths = AppPaths::under(root.path());
        fs::create_dir_all(&paths.next_root).unwrap();
        fs::write(&paths.settings, b"{broken recipes").unwrap();
        let (settings, recipes) = load(&paths);
        assert!(save(&paths, &settings, &recipes).is_err());
        assert_eq!(fs::read(&paths.settings).unwrap(), b"{broken recipes");
        recover_defaults(&paths).unwrap();
        save(&paths, &settings, &recipes).unwrap();
        let copies: Vec<_> = fs::read_dir(&paths.next_root)
            .unwrap()
            .flatten()
            .filter(|p| {
                p.file_name()
                    .to_string_lossy()
                    .starts_with("settings.recovery-")
            })
            .collect();
        assert_eq!(copies.len(), 1);
        assert_eq!(fs::read(copies[0].path()).unwrap(), b"{broken recipes");
    }

    #[test]
    fn an_unreadable_previous_copy_is_protected_like_an_unreadable_settings_file() {
        for original in [&br#"{broken"#[..], &br#"{}"#[..]] {
            let root = tempfile::tempdir().unwrap();
            let paths = AppPaths::under(root.path());
            fs::create_dir_all(&paths.next_root).unwrap();
            // A save stopped between its renames and the only copy cannot be read.
            fs::write(previous_copy(&paths), original).unwrap();
            assert!(!paths.settings.exists());
            assert!(needs_recovery(&paths));
            let (settings, recipes) = load(&paths);
            assert!(save(&paths, &settings, &recipes).is_err());
            assert_eq!(fs::read(previous_copy(&paths)).unwrap(), original);
            assert!(!paths.settings.exists());
            // Recovery sets it aside instead of deleting it, and saving works again.
            recover_defaults(&paths).unwrap();
            assert!(!previous_copy(&paths).exists());
            assert!(!needs_recovery(&paths));
            save(&paths, &settings, &recipes).unwrap();
            let copies: Vec<_> = fs::read_dir(&paths.next_root)
                .unwrap()
                .flatten()
                .filter(|p| {
                    p.file_name()
                        .to_string_lossy()
                        .starts_with("settings.recovery-")
                })
                .collect();
            assert_eq!(copies.len(), 1);
            assert_eq!(fs::read(copies[0].path()).unwrap(), original);
        }
    }
}

#[cfg(test)]
mod precision_tests {
    use super::*;

    #[test]
    fn new_install_defaults_to_auto_precision_and_saved_fp32_is_not_migrated() {
        let root = tempfile::tempdir().unwrap();
        let paths = AppPaths::under(root.path());
        let (fresh, _) = load(&paths);
        assert_eq!(fresh.precision, "auto");
        assert_eq!(UiSettings::default().precision, "auto");

        fs::create_dir_all(&paths.next_root).unwrap();
        let saved_fp32 = serde_json::json!({
            "schema_version": 2,
            "ui": { "precision": "fp32" },
            "recipes": []
        });
        fs::write(&paths.settings, saved_fp32.to_string()).unwrap();
        let (loaded, _) = load(&paths);
        assert_eq!(loaded.precision, "fp32");
    }
}

#[cfg(test)]
mod recipe_list_tests {
    use super::*;

    fn recipe(id: &str) -> Value {
        serde_json::json!({
            "id": id, "name": id, "task": "upscale", "model_id": "m", "output_scale": 2,
            "tile_size": 256, "halo": 16, "precision": "fp32", "safe_memory": false
        })
    }

    fn write(paths: &AppPaths, document: Value) {
        fs::create_dir_all(&paths.next_root).unwrap();
        fs::write(&paths.settings, document.to_string()).unwrap();
    }

    #[test]
    fn a_file_from_before_pins_pins_its_first_seven_recipes_and_an_empty_list_stays_empty() {
        let ids: Vec<String> = (1..=9).map(|index| format!("r{index}")).collect();
        let recipes: Vec<Value> = ids.iter().map(|id| recipe(id)).collect();
        for schema_version in [1, 2] {
            let directory = tempfile::tempdir().unwrap();
            let paths = AppPaths::under(directory.path());
            write(
                &paths,
                serde_json::json!({
                    "schema_version": schema_version, "ui": {}, "recipes": recipes
                }),
            );
            let (settings, loaded) = load(&paths);
            assert_eq!(settings.recipe_pins, ids[..7]);
            assert!(settings.recipe_recent.is_empty());

            // Unpinning everything is a choice the next start keeps.
            let unpinned = UiSettings {
                recipe_pins: Vec::new(),
                ..settings
            };
            save(&paths, &unpinned, &loaded).unwrap();
            let written: Value =
                serde_json::from_slice(&fs::read(&paths.settings).unwrap()).unwrap();
            assert_eq!(written["ui"]["recipe_pins"], serde_json::json!([]));
            assert!(load(&paths).0.recipe_pins.is_empty());
        }

        let directory = tempfile::tempdir().unwrap();
        let paths = AppPaths::under(directory.path());
        write(
            &paths,
            serde_json::json!({
                "schema_version": 2, "ui": {"recipe_pins": []}, "recipes": recipes
            }),
        );
        assert!(load(&paths).0.recipe_pins.is_empty());

        // Fewer than seven recipes, or none: each is pinned, and nothing is made up.
        for count in [0, 3] {
            let directory = tempfile::tempdir().unwrap();
            let paths = AppPaths::under(directory.path());
            write(
                &paths,
                serde_json::json!({
                    "schema_version": 2, "ui": {}, "recipes": recipes[..count]
                }),
            );
            assert_eq!(load(&paths).0.recipe_pins, ids[..count]);
        }
    }

    #[test]
    fn pin_and_recent_lists_this_version_cannot_save_are_cut_to_what_it_can() {
        let directory = tempfile::tempdir().unwrap();
        let paths = AppPaths::under(directory.path());
        let mut pins: Vec<String> = vec!["a".into(), "a".into(), String::new(), "x".repeat(65)];
        pins.extend((0..25).map(|index| format!("p{index}")));
        let recent: Vec<String> = (0..15).map(|index| format!("r{index}")).collect();
        write(
            &paths,
            serde_json::json!({
                "schema_version": 2,
                "ui": {"recipe_pins": pins, "recipe_recent": recent},
                "recipes": []
            }),
        );
        let (settings, _) = load(&paths);
        let mut expected = vec!["a".to_owned()];
        expected.extend((0..19).map(|index| format!("p{index}")));
        assert_eq!(settings.recipe_pins, expected);
        assert_eq!(settings.recipe_recent, recent[..12]);
    }
}
