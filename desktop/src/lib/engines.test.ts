import { describe, expect, it } from 'vitest';
import { demoSnapshot } from './demo';
import {
  engineDisplayName,
  engineListRows,
  engineShortName,
  plainEngineError,
  recommendedEngineOffer,
} from './engines';

describe('engine helpers', () => {
  it('formats display and short names for every backend', () => {
    expect(engineDisplayName('cpu')).toBe('CPU');
    expect(engineDisplayName('directml')).toBe('DirectML');
    expect(engineDisplayName('cuda')).toBe('NVIDIA CUDA');
    expect(engineDisplayName('rocm')).toBe('AMD ROCm');
    expect(engineShortName('cuda')).toBe('NVIDIA');
    expect(engineShortName('rocm')).toBe('AMD');
    expect(engineShortName('directml')).toBe('DirectML');
  });

  it('offers the recommended GPU engine only after setup and when not installed or dismissed', () => {
    const snapshot = demoSnapshot();
    const offer = recommendedEngineOffer(snapshot.engines, snapshot.settings);
    expect(offer?.backend).toBe('cuda');
    expect(offer?.message).toBe(
      'Your NVIDIA GeForce RTX 4070 can run LocalSR much faster with the NVIDIA engine (4.3 GB).',
    );

    expect(
      recommendedEngineOffer(snapshot.engines, {
        ...snapshot.settings,
        privacy_choice_made: false,
      }),
    ).toBeNull();

    expect(
      recommendedEngineOffer(snapshot.engines, {
        ...snapshot.settings,
        gpu_download_notice_dismissed: true,
      }),
    ).toBeNull();

    expect(
      recommendedEngineOffer(
        {
          ...snapshot.engines!,
          installed: [
            ...snapshot.engines!.installed,
            {
              id: 'windows-x86_64-cuda-demo',
              backend: 'cuda',
              bundled: false,
              active: false,
              path: '/engines/cuda',
            },
          ],
        },
        snapshot.settings,
      ),
    ).toBeNull();

    expect(
      recommendedEngineOffer(
        {
          ...snapshot.engines!,
          managed_by_store: true,
        },
        snapshot.settings,
      ),
    ).toBeNull();
  });

  it('orders rows and marks active, installed, removable, and recommended engines', () => {
    const engines = structuredClone(demoSnapshot().engines!);
    engines.installed.push({
      id: 'windows-x86_64-directml-demo',
      backend: 'directml',
      bundled: false,
      active: false,
      path: '/engines/directml',
    });
    const rows = engineListRows(engines);
    expect(rows.map((row) => row.backend)).toEqual(['cuda', 'directml', 'cpu']);
    expect(rows[0]).toMatchObject({
      backend: 'cuda',
      recommended: true,
      installed: false,
      removable: false,
    });
    expect(rows[1]).toMatchObject({
      backend: 'directml',
      installed: true,
      active: false,
      removable: true,
    });
    expect(rows[2]).toMatchObject({
      backend: 'cpu',
      installed: true,
      active: true,
      bundled: true,
      removable: false,
    });
  });

  it('maps technical error prefixes into plain wording', () => {
    expect(plainEngineError('network error: error sending request for url (https://x)')).toBe(
      'Could not reach the download server. Check your internet connection and try again.',
    );
    expect(plainEngineError('Error: Not enough free disk space')).toBe(
      'Not enough free disk space',
    );
  });

  it('prefers a verified engine when an older install also needs reinstall', () => {
    const engines = structuredClone(demoSnapshot().engines!);
    engines.installed.push(
      {
        id: 'old',
        backend: 'cuda',
        bundled: false,
        active: false,
        path: '',
        needs_reinstall: true,
      },
      { id: 'new', backend: 'cuda', bundled: false, active: true, path: '' },
    );
    expect(engineListRows(engines).find((row) => row.backend === 'cuda')?.installedEntry?.id).toBe(
      'new',
    );
  });
});
