//! Detect a DirectX 12 graphics adapter on Windows CPU builds and build a
//! short dismissable notice pointing to the GPU download that can use it.

use crate::types::CapabilityInfo;

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
        "Your {name} can run LocalSR faster with the {download} download."
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
            Some("Your AMD Radeon RX 7900 XT can run LocalSR faster with the DirectML download.")
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
            Some("Your Intel UHD Graphics 620 can run LocalSR faster with the DirectML download.")
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
            Some("Your NVIDIA GeForce RTX 4070 can run LocalSR faster with the CUDA download.")
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
            Some("Your NVIDIA GeForce RTX 4060 Laptop GPU can run LocalSR faster with the CUDA download.")
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
            Some("Your AMD Radeon RX 7800 XT can run LocalSR faster with the DirectML download.")
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
}
