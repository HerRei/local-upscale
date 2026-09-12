**Native Windows Intel test device**

Prepared on 12 September 2026 at the user's request for native Windows CPU,
Intel integrated-graphics and Microsoft Store/MSIX acceptance. This is a test
device; it has not been registered as a build or CI runner. The active `.12`
release and the running Mac application were not changed.

Machine-readable observations: [access and hardware record](windows-intel-test-host-2026-09.json).

| Item | Observed value |
| --- | --- |
| Computer | `LAPTOP-G3M0MOM5`, Acer Swift SF514-52T |
| Windows | Windows 11 Home, build `10.0.26200`, x64 |
| CPU / memory | Intel Core i7-8550U; 15.89 GiB RAM |
| Graphics | Intel UHD Graphics 620 |
| Driver | `24.20.100.6286`, dated 15 August 2018; WDDM 2.4 |
| DirectX capability | DDI 12; feature levels include `12_1` and `12_0` |
| SSH | `agenttest`, administrator; public-key login verified over LAN and Tailscale |
| Desktop | TightVNC `2.8.88`, automatic Windows service |
| Tailscale | `1.102.4`, running in unattended mode; `100.103.31.7` |
| Remote hostname | `localsr-intel-test.tail34a4e0.ts.net` |

**Verified remote access**

- SSH commands and SFTP transfer work using the existing Mac SSH identity over
  LAN and Tailscale. The Tailscale transfer was checked against its SHA-256.
- TightVNC listens on **127.0.0.1:5900 only**, requires VNC authentication and
  is reached through an authenticated SSH tunnel. HTTP access is off; no VNC
  firewall exception was added.
- A temporary native Windows form displayed successfully. Remote keyboard input
  entered `LOCALSR-REMOTE-OK`; a remote mouse click produced a successful result
  in the interactive Windows session.
- The same type of test passed through the Tailscale SSH tunnel: remote input
  entered `LOCALSR-TAILSCALE-OK-12`, and a remote click verified the text and closed
  only the test window. The result is recorded with its timestamp and session ID.
- A Windows elevation prompt displayed on the secure desktop with UAC and
  `PromptOnSecureDesktop` enabled. A remote click cancelled the prompt during the
  LAN check; that secure-desktop check was not repeated over Tailscale.
- The Mac desktop-control helper refreshes the initial framebuffer before
  returning its first screenshot; the initial server frame can otherwise be blank.
- The client now also waits for a server round trip before input and before
  disconnecting. Input-only calls can otherwise finish before the last queued
  write reaches the desktop. The corrected client passed keyboard and mouse
  verification, including a final click without a subsequent screenshot action.

The user completed enrollment in the existing Tailscale account. Windows reports
Running and unattended mode enabled; the Mac sees the device online in the same
tailnet as DDP. SSH through `100.103.31.7` verified the same host key as the LAN
connection. The persistent Mac SSH aliases `localsr-intel-test` and
`localsr-intel-test-remote` use the existing Tailscale userspace socket and that
verified host key. Desktop control was verified through the alias. No enrollment
link, password or private key is stored in this repository.

The services start automatically. At the user's explicit request, automatic
standby, timed hibernation and sleep after unattended wake are disabled on both
AC power and battery. All six timeout values were read back as zero (never).
Display timeout, lid-close, power-button and critical-battery policies were not
changed. The active plan is Balanced. Previously, standby was 300 minutes on AC
and 10 minutes on battery; timed hibernation was 720 and 180 minutes respectively.
These original values and the verified settings are in the observation record.
Reboot, locked-account sign-in and lid/power-loss recovery have not been tested.

**Local operator files**

The persistent Mac client lives outside the source repository at
`~/.local/share/localsr-test-access/windows-intel/`. Its README describes the
desktop actions and tunnel lifecycle. The generated VNC credential is in a
mode-0600 file inside a mode-0700 directory; no credential or private SSH key is
included here. The client closes its SSH tunnel after each invocation.

The four temporary Windows GUI/UAC/DxDiag/Tailscale test tasks, their test folders,
the transfer probe and downloaded installers were removed after verification.
The installed access services and persistent Mac client remain available for
subsequent tests.

Both installers were obtained from the vendor and passed Windows Authenticode
validation before installation:

| Installer | SHA-256 |
| --- | --- |
| Tailscale `1.102.4` amd64 MSI | `80eb007e39dfebe17299fa1a09c79a8e1d934f76e0246c0817ebe3af675b7ef6` |
| TightVNC `2.8.88` x64 MSI | `fa86d817ac29c5ffe1e8e7095e738d9ba5ca28aa62304ac234580916622a8ca2` |

**Next acceptance work**

The observed DirectX capabilities do not establish that LocalSR's pinned
DirectML runtime or HAT inference works on this driver. Check the existing
driver first with a bounded real DirectML workload; investigate a supported
driver update if that check fails. Preserve the actual driver version in each
test result.

Prepare an isolated Windows candidate with CPU and DirectML access, then run
the [Intel acceptance workflow](intel-gpu-support.md): actual HAT-S image and
short SDR video inference, cancellation/retry, separate CPU/GPU benchmarks and
the selected device in the UI and worker. Test the installed MSIX and data
preservation separately. LocalSR inference and MSIX acceptance have **not** yet
been run on this device.

References: [Tailscale unattended mode](https://tailscale.com/docs/how-to/run-unattended),
[Tailscale MSI installation](https://tailscale.com/docs/install/windows/msi),
[TightVNC download](https://www.tightvnc.com/download.php),
[TightVNC service/loopback configuration](https://www.tightvnc.com/doc/win/TightVNC_2.7_for_Windows_Installing_from_MSI_Packages.pdf).
