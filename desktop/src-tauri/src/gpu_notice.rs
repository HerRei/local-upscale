//! Detect a DirectX 12 graphics adapter on Windows CPU builds and build a
//! short dismissable notice pointing to the GPU download that can use it.

use crate::types::{CapabilityInfo, EngineRecommendation};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum GpuVendor {
    Nvidia,
    Amd,
    Intel,
    Other(u32),
}

impl GpuVendor {
    pub const fn from_pci_id(vendor_id: u32) -> Self {
        match vendor_id {
            0x10DE => Self::Nvidia,
            0x1002 => Self::Amd,
            0x8086 => Self::Intel,
            other => Self::Other(other),
        }
    }

    const fn priority(self) -> u8 {
        match self {
            Self::Nvidia => 3,
            Self::Amd => 2,
            Self::Intel => 1,
            Self::Other(_) => 0,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DxgiAdapter {
    pub name: String,
    pub vendor_id: u32,
    pub dedicated_video_memory: u64,
    pub is_software: bool,
    pub supports_d3d12: bool,
}

/// Remove trademark noise (`(R)`, `(TM)`, `®`, etc.) and collapse extra spaces.
pub fn clean_adapter_name(name: &str) -> String {
    let stripped = name
        .replace("(R)", "")
        .replace("(r)", "")
        .replace("(TM)", "")
        .replace("(tm)", "")
        .replace("(C)", "")
        .replace("(c)", "")
        .replace(['®', '™', '©'], "");
    stripped.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// Pick the strongest DirectX 12 capable AMD, Intel or NVIDIA adapter.
pub fn select_best_adapter(adapters: &[DxgiAdapter]) -> Option<&DxgiAdapter> {
    adapters
        .iter()
        .filter(|adapter| {
            !adapter.is_software
                && adapter.supports_d3d12
                && !clean_adapter_name(&adapter.name).is_empty()
                && !matches!(
                    GpuVendor::from_pci_id(adapter.vendor_id),
                    GpuVendor::Other(_)
                )
        })
        .max_by_key(|adapter| {
            (
                adapter.dedicated_video_memory,
                GpuVendor::from_pci_id(adapter.vendor_id).priority(),
            )
        })
}

/// Format the one-line GPU download suggestion for the highest-ranked adapter.
pub fn download_notice_for_adapters(adapters: &[DxgiAdapter]) -> Option<String> {
    let adapter = select_best_adapter(adapters)?;
    let name = clean_adapter_name(&adapter.name);
    let download = match GpuVendor::from_pci_id(adapter.vendor_id) {
        GpuVendor::Nvidia => "CUDA",
        GpuVendor::Amd | GpuVendor::Intel => "DirectML",
        GpuVendor::Other(_) => return None,
    };
    Some(format!(
        "Your {name} can run LocalSR much faster with the {download} edition."
    ))
}

/// Whether the Windows CPU edition should show the GPU download notice.
pub fn should_show_windows_cpu_notice(
    is_windows: bool,
    update_backend: &str,
    capabilities: &CapabilityInfo,
    dismissed: bool,
) -> bool {
    if !is_windows || dismissed {
        return false;
    }
    if matches!(update_backend, "directml" | "cuda" | "rocm" | "mps") {
        return false;
    }
    if update_backend == "cpu" {
        return true;
    }
    // In a local build without LOCALSR_UPDATE_BACKEND, wait until worker
    // capabilities arrive and confirm only the CPU backend is present.
    let detecting = capabilities
        .devices
        .iter()
        .all(|device| device.name.contains("detecting hardware"));
    !detecting
        && !capabilities.devices.is_empty()
        && capabilities
            .devices
            .iter()
            .all(|device| device.device_type == "cpu")
}

#[cfg(windows)]
pub fn detect_dxgi_adapters() -> Vec<DxgiAdapter> {
    if std::env::var_os("LOCALSR_SKIP_DIRECTML_PROBE").as_deref() == Some(std::ffi::OsStr::new("1"))
    {
        return Vec::new();
    }
    win_dxgi::enumerate_adapters()
}

#[cfg(not(windows))]
pub fn detect_dxgi_adapters() -> Vec<DxgiAdapter> {
    Vec::new()
}

/// Recommend an engine backend and adapter name from DXGI adapters on Windows.
#[cfg(any(windows, test))]
pub fn recommend_for_dxgi_adapters(adapters: &[DxgiAdapter]) -> EngineRecommendation {
    let Some(adapter) = select_best_adapter(adapters) else {
        return EngineRecommendation {
            backend: "cpu".into(),
            adapter_name: None,
        };
    };
    let backend = match GpuVendor::from_pci_id(adapter.vendor_id) {
        GpuVendor::Nvidia => "cuda",
        GpuVendor::Amd | GpuVendor::Intel => "directml",
        GpuVendor::Other(_) => "cpu",
    };
    EngineRecommendation {
        backend: backend.into(),
        adapter_name: (backend != "cpu").then(|| clean_adapter_name(&adapter.name)),
    }
}

#[cfg(any(target_os = "linux", test))]
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LinuxGpu {
    pub vendor: GpuVendor,
    pub name: String,
}

#[cfg(any(target_os = "linux", test))]
fn is_pci_display_line(line: &str) -> bool {
    let bytes = line.as_bytes();
    for window in bytes.windows(6) {
        if window[0] == b'['
            && window[1] == b'0'
            && window[2] == b'3'
            && window[3].is_ascii_hexdigit()
            && window[4].is_ascii_hexdigit()
            && window[5] == b']'
        {
            return true;
        }
    }
    line.contains("VGA compatible controller")
        || line.contains("3D controller")
        || line.contains("Display controller")
}

#[cfg(any(target_os = "linux", test))]
fn find_pci_vendor_device(line: &str) -> Option<(usize, u32)> {
    let bytes = line.as_bytes();
    if bytes.len() < 11 {
        return None;
    }
    let mut found = None;
    for idx in 0..=bytes.len() - 11 {
        let slice = &bytes[idx..idx + 11];
        if slice[0] == b'['
            && slice[5] == b':'
            && slice[10] == b']'
            && slice[1..5].iter().all(u8::is_ascii_hexdigit)
            && slice[6..10].iter().all(u8::is_ascii_hexdigit)
        {
            if let Ok(vendor_str) = std::str::from_utf8(&slice[1..5]) {
                if let Ok(vendor_id) = u32::from_str_radix(vendor_str, 16) {
                    found = Some((idx, vendor_id));
                }
            }
        }
    }
    found
}

/// Parse display GPUs and vendor IDs from `lspci -nn` output.
#[cfg(any(target_os = "linux", test))]
pub fn parse_lspci_gpus(output: &str) -> Vec<LinuxGpu> {
    let mut gpus = Vec::new();
    for line in output.lines() {
        let line = line.trim();
        if line.is_empty() || !is_pci_display_line(line) {
            continue;
        }
        let Some((id_offset, vendor_id)) = find_pci_vendor_device(line) else {
            continue;
        };
        let vendor = GpuVendor::from_pci_id(vendor_id);
        let prefix = &line[..id_offset];
        let raw_name = prefix
            .split_once("]: ")
            .map(|(_, rest)| rest)
            .or_else(|| prefix.split_once(": ").map(|(_, rest)| rest))
            .unwrap_or("")
            .trim();
        let cleaned = clean_adapter_name(raw_name);
        let name = if cleaned.is_empty() {
            match vendor {
                GpuVendor::Nvidia => "NVIDIA GPU".into(),
                GpuVendor::Amd => "AMD GPU".into(),
                GpuVendor::Intel => "Intel GPU".into(),
                GpuVendor::Other(_) => "GPU".into(),
            }
        } else {
            cleaned
        };
        gpus.push(LinuxGpu { vendor, name });
    }
    gpus
}

/// Parse the primary GPU name from `nvidia-smi --query-gpu=name --format=csv,noheader`.
#[cfg(any(target_os = "linux", test))]
pub fn parse_nvidia_smi_name(output: &str) -> Option<String> {
    for line in output.lines() {
        let cleaned = clean_adapter_name(line.trim());
        if !cleaned.is_empty() {
            return Some(cleaned);
        }
    }
    None
}

/// Recommend a Linux engine backend and adapter name from `lspci -nn` and `nvidia-smi` outputs.
#[cfg(any(target_os = "linux", test))]
pub fn recommend_for_linux_outputs(
    lspci_output: Option<&str>,
    nvidia_smi_output: Option<&str>,
) -> EngineRecommendation {
    let gpus = lspci_output.map(parse_lspci_gpus).unwrap_or_default();
    let smi_name = nvidia_smi_output.and_then(parse_nvidia_smi_name);
    if let Some(name) = smi_name {
        return EngineRecommendation {
            backend: "cuda".into(),
            adapter_name: Some(name),
        };
    }
    if let Some(nvidia) = gpus.iter().find(|gpu| gpu.vendor == GpuVendor::Nvidia) {
        return EngineRecommendation {
            backend: "cuda".into(),
            adapter_name: Some(nvidia.name.clone()),
        };
    }
    if let Some(amd) = gpus.iter().find(|gpu| gpu.vendor == GpuVendor::Amd) {
        return EngineRecommendation {
            backend: "rocm".into(),
            adapter_name: Some(amd.name.clone()),
        };
    }
    EngineRecommendation {
        backend: "cpu".into(),
        adapter_name: None,
    }
}

#[cfg(any(target_os = "linux", test))]
fn run_tool_with_timeout(
    program: &str,
    args: &[&str],
    timeout: std::time::Duration,
) -> Option<String> {
    use std::io::Read;
    use std::process::{Command, Stdio};

    let mut child = Command::new(program)
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .ok()?;
    let stdout = child.stdout.take()?;
    let reader = std::thread::spawn(move || {
        let mut buf = Vec::new();
        let _ = stdout.take(256 * 1024).read_to_end(&mut buf);
        buf
    });
    let start = std::time::Instant::now();
    let success = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status.success(),
            Ok(None) if start.elapsed() >= timeout => {
                let _ = child.kill();
                let _ = child.wait();
                break false;
            }
            Ok(None) => std::thread::sleep(std::time::Duration::from_millis(20)),
            Err(_) => {
                let _ = child.kill();
                let _ = child.wait();
                break false;
            }
        }
    };
    let bytes = reader.join().ok()?;
    if !success {
        return None;
    }
    String::from_utf8(bytes).ok()
}

pub fn detect_engine_recommendation() -> EngineRecommendation {
    #[cfg(windows)]
    {
        recommend_for_dxgi_adapters(&detect_dxgi_adapters())
    }
    #[cfg(target_os = "linux")]
    {
        let timeout = std::time::Duration::from_secs(3);
        let lspci = run_tool_with_timeout("lspci", &["-nn"], timeout);
        let has_nvidia = lspci.as_deref().is_some_and(|out| {
            parse_lspci_gpus(out)
                .iter()
                .any(|g| g.vendor == GpuVendor::Nvidia)
        });
        let smi = if lspci.is_none() || has_nvidia {
            run_tool_with_timeout(
                "nvidia-smi",
                &["--query-gpu=name", "--format=csv,noheader"],
                timeout,
            )
        } else {
            None
        };
        recommend_for_linux_outputs(lspci.as_deref(), smi.as_deref())
    }
    #[cfg(target_os = "macos")]
    {
        EngineRecommendation {
            backend: "mps".into(),
            adapter_name: None,
        }
    }
    #[cfg(not(any(windows, target_os = "linux", target_os = "macos")))]
    {
        EngineRecommendation {
            backend: "cpu".into(),
            adapter_name: None,
        }
    }
}

#[cfg(windows)]
mod win_dxgi {
    use std::{ffi::c_void, ptr};

    use super::DxgiAdapter;

    const LOAD_LIBRARY_SEARCH_SYSTEM32: u32 = 0x0000_0800;
    const DXGI_ERROR_NOT_FOUND: i32 = 0x887A_0002_u32 as i32;
    const DXGI_ADAPTER_FLAG_SOFTWARE: u32 = 2;
    const MICROSOFT_VENDOR_ID: u32 = 0x1414;
    const D3D_FEATURE_LEVEL_11_0: i32 = 0xB000;

    #[repr(C)]
    struct Guid {
        data1: u32,
        data2: u16,
        data3: u16,
        data4: [u8; 8],
    }

    // 770aae78-f26f-4dba-a829-253c83d1b387
    const IID_IDXGIFACTORY1: Guid = Guid {
        data1: 0x770a_ae78,
        data2: 0xf26f,
        data3: 0x4dba,
        data4: [0xa8, 0x29, 0x25, 0x3c, 0x83, 0xd1, 0xb3, 0x87],
    };

    // 189819f1-1db6-4b57-be54-1821339b85f7
    const IID_ID3D12DEVICE: Guid = Guid {
        data1: 0x1898_19f1,
        data2: 0x1db6,
        data3: 0x4b57,
        data4: [0xbe, 0x54, 0x18, 0x21, 0x33, 0x9b, 0x85, 0xf7],
    };

    #[repr(C)]
    struct Luid {
        low: u32,
        high: i32,
    }

    #[repr(C)]
    struct DxgiAdapterDesc1 {
        description: [u16; 128],
        vendor_id: u32,
        device_id: u32,
        subsys_id: u32,
        revision: u32,
        dedicated_video_memory: usize,
        dedicated_system_memory: usize,
        shared_system_memory: usize,
        luid: Luid,
        flags: u32,
    }

    #[link(name = "kernel32")]
    extern "system" {
        fn LoadLibraryExW(
            lp_lib_file_name: *const u16,
            h_file: *mut c_void,
            dw_flags: u32,
        ) -> *mut c_void;
        fn GetProcAddress(h_module: *mut c_void, lp_proc_name: *const u8) -> *const c_void;
        fn FreeLibrary(h_lib_module: *mut c_void) -> i32;
    }

    struct LoadedLibrary(*mut c_void);

    impl LoadedLibrary {
        fn system(name: &str) -> Option<Self> {
            let wide: Vec<u16> = name.encode_utf16().chain(std::iter::once(0)).collect();
            // SAFETY: null-terminated UTF-16 DLL name loaded strictly from System32.
            let handle = unsafe {
                LoadLibraryExW(wide.as_ptr(), ptr::null_mut(), LOAD_LIBRARY_SEARCH_SYSTEM32)
            };
            if handle.is_null() {
                None
            } else {
                Some(Self(handle))
            }
        }

        fn symbol(&self, name: &[u8]) -> Option<*const c_void> {
            debug_assert!(name.ends_with(b"\0"));
            // SAFETY: valid module handle and null-terminated ASCII symbol name.
            let address = unsafe { GetProcAddress(self.0, name.as_ptr()) };
            if address.is_null() {
                None
            } else {
                Some(address)
            }
        }
    }

    impl Drop for LoadedLibrary {
        fn drop(&mut self) {
            if !self.0.is_null() {
                // SAFETY: handle returned by LoadLibraryExW.
                unsafe {
                    FreeLibrary(self.0);
                }
            }
        }
    }

    struct ComPtr(*mut c_void);

    impl ComPtr {
        unsafe fn vtable_slot(&self, index: usize) -> *const c_void {
            let vtable = *(self.0 as *const *const *const c_void);
            *vtable.add(index)
        }
    }

    impl Drop for ComPtr {
        fn drop(&mut self) {
            if !self.0.is_null() {
                // SAFETY: slot 2 on every COM interface is IUnknown::Release.
                unsafe {
                    let release: extern "system" fn(*mut c_void) -> u32 =
                        std::mem::transmute(self.vtable_slot(2));
                    release(self.0);
                }
            }
        }
    }

    type CreateDxgiFactory1Fn = extern "system" fn(*const Guid, *mut *mut c_void) -> i32;
    type D3d12CreateDeviceFn =
        extern "system" fn(*mut c_void, i32, *const Guid, *mut *mut c_void) -> i32;
    type EnumAdapters1Fn = extern "system" fn(*mut c_void, u32, *mut *mut c_void) -> i32;
    type GetDesc1Fn = extern "system" fn(*mut c_void, *mut DxgiAdapterDesc1) -> i32;

    pub(super) fn enumerate_adapters() -> Vec<DxgiAdapter> {
        let Some(dxgi) = LoadedLibrary::system("dxgi.dll") else {
            return Vec::new();
        };
        let Some(d3d12) = LoadedLibrary::system("d3d12.dll") else {
            return Vec::new();
        };
        let Some(create_factory_ptr) = dxgi.symbol(b"CreateDXGIFactory1\0") else {
            return Vec::new();
        };
        let Some(create_device_ptr) = d3d12.symbol(b"D3D12CreateDevice\0") else {
            return Vec::new();
        };

        // SAFETY: symbol signatures match Windows SDK dxgi.h / d3d12.h.
        let create_factory: CreateDxgiFactory1Fn =
            unsafe { std::mem::transmute(create_factory_ptr) };
        let create_device: D3d12CreateDeviceFn = unsafe { std::mem::transmute(create_device_ptr) };

        let mut factory_raw = ptr::null_mut();
        if create_factory(&IID_IDXGIFACTORY1, &mut factory_raw) < 0 || factory_raw.is_null() {
            return Vec::new();
        }
        let factory = ComPtr(factory_raw);
        let mut adapters = Vec::new();

        for index in 0..64_u32 {
            let mut adapter_raw = ptr::null_mut();
            // SAFETY: IDXGIFactory1::EnumAdapters1 is COM vtable slot 12.
            let hr = unsafe {
                let enum_adapters: EnumAdapters1Fn = std::mem::transmute(factory.vtable_slot(12));
                enum_adapters(factory.0, index, &mut adapter_raw)
            };
            if hr == DXGI_ERROR_NOT_FOUND {
                break;
            }
            if hr < 0 || adapter_raw.is_null() {
                break;
            }
            let adapter = ComPtr(adapter_raw);
            // SAFETY: DxgiAdapterDesc1 is plain old data; IDXGIAdapter1::GetDesc1 is slot 10.
            let mut desc: DxgiAdapterDesc1 = unsafe { std::mem::zeroed() };
            let desc_hr = unsafe {
                let get_desc: GetDesc1Fn = std::mem::transmute(adapter.vtable_slot(10));
                get_desc(adapter.0, &mut desc)
            };
            if desc_hr < 0 {
                continue;
            }
            let end = desc
                .description
                .iter()
                .position(|&ch| ch == 0)
                .unwrap_or(desc.description.len());
            let name = String::from_utf16_lossy(&desc.description[..end]);
            let is_software = (desc.flags & DXGI_ADAPTER_FLAG_SOFTWARE) != 0
                || desc.vendor_id == MICROSOFT_VENDOR_ID;
            let supports_d3d12 = !is_software
                && create_device(
                    adapter.0,
                    D3D_FEATURE_LEVEL_11_0,
                    &IID_ID3D12DEVICE,
                    ptr::null_mut(),
                ) >= 0;
            adapters.push(DxgiAdapter {
                name,
                vendor_id: desc.vendor_id,
                dedicated_video_memory: desc.dedicated_video_memory as u64,
                is_software,
                supports_d3d12,
            });
        }
        adapters
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::DeviceInfo;

    const GIB: u64 = 1024 * 1024 * 1024;

    fn adapter(
        name: &str,
        vendor_id: u32,
        dedicated_video_memory: u64,
        is_software: bool,
        supports_d3d12: bool,
    ) -> DxgiAdapter {
        DxgiAdapter {
            name: name.into(),
            vendor_id,
            dedicated_video_memory,
            is_software,
            supports_d3d12,
        }
    }

    #[test]
    fn formats_directml_and_cuda_notices_for_supported_vendors() {
        let amd = [adapter(
            "AMD Radeon(TM) RX 7900 XT",
            0x1002,
            20 * GIB,
            false,
            true,
        )];
        assert_eq!(
            download_notice_for_adapters(&amd).as_deref(),
            Some(
                "Your AMD Radeon RX 7900 XT can run LocalSR much faster with the DirectML edition."
            )
        );

        let intel = [adapter(
            "Intel(R) UHD Graphics 620",
            0x8086,
            128 * 1024 * 1024,
            false,
            true,
        )];
        assert_eq!(
            download_notice_for_adapters(&intel).as_deref(),
            Some("Your Intel UHD Graphics 620 can run LocalSR much faster with the DirectML edition.")
        );

        let nvidia = [adapter(
            "NVIDIA GeForce RTX 4070",
            0x10DE,
            12 * GIB,
            false,
            true,
        )];
        assert_eq!(
            download_notice_for_adapters(&nvidia).as_deref(),
            Some("Your NVIDIA GeForce RTX 4070 can run LocalSR much faster with the CUDA edition.")
        );
    }

    #[test]
    fn ranks_discrete_adapters_over_integrated_and_prefers_nvidia_on_tie() {
        let hybrid_nvidia = [
            adapter(
                "Intel(R) UHD Graphics",
                0x8086,
                128 * 1024 * 1024,
                false,
                true,
            ),
            adapter(
                "NVIDIA GeForce RTX 4060 Laptop GPU",
                0x10DE,
                8 * GIB,
                false,
                true,
            ),
        ];
        assert_eq!(
            download_notice_for_adapters(&hybrid_nvidia).as_deref(),
            Some("Your NVIDIA GeForce RTX 4060 Laptop GPU can run LocalSR much faster with the CUDA edition.")
        );

        let hybrid_amd = [
            adapter(
                "Intel(R) Iris(R) Xe Graphics",
                0x8086,
                128 * 1024 * 1024,
                false,
                true,
            ),
            adapter("AMD Radeon RX 7800 XT", 0x1002, 16 * GIB, false, true),
        ];
        assert_eq!(
            download_notice_for_adapters(&hybrid_amd).as_deref(),
            Some(
                "Your AMD Radeon RX 7800 XT can run LocalSR much faster with the DirectML edition."
            )
        );

        let tie = [
            adapter("Intel(R) Graphics", 0x8086, 8 * GIB, false, true),
            adapter("AMD Radeon Graphics", 0x1002, 8 * GIB, false, true),
            adapter("NVIDIA GeForce GPU", 0x10DE, 8 * GIB, false, true),
        ];
        assert_eq!(select_best_adapter(&tie).map(|a| a.vendor_id), Some(0x10DE));
    }

    #[test]
    fn skips_software_non_dx12_and_unknown_vendor_adapters() {
        let adapters = [
            adapter("Microsoft Basic Render Driver", 0x1414, 0, true, true),
            adapter("Legacy Radeon HD", 0x1002, 2 * GIB, false, false),
            adapter("Unknown Adapter", 0x1234, 8 * GIB, false, true),
        ];
        assert_eq!(download_notice_for_adapters(&adapters), None);
        assert_eq!(download_notice_for_adapters(&[]), None);
    }

    #[test]
    fn notice_visibility_respects_platform_edition_capabilities_and_dismissal() {
        let cpu_only = CapabilityInfo {
            devices: vec![DeviceInfo {
                id: "cpu".into(),
                device_type: "cpu".into(),
                name: "CPU".into(),
                ..DeviceInfo::default()
            }],
            ..CapabilityInfo::default()
        };
        let with_directml = CapabilityInfo {
            devices: vec![
                DeviceInfo {
                    id: "directml:0".into(),
                    device_type: "directml".into(),
                    name: "AMD Radeon RX 7900 XT (DirectML)".into(),
                    ..DeviceInfo::default()
                },
                cpu_only.devices[0].clone(),
            ],
            ..CapabilityInfo::default()
        };

        assert!(should_show_windows_cpu_notice(
            true, "cpu", &cpu_only, false
        ));
        assert!(!should_show_windows_cpu_notice(
            true, "cpu", &cpu_only, true
        ));
        assert!(!should_show_windows_cpu_notice(
            false, "cpu", &cpu_only, false
        ));
        assert!(!should_show_windows_cpu_notice(
            true, "directml", &cpu_only, false
        ));
        assert!(!should_show_windows_cpu_notice(
            true, "cuda", &cpu_only, false
        ));

        // Dev builds (empty update_backend) show only after capabilities confirm CPU-only.
        assert!(!should_show_windows_cpu_notice(
            true,
            "",
            &CapabilityInfo::detecting(),
            false
        ));
        assert!(should_show_windows_cpu_notice(true, "", &cpu_only, false));
        assert!(!should_show_windows_cpu_notice(
            true,
            "",
            &with_directml,
            false
        ));
    }

    #[test]
    fn windows_engine_recommendation_maps_nvidia_to_cuda_and_amd_or_intel_to_directml() {
        let nvidia = [adapter(
            "NVIDIA GeForce RTX 4070",
            0x10DE,
            12 * GIB,
            false,
            true,
        )];
        assert_eq!(
            recommend_for_dxgi_adapters(&nvidia),
            EngineRecommendation {
                backend: "cuda".into(),
                adapter_name: Some("NVIDIA GeForce RTX 4070".into()),
            }
        );

        let amd = [adapter(
            "AMD Radeon(TM) RX 7900 XT",
            0x1002,
            20 * GIB,
            false,
            true,
        )];
        assert_eq!(
            recommend_for_dxgi_adapters(&amd),
            EngineRecommendation {
                backend: "directml".into(),
                adapter_name: Some("AMD Radeon RX 7900 XT".into()),
            }
        );

        let intel = [adapter(
            "Intel(R) Arc(TM) A770 Graphics",
            0x8086,
            16 * GIB,
            false,
            true,
        )];
        assert_eq!(
            recommend_for_dxgi_adapters(&intel),
            EngineRecommendation {
                backend: "directml".into(),
                adapter_name: Some("Intel Arc A770 Graphics".into()),
            }
        );

        let software_only = [adapter(
            "Microsoft Basic Render Driver",
            0x1414,
            0,
            true,
            true,
        )];
        assert_eq!(
            recommend_for_dxgi_adapters(&software_only),
            EngineRecommendation {
                backend: "cpu".into(),
                adapter_name: None,
            }
        );
    }

    #[test]
    fn linux_lspci_and_nvidia_smi_outputs_recommend_expected_backends() {
        let lspci_nvidia = "\
00:02.0 VGA compatible controller [0300]: Intel Corporation Arrow Lake-S [Intel Graphics] [8086:7d67] (rev 06)
01:00.0 VGA compatible controller [0300]: NVIDIA Corporation AD104 [GeForce RTX 4070] [10de:2786] (rev a1)
01:00.1 Audio device [0403]: NVIDIA Corporation AD104 High Definition Audio Controller [10de:22bd] (rev a1)
";
        assert_eq!(
            recommend_for_linux_outputs(Some(lspci_nvidia), Some("NVIDIA GeForce RTX 4070\n")),
            EngineRecommendation {
                backend: "cuda".into(),
                adapter_name: Some("NVIDIA GeForce RTX 4070".into()),
            }
        );
        // Without nvidia-smi, falls back to the parsed lspci NVIDIA adapter name.
        assert_eq!(
            recommend_for_linux_outputs(Some(lspci_nvidia), None),
            EngineRecommendation {
                backend: "cuda".into(),
                adapter_name: Some("NVIDIA Corporation AD104 [GeForce RTX 4070]".into()),
            }
        );

        let lspci_amd = "\
03:00.0 Display controller [0380]: Advanced Micro Devices, Inc. [AMD/ATI] Navi 44 [Radeon RX 9060 XT] [1002:7590] (rev c0)
03:00.1 Audio device [0403]: Advanced Micro Devices, Inc. [AMD/ATI] Audio [1002:ab40]
";
        assert_eq!(
            recommend_for_linux_outputs(Some(lspci_amd), None),
            EngineRecommendation {
                backend: "rocm".into(),
                adapter_name: Some(
                    "Advanced Micro Devices, Inc. [AMD/ATI] Navi 44 [Radeon RX 9060 XT]".into()
                ),
            }
        );

        // Non-display NVIDIA/AMD devices (e.g. HDMI audio only) must not trigger CUDA/ROCm.
        let lspci_intel_plus_audio = "\
00:02.0 VGA compatible controller [0300]: Intel Corporation UHD Graphics 770 [8086:4680] (rev 0c)
00:1f.3 Audio device [0403]: Advanced Micro Devices, Inc. [AMD/ATI] Audio [1002:ab40]
";
        assert_eq!(
            recommend_for_linux_outputs(Some(lspci_intel_plus_audio), None),
            EngineRecommendation {
                backend: "cpu".into(),
                adapter_name: None,
            }
        );

        // Missing lspci with working nvidia-smi still recommends CUDA.
        assert_eq!(
            recommend_for_linux_outputs(None, Some("NVIDIA GeForce RTX 3080\n")),
            EngineRecommendation {
                backend: "cuda".into(),
                adapter_name: Some("NVIDIA GeForce RTX 3080".into()),
            }
        );

        // Missing tools never panic and fall back to CPU.
        assert_eq!(
            recommend_for_linux_outputs(None, None),
            EngineRecommendation {
                backend: "cpu".into(),
                adapter_name: None,
            }
        );
    }

    #[test]
    fn linux_tool_runner_handles_missing_binary_and_timeouts_without_panicking() {
        assert_eq!(
            run_tool_with_timeout(
                "definitely-missing-localsr-tool-xyz",
                &[],
                std::time::Duration::from_millis(200)
            ),
            None
        );
        #[cfg(unix)]
        assert_eq!(
            run_tool_with_timeout("sleep", &["5"], std::time::Duration::from_millis(80)),
            None
        );
    }
}
