/** Time-based Compare transport. No DOM, scheduling or source authority lives here. */
export interface ComparePlayer {
  currentTime: number;
  duration: number;
  paused: boolean;
  seeking: boolean;
  readyState: number;
  playbackRate: number;
  muted: boolean;
  play(): Promise<void>;
  pause(): void;
}
type Entry = {
  video: ComparePlayer;
  fps: number;
  /** The side whose sound plays; every other side is muted. */
  audible?: boolean;
  /**
   * A stopped job's kept part: the clock ends where the shortest of these ends. Seconds
   * and frames are what the host measured in the file; either can be unknown (0).
   */
  kept?: { seconds?: number; frames?: number };
};
type KeptRange = { seconds?: number; frames?: number };
type TimingSpec = { fps: number; duration: number; kept?: KeptRange };
type LimitInfo = { seconds: number; frames: number; rate: number };

const length = (video: ComparePlayer) =>
  Number.isFinite(video.duration) ? Math.max(0, video.duration) : 0;

function computeLimit(specs: TimingSpec[]): LimitInfo | undefined {
  let first: LimitInfo | undefined;
  for (const { duration: played, fps, kept } of specs) {
    if (!kept) continue;
    const frames = kept.frames && kept.frames > 0 ? Math.round(kept.frames) : 0;
    const nominalFps = fps > 0 ? fps : 0;
    const measured =
      frames && nominalFps
        ? frames / nominalFps
        : kept.seconds && kept.seconds > 0
          ? kept.seconds
          : played;
    const seconds = played > 0 ? Math.min(measured, played) : measured;
    if (!(seconds > 0) || (first && first.seconds <= seconds)) continue;
    const rate = frames ? nominalFps || frames / measured : 0;
    first = { seconds, frames, rate };
  }
  return first;
}

function rateRefinement(specs: readonly { fps: number }[], rate: number): number {
  const fastest = Math.max(1, ...specs.map((entry) => entry.fps || 30));
  return Math.max(1, Math.round(fastest / rate));
}

function effectiveFrameRate(specs: readonly { fps: number }[], limit?: LimitInfo): number {
  const rate = limit?.rate;
  return rate
    ? rate * rateRefinement(specs, rate)
    : Math.max(1, ...specs.map((entry) => entry.fps || 30));
}

function totalFrames(
  specs: readonly { fps: number }[],
  duration: number,
  limit?: LimitInfo,
): number {
  return limit?.rate
    ? limit.frames * rateRefinement(specs, limit.rate)
    : Math.max(1, Math.ceil(duration * effectiveFrameRate(specs, limit) - 1e-6));
}

