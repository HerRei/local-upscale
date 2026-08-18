# Architecture Decision Record: 0003 - Workspace Information Architecture

## Status

Accepted.

## Context

The first Slint layout duplicated the operating-system title bar with a static application header
and split job-changing settings across both sides of the canvas. Quick and Best looked like ordinary
configuration buttons even though they represent complete recipes. This weakened hierarchy and made
the right pane ambiguous.

## Evidence

- GNOME's utility-pane guidance places controls that affect the main view on the left and subordinate
  information on the right.
- Apple's sidebar guidance recommends succinct groups, disclosure controls for vertical density, and
  avoiding critical actions at the bottom of a sidebar.
- Adobe's professional image workspaces keep the document/image central, group source material and
  tools in side panels, and allow less-used panels to collapse.
- Microsoft's property-inspector guidance describes a persistent pane for frequently inspected
  properties of the current selection, while its command guidance keeps primary actions visible and
  consistently located.

Primary references:

- https://developer.gnome.org/hig/patterns/containers/utility-panes.html
- https://developer.apple.com/design/human-interface-guidelines/sidebars
- https://helpx.adobe.com/lightroom-classic/desktop/workspace/workspace-basics.html
- https://learn.microsoft.com/en-us/windows/win32/uxguide/win-property-win

## Decision

- Use the operating-system title bar as the only branded header. A slim in-content command bar holds
  frequent workspace commands such as Add Images, Compare, and compact-window pane navigation.
- Use a leading Media pane for Single/Batch selection, import, queue selection, and removal.
- Keep the image canvas visually dominant in the center and retain aspect-correct preview,
  zoom/pan, progressive tiles, and completed-result comparison.
- Use one trailing Enhance pane for the decisions that change a job. Put Upscale/Denoise first,
  immediate Quick/Best commands second, and model/output/hardware controls behind one Advanced
  disclosure.
- Keep contextual facts, uncertainty-aware estimates, warnings, and the single manual Start action
  in the Enhance pane. Keep the bottom status area informational, with result and cancellation
  actions only when they are relevant.
- Reveal Quick and Best only after task selection. Treat them as immediate commands: resolve the
  complete recipe, download and verify a missing model if necessary, then start without another click.
- Adapt by content fit rather than display DPI: show all three panes at 1280 logical pixels and
  above, Media plus Preview at medium widths, and mutually exclusive Media/Preview/Enhance pages
  below 920 logical pixels. Rely on Slint's native logical-pixel scaling instead of forcing a global
  scale factor.

## Consequences

- The interface has one clear direction of work: choose media, inspect it, choose the enhancement,
  then start.
- The media queue and editable job controls no longer compete inside one scrolling pane.
- Manual controls remain available for expert use, while the common path requires task selection plus
  one plainly labelled recipe click.
- Small and split-screen windows retain the complete workflow without shrinking text or controls.
- Automatic downloads must retain cancellation, atomic installation, fixed byte counts, pinned URLs,
  and SHA-256 verification before an automatic job can start.
