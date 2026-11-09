use std::{fs, path::Path};

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::{
    error::{AppError, AppResult},
    paths::AppPaths,
    types::{BenchmarkResult, Recipe, UiSettings},
};

#[derive(Clone, Debug, Deserialize, Serialize)]
struct SettingsDocument {
    schema_version: u32,
    ui: UiSettings,
    recipes: Vec<Recipe>,
}

pub fn load(paths: &AppPaths) -> (UiSettings, Vec<Recipe>) {
    if let Some(mut document) = read_document(&paths.settings) {
        adopt_automatic_video_format(&mut document.ui);
        return (document.ui, document.recipes);
    }

    let mut settings = UiSettings {
        output_directory: paths.default_output.to_string_lossy().into_owned(),
        ..UiSettings::default()
    };
    if let Ok(contents) = fs::read_to_string(&paths.legacy_settings) {
        if let Ok(value) = serde_json::from_str::<Value>(&contents) {
            import_legacy_settings(&mut settings, &value);
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
    serde_json::from_value(value).ok()
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
                    .filter(|fix| matches!(*fix, "noise" | "jpeg" | "blur"))
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
    if paths.settings.exists() && read_document(&paths.settings).is_none() {
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

pub fn needs_recovery(paths: &AppPaths) -> bool {
    paths.settings.exists() && read_document(&paths.settings).is_none()
}

pub fn recover_defaults(paths: &AppPaths) -> AppResult<()> {
    if !needs_recovery(paths) {
        return Ok(());
    }
    let backup = paths
        .next_root
        .join(format!("settings.recovery-{}.json", uuid::Uuid::new_v4()));
    fs::rename(&paths.settings, backup)?;
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
    fn future_settings_are_never_overwritten() {
        let directory = tempfile::tempdir().unwrap();
        let paths = AppPaths::under(directory.path());
        fs::create_dir_all(&paths.next_root).unwrap();
        let original = r#"{"schema_version":3,"ui":{},"recipes":[]}"#;
        fs::write(&paths.settings, original).unwrap();
        assert!(needs_recovery(&paths));
        let (settings, recipes) = load(&paths);
        assert!(save(&paths, &settings, &recipes).is_err());
        assert_eq!(fs::read_to_string(&paths.settings).unwrap(), original);
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
}