function frameIndexAt(time: number, frames: number, frameRate: number): number {
  return Math.max(0, Math.min(frames - 1, Math.floor(time * frameRate + 1e-6)));
}
export class CompareVideoSync {
  time = 0;
  playing = false;
  speed = 1;
  private wanted = false;
  private blocked = new Set<ComparePlayer>();
  private correcting = new Set<ComparePlayer>();
  private lastCorrection = new Map<ComparePlayer, { at: number; cooldown: number }>();
  private tail: { time: number; at: number } | undefined;
  private revision = 0;
  constructor(
    private entries: Entry[],
    private now = () => performance.now(),
  ) {
    // Slot one sounds unless the caller named the audible side.
    if (entries.length && entries.every((entry) => entry.audible === undefined))
      entries[0].audible = true;
    this.applyAudio();
  }
  get requestedPlayback(): boolean {
    return this.wanted;
  }
  private get longest(): number {
    return Math.max(0, ...this.entries.map(({ video }) => length(video)));
  }
  /**
   * The unfinished side that ends first. Its length comes from kept_frames / fps when
   * both are known: container durations (both video.duration and host-probed kept_seconds)
   * can be longer than the kept video frames (e.g. audio overhang).
   */
  private get limit(): LimitInfo | undefined {
    return computeLimit(
      this.entries.map(({ video, fps, kept }) => ({
        duration: length(video),
        fps,
        kept,
      })),
    );
  }
  /** The longest side, or the shortest unfinished one: past its end there is nothing to compare. */
  get duration(): number {
    return this.limit?.seconds ?? this.longest;
  }
  /** An unfinished side ends the clock before the longest side does. */
  get limited(): boolean {
    return this.duration < this.longest;
  }
  /** Frames per second that stepping counts in. */
  private get frameRate(): number {
    return effectiveFrameRate(this.entries, this.limit);
  }
  get frames(): number {
    return totalFrames(this.entries, this.duration, this.limit);
  }
  get frame(): number {
    return frameIndexAt(this.time, this.frames, this.frameRate);
  }
  private stop(): void {
    this.playing = false;
    this.tail = undefined;
    this.revision++;
    for (const { video } of this.entries) video.pause();
  }
  pause(): void {
    const wasActive = this.playing || this.wanted;
    if (this.playing) {
      const master = this.entries[0]?.video;
      if (master && !this.tail && !master.seeking) {
        this.time = Math.max(0, Math.min(this.duration, master.currentTime));
      } else if (this.tail) {
        this.time = Math.max(
          0,
          Math.min(
            this.duration,
            this.tail.time + ((this.now() - this.tail.at) / 1000) * this.speed,
          ),
        );
      }
    } else if (wasActive) {
      const master = this.entries[0]?.video;
      if (master && !this.tail && !master.seeking && this.time < length(master)) {
        this.time = Math.max(0, Math.min(this.duration, master.currentTime));
      }
    }
    this.wanted = false;
    this.stop();
    if (wasActive && this.entries.length) {
      // Land in the middle of the paused frame mid-clip (or at an unfinished limit);
      // at natural EOF only pull stragglers forward so WebKit never hangs seeking at EOF.
      if (this.time < this.duration || this.limited) this.seekFrame(this.frame);
      else {
        this.time = this.duration;
        for (const { video } of this.entries) {
          const target = Math.min(this.duration, length(video));
          if (!video.seeking && Math.abs(video.currentTime - target) > 0.05) {
            this.correcting.delete(video);
            this.lastCorrection.delete(video);
            video.currentTime = target;
          }
        }
      }
    }
  }
  async play(): Promise<void> {
    this.wanted = true;
    if (
      !this.entries.length ||
      this.blocked.size ||
      this.entries.some(({ video }) => video.seeking || video.readyState < 3)
    )
      return;
    // At the end, or on the last frame of a span an unfinished side cut short: from the start.
    if (
      this.duration &&
      (this.time >= this.duration || (this.limited && this.frame >= this.frames - 1))
    ) {
      this.seek(0);
      if (this.entries.some(({ video }) => video.seeking || video.readyState < 3)) return;
    }
    this.playing = true;
    const revision = ++this.revision;
    const master = this.entries[0].video;
    if (this.time >= length(master)) this.tail = { time: this.time, at: this.now() };
    try {
      await Promise.all(
        this.entries.map(({ video }) => {
          video.playbackRate = this.speed;
          return this.time < length(video) ? video.play() : undefined;
        }),
      );
      if (revision !== this.revision && !this.playing)
        for (const { video } of this.entries) video.pause();
    } catch (error) {
      if (revision === this.revision) this.pause();
      throw error;
    }
  }
  seek(seconds: number): void {
    this.time = Math.max(0, Math.min(this.duration, seconds));
    this.tail =
      this.playing && this.entries[0] && this.time >= length(this.entries[0].video)
        ? { time: this.time, at: this.now() }
        : undefined;
    for (const { video } of this.entries) {
      const target = Math.min(this.time, length(video));
      if (Math.abs(video.currentTime - target) > 0.0001) {
        this.correcting.delete(video);
        this.lastCorrection.delete(video);
        video.currentTime = target;
      }
      if (this.time >= length(video)) video.pause();
    }
  }
  rate(value: number): void {
    if (this.playing) this.tick();
    this.speed = value;
    if (this.tail) this.tail = { time: this.time, at: this.now() };
    for (const { video } of this.entries) video.playbackRate = value;
  }
  /** One side sounds: the one given, or none. */
  audio(source: ComparePlayer | null): void {
    for (const entry of this.entries) entry.audible = entry.video === source;
    this.applyAudio();
  }
  private applyAudio(): void {
    for (const { video, audible } of this.entries) video.muted = !audible;
  }
  /**
   * The middle of a frame, never its edge: every side then shows the frame that holds
   * this time, whatever its own timestamps round to.
   */
  seekFrame(index: number): void {
    const frame = Math.max(0, Math.min(this.frames - 1, Math.round(index)));
    this.seek((frame + 0.5) / this.frameRate);
  }
  /** A seek from the timeline: the frame that holds this time. */
  scrub(seconds: number): void {
    this.seekFrame(Math.floor(Math.max(0, seconds) * this.frameRate + 1e-6));
  }
  step(direction: number): void {
    this.pause();
    this.seekFrame(this.frame + direction);
  }
  stall(video: ComparePlayer): void {
    // Ignore transient 'waiting' events emitted while a follower completes a drift-correction seek.
    const last = this.lastCorrection.get(video);
    if (
      this.correcting.has(video) &&
      (video.seeking || (last !== undefined && this.now() - last.at < 300))
    )
      return;
    if (this.playing) this.tick();
    this.blocked.add(video);
    this.stop();
  }
  async ready(video: ComparePlayer): Promise<void> {
    if (!video.seeking && video.readyState >= 3) this.blocked.delete(video);
    if (this.wanted && !this.playing && !this.blocked.size) {
      const now = this.now();
      for (const [index, { video: entry, fps }] of this.entries.entries()) {
        const target = Math.min(this.time, length(entry));
        const threshold = 0.5 / (fps > 0 ? fps : 30);
        const last = this.lastCorrection.get(entry);
        if (
          !entry.seeking &&
          Math.abs(entry.currentTime - target) > threshold &&
          (index === 0 || last === undefined || now - last.at >= 180)
        ) {
          if (index > 0) {
            this.lastCorrection.set(entry, { at: now, cooldown: last?.cooldown ?? 180 });
            this.correcting.add(entry);
          }
          entry.currentTime = target;
        }
      }
      await this.play();
    }
  }
  tick(): void {
    const master = this.entries[0]?.video;
    if (!master) return;
    const now = this.now();
    for (const { video } of this.entries) {
      if (!video.seeking && this.correcting.delete(video)) {
        // Measure the quiet window from when the seek finishes decoding so a slow seek
        // neither chains immediately into another seek nor locks out recovery for too long.
        const prev = this.lastCorrection.get(video);
        this.lastCorrection.set(video, { at: now, cooldown: prev?.cooldown ?? 180 });
      }
    }
    if (this.playing) {
      const unready = this.entries.filter(
        ({ video }) =>
          !this.correcting.has(video) &&
          (video.seeking || (video.readyState < 3 && this.time < length(video))),
      );
      const clockTime = this.tail
        ? this.tail.time + ((now - this.tail.at) / 1000) * this.speed
        : master.seeking
          ? this.time
          : master.currentTime;
      if (unready.length) {
        this.time = Math.min(this.duration, clockTime);
        for (const { video } of unready) this.blocked.add(video);
        this.stop();
        return;
      }
      // The first slot is the clock until it ends. Its last time then anchors a
      // monotonic tail so a longer follower can finish while slot one holds.
      if (!this.tail && !master.seeking && master.currentTime >= length(master))
        this.tail = { time: length(master), at: now };
      this.time = Math.min(
        this.duration,
        this.tail
          ? this.tail.time + ((now - this.tail.at) / 1000) * this.speed
          : master.seeking
            ? this.time
            : master.currentTime,
      );
    } else if (!this.wanted && !master.seeking && this.time < length(master))
      this.time = Math.min(this.duration, master.currentTime);
    for (const { video, fps } of this.entries.slice(1)) {
      const dur = length(video);
      const target = Math.min(this.time, dur);
      if (this.time >= dur) {
        video.pause();
        if (!video.seeking && Math.abs(video.currentTime - target) > 0.001)
          video.currentTime = target;
        continue;
      }
      if (video.seeking) continue;
      const halfFrame = 0.5 / (fps > 0 ? fps : 30);
      const drift = Math.abs(video.currentTime - target);
      // Rate-limit corrections below so normal decode jitter cannot cause a seek on every tick.
      const threshold = halfFrame;
      const last = this.lastCorrection.get(video);
      if (drift <= threshold) {
        if (last && !this.correcting.has(video) && now - last.at > 1000) {
          this.lastCorrection.delete(video);
        }
        continue;
      }
      if (this.playing) {
        // Back off repeated seeks under slow decode, but stay responsive when drift exceeds 160 ms.
        const wait = drift > 0.16 ? 180 : (last?.cooldown ?? 180);
        if (last && now - last.at < wait) continue;
        const cooldown = last ? Math.min(450, Math.round(last.cooldown * 1.8)) : 180;
        this.lastCorrection.set(video, { at: now, cooldown });
        this.correcting.add(video);
      }
      video.currentTime = target;
    }
    if (this.playing && this.time >= this.duration) this.pause();
  }
}

