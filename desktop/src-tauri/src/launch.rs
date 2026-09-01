use std::{
    ffi::OsString,
    path::{Path, PathBuf},
};

use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Default, Deserialize, PartialEq, Eq, Serialize)]
pub struct LaunchIntent {
    pub files: Vec<String>,
    pub preset: Option<String>,
    pub recipe: Option<String>,
    pub auto_start: bool,
}

impl LaunchIntent {
    pub fn is_empty(&self) -> bool {
        self.files.is_empty() && self.preset.is_none() && self.recipe.is_none() && !self.auto_start
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum ImmediateAction {
    #[default]
    Run,
    InstallIntegrations,
    UninstallIntegrations,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ParsedLaunch {
    pub intent: LaunchIntent,
    pub action: ImmediateAction,
    pub smoke_test: bool,
    pub headless_smoke_test: bool,
}

pub fn parse_os_arguments<I>(arguments: I, cwd: &Path) -> ParsedLaunch
where
    I: IntoIterator<Item = OsString>,
{
    let values: Vec<String> = arguments
        .into_iter()
        .map(|value| value.to_string_lossy().into_owned())
        .collect();
    parse_arguments(&values, cwd)
}

pub fn parse_arguments(arguments: &[String], cwd: &Path) -> ParsedLaunch {
    let mut parsed = ParsedLaunch::default();
    let mut index = 0;
    let mut positional_only = false;
    while index < arguments.len() {
        let value = &arguments[index];
        if positional_only {
            push_file(&mut parsed.intent, value, cwd);
        } else {
            match value.as_str() {
                "--" => positional_only = true,
                "--auto-start" => parsed.intent.auto_start = true,
                "--install-integrations" => parsed.action = ImmediateAction::InstallIntegrations,
                "--uninstall-integrations" => {
                    parsed.action = ImmediateAction::UninstallIntegrations
                }
                "--smoke-test" => parsed.smoke_test = true,
                "--headless-smoke-test" => parsed.headless_smoke_test = true,
                "--preset" if index + 1 < arguments.len() => {
                    index += 1;
                    let preset = arguments[index].to_ascii_lowercase();
                    if matches!(preset.as_str(), "quick" | "best") {
                        parsed.intent.preset = Some(preset);
                    }
                }
                "--recipe" if index + 1 < arguments.len() => {
                    index += 1;
                    let recipe = arguments[index].trim();
                    if !recipe.is_empty() {
                        parsed.intent.recipe = Some(recipe.chars().take(60).collect());
                    }
                }
                value if value.starts_with("-psn_") => {}
                value if value.starts_with('-') => {}
                value => push_file(&mut parsed.intent, value, cwd),
            }
        }
        index += 1;
    }
    parsed
}

fn push_file(intent: &mut LaunchIntent, value: &str, cwd: &Path) {
    if intent.files.len() >= 10_000 || value.trim().is_empty() {
        return;
    }
    let path = PathBuf::from(value);
    let absolute = if path.is_absolute() {
        path
    } else {
        cwd.join(path)
    };
    intent.files.push(absolute.to_string_lossy().into_owned());
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_files_presets_recipes_and_auto_start_without_treating_flags_as_files() {
        let parsed = parse_arguments(
            &[
                "image.png".into(),
                "--preset".into(),
                "quick".into(),
                "--auto-start".into(),
                "--unknown".into(),
                "--recipe".into(),
                "Portraits".into(),
                "clip.mp4".into(),
            ],
            Path::new("/work"),
        );

        assert_eq!(
            parsed.intent.files,
            vec![
                Path::new("/work")
                    .join("image.png")
                    .to_string_lossy()
                    .into_owned(),
                Path::new("/work")
                    .join("clip.mp4")
                    .to_string_lossy()
                    .into_owned(),
            ]
        );
        assert_eq!(parsed.intent.preset.as_deref(), Some("quick"));
        assert_eq!(parsed.intent.recipe.as_deref(), Some("Portraits"));
        assert!(parsed.intent.auto_start);
    }

    #[test]
    fn recognizes_explicit_integration_and_smoke_commands() {
        let parsed = parse_arguments(
            &[
                "--install-integrations".into(),
                "--smoke-test".into(),
                "--headless-smoke-test".into(),
            ],
            Path::new("/"),
        );
        assert_eq!(parsed.action, ImmediateAction::InstallIntegrations);
        assert!(parsed.smoke_test);
        assert!(parsed.headless_smoke_test);
    }
}
