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

/// Test-only fault injection for the swap, the engine install and the install marker.
/// `fault(step)` runs just before each durable step: a test can record the steps a run
/// passes, make one fail, or "crash" there (unwind past all cleanup code, as a killed
/// process would skip it). Outside tests it does nothing.
#[cfg(test)]
pub(crate) mod fault {
    use std::cell::RefCell;

    #[derive(Clone, Copy, PartialEq, Debug)]
    pub enum Mode {
        Fail,
        Crash,
    }

    /// The payload of a simulated crash.
    pub struct SimulatedCrash;

    #[derive(Default)]
    struct Plan {
        seen: Vec<&'static str>,
        at: Option<(&'static str, Mode)>,
    }

    thread_local! {
        static PLAN: RefCell<Plan> = RefCell::new(Plan::default());
    }

    /// Forget the previous run; with `at`, stop there the next time that step is reached.
    pub fn arm(at: Option<(&'static str, Mode)>) {
        PLAN.with(|plan| {
            *plan.borrow_mut() = Plan {
                seen: Vec::new(),
                at,
            }
        });
    }

    /// The steps passed since `arm`, in order.
    pub fn seen() -> Vec<&'static str> {
        PLAN.with(|plan| plan.borrow().seen.clone())
    }

    pub fn point(step: &'static str) -> std::io::Result<()> {
        let mode = PLAN.with(|plan| {
            let mut plan = plan.borrow_mut();
            plan.seen.push(step);
            plan.at.filter(|(name, _)| *name == step).map(|(_, m)| m)
        });
        match mode {
            Some(Mode::Fail) => Err(std::io::Error::other(format!("injected failure at {step}"))),
            Some(Mode::Crash) => std::panic::resume_unwind(Box::new(SimulatedCrash)),
            None => Ok(()),
        }
    }

    /// `recover_swap`, again while the folder lock is briefly held by a child process
    /// that another test thread forked while it held the lock (the descriptor closes at
    /// the child's exec, a moment after the lock was released here).
    pub fn recover_swap(
        journal: &std::path::Path,
    ) -> crate::error::AppResult<Option<std::path::PathBuf>> {
        for _ in 0..200 {
            match super::recover_swap(journal) {
                Err(crate::error::AppError::Validation(message))
                    if message.starts_with("Another LocalSR") =>
                {
                    std::thread::sleep(std::time::Duration::from_millis(25));
                }
                other => return other,
            }
        }
        super::recover_swap(journal)
    }

    /// Run `action`; Err when it was cut short by a simulated crash.
    pub fn crashed<T>(action: impl FnOnce() -> T) -> Result<T, ()> {
        match std::panic::catch_unwind(std::panic::AssertUnwindSafe(action)) {
            Ok(value) => Ok(value),
            Err(payload) if payload.is::<SimulatedCrash>() => Err(()),
            Err(payload) => std::panic::resume_unwind(payload),
        }
    }
}

#[cfg(test)]
pub(crate) use fault::point as fault;
#[cfg(not(test))]
#[inline(always)]
pub(crate) fn fault(_step: &'static str) -> std::io::Result<()> {
    Ok(())
}

/// What an unfinished executable swap left behind; the next start rolls it back.
#[derive(Deserialize, Serialize)]
struct SwapJournal {
    current: PathBuf,
    backup: PathBuf,
    staged: PathBuf,
    /// Written before the staged file and the backup exist: a crash leaves partial files
    /// that recovery only removes, since the backup may be a prefix. Absent in journals
    /// of earlier versions, which always named a complete backup.
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    preparing: bool,
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
        // In tests another thread may have forked a child while this folder was locked
        // before; the child holds the descriptor, and with it the lock, until its exec a
        // moment later. Wait that out instead of reporting a rival installer.
        #[cfg(test)]
        let attempts = 200;
        #[cfg(not(test))]
        let attempts = 1;
        for attempt in 1..=attempts {
            match folder.try_lock_exclusive() {
                Ok(()) => return Ok(folder),
                Err(_) if attempt < attempts => {
                    std::thread::sleep(std::time::Duration::from_millis(25))
                }
                Err(_) => break,
            }
        }
        Err(AppError::Validation(
            "Another LocalSR is installing an update; try again when it has finished".into(),
        ))
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
        fault("atomic-rename")?;
        fs::rename(&temporary, path)?;
        fault("atomic-sync")?;
        sync_dir(parent)
    })();
    if result.is_err() {
        let _ = fs::remove_file(temporary);
    }
    result
}

