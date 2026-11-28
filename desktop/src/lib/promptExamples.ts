export interface PromptExample {
  title: string;
  prompt: string;
  /** Shown under the prompt; the Colour entry points to the Fix that only adds colour. */
  note?: string;
  /** Edit model families this prompt passed on; the list-wide families apply when absent. */
  families?: string[];
}

export const PROMPT_EXAMPLES: PromptExample[] = [
  {
    title: 'Scratches and dust',
    prompt:
      'Remove the scratches, dust specks and stains. Keep the faces, the grain and everything else unchanged.',
    families: [],
  },
  {
    title: 'Colour',
    prompt:
      'Add natural colour to this black-and-white photo. Keep the faces, the lighting and every detail unchanged.',
    note: 'Repaints the photo. To only add colour, use Fix › Colour.',
  },
  {
    title: 'Remove something',
    prompt:
      'Remove [the person on the left] and fill the gap with what is behind. Keep everyone and everything else unchanged.',
    families: [],
  },
  {
    title: 'New background',
    prompt:
      'Replace the background with [a plain light grey wall]. Keep the person, their clothing and the light on them unchanged.',
    families: [],
  },
  {
    title: 'Film look',
    prompt:
      'Give the photo a subtle colour-film look: a little warmer, a little softer contrast and fine grain. Keep the colours natural, and the subject and the framing unchanged.',
  },
];

/**
 * Edit model families the examples passed on (task E0, 2026-11-28: FLUX.2 klein 4B Q4 at 768 px,
 * three photos per prompt). Qwen Image Edit 2511 is not tested yet. The prompts with
 * `families: []` redrew small faces on FLUX.2 klein, so they wait for a family they pass on.
 * Mage-Flow-Edit-Turbo and the user's own edit model get no examples.
 */
export const PROMPT_EXAMPLE_FAMILIES: string[] = ['flux2-klein-4b'];

export function examplesFor(family: string | undefined): PromptExample[] {
  if (!family) return [];
  return PROMPT_EXAMPLES.filter((example) =>
    (example.families ?? PROMPT_EXAMPLE_FAMILIES).includes(family),
  );
}

export interface InsertedPrompt {
  text: string;
  slot: [number, number] | null;
  /** False when the browser's own editing refused and the value was set directly. */
  undoable: boolean;
}

/** The text without its brackets and the span between them, if any. */
export function splitSlot(prompt: string): { text: string; slot: [number, number] | null } {
  const match = /\[([^\]]*)\]/.exec(prompt);
  if (!match) return { text: prompt, slot: null };
  const start = match.index;
  const text = prompt.slice(0, start) + match[1] + prompt.slice(start + match[0].length);
  return { text, slot: [start, start + match[1].length] };
}

/**
 * Replaces the field's whole text through its own editing, so the WebView's undo holds the old
 * text, and selects the [slot] words.
 */
export function insertPrompt(textarea: HTMLTextAreaElement, prompt: string): InsertedPrompt {
  const { text, slot } = splitSlot(prompt);
  textarea.focus();
  textarea.select();
  let undoable = false;
  try {
    undoable =
      typeof document.execCommand === 'function' && document.execCommand('insertText', false, text);
  } catch {
    undoable = false;
  }
  if (!undoable) {
    textarea.value = text;
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
  }
  if (slot) textarea.setSelectionRange(slot[0], slot[1]);
  else textarea.setSelectionRange(text.length, text.length);
  return { text, slot, undoable };
}

export function isExampleText(value: string): boolean {
  return PROMPT_EXAMPLES.some((example) => splitSlot(example.prompt).text === value);
}
