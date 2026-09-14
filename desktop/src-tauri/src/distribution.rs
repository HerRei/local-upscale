//! MSIX installs update the host and engine together through Windows.
//! Detect package identity as well as the build feature so wrapping a direct
//! build in MSIX cannot accidentally enable its external updater.

pub fn managed_by_store() -> bool {
    cfg!(feature = "microsoft-store") || package_identity().is_some()
}

pub fn profile_version() -> String {
    let version = env!("CARGO_PKG_VERSION");
    // A Store package may update its engine without changing the UI version.
    // Its full identity includes the four-part package version.
    package_identity().map_or_else(
        || version.into(),
        |identity| format!("{version}@{identity}"),
    )
}

#[cfg(not(windows))]
pub fn package_identity() -> Option<String> {
    None
}

#[cfg(windows)]
pub fn package_identity() -> Option<String> {
    #[link(name = "kernel32")]
    extern "system" {
        fn GetCurrentPackageFullName(length: *mut u32, name: *mut u16) -> i32;
    }
    let mut length = 0;
    // The first call obtains the UTF-16 buffer size, including its terminator.
    if unsafe { GetCurrentPackageFullName(&mut length, std::ptr::null_mut()) } != 122 || length == 0
    {
        return None;
    }
    let mut name = vec![0u16; length as usize];
    if unsafe { GetCurrentPackageFullName(&mut length, name.as_mut_ptr()) } != 0 {
        return None;
    }
    Some(String::from_utf16_lossy(
        &name[..length.saturating_sub(1) as usize],
    ))
}

pub fn require_direct_updates() -> crate::error::AppResult<()> {
    if managed_by_store() {
        return Err(crate::error::AppError::Validation(
            "This installation receives app and engine updates through Microsoft Store.".into(),
        ));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    #[test]
    fn store_build_rejects_direct_updates_even_without_package_identity() {
        if cfg!(feature = "microsoft-store") {
            assert!(super::managed_by_store());
            assert!(super::require_direct_updates().is_err());
        } else if super::package_identity().is_none() {
            assert!(!super::managed_by_store());
            assert!(super::require_direct_updates().is_ok());
        }
    }
}
