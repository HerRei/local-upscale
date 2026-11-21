//! Process scheduling priority for the inference engine and host-spawned helpers.
//!
//! GPU and Neural Engine command queues are scheduled by their drivers and are
//! not throttled here; this controls CPU scheduling (and disk/network I/O where
//! the OS exposes it) so other apps stay responsive.

use std::process::Command;

#[cfg(target_os = "macos")]
mod os {
    use std::{os::unix::process::CommandExt, process::Command};

    const PRIO_PROCESS: i32 = 0;
    const PRIO_PGRP: i32 = 1;
    const PRIO_DARWIN_PROCESS: i32 = 4;
    pub(crate) const PRIO_DARWIN_BG: i32 = 0x1000;

    #[derive(Clone, Copy, Debug, PartialEq, Eq)]
    pub(crate) struct PriorityMapping {
        pub darwin_bg: i32,
        pub nice: Option<i32>,
    }

    pub(crate) const fn priority_mapping(low_priority: bool) -> PriorityMapping {
        if low_priority {
            // No `nice` here: an unprivileged process cannot raise it back, and
            // background QoS alone is fully reversible while the engine runs.
            PriorityMapping {
                darwin_bg: PRIO_DARWIN_BG,
                nice: None,
            }
        } else {
            PriorityMapping {
                darwin_bg: 0,
                nice: None,
            }
        }
    }

    extern "C" {
        fn setpriority(which: i32, who: u32, prio: i32) -> i32;
    }

    pub fn configure_command(command: &mut Command, low_priority: bool) {
        if !low_priority {
            return;
        }
        let mapping = priority_mapping(true);
        // SAFETY: `pre_exec` runs after fork and before exec; `setpriority` is a direct
        // async-signal-safe syscall and returns any failure without touching Rust state.
        unsafe {
            command.pre_exec(move || {
                // `PRIO_DARWIN_PROCESS` with `who = 0` places the child in background QoS
                // before Python starts, so all future child processes inherit it.
                let _ = setpriority(PRIO_DARWIN_PROCESS, 0, mapping.darwin_bg);
                if let Some(nice) = mapping.nice {
                    let _ = setpriority(PRIO_PROCESS, 0, nice);
                }
                Ok(())
            });
        }
    }

    /// Update an already-running process and its process group.
    ///
    /// On macOS, `man setpriority` states `who = 0` for `PRIO_DARWIN_PROCESS`, while
    /// `<sys/resource.h>` documents `who` as a PID and XNU accepts a child PID from the
    /// parent (dropping its priority to background and restoring it on `0`). Running
    /// grandchildren keep their previous QoS until updated individually by the worker via
    /// `set_low_priority`.
    pub fn apply_to_pid(pid: u32, low_priority: bool) {
        if pid == 0 {
            return;
        }
        let mapping = priority_mapping(low_priority);
        // SAFETY: plain syscall on a numeric PID.
        unsafe {
            let _ = setpriority(PRIO_DARWIN_PROCESS, pid, mapping.darwin_bg);
            if let Some(nice) = mapping.nice {
                if setpriority(PRIO_PGRP, pid, nice) != 0 {
                    let _ = setpriority(PRIO_PROCESS, pid, nice);
                }
            }
        }
    }
}

#[cfg(target_os = "windows")]
mod os {
    use std::{ffi::c_void, os::windows::process::CommandExt, process::Command};

    pub(crate) const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    pub(crate) const NORMAL_PRIORITY_CLASS: u32 = 0x0000_0020;
    pub(crate) const BELOW_NORMAL_PRIORITY_CLASS: u32 = 0x0000_4000;
    const PROCESS_SET_INFORMATION: u32 = 0x0200;

    #[link(name = "kernel32")]
    extern "system" {
        fn OpenProcess(access: u32, inherit_handles: i32, pid: u32) -> *mut c_void;
        fn SetPriorityClass(process: *mut c_void, priority_class: u32) -> i32;
        fn CloseHandle(handle: *mut c_void) -> i32;
    }

