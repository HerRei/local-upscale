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
/// passes, make the one at a given position fail, or "crash" there (unwind past all
/// cleanup code, as a killed process would skip it). A step that runs twice (the two
/// journal writes, one extraction per archive entry) is told apart by its position in
/// the run. Outside tests it does nothing.
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
        /// The position in `seen` to stop at, and how.
        at: Option<(usize, Mode)>,
        /// Directories and files made durable, with the number of steps passed before.
        synced: Vec<(usize, std::path::PathBuf)>,
    }

    thread_local! {
        static PLAN: RefCell<Plan> = RefCell::new(Plan::default());
    }

    /// Forget the previous run; with `at`, stop at the step with that position in the run
    /// (an index into what `seen` returned for an unarmed run).
    pub fn arm(at: Option<(usize, Mode)>) {
        PLAN.with(|plan| {
            *plan.borrow_mut() = Plan {
                seen: Vec::new(),
                at,
                synced: Vec::new(),
            }
        });
    }

    /// The steps passed since `arm`, in order.
    pub fn seen() -> Vec<&'static str> {
        PLAN.with(|plan| plan.borrow().seen.clone())
    }

    /// Record that `path` was made durable now.
    pub fn note_sync(path: &std::path::Path) {
        PLAN.with(|plan| {
            let mut plan = plan.borrow_mut();
            let passed = plan.seen.len();
            plan.synced.push((passed, path.to_path_buf()));
        });
    }

    /// What was made durable since `arm`: each path with the number of steps passed
    /// before it, so a sync after step `i` and before step `i + 1` is recorded as `i + 1`.
    pub fn synced() -> Vec<(usize, std::path::PathBuf)> {
        PLAN.with(|plan| plan.borrow().synced.clone())
    }

    pub fn point(step: &'static str) -> std::io::Result<()> {
        let mode = PLAN.with(|plan| {
            let mut plan = plan.borrow_mut();
            let index = plan.seen.len();
            plan.seen.push(step);
            // One stop per run: a failure is not repeated by a later step.
            match plan.at {
                Some((at, mode)) if at == index => {
                    plan.at = None;
                    Some(mode)
                }
                _ => None,
            }
        });
        match mode {
            Some(Mode::Fail) => Err(std::io::Error::other(format!("injected failure at {step}"))),
            Some(Mode::Crash) => std::panic::resume_unwind(Box::new(SimulatedCrash)),
            None => Ok(()),
        }
    }

    #[cfg(unix)]
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
mod tests {
    use super::*;
    /// The folders of one swap: the executable's, and the profile's with the journal; the
    /// same folder, or two (a swap then takes two locks).
    #[cfg(unix)]
    struct Swap {
        _root: tempfile::TempDir,
        folders: Vec<PathBuf>,
        current: PathBuf,
        journal: PathBuf,
    }

    #[cfg(unix)]
    impl Swap {
        fn new(separate: bool) -> Swap {
            let root = tempfile::tempdir().unwrap();
            let (app, profile) = if separate {
                (root.path().join("app-folder"), root.path().join("profile"))
            } else {
                (root.path().to_path_buf(), root.path().to_path_buf())
            };
            fs::create_dir_all(&app).unwrap();
            fs::create_dir_all(&profile).unwrap();
            let current = app.join("app");
            fs::write(&current, b"old").unwrap();
            let journal = profile.join("journal");
            let folders = if separate {
                vec![app, profile]
            } else {
                vec![app]
            };
            Swap {
                _root: root,
                folders,
                current,
                journal,
            }
        }

        /// The executable's folder and the journal's.
        fn app_and_profile(&self) -> (PathBuf, PathBuf) {
            (
                self.current.parent().unwrap().to_path_buf(),
                self.journal.parent().unwrap().to_path_buf(),
            )
        }

        /// Everything in the folders besides the executable itself.
        fn leftovers(&self) -> Vec<String> {
            let mut names = Vec::new();
            for folder in &self.folders {
                for entry in fs::read_dir(folder).unwrap() {
                    let path = entry.unwrap().path();
                    if path != self.current && path.is_file() {
                        names.push(path.file_name().unwrap().to_string_lossy().into_owned());
                    }
                }
            }
            names.sort();
            names
        }
    }

