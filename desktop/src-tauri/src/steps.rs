use std::sync::LazyLock;

use serde::Deserialize;

const STEPS_JSON: &str = include_str!("../resources/steps.json");

static REGISTRY: LazyLock<StepRegistry> =
    LazyLock::new(|| serde_json::from_str(STEPS_JSON).expect("bundled steps.json must be valid"));

pub fn registry() -> &'static StepRegistry {
    &REGISTRY
}

#[derive(Clone, Debug, Deserialize)]
pub struct StepRegistry {
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
    pub multi_fix_feature: String,
}

#[derive(Clone, Debug, Deserialize)]
pub struct StepParameter {
    pub name: String,
    pub default: Option<serde_json::Value>,
    pub minimum: Option<f64>,
    pub maximum: Option<f64>,
    #[serde(default)]
    pub choices: Vec<String>,
    #[serde(default)]
    pub int_choices: Vec<u32>,
    pub ui_minimum: Option<f64>,
    pub ui_maximum: Option<f64>,
}

#[derive(Clone, Debug, Deserialize)]
pub struct StepDefinition {
    pub id: String,
    pub phase: String,
    pub stage_kind: Option<String>,
    pub role: Option<String>,
    pub fix_kind: Option<String>,
    pub is_fix_stage: bool,
    pub in_recipe_stages: bool,
    #[serde(default)]
    pub stage_label: String,
    pub required_feature: Option<String>,
    #[serde(default)]
    pub parameters: Vec<StepParameter>,
}

impl StepRegistry {
    pub fn limits(&self) -> &StepLimits {
        &self.limits
    }

    pub fn step(&self, id: &str) -> Option<&StepDefinition> {
        self.steps.iter().find(|step| step.id == id)
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
            .is_some_and(|param| param.int_choices.contains(&value))
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
}
