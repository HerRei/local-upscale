import { describe, expect, it, vi } from 'vitest';
import { CompareVideoSync, type ComparePlayer } from './compare-video-sync';

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
    sync.mute(true);
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
  it('uses the highest frame rate for stepping and a paused seek still corrects followers', () => {
    const { players, sync } = setup([5, 2]);
    sync.step(1);
    expect(sync.time).toBeCloseTo(1 / 60);
    sync.seek(4);
    expect(players.map((v) => v.currentTime)).toEqual([4, 2]);
    expect(players[1].paused).toBe(true);
  });
});

it('keeps later slots muted if the first slot is unavailable', () => {
  const follower = player();
  const sync = new CompareVideoSync([{ video: follower, fps: 60, audible: false }]);
  sync.mute(false);
  expect(follower.muted).toBe(true);
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
