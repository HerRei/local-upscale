//! Keep the computer from going to idle sleep while jobs run.
//!
//! Display sleep and user-initiated sleep (such as closing the lid) remain
//! untouched; only idle system sleep is prevented until the queue is idle, the
//! setting is turned off, or the app exits.

use std::sync::{atomic::Ordering, Mutex};

pub(crate) trait Backend: Send {
    fn acquire(&mut self);
    fn release(&mut self);
}

struct Inner<B: Backend> {
    held: bool,
    backend: B,
}

pub struct AwakeGuard<B: Backend = os::OsBackend> {
    inner: Mutex<Inner<B>>,
}

pub type StayAwake = AwakeGuard<os::OsBackend>;

impl Default for StayAwake {
    fn default() -> Self {
        Self::new(os::OsBackend::new())
    }
}

impl<B: Backend> AwakeGuard<B> {
    pub(crate) fn new(backend: B) -> Self {
        Self {
            inner: Mutex::new(Inner {
                held: false,
                backend,
            }),
        }
    }

    pub fn update(&self, active: bool, enabled: bool) {
        let want = active && enabled;
        let Ok(mut inner) = self.inner.lock() else {
            return;
        };
        if want && !inner.held {
            inner.backend.acquire();
            inner.held = true;
        } else if !want && inner.held {
            inner.backend.release();
            inner.held = false;
        }
    }

    pub fn release(&self) {
        self.update(false, false);
    }

    #[cfg(test)]
    pub(crate) fn is_held(&self) -> bool {
        self.inner.lock().map(|inner| inner.held).unwrap_or(false)
    }

    #[cfg(test)]
    pub(crate) fn with_backend<R>(&self, f: impl FnOnce(&mut B) -> R) -> R {
        let mut inner = self.inner.lock().expect("stay_awake lock poisoned");
        f(&mut inner.backend)
    }
}

impl<B: Backend> Drop for AwakeGuard<B> {
    fn drop(&mut self) {
        if let Ok(mut inner) = self.inner.lock() {
            if inner.held {
                inner.backend.release();
                inner.held = false;
            }
        }
    }
}

/// Reconcile the stay-awake assertion with the current queue and setting.
/// Acquires `settings`, `database`, and `runtime` sequentially (never nested)
/// so it cannot invert lock order with callers such as `AppState::snapshot`.
pub(crate) fn sync(state: &crate::state::AppState) {
    if state.worker.shutting_down.load(Ordering::SeqCst) {
        state.stay_awake.release();
        return;
    }
    let enabled = state
        .settings
        .lock()
        .map(|settings| settings.keep_awake)
        .unwrap_or(true);
    let has_db_unfinished = state
        .database
        .lock()
        .ok()
        .and_then(|database| database.has_unfinished_jobs().ok())
        .unwrap_or(false);
    let (has_runtime_active, worker_failed, queue_paused) = state
        .runtime
        .lock()
        .map(|runtime| {
            (
                !runtime.active_job_id.is_empty() || !runtime.active_batch_job_ids.is_empty(),
                runtime.worker == "failed",
                runtime.queue_paused,
            )
        })
        .unwrap_or((false, false, false));
    let active = has_runtime_active || (has_db_unfinished && !worker_failed && !queue_paused);
    state.stay_awake.update(active, enabled);
}

#[cfg(any(target_os = "linux", test))]
fn is_executable_file(path: &std::path::Path) -> bool {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::metadata(path)
            .is_ok_and(|meta| meta.is_file() && (meta.permissions().mode() & 0o111) != 0)
    }
    #[cfg(not(unix))]
    {
        path.is_file()
    }
}

#[cfg(any(target_os = "linux", test))]
fn find_in_paths(
    program: &str,
    dirs: impl IntoIterator<Item = std::path::PathBuf>,
) -> Option<std::path::PathBuf> {
    dirs.into_iter()
        .map(|dir| dir.join(program))
        .find(|candidate| is_executable_file(candidate))
}

#[cfg(target_os = "macos")]
mod os {
    use std::{
        process::{Child, Command, Stdio},
        sync::atomic::{AtomicBool, Ordering},
    };

    use super::Backend;

    pub(crate) struct OsBackend {
        child: Option<Child>,
    }

    impl OsBackend {
        pub(crate) fn new() -> Self {
            Self { child: None }
        }

        #[cfg(test)]
        pub(crate) fn child_pid(&self) -> Option<u32> {
            self.child.as_ref().map(Child::id)
        }
    }

