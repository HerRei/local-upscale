import { describe, expect, it } from 'vitest';
import { demoSnapshot } from './demo';
import {
  EtaCountdown,
  EtaDisplay,
  EtaLabel,
  etaStep,
  etaView,
  formatEta,
  withEta,
} from './eta-display';
import { formatDuration } from './format';
import type { JobRecord } from './types';

describe('the countdown', () => {
  it('counts down by itself between the engine reports', () => {
    const countdown = new EtaCountdown();
    expect(countdown.seconds(0)).toBeNull();
    countdown.update(120, 0);
    expect(countdown.seconds(0)).toBe(120);
    expect(countdown.seconds(1)).toBe(119);
    expect(countdown.seconds(30)).toBe(90);
  });

  it('ignores an estimate within six percent or three seconds of it', () => {
    const countdown = new EtaCountdown();
    countdown.update(120, 0);
    for (let at = 1; at <= 30; at++) countdown.update(120 - at + (at % 2 ? 4 : -4), at);
    expect(countdown.seconds(30)).toBe(90);
    // Near the end three seconds is the limit, not six percent of the little that is left.
    countdown.update(12, 108);
    countdown.update(15, 109);
    expect(countdown.seconds(109)).toBe(11);
  });

  it('follows a lasting difference only after it has lasted, and closes the gap smoothly', () => {
    const countdown = new EtaCountdown();
    countdown.update(100, 0);
    // 60 s on, the estimate says 70 more instead of 40: a one-off value does nothing...
    countdown.update(70, 60);
    expect(countdown.seconds(60)).toBe(40);
    countdown.update(40, 61);
    countdown.update(69, 62);
    // ...a second one three seconds later is confirmed.
    countdown.update(68, 65);
    const shown = [65, 66, 67, 68, 69, 70].map((at) => countdown.seconds(at) as number);
    expect(shown[0]).toBeCloseTo(35, 5);
    // From what was shown to the new estimate (68 at 65 s) over four seconds.
    expect(shown[4]).toBeCloseTo(64, 5);
    for (let index = 1; index < shown.length; index++)
      expect(shown[index]).toBeGreaterThan(shown[index - 1] - 1.0001);
    expect(shown[0]).toBeLessThan(68);
    expect(shown[5]).toBeCloseTo(63, 5);
  });

  it('follows an estimate at once in its first seconds, while it is still sharpening', () => {
    const countdown = new EtaCountdown();
    countdown.update(80, 0);
    countdown.update(55, 1);
    countdown.update(50, 2);
    // A second of confirmation instead of three, then the gap closes over four seconds.
    expect(countdown.seconds(2)).toBeCloseTo(78, 5);
    expect(countdown.seconds(6)).toBeCloseTo(46, 5);
  });

  it('does not move up and down for noise', () => {
    const countdown = new EtaCountdown();
    countdown.update(100, 0);
    countdown.update(150, 10);
    countdown.update(150, 14);
    const high = countdown.seconds(20) as number;
    expect(high).toBeGreaterThan(130);
    // The estimate falls back at once, but not by twice the allowed difference.
    countdown.update(115, 21);
    countdown.update(115, 26);
    expect(countdown.seconds(26)).toBeCloseTo(high - 6, 5);
  });

  it('shows nothing without an estimate and is never below a second', () => {
    const countdown = new EtaCountdown();
    for (const bad of [0, -5, Number.NaN, Number.POSITIVE_INFINITY, null, undefined]) {
      countdown.update(bad, 0);
      expect(countdown.seconds(0)).toBeNull();
    }
    countdown.update(5, 0);
    expect(countdown.seconds(100)).toBe(1);
    countdown.update(0, 101);
    expect(countdown.seconds(101)).toBeNull();
  });
});

