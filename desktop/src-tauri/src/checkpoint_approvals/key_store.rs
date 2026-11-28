//! Where the approval key is kept, per platform.
//!
//! - macOS release builds: an item in the login Keychain that LocalSR creates, readable
//!   without a prompt only by LocalSR's code signature. The item's access list carries a
//!   digest of the key, which every read checks, and the item is found by that access
//!   list rather than by its name.
//! - Windows: a file in the profile folder holding the key protected with DPAPI for the
//!   current user.
//! - Linux: an item in the Secret Service (the desktop keyring) when one runs; a file in
//!   the profile folder, readable only by the user, when none does.
//! - Development builds on macOS: the file, so that each rebuild can read it.
use std::path::Path;

pub(super) enum Loaded {
    Existing([u8; 32]),
    Created([u8; 32]),
}

fn random_key() -> Result<[u8; 32], String> {
    use ring::rand::SecureRandom;
    let mut key = [0u8; 32];
    ring::rand::SystemRandom::new()
        .fill(&mut key)
        .map_err(|_| "no random numbers are available".to_owned())?;
    Ok(key)
}

/// The digest of `key` its Keychain item states.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
fn commitment(key: &[u8; 32]) -> String {
    use sha2::{Digest, Sha256};
    let digest = Sha256::digest([&b"localsr model approvals key\0"[..], key].concat());
    digest[..16]
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

#[cfg(test)]
pub(super) fn load_or_create(next_root: &Path) -> Result<Loaded, String> {
    test_store::load_or_create(next_root)
}

#[cfg(all(not(test), target_os = "macos", not(debug_assertions)))]
pub(super) fn load_or_create(_next_root: &Path) -> Result<Loaded, String> {
    keychain::load_or_create(None)
}

#[cfg(all(not(test), windows))]
pub(super) fn load_or_create(next_root: &Path) -> Result<Loaded, String> {
    file::load_or_create(next_root, dpapi::protect, dpapi::unprotect)
}

#[cfg(all(not(test), target_os = "linux"))]
pub(super) fn load_or_create(next_root: &Path) -> Result<Loaded, String> {
    match secret_service::load_or_create() {
        Ok(Some(loaded)) => Ok(loaded),
        Ok(None) => file::load_or_create(next_root, plain, plain),
        Err(error) => Err(error),
    }
}

#[cfg(all(
    not(test),
    not(windows),
    not(target_os = "linux"),
    any(not(target_os = "macos"), debug_assertions)
))]
pub(super) fn load_or_create(next_root: &Path) -> Result<Loaded, String> {
    file::load_or_create(next_root, plain, plain)
}

#[cfg(all(not(test), not(windows)))]
#[cfg_attr(all(target_os = "macos", not(debug_assertions)), allow(dead_code))]
fn plain(bytes: &[u8]) -> Result<Vec<u8>, String> {
    Ok(bytes.to_vec())
}

#[cfg_attr(
    any(test, all(target_os = "macos", not(debug_assertions))),
    allow(dead_code)
)]
mod file {
    use super::{random_key, Loaded};
    use std::{fs, io::Write, path::Path};

    const KEY_FILE: &str = "checkpoint-approvals.key";

    /// The key in the profile folder, stored as `seal` makes it and read back through
    /// `open`. A file is never replaced: one that cannot be read leaves the key unavailable.
    pub(super) fn load_or_create(
        next_root: &Path,
        seal: fn(&[u8]) -> Result<Vec<u8>, String>,
        open: fn(&[u8]) -> Result<Vec<u8>, String>,
    ) -> Result<Loaded, String> {
        let path = next_root.join(KEY_FILE);
        match fs::symlink_metadata(&path) {
            Ok(metadata) if metadata.file_type().is_file() && metadata.len() <= 64 * 1024 => {
                let stored = fs::read(&path).map_err(|error| error.to_string())?;
                let key = open(&stored)?;
                return key
                    .try_into()
                    .map(Loaded::Existing)
                    .map_err(|_| "the approval key file is damaged".to_owned());
            }
            Ok(_) => return Err("the approval key file is not a plain file".into()),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => return Err(error.to_string()),
        }
        let key = random_key()?;
        let stored = seal(&key)?;
        fs::create_dir_all(next_root).map_err(|error| error.to_string())?;
        let mut options = fs::OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options.open(&path).map_err(|error| error.to_string())?;
        file.write_all(&stored)
            .and_then(|()| file.sync_all())
            .map_err(|error| error.to_string())?;
        Ok(Loaded::Created(key))
    }
}

