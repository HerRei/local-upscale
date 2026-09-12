//! Backups and a transactional installer for the unpacked Linux preview.
use crate::{
    error::{AppError, AppResult},
    paths::AppPaths,
};
use serde::{Deserialize, Serialize};
use std::{
    fs,
    path::{Component, Path, PathBuf},
    process::{Command, Stdio},
    thread,
    time::{Duration, Instant},
};

pub fn backup_profile(paths: &AppPaths, label: &str) -> AppResult<PathBuf> {
    let backup = paths.next_root.join("backups").join(format!(
        "{}-{}",
        label.replace(|c: char| !c.is_ascii_alphanumeric(), "_"),
        uuid::Uuid::new_v4()
    ));
    fs::create_dir_all(&backup)?;
    for name in [
        "settings.json",
        "benchmark-latest.json",
        "update-channel.json",
        "data-version.json",
    ] {
        let source = paths.next_root.join(name);
        if source.exists() {
            fs::copy(source, backup.join(name))?;
        }
    }
    if paths.database.exists() {
        let db = rusqlite::Connection::open(&paths.database)?;
        db.busy_timeout(Duration::from_secs(10))?;
        db.execute(
            "VACUUM INTO ?1",
            [backup.join("queue.sqlite3").to_string_lossy().as_ref()],
        )?;
    }
    Ok(backup)
}

pub fn before_version(paths: &AppPaths, version: &str) -> AppResult<()> {
    fs::create_dir_all(&paths.next_root)?;
    let marker = paths.next_root.join("data-version.json");
    let old = fs::read_to_string(&marker).ok();
    if old.as_deref() != Some(version) {
        if paths.settings.exists() || paths.database.exists() {
            backup_profile(paths, "before-version")?;
        }
        if !crate::settings::needs_recovery(paths) {
            let temporary = marker.with_extension("tmp");
            fs::write(&temporary, version)?;
            fs::rename(temporary, marker)?;
        }
    }
    Ok(())
}

pub fn extract_portable(archive: &Path, destination: &Path, maximum: u64) -> AppResult<()> {
    fs::create_dir(destination)?;
    let mut tar = tar::Archive::new(flate2::read::GzDecoder::new(fs::File::open(archive)?));
    let mut total = 0u64;
    for entry in tar.entries()? {
        let mut entry = entry?;
        let path = entry.path()?.into_owned();
        if path
            .components()
            .any(|c| !matches!(c, Component::Normal(_)))
            || path.to_string_lossy().contains(['\\', ':'])
        {
            return Err(AppError::Validation("Unsafe path in update archive".into()));
        }
        if !entry.header().entry_type().is_file() && !entry.header().entry_type().is_dir() {
            return Err(AppError::Validation(
                "Links and special files are not allowed in a portable update".into(),
            ));
        }
        total = total
            .checked_add(entry.size())
            .ok_or_else(|| AppError::Validation("Update is too large".into()))?;
        if total > maximum {
            return Err(AppError::Validation(
                "Update exceeds its declared unpacked size".into(),
            ));
        }
        entry.unpack_in(destination)?;
    }
    if !destination.join("localsr-next").is_file() {
        return Err(AppError::Validation(
            "Update has no LocalSR executable".into(),
        ));
    }
    Ok(())
}

#[derive(Deserialize, Serialize)]
pub struct PortablePlan {
    pub current: PathBuf,
    pub staged: PathBuf,
    pub previous: PathBuf,
    pub report: PathBuf,
}

pub fn smoke(executable: &Path, timeout: Duration) -> AppResult<()> {
    let mut child = Command::new(executable)
        .arg("--headless-smoke-test")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()?;
    let start = Instant::now();
    loop {
        if let Some(status) = child.try_wait()? {
            return if status.success() {
                Ok(())
            } else {
                Err(AppError::Worker(
                    "The new version failed its startup check".into(),
                ))
            };
        }
        if start.elapsed() >= timeout {
            let _ = child.kill();
            let _ = child.wait();
            return Err(AppError::Worker(
                "The new version's startup check timed out".into(),
            ));
        }
        thread::sleep(Duration::from_millis(100));
    }
}

pub fn install_portable(
    plan: &PortablePlan,
    verify: impl Fn(&Path) -> AppResult<()>,
) -> AppResult<()> {
    // Replace only the host executable. The versioned engine is installed
    // separately and model checkpoints/profile data live outside this directory.
    if plan.previous.exists() {
        return Err(AppError::Validation(
            "An earlier update still needs recovery".into(),
        ));
    }
    fs::rename(&plan.current, &plan.previous)?;
    let install = fs::rename(&plan.staged, &plan.current)
        .map_err(AppError::from)
        .and_then(|_| verify(&plan.current));
    if let Err(error) = install {
        if plan.current.exists() {
            fs::remove_file(&plan.current)?;
        }
        fs::rename(&plan.previous, &plan.current)?;
        return Err(error);
    }
    fs::remove_file(&plan.previous)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn failed_startup_restores_old_host_and_leaves_models_and_profile_untouched() {
        let root = tempfile::tempdir().unwrap();
        let plan = PortablePlan {
            current: root.path().join("localsr-next"),
            staged: root.path().join("new"),
            previous: root.path().join("previous"),
            report: root.path().join("report"),
        };
        fs::write(&plan.current, b"old app").unwrap();
        fs::write(&plan.staged, b"bad app").unwrap();
        let model = root.path().join("model.pth");
        fs::write(&model, b"model").unwrap();
        assert!(
            install_portable(&plan, |_| Err(AppError::Worker("startup failed".into()))).is_err()
        );
        assert_eq!(fs::read(&plan.current).unwrap(), b"old app");
        assert_eq!(fs::read(model).unwrap(), b"model");
        fs::write(&plan.staged, b"good app").unwrap();
        install_portable(&plan, |_| Ok(())).unwrap();
        assert_eq!(fs::read(&plan.current).unwrap(), b"good app");
        assert!(!plan.previous.exists());
    }
    #[test]
    fn new_version_backs_up_recipes_settings_and_wal_queue_once() {
        let root = tempfile::tempdir().unwrap();
        let paths = AppPaths::under(root.path());
        fs::create_dir_all(&paths.next_root).unwrap();
        crate::settings::save(
            &paths,
            &crate::types::UiSettings {
                tile_size: 128,
                ..Default::default()
            },
            &[],
        )
        .unwrap();
        let db = crate::database::Database::open(&paths.database).unwrap();
        db.insert_media("media", "/video.mp4", "video.mp4", "video")
            .unwrap();
        before_version(&paths, "0.0.13-beta.1").unwrap();
        before_version(&paths, "0.0.13-beta.1").unwrap();
        let backups: Vec<_> = fs::read_dir(paths.next_root.join("backups"))
            .unwrap()
            .collect();
        assert_eq!(backups.len(), 1);
        let path = backups[0].as_ref().unwrap().path();
        assert_eq!(
            fs::read(path.join("settings.json")).unwrap(),
            fs::read(&paths.settings).unwrap()
        );
        let backup = rusqlite::Connection::open(path.join("queue.sqlite3")).unwrap();
        assert_eq!(
            backup
                .query_row("select count(*) from media", [], |r| r.get::<_, i64>(0))
                .unwrap(),
            1
        );
    }
}
