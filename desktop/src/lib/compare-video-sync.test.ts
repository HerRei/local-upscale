import { describe, expect, it, vi } from 'vitest';
import { CompareVideoSync, SingleVideoCompareSync, type ComparePlayer } from './compare-video-sync';

function player(duration = 10): ComparePlayer {
  const video: ComparePlayer = {
    currentTime: 0,
    duration,
    paused: true,
    seeking: false,
    readyState: 4,
    playbackRate: 1,
    muted: false,
    play: vi.fn(async () => {
      video.paused = false;
    }),
    pause: vi.fn(() => {
      video.paused = true;
    }),
  };
  return video;
}
function setup(durations = [10, 10]) {
  const players = durations.map(player);
  let now = 0;
  const sync = new CompareVideoSync(
    players.map((video, i) => ({ video, fps: i ? 60 : 15 })),
    () => now,
  );
  return {
    players,
    sync,
    advance: (ms: number) => {
      now += ms;
    },
  };
}
describe('video Compare clock', () => {
  it('propagates play, pause, seek, speed and first-slot audio', async () => {
    const { players, sync } = setup();
    await sync.play();
    expect(players.every((v) => !v.paused)).toBe(true);
    sync.seek(3);
    expect(players.map((v) => v.currentTime)).toEqual([3, 3]);
    sync.rate(1.5);
    expect(players.map((v) => v.playbackRate)).toEqual([1.5, 1.5]);
    expect(players.map((v) => v.muted)).toEqual([false, true]);
    sync.audio(players[1]);
    expect(players.map((v) => v.muted)).toEqual([true, false]);
    sync.audio(null);
    expect(players.every((v) => v.muted)).toBe(true);
    sync.pause();
    expect(players.every((v) => v.paused)).toBe(true);
  });
  it('corrects drift only beyond half of the follower frame without rate nudging', () => {
    const { players, sync } = setup();
    players[0].currentTime = 1;
    players[1].currentTime = 1 - 0.007;
    sync.tick();
    expect(players[1].currentTime).toBe(0.993);
    players[1].currentTime = 0.98;
    sync.tick();
    expect(players[1].currentTime).toBe(1);
    expect(players[1].playbackRate).toBe(1);
  });
  it('pauses every player for a stalled follower and resumes only when all are ready', async () => {
    const { players, sync } = setup();
    await sync.play();
    players[1].readyState = 2;
    sync.stall(players[1]);
    expect(players.every((v) => v.paused)).toBe(true);
    await sync.ready(players[1]);
    expect(players.every((v) => v.paused)).toBe(true);
    players[1].readyState = 4;
    await sync.ready(players[1]);
    expect(players.every((v) => !v.paused)).toBe(true);
    sync.stall(players[1]);
    sync.pause();
    await sync.ready(players[1]);
    expect(players.every((v) => v.paused)).toBe(true);
  });
  it('holds short followers and spans the longest duration even when slot one ends first', async () => {
    const { players, sync, advance } = setup([2, 5]);
    sync.seek(3);
    expect(players.map((v) => v.currentTime)).toEqual([2, 3]);
    await sync.play();
    advance(1000);
    sync.tick();
    expect(sync.time).toBe(4);
    expect(players[0].currentTime).toBe(2);
    expect(players[1].currentTime).toBe(4);
    advance(1000);
    sync.tick();
    expect(sync.duration).toBe(5);
    expect(sync.playing).toBe(false);
  });
  it('steps by the highest frame rate into the middle of a frame and a paused seek still corrects followers', () => {
    const { players, sync } = setup([5, 2]);
    sync.step(1);
    // Frame 1 of the 60 fps side; the 15 fps side shows its frame 0, which holds that time.
    expect(sync.frame).toBe(1);
    expect(sync.time).toBeCloseTo(1.5 / 60);
    expect(players.map((v) => v.currentTime)).toEqual([1.5 / 60, 1.5 / 60]);
    sync.step(-1);
    sync.step(-1);
    expect(sync.frame).toBe(0);
    sync.scrub(1);
    expect(sync.frame).toBe(60);
    expect(sync.time).toBeCloseTo(60.5 / 60);
    sync.seek(4);
    expect(players.map((v) => v.currentTime)).toEqual([4, 2]);
    expect(players[1].paused).toBe(true);
  });
});