    #[cfg(unix)]
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
    #[cfg(unix)]
    fn assert_recovered(swap: &Swap, expected: &[u8], why: &str) {
        fault::recover_swap(&swap.journal)
            .unwrap_or_else(|e| panic!("{why}: recovery failed: {e}"));
        assert_eq!(&fs::read(&swap.current).unwrap(), expected, "{why}");
        assert!(!swap.journal.exists(), "{why}: journal left");
        assert_eq!(swap.leftovers(), Vec::<String>::new(), "{why}");
        // A second recovery changes nothing.
        assert_eq!(fault::recover_swap(&swap.journal).unwrap(), None, "{why}");
    }

    /// Every combination the swap runs in: the journal beside the executable or in
    /// another folder, a hard-linked or a copied backup, an accepted or a refused update.
    #[cfg(unix)]
    fn layouts() -> Vec<(bool, bool, bool)> {
        let mut all = Vec::new();
        for separate in [false, true] {
            for copy_backup in [false, true] {
                for accept in [true, false] {
                    all.push((separate, copy_backup, accept));
                }
            }
        }
        all
    }

    /// A crashed swap, left as a killed process leaves it: crashed at step `index`.
    #[cfg(unix)]
    fn crashed_swap(separate: bool, accept: bool, index: usize) -> Swap {
        let folder = Swap::new(separate);
        fault::arm(Some((index, fault::Mode::Crash)));
        let outcome = fault::crashed(|| swap(&folder.current, &folder.journal, accept));
        fault::arm(None);
        assert!(outcome.is_err(), "no crash at step {index}");
        folder
    }

    #[cfg(unix)]
    #[test]
    fn the_swap_survives_a_failure_or_a_crash_at_every_step() {
        for (separate, copy_backup, accept) in layouts() {
            NO_HARD_LINKS.with(|flag| flag.set(copy_backup));
            let folder = Swap::new(separate);
            fault::arm(None);
            assert_eq!(
                swap(&folder.current, &folder.journal, accept).is_ok(),
                accept
            );
            let steps = fault::seen();
            for expected in [
                "stage",
                "backup",
                "backup-sync",
                "journal-prepare",
                "journal",
                "atomic-rename",
                "atomic-sync",
                "swap-rename",
                "swap-sync",
                "check",
            ] {
                assert!(steps.contains(&expected), "{steps:?} lacks {expected}");
            }
            // The journal is written twice: the steps of the second write are in the run.
            assert_eq!(
                steps.iter().filter(|s| **s == "atomic-rename").count(),
                2,
                "{steps:?}"
            );
            let accepted_at = steps.iter().position(|s| *s == "backup-remove");
            assert_eq!(accepted_at.is_some(), accept);
            for (index, step) in steps.iter().enumerate() {
                for mode in [fault::Mode::Fail, fault::Mode::Crash] {
                    let why = format!(
                        "{step} #{index} {mode:?} separate={separate} copy={copy_backup} accept={accept}"
                    );
                    let folder = Swap::new(separate);
                    fault::arm(Some((index, mode)));
                    let outcome = fault::crashed(|| swap(&folder.current, &folder.journal, accept));
                    fault::arm(None);
                    match (mode, &outcome) {
                        (fault::Mode::Crash, Err(())) => {}
                        (fault::Mode::Fail, Ok(Err(_))) => {}
                        _ => panic!("{why}: the injected stop did not stop the swap"),
                    }
                    // Before any recovery there is always a working executable: the old
                    // one, or the new one once it is in place.
                    let content = fs::read(&folder.current)
                        .unwrap_or_else(|e| panic!("{why}: no executable before recovery: {e}"));
                    assert!(content == b"old" || content == b"new", "{why}");
                    // The new file stays once it was accepted and its backup is gone;
                    // until then a failure or a crash returns the old one.
                    let kept = accepted_at.is_some_and(|at| index > at);
                    let expected: &[u8] = if kept { b"new" } else { b"old" };
                    assert_recovered(&folder, expected, &why);
                }
            }
        }
        NO_HARD_LINKS.with(|flag| flag.set(false));
    }