#[cfg(windows)]
mod dpapi {
    use std::{ffi::c_void, ptr};

    #[repr(C)]
    struct DataBlob {
        size: u32,
        data: *mut u8,
    }

    #[link(name = "crypt32")]
    unsafe extern "system" {
        fn CryptProtectData(
            data_in: *const DataBlob,
            description: *const u16,
            entropy: *const DataBlob,
            reserved: *mut c_void,
            prompt: *mut c_void,
            flags: u32,
            data_out: *mut DataBlob,
        ) -> i32;
        fn CryptUnprotectData(
            data_in: *const DataBlob,
            description: *mut *mut u16,
            entropy: *const DataBlob,
            reserved: *mut c_void,
            prompt: *mut c_void,
            flags: u32,
            data_out: *mut DataBlob,
        ) -> i32;
    }

    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn LocalFree(memory: *mut c_void) -> *mut c_void;
    }

    const CRYPTPROTECT_UI_FORBIDDEN: u32 = 0x1;
    const ENTROPY: &[u8] = b"LocalSR model approvals v1";

    fn call(
        input: &[u8],
        apply: impl FnOnce(*const DataBlob, *const DataBlob, *mut DataBlob) -> i32,
    ) -> Result<Vec<u8>, String> {
        let input = DataBlob {
            size: u32::try_from(input.len()).map_err(|_| "the key is too large".to_owned())?,
            data: input.as_ptr().cast_mut(),
        };
        let entropy = DataBlob {
            size: ENTROPY.len() as u32,
            data: ENTROPY.as_ptr().cast_mut(),
        };
        let mut output = DataBlob {
            size: 0,
            data: ptr::null_mut(),
        };
        if apply(&input, &entropy, &mut output) == 0 {
            return Err(format!(
                "Windows data protection failed: {}",
                std::io::Error::last_os_error()
            ));
        }
        // The system allocated the output with LocalAlloc; it is copied, then freed.
        let bytes =
            unsafe { std::slice::from_raw_parts(output.data, output.size as usize) }.to_vec();
        unsafe { LocalFree(output.data.cast()) };
        Ok(bytes)
    }

    pub(super) fn protect(key: &[u8]) -> Result<Vec<u8>, String> {
        // The blobs and the entropy outlive the call; no prompt is ever shown.
        call(key, |input, entropy, output| unsafe {
            CryptProtectData(
                input,
                ptr::null(),
                entropy,
                ptr::null_mut(),
                ptr::null_mut(),
                CRYPTPROTECT_UI_FORBIDDEN,
                output,
            )
        })
    }

    pub(super) fn unprotect(blob: &[u8]) -> Result<Vec<u8>, String> {
        call(blob, |input, entropy, output| unsafe {
            CryptUnprotectData(
                input,
                ptr::null_mut(),
                entropy,
                ptr::null_mut(),
                ptr::null_mut(),
                CRYPTPROTECT_UI_FORBIDDEN,
                output,
            )
        })
    }
}

#[cfg(target_os = "linux")]
mod secret_service {
    use super::{random_key, Loaded};
    use std::collections::HashMap;
    use zbus::{
        blocking::{Connection, Proxy},
        zvariant::{OwnedObjectPath, Value},
    };

    const SERVICE: &str = "org.freedesktop.secrets";
    const PATH: &str = "/org/freedesktop/secrets";

    fn attributes() -> HashMap<&'static str, &'static str> {
        HashMap::from([
            ("application", "com.localsr.app"),
            ("purpose", "checkpoint-approvals-v1"),
        ])
    }