it('keeps a side muted when the audible side has no player yet', () => {
  const follower = player();
  new CompareVideoSync([{ video: follower, fps: 60, audible: false }]);
  expect(follower.muted).toBe(true);
});

describe('an unfinished side', () => {
  // The original runs 4 s; the stopped job kept two frames of it.
  function partial(order: 'original first' | 'unfinished first' = 'original first') {
    const original = player(4);
    const kept = player(2 / 27);
    let now = 0;
    const entries = [
      { video: original, fps: 27 },
      { video: kept, fps: 27, kept: {} },
    ];
    const sync = new CompareVideoSync(
      order === 'original first' ? entries : entries.reverse(),
      () => now,
    );
    return { original, kept, sync, advance: (ms: number) => (now += ms) };
  }
  it('ends the clock where the kept part ends, whichever slot holds it', () => {
    for (const order of ['original first', 'unfinished first'] as const) {
      const { original, sync } = partial(order);
      expect(sync.duration).toBeCloseTo(2 / 27);
      expect(sync.limited).toBe(true);
      expect(sync.frames).toBe(2);
      sync.seek(3);
      expect(original.currentTime).toBeCloseTo(2 / 27);
    }
  });
  it('steps both sides to the same frame, one by one, and stops at the last kept frame', () => {
    const { original, kept, sync } = partial();
    expect(sync.frame).toBe(0);
    sync.step(1);
    expect(sync.frame).toBe(1);
    expect(original.currentTime).toBeCloseTo(1.5 / 27);
    expect(kept.currentTime).toBeCloseTo(1.5 / 27);
    sync.step(1);
    expect(sync.frame).toBe(1);
    expect(original.currentTime).toBeCloseTo(1.5 / 27);
    sync.step(-1);
    expect(original.currentTime).toBeCloseTo(0.5 / 27);
    expect(kept.currentTime).toBeCloseTo(0.5 / 27);
  });
  it('counts the frames the host counted instead of a nominal rate over the length', () => {
    const original = player(4);
    const kept = player(0.074);
    // 0.074 s at a nominal 30 fps would count a third frame that does not exist.
    expect(
      new CompareVideoSync([
        { video: original, fps: 30 },
        { video: kept, fps: 30, kept: {} },
      ]).frames,
    ).toBe(3);
    const sync = new CompareVideoSync([
      { video: original, fps: 30 },
      { video: kept, fps: 30, kept: { seconds: 0.074, frames: 2 } },
    ]);
    expect(sync.frames).toBe(2);
    sync.seekFrame(1);
    expect(kept.currentTime).toBeCloseTo(0.05);
    expect(original.currentTime).toBeCloseTo(0.05);
  });
  it('uses the measured duration and frame count when the result fps is unknown', () => {
    const sync = new CompareVideoSync([
      { video: player(3), fps: 30 },
      { video: player(1), fps: 0, kept: { seconds: 1, frames: 24 } },
    ]);
    expect(sync.duration).toBe(1);
    expect(sync.frames).toBe(24);
    sync.seekFrame(23);
    expect(sync.time).toBeCloseTo(23.5 / 24);
  });
  it('clamps to kept_frames / fps when container kept_seconds includes audio overhang', () => {
    const original = player(3);
    const kept = player(1.5);
    const sync = new CompareVideoSync([
      { video: original, fps: 30 },
      { video: kept, fps: 30, kept: { seconds: 1.5, frames: 30 } },
    ]);
    expect(sync.duration).toBeCloseTo(1);
    expect(sync.frames).toBe(30);
    sync.seekFrame(29);
    expect(original.currentTime).toBeCloseTo(29.5 / 30);
    expect(kept.currentTime).toBeCloseTo(29.5 / 30);
  });
  it('pairs frames by the file’s own length when the player reports a longer one', () => {
    // Two frames of a 30 fps video; the player gives the clip as 0.10 s long.
    const original = player(4);
    const kept = player(0.1);
    const sync = new CompareVideoSync([
      { video: original, fps: 30 },
      { video: kept, fps: 30, kept: { seconds: 2 / 30, frames: 2 } },
    ]);
    expect(sync.duration).toBeCloseTo(2 / 30);
    expect(sync.frames).toBe(2);
    sync.seekFrame(1);
    // The middle of frame 1 of both: 0.075 s (frames over the player's length) is the
    // original's third frame.
    expect(original.currentTime).toBeCloseTo(0.05);
    expect(kept.currentTime).toBeCloseTo(0.05);
    expect(Math.floor(original.currentTime * 30)).toBe(1);
    sync.step(1);
    expect(sync.frame).toBe(1);
  });
  it('takes frames and length from the same unfinished side when two are compared', () => {
    // 15 frames in half a second (30 fps) and 12 frames in a fifth (60 fps).
    const slow = player(0.5);
    const fast = player(0.2);
    const sync = new CompareVideoSync([
      { video: slow, fps: 30, kept: { seconds: 0.5, frames: 15 } },
      { video: fast, fps: 60, kept: { seconds: 0.2, frames: 12 } },
    ]);
    expect(sync.duration).toBeCloseTo(0.2);
    expect(sync.frames).toBe(12);
    sync.seekFrame(11);
    expect(fast.currentTime).toBeCloseTo(11.5 / 60);
    expect(slow.currentTime).toBeCloseTo(11.5 / 60);
  });
  it('steps through the frames of an interpolated side beside the unfinished one', () => {
    const kept = player(2 / 30);
    const doubled = player(4);
    const sync = new CompareVideoSync([
      { video: kept, fps: 30, kept: { seconds: 2 / 30, frames: 2 } },
      { video: doubled, fps: 60 },
    ]);
    expect(sync.frames).toBe(4);
    sync.seekFrame(3);
    expect(doubled.currentTime).toBeCloseTo(3.5 / 60);
    // A nominal rate that is a little off does not add frames: 30 for 29.97.
    const ntsc = new CompareVideoSync([
      { video: player(4), fps: 30 },
      { video: player(2 / 29.97), fps: 30, kept: { seconds: 2 / 29.97, frames: 2 } },
    ]);
    expect(ntsc.frames).toBe(2);
  });
  it('holds every side on the last kept frame when playback reaches the end', async () => {
    const { original, kept, sync } = partial();
    await sync.play();
    original.currentTime = 0.1;
    kept.currentTime = 2 / 27;
    sync.tick();
    expect(sync.playing).toBe(false);
    expect(original.paused).toBe(true);
    expect(original.currentTime).toBeCloseTo(1.5 / 27);
    expect(sync.frame).toBe(1);
    // Play again starts over instead of ending at once.
    await sync.play();
    expect(sync.time).toBe(0);
    expect(original.currentTime).toBe(0);
  });
  it('spans the longest side again when no side is unfinished', () => {
    const sync = new CompareVideoSync([
      { video: player(4), fps: 27 },
      { video: player(2), fps: 27 },
    ]);
    expect(sync.duration).toBe(4);
    expect(sync.limited).toBe(false);
  });
});

