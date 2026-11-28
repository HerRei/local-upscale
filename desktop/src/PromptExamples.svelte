<script lang="ts">
  import { detectPlatform } from './lib/platform';
  import { insertPrompt, isExampleText, type PromptExample } from './lib/promptExamples';

  export let examples: PromptExample[];
  export let textarea: HTMLTextAreaElement | undefined;
  export let value: string;
  export let onClose: () => void;

  const undoKey = detectPlatform() === 'macos' ? '⌘Z' : 'Ctrl+Z';

  $: replacesText = value.trim() !== '' && !isExampleText(value);

  // Where execCommand refuses, the WebView has no undo step for the replaced text; this one
  // Ctrl+Z gives it back.
  function keepForUndo(field: HTMLTextAreaElement, old: string): void {
    const restore = (event: KeyboardEvent): void => {
      if (['Control', 'Meta', 'Shift', 'Alt'].includes(event.key)) return;
      const isUndo = (event.metaKey || event.ctrlKey) && !event.shiftKey && event.key === 'z';
      if (isUndo) {
        event.preventDefault();
        field.value = old;
        field.dispatchEvent(new Event('input', { bubbles: true }));
      }
      field.removeEventListener('keydown', restore);
    };
    field.addEventListener('keydown', restore);
  }

  function choose(example: PromptExample): void {
    if (!textarea) return;
    const old = textarea.value;
    const result = insertPrompt(textarea, example.prompt);
    if (!result.undoable && old !== '') keepForUndo(textarea, old);
    onClose();
    textarea.focus();
    if (result.slot) textarea.setSelectionRange(result.slot[0], result.slot[1]);
  }
</script>

<div class="prompt-examples">
  {#each examples as example (example.title)}
    <button
      type="button"
      class="prompt-example"
      title={example.prompt}
      on:click={() => choose(example)}
    >
      <b>{example.title}</b>
      <span class="prompt-example-text">{example.prompt.replace(/[[\]]/g, '')}</span>
      {#if example.note}<span class="prompt-example-note">{example.note}</span>{/if}
    </button>
  {/each}
  <p class="prompt-examples-warning">
    Edit redraws the picture and can change faces. Compare before you keep it.
  </p>
  {#if replacesText}
    <p class="prompt-examples-warning">Replaces your text. {undoKey} brings it back.</p>
  {/if}
</div>
