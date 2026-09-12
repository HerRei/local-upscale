# LocalSR feedback

Use **Issues → New issue → Beta bug report** to report a problem with LocalSR.
Include the app version, your operating system, the steps to reproduce it and
what you expected to happen. Model/device details and Copy diagnostics can help
with processing, memory or performance problems.

Issues and their attachments are public. Only include media you are allowed to
share, and remove personal information from screenshots and error messages.
Diagnostics and sample files are optional.

Image processing and SDR video are the core beta scope. HDR preservation and
SeedVR2 3B FP16/FP8 are optional Labs features, subject to the installed backend's
compatibility. SeedVR2 exports SDR. Demanding video work needs powerful hardware
and substantial memory; processing can take hours or days. Try a short clip at a
modest output resolution first. A smaller FP8 download does not guarantee that
the working memory fits.

The planned beta retains Windows CPU, CUDA and DirectML, macOS Apple Silicon/MPS,
and Linux CPU, CUDA, AMD ROCm and Intel XPU. Less-tested hardware paths are Labs.
Compatible Intel integrated graphics use the Windows DirectML engine; the
CPU-only engine does not include that acceleration. Report the selected device
and exact GPU name so CPU fallback is not mistaken for a GPU result. Testing
coverage and model availability depend on the specific package and backend.

For contact, privacy requests or confidential reports, email
[hermes.reisner@gmail.com](mailto:hermes.reisner@gmail.com).
