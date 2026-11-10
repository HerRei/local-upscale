//! A child process and everything it starts, ended together.
//!
//! Killing only the direct child leaves the helpers it started (FFmpeg, the
//! system media helper) running, and writing into scratch the host then deletes.
//! On Unix the child leads a process group of its own and the group is killed; on
//! Windows the child runs in a Job Object that kills its members when it is
//! terminated or when the host's last handle to it closes, which includes the
//! host crashing. A child must keep its helpers in the same group (no new session
//! or group, no breakaway from the job), or the host cannot reach them.
use std::process::{Child, Command, ExitStatus};

/// Make the process `command` starts the leader of a new process group. Call it
/// before `spawn`; `ProcessTree::new` then owns the group.
pub fn lead_own_group(command: &mut Command) {
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;

        command.process_group(0);
    }
    #[cfg(not(unix))]
    {
        // The Job Object is assigned after the process exists.
        let _ = command;
    }
}

pub struct ProcessTree {
    #[cfg(unix)]
    group: i32,
    /// Set once the leader has been reaped: its number may then belong to another
    /// process, so the group is never signalled again.
    #[cfg(unix)]
    spent: std::sync::atomic::AtomicBool,
    #[cfg(windows)]
    job: Option<job::Job>,
}

impl ProcessTree {
    /// Take ownership of the tree of a child that `lead_own_group` prepared.
    pub fn new(child: &Child) -> Self {
        #[cfg(unix)]
        {
            Self {
                group: i32::try_from(child.id()).unwrap_or(-1),
                spent: std::sync::atomic::AtomicBool::new(false),
            }
        }
        #[cfg(windows)]
        {
            Self {
                job: job::Job::around(child),
            }
        }
    }

    /// End every process of the tree at once. Safe to call again. On Unix it works
    /// only while the leader is unreaped (a zombie still holds the group's number); once
    /// the child was reaped through `wait` or `try_wait` it does nothing.
    pub fn terminate(&self) {
        #[cfg(unix)]
        {
            use std::sync::atomic::Ordering;

            extern "C" {
                fn killpg(group: i32, signal: i32) -> i32;
            }
            const SIGKILL: i32 = 9;
            // A group of 0 or below would mean the caller's own group or every
            // process; only a real child group is ever signalled.
            if self.group > 1 && !self.spent.load(Ordering::SeqCst) {
                // SAFETY: killpg only sends a signal; the leader has not been
                // reaped, so the group number still belongs to this tree.
                unsafe {
                    killpg(self.group, SIGKILL);
                }
            }
        }
        #[cfg(windows)]
        if let Some(job) = &self.job {
            job.terminate();
        }
    }

    /// Wait for the child to exit and reap it. Whatever it left behind ends first,
    /// while the exited leader still holds the group's number. Use this and
    /// `try_wait`, not the child's own, for a child this tree owns.
    pub fn wait(&self, child: &mut Child) -> std::io::Result<ExitStatus> {
        #[cfg(unix)]
        if !self.spent.load(std::sync::atomic::Ordering::SeqCst) {
            // Blocks until the exit without reaping. An error means there is nothing
            // to wait for (already reaped): no group is signalled then.
            if let Ok(true) = exit_without_reaping(child.id(), true) {
                self.terminate();
            }
        }
        let status = child.wait()?;
        self.mark_spent();
        // The job outlives the process: what the child left behind ends with it.
        #[cfg(windows)]
        self.terminate();
        Ok(status)
    }

    /// Like `wait` without blocking: None while the child still runs.
    pub fn try_wait(&self, child: &mut Child) -> std::io::Result<Option<ExitStatus>> {
        #[cfg(unix)]
        if !self.spent.load(std::sync::atomic::Ordering::SeqCst) {
            match exit_without_reaping(child.id(), false) {
                Ok(false) => return Ok(None),
                Ok(true) => self.terminate(),
                Err(_) => {}
            }
        }
        let status = child.try_wait()?;
        if status.is_some() {
            self.mark_spent();
            #[cfg(windows)]
            self.terminate();
        }
        Ok(status)
    }

    fn mark_spent(&self) {
        #[cfg(unix)]
        self.spent.store(true, std::sync::atomic::Ordering::SeqCst);
    }
}