    #[cfg(unix)]
    #[test]
    fn a_crash_during_recovery_is_recovered_by_the_next_start() {
        for (separate, copy_backup, accept) in layouts() {
            NO_HARD_LINKS.with(|flag| flag.set(copy_backup));
            let folder = Swap::new(separate);
            fault::arm(None);
            swap(&folder.current, &folder.journal, accept).ok();
            let swap_steps = fault::seen();
            let accepted_at = swap_steps.iter().position(|s| *s == "backup-remove");
            // The swap is killed at every step in turn; each of those states is recovered
            // by a start that is itself killed at every step of its recovery.
            for (base, base_step) in swap_steps.iter().enumerate() {
                let kept = accepted_at.is_some_and(|at| base > at);
                let expected: &[u8] = if kept { b"new" } else { b"old" };
                let probe = crashed_swap(separate, accept, base);
                fault::arm(None);
                fault::recover_swap(&probe.journal).unwrap();
                let steps = fault::seen();
                for (index, step) in steps.iter().enumerate() {
                    for mode in [fault::Mode::Fail, fault::Mode::Crash] {
                        let why = format!(
                            "swap {} #{base}, recovery {step} #{index} {mode:?} \
                             separate={separate} copy={copy_backup} accept={accept}",
                            base_step
                        );
                        let folder = crashed_swap(separate, accept, base);
                        fault::arm(Some((index, mode)));
                        let _ = fault::crashed(|| fault::recover_swap(&folder.journal));
                        fault::arm(None);
                        assert_recovered(&folder, expected, &why);
                    }
                }
            }
        }
        NO_HARD_LINKS.with(|flag| flag.set(false));
    }

    /// Which of the synced paths came after `after` steps, in order.
    #[cfg(unix)]
    fn synced_after(after: usize) -> Vec<PathBuf> {
        fault::synced()
            .into_iter()
            .filter(|(passed, _)| *passed > after)
            .map(|(_, path)| path)
            .collect()
    }

    /// A removal in the executable's folder is made durable before the journal that
    /// describes it goes: the journal usually lives in another folder, and a journal
    /// removed for good beside a backup that came back would restore the old executable
    /// over an accepted update (or leave a stray backup of hundreds of megabytes).
    #[cfg(unix)]
    #[test]
    fn removals_are_durable_before_the_journal_is_removed() {
        // An accepted swap: the backup goes, then the journal.
        let folder = Swap::new(true);
        let (app, _) = folder.app_and_profile();
        fault::arm(None);
        swap(&folder.current, &folder.journal, true).unwrap();
        let steps = fault::seen();
        let removed = steps.iter().position(|s| *s == "backup-remove").unwrap();
        let journal_removed = steps.iter().position(|s| *s == "journal-remove").unwrap();
        assert!(
            fault::synced().iter().any(|(passed, dir)| *dir == app
                && *passed > removed
                && *passed <= journal_removed),
            "the backup's removal was not synced before the journal's: {:?}",
            fault::synced()
        );

        // A swap that fails while staging: its staged file and backup go, then the journal.
        let failed_at = steps.iter().position(|s| *s == "backup-sync").unwrap();
        let folder = Swap::new(true);
        let (app, profile) = folder.app_and_profile();
        fault::arm(Some((failed_at, fault::Mode::Fail)));
        assert!(swap(&folder.current, &folder.journal, true).is_err());
        let order = synced_after(failed_at);
        let app_at = order.iter().position(|dir| *dir == app);
        let profile_at = order.iter().position(|dir| *dir == profile);
        assert!(
            app_at.is_some() && app_at < profile_at,
            "the cleanup was not synced before the journal's removal: {order:?}"
        );

        // Recovery: after a crash while staging (the journal only drops files), and after
        // one with a complete backup (rolled back; or kept, the backup being gone).
        for (crash_at, removal) in [
            ("backup-sync", "recover-prepared-remove"),
            ("swap-rename", "recover-staged-remove"),
            ("journal-remove", "recover-staged-remove"),
        ] {
            let index = steps.iter().position(|s| *s == crash_at).unwrap();
            let folder = crashed_swap(true, true, index);
            let (app, profile) = folder.app_and_profile();
            fault::arm(None);
            fault::recover_swap(&folder.journal).unwrap();
            let removal = fault::seen().iter().position(|s| *s == removal).unwrap();
            let order = synced_after(removal);
            let app_at = order.iter().position(|dir| *dir == app);
            let profile_at = order.iter().rposition(|dir| *dir == profile);
            assert!(
                app_at.is_some() && app_at < profile_at,
                "recovery after {crash_at}: not synced before the journal's removal: {order:?}"
            );
        }
    }

