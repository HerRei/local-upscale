/**
 * Output formats the engine offers. PNG, JPEG, TIFF and WebP, and the MP4 and MKV
 * containers, work everywhere. HEIC images and MOV videos (HEVC or Apple ProRes) are
 * written by macOS's own encoders, so they appear only when the engine announces them.
 */
import type { CapabilityInfo, UiSettings, VideoCodec } from './types';

type ImageFormat = UiSettings['output_format'];
type VideoContainer = UiSettings['video_container'];

const IMAGE_LABELS: Record<ImageFormat, string> = {
  png: 'PNG',
  jpg: 'JPEG',
  tif: 'TIFF',
  webp: 'WebP',
  heic: 'HEIC',
};
const CONTAINER_LABELS: Record<VideoContainer, string> = { mp4: 'MP4', mkv: 'MKV', mov: 'MOV' };
const EVERYWHERE_IMAGE: ImageFormat[] = ['png', 'jpg', 'tif', 'webp'];
const EVERYWHERE_CONTAINERS: VideoContainer[] = ['mp4', 'mkv'];
const MOV_CODECS: VideoCodec[] = ['hevc', 'prores'];

/** `current` stays listed, so a saved choice the engine has not announced yet shows as chosen. */
function withCurrent<T extends string>(values: T[], current?: T): T[] {
  return current && !values.includes(current) ? [...values, current] : values;
}

export function imageFormatChoices(
  capabilities?: CapabilityInfo | null,
  current?: ImageFormat,
): { value: ImageFormat; label: string }[] {
  const announced = (capabilities?.media?.image_output ?? []).filter(
    (name): name is ImageFormat => name === 'heic',
  );
  return withCurrent([...EVERYWHERE_IMAGE, ...announced], current).map((value) => ({
    value,
    label: IMAGE_LABELS[value],
  }));
}

/** The lossy formats show a quality slider; the others have none. */
export function imageQualityName(format: ImageFormat): string | null {
  return format === 'jpg' || format === 'webp' || format === 'heic' ? IMAGE_LABELS[format] : null;
}

/** Whether the system encoders write MOV (HEVC and ProRes), as the engine announced. */
function movAvailable(capabilities?: CapabilityInfo | null): boolean {
  const system = capabilities?.media?.system_codecs;
  return Boolean(system?.containers?.includes('mov') && system.encode?.includes('prores'));
}

export function containerChoices(
  capabilities?: CapabilityInfo | null,
  current?: VideoContainer,
): { value: VideoContainer; label: string }[] {
  const values: VideoContainer[] = movAvailable(capabilities)
    ? [...EVERYWHERE_CONTAINERS, 'mov']
    : EVERYWHERE_CONTAINERS;
  return withCurrent(values, current).map((value) => ({ value, label: CONTAINER_LABELS[value] }));
}

/** The codecs a container holds. MOV: HEVC or ProRes; MP4 and MKV keep the usual choices. */
export function codecsForContainer(container: VideoContainer): VideoCodec[] {
  return container === 'mov' ? MOV_CODECS : ['auto', 'av1', 'vp9', 'ffv1', 'h264', 'hevc'];
}

/**
 * Choosing a container moves the codec to one it holds. Both settings change in one
 * update: the host rejects a pair such as MOV with AV1.
 */
export function containerPatch(container: VideoContainer, codec: VideoCodec): Partial<UiSettings> {
  if (container === 'mov') {
    return MOV_CODECS.includes(codec)
      ? { video_container: container }
      : { video_container: container, video_codec: 'hevc' };
  }
  return codec === 'prores'
    ? { video_container: container, video_codec: 'auto' }
    : { video_container: container };
}

/** FFV1 is lossless and only fits MKV, ProRes only MOV; the others keep the container. */
export function codecPatch(codec: VideoCodec): Partial<UiSettings> {
  if (codec === 'ffv1') return { video_codec: codec, video_container: 'mkv' };
  if (codec === 'prores') return { video_codec: codec, video_container: 'mov' };
  return { video_codec: codec };
}