it('does not resume until both seeking players are ready', async () => {
  const { players, sync } = setup();
  await sync.play();
  players[0].seeking = players[1].seeking = true;
  sync.stall(players[0]);
  sync.stall(players[1]);
  players[0].seeking = false;
  await sync.ready(players[0]);
  expect(players.every((video) => video.paused)).toBe(true);
  players[1].seeking = false;
  await sync.ready(players[1]);
  expect(players.every((video) => !video.paused)).toBe(true);
});

it('detects insufficient data in a frame check even before a waiting event arrives', async () => {
  const { players, sync } = setup();
  await sync.play();
  players[0].currentTime = 0.5;
  players[1].readyState = 2;
  sync.tick();
  expect(sync.time).toBe(0.5);
  expect(players.every((video) => video.paused)).toBe(true);
  players[1].readyState = 4;
  await sync.ready(players[1]);
  expect(players.every((video) => !video.paused)).toBe(true);
});

it('corrects playback drift beyond half a follower frame', async () => {
  const players = [player(4), player(4)];
  const sync = new CompareVideoSync(players.map((video) => ({ video, fps: 30 })));
  await sync.play();
  players[0].currentTime = 1;
  players[1].currentTime = 0.98;
  sync.tick();
  expect(players[1].currentTime).toBe(1);
});