    /// The backup is renamed over the executable only when it is the plain file the
    /// swap made; a link put there, or one that cannot be inspected, is not followed.
    #[cfg(unix)]
    #[test]
    fn recovery_does_not_follow_a_linked_backup() {
        let folder = Swap::new(false);
        fault::arm(None);
        swap(&folder.current, &folder.journal, true).unwrap();
        let steps = fault::seen();
        let at = steps.iter().position(|s| *s == "check").unwrap();
        let folder = crashed_swap(false, true, at);
        let record: serde_json::Value =
            serde_json::from_slice(&fs::read(&folder.journal).unwrap()).unwrap();
        let backup = PathBuf::from(record["backup"].as_str().unwrap());
        let victim = folder.current.with_file_name("victim");
        fs::write(&victim, b"precious").unwrap();
        fs::remove_file(&backup).unwrap();
        std::os::unix::fs::symlink(&victim, &backup).unwrap();
        assert!(recover_swap(&folder.journal).is_err());
        assert_eq!(fs::read(&victim).unwrap(), b"precious");
        assert!(
            !fs::symlink_metadata(&folder.current)
                .unwrap()
                .file_type()
                .is_symlink(),
            "the executable became a link"
        );
        assert!(!folder.journal.exists());
        assert!(fs::symlink_metadata(&backup).is_err(), "the link stays");
    }

    /// A temporary file of the journal's from a crash goes; a file that only starts with
    /// the same words does not.
    #[test]
    fn only_temporary_files_of_write_atomic_are_removed() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("swap-journal.json");
        let own = root
            .path()
            .join(format!("swap-journal.new-{}", uuid::Uuid::new_v4()));
        fs::write(&own, b"half").unwrap();
        let others = [
            "swap-journal.new-notes.txt",
            "swap-journal.new-",
            "swap-journal.new-backup",
            "other.new-6a5a8b3e-0000-4000-8000-000000000000",
        ];
        for name in others {
            fs::write(root.path().join(name), b"mine").unwrap();
        }
        remove_stale_temporaries(&path);
        assert!(!own.exists());
        for name in others {
            assert_eq!(fs::read(root.path().join(name)).unwrap(), b"mine", "{name}");
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

    /// A gzip-compressed tar with the given entries (name, type, content, link target);
    /// a name is written into the header bytes as it is, so `../x` and the like survive.
    fn portable_archive(
        folder: &Path,
        entries: &[(&str, tar::EntryType, &[u8], Option<&str>)],
    ) -> PathBuf {
        let mut builder = tar::Builder::new(Vec::new());
        for (name, kind, content, link) in entries {
            let mut header = tar::Header::new_gnu();
            header.set_entry_type(*kind);
            header.set_mode(0o755);
            header.set_size(content.len() as u64);
            if let Some(link) = link {
                header.set_link_name(link).unwrap();
            }
            header.as_mut_bytes()[..100].fill(0);
            header.as_mut_bytes()[..name.len()].copy_from_slice(name.as_bytes());
            header.set_cksum();
            builder.append(&header, *content).unwrap();
        }
        let mut gzip = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::fast());
        std::io::Write::write_all(&mut gzip, &builder.into_inner().unwrap()).unwrap();
        let path = folder.join("update.tar.gz");
        fs::write(&path, gzip.finish().unwrap()).unwrap();
        path
    }

    fn message(result: AppResult<()>) -> String {
        result.expect_err("expected a refusal").to_string()
    }

