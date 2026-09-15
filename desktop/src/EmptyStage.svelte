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
  <div class="glow" aria-hidden="true"><i></i><i></i><i></i></div>
  <button
    class="well"
    type="button"
    aria-label="Add photos or videos: drop them here or click to choose files"
    on:pointerdown|stopPropagation
    on:click={addFiles}
  >
    <svg class="edge" aria-hidden="true"><rect width="100%" height="100%" rx="20" /></svg>
    <span class="content">
      <span class="shot" bind:this={shot} aria-hidden="true">
        <canvas bind:this={canvas}></canvas>
        <span class="divider" style={`left: ${dividerLeft}%`}></span>
        <span class="chip before">Original</span>
        <span class="chip after">4×</span>
      </span>
      <span class="title">{dropping ? 'Drop to add' : 'Drop photos or videos here'}</span>
      <span class="subtitle">or click to choose · upscaled and restored privately on this Mac</span>
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
  .glow {
    position: absolute;
    inset: -20%;
    filter: blur(56px);
    opacity: 0.26;
    transition: opacity 0.3s;
    pointer-events: none;
  }
  .glow i {
    position: absolute;
    border-radius: 50%;
  }
  .glow i:nth-child(1) {
    width: 46%;
    height: 46%;
    left: 18%;
    top: 22%;
    background: #2f6fe8;
    animation: drift-a 16s ease-in-out infinite alternate;
  }
  .glow i:nth-child(2) {
    width: 38%;
    height: 38%;
    right: 16%;
    top: 34%;
    background: #1fa6a0;
    animation: drift-b 19s ease-in-out infinite alternate;
  }
  .glow i:nth-child(3) {
    width: 30%;
    height: 30%;
    left: 40%;
    bottom: 14%;
    background: #243a78;
    animation: drift-a 23s ease-in-out infinite alternate-reverse;
  }
  .well {
    position: absolute;
    inset: 22px;
    display: grid;
    place-items: center;
    padding: 0;
    border: 0;
    border-radius: 20px;
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
    outline: 2px solid var(--accent);
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
    border-radius: 16px;
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
    box-shadow: 0 0 12px rgba(78, 140, 255, 0.8);
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
    background: rgba(20, 22, 27, 0.9);
  }
  .chip {
    position: absolute;
    top: 8px;
    padding: 4px 6px;
    border-radius: 5px;
    background: rgba(12, 13, 16, 0.72);
    color: #dfe3ea;
    font-size: calc(10px * var(--ui-scale));
    font-weight: 600;
    line-height: 1;
  }
  .chip.before {
    left: 8px;
  }
  .chip.after {
    right: 8px;
    color: #b9d0ff;
  }
  .title {
    font-size: calc(21px * var(--ui-scale));
    font-weight: 650;
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
    color: #3c4149;
  }
  .dropping .glow {
    opacity: 0.6;
  }
  .dropping .well {
    background: rgba(78, 140, 255, 0.07);
  }
  .dropping .edge rect {
    stroke: var(--accent);
    stroke-dasharray: none;
  }
  .dropping .shot {
    box-shadow:
      0 18px 40px -14px rgba(0, 0, 0, 0.7),
      0 0 0 2px var(--accent),
      0 0 40px rgba(78, 140, 255, 0.35);
  }
  @keyframes drift-a {
    to {
      transform: translate(12%, -8%) scale(1.1);
    }
  }
  @keyframes drift-b {
    to {
      transform: translate(-14%, 10%) scale(0.92);
    }
  }
  @media (max-height: 520px) {
    .shot {
      display: none;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .glow i {
      animation: none;
    }
  }
</style>
