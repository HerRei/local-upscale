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
  /** The GPU's name over its routes, or a single entry's own label. */
  name: string;
  /** One option per route of a paired GPU; anything else is a single option. */
  options: HardwareOption[];
}

/**
 * One group per physical device where that is certain. A GPU that ONNX Runtime reaches through
 * both DirectML and OpenVINO becomes one group with an option per route; the option still names
 * the GPU, so the closed menu says which GPU and which route.
 *
 * The device list carries no shared adapter identity (DirectML ids are adapter indices,
 * OpenVINO ids UUIDs, and the two lists can order identical cards differently), so the routes
 * are paired only when each route sees exactly one GPU of that name. Otherwise every route of
 * every card is its own entry, named with its route and numbered within it.
 */
export function groupedHardware(devices: DeviceInfo[]): HardwareGroup[] {
  const entries = devices.map((device) => ({
    id: device.id,
    route: routeOf(device),
    name: cleanDeviceName(device.name),
  }));
  const count = (name: string, route: GpuRoute | null) =>
    entries.filter((entry) => entry.name === name && entry.route === route).length;
  const paired = (name: string) => count(name, 'DirectML') === 1 && count(name, 'OpenVINO') === 1;
  const groups: HardwareGroup[] = [];
  const pairs = new Map<string, HardwareGroup>();
  const numbers = new Map<string, number>();
  for (const entry of entries) {
    if (entry.route && paired(entry.name)) {
      let group = pairs.get(entry.name);
      if (!group) {
        group = { name: entry.name, options: [] };
        pairs.set(entry.name, group);
        groups.push(group);
      }
      group.options.push({ id: entry.id, label: `${entry.name} · ${entry.route}` });
      continue;
    }
    // On its own, an entry names its route when the same GPU also shows up through the other.
    const otherRoute = entries.some(
      (other) => other.name === entry.name && other.route && other.route !== entry.route,
    );
    let label = entry.route && otherRoute ? `${entry.name} · ${entry.route}` : entry.name;
    // Two cards with one name are numbered, so the closed menu tells them apart.
    if (count(entry.name, entry.route) > 1) {
      const number = (numbers.get(label) ?? 0) + 1;
      numbers.set(label, number);
      label = `${label} (${number})`;
    }
    groups.push({ name: label, options: [{ id: entry.id, label }] });
  }
  return groups;
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