export interface CompareSlotTiming {
  fps: number;
  duration?: number;
  kept?: KeptRange;
}

/**
 * Single-clock transport for the combined Compare video, plus an optional muted-follower
 * `<audio>` element that never stalls or alters the video clock.
 */
export class SingleVideoCompareSync {
  time = 0;
  playing = false;
  speed = 1;
  private wanted = false;
  private blocked = false;
  private audioPlayer: ComparePlayer | null = null;
  private audioDuration = 0;
  private revision = 0;

  constructor(
    private video: ComparePlayer,
    private slots: CompareSlotTiming[],
  ) {
    this.video.muted = true;
  }

  get requestedPlayback(): boolean {
    return this.wanted;
  }

  private get longest(): number {
    return Math.max(length(this.video), ...this.slots.map((slot) => slot.duration || 0));
  }

  private get limit(): LimitInfo | undefined {
    const played = length(this.video);
    return computeLimit(
      this.slots.map((slot) => ({
        duration: played > 0 ? played : slot.duration || 0,
        fps: slot.fps,
        kept: slot.kept,
      })),
    );
  }

  get duration(): number {
    const limit = this.limit?.seconds;
    const played = length(this.video);
    return limit !== undefined ? limit : played;
  }

  get limited(): boolean {
    return this.duration > 0 && this.duration < this.longest;
  }