it('rate-limits drift seeks during playback without pausing master while follower seeks', async () => {
  const { players, sync, advance } = setup();
  await sync.play();
  advance(100);
  players[0].currentTime = 0.5;
  players[1].currentTime = 0.4;
  sync.tick();
  expect(players[1].currentTime).toBe(0.5);
  expect(sync.playing).toBe(true);
  expect(players[0].paused).toBe(false);
  // Follower enters seeking and temporarily drops readyState while decoding: master stays playing.
  players[1].seeking = true;
  players[1].readyState = 2;
  advance(50);
  players[0].currentTime = 0.55;
  sync.tick();
  expect(sync.playing).toBe(true);
  expect(players[0].paused).toBe(false);
  // Seek finishes within cooldown: another drift inside the cooldown window does not re-seek every tick.
  players[1].seeking = false;
  players[1].readyState = 4;
  players[1].currentTime = 0.5;
  advance(100);
  players[0].currentTime = 0.65;
  sync.tick();
  expect(players[1].currentTime).toBe(0.5);
  // After cooldown expires, drift correction runs again.
  advance(500);
  players[0].currentTime = 1.15;
  sync.tick();
  expect(players[1].currentTime).toBe(1.15);
});

it('aligns both sides to the middle of the current frame on pause', async () => {
  const players = [player(4), player(4)];
  const sync = new CompareVideoSync(players.map((video) => ({ video, fps: 30 })));
  await sync.play();
  players[0].currentTime = 1.001;
  players[1].currentTime = 0.999;
  sync.pause();
  expect(players.every((v) => v.paused)).toBe(true);
  expect(players[0].currentTime).toBeCloseTo(30.5 / 30);
  expect(players[1].currentTime).toBeCloseTo(30.5 / 30);
});

describe('SingleVideoCompareSync', () => {
  it('drives one muted combined video and syncs a separate audio follower by choice', async () => {
    const video = player(3);
    const audio = player(3);
    const sync = new SingleVideoCompareSync(video, [
      { fps: 30, duration: 3 },
      { fps: 60, duration: 3 },
    ]);
    expect(video.muted).toBe(true);
    sync.setAudio(audio, 3);
    expect(audio.muted).toBe(false);
    await sync.play();
    expect(video.paused).toBe(false);
    expect(audio.paused).toBe(false);
    expect(video.muted).toBe(true);

    sync.rate(1.5);
    expect(video.playbackRate).toBe(1.5);
    expect(audio.playbackRate).toBe(1.5);

    sync.step(1);
    expect(video.paused).toBe(true);
    expect(audio.paused).toBe(true);
    expect(video.currentTime).toBeCloseTo(1.5 / 60);
    expect(audio.currentTime).toBeCloseTo(1.5 / 60);

    sync.setAudio(null);
    expect(audio.muted).toBe(true);
    expect(audio.paused).toBe(true);
  });

  it('tracks unfinished kept frames even when the combined video is already truncated', async () => {
    const video = player(1);
    const audio = player(1.5);
    const sync = new SingleVideoCompareSync(video, [
      { fps: 30, duration: 3 },
      { fps: 24, duration: 1.5, kept: { seconds: 1.5, frames: 24 } },
    ]);
    sync.setAudio(audio, 1);
    expect(sync.duration).toBeCloseTo(1);
    expect(sync.limited).toBe(true);
    expect(sync.frames).toBe(24);

    await sync.play();
    video.currentTime = 1;
    sync.tick();
    expect(sync.playing).toBe(false);
    expect(video.paused).toBe(true);
    expect(audio.paused).toBe(true);
    expect(video.currentTime).toBeCloseTo(23.5 / 24);
  });

  it('stops on the last kept frame before the combined video reaches its end', async () => {
    const video = player(1);
    const sync = new SingleVideoCompareSync(video, [
      { fps: 30, duration: 3 },
      { fps: 30, duration: 3, kept: { seconds: 1, frames: 30 } },
    ]);
    expect(sync.limited).toBe(true);
    await sync.play();
    // Inside frame 29 (0.967-1.0 s), short of the end of the stream.
    video.currentTime = 0.97;
    sync.tick();
    expect(sync.playing).toBe(false);
    expect(video.paused).toBe(true);
    expect(sync.frame).toBe(29);
    expect(video.currentTime).toBeCloseTo(29.5 / 30);
  });
});
