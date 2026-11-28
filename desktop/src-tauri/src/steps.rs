use std::sync::LazyLock;

use serde::Deserialize;

const STEPS_JSON: &str = include_str!("../resources/steps.json");

static REGISTRY: LazyLock<StepRegistry> = LazyLock::new(|| {
    StepRegistry::from_json(STEPS_JSON).expect("bundled steps.json must be valid")
});

pub fn registry() -> &'static StepRegistry {
    #[cfg(test)]
    if let Some(registry) = testing::current() {
        return registry;
    }
    &REGISTRY
}

/// Steps that exist only in a test, the way the worker's tests register theirs.
#[cfg(test)]
pub mod testing {
    use std::cell::Cell;

    use super::{StepDefinition, StepRegistry, REGISTRY};

    thread_local! {
        static CURRENT: Cell<Option<&'static StepRegistry>> = const { Cell::new(None) };
    }

    pub(super) fn current() -> Option<&'static StepRegistry> {
        CURRENT.with(Cell::get)
    }

    /// Runs `body` on this thread with `extra` added to the bundled registry.
    pub fn with_steps<T>(extra: Vec<StepDefinition>, body: impl FnOnce() -> T) -> T {
        struct Restore(Option<&'static StepRegistry>);
        impl Drop for Restore {
            fn drop(&mut self) {
                CURRENT.with(|cell| cell.set(self.0));
            }
        }
        let registry: &'static StepRegistry = Box::leak(Box::new(REGISTRY.with_steps(extra)));
        let _restore = Restore(CURRENT.with(|cell| cell.replace(Some(registry))));
        body()
    }

    /// A step definition from the same JSON shape `steps.json` uses.
    pub fn step(json: serde_json::Value) -> StepDefinition {
        serde_json::from_value(json).expect("test step must be a valid step definition")
    }

    /// A photo step stage with a model from catalog task `test_cutout` that sets the alpha,
    /// as `tests/test_model_steps.py` (`CUTOUT`) and the plan parity cases define it.
    pub fn cutout() -> StepDefinition {
        step(serde_json::json!({
            "id": "test_cutout", "media": ["image"], "phase": "cleanup", "order": 10,
            "needs_model": true, "is_fix_stage": false, "in_image_pipeline": true,
            "in_recipe_stages": false, "label": "Test cut-out",
            "model_task": "test_cutout", "output_alpha": true
        }))
    }
}

#[derive(Clone, Debug, Deserialize)]
pub struct StepRegistry {
    #[serde(default)]
    phases: Vec<String>,
    limits: StepLimits,
    steps: Vec<StepDefinition>,
}

#[derive(Clone, Debug, Deserialize)]
pub struct StepLimits {
    pub max_fix_stages: usize,
    pub max_fix_chips: usize,
    pub max_face_stages: usize,
    pub max_recipe_stages: usize,
    pub max_upscale_prefix_stages: usize,
    pub max_denoise_prefix_stages: usize,
    pub max_video_upscale_prefix_stages: usize,
    #[serde(default)]
    pub max_step_stages: usize,
    pub multi_fix_feature: String,
    #[serde(default = "default_stage_params_feature")]
    pub stage_params_feature: String,
}

fn default_stage_params_feature() -> String {
    "stage_params_and_masks".into()
}

fn default_true() -> bool {
    true
}

fn default_param_kind() -> String {
    "str".into()
}

#[derive(Clone, Debug, Deserialize)]
pub struct StepParameter {
    pub name: String,
    #[serde(default = "default_param_kind")]
    pub kind: String,
    pub default: Option<serde_json::Value>,
    pub minimum: Option<f64>,
    pub maximum: Option<f64>,
    #[serde(default)]
    pub choices: Vec<String>,
    #[serde(default)]
    pub int_choices: Vec<i64>,
    pub ui_minimum: Option<f64>,
    pub ui_maximum: Option<f64>,
    #[serde(default)]
    pub nullable: bool,
    #[serde(default)]
    pub required: bool,
}

