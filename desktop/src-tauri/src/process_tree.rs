//! A child process and everything it starts, ended together.
//!
//! Killing only the direct child leaves the helpers it started (FFmpeg, the
//! system media helper) running, and writing into scratch the host then deletes.
//! On Unix the child leads a process group of its own and the group is killed; on
//! Windows the child runs in a Job Object that kills its members when it is
//! terminated or when the host's last handle to it closes, which includes the
//! host crashing. A child must keep its helpers in the same group (no new session
//! or group, no breakaway from the job), or the host cannot reach them.
use std::process::{Child, Command};

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
            }
        }
        #[cfg(windows)]
        {
            Self {
                job: job::Job::around(child),
            }
        }
    }

    /// End every process of the tree at once. Safe to call again and after the
    /// leader has exited: it then ends what the leader left behind.
    pub fn terminate(&self) {
        #[cfg(unix)]
        {
            extern "C" {
                fn killpg(group: i32, signal: i32) -> i32;
            }
            const SIGKILL: i32 = 9;
            // A group of 0 or below would mean the caller's own group or every
            // process; only a real child group is ever signalled.
            if self.group > 1 {
                // SAFETY: killpg only sends a signal; a group that no longer exists
                // makes it fail with ESRCH, which is the wanted outcome.
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
        child.wait().unwrap();
        assert!(wait_until(|| !process_is_running(helper)));
    }

    #[test]
    fn what_a_finished_leader_left_behind_is_ended_too() {
        let directory = tempfile::tempdir().unwrap();
        let pid_file = directory.path().join("helper.pid");
        let mut command = Command::new("/bin/sh");
        command
            .arg("-c")
            .arg("sleep 60 > /dev/null 2>&1 & echo $! > \"$1\"")
            .arg("sh")
            .arg(&pid_file)
            .stdin(Stdio::null());
        lead_own_group(&mut command);
        let mut child = command.spawn().unwrap();
        let tree = ProcessTree::new(&child);
        child.wait().unwrap();
        let helper: u32 = std::fs::read_to_string(&pid_file)
            .unwrap()
            .trim()
            .parse()
            .unwrap();
        assert!(process_is_running(helper));
        tree.terminate();
        assert!(wait_until(|| !process_is_running(helper)));
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