/// Whether the child has exited, seen without reaping it (`waitid` with `WNOWAIT`):
/// the exited child stays a zombie, which keeps its process and group number from
/// being handed to another process. With `block` it waits for the exit.
#[cfg(unix)]
fn exit_without_reaping(pid: u32, block: bool) -> std::io::Result<bool> {
    // Large enough for `siginfo_t` on Linux and macOS, aligned like it.
    #[repr(C, align(8))]
    struct Info([i32; 32]);
    extern "C" {
        fn waitid(kind: i32, id: u32, info: *mut Info, options: i32) -> i32;
    }
    const P_PID: i32 = 1;
    const WNOHANG: i32 = 1;
    const WEXITED: i32 = 4;
    const WNOWAIT: i32 = if cfg!(target_os = "linux") {
        0x0100_0000
    } else {
        0x20
    };
    loop {
        // Zeroed, so that "no child has changed state" leaves `si_signo` at 0.
        let mut info = Info([0; 32]);
        let options = WEXITED | WNOWAIT | if block { 0 } else { WNOHANG };
        // SAFETY: `info` is a writable buffer at least as large as `siginfo_t`.
        let result = unsafe { waitid(P_PID, pid, &raw mut info, options) };
        if result == 0 {
            return Ok(info.0[0] != 0);
        }
        let error = std::io::Error::last_os_error();
        if error.kind() != std::io::ErrorKind::Interrupted {
            return Err(error);
        }
    }
}

#[cfg(windows)]
mod job {
    use std::{ffi::c_void, os::windows::io::AsRawHandle, process::Child, ptr};

    type Handle = *mut c_void;

    #[repr(C)]
    struct BasicLimits {
        per_process_user_time: i64,
        per_job_user_time: i64,
        flags: u32,
        minimum_working_set: usize,
        maximum_working_set: usize,
        active_process_limit: u32,
        affinity: usize,
        priority_class: u32,
        scheduling_class: u32,
    }

    #[repr(C)]
    struct IoCounters {
        counters: [u64; 6],
    }

    #[repr(C)]
    struct ExtendedLimits {
        basic: BasicLimits,
        io: IoCounters,
        process_memory: usize,
        job_memory: usize,
        peak_process_memory: usize,
        peak_job_memory: usize,
    }

    // The layout Windows documents for JOBOBJECT_EXTENDED_LIMIT_INFORMATION.
    #[cfg(target_pointer_width = "64")]
    const _: () = assert!(std::mem::size_of::<ExtendedLimits>() == 144);

    const JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: u32 = 0x2000;
    const JOB_OBJECT_EXTENDED_LIMIT_INFORMATION: i32 = 9;

    #[link(name = "kernel32")]
    extern "system" {
        fn CreateJobObjectW(attributes: *mut c_void, name: *const u16) -> Handle;
        fn SetInformationJobObject(job: Handle, class: i32, info: *const c_void, size: u32) -> i32;
        fn AssignProcessToJobObject(job: Handle, process: Handle) -> i32;
        fn TerminateJobObject(job: Handle, exit_code: u32) -> i32;
        fn CloseHandle(handle: Handle) -> i32;
    }

    /// The handle's closing, with the host's exit or crash, kills what is left.
    pub struct Job(Handle);

    // SAFETY: a job handle is a plain kernel object reference, usable from any thread.
    unsafe impl Send for Job {}
    unsafe impl Sync for Job {}

    impl Job {
        /// A kill-on-close job holding `child`. None when Windows refuses; the
        /// caller then still ends the child itself. A helper the child started
        /// before this call ran stays outside the job: the engine starts none
        /// before it reads its first request.
        pub fn around(child: &Child) -> Option<Self> {
            // SAFETY: plain Win32 calls on handles this function owns or that
            // `child` keeps open for its lifetime.
            unsafe {
                let handle = CreateJobObjectW(ptr::null_mut(), ptr::null());
                if handle.is_null() {
                    return None;
                }
                let job = Self(handle);
                let mut limits: ExtendedLimits = std::mem::zeroed();
                limits.basic.flags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
                let configured = SetInformationJobObject(
                    handle,
                    JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                    (&raw const limits).cast(),
                    std::mem::size_of::<ExtendedLimits>() as u32,
                );
                if configured == 0 || AssignProcessToJobObject(handle, child.as_raw_handle()) == 0 {
                    return None;
                }
                Some(job)
            }
        }

        pub fn terminate(&self) {
            // SAFETY: the handle is open until drop.
            unsafe {
                TerminateJobObject(self.0, 1);
            }
        }
    }

    impl Drop for Job {
        fn drop(&mut self) {
            // SAFETY: closes the handle this value owns, once.
            unsafe {
                CloseHandle(self.0);
            }
        }
    }
}

