//! Output names reserved by queued work, compared the way a case-insensitive file
//! system compares them: two names that differ only by letter case are one name.
use std::{
    collections::HashSet,
    path::{Path, PathBuf},
};

/// Every platform folds the same way: the output folder may be a case-insensitive
/// volume whatever the host's own file system is (an exFAT drive on Linux, a
/// network share), and a needless numbered suffix costs nothing.
///
/// Unicode normalisation forms (an "é" stored as one character or as "e" plus an
/// accent) are not folded: Rust's standard library has no normalisation tables.
/// A file that already exists is found by the file system itself, which compares
/// them as its own rules say; the final publication refuses to replace one.
#[derive(Default)]
pub struct ReservedOutputs(HashSet<String>);

impl ReservedOutputs {
    /// Reserve `path`; false when an equivalent name is already reserved.
    pub fn insert(&mut self, path: &Path) -> bool {
        self.0.insert(fold(path))
    }

    #[cfg(test)]
    pub fn contains(&self, path: &Path) -> bool {
        self.0.contains(&fold(path))
    }

    #[cfg(test)]
    pub fn is_empty(&self) -> bool {
        self.0.is_empty()
    }
}

impl FromIterator<PathBuf> for ReservedOutputs {
    fn from_iter<T: IntoIterator<Item = PathBuf>>(paths: T) -> Self {
        Self(paths.into_iter().map(|path| fold(&path)).collect())
    }
}

/// Upper then lower case: the first step maps characters such as "ß" and the final
/// sigma to the form a case-insensitive file system also treats as equal.
fn fold(path: &Path) -> String {
    path.components()
        .map(|part| {
            part.as_os_str()
                .to_string_lossy()
                .to_uppercase()
                .to_lowercase()
        })
        .collect::<Vec<_>>()
        .join("/")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn letter_case_and_separators_do_not_make_a_new_name() {
        let mut reserved = ReservedOutputs::default();
        assert!(reserved.insert(Path::new("/out/Photo_upscaled_4x.PNG")));
        assert!(!reserved.insert(Path::new("/out/photo_upscaled_4x.png")));
        assert!(!reserved.insert(Path::new("/OUT//photo_upscaled_4x.png")));
        assert!(!reserved.insert(Path::new("/out/./photo_upscaled_4x.png")));
        assert!(reserved.insert(Path::new("/out/photo_upscaled_4x_1.png")));
        assert!(reserved.insert(Path::new("/other/photo_upscaled_4x.png")));
    }

    #[test]
    fn non_ascii_letters_fold_too() {
        let mut reserved = ReservedOutputs::default();
        assert!(reserved.insert(Path::new("/out/Ärger.png")));
        assert!(!reserved.insert(Path::new("/out/ärger.png")));
        assert!(reserved.insert(Path::new("/out/straße.png")));
        assert!(!reserved.insert(Path::new("/out/STRASSE.png")));
        assert!(reserved.insert(Path::new("/out/Σίσυφος.png")));
        assert!(!reserved.insert(Path::new("/out/ΣΊΣΥΦΟς.png")));
    }

    #[test]
    fn collected_paths_reserve_like_inserted_ones() {
        let reserved: ReservedOutputs = [PathBuf::from("/out/A.png")].into_iter().collect();
        assert!(reserved.contains(Path::new("/out/a.png")));
        assert!(!reserved.is_empty());
    }
}
