<script lang="ts">
  import { backgroundLabel, nextBackground, type ViewerBackground } from './lib/viewerBackground';
  export let value: ViewerBackground = 'checker';
  export let set: (value: ViewerBackground) => void = () => {};
  $: next = nextBackground(value);
</script>

<button
  type="button"
  class="bg-switch"
  aria-label={`Viewer background: ${backgroundLabel(value)}`}
  title={`Background: ${backgroundLabel(value)} · click for ${backgroundLabel(next)}`}
  on:click={() => set(next)}><i class="bg-swatch" data-bg={value} aria-hidden="true"></i></button
>
