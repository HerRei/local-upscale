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
    struct Swap {
        _root: tempfile::TempDir,
        folders: Vec<PathBuf>,
        current: PathBuf,
        journal: PathBuf,
    }

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
}
