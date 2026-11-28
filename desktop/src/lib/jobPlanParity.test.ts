import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import type { HostPlanStage } from './api';
import { demoSnapshot } from './demo';
import { toHostStage } from './jobPlanClient';
import { withListedDevice } from './neuralEngine';
import { resolvePlan } from './plan';
import { buildStartInput } from './startInput';
import { STEPS, type StepDefinition } from './steps';
import type {
  CapabilityInfo,
  CatalogModel,
  FixKind,
  FixStageSelection,
  InterpolationOrder,
  InterpolationTarget,
  StartBatchInput,
  TaskKind,
  UiSettings,
} from './types';

type FixKindNoFace = Exclude<FixKind, 'faces'>;

interface ParityCase {
  id: string;
  installed_ids: string[];
  /** Steps and catalog models that exist only in this case. */
  extra_steps?: StepDefinition[];
  extra_models?: CatalogModel[];
  capabilities: CapabilityInfo;
  settings: UiSettings;
  options: {
    face_model_id: string | null;
    face_enabled: boolean;
    using_temporal_video: boolean;
    selected_video_model_id: string | null;
    edit_model_id: string | null;
    has_audio: boolean | null;
    /** Left out of a case that names none. */
    masked_steps?: string[];
    /** The engine's features; left out of a case that names none. */
    features?: string[];
  };
  start_request: {
    media_ids: string[];
    task: TaskKind | '';
    batch_mode: boolean;
    recipe_name: string;
  };
  expected_plan: HostPlanStage[];
  expected_start_input: StartBatchInput;
}

const FIXTURE_PATH = path.resolve(__dirname, '../../src-tauri/tests/fixtures/job_plan_cases.json');

const ALL_FIXES: FixKindNoFace[] = ['noise', 'jpeg', 'blur', 'colour'];

const DEFAULT_FIX_MODELS: Record<FixKindNoFace, string> = {
  noise: 'denoise_realplksr_1x',
  jpeg: 'fbcnn_color',
  blur: 'nafnet_gopro_deblur',
  colour: 'ddcolor_modelscope',
};

const ALT_FIX_MODELS: Record<FixKindNoFace, string> = {
  noise: 'scunet_color_real_psnr',
  jpeg: 'fbcnn_color',
  blur: 'nafnet_gopro_deblur',
  colour: 'ddcolor_modelscope',
};

function makeCapabilities(deviceIds: string[]): CapabilityInfo {
  const base = demoSnapshot().capabilities;
  return {
    ...base,
    devices: deviceIds.map((id) => ({
      id,
      type: id === 'cpu' ? 'cpu' : id.startsWith('cuda') ? 'cuda' : 'mps',
      name: id.toUpperCase(),
      total_memory: 16 * 1024 ** 3,
      free_memory: 12 * 1024 ** 3,
      supports_fp16: id !== 'cpu',
      is_integrated: id === 'mps',
      recommended_tile_sizes: [64, 128, 256],
      neural_engine: id === 'mps',
    })),
  };
}

function makeCase(
  id: string,
  spec: {
    settingsPatch: Partial<UiSettings>;
    installedIds?: string[];
    deviceIds?: string[];
    faceModelId?: string | null;
    faceEnabled?: boolean;
    usingTemporalVideo?: boolean;
    selectedVideoModelId?: string | null;
    editModelId?: string | null;
    hasAudio?: boolean | null;
    maskedSteps?: string[];
    features?: string[];
    mediaIds?: string[];
    batchMode?: boolean;
    recipeName?: string;
    extraSteps?: StepDefinition[];
    extraModels?: CatalogModel[];
  },
): ParityCase {
  const shared = demoSnapshot();
  const extraModels = spec.extraModels?.map((model) => structuredClone(model));
  const snapshot = extraModels
    ? {
        ...shared,
        catalog: { ...shared.catalog, models: [...shared.catalog.models, ...extraModels] },
      }
    : shared;
  const steps = spec.extraSteps ? [...STEPS, ...spec.extraSteps] : undefined;
  const installedIds = [...new Set(spec.installedIds ?? [])].sort();
  const installedSet = new Set(installedIds);

  for (const model of snapshot.catalog.models) {
    model.installed = installedSet.has(model.model_id);
  }
  for (const model of [
    ...snapshot.catalog.video_models,
    ...snapshot.catalog.interpolation_models,
    ...(snapshot.catalog.audio_models ?? []),
  ]) {
    model.installed = installedSet.has(model.model_id);
  }
  for (const model of snapshot.edit.models) {
    model.installed = installedSet.has(model.model_id);
  }

  const capabilities = makeCapabilities(spec.deviceIds ?? ['cpu', 'mps']);
  const settings: UiSettings = {
    ...snapshot.settings,
    ...spec.settingsPatch,
  };
  const effectiveSettings = withListedDevice(settings, capabilities);

  const faceModelId = spec.faceModelId ?? null;
  const faceEnabled = spec.faceEnabled ?? false;
  const usingTemporalVideo = spec.usingTemporalVideo ?? false;
  const selectedVideoModelId = spec.selectedVideoModelId ?? null;
  const editModelId = spec.editModelId ?? null;
  const hasAudio = spec.hasAudio === undefined ? null : spec.hasAudio;
  const maskedSteps = spec.maskedSteps ?? [];
  const features = spec.features ?? [];

  const faceModel = faceModelId
    ? snapshot.catalog.models.find((m) => m.model_id === faceModelId)
    : undefined;
  const selectedVideoModel = selectedVideoModelId
    ? snapshot.catalog.video_models.find((m) => m.model_id === selectedVideoModelId)
    : undefined;
  const editModel = editModelId
    ? snapshot.edit.models.find((m) => m.model_id === editModelId)
    : undefined;

  const plan = resolvePlan(snapshot, effectiveSettings, {
    faceModel,
    faceEnabled,
    usingTemporalVideo,
    selectedVideoModel,
    editModel,
    hasAudio: hasAudio === null ? undefined : hasAudio,
    maskedSteps,
    features,
    steps,
  });

  const mediaIds = spec.mediaIds ?? ['media-1'];
  const batchMode = spec.batchMode ?? false;
  const recipeName = spec.recipeName ?? '';
  const startInput = buildStartInput(settings, {
    mediaIds,
    task: effectiveSettings.task,
    batchMode,
    hasAudio: hasAudio === null ? true : hasAudio,
    recipeName,
    capabilities,
    steps,
  });

  return {
    id,
    installed_ids: installedIds,
    ...(spec.extraSteps ? { extra_steps: spec.extraSteps } : {}),
    ...(extraModels ? { extra_models: extraModels } : {}),
    capabilities,
    settings,
    options: {
      face_model_id: faceModelId,
      face_enabled: faceEnabled,
      using_temporal_video: usingTemporalVideo,
      selected_video_model_id: selectedVideoModelId,
      edit_model_id: editModelId,
      has_audio: hasAudio,
      ...(maskedSteps.length > 0 ? { masked_steps: maskedSteps } : {}),
      ...(features.length > 0 ? { features } : {}),
    },
    start_request: {
      media_ids: mediaIds,
      task: settings.task,
      batch_mode: batchMode,
      recipe_name: recipeName,
    },
    expected_plan: plan.map(toHostStage),
    expected_start_input: startInput,
  };
}