#[derive(Clone, Debug, Deserialize)]
pub struct StepDefinition {
    pub id: String,
    #[serde(default)]
    pub media: Vec<String>,
    pub phase: String,
    /// Where the step runs within its phase.
    #[serde(default)]
    pub order: i64,
    #[serde(default = "default_true")]
    pub needs_model: bool,
    #[serde(default)]
    pub supports_mask: bool,
    /// A stage of this step needs a mask; the host draws it from the photo's paint.
    #[serde(default)]
    pub requires_mask: bool,
    pub stage_kind: Option<String>,
    pub role: Option<String>,
    pub fix_kind: Option<String>,
    pub is_fix_stage: bool,
    #[serde(default)]
    pub in_image_pipeline: bool,
    pub in_recipe_stages: bool,
    #[serde(default)]
    pub label: String,
    #[serde(default)]
    pub row_label: String,
    #[serde(default)]
    pub stage_label: String,
    pub required_feature: Option<String>,
    /// The engine lists this step's feature only when started with `LOCALSR_DEV_STEPS=1`.
    #[serde(default)]
    pub dev_only: bool,
    /// A step stage with a model takes it from the catalog models whose `purposes` name this.
    #[serde(default)]
    pub model_task: Option<String>,
    /// The step sets the image's alpha, so the job's output format must keep one.
    #[serde(default)]
    pub output_alpha: bool,
    #[serde(default)]
    pub parameters: Vec<StepParameter>,
}

/// Where a step a job adds by id runs: before the fixes, or after the main model and Faces.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum StepPlacement {
    BeforeFixes,
    AfterPrimary,
}

impl StepRegistry {
    pub fn from_json(json: &str) -> Result<Self, serde_json::Error> {
        serde_json::from_str(json)
    }

    pub fn limits(&self) -> &StepLimits {
        &self.limits
    }

    pub fn step(&self, id: &str) -> Option<&StepDefinition> {
        self.steps.iter().find(|step| step.id == id)
    }

    /// This registry with `extra` steps added, each also counted as a step a job may add.
    #[cfg(test)]
    pub fn with_steps(&self, extra: Vec<StepDefinition>) -> Self {
        let mut registry = self.clone();
        registry.limits.max_step_stages += extra.len();
        registry.steps.extend(extra);
        registry
    }

    pub fn phase_index(&self, phase: &str) -> Option<usize> {
        self.phases.iter().position(|candidate| candidate == phase)
    }

    /// A step of the stage list that a job adds by id beside its fixes: model-less, or
    /// one whose model comes from the catalog task it names.
    pub fn step_stage(&self, id: &str) -> Option<(&StepDefinition, StepPlacement)> {
        let step = self.step(id).filter(|step| {
            step.in_image_pipeline
                && !step.is_fix_stage
                && (!step.needs_model || step.model_task.is_some())
        })?;
        let phase = self.phase_index(&step.phase)?;
        if phase < self.phase_index("primary")? {
            Some((step, StepPlacement::BeforeFixes))
        } else if phase > self.phase_index("face")? {
            Some((step, StepPlacement::AfterPrimary))
        } else {
            None
        }
    }

    /// Checks one step stage's id, parameters and mask path against the registry.
    pub fn validate_step_stage(
        &self,
        step_id: &str,
        params: Option<&serde_json::Map<String, serde_json::Value>>,
        mask_path: Option<&str>,
    ) -> Result<&StepDefinition, String> {
        let (step, _) = self
            .step_stage(step_id)
            .ok_or_else(|| format!("'{step_id}' is not a step a job can add"))?;
        self.validate_step_params(step, params)?;
        if mask_path.is_some() && step.requires_mask {
            return Err(format!("The mask of {} is painted in LocalSR.", step.label));
        }
        if let Some(mask) = mask_path {
            if mask.trim().is_empty() || mask.contains('\0') {
                return Err("stage mask path is invalid".into());
            }
            if !step.supports_mask {
                return Err(format!("the {} step does not support a mask", step.id));
            }
        }
        Ok(step)
    }

    pub fn step_for_stage(&self, kind: &str, fix: Option<&str>) -> Option<&StepDefinition> {
        if let Some(fix_id) = fix {
            return self.step(fix_id).filter(|step| step.is_fix_stage);
        }
        if let Some(step) = self.step(kind) {
            return Some(step);
        }
        self.steps
            .iter()
            .find(|step| step.stage_kind.as_deref() == Some(kind))
    }

    pub fn required_feature(&self, step_id: &str) -> Option<&str> {
        self.step(step_id)?.required_feature.as_deref()
    }

