# Comparing image results

Every run of an image saves its own file. To compare finished results, open
**Outputs** in the left pane and tick two to four items. Results are grouped by
input image, with its **Original** first. A picked row shows its slot number,
in the order picked. Choose **Compare these** (it names how many are picked) or
open the **Compare** tab above the main area. Switch back to **Preview** to work
there; the picks stay in Compare for the session.

The list shows finished outputs, their model and, when recorded, quality,
scale and run time. A result whose file was moved or deleted is marked **File
missing** and cannot be newly selected. Failed, cancelled and interrupted runs
are not shown in Outputs. Removing an image from the queue or clearing the
queue removes its history, but leaves its result files in place; adding the
image again does not restore that history.

## Slider and boxes

With two versions of the same input, **Slider** puts slot 1 on the left and
slot 2 on the right. Drag the divider to compare them. Use the swap button
between the first two slots to exchange sides. With three or four picks,
**Boxes** shows every slot, normally in a two-column grid; on a very wide
canvas they can appear in one row. The layout switches to boxes when a third
item is added. You can still select Slider to compare the first two, and use a
slot's arrow to move slot 3 or 4 into slot 2. Items from different inputs can
be compared in boxes; the slider requires its first two slots to belong to the
same input.

Remove a pick with its **×** button or untick it in Outputs; **Clear** removes
all picks. Zoom and pan are linked across boxes. Use the zoom controls or the
mouse wheel, drag to pan, and choose **Fit** to reset zoom. At fit size Compare
uses previews; when zoom exceeds a preview's available detail, LocalSR loads
the visible region from the saved file at full resolution. Loading and file
errors are shown on the affected item.

## Preview and result history

Preview continues to show the selected image and its current result, including
live tiles while it runs. **View > Previous Result** and **Next Result**
(Command-Option-Left and -Right on a Mac, Ctrl+Alt elsewhere) move through
available saved results in Preview. A new run returns Preview to the newest
result. **Open** and **Reveal** in the footer act on the result shown there.
Compare picks are independent of Preview's selected result.

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
same settings; otherwise it names the models that have not. Compare the saved
outputs, then use **Make my Best** or **Use for this job** in the library for
the one you prefer.

The queue row of the image reads like `Running SwinIR-M · 2 queued`.
**Cancel runs for this image** stops this image's running model and cancels its
queued ones; other images stay queued, and finished results stay. **Cancel
queue** (File > Cancel Queue, Command-.) still stops everything.

Finder actions and the command line still start one ordinary run.
