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