function buildCorpus(): ParityCase[] {
  const cases: ParityCase[] = [];
  let index = 0;
  const push = (tag: string, spec: Parameters<typeof makeCase>[1]) => {
    index += 1;
    cases.push(makeCase(`case-${String(index).padStart(3, '0')}-${tag}`, spec));
  };

  // 1. Image upscale: qualities x contents x single fixes x face x installed (64 cases)
  for (const quality of ['quick', 'best'] as const) {
    for (const content of ['photo', 'illustration'] as const) {
      const selectedModelId =
        content === 'photo'
          ? quality === 'quick'
            ? 'span_photo_x4'
            : 'realplksr_nomoswebphoto_x4'
          : quality === 'quick'
            ? 'realesrgan_x4plus_anime_6b'
            : 'esrgan_hfa2k_x4';
      for (const fix of ALL_FIXES) {
        for (const faceEnabled of [false, true]) {
          const fixModelId = DEFAULT_FIX_MODELS[fix];
          const installedIds = faceEnabled ? [selectedModelId, 'hat_l_x4_face'] : [fixModelId];
          push(`upscale-${quality}-${content}-${fix}-face_${faceEnabled}`, {
            settingsPatch: {
              task: 'upscale',
              quality,
              content,
              output_scale: quality === 'quick' ? 2 : 4,
              selected_model_id: selectedModelId,
              fixes: faceEnabled ? [fix, 'faces'] : [fix],
              fix_stages: [{ fix, model_id: fixModelId }],
              enable_face_model: faceEnabled,
            },
            installedIds,
            faceEnabled,
            faceModelId: faceEnabled ? 'hat_l_x4_face' : 'hat_s_x4_face',
          });
        }
      }
    }
  }

  // 2. All ordered pairs of fix kinds across upscale & denoise, with pins & missing models (72 cases)
  const fixPairs: [FixKindNoFace, FixKindNoFace][] = [];
  for (const first of ALL_FIXES) {
    for (const second of ALL_FIXES) {
      if (first !== second) fixPairs.push([first, second]);
    }
  }
  for (const [first, second] of fixPairs) {
    for (const task of ['upscale', 'denoise'] as const) {
      for (const pinFirst of [false, true]) {
        const firstModel = ALT_FIX_MODELS[first];
        const secondModel = DEFAULT_FIX_MODELS[second];
        const primaryId = task === 'denoise' ? secondModel : 'span_photo_x4';
        const presetPins: Record<string, string> = pinFirst
          ? { [`fix/${first}/best`]: firstModel }
          : {};
        push(`pair-${task}-${first}-${second}-pin_${pinFirst}`, {
          settingsPatch: {
            task,
            quality: 'best',
            selected_model_id: primaryId,
            output_scale: task === 'denoise' ? 1 : 4,
            fixes: [first, second],
            fix_stages: [
              { fix: first, model_id: firstModel },
              { fix: second, model_id: secondModel },
            ],
            preset_pins: presetPins,
          },
          installedIds: pinFirst ? [primaryId, firstModel] : [secondModel],
          faceEnabled: true,
          faceModelId: 'hat_l_x4_face',
        });
      }
    }
  }
  // Additional 12 cases for all pairs with quick quality + one missing model on second fix
  for (const [first, second] of fixPairs) {
    push(`pair-missing-${first}-${second}`, {
      settingsPatch: {
        task: 'upscale',
        quality: 'quick',
        selected_model_id: 'localsr_quick_x4_v1',
        fixes: [first, second],
        fix_stages: [
          { fix: first, model_id: DEFAULT_FIX_MODELS[first] },
          { fix: second, model_id: 'missing_model_id' },
        ],
      },
      installedIds: [DEFAULT_FIX_MODELS[first]],
    });
  }

  // 3. Restore task ('denoise') variations: single fixes, 3 fixes, 4 fixes, custom, missing, no-fix (32 cases)
  for (const quality of ['quick', 'best', 'custom', ''] as const) {
    for (const fix of ALL_FIXES) {
      const modelId = ALT_FIX_MODELS[fix];
      push(`restore-${quality || 'empty'}-${fix}`, {
        settingsPatch: {
          task: 'denoise',
          quality,
          output_scale: 1,
          selected_model_id: modelId,
          fixes: [fix],
          fix_stages: [{ fix, model_id: modelId }],
          preset_pins: { [`fix/${fix}/best`]: modelId },
        },
        installedIds: quality === 'quick' ? [modelId] : [],
      });
    }
  }
  for (const customPath of ['', '/models/custom_restore.safetensors']) {
    for (const installed of [false, true]) {
      push(`restore-custom-${customPath ? 'set' : 'empty'}-${installed}`, {
        settingsPatch: {
          task: 'denoise',
          quality: 'custom',
          output_scale: 1,
          selected_model_id: '__custom__',
          custom_model_path: customPath,
          fix_stages: [],
          fixes: [],
        },
        installedIds: installed ? ['span_photo_x4'] : [],
      });
    }
  }
  for (const fixStages of [
    [] as FixStageSelection[],
    [{ fix: 'blur' as const, model_id: 'fbcnn_color' }], // misfit
    [{ fix: 'jpeg' as const, model_id: 'missing' }], // missing
    [
      { fix: 'blur' as const, model_id: 'nafnet_gopro_deblur' },
      { fix: 'jpeg' as const, model_id: 'fbcnn_color' },
      { fix: 'noise' as const, model_id: 'scunet_color_real_psnr' },
    ],
    [
      { fix: 'blur' as const, model_id: 'nafnet_gopro_deblur' },
      { fix: 'jpeg' as const, model_id: 'fbcnn_color' },
      { fix: 'noise' as const, model_id: 'scunet_color_real_psnr' },
      { fix: 'colour' as const, model_id: 'ddcolor_modelscope' },
    ],
  ]) {
    for (const selectedId of ['denoise_realplksr_1x', 'missing_primary']) {
      push(`restore-multi-${fixStages.length}-${selectedId}`, {
        settingsPatch: {
          task: 'denoise',
          output_scale: 1,
          selected_model_id: selectedId,
          fix_stages: fixStages,
          fixes: fixStages.map((s) => s.fix),
        },
        installedIds: ['nafnet_gopro_deblur', 'fbcnn_color'],
      });
    }
  }

  // 4. Upscale custom model, preset pins, face installed/uninstalled, batch/device fallback (36 cases)
  for (const customPath of ['', '/models/my_upscaler.pth']) {
    for (const faceEnabled of [false, true]) {
      for (const faceInstalled of [false, true]) {
        push(
          `upscale-custom-${customPath ? 'set' : 'empty'}-face_${faceEnabled}_${faceInstalled}`,
          {
            settingsPatch: {
              task: 'upscale',
              quality: 'custom',
              selected_model_id: '__custom__',
              custom_model_path: customPath,
              output_scale: 4,
              device_id: 'cuda:0', // unlisted in ['cpu', 'mps'] -> falls back to cpu
            },
            deviceIds: ['cpu', 'mps'],
            installedIds: faceInstalled ? ['hat_l_x4_face'] : [],
            faceEnabled,
            faceModelId: 'hat_l_x4_face',
            batchMode: true,
            mediaIds: ['m1', 'm2'],
            recipeName: 'Custom upscale',
          },
        );
      }
    }
  }
  const pinConfigs: Record<string, string>[] = [
    {},
    { 'upscale/photo/best': 'hat_l_x4_imagenet' },
    { 'fix/noise/best': 'scunet_color_real_psnr' },
    { 'fix/noise/quick': 'scunet_color_real_psnr' },
    { 'fix/noise/best': 'fbcnn_color' }, // invalid pin for noise
    { 'fix/jpeg/best': 'fbcnn_color', 'fix/blur/quick': 'nafnet_gopro_deblur' },
  ];
  for (const pins of pinConfigs) {
    for (const quality of ['quick', 'best'] as const) {
      for (const installed of [false, true]) {
        push(`pins-${quality}-${Object.keys(pins).length}-${installed}`, {
          settingsPatch: {
            task: 'upscale',
            quality,
            selected_model_id: 'realplksr_nomoswebphoto_x4',
            fix_stages: [
              { fix: 'noise', model_id: 'scunet_color_real_psnr' },
              { fix: 'jpeg', model_id: 'fbcnn_color' },
            ],
            fixes: ['noise', 'jpeg'],
            preset_pins: pins,
            device_id: 'mps',
          },
          deviceIds: ['mps'], // no cpu, mps listed
          installedIds: installed ? ['realplksr_nomoswebphoto_x4', 'scunet_color_real_psnr'] : [],
        });
      }
    }
  }

  // 5. Video frame-by-frame upscale: interpolation x order x clean audio x hasAudio x fixes (96 cases)
  const interpTargets: InterpolationTarget[] = ['off', 'x2', 'fps60'];
  const interpOrders: InterpolationOrder[] = ['after_upscale', 'before_upscale'];
  for (const target of interpTargets) {
    for (const order of interpOrders) {
      for (const cleanAudio of [false, true]) {
        for (const hasAudio of [true, false, null] as const) {
          for (const audioModel of ['', 'dpdfnet8_48khz_hr']) {
            for (const withFix of [false, true]) {
              if (cases.length >= 340 && target === 'off' && order === 'before_upscale') continue;
              const installedIds = withFix
                ? ['span_photo_x4', 'rife_4_26', 'deepfilternet3']
                : ['dpdfnet8_48khz_hr'];
              push(
                `video-fbf-${target}-${order}-aud_${cleanAudio}_${hasAudio}-${audioModel || 'def'}-fix_${withFix}`,
                {
                  settingsPatch: {
                    task: 'video',
                    video_task: 'upscale',
                    selected_video_model_id: 'frame_by_frame',
                    selected_model_id: 'span_photo_x4',
                    output_scale: 4,
                    deflicker: withFix,
                    video_hdr_mode: withFix ? 'tone_map' : 'preserve',
                    video_interpolation: target,
                    video_interpolation_order: order,
                    video_clean_audio: cleanAudio,
                    video_audio_model: audioModel,
                    fix_stages: withFix ? [{ fix: 'blur', model_id: 'nafnet_gopro_deblur' }] : [],
                    fixes: withFix ? ['blur'] : [],
                  },
                  installedIds,
                  hasAudio,
                  batchMode: withFix,
                  mediaIds: withFix ? ['v1', 'v2'] : ['v1'],
                },
              );
            }
          }
        }
      }
    }
  }

  // Additional interpolation targets x4 and fps120 + unknown audio model
  for (const target of ['x4', 'fps120'] as const) {
    for (const order of interpOrders) {
      push(`video-interp-${target}-${order}`, {
        settingsPatch: {
          task: 'video',
          video_task: 'upscale',
          selected_video_model_id: 'frame_by_frame',
          selected_model_id: 'span_photo_x4',
          video_interpolation: target,
          video_interpolation_order: order,
          video_clean_audio: true,
          video_audio_model: 'unknown_id',
        },
        installedIds: ['span_photo_x4'],
        hasAudio: true,
      });
    }
  }

  // Slow motion counts for x2 and x4 only, and leaves the Clean audio stage out with it
  for (const target of ['x2', 'x4', 'fps60'] as const) {
    for (const hasAudio of [true, null] as const) {
      push(`video-slow-${target}-aud_${hasAudio}`, {
        settingsPatch: {
          task: 'video',
          video_task: 'upscale',
          selected_video_model_id: 'frame_by_frame',
          selected_model_id: 'span_photo_x4',
          output_scale: 2,
          video_interpolation: target,
          video_interpolation_order: 'after_upscale',
          video_slow_motion: true,
          video_clean_audio: true,
        },
        installedIds: ['span_photo_x4', 'rife_4_26', 'deepfilternet3'],
        hasAudio,
      });
    }
  }

  // 6. Video temporal (SeedVR2 / FlashVSR): target_resolution, fixes ignored, interp ignored, audio kept (24 cases)
  for (const videoModelId of ['flashvsr_v11', 'seedvr2_3b']) {
    for (const targetRes of [0, 720]) {
      for (const cleanAudio of [false, true]) {
        for (const hasAudio of [true, false]) {
          for (const installed of [false, true]) {
            if (videoModelId === 'seedvr2_3b' && targetRes === 720 && cleanAudio && !installed) {
              continue;
            }
            push(
              `video-temporal-${videoModelId}-res_${targetRes}-aud_${cleanAudio}_${hasAudio}-${installed}`,
              {
                settingsPatch: {
                  task: 'video',
                  video_task: 'upscale',
                  selected_video_model_id: videoModelId,
                  selected_model_id: 'span_photo_x4',
                  output_scale: 4,
                  video_target_resolution: targetRes,
                  deflicker: true,
                  video_hdr_mode: 'preserve',
                  video_interpolation: 'fps60',
                  video_interpolation_order: 'before_upscale',
                  video_clean_audio: cleanAudio,
                  fix_stages: [{ fix: 'jpeg', model_id: 'fbcnn_color' }],
                  fixes: ['jpeg'],
                },
                installedIds: installed ? [videoModelId, 'deepfilternet3'] : [],
                usingTemporalVideo: true,
                selectedVideoModelId: videoModelId,
                hasAudio,
                faceEnabled: true,
                faceModelId: 'hat_l_x4_face',
              },
            );
          }
        }
      }
    }
  }

  // 7. Video restore ('video' + video_task: 'denoise'): with fixes, without fixes, custom, interp, audio (24 cases)
  for (const fixMode of ['none', 'single', 'pair', 'custom', 'missing'] as const) {
    for (const cleanAudio of [false, true]) {
      for (const target of ['off', 'x2'] as const) {
        const fixStages: FixStageSelection[] =
          fixMode === 'none' || fixMode === 'custom'
            ? []
            : fixMode === 'single'
              ? [{ fix: 'colour', model_id: 'ddcolor_modelscope' }]
              : fixMode === 'missing'
                ? [{ fix: 'noise', model_id: 'missing' }]
                : [
                    { fix: 'jpeg', model_id: 'fbcnn_color' },
                    { fix: 'noise', model_id: 'scunet_color_real_psnr' },
                  ];
        push(`video-restore-${fixMode}-aud_${cleanAudio}-${target}`, {
          settingsPatch: {
            task: 'video',
            video_task: 'denoise',
            selected_video_model_id: 'flashvsr_v11', // restoring overrides temporal to false
            selected_model_id:
              fixMode === 'custom'
                ? '__custom__'
                : fixMode === 'single'
                  ? 'ddcolor_modelscope'
                  : 'scunet_color_real_psnr',
            custom_model_path: fixMode === 'custom' ? '/models/vid_fix.safetensors' : '',
            output_scale: 1,
            deflicker: true,
            video_interpolation: target,
            video_clean_audio: cleanAudio,
            fix_stages: fixStages,
            fixes: fixStages.map((s) => s.fix),
          },
          installedIds: ['ddcolor_modelscope', 'fbcnn_color'],
          usingTemporalVideo: false,
          hasAudio: true,
        });
      }
    }
  }

  // 8. Edit task & empty task (16 cases)
  for (const editModelId of ['qwen_edit_2511_q4_k_m', 'flux2_klein_4b_q4_0', null]) {
    for (const installed of [false, true]) {
      if (editModelId === null && installed) continue;
      push(`edit-${editModelId ?? 'none'}-inst_${installed}`, {
        settingsPatch: {
          task: 'edit',
          edit_model_id: editModelId ?? '',
          fix_stages: [{ fix: 'noise', model_id: 'scunet_color_real_psnr' }],
          fixes: ['noise'],
          video_clean_audio: true,
          video_interpolation: 'x2',
        },
        installedIds: installed && editModelId ? [editModelId] : [],
        editModelId,
        faceEnabled: true,
        faceModelId: 'hat_l_x4_face',
      });
    }
  }
  push('empty-task', {
    settingsPatch: {
      task: '',
      selected_model_id: '',
      fix_stages: [{ fix: 'noise', model_id: 'scunet_color_real_psnr' }],
    },
    installedIds: [],
  });

  // 9. Extra colour/purpose fallback and video-only edge cases
  push('upscale-colorize-restore-fallback', {
    settingsPatch: {
      task: 'denoise',
      selected_model_id: 'ddcolor_modelscope',
      fix_stages: [],
      fixes: [],
    },
    installedIds: ['ddcolor_modelscope'],
  });

  // 10. Step stages added by id: photos run them, video, temporal models and Edit leave them out
  const grain = { step: 'grain', params: { strength: 40, seed: 7 } };
  const maskedGrain = { ...grain, mask_path: '/in/mask.png' };
  const background = { step: 'remove_background', model_id: 'birefnet_general' };
  const stepCases: [
    string,
    Partial<UiSettings>,
    (Omit<Parameters<typeof makeCase>[1], 'settingsPatch'> & { installedExtra?: string[] })?,
  ][] = [
    ['upscale', { task: 'upscale', step_stages: [grain] }],
    [
      'upscale-fix-face-mask',
      {
        task: 'upscale',
        fix_stages: [{ fix: 'noise', model_id: 'denoise_realplksr_1x' }],
        fixes: ['noise', 'faces'],
        step_stages: [maskedGrain],
      },
      { faceEnabled: true, faceModelId: 'hat_l_x4_face' },
    ],
    [
      'upscale-unknown-step',
      { task: 'upscale', step_stages: [{ step: 'tool_from_a_newer_version' }, grain] },
    ],
    [
      'restore',
      {
        task: 'denoise',
        selected_model_id: 'denoise_realplksr_1x',
        output_scale: 1,
        fix_stages: [{ fix: 'noise', model_id: 'denoise_realplksr_1x' }],
        fixes: ['noise'],
        step_stages: [grain],
      },
    ],
    [
      'video-frame-by-frame',
      { task: 'video', selected_video_model_id: 'frame_by_frame', step_stages: [grain] },
    ],
    [
      'video-temporal',
      { task: 'video', selected_video_model_id: 'seedvr2_3b', step_stages: [grain] },
      { usingTemporalVideo: true, selectedVideoModelId: 'seedvr2_3b' },
    ],
    ['edit', { task: 'edit', step_stages: [grain] }, { editModelId: null }],
    ['background-missing', { task: 'upscale', output_format: 'jpg', step_stages: [background] }],
    [
      'background-installed-tif',
      { task: 'upscale', output_format: 'tif', step_stages: [background, grain] },
      { installedExtra: ['birefnet_general'] },
    ],
    [
      'background-restore-alone',
      {
        task: 'denoise',
        selected_model_id: '',
        output_scale: 1,
        fix_stages: [],
        fixes: [],
        step_stages: [background],
      },
      { installedExtra: ['birefnet_general'] },
    ],
    [
      'background-and-deinterlace-on-a-photo',
      { task: 'upscale', step_stages: [{ step: 'deinterlace', params: {} }, background] },
    ],
    [
      'deinterlace-video-before-interpolation',
      {
        task: 'video',
        selected_video_model_id: 'frame_by_frame',
        video_interpolation: 'x2',
        video_interpolation_order: 'before_upscale',
        step_stages: [{ step: 'deinterlace', params: { mode: 'field', parity: 'auto' } }],
      },
    ],
    [
      'deinterlace-and-background-stored-on-a-photo',
      {
        task: 'upscale',
        step_stages: [
          { step: 'deinterlace', params: { mode: 'field', parity: 'auto' } },
          background,
        ],
      },
    ],
    [
      'stabilise-video-alone',
      {
        task: 'video',
        selected_video_model_id: 'frame_by_frame',
        output_scale: 1,
        step_stages: [{ step: 'stabilise', params: { smoothing: 'medium' } }],
      },
    ],
    [
      'deinterlace-and-stabilise-video',
      {
        task: 'video',
        selected_video_model_id: 'frame_by_frame',
        step_stages: [
          { step: 'stabilise', params: { smoothing: 'strong' } },
          { step: 'deinterlace', params: { mode: 'frame', parity: 'tff' } },
        ],
      },
    ],
    [
      'stabilise-stored-on-a-photo',
      { task: 'upscale', step_stages: [{ step: 'stabilise', params: { smoothing: 'light' } }] },
    ],
    [
      'background-video',
      { task: 'video', selected_video_model_id: 'frame_by_frame', step_stages: [background] },
    ],
  ];
  for (const [tag, patch, { installedExtra = [], ...spec } = {}] of stepCases) {
    push(`step-stages-${tag}`, {
      ...spec,
      settingsPatch: { selected_model_id: 'span_photo_x4', output_scale: 4, ...patch },
      installedIds: ['span_photo_x4', 'denoise_realplksr_1x', ...installedExtra],
    });
  }

  // 11. Steps that exist only in these cases: with a catalog model, and setting the alpha
  const base = STEPS.find((step) => step.id === 'grain')!;
  const testStep = (overrides: Partial<StepDefinition>): StepDefinition => ({
    ...base,
    supports_mask: false,
    stage_label: '',
    required_feature: null,
    dev_only: false,
    parameters: [],
    ...overrides,
  });
  const extraSteps = [
    testStep({
      id: 'test_cutout',
      phase: 'cleanup',
      needs_model: true,
      label: 'Test cut-out',
      model_task: 'test_cutout',
      output_alpha: true,
    }),
    testStep({
      id: 'test_tone',
      phase: 'grade',
      needs_model: true,
      label: 'Test tone',
      model_task: 'test_tone',
    }),
    testStep({ id: 'test_key', phase: 'geometry', label: 'Test key', output_alpha: true }),
  ];
  const shipped = demoSnapshot().catalog.models.find(
    (model) => model.model_id === 'denoise_realplksr_1x',
  )!;
  const taskModel = (modelId: string, task: string, downloadable: boolean): CatalogModel => ({
    ...structuredClone(shipped),
    model_id: modelId,
    name: `Test ${modelId}`,
    display_name: `Test ${modelId}`,
    filename: `${modelId}.safetensors`,
    purposes: [task],
    fixes: [],
    size_bytes: 4321,
    automated_download_allowed: downloadable,
  });
  const extraModels = [
    taskModel('test_cutout_ready', 'test_cutout', true),
    taskModel('test_cutout_download', 'test_cutout', true),
    taskModel('test_cutout_manual', 'test_cutout', false),
    taskModel('test_tone_model', 'test_tone', true),
  ];
  const cutout = (modelId?: string) => ({
    step: 'test_cutout',
    ...(modelId === undefined ? {} : { model_id: modelId }),
  });
  const tone = { step: 'test_tone', model_id: 'test_tone_model' };
  const modelStepCases: [
    string,
    Partial<UiSettings>,
    Omit<Parameters<typeof makeCase>[1], 'settingsPatch'>?,
  ][] = [
    ['ready-jpg', { output_format: 'jpg', step_stages: [cutout('test_cutout_ready')] }],
    ['ready-heic', { output_format: 'heic', step_stages: [cutout('test_cutout_ready')] }],
    ['ready-tif', { output_format: 'tif', step_stages: [cutout('test_cutout_ready')] }],
    ['ready-webp', { output_format: 'webp', step_stages: [cutout('test_cutout_ready')] }],
    ['download', { output_format: 'png', step_stages: [cutout('test_cutout_download')] }],
    ['manual', { output_format: 'png', step_stages: [cutout('test_cutout_manual')] }],
    ['unchosen', { output_format: 'jpg', step_stages: [cutout()] }],
    ['unknown-model', { step_stages: [cutout('not_in_this_catalog')] }],
    ['other-task-model', { step_stages: [cutout('denoise_realplksr_1x')] }],
    [
      'key-face-tone',
      {
        output_format: 'jpg',
        fixes: ['faces'],
        step_stages: [{ step: 'test_key' }, tone],
      },
      { faceEnabled: true, faceModelId: 'hat_l_x4_face' },
    ],
    ['tone-only-no-alpha', { output_format: 'jpg', step_stages: [tone] }],
    [
      'restore-step-alone',
      {
        task: 'denoise',
        selected_model_id: '',
        output_scale: 1,
        output_format: 'jpg',
        fix_stages: [],
        fixes: [],
        step_stages: [cutout('test_cutout_ready')],
      },
    ],
    [
      'restore-fix-then-tone',
      {
        task: 'denoise',
        selected_model_id: 'denoise_realplksr_1x',
        output_scale: 1,
        fix_stages: [{ fix: 'noise', model_id: 'denoise_realplksr_1x' }],
        fixes: ['noise'],
        step_stages: [cutout('test_cutout_download'), tone],
      },
    ],
    [
      'video-frame-by-frame',
      {
        task: 'video',
        selected_video_model_id: 'frame_by_frame',
        step_stages: [cutout('test_cutout_ready')],
      },
    ],
  ];
  for (const [tag, patch, spec] of modelStepCases) {
    push(`model-steps-${tag}`, {
      ...spec,
      settingsPatch: {
        task: 'upscale',
        selected_model_id: 'span_photo_x4',
        output_scale: 4,
        ...patch,
      },
      installedIds: ['span_photo_x4', 'denoise_realplksr_1x', 'test_cutout_ready'],
      extraSteps,
      extraModels,
    });
  }

  // 9. A fix's strength changes neither the plan nor the model: each case has a twin without it.
  for (const task of ['upscale', 'denoise'] as const) {
    for (const strength of [undefined, 0.4]) {
      push(`strength-${task}-${strength ?? 'full'}`, {
        settingsPatch: {
          task,
          quality: 'best',
          selected_model_id: task === 'denoise' ? 'denoise_realplksr_1x' : 'span_photo_x4',
          output_scale: task === 'denoise' ? 1 : 4,
          fixes: ['noise'],
          fix_stages: [
            {
              fix: 'noise',
              model_id: 'denoise_realplksr_1x',
              ...(strength === undefined ? {} : { params: { strength } }),
            },
          ],
        },
        installedIds: ['span_photo_x4', 'denoise_realplksr_1x'],
      });
    }
  }

  // 12. Dust & scratches runs for a photo that has paint: the plan lists the step only then,
  // ahead of the fixes and the background; a start always carries it, and the host checks each photo.
  const repair = { step: 'repair' };
  const repairCases: [string, Partial<UiSettings>, string[]][] = [
    ['upscale-painted', { task: 'upscale', step_stages: [repair] }, ['repair']],
    ['upscale-unpainted', { task: 'upscale', step_stages: [repair] }, []],
    [
      'upscale-with-fix-and-background',
      {
        task: 'upscale',
        fix_stages: [{ fix: 'noise', model_id: 'denoise_realplksr_1x' }],
        fixes: ['noise'],
        step_stages: [{ step: 'remove_background', model_id: 'birefnet_general' }, repair],
      },
      ['repair'],
    ],
    [
      'restore-alone-painted',
      {
        task: 'denoise',
        selected_model_id: '',
        output_scale: 1,
        fix_stages: [],
        fixes: [],
        step_stages: [repair],
      },
      ['repair'],
    ],
    [
      'restore-alone-unpainted',
      {
        task: 'denoise',
        selected_model_id: '',
        output_scale: 1,
        fix_stages: [],
        fixes: [],
        step_stages: [repair],
      },
      [],
    ],
    [
      'video',
      { task: 'video', selected_video_model_id: 'frame_by_frame', step_stages: [repair] },
      ['repair'],
    ],
  ];
  for (const [tag, patch, maskedSteps] of repairCases) {
    push(`repair-${tag}`, {
      maskedSteps,
      settingsPatch: { selected_model_id: 'span_photo_x4', output_scale: 4, ...patch },
      installedIds: ['span_photo_x4', 'denoise_realplksr_1x', 'birefnet_general'],
    });
  }
  // With the engine's `image_repair_model`, the step names MI-GAN: its download is on the plan
  // while it is missing, and nothing when it is installed or the photo has no paint.
  const withModel = ['image_repair', 'image_repair_model'];
  const modelCases: [string, Partial<UiSettings>, string[], string[]][] = [
    ['painted-missing', { task: 'upscale', step_stages: [repair] }, ['repair'], []],
    ['painted-installed', { task: 'upscale', step_stages: [repair] }, ['repair'], ['migan']],
    ['unpainted-missing', { task: 'upscale', step_stages: [repair] }, [], []],
    [
      'with-fix-and-background',
      {
        task: 'upscale',
        fix_stages: [{ fix: 'noise', model_id: 'denoise_realplksr_1x' }],
        fixes: ['noise'],
        step_stages: [{ step: 'remove_background', model_id: 'birefnet_general' }, repair],
      },
      ['repair'],
      [],
    ],
    [
      'restore-alone-missing',
      {
        task: 'denoise',
        selected_model_id: '',
        output_scale: 1,
        fix_stages: [],
        fixes: [],
        step_stages: [repair],
      },
      ['repair'],
      [],
    ],
  ];
  for (const [tag, patch, maskedSteps, installed] of modelCases) {
    push(`repair-model-${tag}`, {
      maskedSteps,
      features: withModel,
      settingsPatch: { selected_model_id: 'span_photo_x4', output_scale: 4, ...patch },
      installedIds: ['span_photo_x4', 'denoise_realplksr_1x', 'birefnet_general', ...installed],
    });
  }
  // A catalog or engine without the model: the stage is today's.
  push('repair-model-engine-without-feature', {
    maskedSteps: ['repair'],
    features: ['image_repair'],
    settingsPatch: { task: 'upscale', selected_model_id: 'span_photo_x4', step_stages: [repair] },
    installedIds: ['span_photo_x4'],
  });

  return cases;
}

