<script lang="ts">
  import { onDestroy, onMount } from 'svelte';

  export let addFiles: () => void;
  export let dropping = false;

  let shot: HTMLSpanElement;
  let canvas: HTMLCanvasElement;
  let dividerLeft = 50;
  let frame = 0;

  // A small dusk landscape: sharp on the right of the divider, pixelated on the left.
  function paintScene(context: CanvasRenderingContext2D, width: number, height: number): void {
    const sky = context.createLinearGradient(0, 0, 0, height);
    sky.addColorStop(0, '#2a4a86');
    sky.addColorStop(0.55, '#d9876a');
    sky.addColorStop(1, '#f2c48d');
    context.fillStyle = sky;
    context.fillRect(0, 0, width, height);
    context.fillStyle = '#ffe7b8';
    context.beginPath();
    context.arc(width * 0.7, height * 0.46, height * 0.1, 0, Math.PI * 2);
    context.fill();
    const ridges: [string, number, number][] = [
      ['#6b5a7a', 0.55, 0.18],
      ['#3f3c5c', 0.68, 0.14],
      ['#1f2338', 0.82, 0.1],
    ];
    ridges.forEach(([color, base, amplitude], index) => {
      context.fillStyle = color;
      context.beginPath();
      context.moveTo(0, height);
      for (let x = 0; x <= width; x += width / 60) {
        const y =
          height * base -
          Math.sin((x / width) * (5 + index * 3) + index) * height * amplitude * 0.5 -
          Math.sin((x / width) * 17 + index * 2) * height * amplitude * 0.18;
        context.lineTo(x, y);
      }
      context.lineTo(width, height);
      context.fill();
    });
  }

  onMount(() => {
    const context = canvas.getContext('2d');
    // Headless test environments may provide a canvas without 2D drawing.
    if (!context || typeof context.createLinearGradient !== 'function') return;
    const scale = Math.min(2, window.devicePixelRatio || 1);
    const width = Math.round(shot.clientWidth * scale);
    const height = Math.round(shot.clientHeight * scale);
    canvas.width = width;
    canvas.height = height;
    const sharp = document.createElement('canvas');
    sharp.width = width;
    sharp.height = height;
    const tiny = document.createElement('canvas');
    tiny.width = 26;
    tiny.height = 17;
    const sharpContext = sharp.getContext('2d');
    const tinyContext = tiny.getContext('2d');
    if (!sharpContext || !tinyContext) return;
    paintScene(sharpContext, width, height);
    paintScene(tinyContext, tiny.width, tiny.height);
    const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;

    const draw = (time: number) => {
      const phase = reduceMotion ? 0.5 : (Math.sin(time / 1100) * 0.5 + 0.5) * 0.7 + 0.15;
      const x = width * phase;
      context.imageSmoothingEnabled = false;
      context.drawImage(tiny, 0, 0, width, height);
      context.save();
      context.beginPath();
      context.rect(x, 0, width - x, height);
      context.clip();
      context.drawImage(sharp, 0, 0);
      context.restore();
      dividerLeft = phase * 100;
      if (!reduceMotion) frame = requestAnimationFrame(draw);
    };
    draw(0);
  });

  onDestroy(() => cancelAnimationFrame(frame));
</script>

<div class="empty-stage" class:dropping>
  <button
    class="well"
    type="button"
    aria-label="Add photos or videos: drop them here or click to choose files"
    on:pointerdown|stopPropagation
    on:click={addFiles}
  >
    <svg class="edge" aria-hidden="true"><rect width="100%" height="100%" rx="4" /></svg>
    <span class="content">
      <span class="shot" bind:this={shot} aria-hidden="true">
        <canvas bind:this={canvas}></canvas>
        <span class="divider" style={`left: ${dividerLeft}%`}></span>
        <span class="chip before">Original</span>
        <span class="chip after">4×</span>
      </span>
      <span class="title">{dropping ? 'Drop to add' : 'Drop photos or videos here'}</span>
      <span class="subtitle"
        >or click to choose · upscaled and restored privately on this computer</span
      >
      <span class="capabilities" aria-hidden="true">
        <span>Upscale 2–4×</span>
        <span>Remove noise, blur, JPEG</span>
        <span>Video · Labs</span>
      </span>
    </span>
  </button>
