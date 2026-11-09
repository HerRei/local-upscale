//! Profile backups, and the transactional swap of a Linux executable (an AppImage or
//! the unpacked host) with crash recovery.
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

/// Copy `source` into a new file that only this call created: an existing file or link at
/// `destination` is refused, never followed or truncated.
pub fn copy_new(source: &Path, destination: &Path) -> AppResult<()> {
    let mut output = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(destination)?;
    let result = std::io::copy(&mut fs::File::open(source)?, &mut output)
        .and_then(|_| output.set_permissions(fs::metadata(source)?.permissions()))
        .and_then(|_| output.sync_all());
    if let Err(error) = result {
        drop(output);
        let _ = fs::remove_file(destination);
        return Err(error.into());
    }
    Ok(())
}

/// What an unfinished executable swap left behind; the next start rolls it back.
#[derive(Deserialize, Serialize)]
struct SwapJournal {
    current: PathBuf,
    backup: PathBuf,
    staged: PathBuf,
}

#[cfg(test)]
thread_local! {
    static FAIL_SYNC_AFTER_RENAME: std::cell::RefCell<Option<PathBuf>> = const { std::cell::RefCell::new(None) };
}

fn sync_dir(directory: &Path) -> AppResult<()> {
    #[cfg(test)]
    FAIL_SYNC_AFTER_RENAME.with(|fail| {
        let mut fail = fail.borrow_mut();
        if fail
            .as_ref()
            .is_some_and(|path| fs::read(path).ok().as_deref() == Some(b"new"))
        {
            *fail = None;
            return Err(std::io::Error::other("injected sync failure"));
        }
        Ok(())
    })?;
    #[cfg(unix)]
    fs::File::open(directory)?.sync_all()?;
    #[cfg(not(unix))]
    let _ = directory;
    Ok(())
}

/// One swap per folder at a time, across processes and sessions: an advisory lock on
/// the folder itself, released when the returned handle drops.
fn lock_folder(directory: &Path) -> AppResult<fs::File> {
    #[cfg(unix)]
    {
        use fs2::FileExt;
        let folder = fs::File::open(directory)?;
        folder.try_lock_exclusive().map_err(|_| {
            AppError::Validation(
                "Another LocalSR is installing an update; try again when it has finished".into(),
            )
        })?;
        Ok(folder)
    }
    #[cfg(not(unix))]
    {
        let _ = directory;
        Err(AppError::Validation(
            "Replacing the running executable is supported only on Linux".into(),
        ))
    }
}

/// Replace `path` with `bytes` through a fresh file and a rename: a crash leaves the old
/// or the new content, never a prefix, and a link at `path` is replaced, never followed.
pub fn write_atomic(path: &Path, bytes: &[u8]) -> AppResult<()> {
    use std::io::Write;
    let parent = path
        .parent()
        .ok_or_else(|| AppError::Validation("The file has no folder".into()))?;
    let temporary = path.with_extension(format!("new-{}", uuid::Uuid::new_v4()));
    let result = (|| -> AppResult<()> {
        let mut file = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)?;
        file.write_all(bytes)?;
        file.sync_all()?;
        fs::rename(&temporary, path)?;
        sync_dir(parent)
    })();
    if result.is_err() {
        let _ = fs::remove_file(temporary);
    }
    result
}

fn write_journal(journal: &Path, record: &SwapJournal) -> AppResult<()> {
    write_atomic(journal, &serde_json::to_vec(record)?)
}

