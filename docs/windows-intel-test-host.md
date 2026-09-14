# Windows Intel acceptance device

Native Windows testing uses an Acer Swift SF514-52T. It is reserved for CPU,
Intel integrated-graphics and Microsoft Store/MSIX acceptance; it is not a build
runner. The [hardware snapshot](windows-intel-test-host-2026-09.json) retains the
reported OS, device and driver information.

| Component | Recorded configuration |
| --- | --- |
| Operating system | Windows 11 Home, build 10.0.26200, x64 |
| Processor | Intel Core i7-8550U |
| System memory | 15.89 GiB |
| Graphics | Intel UHD Graphics 620 |
| Driver | 24.20.100.6286, dated 15 August 2018; WDDM 2.4 |
| DirectX capability | DDI 12; feature levels include 12_1 and 12_0 |

## Recorded application checks

Real DirectML HAT-S inference produced finite 4× output. The CPU/GPU comparison
measured mean absolute error `3.599e-7` and maximum error `4.05e-6`. PyTorch reported
an `aten::roll` CPU fallback, so this does not establish that every operator runs
on the GPU. The existing graphics driver was retained.

Installed CPU and iGPU benchmarks completed separately. Image/video processing,
comparison controls, completed-result switching, live tiles/ETA, cancellation and
worker recovery have native acceptance records.

MSIX upgrades through package 1.0.7.0 preserved settings, recipes, model hashes and
logical queue contents. Explicit uninstall removed the profile; restoration from
a verified backup after reinstalling passed. WACK remains **WARNING** despite
an actual PerMonitorV2 window. Store certification and final external Open/Reveal
observation remain incomplete.

Read the [dated acceptance record](beta-acceptance-2026-09-12.md) for exact package
versions and test scope. These observations cover this device and driver, not all
Intel graphics. New runtime or packaging changes require affected checks again.

## Operator records

Remote access and input were verified for the authorized test setup. Connection
addresses, account names, power-policy restoration values and access-client
instructions are kept in local operator records. The complete originals were
preserved before preparing this public summary. No remote access configuration,
credentials or private key is part of the public hardware record.