    pub(crate) const fn priority_class(low_priority: bool) -> u32 {
        if low_priority {
            BELOW_NORMAL_PRIORITY_CLASS
        } else {
            NORMAL_PRIORITY_CLASS
        }
    }

    pub(crate) const fn creation_flags(low_priority: bool) -> u32 {
        if low_priority {
            CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS
        } else {
            CREATE_NO_WINDOW
        }
    }

    pub fn configure_command(command: &mut Command, low_priority: bool) {
        command.creation_flags(creation_flags(low_priority));
    }

    pub fn apply_to_pid(pid: u32, low_priority: bool) {
        if pid == 0 {
            return;
        }
        // SAFETY: plain Win32 calls; handle is checked for null and closed before returning.
        unsafe {
            let handle = OpenProcess(PROCESS_SET_INFORMATION, 0, pid);
            if handle.is_null() {
                return;
            }
            let _ = SetPriorityClass(handle, priority_class(low_priority));
            let _ = CloseHandle(handle);
        }
    }
}

#[cfg(target_os = "linux")]
mod os {
    use std::{ffi::c_long, os::unix::process::CommandExt, process::Command};

    const PRIO_PROCESS: i32 = 0;
    const PRIO_PGRP: i32 = 1;
    pub(crate) const LOW_NICE: i32 = 10;

    const IOPRIO_WHO_PROCESS: i32 = 1;
    const IOPRIO_WHO_PGRP: i32 = 2;
    const IOPRIO_CLASS_NONE: i32 = 0;
    const IOPRIO_CLASS_IDLE: i32 = 3;
    const IOPRIO_CLASS_SHIFT: i32 = 13;

    #[derive(Clone, Copy, Debug, PartialEq, Eq)]
    pub(crate) struct PriorityMapping {
        pub nice: Option<i32>,
        pub ioprio: i32,
    }

    pub(crate) const fn priority_mapping(low_priority: bool) -> PriorityMapping {
        if low_priority {
            PriorityMapping {
                nice: Some(LOW_NICE),
                ioprio: IOPRIO_CLASS_IDLE << IOPRIO_CLASS_SHIFT,
            }
        } else {
            PriorityMapping {
                nice: None,
                ioprio: IOPRIO_CLASS_NONE << IOPRIO_CLASS_SHIFT,
            }
        }
    }

    const fn sys_ioprio_set() -> Option<c_long> {
        if cfg!(target_arch = "x86_64") {
            Some(251)
        } else if cfg!(any(target_arch = "aarch64", target_arch = "riscv64")) {
            Some(30)
        } else if cfg!(target_arch = "x86") {
            Some(289)
        } else if cfg!(target_arch = "arm") {
            Some(314)
        } else {
            None
        }
    }

    extern "C" {
        fn setpriority(which: i32, who: u32, prio: i32) -> i32;
        fn syscall(number: c_long, ...) -> c_long;
    }

    unsafe fn set_ioprio(which: i32, who: i32, ioprio: i32) -> bool {
        let Some(number) = sys_ioprio_set() else {
            return false;
        };
        syscall(
            number,
            c_long::from(which),
            c_long::from(who),
            c_long::from(ioprio),
        ) == 0
    }

    pub fn configure_command(command: &mut Command, low_priority: bool) {
        if !low_priority {
            return;
        }
        let mapping = priority_mapping(true);
        // SAFETY: `pre_exec` runs after fork and before exec; both `setpriority` and
        // `syscall` are async-signal-safe.
        unsafe {
            command.pre_exec(move || {
                if let Some(nice) = mapping.nice {
                    let _ = setpriority(PRIO_PROCESS, 0, nice);
                }
                let _ = set_ioprio(IOPRIO_WHO_PROCESS, 0, mapping.ioprio);
                Ok(())
            });
        }
    }