    /// The key in the desktop keyring; None when no keyring service runs. A locked
    /// keyring is never unlocked from here, so no password prompt appears.
    pub(super) fn load_or_create() -> Result<Option<Loaded>, String> {
        let Ok(connection) = Connection::session() else {
            return Ok(None);
        };
        let error = |error: zbus::Error| format!("the desktop keyring failed: {error}");
        let service = Proxy::new(&connection, SERVICE, PATH, "org.freedesktop.Secret.Service")
            .map_err(error)?;
        let opened: Result<(zbus::zvariant::OwnedValue, OwnedObjectPath), zbus::Error> =
            service.call("OpenSession", &("plain", Value::from("")));
        let session = match opened {
            Ok((_, session)) => session,
            // Nobody owns the keyring name: there is no keyring.
            Err(zbus::Error::MethodError(name, _, _))
                if name.as_str() == "org.freedesktop.DBus.Error.ServiceUnknown" =>
            {
                return Ok(None);
            }
            Err(other) => return Err(error(other)),
        };
        let (unlocked, locked): (Vec<OwnedObjectPath>, Vec<OwnedObjectPath>) = service
            .call("SearchItems", &(attributes(),))
            .map_err(error)?;
        if let Some(item) = unlocked.first() {
            let item = Proxy::new(
                &connection,
                SERVICE,
                item.as_str(),
                "org.freedesktop.Secret.Item",
            )
            .map_err(error)?;
            let (_, _, value, _): (OwnedObjectPath, Vec<u8>, Vec<u8>, String) =
                item.call("GetSecret", &(&session,)).map_err(error)?;
            return value
                .try_into()
                .map(|key| Some(Loaded::Existing(key)))
                .map_err(|_| "the keyring holds a damaged approval key".to_owned());
        }
        if !locked.is_empty() {
            return Err("the desktop keyring is locked".into());
        }
        let (collection,): (OwnedObjectPath,) =
            service.call("ReadAlias", &("default",)).map_err(error)?;
        if collection.as_str() == "/" {
            return Err("the desktop keyring has no default collection".into());
        }
        let collection = Proxy::new(
            &connection,
            SERVICE,
            collection.as_str(),
            "org.freedesktop.Secret.Collection",
        )
        .map_err(error)?;
        let key = random_key()?;
        let properties: HashMap<&str, Value> = HashMap::from([
            (
                "org.freedesktop.Secret.Item.Label",
                Value::from("LocalSR model approvals"),
            ),
            (
                "org.freedesktop.Secret.Item.Attributes",
                Value::from(attributes()),
            ),
        ]);
        let secret = (
            session,
            Vec::<u8>::new(),
            key.to_vec(),
            "application/octet-stream",
        );
        let (item, _prompt): (OwnedObjectPath, OwnedObjectPath) = collection
            .call("CreateItem", &(properties, secret, false))
            .map_err(error)?;
        if item.as_str() == "/" {
            return Err("the desktop keyring asks for a password first".into());
        }
        Ok(Some(Loaded::Created(key)))
    }
}

#[cfg(target_os = "macos")]
#[cfg_attr(any(test, debug_assertions), allow(dead_code))]
pub(crate) mod keychain {
    use super::{commitment, random_key, Loaded};
    use core_foundation::{
        array::{CFArray, CFArrayRef},
        base::{CFType, CFTypeRef, TCFType},
        boolean::CFBoolean,
        data::CFData,
        dictionary::CFDictionary,
        string::{CFString, CFStringRef},
    };
    use security_framework_sys::{
        base::{errSecDuplicateItem, errSecItemNotFound, SecAccessRef, SecKeychainItemRef},
        item::{
            kSecAttrAccount, kSecAttrLabel, kSecAttrService, kSecClass, kSecClassGenericPassword,
            kSecMatchLimit, kSecMatchLimitAll, kSecMatchSearchList, kSecReturnRef, kSecUseKeychain,
            kSecValueData,
        },
        keychain_item::{SecItemAdd, SecItemCopyMatching},
    };
    use std::{
        ffi::{c_char, c_void},
        ptr,
    };

    type OsStatus = i32;
    pub(crate) enum OpaqueTrustedApplication {}
    pub(crate) enum OpaqueAcl {}

