use std::{fs, path::Path};

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::{
    error::AppResult,
    paths::AppPaths,
    types::{Recipe, UiSettings},
};

#[derive(Clone, Debug, Deserialize, Serialize)]
struct SettingsDocument {
    schema_version: u32,
    ui: UiSettings,
    recipes: Vec<Recipe>,
}

pub fn load(paths: &AppPaths) -> (UiSettings, Vec<Recipe>) {
    if let Some(document) = read_document(&paths.settings) {
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

fn read_document(path: &Path) -> Option<SettingsDocument> {
    let contents = fs::read_to_string(path).ok()?;
    serde_json::from_str(&contents).ok()
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
        settings.output_format = ["png", "jpg", "tif"]
            .get(index as usize)
            .copied()
            .unwrap_or("png")
            .into();
    }
}

pub fn save(paths: &AppPaths, settings: &UiSettings, recipes: &[Recipe]) -> AppResult<()> {
    fs::create_dir_all(&paths.next_root)?;
    let document = SettingsDocument {
        schema_version: 1,
        ui: settings.clone(),
        recipes: recipes.to_vec(),
    };
    let bytes = serde_json::to_vec_pretty(&document)?;
    let temporary = paths.settings.with_extension("json.tmp");
    fs::write(&temporary, bytes)?;
    replace_settings_file(&temporary, &paths.settings)?;
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
