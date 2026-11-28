use super::*;

fn profile() -> (tempfile::TempDir, AppPaths) {
    let root = tempfile::tempdir().unwrap();
    let paths = AppPaths::under(root.path());
    fs::create_dir_all(&paths.next_root).unwrap();
    (root, paths)
}

#[test]
fn an_approval_made_in_localsr_is_sealed_and_a_withdrawal_removes_the_seal() {
    let (_root, paths) = profile();
    let digest = "a".repeat(64);
    assert!(!sealed(&paths, &digest));
    record(&paths, &digest, true).unwrap();
    assert!(sealed(&paths, &digest));
    test_store::restart(&paths.next_root);
    assert!(sealed(&paths, &digest));
    record(&paths, &digest, false).unwrap();
    assert!(!sealed(&paths, &digest));
}

#[test]
fn a_seal_of_another_digest_or_key_does_not_verify() {
    let (_root, paths) = profile();
    let digest = "a".repeat(64);
    let other = "b".repeat(64);
    record(&paths, &digest, true).unwrap();
    let mut seals = read_seals(&paths).unwrap().seals;
    // The seal of one digest copied to another.
    let copied = seals[&digest].clone();
    seals.insert(other.clone(), copied);
    // A seal made with a key LocalSR does not hold.
    seals.insert("c".repeat(64), seal(&[7; 32], &"c".repeat(64)));
    seals.insert("d".repeat(64), "not hex".into());
    write_seals(&paths, &seals).unwrap();
    assert!(sealed(&paths, &digest));
    assert!(!sealed(&paths, &other));
    assert!(!sealed(&paths, &"c".repeat(64)));
    assert!(!sealed(&paths, &"d".repeat(64)));
}

#[test]
fn a_replaced_key_leaves_the_earlier_seals_unverified() {
    let (_root, paths) = profile();
    let digest = "a".repeat(64);
    record(&paths, &digest, true).unwrap();
    test_store::replace(&paths.next_root, Some([9; 32]));
    assert!(!sealed(&paths, &digest));
    // The key is unavailable: an approval lasts for this session only.
    test_store::replace(&paths.next_root, None);
    assert!(!sealed(&paths, &digest));
    record(&paths, &digest, true).unwrap();
    assert!(sealed(&paths, &digest));
    test_store::restart(&paths.next_root);
    assert!(!sealed(&paths, &digest));
}

#[test]
fn a_profile_from_before_seals_has_its_approvals_sealed_once_when_the_key_is_made() {
    let (_root, paths) = profile();
    let listed = vec!["a".repeat(64), "b".repeat(64)];
    prepare_at_start(&paths, &listed);
    assert!(listed.iter().all(|digest| sealed(&paths, digest)));

    // A later start does not seal what the settings list again.
    test_store::restart(&paths.next_root);
    let added = "c".repeat(64);
    prepare_at_start(&paths, &[listed.clone(), vec![added.clone()]].concat());
    assert!(!sealed(&paths, &added));

    // Nor does a start that finds the seals gone but the key in place.
    fs::remove_file(paths.next_root.join(SEALS_FILE)).unwrap();
    test_store::restart(&paths.next_root);
    prepare_at_start(&paths, std::slice::from_ref(&added));
    assert!(!sealed(&paths, &added));
    assert!(!sealed(&paths, &listed[0]));
}

#[test]
fn the_first_start_makes_the_key_so_a_later_entry_is_never_sealed_for_it() {
    let (_root, paths) = profile();
    prepare_at_start(&paths, &[]);
    assert!(paths.next_root.join(SEALS_FILE).is_file());
    let added = "a".repeat(64);
    for removed_seals in [false, true] {
        if removed_seals {
            fs::remove_file(paths.next_root.join(SEALS_FILE)).unwrap();
        }
        test_store::restart(&paths.next_root);
        prepare_at_start(&paths, std::slice::from_ref(&added));
        assert!(!sealed(&paths, &added), "{removed_seals}");
    }
}

#[test]
fn without_a_key_the_start_changes_nothing_and_tries_again_later() {
    let (_root, paths) = profile();
    test_store::replace(&paths.next_root, None);
    let listed = vec!["a".repeat(64)];
    prepare_at_start(&paths, &listed);
    assert!(!paths.next_root.join(SEALS_FILE).exists());
    assert!(!sealed(&paths, &listed[0]));
}