    pub fn is_valid_fix_chip(&self, fix: &str) -> bool {
        self.steps
            .iter()
            .any(|step| step.fix_kind.as_deref() == Some(fix))
    }

    pub fn is_valid_fix_stage(&self, fix: &str) -> bool {
        self.steps
            .iter()
            .any(|step| step.is_fix_stage && step.fix_kind.as_deref() == Some(fix))
    }

    pub fn is_fix_stage_kind(&self, kind: &str) -> bool {
        self.steps
            .iter()
            .any(|step| step.is_fix_stage && step.stage_kind.as_deref() == Some(kind))
    }

    pub fn is_recipe_stage_kind(&self, kind: &str) -> bool {
        self.steps
            .iter()
            .any(|step| step.in_recipe_stages && step.stage_kind.as_deref() == Some(kind))
    }

    pub fn is_video_primary_stage_kind(&self, kind: &str) -> bool {
        self.steps.iter().any(|step| {
            step.in_recipe_stages
                && step.role.as_deref() == Some("primary")
                && step.stage_kind.as_deref() == Some(kind)
                && step.id != "restore"
        })
    }

    pub fn face_stage_kind(&self) -> &str {
        self.steps
            .iter()
            .find(|step| step.phase == "face")
            .and_then(|step| step.stage_kind.as_deref())
            .expect("step registry must define a face stage")
    }

    pub fn fix_stage_kind(&self, model_stage: &str, fix: &str) -> &str {
        if model_stage == "deblock" {
            return "deblock";
        }
        self.step(fix)
            .and_then(|step| step.stage_kind.as_deref())
            .unwrap_or("restore")
    }

    pub fn fix_row_label(&self, fix: &str) -> &str {
        self.steps
            .iter()
            .find(|step| step.is_fix_stage && step.fix_kind.as_deref() == Some(fix))
            .map(|step| step.row_label.as_str())
            .filter(|label| !label.is_empty())
            .unwrap_or("Fix")
    }

    pub fn stage_label(&self, kind: &str) -> &str {
        self.steps
            .iter()
            .find(|step| step.stage_kind.as_deref() == Some(kind) && !step.stage_label.is_empty())
            .map(|step| step.stage_label.as_str())
            .unwrap_or("Processing")
    }

    pub fn parameter(&self, step_id: &str, param_name: &str) -> Option<&StepParameter> {
        self.step(step_id)?
            .parameters
            .iter()
            .find(|param| param.name == param_name)
    }

    pub fn param_int_in_range(&self, step_id: &str, param_name: &str, value: u32) -> bool {
        let Some(param) = self.parameter(step_id, param_name) else {
            return false;
        };
        let v = f64::from(value);
        param.minimum.is_none_or(|min| v >= min) && param.maximum.is_none_or(|max| v <= max)
    }

    pub fn param_ui_int_in_range(&self, step_id: &str, param_name: &str, value: u32) -> bool {
        let Some(param) = self.parameter(step_id, param_name) else {
            return false;
        };
        let v = f64::from(value);
        let min = param.ui_minimum.or(param.minimum);
        let max = param.ui_maximum.or(param.maximum);
        min.is_none_or(|m| v >= m) && max.is_none_or(|m| v <= m)
    }

    pub fn param_float_in_range(&self, step_id: &str, param_name: &str, value: f64) -> bool {
        let Some(param) = self.parameter(step_id, param_name) else {
            return false;
        };
        value.is_finite()
            && param.minimum.is_none_or(|min| value >= min)
            && param.maximum.is_none_or(|max| value <= max)
    }

    pub fn param_has_choice(&self, step_id: &str, param_name: &str, value: &str) -> bool {
        self.parameter(step_id, param_name)
            .is_some_and(|param| param.choices.iter().any(|choice| choice == value))
    }

    pub fn param_has_int_choice(&self, step_id: &str, param_name: &str, value: u32) -> bool {
        self.parameter(step_id, param_name)
            .is_some_and(|param| param.int_choices.contains(&i64::from(value)))
    }

    pub fn validate_stage_params(
        &self,
        step_id: &str,
        params: Option<&serde_json::Map<String, serde_json::Value>>,
    ) -> Result<(), String> {
        let step = self
            .step(step_id)
            .ok_or_else(|| format!("unknown step '{step_id}'"))?;
        self.validate_step_params(step, params)
    }

