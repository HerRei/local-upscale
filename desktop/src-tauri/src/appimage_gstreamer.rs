//! GStreamer registry for the AppImage's own plugins (issue #185).
//!
//! The AppImage ships WebKitGTK with GStreamer 1.20 and its plugins from the build
//! host, and linuxdeploy's hook points GStreamer at those plugins. The plugin
//! registry cache would still be the user's shared one in ~/.cache, written by the
//! system's newer GStreamer with its own plugin paths; each side would keep
//! rebuilding it for the other. The AppImage keeps a registry of its own instead.

use std::ffi::OsString;
use std::path::{Path, PathBuf};

/// Where the AppImage's GStreamer registry goes, or None when GStreamer should be
/// left alone: outside an AppImage, without bundled plugins, or when the user has
/// chosen a registry.
pub fn registry_path(
    appdir: Option<OsString>,
    configured: Option<OsString>,
    xdg_cache_home: Option<OsString>,
    home: Option<OsString>,
) -> Option<PathBuf> {
    if configured.is_some() {
        return None;
    }
    let appdir = PathBuf::from(appdir?);
    if !appdir.join("usr/lib/gstreamer-1.0").is_dir() {
        return None;
    }
    let cache = xdg_cache_home
        .map(PathBuf::from)
        .filter(|path| path.is_absolute())
        .or_else(|| home.map(|home| Path::new(&home).join(".cache")))?;
    Some(cache.join("LocalSR/gstreamer-1.0/registry.x86_64.bin"))
}

/// Run before anything starts WebKitGTK or another thread.
pub fn use_own_registry() {
    let Some(path) = registry_path(
        std::env::var_os("APPDIR"),
        std::env::var_os("GST_REGISTRY_1_0").or_else(|| std::env::var_os("GST_REGISTRY")),
        std::env::var_os("XDG_CACHE_HOME"),
        std::env::var_os("HOME"),
    ) else {
        return;
    };
    if let Some(parent) = path.parent() {
        if let Err(error) = std::fs::create_dir_all(parent) {
            eprintln!("Could not create the GStreamer registry folder {parent:?}: {error}");
            return;
        }
    }
    std::env::set_var("GST_REGISTRY_1_0", path);
}

#[cfg(test)]
mod tests {
    use super::*;

    fn appdir_with_plugins() -> tempfile::TempDir {
        let directory = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(directory.path().join("usr/lib/gstreamer-1.0")).unwrap();
        directory
    }

    #[test]
    fn an_appimage_with_plugins_gets_its_own_registry() {
        let appdir = appdir_with_plugins();
        assert_eq!(
            registry_path(
                Some(appdir.path().into()),
                None,
                Some("/home/u/.cache-x".into()),
                Some("/home/u".into())
            ),
            Some(PathBuf::from(
                "/home/u/.cache-x/LocalSR/gstreamer-1.0/registry.x86_64.bin"
            ))
        );
        // A relative XDG_CACHE_HOME is invalid by the spec; HOME/.cache is used.
        assert_eq!(
            registry_path(
                Some(appdir.path().into()),
                None,
                Some("relative".into()),
                Some("/home/u".into())
            ),
            Some(PathBuf::from(
                "/home/u/.cache/LocalSR/gstreamer-1.0/registry.x86_64.bin"
            ))
        );
    }

    #[test]
    fn gstreamer_is_left_alone_outside_an_appimage_with_plugins_or_when_configured() {
        let appdir = appdir_with_plugins();
        let home = Some(OsString::from("/home/u"));
        assert_eq!(registry_path(None, None, None, home.clone()), None);
        let bare = tempfile::tempdir().unwrap();
        assert_eq!(
            registry_path(Some(bare.path().into()), None, None, home.clone()),
            None
        );
        assert_eq!(
            registry_path(
                Some(appdir.path().into()),
                Some("/tmp/mine.bin".into()),
                None,
                home
            ),
            None
        );
        assert_eq!(
            registry_path(Some(appdir.path().into()), None, None, None),
            None
        );
    }
}