describe('rounding', () => {
  it('rounds to what the estimate can claim', () => {
    expect(etaStep(40)).toBe(5);
    expect(etaStep(60)).toBe(10);
    expect(etaStep(599)).toBe(10);
    expect(etaStep(600)).toBe(60);
    const cases: [number, string][] = [
      [0.4, '5s'],
      [3, '5s'],
      [37, '35s'],
      [58, '1:00'],
      [64, '1:00'],
      [66, '1:10'],
      [127, '2:10'],
      [596, '10 min'],
      [740, '12 min'],
      [3000, '50 min'],
      [3600, '1h'],
      [3722, '1h 2m'],
      [7 * 3600 + 29 * 60, '7h 29m'],
      [90000, '1d 1h'],
    ];
    for (const [seconds, text] of cases)
      expect([seconds, formatEta(seconds)]).toEqual([seconds, text]);
    expect(formatEta(0)).toBeNull();
    expect(formatEta(Number.NaN)).toBeNull();
  });

  it('does not flip back and forth at the edge of a step', () => {
    const label = new EtaLabel();
    const shown = [64, 66, 64, 66, 65, 67, 64, 66].map((seconds) => label.text(seconds));
    expect(new Set(shown).size).toBe(1);
    // It does change once the value has clearly left.
    expect(label.text(72)).toBe('1:10');
    expect(label.text(63)).toBe('1:10');
    expect(label.text(61)).toBe('1:00');
    const minutes = new EtaLabel();
    const near = [629, 631, 629, 631, 630].map((seconds) => minutes.text(seconds));
    expect(new Set(near).size).toBe(1);
  });

  it('counts down in steps, not in every second', () => {
    const label = new EtaLabel();
    const texts: string[] = [];
    for (let seconds = 300; seconds >= 1; seconds--) {
      const text = label.text(seconds);
      if (text !== texts[texts.length - 1]) texts.push(text as string);
    }
    // 5:00 to 1:00 in tens (30 steps), then fives (12): the 42 steps and a few edge steps.
    expect(texts.length).toBeLessThan(48);
    expect(texts[0]).toBe('5:00');
    expect(texts[texts.length - 1]).toBe('5s');
  });

  it('puts the shown time into a progress line that carried the raw one', () => {
    expect(withEta('3 of 12 tiles · ETA 1:05', '1:10')).toBe('3 of 12 tiles · ETA ≈ 1:10');
    expect(withEta('Frame 2 of 9 · Tile 1 / 4 · ETA ≈ 2d 2h', '2 days')).toBe(
      'Frame 2 of 9 · Tile 1 / 4 · ETA ≈ 2 days',
    );
    expect(withEta('Frame 2 of 9 · ETA 1h 5m', '1h 5m')).toBe('Frame 2 of 9 · ETA ≈ 1h 5m');
    expect(withEta('3 of 12 tiles · ETA 7s', null)).toBe('3 of 12 tiles');
    expect(withEta('Upscaling stage complete.', '1:10')).toBe('Upscaling stage complete.');
    expect(withEta('ETA: measuring first tile…', '1:10')).toBe('ETA: measuring first tile…');
  });
});

function job(id: string, status: JobRecord['status'], units: number): JobRecord {
  return {
    id,
    media_id: id,
    media_name: `${id}.png`,
    media_kind: 'image',
    status,
    progress: 0,
    output_path: '',
    error: '',
    created_at: 0,
    work_key: 'span:mps',
    work_units: units,
  };
}

describe('the status bar times', () => {
  it('count the running photo and the queue down by themselves', () => {
    const s = demoSnapshot();
    s.jobs = [job('b', 'queued', 4), job('a', 'running', 4)];
    Object.assign(s.runtime, {
      active_job_id: 'a',
      progress: 50,
      estimated_remaining_seconds: 40,
      elapsed_seconds: 40,
    });
    const display = new EtaDisplay();
    const first = etaView(s, display, 100);
    expect(first.job).toBe('40s');
    expect(first.current).toBe('40s');
    // Queue: 40 left now, one more photo of the same size: 80 s long as measured.
    expect(first.queue).toBe('2:00');
    expect(first.next).toBe('1:20');
    // Twenty seconds later with no news from the engine.
    const later = etaView(s, display, 120);
    expect(later.job).toBe('20s');
    expect(later.queue).toBe('1:40');
    // A photo's length is what has run plus what is left: unchanged by the counting.
    expect(later.next).toBe('1:20');
  });

  it('measures a batch at the speed of all lanes: the remaining photos over one speed', () => {
    const s = demoSnapshot();
    // Four equal photos in a batch, the first one a third done. The engine's number for
    // it is its unfinished share at the batch's measured speed (12 MP per 30 s).
    s.jobs = [
      job('d', 'queued', 12),
      job('c', 'queued', 12),
      job('b', 'queued', 12),
      job('a', 'running', 12),
    ];
    Object.assign(s.runtime, {
      active_job_id: 'a',
      progress: 33,
      estimated_remaining_seconds: 20,
      elapsed_seconds: 10,
    });
    const view = etaView(s, new EtaDisplay(), 0);
    expect(view.timing?.queueSeconds).toBeCloseTo(20 + 3 * 30, 5);
    expect(view.queue).toBe('1:50');
    expect(view.timing?.itemCount).toBe(4);
  });
});