</div>

<style>
  .empty-stage {
    position: absolute;
    inset: 0;
    display: grid;
    place-items: center;
    overflow: hidden;
    text-align: center;
  }
  .well {
    position: absolute;
    inset: 22px;
    display: grid;
    place-items: center;
    padding: 0;
    border: 0;
    border-radius: var(--radius-surface);
    background: transparent;
    color: inherit;
    font: inherit;
    text-align: center;
    cursor: pointer;
    transition: background 0.25s;
  }
  .well:hover {
    background: rgba(255, 255, 255, 0.025);
  }
  .well:hover .edge rect {
    stroke: rgba(255, 255, 255, 0.26);
  }
  .well:focus-visible {
    outline: 2px solid var(--focus);
    outline-offset: 3px;
  }
  .edge {
    position: absolute;
    inset: 0.75px;
    width: calc(100% - 1.5px);
    height: calc(100% - 1.5px);
    overflow: visible;
    pointer-events: none;
  }
  .edge rect {
    fill: none;
    stroke: rgba(255, 255, 255, 0.14);
    stroke-width: 1.5;
    stroke-dasharray: 7 7;
    transition: stroke 0.25s;
  }
  .content {
    position: relative;
    display: grid;
    justify-items: center;
    gap: 14px;
    padding: 24px;
  }
  .shot {
    display: block;
    position: relative;
    width: 216px;
    height: 144px;
    margin-bottom: 4px;
    overflow: hidden;
    border-radius: var(--radius-surface);
    box-shadow:
      0 18px 40px -14px rgba(0, 0, 0, 0.7),
      0 0 0 1px rgba(255, 255, 255, 0.08);
    transition: box-shadow 0.25s;
  }
  .shot canvas {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
  }
  .divider {
    position: absolute;
    top: 0;
    bottom: 0;
    width: 2px;
    margin-left: -1px;
    background: rgba(255, 255, 255, 0.9);
  }
  .divider::after {
    content: '';
    position: absolute;
    top: 50%;
    left: 50%;
    width: 20px;
    height: 20px;
    margin: -10px 0 0 -10px;
    border: 1.5px solid #fff;
    border-radius: 50%;
    background: rgba(22, 22, 24, 0.9);
  }
  .chip {
    position: absolute;
    top: 8px;
    padding: 4px 6px;
    border-radius: var(--radius);
    background: rgba(13, 13, 15, 0.72);
    color: #e3e3e6;
    font-size: calc(10px * var(--ui-scale));
    font-weight: 600;
    line-height: 1;
  }
  .chip.before {
    left: 8px;
  }
  .chip.after {
    right: 8px;
  }
  .title {
    font-size: calc(21px * var(--ui-scale));
    font-weight: 600;
    letter-spacing: -0.015em;
  }
  .subtitle {
    margin-top: -6px;
    color: var(--secondary);
    font-size: calc(13px * var(--ui-scale));
  }
  .capabilities {
    display: flex;
    flex-wrap: wrap;
    justify-content: center;
    gap: 6px 14px;
    margin: 2px 0 0;
    padding: 0;
    list-style: none;
    color: var(--tertiary);
    font-size: calc(12px * var(--ui-scale));
  }
  .capabilities span + span::before {
    content: '·';
    margin-right: 14px;
    color: #414143;
  }
  .dropping .well {
    background: rgba(255, 255, 255, 0.039);
  }
  .dropping .edge rect {
    stroke: var(--accent);
    stroke-dasharray: none;
  }
  .dropping .shot {
    box-shadow:
      0 18px 40px -14px rgba(0, 0, 0, 0.7),
      0 0 0 2px var(--accent);
  }
  @media (max-height: 520px) {
    .shot {
      display: none;
    }
  }
</style>
