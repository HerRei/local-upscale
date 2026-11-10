import { describe, expect, it } from 'vitest';
import {
  cleanDeviceName,
  cpuHint,
  deviceLabel,
  groupedHardware,
  optionLabel,
  routeOf,
} from './hardwareRoutes';
import { demoSnapshot } from './demo';
import type { DeviceInfo } from './types';

function device(id: string, name: string, type = id.split(':')[0]): DeviceInfo {
  return {
    id,
    type,
    name,
    total_memory: 0,
    free_memory: 0,
    supports_fp16: false,
    is_integrated: true,
    recommended_tile_sizes: [64, 128],
  };
}

const cpu = device('cpu', 'CPU');
const dml = device('directml:0', 'Intel(R) UHD Graphics 620 (DirectML)');
// OpenVINO's own name for the same GPU carries "(iGPU)"; the DirectML adapter name does not.
const ov = device('openvino:8680', 'Intel(R) UHD Graphics 620 (iGPU) (OpenVINO)');
const npu = device('openvino:80d1', 'Intel(R) AI Boost (NPU)', 'npu');
const nvidia = device('directml:1', 'NVIDIA GeForce RTX 3060 (DirectML)');

describe('hardware routes', () => {
  it('reads the route from the worker label and cleans the adapter name', () => {
    expect(routeOf(dml)).toBe('DirectML');
    expect(routeOf(ov)).toBe('OpenVINO');
    expect(routeOf(npu)).toBeNull();
    expect(routeOf(cpu)).toBeNull();
    // The same names as display_name in hardware.py (tests/test_hardware.py).
    expect(cleanDeviceName('Intel(R) UHD Graphics 620')).toBe('Intel UHD Graphics 620');
    expect(cleanDeviceName(ov.name)).toBe('Intel UHD Graphics 620');
    expect(cleanDeviceName('Intel(R) Arc(TM) A770 Graphics (dGPU)')).toBe(
      'Intel Arc A770 Graphics',
    );
    expect(cleanDeviceName('Intel® Arc™ 140V GPU (16GB)')).toBe('Intel Arc 140V GPU (16GB)');
    expect(cleanDeviceName(npu.name)).toBe('Intel AI Boost (NPU)');
    expect(cleanDeviceName('Apple GPU (Metal)')).toBe('Apple GPU (Metal)');
  });

  it('names a device in running text as the engine does', () => {
    expect(deviceLabel(dml)).toBe('Intel UHD Graphics 620 (DirectML)');
    expect(deviceLabel(ov)).toBe('Intel UHD Graphics 620 (OpenVINO)');
    expect(deviceLabel(npu)).toBe('Intel AI Boost (NPU)');
    expect(deviceLabel(cpu)).toBe('CPU');
  });

  it('groups a GPU reachable two ways under its name and keeps both routes as options', () => {
    const groups = groupedHardware([cpu, dml, ov, npu, nvidia]);
    expect(groups.map((group) => group.name)).toEqual([
      'CPU',
      'Intel UHD Graphics 620',
      'Intel AI Boost (NPU)',
      'NVIDIA GeForce RTX 3060',
    ]);
    expect(groups[1].options).toEqual([
      { id: 'directml:0', label: 'Intel UHD Graphics 620 · DirectML' },
      { id: 'openvino:8680', label: 'Intel UHD Graphics 620 · OpenVINO' },
    ]);
    // One route: one option named like the device, no route tag.
    expect(groups[3].options).toEqual([{ id: 'directml:1', label: 'NVIDIA GeForce RTX 3060' }]);
    expect(optionLabel(groups, 'openvino:8680')).toBe('Intel UHD Graphics 620 · OpenVINO');
    expect(optionLabel(groups, 'cuda:0')).toBe('');
  });

  it('keeps two cards with one name apart and numbers them', () => {
    const twin = device('directml:2', 'NVIDIA GeForce RTX 3060 (DirectML)');
    const groups = groupedHardware([nvidia, twin]);
    expect(groups.map((group) => group.options)).toEqual([
      [{ id: 'directml:1', label: 'NVIDIA GeForce RTX 3060 (1)' }],
      [{ id: 'directml:2', label: 'NVIDIA GeForce RTX 3060 (2)' }],
    ]);
  });

  it('leaves the routes of two identical cards apart: nothing pairs them for certain', () => {
    // DirectML lists adapters by index and OpenVINO by UUID, so card A can come first in one
    // list and second in the other. Grouping by name and order would switch cards with the route.
    const a380 = (id: string, route: string, kind = '') =>
      device(id, `Intel(R) Arc(TM) A380 Graphics${kind} (${route})`);
    const twins = [
      a380('directml:3', 'DirectML'),
      a380('directml:4', 'DirectML'),
      a380('openvino:b', 'OpenVINO', ' (dGPU)'),
      a380('openvino:a', 'OpenVINO', ' (dGPU)'),
    ];
    const groups = groupedHardware(twins);
    expect(groups.every((group) => group.options.length === 1)).toBe(true);
    expect(groups.map((group) => group.options[0])).toEqual([
      { id: 'directml:3', label: 'Intel Arc A380 Graphics · DirectML (1)' },
      { id: 'directml:4', label: 'Intel Arc A380 Graphics · DirectML (2)' },
      { id: 'openvino:b', label: 'Intel Arc A380 Graphics · OpenVINO (1)' },
      { id: 'openvino:a', label: 'Intel Arc A380 Graphics · OpenVINO (2)' },
    ]);
    // One route seeing two cards and the other one: still not certain which one.
    expect(
      groupedHardware([twins[0], twins[1], twins[2]]).map((group) => group.options[0].label),
    ).toEqual([
      'Intel Arc A380 Graphics · DirectML (1)',
      'Intel Arc A380 Graphics · DirectML (2)',
      'Intel Arc A380 Graphics · OpenVINO',
    ]);
    // The menu shows a single entry by its label.
    expect(groups[0].name).toBe('Intel Arc A380 Graphics · DirectML (1)');
  });

  it('names what a CPU-only run could use, and links only to another LocalSR download', () => {
    const capabilities = demoSnapshot().capabilities;
    expect(cpuHint(capabilities, 'linux')).toBeNull();

    capabilities.gpu_suggestion = { name: 'an NVIDIA GPU', download: 'CUDA' };
    expect(cpuHint(capabilities, 'linux')).toEqual({
      text: 'Runs on the CPU. This computer has an NVIDIA GPU, which the CUDA download of LocalSR can use.',
      downloads: true,
    });
    capabilities.gpu_suggestion = { name: 'Intel(R) UHD Graphics 620', download: 'DirectML' };
    expect(cpuHint(capabilities, 'windows')?.text).toContain(
      'This computer has Intel UHD Graphics 620, which the DirectML download',
    );

    // Intel's compute driver is a system package: named, not linked.
    capabilities.gpu_suggestion = { name: 'Intel graphics', download: 'intel-driver' };
    expect(cpuHint(capabilities, 'linux')).toEqual({
      text: "Runs on the CPU. Intel graphics can run models once Intel's compute driver, the intel-opencl-icd package, is installed.",
      downloads: false,
    });

    // So is the NPU compiler, which depends on the system.
    capabilities.gpu_suggestion = null;
    capabilities.npu_hint = { name: 'Intel(R) AI Boost', missing: 'compiler' };
    expect(cpuHint(capabilities, 'linux')).toEqual({
      text: "Runs on the CPU. The NPU (Intel AI Boost) can run models once the intel-driver-compiler-npu package from Intel's NPU driver is installed.",
      downloads: false,
    });
    expect(cpuHint(capabilities, 'windows')?.text).toBe(
      'Runs on the CPU. The NPU (Intel AI Boost) can run models once the current Intel NPU driver is installed.',
    );
  });
});