    #[link(name = "Security", kind = "framework")]
    unsafe extern "C" {
        static kSecAttrAccess: CFStringRef;
        fn SecTrustedApplicationCreateFromPath(
            path: *const c_char,
            application: *mut *mut OpaqueTrustedApplication,
        ) -> OsStatus;
        fn SecAccessCreate(
            descriptor: CFStringRef,
            trusted: CFArrayRef,
            access: *mut SecAccessRef,
        ) -> OsStatus;
        fn SecKeychainItemCopyAccess(
            item: SecKeychainItemRef,
            access: *mut SecAccessRef,
        ) -> OsStatus;
        fn SecAccessCopyACLList(access: SecAccessRef, list: *mut CFArrayRef) -> OsStatus;
        fn SecACLCopyContents(
            acl: *mut OpaqueAcl,
            applications: *mut CFArrayRef,
            description: *mut CFStringRef,
            prompt: *mut u16,
        ) -> OsStatus;
        fn SecKeychainItemCopyContent(
            item: SecKeychainItemRef,
            class: *mut u32,
            attributes: *mut c_void,
            length: *mut u32,
            data: *mut *mut c_void,
        ) -> OsStatus;
        fn SecKeychainItemFreeContent(attributes: *mut c_void, data: *mut c_void) -> OsStatus;
        fn SecKeychainGetUserInteractionAllowed(state: *mut u8) -> OsStatus;
        fn SecKeychainSetUserInteractionAllowed(state: u8) -> OsStatus;
    }

    const SERVICE: &str = "com.localsr.app.checkpoint-approvals";
    const ACCOUNT: &str = "v1";
    const LABEL: &str = "LocalSR model approvals";
    /// The access list's description: this, then the key's digest.
    const MARKER: &str = "LocalSR model approvals ";

    /// No Keychain dialog while this lives: an item that would need one is unavailable.
    struct NoPrompts(u8);

    impl NoPrompts {
        fn new() -> Self {
            let mut previous = 1;
            // Both calls only read or set a flag of this process.
            unsafe {
                SecKeychainGetUserInteractionAllowed(&mut previous);
                SecKeychainSetUserInteractionAllowed(0);
            }
            Self(previous)
        }
    }

    impl Drop for NoPrompts {
        fn drop(&mut self) {
            unsafe { SecKeychainSetUserInteractionAllowed(self.0) };
        }
    }

    fn cf_string(value: &str) -> CFType {
        CFString::new(value).as_CFType()
    }

    fn key_of(name: CFStringRef) -> CFString {
        // The constants are static strings of the Security framework.
        unsafe { CFString::wrap_under_get_rule(name) }
    }

    /// The description the item's access list carries, read without its secret.
    fn description(item: SecKeychainItemRef) -> Option<String> {
        let mut access: SecAccessRef = ptr::null_mut();
        if unsafe { SecKeychainItemCopyAccess(item, &mut access) } != 0 || access.is_null() {
            return None;
        }
        let access = unsafe { CFType::wrap_under_create_rule(access as CFTypeRef) };
        let mut list: CFArrayRef = ptr::null();
        if unsafe { SecAccessCopyACLList(access.as_CFTypeRef() as SecAccessRef, &mut list) } != 0
            || list.is_null()
        {
            return None;
        }
        let list: CFArray<CFType> = unsafe { CFArray::wrap_under_create_rule(list) };
        let mut found: Option<String> = None;
        for acl in list.iter() {
            let mut applications: CFArrayRef = ptr::null();
            let mut text: CFStringRef = ptr::null();
            let mut prompt = 0u16;
            let status = unsafe {
                SecACLCopyContents(
                    acl.as_CFTypeRef() as *mut OpaqueAcl,
                    &mut applications,
                    &mut text,
                    &mut prompt,
                )
            };
            if !applications.is_null() {
                drop(unsafe { CFArray::<CFType>::wrap_under_create_rule(applications) });
            }
            if status != 0 || text.is_null() {
                return None;
            }
            let text = unsafe { CFString::wrap_under_create_rule(text) }.to_string();
            // Every entry of an access list LocalSR made has the same description.
            if found.as_ref().is_some_and(|earlier| *earlier != text) {
                return None;
            }
            found = Some(text);
        }
        found
    }