    #[test]
    fn a_portable_update_with_files_and_folders_is_unpacked() {
        use tar::EntryType::{Directory, Regular};
        let root = tempfile::tempdir().unwrap();
        let archive = portable_archive(
            root.path(),
            &[
                ("localsr-next", Regular, b"exe", None),
                ("lib/", Directory, b"", None),
                ("lib/data.bin", Regular, b"data", None),
            ],
        );
        let unpacked = root.path().join("unpacked");
        extract_portable(&archive, &unpacked, 7).unwrap();
        assert_eq!(fs::read(unpacked.join("localsr-next")).unwrap(), b"exe");
        assert_eq!(fs::read(unpacked.join("lib/data.bin")).unwrap(), b"data");
    }

    #[test]
    fn a_portable_update_with_an_unsafe_path_writes_nothing_outside() {
        use tar::EntryType::Regular;
        for name in [
            "../outside",
            "a\\b",
            "a:b",
            "/absolute",
            "lib/../../outside",
        ] {
            let root = tempfile::tempdir().unwrap();
            let archive = portable_archive(
                root.path(),
                &[
                    ("localsr-next", Regular, b"exe", None),
                    (name, Regular, b"x", None),
                ],
            );
            let unpacked = root.path().join("unpacked");
            let text = message(extract_portable(&archive, &unpacked, 100));
            assert!(text.contains("Unsafe path"), "{name}: {text}");
            for stray in ["outside", "absolute"] {
                assert!(!root.path().join(stray).exists(), "{name}");
            }
            assert!(!unpacked.join("a\\b").exists() && !unpacked.join("a:b").exists());
        }
    }

    #[test]
    fn a_portable_update_refuses_links_and_special_files() {
        use tar::EntryType::{Char, Fifo, Link, Symlink};
        for kind in [Symlink, Link, Fifo, Char] {
            let root = tempfile::tempdir().unwrap();
            let archive = portable_archive(
                root.path(),
                &[
                    ("localsr-next", tar::EntryType::Regular, b"exe", None),
                    ("odd", kind, b"", Some("localsr-next")),
                ],
            );
            let unpacked = root.path().join("unpacked");
            let text = message(extract_portable(&archive, &unpacked, 100));
            assert!(text.contains("Links and special files"), "{kind:?}: {text}");
            assert!(
                fs::symlink_metadata(unpacked.join("odd")).is_err(),
                "{kind:?}"
            );
        }
    }

    /// The declared size is the most an update may unpack to: exactly that is fine, one
    /// byte more is refused before it is unpacked.
    #[test]
    fn a_portable_update_may_unpack_to_its_declared_size_and_not_beyond() {
        use tar::EntryType::Regular;
        let root = tempfile::tempdir().unwrap();
        let archive = portable_archive(
            root.path(),
            &[
                ("localsr-next", Regular, b"abc", None),
                ("data", Regular, b"def", None),
            ],
        );
        extract_portable(&archive, &root.path().join("exact"), 6).unwrap();
        for maximum in [5, 3, 0] {
            let unpacked = root.path().join(format!("over-{maximum}"));
            let text = message(extract_portable(&archive, &unpacked, maximum));
            assert!(text.contains("exceeds its declared"), "{maximum}: {text}");
        }
        // Refused while unpacking, not afterwards: the second file never reached the disk.
        assert!(!root.path().join("over-5/data").exists());
    }

    #[test]
    fn a_portable_update_needs_its_executable() {
        use tar::EntryType::Regular;
        let root = tempfile::tempdir().unwrap();
        let archive = portable_archive(root.path(), &[("other", Regular, b"x", None)]);
        let text = message(extract_portable(
            &archive,
            &root.path().join("unpacked"),
            10,
        ));
        assert!(text.contains("no LocalSR executable"), "{text}");
        // A folder of that name is not the executable.
        let archive = portable_archive(
            root.path(),
            &[("localsr-next/", tar::EntryType::Directory, b"", None)],
        );
        let text = message(extract_portable(&archive, &root.path().join("second"), 10));
        assert!(text.contains("no LocalSR executable"), "{text}");
    }

    /// An executable file that runs `body` with `sh`; the spawn is retried while another
    /// test thread's fork still holds the file open for writing.
    #[cfg(unix)]
    fn script(folder: &Path, name: &str, body: &str) -> PathBuf {
        use std::os::unix::fs::PermissionsExt;
        let path = folder.join(name);
        fs::write(&path, format!("#!/bin/sh\n{body}\n")).unwrap();
        fs::set_permissions(&path, fs::Permissions::from_mode(0o755)).unwrap();
        path
    }