/** A small seeded random source. */
function random(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

interface Report {
  at: number;
  raw: number;
}

/** What the engine sends: the true time left, off by a slowly wandering few percent (2 to 3 on
 * average: what the estimate wanders by in simulated runs with noisy tiles). */
function reports(seed: number, length: number, every = 0.4): Report[] {
  const next = random(seed);
  const out: Report[] = [];
  let wander = 0;
  for (let at = 3; at < length - 1; at += every) {
    wander = 0.92 * wander + (next() - 0.5) * 0.03;
    // Now and then a tile that was slow or fast moves the number for one report.
    const spike = next() < 0.03 ? (next() < 0.5 ? 0.12 : -0.12) : 0;
    out.push({ at, raw: Math.max(0.5, (length - at) * (1 + wander + spike)) });
  }
  return out;
}

/** The shown texts of a run, once per second, and how many times the text changed. */
function replay(list: Report[], length: number, how: 'before' | 'after') {
  const display = new EtaDisplay();
  const label = new EtaLabel();
  const shown: { at: number; text: string | null; seconds: number | null }[] = [];
  let index = 0;
  let raw = 0;
  let changes = 0;
  let previous: string | null = null;
  // The window redraws on every report; a second hand ticks in between.
  const instants = new Set<number>();
  for (let at = 0; at < length - 1; at += 1) instants.add(at);
  for (const report of list) instants.add(report.at);
  for (const at of [...instants].sort((a, b) => a - b)) {
    while (index < list.length && list[index].at <= at) raw = list[index++].raw;
    let text: string | null;
    let seconds: number | null;
    if (how === 'before') {
      seconds = raw > 0 ? raw : null;
      text = seconds === null ? null : formatDuration(seconds);
    } else {
      display.observe('job', raw, at, at);
      seconds = display.live(at).remaining;
      text = label.text(seconds);
    }
    if (text !== null && text !== previous) changes++;
    previous = text ?? previous;
    shown.push({ at, text, seconds });
  }
  return { shown, changes };
}

describe('a run with realistic noise', () => {
  const length = 240;

  it('changes only at the steps of the rounding, a handful of times more at most', () => {
    // How many times a perfect estimate would change the text: the steps alone.
    const ideal = new EtaLabel();
    let steps = 0;
    let last: string | null = null;
    for (let at = 3; at < length - 1; at += 1) {
      const text = ideal.text(length - at);
      if (text !== last) steps++;
      last = text;
    }
    const before: number[] = [];
    for (let seed = 1; seed <= 20; seed++) {
      const list = reports(seed, length);
      const after = replay(list, length, 'after').changes;
      before.push(replay(list, length, 'before').changes);
      expect(after - steps, `seed ${seed}`).toBeLessThanOrEqual(6);
    }
    // Today's way of redrawing every report changes the text about once a second or more.
    expect(Math.min(...before)).toBeGreaterThan(steps * 4);
  });

  it('is within 25 % after a seventh of the run and 15 % after half of it', () => {
    for (let seed = 1; seed <= 20; seed++) {
      const { shown } = replay(reports(seed, length), length, 'after');
      for (const { at, seconds } of shown) {
        const left = length - at;
        // Under half a minute the step of five seconds and the allowed difference of three
        // are a sixth of what is left or more.
        if (seconds === null || at < 0.15 * length || left < 30) continue;
        const allowed = at < 0.5 * length ? 0.25 : 0.15;
        expect(Math.abs(seconds - left), `seed ${seed} at ${at}`).toBeLessThanOrEqual(
          allowed * left,
        );
      }
    }
  });

  it('shows no time that is not an estimate, and never a negative one', () => {
    const { shown } = replay(reports(5, length), length, 'after');
    expect(shown.filter((item) => item.at < 3).every((item) => item.text === null)).toBe(true);
    for (const { seconds } of shown)
      if (seconds !== null) expect(seconds).toBeGreaterThanOrEqual(1);
  });

  it('follows a lasting slowdown within a reasonable time', () => {
    // At 60 s the job turns twice as slow: the time left is then 360 s instead of 180.
    const list: Report[] = [];
    for (let at = 3; at < 360; at += 0.4)
      list.push({ at, raw: at < 60 ? 240 - at : (300 - 60) * 2 - (at - 60) * 1 });
    const display = new EtaDisplay();
    let index = 0;
    let raw = 0;
    let caught = Number.NaN;
    for (let at = 3; at < 200; at += 1) {
      while (index < list.length && list[index].at <= at) {
        raw = list[index++].raw;
        display.observe('job', raw, at, list[index - 1].at);
      }
      const seconds = display.live(at).remaining as number;
      if (at > 60 && Math.abs(seconds - raw) <= 0.15 * raw && Number.isNaN(caught)) caught = at;
    }
    expect(caught).toBeLessThanOrEqual(75);
  });
});