  private get frameRate(): number {
    return effectiveFrameRate(this.slots, this.limit);
  }

  get frames(): number {
    return totalFrames(this.slots, this.duration, this.limit);
  }

  get frame(): number {
    return frameIndexAt(this.time, this.frames, this.frameRate);
  }

  setAudio(player: ComparePlayer | null, duration = 0): void {
    if (this.audioPlayer && this.audioPlayer !== player) {
      this.audioPlayer.pause();
      this.audioPlayer.muted = true;
    }
    this.audioPlayer = player;
    this.audioDuration = duration;
    if (!player) return;
    player.muted = false;
    player.playbackRate = this.speed;
    this.syncAudio();
  }

  private audioCap(): number {
    const player = this.audioPlayer;
    if (!player) return 0;
    const playerLen = length(player);
    const maxAudio =
      this.audioDuration > 0 && playerLen > 0
        ? Math.min(this.audioDuration, playerLen)
        : playerLen || this.audioDuration;
    return Math.min(this.duration, maxAudio);
  }

  syncAudio(): void {
    const player = this.audioPlayer;
    if (!player) return;
    const cap = this.audioCap();
    if (cap <= 0 || this.time >= cap) {
      player.pause();
      return;
    }
    player.playbackRate = this.speed;
    if (!player.seeking && Math.abs(player.currentTime - this.time) > 0.06) {
      player.currentTime = Math.min(this.time, cap);
    }
    if (this.playing && !this.video.paused && player.paused && player.readyState >= 2) {
      void player.play().catch(() => {});
    }
  }