    #[cfg(unix)]
    fn smoke_retrying(executable: &Path, timeout: Duration) -> (AppResult<()>, Duration) {
        let start = Instant::now();
        loop {
            let result = smoke(executable, timeout);
            let busy = matches!(&result, Err(error) if error.to_string().contains("busy"));
            if !busy || start.elapsed() > Duration::from_secs(20) {
                return (result, start.elapsed());
            }
            thread::sleep(Duration::from_millis(50));
        }
    }

    #[cfg(unix)]
    #[test]
    fn the_startup_check_accepts_success_refuses_failure_and_stops_a_hang() {
        let root = tempfile::tempdir().unwrap();
        // Success inside a long deadline: not cut short, and not left waiting for it.
        let good = script(root.path(), "good", "[ \"$1\" = --headless-smoke-test ]");
        let (result, took) = smoke_retrying(&good, Duration::from_secs(30));
        result.unwrap();
        assert!(took < Duration::from_secs(10), "{took:?}");
        // A failing check is a failure, not a timeout.
        let bad = script(root.path(), "bad", "exit 3");
        let (result, _) = smoke_retrying(&bad, Duration::from_secs(30));
        let text = message(result);
        assert!(text.contains("failed its startup check"), "{text}");
        // A hang is killed once the deadline has passed, and not before.
        let pid_file = root.path().join("pid");
        let hang = script(
            root.path(),
            "hang",
            &format!("echo $$ > '{}'\nexec sleep 60", pid_file.display()),
        );
        let timeout = Duration::from_millis(700);
        let (result, took) = smoke_retrying(&hang, timeout);
        let text = message(result);
        assert!(text.contains("timed out"), "{text}");
        assert!(
            took >= timeout,
            "stopped after {took:?}, before {timeout:?}"
        );
        assert!(took < Duration::from_secs(10), "{took:?}");
        let pid = fs::read_to_string(&pid_file).unwrap();
        let alive = Command::new("kill")
            .args(["-0", pid.trim()])
            .stderr(Stdio::null())
            .status()
            .unwrap()
            .success();
        assert!(!alive, "the hung check is still running");
    }

