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
type Entry = { video: ComparePlayer; fps: number; audible?: boolean };
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
    this.mute(false);
  }
  get requestedPlayback(): boolean {
    return this.wanted;
  }
  get duration(): number {
    return Math.max(0, ...this.entries.map(({ video }) => length(video)));
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
    if (this.time >= this.duration && this.duration) this.seek(0);
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
  mute(muted: boolean): void {
    this.entries.forEach(({ video, audible }, index) => {
      video.muted = index > 0 || audible === false || muted;
    });
  }
  step(direction: number): void {
    this.pause();
    this.seek(this.time + direction / Math.max(1, ...this.entries.map((e) => e.fps || 30)));
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
    if (this.playing && this.time >= this.duration) this.pause();
  }
}