    pub fn validate_step_params(
        &self,
        step: &StepDefinition,
        params: Option<&serde_json::Map<String, serde_json::Value>>,
    ) -> Result<(), String> {
        let empty = serde_json::Map::new();
        let map = params.unwrap_or(&empty);
        for key in map.keys() {
            if !step.parameters.iter().any(|param| param.name == *key) {
                return Err(format!("unknown parameter '{key}' for step '{}'", step.id));
            }
        }
        for param in &step.parameters {
            if param.required && !map.contains_key(&param.name) {
                return Err(format!(
                    "missing required parameter '{}' for step '{}'",
                    param.name, step.id
                ));
            }
        }
        for (key, value) in map {
            let param = step
                .parameters
                .iter()
                .find(|candidate| candidate.name == *key)
                .expect("checked above");
            validate_param_value(&step.id, param, value)?;
        }
        Ok(())
    }

    pub fn param_default_u32(&self, step_id: &str, param_name: &str) -> u32 {
        self.parameter(step_id, param_name)
            .and_then(|param| param.default.as_ref())
            .and_then(serde_json::Value::as_u64)
            .and_then(|v| u32::try_from(v).ok())
            .expect("step parameter must have a u32 default")
    }

    pub fn param_default_bool(&self, step_id: &str, param_name: &str) -> bool {
        self.parameter(step_id, param_name)
            .and_then(|param| param.default.as_ref())
            .and_then(serde_json::Value::as_bool)
            .expect("step parameter must have a bool default")
    }

    pub fn param_default_str(&self, step_id: &str, param_name: &str) -> String {
        self.parameter(step_id, param_name)
            .and_then(|param| param.default.as_ref())
            .and_then(serde_json::Value::as_str)
            .map(str::to_owned)
            .expect("step parameter must have a string default")
    }
}

fn validate_param_value(
    step_id: &str,
    param: &StepParameter,
    value: &serde_json::Value,
) -> Result<(), String> {
    if value.is_null() {
        return if param.nullable {
            Ok(())
        } else {
            Err(format!(
                "parameter '{}' for step '{}' cannot be null",
                param.name, step_id
            ))
        };
    }
    match param.kind.as_str() {
        "bool" => {
            if !value.is_boolean() {
                return Err(format!(
                    "parameter '{}' for step '{}' must be a boolean",
                    param.name, step_id
                ));
            }
            Ok(())
        }
        "str" => {
            if !value.is_string() {
                return Err(format!(
                    "parameter '{}' for step '{}' must be a string",
                    param.name, step_id
                ));
            }
            Ok(())
        }
        "choice" => {
            let Some(chosen) = value.as_str() else {
                return Err(format!(
                    "parameter '{}' for step '{}' must be a string choice",
                    param.name, step_id
                ));
            };
            if !param.choices.iter().any(|choice| choice == chosen) {
                return Err(format!(
                    "parameter '{}' for step '{}' must be one of {:?}",
                    param.name, step_id, param.choices
                ));
            }
            Ok(())
        }
        "int_choice" => {
            let Some(chosen) = value.as_i64() else {
                return Err(format!(
                    "parameter '{}' for step '{}' must be an integer choice",
                    param.name, step_id
                ));
            };
            if !param.int_choices.contains(&chosen) {
                return Err(format!(
                    "parameter '{}' for step '{}' must be one of {:?}",
                    param.name, step_id, param.int_choices
                ));
            }
            Ok(())
        }
        "int" => {
            let Some(number) = value.as_i64().map(|v| v as f64) else {
                return Err(format!(
                    "parameter '{}' for step '{}' must be an integer",
                    param.name, step_id
                ));
            };
            check_numeric_bounds(step_id, param, number)
        }
        "number" => {
            let Some(number) = value.as_f64().filter(|v| v.is_finite()) else {
                return Err(format!(
                    "parameter '{}' for step '{}' must be a finite number",
                    param.name, step_id
                ));
            };
            check_numeric_bounds(step_id, param, number)
        }
        other => Err(format!(
            "unsupported parameter kind '{other}' for '{step_id}.{}'",
            param.name
        )),
    }
}

