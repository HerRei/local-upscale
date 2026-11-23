# Unfinished results

A job that is stopped can keep what it finished: the first frames of a video, or the
finished tiles of a photo. This note records how the window shows such a result and why.
It covers the 0.6.0 work on `w/060-partial-ui`.

## What a kept result is

- **Video:** a playable file with the frames that were finished (`kept_seconds`,
  `kept_frames`).
- **Photo:** a full-size picture. The finished tiles are enhanced; the rest is the
  original, resized to the output size (`finished_tiles`, `total_tiles`). The file is named
  `<requested name>_partial.<ext>`.
- **Edit:** nothing. An edit has steps, not tiles, and a half-denoised picture is no
  result. A stopped edit is discarded.

Both kinds are an ordinary entry under Outputs with `partial: true`. The same word marks
them everywhere: **Unfinished**.

## Decisions

### One mark, three places

`Unfinished · <how much>` appears in the Outputs row, on the preview (a chip in the top-left
corner, where the Original chip sits, which never shows at the same time) and under Compare
(a tag on the first line of the slot's details). The amount uses the unit the user can check:

| Kept | Mark |
| --- | --- |
| Video, a second or more | `Unfinished · 0:05 of 0:12` |
| Video, under a second | `Unfinished · 2 of 54 frames` (or `2 frames` without a total) |
| Photo | `Unfinished · 12 of 40 tiles` |
| Nothing recorded | `Unfinished` |

Under a second a clock reads `0:00`, which says nothing and looked like a bug in the
candidate ("0:00 of 0:02" for two frames). Frames are exact there. The total in frames is
the source's length times the result's frame rate. Kept seconds are rounded down: a part of
42.9 s has 42 full seconds.

The status line after a stop follows the same rule: `Stopped, kept 2 frames`,
`Stopped, kept 0:42`, `Stopped, kept 12 of 40 tiles`. The line below it no longer counts a
kept part as finished: `Stopped. 0 of 1 finished, 1 kept unfinished.`

### The finished tiles of a photo

The picture itself must stay readable, because the reason to keep it is to judge the
finished part. So the marking is in two levels:

- **At rest:** one thin dashed line where the finished tiles end. It is drawn light on a
  dark shadow so it reads on any picture. Tile borders inside the finished part are not
  drawn, and the picture's own edge is not part of the line.
- **Shown:** the part that was not reached is dimmed and every finished tile gets a thin
  outline. The finished tiles keep their real pixels, so they seem to light up.

The mark is the switch. Hovering it, or focusing it with the keyboard, shows the tiles; a
click (or Space/Enter) keeps them shown until the next click. Hovering the picture does not
switch it: zoomed in, the pointer is always over the picture, and a marking that cannot be
moved away from would cover what the user came to look at.

The marks follow the result, not the stage: in a split they are cut at the divider like the
picture they belong to, and the Original view has none. They are drawn in window pixels
beside the zoomed stage (`UnfinishedTiles.svelte`), so the lines stay one pixel wide and
sharp at every zoom. Inside the stage they would be scaled with it as one composited layer.

The geometry (`lib/unfinished.ts`, `tileMarks`) does not assume a regular grid. Tile edges
cut the picture into cells, a cell counts as finished when a tile covers it, and the line
runs between finished and unfinished cells. Overlapping tiles and tiles past the edge are
handled; beyond 250 000 cells the line is left out and only the dimming remains.

### Stopping a photo

The question is the same dialog as for a video, and it is asked only when there is
something to keep: the host says the job can be kept (`runtime.can_keep_partial`) and at
least one tile of the running stage is finished. The window counts the tiles from the
engine's `progress` messages (`runtime.tiles_completed`); an edit's steps are not counted.

- Title: `Stop <file name>?`
- Photo: `12 of 40 tiles are finished. If you keep it, the photo is saved at full size with
  the rest left as in the original.`
- Video: `0:05 of 0:12 is finished.` or, under a second, `2 of 288 frames are finished.`
- Buttons: **Discard**, **Keep finished part** (default), and **Don't ask again**.

Settings, "When you stop a job": `A video keeps its finished frames as a playable video; a
photo keeps its finished tiles, with the rest left as in the original. Both are marked
Unfinished. A stopped edit is always discarded; finished files in a queue always stay.`

### Comparing an unfinished video

Past the kept part there is nothing to compare, so the clock ends there: with an unfinished
side in Compare the timeline spans the shortest kept part instead of the longest video. A
line under the transport says so (`Playback ends where the unfinished result ends, after 2
frames.`). Without an unfinished side the timeline spans the longest video as before.

Frames are compared one by one:

- A step, and every seek from the timeline, lands in the **middle** of a frame. On a frame
  edge two files can round the same time to different frames; in the middle each side shows
  the frame that holds that time.
- The grid is the fastest side's frame rate. Where the host counted the kept frames, the
  grid is `kept_frames / kept length` instead, the file's own rate. A nominal 30 fps would
  count a third frame in a clip of two.
- At the end of playback every side is put on the last kept frame; the longer side had
  already run past it.
- The transport has **Previous frame** and **Next frame** buttons beside Play (Left and
  Right still work in Compare). At an end the button is dimmed but keeps the focus, so the
  other arrow key still works.

Under a second the transport counts frames: `Frame 1`, a scrubber with one stop per frame,
`2 frames`. The preview's own player (`VideoComparison.svelte`) does the same; it already
ended at the shorter of its two sides.

### Audio in Compare

One side sounds. The transport has a small group, `Audio 1 2 3 4 Off`, with the same numbers
the slots carry everywhere else. Slot 1 sounds at first, as before. The choice belongs to
the result, not to the position: it follows a swap, and falls back to slot 1 when its result
leaves. This replaces the Mute button.

### The details under Compare

The strip is a title and four lines per slot, always (97 px at the standard text size, the
height of the two-slot strip in the candidate). It no longer wraps, and all slots share one
row, also when four boxes stand two by two.

- Line 1 is the Unfinished tag when there is one.
- **Models** and **Fixes**, which can be long, take a line each.
- The short facts (scale, output size, time, device, date) share the remaining lines in
  their order.
- A line that does not fit is cut with an ellipsis. A slot with a cut line gets a **More**
  button on its title line; hovering the slot, focusing More, or a click on it opens the
  full list above the strip. Escape closes the list and leaves Compare open.

A count like "+3" was not used: it would hide which facts are missing, while a cut line
still shows the start of each one.

### The "Video · processed locally" chip

Removed. It said nothing the user can act on, and the corner is now the place of the
Unfinished mark.

## States

| Where | State | Shows |
| --- | --- | --- |
| Outputs row | unfinished | the mark first in the detail line |
| Preview, photo | Split or Enhanced | chip; line at rest; tiles when the chip is hovered, focused or pressed |
| Preview, photo | Original, or M held | Original chip only |
| Preview, pair of results | either side unfinished | each side's tiles on its side of the divider; one chip |
| Preview, video | result showing | chip (a note, not a switch); frame transport under a second |
| Compare, photos | slider or boxes | tag in the slot's details; tiles over that slot's picture |
| Compare, videos | any side unfinished | tag; timeline ends at the kept part; note under the transport |
| Stop dialog | photo with a finished tile, video with a finished frame | the question |
| Stop dialog | nothing finished, an edit, or a host that cannot keep | no question; the job is cancelled |

Motion is two opacity changes of 140 ms, off with reduced motion. Colours on the picture
(`--unfinished-veil`, `--unfinished-line`, `--unfinished-line-shadow`) are the same in every
theme because they sit on the photo, not on the interface; chips and tags use the existing
chip tokens.

## What the host has to deliver

The window reads these optional fields and works without them (it then shows the plain word
Unfinished and no tiles):

- `ResultSummary`: `partial`, `kept_seconds`, `kept_frames`, `finished_tiles`,
  `total_tiles`.
- The `job_completed` / `video_job_completed` message of a kept job: `partial`, and the
  same counts (`kept_seconds`, `kept_frames`; `finished_tiles` as the list or its length,
  `total_tiles`) for the status line.
- `job_started.can_keep_partial` (and `runtime.can_keep_partial`) true for a photo job the
  host can keep.