    impl Backend for OsBackend {
        fn acquire(&mut self) {
            if self
                .child
                .as_mut()
                .is_some_and(|child| child.try_wait().ok().flatten().is_none())
            {
                return;
            }
            self.child = None;
            let pid = std::process::id().to_string();
            // `-w` makes caffeinate exit as soon as this process exits, even on a crash.
            match Command::new("/usr/bin/caffeinate")
                .args(["-i", "-w", &pid])
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .spawn()
            {
                Ok(child) => self.child = Some(child),
                Err(error) => {
                    static REPORTED: AtomicBool = AtomicBool::new(false);
                    if !REPORTED.swap(true, Ordering::SeqCst) {
                        eprintln!("LocalSR could not start caffeinate: {error}");
                    }
                }
            }
        }

        fn release(&mut self) {
            if let Some(mut child) = self.child.take() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    }

    impl Drop for OsBackend {
        fn drop(&mut self) {
            self.release();
        }
    }
}

#[cfg(target_os = "windows")]
mod os {
    use std::{
        sync::mpsc::{self, Sender},
        thread::{self, JoinHandle},
    };

    use super::Backend;

    const ES_SYSTEM_REQUIRED: u32 = 0x0000_0001;
    const ES_CONTINUOUS: u32 = 0x8000_0000;

    #[link(name = "kernel32")]
    extern "system" {
        fn SetThreadExecutionState(es_flags: u32) -> u32;
    }

    enum Message {
        Set(bool),
        Stop,
    }

    pub(crate) struct OsBackend {
        sender: Option<Sender<Message>>,
        thread: Option<JoinHandle<()>>,
    }

    impl OsBackend {
        pub(crate) fn new() -> Self {
            Self {
                sender: None,
                thread: None,
            }
        }

        fn ensure_thread(&mut self) -> Option<&Sender<Message>> {
            if self.sender.is_none() {
                let (sender, receiver) = mpsc::channel::<Message>();
                let handle = thread::Builder::new()
                    .name("localsr-stay-awake".into())
                    .spawn(move || {
                        let mut held = false;
                        while let Ok(message) = receiver.recv() {
                            match message {
                                Message::Set(true) => {
                                    // SAFETY: `SetThreadExecutionState` sets this dedicated
                                    // thread's continuous idle-sleep requirement.
                                    unsafe {
                                        SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED);
                                    }
                                    held = true;
                                }
                                Message::Set(false) => {
                                    // SAFETY: clears the requirement on the same thread that set it.
                                    unsafe {
                                        SetThreadExecutionState(ES_CONTINUOUS);
                                    }
                                    held = false;
                                }
                                Message::Stop => break,
                            }
                        }
                        if held {
                            // SAFETY: clears any remaining execution state before thread exit.
                            unsafe {
                                SetThreadExecutionState(ES_CONTINUOUS);
                            }
                        }
                    })
                    .ok()?;
                self.sender = Some(sender);
                self.thread = Some(handle);
            }
            self.sender.as_ref()
        }
    }

    impl Backend for OsBackend {
        fn acquire(&mut self) {
            if let Some(sender) = self.ensure_thread() {
                let _ = sender.send(Message::Set(true));
            }
        }

        fn release(&mut self) {
            if let Some(sender) = &self.sender {
                let _ = sender.send(Message::Set(false));
            }
        }
    }

    impl Drop for OsBackend {
        fn drop(&mut self) {
            if let Some(sender) = self.sender.take() {
                let _ = sender.send(Message::Stop);
            }
            if let Some(handle) = self.thread.take() {
                let _ = handle.join();
            }
        }
    }
}

#[cfg(target_os = "linux")]
mod os {
    use std::{
        env,
        os::unix::process::CommandExt,
        path::PathBuf,
        process::{Child, Command, Stdio},
        sync::{
            atomic::{AtomicBool, Ordering},
            mpsc::{self, Sender},
        },
        thread::{self, JoinHandle},
    };

    use super::{find_in_paths, Backend};
    use crate::process_tree::{lead_own_group, ProcessTree};

    enum Message {
        Set(bool),
        Stop,
    }

    pub(crate) struct OsBackend {
        sender: Option<Sender<Message>>,
        thread: Option<JoinHandle<()>>,
    }

    impl OsBackend {
        pub(crate) fn new() -> Self {
            Self {
                sender: None,
                thread: None,
            }
        }