fn check_numeric_bounds(step_id: &str, param: &StepParameter, value: f64) -> Result<(), String> {
    if let Some(min) = param.minimum {
        if value < min {
            return Err(format!(
                "parameter '{}' for step '{}' must be >= {min}",
                param.name, step_id
            ));
        }
    }
    if let Some(max) = param.maximum {
        if value > max {
            return Err(format!(
                "parameter '{}' for step '{}' must be <= {max}",
                param.name, step_id
            ));
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::collections::HashSet;

    use super::*;

    #[test]
    fn bundled_steps_manifest_has_unique_ids_and_valid_phases() {
        let raw: serde_json::Value = serde_json::from_str(STEPS_JSON).unwrap();
        let phases: HashSet<&str> = raw["phases"]
            .as_array()
            .unwrap()
            .iter()
            .map(|phase| phase.as_str().unwrap())
            .collect();
        let reg = registry();
        let mut ids = HashSet::new();
        for step in &reg.steps {
            assert!(
                ids.insert(step.id.as_str()),
                "duplicate step id: {}",
                step.id
            );
            assert!(
                phases.contains(step.phase.as_str()),
                "unknown phase {}",
                step.phase
            );
        }
        assert_eq!(reg.limits().max_fix_stages, 4);
        assert_eq!(reg.limits().max_fix_chips, 5);
        assert_eq!(reg.limits().max_recipe_stages, 6);
        assert!(reg.is_valid_fix_chip("faces"));
        assert!(!reg.is_valid_fix_stage("faces"));
        assert_eq!(reg.fix_stage_kind("", "jpeg"), "deblock");
        assert_eq!(reg.fix_stage_kind("", "blur"), "restore");
        assert_eq!(reg.stage_label("deblock"), "Removing JPEG artifacts");
        assert_eq!(reg.stage_label("edit"), "Preparing edit");
    }

    #[test]
    fn generic_registry_validator_checks_fixture_modelless_step_params() {
        let fixture_json = r#"{
            "schema_version": 1,
            "phases": ["grade"],
            "limits": {
                "max_fix_stages": 4,
                "max_fix_chips": 5,
                "max_face_stages": 1,
                "max_recipe_stages": 6,
                "max_upscale_prefix_stages": 4,
                "max_denoise_prefix_stages": 3,
                "max_video_upscale_prefix_stages": 3,
                "multi_fix_feature": "image_multi_fix",
                "stage_params_feature": "stage_params_and_masks"
            },
            "steps": [
                {
                    "id": "test_tint",
                    "media": ["image"],
                    "phase": "grade",
                    "order": 10,
                    "needs_model": false,
                    "supports_mask": true,
                    "stage_kind": "test_tint",
                    "role": null,
                    "fix_kind": null,
                    "is_fix_stage": false,
                    "fix_chip_order": null,
                    "in_image_pipeline": true,
                    "in_recipe_stages": false,
                    "label": "Test tint",
                    "row_label": "",
                    "short_name": "",
                    "stage_label": "",
                    "required_feature": null,
                    "parameters": [
                        {"name": "amount", "kind": "number", "default": null, "minimum": 0.0, "maximum": 1.0, "choices": [], "int_choices": [], "ui_minimum": null, "ui_maximum": null, "ui_default": null, "nullable": false, "required": true},
                        {"name": "passes", "kind": "int", "default": 1, "minimum": 1, "maximum": 4, "choices": [], "int_choices": [], "ui_minimum": null, "ui_maximum": null, "ui_default": null, "nullable": false, "required": false},
                        {"name": "mode", "kind": "choice", "default": "add", "minimum": null, "maximum": null, "choices": ["add", "multiply"], "int_choices": [], "ui_minimum": null, "ui_maximum": null, "ui_default": null, "nullable": false, "required": false},
                        {"name": "bucket", "kind": "int_choice", "default": 16, "minimum": null, "maximum": null, "choices": [], "int_choices": [8, 16, 32], "ui_minimum": null, "ui_maximum": null, "ui_default": null, "nullable": false, "required": false},
                        {"name": "clamp", "kind": "bool", "default": true, "minimum": null, "maximum": null, "choices": [], "int_choices": [], "ui_minimum": null, "ui_maximum": null, "ui_default": null, "nullable": false, "required": false},
                        {"name": "label", "kind": "str", "default": null, "minimum": null, "maximum": null, "choices": [], "int_choices": [], "ui_minimum": null, "ui_maximum": null, "ui_default": null, "nullable": true, "required": false}
                    ]
                }
            ]
        }"#;
        let reg = StepRegistry::from_json(fixture_json).unwrap();
        let step = reg.step("test_tint").unwrap();
        assert!(!step.needs_model);
        assert!(step.supports_mask);

        let valid = serde_json::json!({
            "amount": 0.5,
            "passes": 2,
            "mode": "multiply",
            "bucket": 32,
            "clamp": false,
            "label": null
        });
        assert!(reg
            .validate_stage_params("test_tint", valid.as_object())
            .is_ok());

        for bad in [
            serde_json::json!({}),
            serde_json::json!({"amount": null}),
            serde_json::json!({"amount": -0.1}),
            serde_json::json!({"amount": 1.1}),
            serde_json::json!({"amount": true}),
            serde_json::json!({"amount": "0.5"}),
            serde_json::json!({"amount": 0.5, "passes": 0}),
            serde_json::json!({"amount": 0.5, "passes": 5}),
            serde_json::json!({"amount": 0.5, "passes": 1.5}),
            serde_json::json!({"amount": 0.5, "mode": "screen"}),
            serde_json::json!({"amount": 0.5, "bucket": 12}),
            serde_json::json!({"amount": 0.5, "clamp": "yes"}),
            serde_json::json!({"amount": 0.5, "label": 42}),
            serde_json::json!({"amount": 0.5, "unknown": 1}),
        ] {
            assert!(
                reg.validate_stage_params("test_tint", bad.as_object())
                    .is_err(),
                "expected error for {bad}"
            );
        }
        assert!(reg.validate_stage_params("test_tint", None).is_err());
    }

    #[test]
    fn grain_is_a_dev_only_step_stage_that_runs_after_the_main_model() {
        let reg = registry();
        let grain = reg.step("grain").unwrap();
        assert!(grain.dev_only && grain.supports_mask && !grain.needs_model);
        assert_eq!(grain.media, vec!["image".to_owned()]);
        assert_eq!(grain.label, "Film grain");
        assert_eq!(grain.required_feature.as_deref(), Some("image_grain"));
        assert_eq!(
            reg.step_stage("grain").map(|(_, placement)| placement),
            Some(StepPlacement::AfterPrimary)
        );
        for id in [
            "upscale",
            "noise",
            "face_restore",
            "deflicker",
            "edit",
            "missing",
        ] {
            assert!(reg.step_stage(id).is_none(), "{id} is not a step stage");
        }
        assert_eq!(reg.limits().max_step_stages, 5);
        assert!(reg
            .steps
            .iter()
            .all(|step| step.dev_only == (step.id == "grain")));

        let check = |params: serde_json::Value, mask: Option<&str>| {
            reg.validate_step_stage("grain", params.as_object(), mask)
                .map(|step| step.id.clone())
        };
        assert_eq!(check(serde_json::json!({}), None).unwrap(), "grain");
        for valid in [
            serde_json::json!({"strength": 0}),
            serde_json::json!({"strength": 100, "seed": 2_147_483_647}),
        ] {
            assert!(
                check(valid.clone(), Some("/in/mask.png")).is_ok(),
                "{valid}"
            );
        }
        for invalid in [
            serde_json::json!({"strength": 101}),
            serde_json::json!({"strength": -1}),
            serde_json::json!({"strength": 12.5}),
            serde_json::json!({"strength": "50"}),
            serde_json::json!({"strength": true}),
            serde_json::json!({"strength": null}),
            serde_json::json!({"seed": -1}),
            serde_json::json!({"seed": 2_147_483_648_u64}),
            serde_json::json!({"grain_size": 2}),
        ] {
            assert!(check(invalid.clone(), None).is_err(), "{invalid}");
        }
        for mask in ["", "  ", "/in/a\0b.png"] {
            assert!(
                check(serde_json::json!({}), Some(mask)).is_err(),
                "{mask:?}"
            );
        }
        assert!(reg
            .validate_step_stage("upscale", None, None)
            .unwrap_err()
            .contains("upscale"));
    }

    #[test]
    fn a_step_with_a_model_is_a_step_stage_only_when_it_names_its_catalog_task() {
        let cutout = testing::cutout();
        let late = testing::step(serde_json::json!({
            "id": "test_late", "media": ["image"], "phase": "grade", "needs_model": true,
            "is_fix_stage": false, "in_image_pipeline": true, "in_recipe_stages": false,
            "model_task": "test_late"
        }));
        let no_task = StepDefinition {
            id: "test_no_task".into(),
            model_task: None,
            ..cutout.clone()
        };
        let fix_with_task = StepDefinition {
            id: "test_fix_with_task".into(),
            is_fix_stage: true,
            ..cutout.clone()
        };
        let reg = registry().with_steps(vec![cutout, late, no_task, fix_with_task]);
        let (step, placement) = reg.step_stage("test_cutout").unwrap();
        assert_eq!(placement, StepPlacement::BeforeFixes);
        assert!(step.needs_model && step.output_alpha);
        assert_eq!(step.model_task.as_deref(), Some("test_cutout"));
        let (late, placement) = reg.step_stage("test_late").unwrap();
        assert_eq!(placement, StepPlacement::AfterPrimary);
        assert!(!late.output_alpha);
        assert!(reg.step_stage("test_no_task").is_none());
        assert!(reg.step_stage("test_fix_with_task").is_none());
        assert!(reg.validate_step_stage("test_cutout", None, None).is_ok());
        assert_eq!(
            reg.limits().max_step_stages,
            registry().limits().max_step_stages + 4
        );

        let (background, placement) = registry().step_stage("remove_background").unwrap();
        assert_eq!(placement, StepPlacement::BeforeFixes);
        assert_eq!(background.model_task.as_deref(), Some("background"));
        assert!(background.output_alpha);
    }

    #[test]
    fn test_steps_replace_the_registry_only_inside_their_closure_on_their_thread() {
        assert!(registry().step("test_cutout").is_none());
        let inside = testing::with_steps(vec![testing::cutout()], || {
            let other_thread = std::thread::spawn(|| registry().step("test_cutout").is_none())
                .join()
                .unwrap();
            (registry().step_stage("test_cutout").is_some(), other_thread)
        });
        assert_eq!(inside, (true, true));
        assert!(registry().step("test_cutout").is_none());
    }

    #[test]
    fn step_stage_placement_follows_the_registry_phase() {
        let fixture_json = r#"{
            "schema_version": 1,
            "phases": ["audio", "geometry", "cleanup", "restore", "primary", "face", "temporal", "grade", "encode"],
            "limits": {
                "max_fix_stages": 4, "max_fix_chips": 5, "max_face_stages": 1,
                "max_recipe_stages": 6, "max_upscale_prefix_stages": 4,
                "max_denoise_prefix_stages": 3, "max_video_upscale_prefix_stages": 3,
                "max_step_stages": 3, "multi_fix_feature": "image_multi_fix"
            },
            "steps": [
                {"id": "level", "media": ["image", "video"], "phase": "geometry", "needs_model": false,
                 "in_image_pipeline": true, "is_fix_stage": false, "in_recipe_stages": false,
                 "stage_kind": null, "role": null, "fix_kind": null, "required_feature": null},
                {"id": "beside_face", "media": ["image"], "phase": "face", "needs_model": false,
                 "in_image_pipeline": true, "is_fix_stage": false, "in_recipe_stages": false,
                 "stage_kind": null, "role": null, "fix_kind": null, "required_feature": null},
                {"id": "not_listed", "media": ["image"], "phase": "grade", "needs_model": false,
                 "in_image_pipeline": false, "is_fix_stage": false, "in_recipe_stages": false,
                 "stage_kind": null, "role": null, "fix_kind": null, "required_feature": null}
            ]
        }"#;
        let reg = StepRegistry::from_json(fixture_json).unwrap();
        assert_eq!(
            reg.step_stage("level").map(|(_, placement)| placement),
            Some(StepPlacement::BeforeFixes)
        );
        assert!(reg.step_stage("beside_face").is_none());
        assert!(reg.step_stage("not_listed").is_none());
        assert!(!reg.step("level").unwrap().dev_only);
        assert_eq!(reg.limits().max_step_stages, 3);
    }
}
