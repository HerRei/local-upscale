import { writable, type Readable } from 'svelte/store';
import { estimateStart } from './api';
import type { StartBatchInput } from './types';

/** What Start would send, and a stamp for what else changes the answer. */
export interface StartWanted {
  mediaIds: string[];
  input: StartBatchInput;
  /** Anything outside the input that changes the answer: installed models, measured runs. */
  stamp: string;
}

export type StartFetcher = (mediaIds: string[], input: StartBatchInput) => Promise<number | null>;

/** Long enough that a run of clicks or a slider drag is one call, short enough to feel live. */
export const ESTIMATE_DELAY_MS = 400;

/**
 * The run time shown on the cost line. `want(null)` means "no honest request right now"
 * (a job is running, no plan, nothing to process) and clears the time. A new request keeps
 * the last answer on screen until its own answer arrives, so the line does not blink on
 * every click. An error or a missing estimate is the same as no estimate: null.
 */
export function createStartEstimate(
  fetchSeconds: StartFetcher = estimateStart,
  delayMs: number = ESTIMATE_DELAY_MS,
): { seconds: Readable<number | null>; want(wanted: StartWanted | null): void } {
  const store = writable<number | null>(null);
  const seconds: Readable<number | null> = { subscribe: store.subscribe };
  let current: number | null = null;
  const publish = (value: number | null) => {
    if (value === current) return;
    current = value;
    store.set(value);
  };
  let timer: ReturnType<typeof setTimeout> | undefined;
  let lastKey: string | null = null;
  let generation = 0;

  function want(wanted: StartWanted | null): void {
    if (wanted === null) {
      generation += 1;
      clearTimeout(timer);
      lastKey = null;
      publish(null);
      return;
    }
    const key = JSON.stringify([wanted.mediaIds, wanted.input, wanted.stamp]);
    if (key === lastKey) return;
    lastKey = key;
    generation += 1;
    const mine = generation;
    clearTimeout(timer);
    timer = setTimeout(() => {
      fetchSeconds(wanted.mediaIds, wanted.input).then(
        (value) => {
          if (mine !== generation) return;
          publish(typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null);
        },
        () => {
          if (mine === generation) publish(null);
        },
      );
    }, delayMs);
  }

  return { seconds, want };
}