/// Replace the executable at `current` with the file `stage` writes, keep it only when
/// `check` passes it, and otherwise put the previous one back.
///
/// The working file never leaves its name: a link to it is the backup, and the new file
/// replaces it with one atomic rename. `journal` names the backup and the staged file
/// until the new one is accepted, so a crash is rolled back at the next start
/// (`recover_swap`).
pub fn replace_executable(
    current: &Path,
    journal: &Path,
    stage: impl FnOnce(&Path) -> AppResult<()>,
    check: impl Fn(&Path) -> AppResult<()>,
) -> AppResult<()> {
    let parent = current
        .parent()
        .ok_or_else(|| AppError::Validation("The application has no folder".into()))?;
    let name = current
        .file_name()
        .and_then(|name| name.to_str())
        .ok_or_else(|| AppError::Validation("The application has no usable name".into()))?;
    let journal_parent = journal
        .parent()
        .ok_or_else(|| AppError::Validation("The journal has no folder".into()))?;
    // Lock the shared profile before touching its journal, including recovery reads.
    let _journal_lock = lock_folder(journal_parent)?;
    let _app_lock = if journal_parent.canonicalize()? != parent.canonicalize()? {
        Some(lock_folder(parent)?)
    } else {
        None
    };
    if fs::symlink_metadata(journal).is_ok() {
        return Err(AppError::Validation(
            "An interrupted update requires recovery first".into(),
        ));
    }
    let id = uuid::Uuid::new_v4();
    let record = SwapJournal {
        current: current.to_path_buf(),
        backup: parent.join(format!(".{name}.before-update-{id}")),
        staged: parent.join(format!(".{name}.update-{id}")),
    };
    let mut replaced = false;
    let prepared = (|| -> AppResult<()> {
        stage(&record.staged)?;
        fs::set_permissions(&record.staged, fs::metadata(current)?.permissions())?;
        fs::File::open(&record.staged)?.sync_all()?;
        if fs::hard_link(current, &record.backup).is_err() {
            copy_new(current, &record.backup)?;
        }
        sync_dir(parent)?;
        // A record can only name a complete, durable backup. A crash while copying
        // it leaves the original untouched and no journal that could restore a prefix.
        write_journal(journal, &record)?;
        fs::rename(&record.staged, current)?;
        replaced = true;
        sync_dir(parent)
    })();
    if let Err(error) = prepared {
        if replaced {
            // Keep the journal/backup if restoration itself fails.
            fs::rename(&record.backup, current)?;
            sync_dir(parent)?;
        }
        let _ = fs::remove_file(&record.staged);
        let _ = fs::remove_file(&record.backup);
        if journal.exists() {
            fs::remove_file(journal)?;
            sync_dir(journal_parent)?;
        }
        return Err(error);
    }
    if let Err(error) = check(current) {
        fs::rename(&record.backup, current)?;
        sync_dir(parent)?;
        fs::remove_file(journal)?;
        sync_dir(journal_parent)?;
        return Err(error);
    }
    // Accepted: the journal goes first, so from here on a crash keeps the new file.
    fs::remove_file(journal)?;
    sync_dir(journal_parent)?;
    let _ = fs::remove_file(&record.backup);
    Ok(())
}