        fn ensure_thread(&mut self) -> Option<&Sender<Message>> {
            if self.sender.is_none() {
                let (sender, receiver) = mpsc::channel::<Message>();
                let handle = thread::Builder::new()
                    .name("localsr-stay-awake".into())
                    .spawn(move || {
                        let mut active: Option<(Child, ProcessTree)> = None;
                        while let Ok(message) = receiver.recv() {
                            match message {
                                Message::Set(true) => {
                                    if active.as_mut().is_some_and(|(child, tree)| {
                                        tree.try_wait(child).ok().flatten().is_none()
                                    }) {
                                        continue;
                                    }
                                    active = spawn_inhibit();
                                }
                                Message::Set(false) => {
                                    stop_inhibit(active.take());
                                }
                                Message::Stop => break,
                            }
                        }
                        stop_inhibit(active.take());
                    })
                    .ok()?;
                self.sender = Some(sender);
                self.thread = Some(handle);
            }
            self.sender.as_ref()
        }
    }

    impl Backend for OsBackend {
        fn acquire(&mut self) {
            if let Some(sender) = self.ensure_thread() {
                let _ = sender.send(Message::Set(true));
            }
        }

        fn release(&mut self) {
            if let Some(sender) = &self.sender {
                let _ = sender.send(Message::Set(false));
            }
        }
    }

    impl Drop for OsBackend {
        fn drop(&mut self) {
            if let Some(sender) = self.sender.take() {
                let _ = sender.send(Message::Stop);
            }
            if let Some(handle) = self.thread.take() {
                let _ = handle.join();
            }
        }
    }

    fn find_inhibit() -> Option<PathBuf> {
        let path = env::var_os("PATH")?;
        find_in_paths("systemd-inhibit", env::split_paths(&path))
    }

    fn spawn_inhibit() -> Option<(Child, ProcessTree)> {
        let Some(program) = find_inhibit() else {
            static MISSING_LOGGED: AtomicBool = AtomicBool::new(false);
            if !MISSING_LOGGED.swap(true, Ordering::SeqCst) {
                eprintln!(
                    "LocalSR could not find systemd-inhibit on PATH; the system may still sleep while jobs run."
                );
            }
            return None;
        };
        let mut command = Command::new(program);
        command
            .args([
                "--what=idle:sleep",
                "--who=LocalSR",
                "--why=Processing images or video",
                "--mode=block",
                "sleep",
                "infinity",
            ])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        lead_own_group(&mut command);

        extern "C" {
            fn prctl(option: i32, ...) -> i32;
            fn getpid() -> i32;
            fn getppid() -> i32;
            fn raise(sig: i32) -> i32;
        }
        const PR_SET_PDEATHSIG: i32 = 1;
        const SIGTERM: i32 = 15;
        // SAFETY: `getpid` is async-signal-safe and `pre_exec` runs on this
        // dedicated long-lived thread so parent death fires only if the host dies.
        let parent_pid = unsafe { getpid() };
        unsafe {
            command.pre_exec(move || {
                if prctl(PR_SET_PDEATHSIG, SIGTERM as usize, 0usize, 0usize, 0usize) == 0
                    && getppid() != parent_pid
                {
                    raise(SIGTERM);
                }
                Ok(())
            });
        }
        match command.spawn() {
            Ok(child) => {
                let tree = ProcessTree::new(&child);
                Some((child, tree))
            }
            Err(error) => {
                static SPAWN_LOGGED: AtomicBool = AtomicBool::new(false);
                if !SPAWN_LOGGED.swap(true, Ordering::SeqCst) {
                    eprintln!("LocalSR could not start systemd-inhibit: {error}");
                }
                None
            }
        }
    }

    fn stop_inhibit(active: Option<(Child, ProcessTree)>) {
        if let Some((mut child, tree)) = active {
            tree.terminate();
            let _ = child.kill();
            let _ = tree.wait(&mut child);
        }
    }
}

#[cfg(not(any(target_os = "macos", target_os = "windows", target_os = "linux")))]
mod os {
    use super::Backend;

    pub(crate) struct OsBackend;

    impl OsBackend {
        pub(crate) fn new() -> Self {
            Self
        }
    }

    impl Backend for OsBackend {
        fn acquire(&mut self) {}
        fn release(&mut self) {}
    }
}

#[cfg(test)]
mod tests {
    use std::sync::{Arc, Mutex};

    use super::*;

    #[derive(Clone, Debug, PartialEq, Eq)]
    enum Call {
        Acquire,
        Release,
    }

    #[derive(Clone, Default)]
    struct FakeBackend {
        calls: Arc<Mutex<Vec<Call>>>,
    }

    impl Backend for FakeBackend {
        fn acquire(&mut self) {
            self.calls.lock().unwrap().push(Call::Acquire);
        }

        fn release(&mut self) {
            self.calls.lock().unwrap().push(Call::Release);
        }
    }

