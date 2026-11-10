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
    fault::recover_swap(&swap.journal).unwrap_or_else(|e| panic!("{why}: recovery failed: {e}"));
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
        fault::synced()
            .iter()
            .any(|(passed, dir)| *dir == app && *passed > removed && *passed <= journal_removed),
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
                replace_executable(&b, &journal, |_| panic!("journal in use"), |_| Ok(())).is_err()
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

#[test]
fn a_missing_source_leaves_no_new_file_behind() {
    let root = tempfile::tempdir().unwrap();
    let destination = root.path().join("copy");
    assert!(copy_new(&root.path().join("missing"), &destination).is_err());
    assert!(!destination.exists());
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
    AFTER_JOURNAL_LOCK
        .with(|hook| *hook.borrow_mut() = Some(Box::new(move || fs::remove_file(path).unwrap())));
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
