//! Profile backups, and the transactional swap of a Linux executable (an AppImage or
//! the unpacked host) with crash recovery.
use crate::{
    error::{AppError, AppResult},
    paths::AppPaths,
    process_tree::{lead_own_group, ProcessTree},
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
    let mut command = Command::new(executable);
    command
        .arg("--headless-smoke-test")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    // The check starts the engine as a helper: a check that is stopped has to take it along.
    lead_own_group(&mut command);
    let mut child = command.spawn()?;
    let tree = ProcessTree::new(&child);
    let start = Instant::now();
    loop {
        if let Some(status) = child.try_wait()? {
            tree.terminate();
            return if status.success() {
                Ok(())
            } else {
                Err(AppError::Worker(
                    "The new version failed its startup check".into(),
                ))
            };
        }
        if start.elapsed() >= timeout {
            tree.terminate();
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
    // The source is opened first: a missing one must not leave an empty new file behind.
    let mut input = fs::File::open(source)?;
    let mut output = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(destination)?;
    let result = std::io::copy(&mut input, &mut output)
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
/// passes, make the one at a given position fail, or "crash" there (unwind past all
/// cleanup code, as a killed process would skip it). A step that runs twice (the two
/// journal writes, one extraction per archive entry) is told apart by its position in
/// the run. Outside tests it does nothing.
#[cfg(test)]
pub(crate) mod fault;

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
    /// Make `fs::hard_link` fail, as on a filesystem without hard links: the backup is a copy.
    static NO_HARD_LINKS: std::cell::Cell<bool> = const { std::cell::Cell::new(false) };
}

/// A second name for the working executable: the backup, until it is accepted.
fn link_backup(current: &Path, backup: &Path) -> std::io::Result<()> {
    #[cfg(test)]
    if NO_HARD_LINKS.with(|flag| flag.get()) {
        return Err(std::io::Error::other("hard links are off in this test"));
    }
    fs::hard_link(current, backup)
}

#[cfg(test)]
thread_local! {
    /// Runs once in `recover_swap`, after it took the folder lock: a test removes the
    /// journal here, as a rival start that finished first would.
    static AFTER_JOURNAL_LOCK: std::cell::RefCell<Option<Box<dyn FnOnce()>>> = const { std::cell::RefCell::new(None) };
}

#[cfg(test)]
thread_local! {
    static FAIL_SYNC_AFTER_RENAME: std::cell::RefCell<Option<PathBuf>> = const { std::cell::RefCell::new(None) };
}

pub(crate) fn sync_dir(directory: &Path) -> AppResult<()> {
    #[cfg(test)]
    fault::note_sync(directory);
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

/// Make the content of a file that was just written durable.
pub(crate) fn sync_file(path: &Path) -> std::io::Result<()> {
    #[cfg(test)]
    fault::note_sync(path);
    match fs::OpenOptions::new().write(true).open(path) {
        Ok(file) => file.sync_all(),
        // Written read-only (a mode of the archive): on Windows such a handle cannot
        // flush, elsewhere it can.
        Err(_) => {
            let flushed = fs::File::open(path)?.sync_all();
            if cfg!(windows) {
                Ok(())
            } else {
                flushed
            }
        }
    }
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

/// Remove the half-written files a crash left from `write_atomic(path, ..)`: only names
/// it made (`<stem>.new-<uuid>`), never another file that starts the same way. Callers
/// hold the folder lock, or know that no writer for `path` runs.
pub fn remove_stale_temporaries(path: &Path) {
    let (Some(parent), Some(stem)) = (
        path.parent(),
        path.file_stem().and_then(|stem| stem.to_str()),
    ) else {
        return;
    };
    let prefix = format!("{stem}.new-");
    for entry in fs::read_dir(parent).into_iter().flatten().flatten() {
        let name = entry.file_name();
        let ours = name
            .to_string_lossy()
            .strip_prefix(&prefix)
            .is_some_and(|rest| uuid::Uuid::try_parse(rest).is_ok());
        if ours {
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
        if link_backup(current, &record.backup).is_err() {
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
            // The journal goes only once those removals are durable: a journal that is
            // gone beside files that came back would leave them for good.
            sync_dir(parent)?;
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
    // The removal must be durable before the journal goes (they may be in two folders):
    // otherwise power loss could keep the backup and drop the journal, or keep both and
    // roll back an accepted update.
    fault("backup-remove-sync")?;
    sync_dir(parent)?;
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
    // A crash while the journal was rewritten (the second write) leaves its temporary
    // file beside the old journal; no writer runs while this lock is held.
    remove_stale_temporaries(journal);
    #[cfg(test)]
    AFTER_JOURNAL_LOCK.with(|hook| {
        if let Some(hook) = hook.borrow_mut().take() {
            hook();
        }
    });
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
        sync_dir(parent)?;
        fs::remove_file(journal)?;
        sync_dir(journal_parent)?;
        return Ok(None);
    }
    // The backup was made by `link_backup` or `copy_new`: a plain file. A link, or
    // anything else, put there since is never renamed over the executable; one that
    // cannot be inspected keeps the journal for the next start.
    let backup_is_file = match fs::symlink_metadata(&record.backup) {
        Ok(metadata) if metadata.file_type().is_file() => true,
        Ok(_) => {
            let _ = fs::remove_file(&record.backup);
            let _ = fs::remove_file(&record.staged);
            sync_dir(parent)?;
            fs::remove_file(journal)?;
            sync_dir(journal_parent)?;
            return Err(AppError::Validation(
                "The backup of an interrupted update is not a plain file; it was removed".into(),
            ));
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => false,
        Err(error) => return Err(error.into()),
    };
    let restored = if backup_is_file {
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
    sync_dir(parent)?;
    fault("recover-journal-remove")?;
    fs::remove_file(journal)?;
    sync_dir(journal_parent)?;
    Ok(restored)
}

#[cfg(test)]
mod tests;
