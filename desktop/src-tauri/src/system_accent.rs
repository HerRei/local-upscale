use serde::Serialize;

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct SystemAccent {
    pub light: &'static str,
    pub dark: &'static str,
}

/// Maps `AppleAccentColor` numbers (measured from `NSColor.controlAccentColor` on macOS 27.2)
/// to their light and dark sRGB hex strings.
#[cfg(any(target_os = "macos", test))]
pub fn accent_pair(value: Option<i64>) -> Option<(&'static str, &'static str)> {
    match value {
        None => None,
        Some(-1) => Some(("#989898", "#8c8c8c")),
        Some(0) => Some(("#e0383e", "#ff5257")),
        Some(1) => Some(("#f7821b", "#f7821b")),
        Some(2) => Some(("#ffc726", "#ffc600")),
        Some(3) => Some(("#62ba46", "#62ba46")),
        Some(4) => Some(("#007aff", "#007aff")),
        Some(5) => Some(("#953d96", "#a550a7")),
        Some(6) => Some(("#f74f9e", "#f74f9e")),
        Some(_) => None,
    }
}

#[cfg(target_os = "macos")]
async fn read_macos_accent() -> Option<SystemAccent> {
    let output = tokio::task::spawn_blocking(|| {
        std::process::Command::new("/usr/bin/defaults")
            .args(["read", "-g", "AppleAccentColor"])
            .output()
    })
    .await
    .ok()?
    .ok()?;
    if !output.status.success() {
        return None;
    }
    let parsed = String::from_utf8_lossy(&output.stdout)
        .trim()
        .parse::<i64>()
        .ok()?;
    let (light, dark) = accent_pair(Some(parsed))?;
    Some(SystemAccent { light, dark })
}

#[tauri::command]
pub async fn system_accent() -> Option<SystemAccent> {
    #[cfg(target_os = "macos")]
    {
        read_macos_accent().await
    }
    #[cfg(not(target_os = "macos"))]
    {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::accent_pair;

    #[test]
    fn maps_macos_accent_numbers() {
        assert_eq!(accent_pair(None), None);
        assert_eq!(accent_pair(Some(-1)), Some(("#989898", "#8c8c8c")));
        assert_eq!(accent_pair(Some(0)), Some(("#e0383e", "#ff5257")));
        assert_eq!(accent_pair(Some(1)), Some(("#f7821b", "#f7821b")));
        assert_eq!(accent_pair(Some(2)), Some(("#ffc726", "#ffc600")));
        assert_eq!(accent_pair(Some(3)), Some(("#62ba46", "#62ba46")));
        assert_eq!(accent_pair(Some(4)), Some(("#007aff", "#007aff")));
        assert_eq!(accent_pair(Some(5)), Some(("#953d96", "#a550a7")));
        assert_eq!(accent_pair(Some(6)), Some(("#f74f9e", "#f74f9e")));
        assert_eq!(accent_pair(Some(9)), None);
    }
}
