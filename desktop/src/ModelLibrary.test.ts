// @vitest-environment jsdom
import { cleanup, render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import ModelLibrary from './ModelLibrary.svelte';
import { demoSnapshot } from './lib/demo';
import { EXAMPLES } from './lib/examples';
import type { AppSnapshot, ContentKind, Quality, TaskKind } from './lib/types';

afterEach(cleanup);

function renderLibrary(
  customize?: (snapshot: AppSnapshot) => void,
  contextOverrides: Partial<{
    task: TaskKind | '';
    videoTask: 'upscale' | 'denoise';
    content: Exclude<ContentKind, 'face'>;
    quality: Quality | '';
    slot: 'primary' | 'fix' | 'face' | 'browse' | 'edit' | 'try';
    select: string;
  }> = {},
) {
  const snapshot = demoSnapshot();
  customize?.(snapshot);
  const openLicense = vi.fn(async () => {});
  const openSource = vi.fn(async () => {});
  render(ModelLibrary, {
    props: {
      snapshot,
      settings: snapshot.settings,
      context: {
        task: 'upscale',
        content: 'photo',
        quality: 'quick',
        slot: 'browse',
        ...contextOverrides,
      },
      device: snapshot.capabilities.devices[0],
      close: vi.fn(),
      useForJob: vi.fn(),
      useVideoForJob: vi.fn(),
      rememberFix: vi.fn(),
      forgetFix: vi.fn(),
      useEditModel: vi.fn(),
      pin: vi.fn(),
      download: vi.fn(async () => {}),
      importFile: vi.fn(async () => {}),
      remove: vi.fn(async () => {}),
      chooseCustom: vi.fn(async () => {}),
      openLicense,
      openSource,
    },
  });
  return { openLicense, openSource, snapshot };
}

describe('ModelLibrary details attribution and licensing across all model kinds', () => {
  it('shows source link, license link, and commercial-use note for image models', async () => {
    const user = userEvent.setup();
    const { openLicense, openSource } = renderLibrary();
    const detail = screen.getByLabelText('Model details');

    await user.click(screen.getByRole('button', { name: /HAT-S ×4/ }));
    expect(within(detail).getByText('License')).toBeTruthy();
    expect(within(detail).getByText('Source')).toBeTruthy();
    expect(within(detail).getByText('Allowed · attribution kept')).toBeTruthy();

    await user.click(within(detail).getByRole('button', { name: 'Apache-2.0 ↗' }));
    await user.click(within(detail).getByRole('button', { name: 'XPixel Group · model source' }));
    expect(openLicense).toHaveBeenCalledWith('hat_s_x4');
    expect(openSource).toHaveBeenCalledWith('hat_s_x4');
  });

  it('shows source link, license link, and commercial-use note for video models', async () => {
    const user = userEvent.setup();
    const { openLicense, openSource } = renderLibrary();

    await user.click(screen.getByRole('button', { name: /^Video \(Labs\)/ }));
    await user.click(screen.getByRole('button', { name: /^SeedVR2-3B FP8 — Labs/ }));

    const detail = screen.getByLabelText('Model details');
    expect(within(detail).getByRole('heading', { name: 'SeedVR2-3B FP8 — Labs' })).toBeTruthy();
    expect(within(detail).getByText('License')).toBeTruthy();
    expect(within(detail).getByText('Source')).toBeTruthy();
    expect(within(detail).getByText('SHA-256 checked before use')).toBeTruthy();
    expect(within(detail).getByText('Allowed · attribution kept')).toBeTruthy();

    const sourceBtn = within(detail).getByRole('button', {
      name: 'ByteDance Seed · numz · model source',
    });
    expect(sourceBtn.getAttribute('title')).toBe(
      'https://github.com/numz/ComfyUI-SeedVR2_VideoUpscaler',
    );
    await user.click(within(detail).getByRole('button', { name: 'Apache-2.0 ↗' }));
    await user.click(sourceBtn);
    expect(openLicense).toHaveBeenCalledWith('seedvr2_3b_fp8');
    expect(openSource).toHaveBeenCalledWith('seedvr2_3b_fp8');
  });

  it('shows source link, license link, and commercial-use note for audio models', async () => {
    const user = userEvent.setup();
    const { openLicense, openSource } = renderLibrary();

    await user.click(screen.getByRole('button', { name: /^Audio/ }));
    await user.click(screen.getByRole('button', { name: /^DPDFNet\b/ }));

    const detail = screen.getByLabelText('Model details');
    expect(within(detail).getByRole('heading', { name: 'DPDFNet' })).toBeTruthy();
    expect(within(detail).getByText('dpdfnet8_48khz_hr.onnx')).toBeTruthy();
    expect(within(detail).getByText('Allowed · attribution kept')).toBeTruthy();

    await user.click(within(detail).getByRole('button', { name: 'Apache-2.0 ↗' }));
    await user.click(within(detail).getByRole('button', { name: 'Ceva · DPDFNet · model source' }));
    expect(openLicense).toHaveBeenCalledWith('dpdfnet8_48khz_hr');
    expect(openSource).toHaveBeenCalledWith('dpdfnet8_48khz_hr');
  });

  it('shows source link, license link, and commercial-use note for interpolation models', async () => {
    const user = userEvent.setup();
    const { openLicense, openSource } = renderLibrary();

    await user.click(screen.getByRole('button', { name: /^Video \(Labs\)/ }));
    await user.click(screen.getByRole('button', { name: /^RIFE 4\.26 — Labs/ }));

    const detail = screen.getByLabelText('Model details');
    expect(within(detail).getByRole('heading', { name: 'RIFE 4.26 — Labs' })).toBeTruthy();
    expect(within(detail).getByText('flownet_v4.26.pkl')).toBeTruthy();
    expect(within(detail).getByText('Allowed')).toBeTruthy();

    await user.click(within(detail).getByRole('button', { name: 'MIT ↗' }));
    await user.click(
      within(detail).getByRole('button', { name: 'hzwer · Practical-RIFE · model source' }),
    );
    expect(openLicense).toHaveBeenCalledWith('rife_4_26');
    expect(openSource).toHaveBeenCalledWith('rife_4_26');
  });

  it('shows source link, license link, and attribution note for Edit-with-a-prompt models', async () => {
    const user = userEvent.setup();
    const { openLicense, openSource } = renderLibrary((snapshot) => {
      snapshot.capabilities.devices = [
        {
          id: 'mps',
          type: 'gpu',
          name: 'Apple M1 Pro',
          total_memory: 16 * 1024 ** 3,
          free_memory: 12 * 1024 ** 3,
          supports_fp16: true,
          is_integrated: true,
          recommended_tile_sizes: [256],
          neural_engine: true,
        },
      ];
      snapshot.capabilities.system_ram_total = 16 * 1024 ** 3;
      snapshot.settings.device_id = 'mps';
    });

    await user.click(screen.getByRole('button', { name: /^Edit with a prompt/ }));
    await user.click(screen.getByRole('button', { name: /^Balanced\s*Q4_0/ }));

    const detail = screen.getByLabelText('Model details');
    expect(within(detail).getByRole('heading', { name: 'FLUX.2 klein 4B · Q4_0' })).toBeTruthy();
    expect(within(detail).getByText('Allowed · attribution kept')).toBeTruthy();

    await user.click(within(detail).getByRole('button', { name: 'Apache-2.0 ↗' }));
    await user.click(
      within(detail).getByRole('button', { name: 'Black Forest Labs · model source' }),
    );
    expect(openLicense).toHaveBeenCalledWith('flux2_klein_4b_q4_0');
    expect(openSource).toHaveBeenCalledWith('flux2_klein_4b_q4_0');
  });

  it('shows filename and "Your own file" without invented license for user custom files', async () => {
    const user = userEvent.setup();
    const { openLicense, openSource } = renderLibrary((snapshot) => {
      snapshot.settings.custom_models = [
        { path: '/checkpoints/custom_portrait_x4.safetensors', name: 'Portrait Mix' },
      ];
    });

    await user.click(screen.getByRole('button', { name: /^Your own file/ }));

    const detail = screen.getByLabelText('Model details');
    expect(within(detail).getByRole('heading', { name: 'Portrait Mix' })).toBeTruthy();
    expect(within(detail).getByText('custom_portrait_x4.safetensors')).toBeTruthy();
    expect(within(detail).getAllByText('Your own file').length).toBeGreaterThanOrEqual(2);
    expect(within(detail).queryByRole('button', { name: /↗/ })).toBeNull();
    expect(openLicense).not.toHaveBeenCalled();
    expect(openSource).not.toHaveBeenCalled();
  });
});

describe('Examples', () => {
  const dir = join(process.cwd(), 'src/assets/examples');

  it('has every file, stays under 1,000,000 bytes, and names catalog models', () => {
    const snapshot = demoSnapshot();
    let total = 0;
    for (const example of EXAMPLES) {
      for (const file of [example.before, example.after]) {
        total += statSync(join(dir, file)).size;
      }
      expect(snapshot.catalog.models.some((m) => m.model_id === example.model_id)).toBe(true);
    }
    const files = new Set(EXAMPLES.flatMap((example) => [example.before, example.after]));
    expect(files.size).toBe(14);
    expect(total).toBeLessThanOrEqual(1_000_000);
    for (const fix of ['noise', 'jpeg', 'blur', 'colour'] as const) {
      expect(EXAMPLES.filter((example) => example.fix === fix)).toHaveLength(1);
    }
  });

  it("shows the model's own example with its caption, and Before swaps the file", async () => {
    const user = userEvent.setup();
    renderLibrary(undefined, { select: 'denoise_realplksr_1x' });
    const detail = screen.getByLabelText('Model details');
    const picture = within(detail).getByRole('img', { name: /noisy photo/ });
    const after = picture.getAttribute('src');
    expect(within(detail).getByText('Example · Noise')).toBeTruthy();
    const group = within(detail).getByRole('radiogroup', { name: 'Example picture' });
    expect(within(group).getByRole('radio', { name: 'After' }).getAttribute('aria-checked')).toBe(
      'true',
    );
    await user.click(within(group).getByRole('radio', { name: 'Before' }));
    expect(
      within(detail)
        .getByRole('img', { name: /noisy photo/ })
        .getAttribute('src'),
    ).not.toBe(after);
  });

  it("shows the fix's example with the second caption for another fix model", () => {
    renderLibrary(undefined, { select: 'nafnet_sidd_width64' });
    const detail = screen.getByLabelText('Model details');
    expect(
      within(detail).getByText('Example of the Noise fix, made with RealPLKSR Denoise'),
    ).toBeTruthy();
  });

  it('shows no picture for an upscaler without one', () => {
    renderLibrary(undefined, { select: 'swinir_m_real_x4_gan' });
    expect(within(screen.getByLabelText('Model details')).queryByRole('img')).toBeNull();
  });

  it('lets a fifth fact span both columns', () => {
    const css = readFileSync(join(process.cwd(), 'src/styles.css'), 'utf8');
    expect(css).toMatch(
      /\.facts > div:last-child:nth-child\(odd\)\s*\{\s*grid-column: 1 \/ -1;\s*\}/,
    );
  });
});