/// Remove the half-written files a crash left from `write_atomic(path, ..)`.
pub fn remove_stale_temporaries(path: &Path) {
    let (Some(parent), Some(stem)) = (
        path.parent(),
        path.file_stem().and_then(|stem| stem.to_str()),
    ) else {
        return;
    };
    let prefix = format!("{stem}.new-");
    for entry in fs::read_dir(parent).into_iter().flatten().flatten() {
        if entry.file_name().to_string_lossy().starts_with(&prefix) {
            let _ = fs::remove_file(entry.path());
        }
    }
}

fn write_journal(journal: &Path, record: &SwapJournal) -> AppResult<()> {
    fault(if record.preparing {
        "journal-prepare"
    } else {
        "journal"
    })?;
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
    let mut record = SwapJournal {
        current: current.to_path_buf(),
        backup: parent.join(format!(".{name}.before-update-{id}")),
        staged: parent.join(format!(".{name}.update-{id}")),
        preparing: true,
    };
    let mut replaced = false;
    let prepared = (|| -> AppResult<()> {
        // Names the files first, so a crash while staging or copying leaves none behind.
        write_journal(journal, &record)?;
        fault("stage")?;
        stage(&record.staged)?;
        fs::set_permissions(&record.staged, fs::metadata(current)?.permissions())?;
        fs::File::open(&record.staged)?.sync_all()?;
        fault("backup")?;
        if fs::hard_link(current, &record.backup).is_err() {
            copy_new(current, &record.backup)?;
        }
        fault("backup-sync")?;
        sync_dir(parent)?;
        // Only now can the record restore: it names a complete, durable backup.
        record.preparing = false;
        write_journal(journal, &record)?;
        fault("swap-rename")?;
        fs::rename(&record.staged, current)?;
        replaced = true;
        fault("swap-sync")?;
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
    let checked = fault("check")
        .map_err(AppError::from)
        .and_then(|()| check(current));
    if let Err(error) = checked {
        fs::rename(&record.backup, current)?;
        sync_dir(parent)?;
        fs::remove_file(journal)?;
        sync_dir(journal_parent)?;
        return Err(error);
    }
    // Accepted: the backup goes first. A journal whose backup is gone only drops the
    // staged file at the next start and keeps the new executable, so a crash here leaves
    // neither a stray backup nor a rollback of an accepted update.
    fault("backup-remove")?;
    fs::remove_file(&record.backup)?;
    fault("journal-remove")?;
    fs::remove_file(journal)?;
    fault("journal-remove-sync")?;
    sync_dir(journal_parent)?;
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
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            // A crash while writing the journal leaves a temporary file; the lock tells
            // that apart from a swap that is writing it now.
            if let Ok(_lock) = lock_folder(journal_parent) {
                if fs::symlink_metadata(journal).is_err() {
                    remove_stale_temporaries(journal);
                }
            }
            return Ok(None);
        }
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
    if record.preparing {
        // The backup may be a prefix and the original was never replaced: drop both.
        fault("recover-prepared-remove")?;
        let _ = fs::remove_file(&record.backup);
        let _ = fs::remove_file(&record.staged);
        fs::remove_file(journal)?;
        sync_dir(journal_parent)?;
        return Ok(None);
    }
    let restored = if record.backup.is_file() {
        fault("recover-rename")?;
        fs::rename(&record.backup, &record.current)?;
        // Renaming a hard link onto the file it links to does nothing: the original was
        // never replaced, and the backup name is still there.
        if fs::symlink_metadata(&record.backup).is_ok() {
            fs::remove_file(&record.backup)?;
        }
        sync_dir(parent)?;
        Some(record.current.clone())
    } else {
        None
    };
    fault("recover-staged-remove")?;
    let _ = fs::remove_file(&record.staged);
    fault("recover-journal-remove")?;
    fs::remove_file(journal)?;
    sync_dir(journal_parent)?;
    Ok(restored)
}

#[cfg(test)]
mod tests {
    use super::*;
    /// Everything in the folder besides the executable itself.
    fn leftovers(folder: &Path, executable: &str) -> Vec<String> {
        let mut names: Vec<String> = fs::read_dir(folder)
            .unwrap()
            .map(|entry| entry.unwrap().file_name().into_string().unwrap())
            .filter(|name| name != executable)
            .collect();
        names.sort();
        names
    }

    fn swap_folder() -> (tempfile::TempDir, PathBuf, PathBuf) {
        let root = tempfile::tempdir().unwrap();
        let current = root.path().join("app");
        fs::write(&current, b"old").unwrap();
        let journal = root.path().join("journal");
        (root, current, journal)
    }

    fn swap(current: &Path, journal: &Path, accept: bool) -> AppResult<()> {
        replace_executable(
            current,
            journal,
            |staged| {
                fs::write(staged, b"new")?;
                Ok(())
            },
            |_| {
                if accept {
                    Ok(())
                } else {
                    Err(AppError::Worker("rejected".into()))
                }
            },
        )
    }

    /// After a failure or a crash: the next start's recovery leaves exactly one working
    /// executable at the original path, no journal and no staging or backup file.
    fn assert_recovered(folder: &Path, current: &Path, journal: &Path, expected: &[u8], why: &str) {
        fault::recover_swap(journal).unwrap_or_else(|e| panic!("{why}: recovery failed: {e}"));
        assert_eq!(&fs::read(current).unwrap(), expected, "{why}");
        assert!(!journal.exists(), "{why}: journal left");
        assert_eq!(leftovers(folder, "app"), Vec::<String>::new(), "{why}");
        // A second recovery changes nothing.
        assert_eq!(fault::recover_swap(journal).unwrap(), None, "{why}");
    }

    #[cfg(unix)]
    #[test]
    fn the_swap_survives_a_failure_or_a_crash_at_every_step() {
        for accept in [true, false] {
            let (root, current, journal) = swap_folder();
            fault::arm(None);
            assert_eq!(swap(&current, &journal, accept).is_ok(), accept);
            let steps = fault::seen();
            for expected in [
                "stage",
                "backup",
                "backup-sync",
                "journal",
                "atomic-rename",
                "atomic-sync",
                "swap-rename",
                "swap-sync",
                "check",
            ] {
                assert!(steps.contains(&expected), "{steps:?} lacks {expected}");
            }
            let accepted_at = steps.iter().position(|s| *s == "backup-remove");
            assert_eq!(accepted_at.is_some(), accept);
            for (index, step) in steps.iter().enumerate() {
                for mode in [fault::Mode::Fail, fault::Mode::Crash] {
                    let why = format!("{step} #{index} {mode:?} accept={accept}");
                    let (root, current, journal) = swap_folder();
                    fault::arm(Some((step, mode)));
                    let outcome = fault::crashed(|| swap(&current, &journal, accept));
                    fault::arm(None);
                    assert!(outcome.is_err() || mode == fault::Mode::Fail, "{why}");
                    // The new file stays once it was accepted and its backup is gone;
                    // until then a failure or a crash returns the old one.
                    let kept = accepted_at.is_some_and(|at| index > at);
                    let expected: &[u8] = if kept { b"new" } else { b"old" };
                    assert_recovered(root.path(), &current, &journal, expected, &why);
                }
            }
            drop(root);
        }
    }

    #[cfg(unix)]
    #[test]
    fn a_crash_during_recovery_is_recovered_by_the_next_start() {
        let (_root, current, journal) = swap_folder();
        fault::arm(Some(("check", fault::Mode::Crash)));
        assert!(fault::crashed(|| swap(&current, &journal, true)).is_err());
        fault::arm(None);
        fault::recover_swap(&journal).unwrap();
        let steps = fault::seen();
        assert!(steps.contains(&"recover-rename"), "{steps:?}");
        for (index, step) in steps.iter().enumerate() {
            for mode in [fault::Mode::Fail, fault::Mode::Crash] {
                let why = format!("recovery {step} #{index} {mode:?}");
                let (root, current, journal) = swap_folder();
                fault::arm(Some(("check", fault::Mode::Crash)));
                assert!(fault::crashed(|| swap(&current, &journal, true)).is_err());
                fault::arm(Some((step, mode)));
                let _ = fault::crashed(|| fault::recover_swap(&journal));
                fault::arm(None);
                assert_recovered(root.path(), &current, &journal, b"old", &why);
            }
        }
    }

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
    fn round2_journal_restores_only_after_a_complete_backup() {
        let root = tempfile::tempdir().unwrap();
        let current = root.path().join("app");
        let journal = root.path().join("journal");
        fs::write(&current, b"old").unwrap();
        replace_executable(
            &current,
            &journal,
            |staged| {
                let record: serde_json::Value =
                    serde_json::from_slice(&fs::read(&journal).unwrap()).unwrap();
                assert_eq!(
                    record["preparing"], true,
                    "a crash during staging/backup must leave no record that restores"
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
