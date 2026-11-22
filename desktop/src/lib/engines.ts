import { formatBytes } from './format';
import type {
  EngineCatalogEntry,
  EngineManagerSnapshot,
  InstalledEngine,
  UiSettings,
} from './types';

const BACKEND_RANK: Record<string, number> = {
  cuda: 0,
  rocm: 1,
  directml: 2,
  mps: 3,
  cpu: 4,
};

export interface EngineOffer {
  backend: string;
  adapterName: string;
  shortName: string;
  sizeText: string;
  message: string;
}

export interface EngineListRow {
  backend: string;
  label: string;
  installedEntry: InstalledEngine | undefined;
  catalogEntry: EngineCatalogEntry | undefined;
  installed: boolean;
  active: boolean;
  bundled: boolean;
  removable: boolean;
  recommended: boolean;
  sizeBytes: number;
}

export function engineDisplayName(backend: string): string {
  switch (backend) {
    case 'cpu':
      return 'CPU';
    case 'directml':
      return 'DirectML';
    case 'cuda':
      return 'NVIDIA CUDA';
    case 'rocm':
      return 'AMD ROCm';
    case 'mps':
      return 'Apple Silicon (MPS)';
    default:
      return backend ? backend.toUpperCase() : 'CPU';
  }
}

export function engineShortName(backend: string): string {
  switch (backend) {
    case 'cuda':
      return 'NVIDIA';
    case 'rocm':
      return 'AMD';
    case 'directml':
      return 'DirectML';
    default:
      return engineDisplayName(backend);
  }
}

export function isGpuBackend(backend: string): boolean {
  return backend === 'cuda' || backend === 'rocm' || backend === 'directml';
}

export function recommendedEngineOffer(
  engines: EngineManagerSnapshot | undefined,
  settings: UiSettings,
): EngineOffer | null {
  if (!engines || !engines.supported || engines.managed_by_store) return null;
  if (settings.privacy_choice_made === false || settings.gpu_download_notice_dismissed) return null;
  const backend = engines.recommendation.backend;
  if (!isGpuBackend(backend)) return null;
  if (engines.installed.some((item) => item.backend === backend)) return null;
  const catalogEntry = engines.catalog.find((entry) => entry.backend === backend);
  if (!engines.configured && !catalogEntry) return null;
  const adapterName = engines.recommendation.adapter_name?.trim() || 'graphics card';
  const shortName = engineShortName(backend);
  const sizeText = catalogEntry && catalogEntry.size > 0 ? formatBytes(catalogEntry.size) : '';
  const sizeSuffix = sizeText ? ` (${sizeText})` : '';
  return {
    backend,
    adapterName,
    shortName,
    sizeText,
    message: `Your ${adapterName} can run LocalSR much faster with the ${shortName} engine${sizeSuffix}.`,
  };
}

export function engineListRows(engines: EngineManagerSnapshot): EngineListRow[] {
  const backends = new Set<string>();
  for (const item of engines.installed) backends.add(item.backend);
  for (const entry of engines.catalog) backends.add(entry.backend);
  if (isGpuBackend(engines.recommendation.backend)) {
    backends.add(engines.recommendation.backend);
  }
  const sorted = [...backends].sort((a, b) => {
    const rankDiff = (BACKEND_RANK[a] ?? 99) - (BACKEND_RANK[b] ?? 99);
    return rankDiff !== 0 ? rankDiff : a.localeCompare(b);
  });

  return sorted.map((backend) => {
    const installedEntry =
      engines.installed.find((item) => item.backend === backend && !item.bundled) ??
      engines.installed.find((item) => item.backend === backend);
    const catalogEntry = engines.catalog.find((entry) => entry.backend === backend);
    const installed = Boolean(installedEntry || catalogEntry?.installed);
    const active = engines.active_backend === backend || Boolean(installedEntry?.active);
    const bundled = Boolean(installedEntry?.bundled);
    return {
      backend,
      label: engineDisplayName(backend),
      installedEntry,
      catalogEntry,
      installed,
      active,
      bundled,
      removable: Boolean(installedEntry && !installedEntry.bundled && !installedEntry.active),
      recommended: engines.recommendation.backend === backend,
      sizeBytes: catalogEntry?.size ?? 0,
    };
  });
}

export function plainEngineError(raw: unknown): string {
  const text = String(raw ?? '')
    .trim()
    .replace(/^(Error|network error|I\/O error):\s*/i, '');
  if (!text) return 'The engine operation failed.';
  if (/error sending request|dns error|timed out|connection refused/i.test(text)) {
    return 'Could not reach the download server. Check your internet connection and try again.';
  }
  return text;
}