    pub fn apply_to_pid(pid: u32, low_priority: bool) {
        if pid == 0 {
            return;
        }
        let mapping = priority_mapping(low_priority);
        // SAFETY: plain syscalls on a numeric PID.
        unsafe {
            if let Some(nice) = mapping.nice {
                if setpriority(PRIO_PGRP, pid, nice) != 0 {
                    let _ = setpriority(PRIO_PROCESS, pid, nice);
                }
            }
            if let Ok(who) = i32::try_from(pid) {
                if !set_ioprio(IOPRIO_WHO_PGRP, who, mapping.ioprio) {
                    let _ = set_ioprio(IOPRIO_WHO_PROCESS, who, mapping.ioprio);
                }
            }
        }
    }
}

#[cfg(not(any(target_os = "macos", target_os = "windows", target_os = "linux")))]
mod os {
    use std::process::Command;

    pub fn configure_command(_command: &mut Command, _low_priority: bool) {}
    pub fn apply_to_pid(_pid: u32, _low_priority: bool) {}
}

pub fn configure_command(command: &mut Command, low_priority: bool) {
    os::configure_command(command, low_priority);
}

pub fn apply_to_pid(pid: u32, low_priority: bool) {
    os::apply_to_pid(pid, low_priority);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[cfg(target_os = "macos")]
    #[test]
    fn macos_priority_mapping_uses_reversible_background_qos_only() {
        let low = os::priority_mapping(true);
        assert_eq!(low.darwin_bg, os::PRIO_DARWIN_BG);
        assert_eq!(low.nice, None);

        let normal = os::priority_mapping(false);
        assert_eq!(normal.darwin_bg, 0);
        assert_eq!(normal.nice, None);
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn macos_apply_to_pid_lowers_and_restores_child_priority() {
        // `ps` reports the scheduler's PRI, which a sleeping process picks up only
        // when it is next scheduled, so poll briefly instead of reading once.
        fn pri_until(pid: u32, wanted: impl Fn(i32) -> bool) -> i32 {
            let mut last = -1;
            for _ in 0..40 {
                let output = Command::new("ps")
                    .args(["-o", "pri=", "-p", &pid.to_string()])
                    .output()
                    .expect("ps");
                last = String::from_utf8_lossy(&output.stdout)
                    .trim()
                    .parse()
                    .expect("parse pri");
                if wanted(last) {
                    break;
                }
                std::thread::sleep(std::time::Duration::from_millis(50));
            }
            last
        }

        let mut child = Command::new("/bin/sleep")
            .arg("5")
            .spawn()
            .expect("spawn sleep");
        let pid = child.id();

        apply_to_pid(pid, true);
        let pri_low = pri_until(pid, |pri| pri <= 10);
        assert!(pri_low <= 10, "expected background PRI, got {pri_low}");

        apply_to_pid(pid, false);
        let pri_normal = pri_until(pid, |pri| pri > pri_low);
        assert!(
            pri_normal > pri_low,
            "expected restored PRI > {pri_low}, got {pri_normal}"
        );

        let _ = child.kill();
        let _ = child.wait();
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn windows_priority_mapping_uses_below_normal_priority_class() {
        assert_eq!(os::priority_class(true), os::BELOW_NORMAL_PRIORITY_CLASS);
        assert_eq!(os::priority_class(false), os::NORMAL_PRIORITY_CLASS);
        assert_eq!(
            os::creation_flags(true),
            os::CREATE_NO_WINDOW | os::BELOW_NORMAL_PRIORITY_CLASS
        );
        assert_eq!(os::creation_flags(false), os::CREATE_NO_WINDOW);
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn linux_priority_mapping_uses_nice_10_and_idle_ioprio() {
        let low = os::priority_mapping(true);
        assert_eq!(low.nice, Some(os::LOW_NICE));
        assert_eq!(low.ioprio, 3 << 13);

        let normal = os::priority_mapping(false);
        assert_eq!(normal.nice, None);
        assert_eq!(normal.ioprio, 0);
    }
}
