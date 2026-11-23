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
const length = (video: ComparePlayer) =>
  Number.isFinite(video.duration) ? Math.max(0, video.duration) : 0;
export class CompareVideoSync {
  time = 0;
  playing = false;
  speed = 1;
  private wanted = false;
  private blocked = new Set<ComparePlayer>();
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
   * The unfinished side that ends first. Its length is the host's, measured in the file:
   * a player can report a longer one (an audio tail, a rounded last frame), and frames
   * counted against that would not be the file's frames.
   */
  private get limit(): { seconds: number; frames: number; rate: number } | undefined {
    let first: { seconds: number; frames: number; rate: number } | undefined;
    for (const { video, kept } of this.entries) {
      if (!kept) continue;
      const played = length(video);
      const measured = kept.seconds && kept.seconds > 0 ? kept.seconds : played;
      const seconds = played > 0 ? Math.min(measured, played) : measured;
      if (!(seconds > 0) || (first && first.seconds <= seconds)) continue;
      const frames = kept.frames && kept.frames > 0 ? Math.round(kept.frames) : 0;
      first = { seconds, frames, rate: frames ? frames / measured : 0 };
    }
    return first;
  }
  /** The longest side, or the shortest unfinished one: past its end there is nothing to compare. */
  get duration(): number {
    return this.limit?.seconds ?? this.longest;
  }
  /** An unfinished side ends the clock before the longest side does. */
  get limited(): boolean {
    return this.duration < this.longest;
  }
  /**
   * How many grid frames one frame of the limiting side holds. Its own rate is exact; a
   * side that runs a whole multiple faster (an interpolated result) refines the grid,
   * while a nominal rate that is only a little off (30 for 29.97) does not.
   */
  private refinement(rate: number): number {
    const fastest = Math.max(1, ...this.entries.map((entry) => entry.fps || 30));
    return Math.max(1, Math.round(fastest / rate));
  }
  /** Frames per second that stepping counts in. */
  private get frameRate(): number {
    const rate = this.limit?.rate;
    return rate
      ? rate * this.refinement(rate)
      : Math.max(1, ...this.entries.map((entry) => entry.fps || 30));
  }
  get frames(): number {
    const limit = this.limit;
    return limit?.rate
      ? limit.frames * this.refinement(limit.rate)
      : Math.max(1, Math.ceil(this.duration * this.frameRate - 1e-6));
  }
  get frame(): number {
    return Math.max(0, Math.min(this.frames - 1, Math.floor(this.time * this.frameRate + 1e-6)));
  }
  private stop(): void {
    this.playing = false;
    this.tail = undefined;
    this.revision++;
    for (const { video } of this.entries) video.pause();
  }
  pause(): void {
    this.wanted = false;
    this.stop();
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
    )
      this.seek(0);
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
      if (Math.abs(video.currentTime - target) > 0.000001) video.currentTime = target;
      if (this.time >= length(video)) video.pause();
    }
  }
  rate(value: number): void {
    this.tick();
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
    this.tick();
    this.blocked.add(video);
    this.stop();
  }
  async ready(video: ComparePlayer): Promise<void> {
    if (!video.seeking && video.readyState >= 3) this.blocked.delete(video);
    if (this.wanted && !this.playing && !this.blocked.size) {
      this.seek(this.time);
      await this.play();
    }
  }
  tick(): void {
    const master = this.entries[0]?.video;
    if (!master) return;
    if (this.playing) {
      const unready = this.entries.filter(
        ({ video }) => video.seeking || (video.readyState < 3 && this.time < length(video)),
      );
      if (unready.length) {
        this.time = Math.min(
          this.duration,
          this.tail
            ? this.tail.time + ((this.now() - this.tail.at) / 1000) * this.speed
            : master.currentTime,
        );
        for (const { video } of unready) this.blocked.add(video);
        this.stop();
        return;
      }
      // The first slot is the clock until it ends. Its last time then anchors a
      // monotonic tail so a longer follower can finish while slot one holds.
      if (!this.tail && master.currentTime >= length(master))
        this.tail = { time: length(master), at: this.now() };
      this.time = Math.min(
        this.duration,
        this.tail
          ? this.tail.time + ((this.now() - this.tail.at) / 1000) * this.speed
          : master.currentTime,
      );
    } else if (!this.wanted && this.time < length(master)) this.time = master.currentTime;
    for (const { video, fps } of this.entries.slice(1)) {
      const target = Math.min(this.time, length(video));
      if (Math.abs(video.currentTime - target) > 0.5 / (fps > 0 ? fps : 30))
        video.currentTime = target;
      if (this.time >= length(video)) video.pause();
    }
    if (this.playing && this.time >= this.duration) {
      const limited = this.limited;
      this.pause();
      // The longer sides ran a frame past the kept part before this check.
      if (limited) this.seekFrame(this.frames - 1);
    }
  }
}