    #[test]
    fn acquires_once_keeps_through_overlapping_jobs_and_releases_when_idle() {
        let calls = Arc::new(Mutex::new(Vec::new()));
        let guard = AwakeGuard::new(FakeBackend {
            calls: calls.clone(),
        });

        guard.update(false, true);
        assert!(!guard.is_held());
        assert!(calls.lock().unwrap().is_empty());

        guard.update(true, true);
        assert!(guard.is_held());
        // Overlapping jobs keep active = true and must not re-acquire.
        guard.update(true, true);
        guard.update(true, true);
        assert_eq!(*calls.lock().unwrap(), vec![Call::Acquire]);

        guard.update(false, true);
        assert!(!guard.is_held());
        // Redundant idle calls do not double-release.
        guard.update(false, true);
        assert_eq!(*calls.lock().unwrap(), vec![Call::Acquire, Call::Release]);
    }

    #[test]
    fn releases_on_drop_only_when_held() {
        let held_calls = Arc::new(Mutex::new(Vec::new()));
        let held_guard = AwakeGuard::new(FakeBackend {
            calls: held_calls.clone(),
        });
        held_guard.update(true, true);
        drop(held_guard);
        assert_eq!(
            *held_calls.lock().unwrap(),
            vec![Call::Acquire, Call::Release]
        );

        let idle_calls = Arc::new(Mutex::new(Vec::new()));
        let idle_guard = AwakeGuard::new(FakeBackend {
            calls: idle_calls.clone(),
        });
        drop(idle_guard);
        assert!(idle_calls.lock().unwrap().is_empty());
    }

    #[test]
    fn setting_off_never_acquires_and_turning_off_while_active_releases() {
        let calls = Arc::new(Mutex::new(Vec::new()));
        let guard = AwakeGuard::new(FakeBackend {
            calls: calls.clone(),
        });

        guard.update(true, false);
        assert!(!guard.is_held());
        guard.update(false, false);
        assert!(calls.lock().unwrap().is_empty());

        guard.update(true, true);
        assert!(guard.is_held());
        guard.update(true, false);
        assert!(!guard.is_held());
        guard.update(true, true);
        assert!(guard.is_held());
        assert_eq!(
            *calls.lock().unwrap(),
            vec![Call::Acquire, Call::Release, Call::Acquire]
        );
    }

    #[test]
    fn find_in_paths_picks_executable_file_only() {
        let dir = tempfile::tempdir().unwrap();
        let non_exec_dir = dir.path().join("a");
        let exec_dir = dir.path().join("b");
        std::fs::create_dir_all(&non_exec_dir).unwrap();
        std::fs::create_dir_all(&exec_dir).unwrap();

        let missing = find_in_paths("systemd-inhibit", [non_exec_dir.clone(), exec_dir.clone()]);
        assert_eq!(missing, None);

        let non_exec = non_exec_dir.join("systemd-inhibit");
        std::fs::write(&non_exec, "#!/bin/sh\n").unwrap();
        let exec = exec_dir.join("systemd-inhibit");
        std::fs::write(&exec, "#!/bin/sh\n").unwrap();

        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&non_exec, std::fs::Permissions::from_mode(0o644)).unwrap();
            std::fs::set_permissions(&exec, std::fs::Permissions::from_mode(0o755)).unwrap();
            assert_eq!(
                find_in_paths("systemd-inhibit", [non_exec_dir, exec_dir]),
                Some(exec)
            );
        }
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn macos_caffeinate_runs_while_held_and_exits_on_release_and_drop() {
        fn wait_for_exit(pid: u32) -> bool {
            let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
            while std::time::Instant::now() < deadline {
                if !crate::process_tree::process_is_running(pid) {
                    return true;
                }
                std::thread::sleep(std::time::Duration::from_millis(20));
            }
            !crate::process_tree::process_is_running(pid)
        }

        let guard = StayAwake::default();
        guard.update(true, true);
        let pid = guard
            .with_backend(|backend| backend.child_pid())
            .expect("caffeinate child pid");
        assert!(crate::process_tree::process_is_running(pid));
        let comm = std::process::Command::new("ps")
            .args(["-o", "comm=", "-p", &pid.to_string()])
            .output()
            .unwrap();
        assert!(
            String::from_utf8_lossy(&comm.stdout).contains("caffeinate"),
            "unexpected process command: {}",
            String::from_utf8_lossy(&comm.stdout)
        );

        guard.update(false, true);
        assert!(guard.with_backend(|backend| backend.child_pid()).is_none());
        assert!(wait_for_exit(pid));

        let drop_guard = StayAwake::default();
        drop_guard.update(true, true);
        let drop_pid = drop_guard
            .with_backend(|backend| backend.child_pid())
            .expect("caffeinate child pid before drop");
        assert!(crate::process_tree::process_is_running(drop_pid));
        drop(drop_guard);
        assert!(wait_for_exit(drop_pid));
    }
}
