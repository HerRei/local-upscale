use crate::{
    error::{AppError, AppResult},
    state::{lock, AppState},
};
use directories::BaseDirs;
use std::{
    fs,
    path::{Path, PathBuf},
    time::{Duration, SystemTime},
};
use tauri::State;

const MAX_AGE: Duration = Duration::from_secs(30 * 86400);
const MAX_SIZE: u64 = 5 * 1024 * 1024 * 1024;

struct Entry {
    path: PathBuf,
    size: u64,
    used: SystemTime,
}

// Do not follow links, including links nested inside a compiled bundle.
fn inspect(path: &Path) -> AppResult<Entry> {
    let metadata = fs::symlink_metadata(path)?;
    if metadata.file_type().is_symlink() {
        return Err(AppError::Validation(format!(
            "Cache contains a symbolic link: {}",
            path.display()
        )));
    }
    let mut size = if metadata.is_file() {
        metadata.len()
    } else {
        0
    };
    let mut used = metadata.modified().unwrap_or(SystemTime::UNIX_EPOCH);
    // Reading a directory changes its access time, including during this scan.
    // Only files contribute access times; directory modification still counts.
    if metadata.is_file() {
        if let Ok(accessed) = metadata.accessed() {
            used = used.max(accessed);
        }
    }
    if metadata.is_dir() {
        for child in fs::read_dir(path)? {
            let entry = inspect(&child?.path())?;
            size = size.saturating_add(entry.size);
            used = used.max(entry.used);
        }
    } else if !metadata.is_file() {
        return Err(AppError::Validation(format!(
            "Unexpected cache entry: {}",
            path.display()
        )));
    }
    Ok(Entry {
        path: path.to_path_buf(),
        size,
        used,
    })
}

fn children(path: &Path) -> AppResult<Vec<PathBuf>> {
    match fs::symlink_metadata(path) {
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(e) => return Err(e.into()),
        Ok(m) if m.file_type().is_symlink() || !m.is_dir() => {
            return Err(AppError::Validation(format!(
                "Unsafe cache folder: {}",
                path.display()
            )))
        }
        Ok(_) => {}
    }
    Ok(fs::read_dir(path)?
        .map(|item| item.map(|entry| entry.path()))
        .collect::<Result<_, _>>()?)
}

// Each root and intermediate directory is checked before listing it. Only known,
// regenerable program files are selected; verdicts and crash records stay intact.
fn entries(base: &Path) -> AppResult<Vec<Entry>> {
    let mut paths = Vec::new();
    let localsr = base.join("LocalSR");
    if children(base)?.contains(&localsr) {
        let local_children = children(&localsr)?;
        let coreml = localsr.join("coreml");
        if local_children.contains(&coreml) {
            for program in children(&coreml)? {
                for item in children(&program)? {
                    if item
                        .file_name()
                        .is_some_and(|name| name == "model.mlmodelc")
                    {
                        paths.push(item);
                    }
                }
            }
        }
        let gpu = localsr.join("gpu-checks");
        if local_children.contains(&gpu) {
            let npu = gpu.join("npu-cache");
            if children(&gpu)?.contains(&npu) {
                paths.extend(children(&npu)?);
            }
        }
    }
    #[cfg(target_os = "macos")]
    {
        let worker = base.join("localsr-worker");
        if children(base)?.contains(&worker) {
            let apple = worker.join("com.apple.e5rt.e5bundlecache");
            if children(&worker)?.contains(&apple) {
                for version in children(&apple)? {
                    paths.extend(children(&version)?);
                }
            }
        }
    }
    paths.into_iter().map(|path| inspect(&path)).collect()
}

fn clean_entries(
    mut entries: Vec<Entry>,
    now: SystemTime,
    age: Duration,
    limit: u64,
) -> AppResult<()> {
    entries.sort_by_key(|entry| entry.used);
    let mut total: u64 = entries.iter().map(|entry| entry.size).sum();
    for entry in entries {
        if age != Duration::ZERO
            && now.duration_since(entry.used).unwrap_or_default() <= age
            && total <= limit
        {
            continue;
        }
        // Recheck before removing, so links found in the entry are refused.
        inspect(&entry.path)?;
        let metadata = fs::symlink_metadata(&entry.path)?;
        if metadata.is_dir() {
            fs::remove_dir_all(&entry.path)?;
        } else {
            fs::remove_file(&entry.path)?;
        }
        total = total.saturating_sub(entry.size);
    }
    Ok(())
}