describe('job_plan parity corpus', () => {
  it('covers all required plan and start-input combinations with at least 300 cases', () => {
    const cases = buildCorpus();
    expect(cases.length).toBeGreaterThanOrEqual(300);

    const tasks = new Set(cases.map((c) => c.settings.task));
    expect(tasks).toEqual(new Set(['upscale', 'denoise', 'video', 'edit', '']));

    const kinds = new Set(cases.flatMap((c) => c.expected_plan.map((s) => s.kind)));
    expect(kinds).toEqual(
      new Set([
        'deblock',
        'restore',
        'upscale',
        'face_restore',
        'video',
        'edit',
        'deflicker',
        'interpolate',
        'audio_denoise',
        'grain',
        'remove_background',
        'repair',
        'deinterlace',
        'stabilise',
        'test_cutout',
        'test_tone',
        'test_key',
      ]),
    );
    expect(cases.some((c) => (c.expected_start_input.step_stages ?? []).length > 0)).toBe(true);
    const repairPlans = new Map(
      cases
        .filter((c) => c.id.includes('-repair-'))
        .map((c) => [c.id.replace(/^case-\d+-repair-/, ''), c]),
    );
    const planned = (tag: string) => repairPlans.get(tag)!.expected_plan.map((s) => s.kind);
    expect(planned('upscale-painted')[0]).toBe('repair');
    expect(planned('upscale-unpainted')).not.toContain('repair');
    expect(planned('upscale-with-fix-and-background').slice(0, 3)).toEqual([
      'repair',
      'remove_background',
      'restore',
    ]);
    expect(planned('restore-alone-painted')).toEqual(['repair']);
    expect(planned('restore-alone-unpainted')).toEqual([]);
    expect(planned('video')).not.toContain('repair');
    for (const [tag, c] of repairPlans) {
      const sent = (c.expected_start_input.step_stages ?? []).map((stage) => stage.step);
      expect(sent.includes('repair')).toBe(tag !== 'video');
    }
    expect(
      repairPlans
        .get('upscale-with-fix-and-background')!
        .expected_start_input.step_stages!.map((stage) => stage.step),
    ).toEqual(['repair', 'remove_background']);
    const modelStepRows = cases.flatMap((c) =>
      c.expected_plan.filter((s) => s.role === 'step' && s.kind.startsWith('test_')),
    );
    expect(modelStepRows.some((s) => s.model_id && s.installed)).toBe(true);
    expect(modelStepRows.some((s) => s.can_download)).toBe(true);
    expect(modelStepRows.some((s) => s.needs_file)).toBe(true);
    expect(modelStepRows.some((s) => s.missing)).toBe(true);
    expect(
      cases.some(
        (c) => c.settings.output_format === 'jpg' && c.expected_start_input.output_format === 'png',
      ),
    ).toBe(true);

    expect(cases.some((c) => c.expected_plan.some((s) => s.missing === true))).toBe(true);
    expect(cases.some((c) => c.expected_plan.some((s) => s.remembered === true))).toBe(true);
    expect(cases.some((c) => c.expected_plan.some((s) => s.custom !== undefined))).toBe(true);
    expect(cases.some((c) => c.expected_plan.some((s) => s.needs_file))).toBe(true);
    expect(
      cases.some(
        (c) => c.expected_start_input.device === 'cpu' && c.settings.device_id === 'cuda:0',
      ),
    ).toBe(true);

    for (const task of ['upscale', 'denoise']) {
      const full = cases.find((c) => c.id.endsWith(`strength-${task}-full`));
      const light = cases.find((c) => c.id.endsWith(`strength-${task}-0.4`));
      expect(full && light).toBeTruthy();
      expect(light!.expected_plan).toEqual(full!.expected_plan);
      expect(light!.expected_start_input.fix_stages[0].params).toEqual({ strength: 0.4 });
      expect(full!.expected_start_input.fix_stages[0].params).toBeUndefined();
    }

    const serialized = `{\n  "schema_version": 1,\n  "cases": [\n${cases.map((c) => `    ${JSON.stringify(c)}`).join(',\n')}\n  ]\n}\n`;
    if (process.env.UPDATE_JOB_PLAN_FIXTURE === '1') {
      fs.mkdirSync(path.dirname(FIXTURE_PATH), { recursive: true });
      fs.writeFileSync(FIXTURE_PATH, serialized, 'utf8');
    }

    expect(fs.existsSync(FIXTURE_PATH)).toBe(true);
    const committed = fs.readFileSync(FIXTURE_PATH, 'utf8');
    expect(serialized).toBe(committed);
  });
});