/// Whether a process still runs; a zombie that nothing has reaped yet does not.
#[cfg(all(test, unix))]
pub(crate) fn process_is_running(pid: u32) -> bool {
    if let Ok(stat) = std::fs::read_to_string(format!("/proc/{pid}/stat")) {
        // The state letter follows the command name, which is in parentheses.
        return stat
            .rsplit(')')
            .next()
            .and_then(|rest| rest.split_whitespace().next())
            .is_some_and(|state| state != "Z");
    }
    std::process::Command::new("ps")
        .args(["-o", "stat=", "-p", &pid.to_string()])
        .output()
        .is_ok_and(|out| {
            let state = String::from_utf8_lossy(&out.stdout);
            !state.trim().is_empty() && !state.trim().starts_with('Z')
        })
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use std::{
        process::Stdio,
        time::{Duration, Instant},
    };

    fn wait_until(mut condition: impl FnMut() -> bool) -> bool {
        let deadline = Instant::now() + Duration::from_secs(5);
        while Instant::now() < deadline {
            if condition() {
                return true;
            }
            std::thread::sleep(Duration::from_millis(20));
        }
        condition()
    }

    #[test]
    fn terminating_the_tree_ends_the_helpers_the_child_started() {
        let directory = tempfile::tempdir().unwrap();
        let pid_file = directory.path().join("helper.pid");
        let mut command = Command::new("/bin/sh");
        command
            .arg("-c")
            .arg("sleep 60 & echo $! > \"$1\"; wait")
            .arg("sh")
            .arg(&pid_file)
            .stdin(Stdio::null());
        lead_own_group(&mut command);
        let mut child = command.spawn().unwrap();
        let tree = ProcessTree::new(&child);
        assert!(wait_until(|| std::fs::read_to_string(&pid_file)
            .is_ok_and(|text| text.trim().parse::<u32>().is_ok())));
        let helper: u32 = std::fs::read_to_string(&pid_file)
            .unwrap()
            .trim()
            .parse()
            .unwrap();
        assert!(process_is_running(helper));
        tree.terminate();
        let _ = child.kill();
        tree.wait(&mut child).unwrap();
        assert!(wait_until(|| !process_is_running(helper)));
    }

    /// A shell that starts a helper in its group and exits at once.
    fn leader_that_leaves_a_helper(directory: &std::path::Path) -> (Child, ProcessTree, u32) {
        let pid_file = directory.join("helper.pid");
        let mut command = Command::new("/bin/sh");
        command
            .arg("-c")
            .arg("sleep 60 > /dev/null 2>&1 & echo $! > \"$1\"")
            .arg("sh")
            .arg(&pid_file)
            .stdin(Stdio::null());
        lead_own_group(&mut command);
        let child = command.spawn().unwrap();
        let tree = ProcessTree::new(&child);
        assert!(wait_until(|| std::fs::read_to_string(&pid_file)
            .is_ok_and(|text| text.trim().parse::<u32>().is_ok())));
        let helper: u32 = std::fs::read_to_string(&pid_file)
            .unwrap()
            .trim()
            .parse()
            .unwrap();
        assert!(process_is_running(helper));
        (child, tree, helper)
    }

    #[test]
    fn what_a_finished_leader_left_behind_is_ended_before_it_is_reaped() {
        let directory = tempfile::tempdir().unwrap();
        let (mut child, tree, helper) = leader_that_leaves_a_helper(directory.path());
        tree.wait(&mut child).unwrap();
        assert!(wait_until(|| !process_is_running(helper)));
    }

    #[test]
    fn noticing_the_exit_of_the_leader_ends_what_it_left_behind() {
        let directory = tempfile::tempdir().unwrap();
        let (mut child, tree, helper) = leader_that_leaves_a_helper(directory.path());
        assert!(wait_until(|| tree.try_wait(&mut child).unwrap().is_some()));
        assert!(wait_until(|| !process_is_running(helper)));
    }

    #[test]
    fn a_tree_whose_leader_was_reaped_signals_nothing() {
        let mut command = Command::new("/bin/sh");
        command.args(["-c", "exit 0"]).stdin(Stdio::null());
        lead_own_group(&mut command);
        let mut child = command.spawn().unwrap();
        let mut tree = ProcessTree::new(&child);
        tree.wait(&mut child).unwrap();
        // The leader's number could belong to anything now: stand in for that with a
        // group of an unrelated process.
        let mut command = Command::new("/bin/sh");
        command.args(["-c", "exec sleep 30"]).stdin(Stdio::null());
        lead_own_group(&mut command);
        let mut bystander = command.spawn().unwrap();
        tree.group = i32::try_from(bystander.id()).unwrap();
        tree.terminate();
        std::thread::sleep(Duration::from_millis(100));
        assert!(bystander.try_wait().unwrap().is_none());
        // A tree that has not been spent still ends its group.
        let live = ProcessTree::new(&bystander);
        live.terminate();
        assert!(wait_until(|| bystander.try_wait().unwrap().is_some()));
    }

    #[test]
    fn the_child_leads_a_group_of_its_own() {
        let mut command = Command::new("/bin/sh");
        command.args(["-c", "exec sleep 30"]).stdin(Stdio::null());
        lead_own_group(&mut command);
        let mut child = command.spawn().unwrap();
        extern "C" {
            fn getpgid(pid: i32) -> i32;
            fn getpgrp() -> i32;
        }
        let pid = i32::try_from(child.id()).unwrap();
        // SAFETY: plain queries about live processes.
        let (group, own) = unsafe { (getpgid(pid), getpgrp()) };
        let _ = child.kill();
        let _ = child.wait();
        assert_eq!(group, pid);
        assert_ne!(group, own);
    }
}