/// Roll back a swap that a crash interrupted: the previous executable takes its name
/// back and the staged file goes. Returns the path it restored, if any.
pub fn recover_swap(journal: &Path) -> AppResult<Option<PathBuf>> {
    let journal_parent = journal
        .parent()
        .ok_or_else(|| AppError::Validation("The journal has no folder".into()))?;
    // Without a journal there is nothing to roll back, and the lock is not needed: where
    // locking is unsupported (Windows) no journal can exist, so every start ends here.
    match fs::symlink_metadata(journal) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error.into()),
        Ok(_) => {}
    }
    let _journal_lock = lock_folder(journal_parent)?;
    let metadata = match fs::symlink_metadata(journal) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error.into()),
    };
    if metadata.file_type().is_symlink() {
        fs::remove_file(journal)?;
        sync_dir(journal_parent)?;
        return Err(AppError::Validation(
            "Removed an unsafe update journal link".into(),
        ));
    }
    let bytes = fs::read(journal)?;
    let record: SwapJournal = match serde_json::from_slice(&bytes) {
        Ok(record) => record,
        Err(error) => {
            fs::rename(
                journal,
                journal.with_extension(format!("invalid-{}", uuid::Uuid::new_v4())),
            )?;
            sync_dir(journal_parent)?;
            return Err(error.into());
        }
    };
    let foreign =
        || AppError::Validation("The interrupted update names files outside its folder".into());
    let parent = record.current.parent().ok_or_else(foreign)?;
    let name = record
        .current
        .file_name()
        .and_then(|name| name.to_str())
        .ok_or_else(foreign)?;
    let ours = |path: &Path, kind: &str| {
        path.parent() == Some(parent)
            && path
                .file_name()
                .and_then(|file| file.to_str())
                .is_some_and(|file| file.starts_with(&format!(".{name}.{kind}-")))
    };
    if !ours(&record.backup, "before-update") || !ours(&record.staged, "update") {
        return Err(foreign());
    }
    let _app_lock = if journal_parent.canonicalize()? != parent.canonicalize()? {
        Some(lock_folder(parent)?)
    } else {
        None
    };
    let restored = if record.backup.is_file() {
        fs::rename(&record.backup, &record.current)?;
        sync_dir(parent)?;
        Some(record.current.clone())
    } else {
        None
    };
    let _ = fs::remove_file(&record.staged);
    fs::remove_file(journal)?;
    sync_dir(journal_parent)?;
    Ok(restored)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[cfg(unix)]
    #[test]
    fn round2_journal_link_never_truncates_its_target() {
        let root = tempfile::tempdir().unwrap();
        let current = root.path().join("app");
        let journal = root.path().join("journal");
        fs::write(&current, b"old").unwrap();
        std::os::unix::fs::symlink(&current, &journal).unwrap();
        let _ = replace_executable(
            &current,
            &journal,
            |_| Err(AppError::Worker("stop".into())),
            |_| Ok(()),
        );
        assert_eq!(fs::read(current).unwrap(), b"old");
    }

    #[cfg(unix)]
    #[test]
    fn round2_journal_is_published_only_after_a_complete_backup() {
        let root = tempfile::tempdir().unwrap();
        let current = root.path().join("app");
        let journal = root.path().join("journal");
        fs::write(&current, b"old").unwrap();
        replace_executable(
            &current,
            &journal,
            |staged| {
                assert!(
                    !journal.exists(),
                    "a crash during staging/backup must leave no recovery record"
                );
                fs::write(staged, b"new")?;
                Ok(())
            },
            |_| Ok(()),
        )
        .unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn round2_copy_backup_preserves_executable_permissions() {
        use std::os::unix::fs::PermissionsExt;
        let root = tempfile::tempdir().unwrap();
        let source = root.path().join("app");
        let backup = root.path().join("backup");
        fs::write(&source, b"old").unwrap();
        fs::set_permissions(&source, fs::Permissions::from_mode(0o751)).unwrap();
        copy_new(&source, &backup).unwrap();
        assert_eq!(
            fs::metadata(backup).unwrap().permissions().mode() & 0o777,
            0o751
        );
    }

    #[cfg(unix)]
    #[test]
    fn round2_post_rename_sync_error_rolls_back() {
        let root = tempfile::tempdir().unwrap();
        let current = root.path().join("app");
        let journal = root.path().join("journal");
        fs::write(&current, b"old").unwrap();
        FAIL_SYNC_AFTER_RENAME.with(|fail| *fail.borrow_mut() = Some(current.clone()));
        assert!(replace_executable(
            &current,
            &journal,
            |staged| {
                fs::write(staged, b"new")?;
                Ok(())
            },
            |_| panic!("sync failed first")
        )
        .is_err());
        assert_eq!(fs::read(current).unwrap(), b"old");
    }

    #[test]
    fn round4_starting_without_a_journal_needs_no_lock_and_no_error() {
        let root = tempfile::tempdir().unwrap();
        assert_eq!(
            recover_swap(&root.path().join("missing/journal")).unwrap(),
            None
        );
        // Another process may hold the folder lock (a swap about to write its journal).
        #[cfg(unix)]
        let _held = lock_folder(root.path()).unwrap();
        assert_eq!(recover_swap(&root.path().join("journal")).unwrap(), None);
    }

    #[cfg(unix)]
    #[test]
    fn round2_torn_journal_is_quarantined_once() {
        let root = tempfile::tempdir().unwrap();
        let journal = root.path().join("journal");
        fs::write(&journal, b"{broken").unwrap();
        assert!(recover_swap(&journal).is_err());
        assert!(!journal.exists());
        assert_eq!(recover_swap(&journal).unwrap(), None);
    }

    #[cfg(unix)]
    #[test]
    fn round2_shared_journal_serializes_different_app_folders_and_recovery() {
        let root = tempfile::tempdir().unwrap();
        let journal = root.path().join("journal");
        let a = root.path().join("a/app");
        let b = root.path().join("b/app");
        for path in [&a, &b] {
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(path, b"old").unwrap();
        }
        replace_executable(
            &a,
            &journal,
            |staged| {
                fs::write(staged, b"new")?;
                Ok(())
            },
            |_| {
                assert!(
                    replace_executable(&b, &journal, |_| panic!("journal in use"), |_| Ok(()))
                        .is_err()
                );
                assert!(recover_swap(&journal).is_err());
                Ok(())
            },
        )
        .unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn failed_startup_restores_old_host_and_leaves_models_and_profile_untouched() {
        let root = tempfile::tempdir().unwrap();
        let current = root.path().join("localsr-next");
        let journal = root.path().join("swap-journal.json");
        fs::write(&current, b"old app").unwrap();
        let model = root.path().join("model.pth");
        fs::write(&model, b"model").unwrap();
        let write = |bytes: &'static [u8]| {
            move |staged: &Path| -> AppResult<()> {
                fs::write(staged, bytes)?;
                Ok(())
            }
        };
        assert!(
            replace_executable(&current, &journal, write(b"bad app"), |_| Err(
                AppError::Worker("startup failed".into())
            ))
            .is_err()
        );
        assert_eq!(fs::read(&current).unwrap(), b"old app");
        assert_eq!(fs::read(&model).unwrap(), b"model");
        replace_executable(&current, &journal, write(b"good app"), |_| Ok(())).unwrap();
        assert_eq!(fs::read(&current).unwrap(), b"good app");
        let mut left: Vec<_> = fs::read_dir(root.path())
            .unwrap()
            .map(|entry| entry.unwrap().file_name().into_string().unwrap())
            .collect();
        left.sort();
        assert_eq!(left, ["localsr-next", "model.pth"]);
    }

    #[cfg(unix)]
    #[test]
    fn copy_new_refuses_an_existing_file_or_link() {
        let root = tempfile::tempdir().unwrap();
        let source = root.path().join("source");
        let victim = root.path().join("victim");
        fs::write(&source, b"new").unwrap();
        fs::write(&victim, b"keep").unwrap();
        let link = root.path().join("staged");
        std::os::unix::fs::symlink(&victim, &link).unwrap();
        assert!(copy_new(&source, &link).is_err());
        assert_eq!(fs::read(&victim).unwrap(), b"keep");
        copy_new(&source, &root.path().join("fresh")).unwrap();
        assert_eq!(fs::read(root.path().join("fresh")).unwrap(), b"new");
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

    #[cfg(unix)]
    #[test]
    fn round3_state_files_are_replaced_whole_and_never_through_a_link() {
        let root = tempfile::tempdir().unwrap();
        let target = root.path().join("target");
        let marker = root.path().join("update-pending.json");
        fs::write(&target, b"precious").unwrap();
        std::os::unix::fs::symlink(&target, &marker).unwrap();
        write_atomic(&marker, b"{\"version\":\"1\"}").unwrap();
        assert_eq!(fs::read(&target).unwrap(), b"precious");
        assert_eq!(fs::read(&marker).unwrap(), b"{\"version\":\"1\"}");
        write_atomic(&marker, b"2").unwrap();
        assert_eq!(fs::read(&marker).unwrap(), b"2");
        let names: Vec<_> = fs::read_dir(root.path())
            .unwrap()
            .map(|e| e.unwrap().file_name().into_string().unwrap())
            .collect();
        assert_eq!(names.len(), 2, "no temporary file stays behind: {names:?}");
    }
}