#[cfg(target_os = "macos")]
#[test]
#[ignore = "creates and removes a keychain file of its own; run by hand on a Mac"]
fn the_keychain_item_is_found_by_its_access_list_and_a_replaced_key_is_refused() {
    use super::key_store::{keychain, Loaded};
    use core_foundation::{
        base::TCFType, data::CFData, dictionary::CFDictionary, string::CFString,
    };
    use security_framework_sys::item::{
        kSecAttrAccount, kSecAttrService, kSecClass, kSecClassGenericPassword, kSecMatchSearchList,
        kSecValueData,
    };
    let folder = tempfile::tempdir().unwrap();
    let scratch = keychain::scratch::Scratch::new(&folder.path().join("test.keychain"));
    let created = match keychain::load_or_create(&scratch.0).unwrap() {
        Loaded::Created(key) => key,
        Loaded::Existing(_) => panic!("a new keychain holds no key"),
    };
    match keychain::load_or_create(&scratch.0).unwrap() {
        Loaded::Existing(key) => assert_eq!(key, created),
        Loaded::Created(_) => panic!("the key was made twice"),
    }
    let key = |name| unsafe { CFString::wrap_under_get_rule(name) };
    let query = |account: &str| {
        CFDictionary::from_CFType_pairs(&[
            (
                key(unsafe { kSecClass }),
                key(unsafe { kSecClassGenericPassword }).as_CFType(),
            ),
            (
                key(unsafe { kSecAttrService }),
                CFString::new("com.localsr.app.checkpoint-approvals").as_CFType(),
            ),
            (
                key(unsafe { kSecAttrAccount }),
                CFString::new(account).as_CFType(),
            ),
            (
                key(unsafe { kSecMatchSearchList }),
                core_foundation::array::CFArray::from_CFTypes(std::slice::from_ref(&scratch.0))
                    .as_CFType(),
            ),
        ])
    };
    // Renamed: still found by its access list, and still the same key.
    let renamed = CFDictionary::from_CFType_pairs(&[(
        key(unsafe { kSecAttrAccount }),
        CFString::new("elsewhere").as_CFType(),
    )]);
    let status = unsafe {
        security_framework_sys::keychain_item::SecItemUpdate(
            query("v1").as_concrete_TypeRef(),
            renamed.as_concrete_TypeRef(),
        )
    };
    assert_eq!(status, 0);
    match keychain::load_or_create(&scratch.0).unwrap() {
        Loaded::Existing(key) => assert_eq!(key, created),
        Loaded::Created(_) => panic!("a renamed item was not found"),
    }
    // Another key written over the item's data: refused, and not replaced.
    let replaced = CFDictionary::from_CFType_pairs(&[(
        key(unsafe { kSecValueData }),
        CFData::from_buffer(&[5u8; 32]).as_CFType(),
    )]);
    let status = unsafe {
        security_framework_sys::keychain_item::SecItemUpdate(
            query("elsewhere").as_concrete_TypeRef(),
            replaced.as_concrete_TypeRef(),
        )
    };
    assert_eq!(status, 0);
    let error = keychain::load_or_create(&scratch.0).err().unwrap();
    assert!(error.contains("replaced"), "{error}");
}

#[cfg(target_os = "macos")]
#[test]
#[ignore = "creates and removes keychain files of its own; run by hand on a Mac"]
fn only_the_keychain_opened_by_its_path_is_searched_and_written() {
    use super::key_store::{keychain, Loaded};
    let folder = tempfile::tempdir().unwrap();
    // Another keychain, made first, holds an item with LocalSR's marks and a key its maker
    // knows.
    let other = keychain::scratch::Scratch::new(&folder.path().join("other.keychain-db"));
    let planted = match keychain::load_or_create(&other.0).unwrap() {
        Loaded::Created(key) => key,
        Loaded::Existing(_) => panic!("a new keychain holds no key"),
    };
    // LocalSR's keychain, opened by its path as the login keychain is.
    let path = folder.path().join("own.keychain-db");
    let own = keychain::scratch::Scratch::new(&path);
    let opened = keychain::open(&path).unwrap();
    let created = match keychain::load_or_create(&opened).unwrap() {
        Loaded::Created(key) => key,
        Loaded::Existing(_) => panic!("the item of another keychain was used"),
    };
    assert_ne!(created, planted);
    // The new item went into that keychain; the other one still holds only its own.
    match keychain::load_or_create(&own.0).unwrap() {
        Loaded::Existing(key) => assert_eq!(key, created),
        Loaded::Created(_) => panic!("the item went into another keychain"),
    }
    match keychain::load_or_create(&other.0).unwrap() {
        Loaded::Existing(key) => assert_eq!(key, planted),
        Loaded::Created(_) => panic!("the other keychain lost its item"),
    }
    // A keychain file that does not exist is refused before anything is searched or added:
    // the Keychain would add the item to the default keychain instead. Only that check
    // runs here, so a failure cannot write into this Mac's own keychains.
    let missing = keychain::open(&folder.path().join("missing.keychain-db")).unwrap();
    assert!(keychain::existing_path(&missing).is_err());
    assert_eq!(
        keychain::existing_path(&opened).unwrap(),
        keychain::existing_path(&own.0).unwrap()
    );
}