    #[cfg(unix)]
    #[test]
    fn a_hung_startup_check_takes_the_helpers_it_started_with_it() {
        let root = tempfile::tempdir().unwrap();
        let helper_pid = root.path().join("helper-pid");
        let hang = script(
            root.path(),
            "hang",
            &format!("sleep 60 &\necho $! > '{}'\nwait", helper_pid.display()),
        );
        let (result, _) = smoke_retrying(&hang, Duration::from_millis(700));
        assert!(message(result).contains("timed out"));
        let pid = fs::read_to_string(&helper_pid).unwrap();
        // A process that ended but has not been reaped yet still answers `kill -0`.
        let alive = || {
            let listing = Command::new("ps")
                .args(["-o", "stat=", "-p", pid.trim()])
                .output()
                .unwrap();
            let state = String::from_utf8_lossy(&listing.stdout);
            !state.trim().is_empty() && !state.trim().starts_with('Z')
        };
        // The signal is delivered at once; the system needs a moment to reap.
        let deadline = Instant::now() + Duration::from_secs(5);
        while alive() && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(20));
        }
        assert!(!alive(), "the helper of the hung check is still running");
    }

    /// A journal as an unfinished swap writes it, with the names given.
    #[cfg(unix)]
    fn write_crafted_journal(journal: &Path, current: &Path, backup: &Path, staged: &Path) {
        fs::write(
            journal,
            serde_json::to_vec(&serde_json::json!({
                "current": current, "backup": backup, "staged": staged,
            }))
            .unwrap(),
        )
        .unwrap();
    }

    /// Recovery moves only the files a swap names beside the executable: a journal that
    /// points elsewhere, or at other names, never replaces the working executable.
    #[cfg(unix)]
    #[test]
    fn recovery_refuses_a_journal_that_names_files_it_does_not_own() {
        let root = tempfile::tempdir().unwrap();
        let app = root.path().join("app-folder");
        let elsewhere = root.path().join("elsewhere");
        fs::create_dir_all(&app).unwrap();
        fs::create_dir_all(&elsewhere).unwrap();
        let current = app.join("app");
        let journal = root.path().join("journal");
        let good_backup = app.join(".app.before-update-1");
        let good_staged = app.join(".app.update-1");
        // (backup, staged): one wrong name at a time.
        let cases = [
            (elsewhere.join(".app.before-update-1"), good_staged.clone()),
            (good_backup.clone(), elsewhere.join(".app.update-1")),
            (app.join(".other.before-update-1"), good_staged.clone()),
            (good_backup.clone(), app.join(".other.update-1")),
            (app.join(".app.update-1"), app.join(".app.update-2")),
            (good_backup.clone(), app.join(".app.before-update-2")),
            (app.join("app"), good_staged.clone()),
            (good_backup.clone(), app.join("app")),
        ];
        for (backup, staged) in cases {
            let why = format!("{} / {}", backup.display(), staged.display());
            fs::write(&current, b"working").unwrap();
            for path in [&backup, &staged] {
                if path.as_path() != current.as_path() {
                    fs::write(path, b"other").unwrap();
                }
            }
            write_crafted_journal(&journal, &current, &backup, &staged);
            let text = message(recover_swap(&journal).map(|_| ()));
            assert!(text.contains("outside its folder"), "{why}: {text}");
            assert_eq!(fs::read(&current).unwrap(), b"working", "{why}");
            for path in [&backup, &staged] {
                if path.as_path() != current.as_path() {
                    assert_eq!(fs::read(path).unwrap(), b"other", "{why}");
                    fs::remove_file(path).unwrap();
                }
            }
            fs::remove_file(&journal).unwrap();
        }
        // The same journal with the right names does restore.
        fs::write(&good_backup, b"previous").unwrap();
        write_crafted_journal(&journal, &current, &good_backup, &good_staged);
        assert_eq!(recover_swap(&journal).unwrap(), Some(current.clone()));
        assert_eq!(fs::read(&current).unwrap(), b"previous");
    }

    /// Only a missing journal means "nothing to roll back"; a journal that cannot be
    /// looked at is an error, and the start must not go on as if there were none.
    #[test]
    fn an_unreadable_journal_location_is_an_error_and_a_missing_one_is_not() {
        let root = tempfile::tempdir().unwrap();
        let file = root.path().join("not-a-folder");
        fs::write(&file, b"x").unwrap();
        assert!(recover_swap(&file.join("journal")).is_err());
        assert_eq!(recover_swap(&root.path().join("journal")).unwrap(), None);
    }

    /// A rival start that finishes the rollback between this one's first look and its
    /// lock leaves nothing to do.
    #[cfg(unix)]
    #[test]
    fn a_journal_removed_by_a_rival_start_is_not_an_error() {
        let root = tempfile::tempdir().unwrap();
        let journal = root.path().join("journal");
        fs::write(&journal, b"{}").unwrap();
        let path = journal.clone();
        AFTER_JOURNAL_LOCK.with(|hook| {
            *hook.borrow_mut() = Some(Box::new(move || fs::remove_file(path).unwrap()))
        });
        assert_eq!(recover_swap(&journal).unwrap(), None);
    }

    /// A journal that cannot be looked at once the lock is held is an error, not a
    /// missing journal: the start does not go on as if no swap had been interrupted.
    #[cfg(unix)]
    #[test]
    fn a_journal_that_becomes_unreadable_under_the_lock_is_an_error() {
        let root = tempfile::tempdir().unwrap();
        let folder = root.path().join("profile");
        fs::create_dir(&folder).unwrap();
        let journal = folder.join("journal");
        fs::write(&journal, b"{}").unwrap();
        let (moved, folder_again) = (root.path().join("moved"), folder.clone());
        AFTER_JOURNAL_LOCK.with(|hook| {
            *hook.borrow_mut() = Some(Box::new(move || {
                fs::rename(&folder_again, &moved).unwrap();
                fs::write(&folder_again, b"a file now").unwrap();
            }))
        });
        assert!(recover_swap(&journal).is_err());
    }
}