  private stop(): void {
    this.playing = false;
    this.revision++;
    this.video.pause();
    this.audioPlayer?.pause();
  }

  pause(): void {
    const wasActive = this.playing || this.wanted;
    if ((this.playing || wasActive) && !this.video.seeking && this.time < length(this.video)) {
      this.time = Math.max(0, Math.min(this.duration, this.video.currentTime));
    }
    this.wanted = false;
    this.stop();
    if (wasActive) {
      if (this.time < this.duration || this.limited) {
        this.seekFrame(this.frame);
      } else {
        this.time = this.duration;
      }
    }
  }

  finish(): void {
    if (!this.playing && !this.wanted) return;
    this.time = this.duration;
    this.wanted = false;
    this.stop();
    if (this.limited) this.seekFrame(this.frames - 1);
  }

  async play(): Promise<void> {
    this.wanted = true;
    this.video.muted = true;
    if (this.blocked || this.video.seeking || this.video.readyState < 3) return;
    if (
      this.duration &&
      (this.time >= this.duration || (this.limited && this.frame >= this.frames - 1))
    ) {
      this.seek(0);
      if (this.video.seeking || this.video.readyState < 3) return;
    }
    this.playing = true;
    const revision = ++this.revision;
    this.video.playbackRate = this.speed;
    try {
      await this.video.play();
      if (revision !== this.revision && !this.playing) {
        this.video.pause();
        return;
      }
      this.syncAudio();
    } catch (error) {
      if (revision === this.revision) this.pause();
      throw error;
    }
  }

  seek(seconds: number): void {
    this.time = Math.max(0, Math.min(this.duration, seconds));
    const target = Math.min(this.time, length(this.video));
    if (Math.abs(this.video.currentTime - target) > 0.0001) {
      this.video.currentTime = target;
    }
    if (this.time >= length(this.video)) this.video.pause();
    const audio = this.audioPlayer;
    if (audio) {
      const cap = this.audioCap();
      if (cap <= 0 || this.time >= cap) {
        audio.pause();
      } else if (Math.abs(audio.currentTime - this.time) > 0.0001) {
        audio.currentTime = Math.min(this.time, cap);
      }
    }
  }

  rate(value: number): void {
    if (this.playing) this.tick();
    this.speed = value;
    this.video.playbackRate = value;
    if (this.audioPlayer) this.audioPlayer.playbackRate = value;
  }

  seekFrame(index: number): void {
    const frame = Math.max(0, Math.min(this.frames - 1, Math.round(index)));
    this.seek((frame + 0.5) / this.frameRate);
  }

  scrub(seconds: number): void {
    this.seekFrame(Math.floor(Math.max(0, seconds) * this.frameRate + 1e-6));
  }

  step(direction: number): void {
    this.pause();
    this.seekFrame(this.frame + direction);
  }

  stall(video: ComparePlayer): void {
    if (video !== this.video) return;
    if (this.playing) this.tick();
    this.blocked = true;
    this.stop();
  }

  async ready(video: ComparePlayer): Promise<void> {
    if (video !== this.video) return;
    if (!video.seeking && video.readyState >= 3) this.blocked = false;
    if (this.wanted && !this.playing && !this.blocked) {
      await this.play();
    }
  }

  tick(): void {
    if (this.playing) {
      if (this.video.seeking || (this.video.readyState < 3 && this.time < length(this.video))) {
        this.time = Math.min(this.duration, this.video.currentTime);
        this.blocked = true;
        this.stop();
        return;
      }
      this.time = Math.min(this.duration, this.video.currentTime);
      this.syncAudio();
      if (this.time >= this.duration) this.pause();
    } else if (!this.wanted && !this.video.seeking && this.time < length(this.video)) {
      this.time = Math.min(this.duration, this.video.currentTime);
    }
  }
}