fn cache_base() -> AppResult<PathBuf> {
    let dirs =
        BaseDirs::new().ok_or_else(|| AppError::Config("home directory unavailable".into()))?;
    Ok(dirs.cache_dir().to_path_buf())
}

fn guarded_cleanup(state: &AppState, bases: &[PathBuf], clear: bool) -> AppResult<u64> {
    let _scheduler = lock(&state.scheduler)?;
    let runtime = lock(&state.runtime)?;
    if !runtime.active_job_id.is_empty() || !runtime.active_batch_job_ids.is_empty() {
        return Err(AppError::Validation(
            "Wait for the running job to finish before clearing the cache.".into(),
        ));
    }
    drop(runtime);
    let mut found = Vec::new();
    for base in bases {
        found.extend(entries(base)?);
    }
    clean_entries(
        found,
        SystemTime::now(),
        if clear { Duration::ZERO } else { MAX_AGE },
        if clear { 0 } else { MAX_SIZE },
    )?;
    let mut remaining = 0u64;
    for base in bases {
        remaining = remaining.saturating_add(entries(base)?.iter().map(|entry| entry.size).sum());
    }
    Ok(remaining)
}

pub(crate) fn cleanup_at_start(state: &AppState) -> AppResult<()> {
    cleanup_at_start_in(state, &[cache_base()?])
}

fn cleanup_at_start_in(state: &AppState, bases: &[PathBuf]) -> AppResult<()> {
    if !lock(&state.settings)?.auto_cache_cleanup {
        return Ok(());
    }
    guarded_cleanup(state, bases, false).map(|_| ())
}

#[tauri::command]
pub fn cache_size() -> AppResult<u64> {
    Ok(entries(&cache_base()?)?
        .iter()
        .map(|entry| entry.size)
        .sum())
}

