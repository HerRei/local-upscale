**Intel graphics in LocalSR**

Compatible Intel integrated GPUs are part of the Windows DirectML target.
Microsoft's [PyTorch/DirectML documentation](https://learn.microsoft.com/en-us/windows/ai/directml/pytorch-windows)
requires a DirectX 12-capable GPU and a suitable graphics driver. The
[DirectML project](https://github.com/microsoft/DirectML) includes Intel among
its supported GPU vendors. This is backend compatibility, not a claim that
every Intel GPU/model combination has passed LocalSR acceptance.

**Choose the matching engine**

- Windows integrated graphics: the `windows-x86_64-directml` engine includes
  `torch-directml`; its detected adapters appear under Advanced → Hardware
  using their actual names, for example `Intel(R) Iris(R) Xe Graphics (DirectML)`.
  The CPU-only engine does not contain the DirectML runtime.
- Linux Intel graphics: `linux-x86_64-xpu` uses PyTorch XPU on hardware supported
  by that runtime and driver. It is a different backend from Windows DirectML.
- Image enhancement and frame-by-frame video use the selected adapter through
  the existing Spandrel inference pipeline. Model operations still need to be
  supported by that backend; SeedVR2 currently has no DirectML/XPU implementation.

The existing Windows DirectML requirements pin `torch-directml 0.2.5.dev240914`
and Torch 2.4.1. The PyInstaller hook includes the DirectML DLL and native module.
This local fix does not change runtime pins or the active `.12` build matrix.
The existing [runtime readiness item](../ci/beta-readiness.json) remains open
for the future beta candidate.

**Local fixes on 12 September 2026**

- The Python preset resolver ranked DirectML below CPU. Quick/Best automatic
  selection now considers DirectML before CPU, retaining shared-memory estimates
  and the model's precision limits.
- The device manager now returns discovered indexed GPU IDs for CUDA, XPU and
  DirectML instead of overlooking them when choosing its default.
- DirectML discovery uses the runtime's `device_name(index)` API. A failed name
  lookup keeps that adapter's original ID selectable; a broken DLL/driver leaves
  CPU capability discovery available.
- An explicitly selected DirectML adapter that disappears reports a refresh/
  reselection error instead of using another GPU. A runtime load failure explains
  that the DirectML engine/driver needs attention.
- The desktop hardware selector explains Intel integrated-graphics support.
  Desktop job tests retain the chosen indexed Intel GPU, while the existing
  model/backend compatibility controls remain in place.

Local regression checks cover device names and IDs on a simulated hybrid laptop,
driver/name lookup failures, automatic GPU selection, shared-memory preset
planning, native-device resolution and frontend job submission. These tests run
on the current Mac without launching or replacing its LocalSR GUI. They do not
execute a Windows DirectML driver or establish Intel GPU model quality.

Verification completed locally: **597 Python tests passed, 3 skipped; 49 focused
frontend tests passed** (`App.test.ts` and `VideoMemory.test.ts`). Svelte checks
reported zero errors/warnings. Python lint/format checks and website validation
passed. These code checks did not modify a Windows or Linux machine. The later
native-device setup is recorded below.

**Actual Intel GPU acceptance still to record**

A native Windows 11 Home laptop with Intel UHD 620 is now available for the
next test stage. SSH and desktop input/UAC access passed LAN checks; Tailscale
login is pending. The installed Intel driver is `24.20.100.6286` from 2018,
with DirectX DDI 12 / WDDM 2.4. This is hardware/access evidence, not a DirectML
inference pass. See the [device record](windows-intel-test-host.md).

Use an isolated Windows installation of the DirectML candidate. Record the app
and engine versions/hashes, Windows version, Intel GPU model and driver. Check
that Hardware and the GPU benchmark identify that Intel adapter, then run:

1. A small stock HAT-S image job and short frame-by-frame SDR video, inspecting
   dimensions, output pixels, timing and audio.
2. Cancellation followed by another job; small tiles under shared-memory pressure.
3. Separate CPU and Intel GPU benchmarks, preserving each selected device and
   score. Report any backend operator fallback separately.
4. Installed update/data preservation using the actual distribution package.

The existing [packaged-worker harness](../scripts/acceptance_worker.py) can run
its bounded SPAN cases with `--device directml:0` (or the actual discovered
index). It is a starting point alongside the HAT/UI checks above. A Windows VM
with only an emulated display does not establish physical Intel GPU acceptance.

The [beta platform matrix](beta-platform-matrix.md) retains this target while
its actual hardware evidence is gathered. Current work remains local, with
release jobs and the running Mac application untouched.
