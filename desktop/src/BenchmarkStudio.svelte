<script lang="ts">
  import type { BenchmarkRender } from './lib/types';
  export let renders: BenchmarkRender[] = [];
  export let running = false;
  let selected = '';
  let reveal = 50;
  let detail = false;
  const key = (render: BenchmarkRender) => `${render.device}/${render.scene_id}`;
  const names: Record<string, string> = {
    's1-classroom': 'Compute study',
    's2-gallery': 'Tile study',
    's3-gallery-encode': 'Export study'
  };
  $: current = renders.find(render => key(render) === selected) ?? renders[renders.length - 1];
  $: if (!renders.length) selected = '';
</script>

<section class="render-studio" aria-label="Benchmark render viewer">
  <header>
    <div><span class="studio-kicker">THE RENDER DESK</span><h4>See the work behind the score.</h4></div>
    <span class="capture-state" class:active={running}>{running ? 'Rendering locally' : current ? 'Captured on this device' : 'Ready when you are'}</span>
  </header>
  {#if current}
    <div class="render-heading">
      <div><strong>{names[current.scene_id] ?? current.scene_id}</strong><span>{current.device} · SPAN · 4×</span></div>
      <button class:chosen={detail} aria-pressed={detail} on:click={() => detail = !detail}>{detail ? 'Fit render' : 'Inspect detail'}</button>
    </div>
    <div class="render-stage" class:detail style={`--split:${reveal}%;--ratio:${current.input_width / current.input_height}`}>
      <div class="render-image"><img src={current.input_data_url} alt="Original benchmark scene" /></div>
      <div class="render-image enhanced"><img src={current.output_data_url} alt="Actual enhanced benchmark render" /></div>
      <span class="image-label before">SOURCE</span><span class="image-label after">ENHANCED</span>
      <div class="split-rule" aria-hidden="true"><span>↔</span></div>
    </div>
    <label class="reveal-control"><span>Original</span><input type="range" min="0" max="100" bind:value={reveal} aria-label="Reveal enhanced benchmark render" /><span>Enhanced</span></label>
    <div class="render-caption"><span>{current.input_width.toLocaleString()} × {current.input_height.toLocaleString()} → {current.output_width.toLocaleString()} × {current.output_height.toLocaleString()}</span><span>Warm-up capture · scaled preview</span></div>
    <div class="render-strip" aria-label="Captured benchmark scenes">
      {#each renders as render (key(render))}
        <button class:selected={current === render} aria-label={`${names[render.scene_id] ?? render.scene_id} ${render.device}`} aria-pressed={current === render} on:click={() => selected = key(render)}>
          <img src={render.output_data_url} alt="" /><span><strong>{names[render.scene_id] ?? render.scene_id}</strong><small>{render.device}</small></span>
        </button>
      {/each}
    </div>
    <p class="studio-note">Real model output from the unmeasured warm-up. The score measures processing speed; these previews let you inspect the result.</p>
  {:else}
    <div class="render-waiting">
      <div class="study-sheet" aria-hidden="true"><i></i><i></i><i></i><span>01 / 02 / 03</span></div>
      <div><span class="studio-kicker">THREE FIXED STUDIES</span><strong>{running ? 'Preparing the first render…' : 'A benchmark you can look at.'}</strong><p>Gradients, fine detail, and texture. Run the benchmark to compare the actual source and enhanced output from each device.</p></div>
    </div>
    <div class="study-index"><span><b>01</b> Compute</span><span><b>02</b> Tiled processing</span><span><b>03</b> Image export</span></div>
  {/if}
</section>

<style>
  .render-studio { margin: 16px 0; border: 1px solid #41434a; border-radius: 8px; overflow: hidden; background: #181a1f; color: #eeece7; }
  header { display: flex; justify-content: space-between; align-items: center; gap: 16px; padding: 20px; border-bottom: 1px solid #36383e; }
  .studio-kicker { color: #b7a480; font: 9px ui-monospace, monospace; letter-spacing: 1.8px; }
  h4 { margin: 6px 0 0; font: 22px Georgia, serif; letter-spacing: -.5px; font-weight: 400; }
  .capture-state { color: #a8aaa7; font-size: 10px; text-align: right; }
  .capture-state::before { content: ''; display: inline-block; width: 5px; height: 5px; border-radius: 50%; background: #83867c; margin-right: 6px; }
  .capture-state.active::before { background: #d4b67e; }
  .render-heading { padding: 14px 20px; display: flex; justify-content: space-between; align-items: center; gap: 12px; }
  .render-heading strong { font-size: 12px; } .render-heading span { display: block; color: #969995; margin-top: 4px; font: 10px ui-monospace, monospace; }
  .render-heading button { padding: 7px 10px; background: transparent; color: #d4c39f; border: 1px solid #555144; border-radius: 4px; font-size: 10px; cursor: pointer; }
  .render-heading button.chosen { background: #393429; }
  .render-stage { position: relative; width: calc(100% - 40px); aspect-ratio: var(--ratio); max-height: 350px; margin: 0 20px; overflow: hidden; background: #0c0e11; border: 1px solid #404148; }
  .render-image { position: absolute; inset: 0; overflow: hidden; display: grid; place-items: center; }
  .render-image img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: contain; }
  .render-stage.detail img { position: absolute; left: 50%; top: 50%; width: 200%; height: 200%; max-width: none; transform: translate(-50%, -50%); }
  .enhanced { clip-path: inset(0 0 0 calc(100% - var(--split))); }
  .image-label { position: absolute; top: 12px; background: #141518dd; color: #f7f3e8; padding: 5px 7px; font: 8px ui-monospace, monospace; letter-spacing: 1.5px; }
  .before { left: 12px; } .after { right: 12px; }
  .split-rule { position: absolute; top: 0; bottom: 0; left: calc(100% - var(--split)); width: 1px; background: #e1c893; pointer-events: none; }
  .split-rule span { position: absolute; top: 50%; left: -12px; width: 25px; height: 25px; display: grid; place-items: center; color: #27231b; background: #e1c893; border-radius: 50%; }
  .reveal-control { display: flex; align-items: center; gap: 12px; margin: 13px 20px 7px; font: 10px ui-monospace, monospace; color: #b5b6b0; }
  .reveal-control input { flex: 1; min-width: 30px; accent-color: #d6ba82; height: 18px; cursor: ew-resize; }
  .render-caption { display: flex; gap: 10px; justify-content: space-between; margin: 0 20px 16px; font: 9px ui-monospace, monospace; color: #a3a59f; }
  .render-strip { display: flex; gap: 8px; padding: 13px 20px; overflow-x: auto; border-top: 1px solid #34363b; }
  .render-strip button { flex: 0 0 auto; display: flex; gap: 10px; align-items: center; padding: 5px 12px 5px 5px; background: #222429; border: 1px solid #36383f; border-radius: 4px; color: #cbcdc8; cursor: pointer; text-align: left; }
  .render-strip button.selected { border-color: #c1a872; background: #302e28; }
  .render-strip img { width: 42px; height: 34px; object-fit: cover; }
  .render-strip strong { display: block; font-size: 10px; font-weight: 500; } .render-strip small { color: #a9aa9f; font: 9px ui-monospace, monospace; }
  .studio-note { margin: 0 20px 16px; color: #979b95; font-size: 10px; line-height: 1.5; }
  .render-waiting { display: grid; grid-template-columns: 155px 1fr; align-items: center; gap: 26px; padding: 30px 24px; }
  .render-waiting strong { display: block; font: 21px Georgia, serif; margin-top: 10px; }
  .render-waiting p { color: #aaada6; font-size: 11px; line-height: 1.65; max-width: 340px; }
  .study-sheet { background: #d4cebe; padding: 12px; transform: rotate(-4deg); box-shadow: 5px 8px 0 #111316; display: grid; grid-template-columns: repeat(3, 1fr); gap: 5px; }
  .study-sheet i { height: 91px; background: repeating-linear-gradient(0deg, #555950 0 1px, #b4b09b 1px 5px); }
  .study-sheet i:nth-child(2) { background: repeating-conic-gradient(#353d39 0% 25%, #b5b09c 0% 50%) 0 0 / 10px 10px; }
  .study-sheet i:nth-child(3) { background: linear-gradient(135deg, #c2b99b, #666e60); }
  .study-sheet span { grid-column: 1 / -1; color: #464c40; font: 8px ui-monospace, monospace; margin-top: 8px; }
  .study-index { display: flex; gap: 24px; padding: 14px 20px; border-top: 1px solid #35373b; color: #aeb0a7; font-size: 10px; }
  .study-index b { font: 9px ui-monospace, monospace; color: #d4b988; margin-right: 5px; }
  button:focus-visible, input:focus-visible { outline: 2px solid #e1c893; outline-offset: 3px; }
  @media (max-width: 560px) { header { align-items: flex-start; flex-direction: column; } .render-waiting { grid-template-columns: 1fr; } .study-sheet { width: 140px; } .render-caption, .study-index { flex-wrap: wrap; } h4 { font-size: 20px; } }
</style>