    /// Every generic password item whose access list carries the marker, with that list's
    /// description.
    fn marked_items(keychain: Option<&CFType>) -> Result<Vec<(CFType, String)>, String> {
        let mut pairs = vec![
            (
                key_of(unsafe { kSecClass }),
                key_of(unsafe { kSecClassGenericPassword }).as_CFType(),
            ),
            (
                key_of(unsafe { kSecReturnRef }),
                CFBoolean::true_value().as_CFType(),
            ),
            (
                key_of(unsafe { kSecMatchLimit }),
                key_of(unsafe { kSecMatchLimitAll }).as_CFType(),
            ),
        ];
        if let Some(keychain) = keychain {
            pairs.push((
                key_of(unsafe { kSecMatchSearchList }),
                CFArray::from_CFTypes(std::slice::from_ref(keychain)).as_CFType(),
            ));
        }
        let query = CFDictionary::from_CFType_pairs(&pairs);
        let mut result: CFTypeRef = ptr::null();
        let status = unsafe { SecItemCopyMatching(query.as_concrete_TypeRef(), &mut result) };
        if status == errSecItemNotFound {
            return Ok(Vec::new());
        }
        if status != 0 || result.is_null() {
            return Err(format!("the Keychain could not be searched ({status})"));
        }
        let items: CFArray<CFType> =
            unsafe { CFArray::wrap_under_create_rule(result as CFArrayRef) };
        Ok(items
            .iter()
            .filter_map(|item| {
                let text = description(item.as_CFTypeRef() as SecKeychainItemRef)?;
                text.starts_with(MARKER).then(|| (item.clone(), text))
            })
            .collect())
    }

    fn read(item: &CFType, text: &str) -> Result<Loaded, String> {
        let mut length = 0u32;
        let mut data: *mut c_void = ptr::null_mut();
        let status = unsafe {
            SecKeychainItemCopyContent(
                item.as_CFTypeRef() as SecKeychainItemRef,
                ptr::null_mut(),
                ptr::null_mut(),
                &mut length,
                &mut data,
            )
        };
        if status != 0 || data.is_null() {
            return Err(format!("the Keychain item cannot be read ({status})"));
        }
        let bytes =
            unsafe { std::slice::from_raw_parts(data.cast::<u8>(), length as usize) }.to_vec();
        unsafe { SecKeychainItemFreeContent(ptr::null_mut(), data) };
        let key: [u8; 32] = bytes
            .try_into()
            .map_err(|_| "the Keychain item holds no approval key".to_owned())?;
        if text != format!("{MARKER}{}", commitment(&key)) {
            return Err("the Keychain item's key was replaced".into());
        }
        Ok(Loaded::Existing(key))
    }

    fn create(keychain: Option<&CFType>) -> Result<Loaded, String> {
        let key = random_key()?;
        let mut application: *mut OpaqueTrustedApplication = ptr::null_mut();
        if unsafe { SecTrustedApplicationCreateFromPath(ptr::null(), &mut application) } != 0
            || application.is_null()
        {
            return Err("LocalSR could not name itself to the Keychain".into());
        }
        let application = unsafe { CFType::wrap_under_create_rule(application as CFTypeRef) };
        let trusted = CFArray::from_CFTypes(&[application]);
        let description = CFString::new(&format!("{MARKER}{}", commitment(&key)));
        let mut access: SecAccessRef = ptr::null_mut();
        if unsafe {
            SecAccessCreate(
                description.as_concrete_TypeRef(),
                trusted.as_concrete_TypeRef(),
                &mut access,
            )
        } != 0
            || access.is_null()
        {
            return Err("the Keychain access list could not be made".into());
        }
        let access = unsafe { CFType::wrap_under_create_rule(access as CFTypeRef) };
        let mut pairs = vec![
            (
                key_of(unsafe { kSecClass }),
                key_of(unsafe { kSecClassGenericPassword }).as_CFType(),
            ),
            (key_of(unsafe { kSecAttrService }), cf_string(SERVICE)),
            (key_of(unsafe { kSecAttrAccount }), cf_string(ACCOUNT)),
            (key_of(unsafe { kSecAttrLabel }), cf_string(LABEL)),
            (
                key_of(unsafe { kSecValueData }),
                CFData::from_buffer(&key).as_CFType(),
            ),
            (key_of(unsafe { kSecAttrAccess }), access),
        ];
        if let Some(keychain) = keychain {
            pairs.push((key_of(unsafe { kSecUseKeychain }), keychain.clone()));
        }
        let attributes = CFDictionary::from_CFType_pairs(&pairs);
        match unsafe { SecItemAdd(attributes.as_concrete_TypeRef(), ptr::null_mut()) } {
            0 => Ok(Loaded::Created(key)),
            status if status == errSecDuplicateItem => {
                Err("a Keychain item LocalSR did not make has its name".into())
            }
            status => Err(format!("the Keychain item could not be added ({status})")),
        }
    }

