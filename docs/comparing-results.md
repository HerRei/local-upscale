# Comparing image results

Every run of an image keeps its own file. Once an image has two saved results,
the preview shows **Showing** and **against** under the file name. Choose a
result, or the Original, on either side. Choosing for one side what the other
side holds swaps the two.

## What you see

Both sides are drawn over the same part of the image, whatever their scale, so
a 2× and a 4× result line up. Each side is a preview reduced to at most 2048
pixels; **1:1** means one source pixel per screen pixel, not one output pixel.
**Open** shows the full saved file. The label on each side of the divider names
the model and the size of the saved file against the source, such as
`SwinIR-M · 2× output`.

With a saved result on the left the views are **Reference / Split / Showing**.
Holding **M** always shows the real Original, labelled Original. A side that is
still decoding says so, and a side whose preview failed shows the error; neither
shows the Original in its place.

An Edit result can be Showing. If its framing differs from the source it is
shown on its own, without a divider, because a split would stretch it over the
source.

Without a choice, Showing follows the newest saved result. While the image is
being processed its live tiles stay in view until you choose a saved result;
results that are already finished stay available the whole time. A choice holds
for the session, through later runs and when you come back to the image.
Starting a new run of the image (Start or Try others…) goes back to the newest
result. Restarting LocalSR starts again from the newest result.

**View > Previous Result** and **Next Result** (Command-Option-Left and -Right
on a Mac, Ctrl+Alt elsewhere) move Showing to an older or newer result. They
skip results whose file is gone and the one on the other side, and stop at the
oldest and newest.

**Open** and **Reveal** in the footer act on Showing. In the list, the
highlighted row has its own Open and Reveal, so the other side is reachable too.

## The list

Each row names the model, the quality or recipe it was queued with, the scale,
the run time, the file and when it finished. **Run time** includes loading the
model and saving the file. The configured stages are listed; a stage that was
skipped says so in the row's note (for example `Blur fix skipped`). Jobs from
before 0.3.7 did not record a quality or recipe and say **Settings unavailable**.

A result whose file was moved or deleted stays in the list as **File missing**
and cannot be chosen. Runs that failed, were cancelled or were interrupted are
listed below the results with their reason. The list shows the newest 50
results; older files stay in the output folder, and **Reveal** on the last line
opens the folder of the oldest result listed.

Removing an image from the queue, or clearing the queue, removes its history;
the result files stay where they are. Adding the image again does not bring the
history back.

## Try others…

For a single image in Upscale or Restore, **Try others…** under the model's
**Change…** opens the library with a checkbox per model. Pick **2 to 4
installed models**. Each one runs the whole image with the settings the plan
card shows, one after the other, as ordinary jobs behind any work already
queued, and saves its own file in the output folder with the model in the name
(`photo_upscaled_4x_swinir-m-real-x4-gan.png`, `photo_denoised_nafnet-sidd-width64.png`).
Each model gets at least the tile overlap it needs, as when you choose it in
the library.

A model that cannot keep the scale, the fix or Faces cannot be picked, and the
whole request is refused if one of them stops fitting before it is queued;
LocalSR does not change those settings to make a model fit. In Restore the
candidates are 1× models that fix the selected problem. The summary gives a
total time only when every model has been measured on this computer with the
same settings; otherwise it names the models that have not. The last result is
not a winner: compare them under Showing, then use **Make my Best** or **Use
for this job** in the library for the one you prefer.

The queue row of the image reads like `Running SwinIR-M · 2 queued`.
**Cancel runs for this image** stops this image's running model and cancels its
queued ones; other images stay queued, and finished results stay. **Cancel
queue** (File > Cancel Queue, Command-.) still stops everything.

Finder actions and the command line still start one ordinary run.
