import type { CapabilityInfo, DeviceInfo } from './types';
import type { FfmpegPlatform } from './ffmpeg';

/** How ONNX Runtime reaches a GPU; the worker names it in the device label (hardware.py). */
export type GpuRoute = 'DirectML' | 'OpenVINO';

const ROUTE_SUFFIX = /\s*\((DirectML|OpenVINO)\)\s*$/;
// OpenVINO's own name for a GPU ends in "(iGPU)" or "(dGPU)"; the DirectML adapter name
// of the same GPU does not. Mirrors display_name in src/localsr/core/hardware.py.
const OPENVINO_KIND = /\s*\((iGPU|dGPU)\)\s*$/;

export function routeOf(device: Pick<DeviceInfo, 'name'>): GpuRoute | null {
  const match = ROUTE_SUFFIX.exec(device.name);
  return match ? (match[1] as GpuRoute) : null;
}

/** The adapter name as people know it: no trademark marks, no route tag. */
export function cleanDeviceName(name: string): string {
  return name
    .replace(ROUTE_SUFFIX, '')
    .replace(OPENVINO_KIND, '')
    .replace(/\((R|TM|C)\)|[®™©]/g, '')
    .replace(/\s{2,}/g, ' ')
    .trim();
}

/** A device in running text: the clean name, and the route in brackets as the engine says it. */
export function deviceLabel(device: Pick<DeviceInfo, 'name'>): string {
  const route = routeOf(device);
  const name = cleanDeviceName(device.name);
  return route ? `${name} (${route})` : name;
}

export interface HardwareOption {
  id: string;
  label: string;
}

export interface HardwareGroup {
  name: string;
  /** One option per route; a device reached one way has a single option named like the group. */
  options: HardwareOption[];
}

/**
 * One group per physical device. A GPU that ONNX Runtime reaches through both DirectML and
 * OpenVINO becomes one group with an option per route; the option still names the GPU, so
 * the closed menu says which GPU and which route.
 */
export function groupedHardware(devices: DeviceInfo[]): HardwareGroup[] {
  const groups: { name: string; entries: { id: string; route: GpuRoute | null }[] }[] = [];
  for (const device of devices) {
    const route = routeOf(device);
    const name = cleanDeviceName(device.name);
    // A second entry of the same route is a second card with the same name, not a route.
    const shared =
      route &&
      groups.find(
        (group) =>
          group.name === name &&
          group.entries.every((entry) => entry.route && entry.route !== route),
      );
    if (shared) shared.entries.push({ id: device.id, route });
    else groups.push({ name, entries: [{ id: device.id, route }] });
  }
  // Two cards with one name are numbered, so the closed menu tells them apart.
  const total = new Map<string, number>();
  for (const group of groups) total.set(group.name, (total.get(group.name) ?? 0) + 1);
  const seen = new Map<string, number>();
  return groups.map((group) => {
    let name = group.name;
    if ((total.get(name) ?? 0) > 1) {
      const number = (seen.get(name) ?? 0) + 1;
      seen.set(name, number);
      name = `${name} (${number})`;
    }
    return {
      name,
      options:
        group.entries.length > 1
          ? group.entries.map((entry) => ({ id: entry.id, label: `${name} · ${entry.route}` }))
          : [{ id: group.entries[0].id, label: name }],
    };
  });
}

/** The label of the option with this id, for the closed menu's tooltip. */
export function optionLabel(groups: HardwareGroup[], id: string): string {
  for (const group of groups) {
    const option = group.options.find((entry) => entry.id === id);
    if (option) return option.label;
  }
  return '';
}

export interface HardwareHint {
  text: string;
  /** The fix is another download of LocalSR, so the hint links to the download page. */
  downloads: boolean;
}

/**
 * What a computer that runs on the CPU could use instead: another download of LocalSR, Intel's
 * compute driver, or the NPU compiler. The last two are system packages, so the hint names
 * them instead of linking to LocalSR's downloads.
 */
export function cpuHint(
  capabilities: CapabilityInfo,
  platform: FfmpegPlatform,
): HardwareHint | null {
  const gpu = capabilities.gpu_suggestion;
  if (gpu?.download === 'intel-driver')
    return {
      text: `Runs on the CPU. ${cleanDeviceName(gpu.name)} can run models once Intel's compute driver, the intel-opencl-icd package, is installed.`,
      downloads: false,
    };
  if (gpu)
    return {
      text: `Runs on the CPU. This computer has ${cleanDeviceName(gpu.name)}, which the ${gpu.download} download of LocalSR can use.`,
      downloads: true,
    };
  const npu = capabilities.npu_hint;
  if (npu)
    return {
      text: `Runs on the CPU. The NPU (${cleanDeviceName(npu.name)}) can run models once ${
        platform === 'windows'
          ? 'the current Intel NPU driver'
          : "the intel-driver-compiler-npu package from Intel's NPU driver"
      } is installed.`,
      downloads: false,
    };
  return null;
}