#[tauri::command]
pub fn clear_cache(state: State<'_, std::sync::Arc<AppState>>) -> AppResult<u64> {
    guarded_cleanup(&state, &[cache_base()?], true)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::time::{Duration, SystemTime};

    #[test]
    fn removes_expired_and_oldest_entries_to_fit_the_limit() {
        let dir = tempfile::tempdir().unwrap();
        let old = dir.path().join("old");
        let middle = dir.path().join("middle");
        let fresh = dir.path().join("fresh");
        for path in [&old, &middle, &fresh] {
            fs::write(path, b"1234").unwrap();
        }
        let now = SystemTime::now();
        let entries = vec![
            Entry {
                path: old.clone(),
                size: 4,
                used: now - Duration::from_secs(40 * 86400),
            },
            Entry {
                path: middle.clone(),
                size: 4,
                used: now - Duration::from_secs(20 * 86400),
            },
            Entry {
                path: fresh.clone(),
                size: 4,
                used: now,
            },
        ];
        clean_entries(entries, now, Duration::from_secs(30 * 86400), 4).unwrap();
        assert!(!old.exists());
        assert!(!middle.exists());
        assert!(fresh.exists());
    }

    #[cfg(unix)]
    #[test]
    fn refuses_symlinked_roots_and_entries() {
        use std::os::unix::fs::symlink;
        let dir = tempfile::tempdir().unwrap();
        let outside = tempfile::tempdir().unwrap();
        fs::write(outside.path().join("keep"), b"keep").unwrap();
        symlink(outside.path(), dir.path().join("link")).unwrap();
        assert!(inspect(&dir.path().join("link")).is_err());
        let nested = dir.path().join("nested");
        fs::create_dir(&nested).unwrap();
        symlink(outside.path(), nested.join("link")).unwrap();
        assert!(inspect(&nested).is_err());
        assert!(outside.path().join("keep").exists());
    }

    #[test]
    fn selects_programs_without_deleting_verdicts_or_crash_records() {
        let dir = tempfile::tempdir().unwrap();
        let coreml = dir.path().join("LocalSR/coreml/program");
        let gpu = dir.path().join("LocalSR/gpu-checks");
        fs::create_dir_all(coreml.join("model.mlmodelc")).unwrap();
        fs::create_dir_all(gpu.join("npu-cache")).unwrap();
        fs::write(coreml.join("model.mlmodelc/program"), b"model").unwrap();
        fs::write(coreml.join("verdicts.json"), b"verdict").unwrap();
        fs::write(gpu.join("crashes.json"), b"crash").unwrap();
        fs::write(gpu.join("npu-cache/program.blob"), b"blob").unwrap();
        let found = entries(dir.path()).unwrap();
        assert_eq!(found.len(), 2);
        clean_entries(found, SystemTime::now(), Duration::ZERO, 0).unwrap();
        assert!(coreml.join("verdicts.json").exists());
        assert!(gpu.join("crashes.json").exists());
        assert!(!coreml.join("model.mlmodelc").exists());
        assert!(!gpu.join("npu-cache/program.blob").exists());
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn selects_only_apple_worker_bundle_entries() {
        let dir = tempfile::tempdir().unwrap();
        let bundle = dir
            .path()
            .join("localsr-worker/com.apple.e5rt.e5bundlecache/build/hash");
        let other = dir.path().join("localsr-worker/other/keep");
        fs::create_dir_all(&bundle).unwrap();
        fs::create_dir_all(other.parent().unwrap()).unwrap();
        fs::write(bundle.join("program"), b"bundle").unwrap();
        fs::write(&other, b"keep").unwrap();
        let found = entries(dir.path()).unwrap();
        assert_eq!(found.len(), 1);
        clean_entries(found, SystemTime::now(), Duration::ZERO, 0).unwrap();
        assert!(!bundle.exists());
        assert!(other.exists());
    }

    #[test]
    fn rejects_cleanup_during_a_job() {
        let dir = tempfile::tempdir().unwrap();
        let state =
            crate::state::AppState::from_paths(crate::paths::AppPaths::under(dir.path())).unwrap();
        let program = dir.path().join("LocalSR/gpu-checks/npu-cache/program.blob");
        fs::create_dir_all(program.parent().unwrap()).unwrap();
        fs::write(&program, b"program").unwrap();
        crate::state::lock(&state.runtime).unwrap().active_job_id = "running".into();
        assert!(guarded_cleanup(&state, &[dir.path().to_path_buf()], true).is_err());
        assert!(program.exists());
    }

    #[test]
    fn startup_cleanup_respects_auto_cache_cleanup_but_manual_clear_always_clears() {
        let dir = tempfile::tempdir().unwrap();
        let state =
            crate::state::AppState::from_paths(crate::paths::AppPaths::under(dir.path())).unwrap();
        let program = dir.path().join("LocalSR/gpu-checks/npu-cache/program.blob");
        fs::create_dir_all(program.parent().unwrap()).unwrap();
        {
            let file = fs::File::create(&program).unwrap();
            use std::io::Write;
            (&file).write_all(b"program").unwrap();
            let old = SystemTime::now() - Duration::from_secs(40 * 86400);
            file.set_times(fs::FileTimes::new().set_accessed(old).set_modified(old))
                .unwrap();
        }

        crate::state::lock(&state.settings)
            .unwrap()
            .auto_cache_cleanup = false;
        cleanup_at_start_in(&state, &[dir.path().to_path_buf()]).unwrap();
        assert!(program.exists());

        // Manual clear still clears when automatic cleanup is off.
        let remaining = guarded_cleanup(&state, &[dir.path().to_path_buf()], true).unwrap();
        assert_eq!(remaining, 0);
        assert!(!program.exists());

        // With automatic cleanup on, an expired entry is removed at startup.
        {
            let file = fs::File::create(&program).unwrap();
            use std::io::Write;
            (&file).write_all(b"program").unwrap();
            let old = SystemTime::now() - Duration::from_secs(40 * 86400);
            file.set_times(fs::FileTimes::new().set_accessed(old).set_modified(old))
                .unwrap();
        }
        crate::state::lock(&state.settings)
            .unwrap()
            .auto_cache_cleanup = true;
        cleanup_at_start_in(&state, &[dir.path().to_path_buf()]).unwrap();
        assert!(!program.exists());
    }
}