    /// The key from the one item LocalSR made, found by its access list; made when there
    /// is none. Two such items leave the key unavailable. `keychain` limits the search to
    /// one keychain, for tests; LocalSR uses the user's default search list.
    pub(crate) fn load_or_create(keychain: Option<&CFType>) -> Result<Loaded, String> {
        let _no_prompts = NoPrompts::new();
        let marked = marked_items(keychain)?;
        match marked.as_slice() {
            [] => create(keychain),
            [(item, text)] => read(item, text),
            _ => Err("the Keychain holds more than one LocalSR approvals item".into()),
        }
    }

    #[cfg(test)]
    pub(crate) mod scratch {
        //! A keychain file of its own for a test, removed when dropped.
        use core_foundation::base::{CFType, CFTypeRef, TCFType};
        use std::{ffi::CString, ptr};

        pub(crate) enum OpaqueKeychain {}

        #[link(name = "Security", kind = "framework")]
        unsafe extern "C" {
            fn SecKeychainCreate(
                path: *const std::ffi::c_char,
                password_length: u32,
                password: *const std::ffi::c_void,
                prompt: u8,
                access: *mut std::ffi::c_void,
                keychain: *mut *mut OpaqueKeychain,
            ) -> i32;
            fn SecKeychainDelete(keychain: *mut OpaqueKeychain) -> i32;
        }

        pub(crate) struct Scratch(pub(crate) CFType);

        impl Scratch {
            pub(crate) fn new(path: &std::path::Path) -> Self {
                let path = CString::new(path.to_string_lossy().as_bytes()).unwrap();
                let mut keychain = ptr::null_mut();
                let password = b"test";
                let status = unsafe {
                    SecKeychainCreate(
                        path.as_ptr(),
                        password.len() as u32,
                        password.as_ptr().cast(),
                        0,
                        ptr::null_mut(),
                        &mut keychain,
                    )
                };
                assert_eq!(status, 0);
                Self(unsafe { CFType::wrap_under_create_rule(keychain as CFTypeRef) })
            }
        }

        impl Drop for Scratch {
            fn drop(&mut self) {
                unsafe { SecKeychainDelete(self.0.as_CFTypeRef() as *mut OpaqueKeychain) };
            }
        }
    }
}

#[cfg(test)]
pub(crate) mod test_store {
    //! The key store of the tests: in memory, per profile folder.
    use super::{random_key, Loaded};
    use std::{
        collections::HashMap,
        path::{Path, PathBuf},
        sync::{LazyLock, Mutex},
    };

    static KEYS: LazyLock<Mutex<HashMap<PathBuf, Option<[u8; 32]>>>> =
        LazyLock::new(|| Mutex::new(HashMap::new()));

    pub(super) fn load_or_create(next_root: &Path) -> Result<Loaded, String> {
        let mut keys = KEYS.lock().unwrap();
        match keys.get(next_root) {
            Some(Some(key)) => Ok(Loaded::Existing(*key)),
            Some(None) => Err("the key store is unavailable".into()),
            None => {
                let key = random_key()?;
                keys.insert(next_root.to_path_buf(), Some(key));
                Ok(Loaded::Created(key))
            }
        }
    }

    /// Make the store of `next_root` unavailable, or give it `key`; LocalSR's cache of the
    /// key is dropped as a restart drops it.
    pub(crate) fn replace(next_root: &Path, key: Option<[u8; 32]>) {
        KEYS.lock().unwrap().insert(next_root.to_path_buf(), key);
        super::super::KEYS.lock().unwrap().remove(next_root);
    }

    /// Forget the key LocalSR read in this process, as a restart does.
    pub(crate) fn restart(next_root: &Path) {
        super::super::KEYS.lock().unwrap().remove(next_root);
        super::super::SESSION
            .lock()
            .unwrap()
            .retain(|(root, _)| root != next_root);
    }
}
