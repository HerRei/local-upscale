<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import type { DeviceInfo, VideoMemoryStatus } from './lib/types';

  export let lowMemory = true;
  export let device: DeviceInfo | undefined = undefined;
  export let memory: VideoMemoryStatus | undefined = undefined;
  export let running = false;
  export let outputDimensions = '';
  export let disabled = false;
  const dispatch = createEventDispatcher<{ change: boolean }>();
  const gib = (bytes: number) => `${(bytes / 1024 ** 3).toFixed(2)} GiB`;
  $: shared = device?.id.startsWith('mps');
  $: separateGpu = device?.id.startsWith('cuda');
  const labels: Record<string, string> = {
    loading_model: 'Loading model', reading_frames: 'Reading frames', preparing_clip: 'Preparing clip',
    encoding: 'VAE encoding', enhancing: 'Diffusion', decoding: 'VAE decoding',
    finishing: 'Finishing clip', saving: 'Saving video'
  };
</script>

<div class="memory-panel" class:failed={memory?.oom} aria-label="SeedVR2 memory">
  {#if separateGpu}<label class="memory-choice"><input type="checkbox" checked={lowMemory} {disabled} on:change={event => dispatch('change', event.currentTarget.checked)} /> Reduce GPU memory</label>
  {:else}<strong>{shared ? 'Apple GPU · automatic memory management' : 'SeedVR2 memory'}</strong>{/if}
  <p>{lowMemory && separateGpu || shared || device?.id === 'cpu' ? 'Up to 5 frames per clip · 128 px VAE tiles.' : 'Up to 9 frames per clip · 512 px VAE tiles.'}
    {#if lowMemory && separateGpu} 32 diffusion blocks and intermediate tensors offload to CPU. Uses more system RAM and can be slower.{:else if separateGpu} CPU block and tensor offload is off. Larger tiles and clips use substantially more GPU memory.{:else if shared} Uses shared system memory with small tiles and short clips automatically. CPU block offload is not used. Safe memory mode belongs to frame-by-frame models.{:else if device?.id.startsWith('xpu') || device?.id.startsWith('directml')} SeedVR2 offload is not implemented for this backend. Use a supported CUDA/ROCm or Apple GPU backend.{/if}
    Smaller tiles and shorter clips can affect seams and temporal consistency.
  </p>
  <p>Requested output: <strong>{outputDimensions || 'Choose a video'}</strong>. Memory saving keeps this size.</p>
  <p>{#if device?.total_memory}{gib(device.total_memory)} {shared ? 'shared memory' : 'device capacity'}.{' '}{/if}Capacity is not a guarantee that a clip fits; output dimensions and clip length also matter.</p>
  {#if memory}
    <div class="reading-title"><strong>{memory.oom ? 'Out of memory' : running ? 'Live memory' : 'Last job memory'}</strong><span>{labels[memory.stage] ?? memory.stage}</span></div>
    <p>{memory.output_width} × {memory.output_height} · up to {memory.clip_frames} frames/clip · {memory.shared_memory ? 'automatic memory management' : memory.low_memory ? 'memory saving' : 'standard'}</p>
    <dl>
      {#if memory.gpu_sample_available}
        <div><dt>GPU allocated / peak</dt><dd>{gib(memory.device_allocated_memory)} / {gib(memory.device_peak_memory)}</dd></div>
        {#if !memory.shared_memory}
          <div><dt>GPU reserved</dt><dd>{gib(memory.device_reserved_memory)}</dd></div>
          <div><dt>GPU free / total</dt><dd>{gib(memory.device_free_memory)} / {gib(memory.device_total_memory)}</dd></div>
        {/if}
      {:else}<div><dt>GPU memory</dt><dd>Measurement unavailable</dd></div>{/if}
      <div><dt>System RAM available</dt><dd>{gib(memory.system_ram_available)}</dd></div>
      <div><dt>Worker RAM</dt><dd>{gib(memory.process_ram)}</dd></div>
    </dl>
    <p class="explanation">Reserved memory includes allocated tensors and PyTorch's cache. {memory.oom ? 'Readings captured at failure.' : running ? 'Measured every second.' : 'Readings from the last job.'}</p>
    {#if memory.oom}<p class="recovery">{memory.device.startsWith('mps') ? 'Apple GPU memory management was already active. ' : memory.device.startsWith('cuda') ? memory.low_memory ? 'Memory saving was already enabled. ' : 'Enable Reduce GPU memory. ' : ''}Choose a smaller Output resolution, or switch to tiled HAT-S for large video.</p>{/if}
  {/if}
</div>

<style>
  .memory-panel { margin: 16px 0; padding: 14px; background: rgba(106, 137, 221, .07); border: 1px solid rgba(106, 137, 221, .3); border-radius: 9px; font-size: 12px; }
  .memory-panel.failed { border-color: #aa5757; }
  .memory-choice { display: flex; align-items: center; gap: 9px; font-weight: 600; font-size: 13px; }
  input { accent-color: #6b91f2; }
  p { color: #adb1ba; line-height: 1.5; margin: 9px 0 0; }
  strong { color: #e2e5ec; }
  .reading-title { display: flex; flex-wrap: wrap; gap: 6px 12px; justify-content: space-between; padding-top: 13px; margin-top: 13px; border-top: 1px solid #383c46; }
  .reading-title span { color: #a9bdec; }
  dl { margin: 12px 0 0; }
  dl div { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 4px 10px; margin-top: 7px; }
  dt { color: #adb1ba; } dd { margin: 0; font-variant-numeric: tabular-nums; }
  .explanation { font-size: 11px; }
  .recovery { color: #f0b4b4; }
</style>
