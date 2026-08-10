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

- Use the operating-system title bar as the only static header.
- Put import, task, recipe, model, scale, output, device, and Advanced controls in the leading pane.
- Keep the canvas visually dominant in the center.
- Use the trailing pane as a read-only inspector for the selected input, effective recipe, model,
  output, live hardware pressure, estimates, warnings, and result state.
- Keep the persistent progress/cancel/manual-start strip at the bottom because it represents an active
  operation, not branding or document metadata.
- Reveal Quick and Best only after task selection. Treat them as immediate commands: resolve the
  complete recipe, download and verify a missing model if necessary, then start without another click.

## Consequences

- The interface has one clear direction of work from the left pane into the central document.
- Diagnostics remain visible without looking editable or competing with task configuration.
- Manual controls remain available for expert use, while the common path requires task selection plus
  one recipe click.
- Automatic downloads must retain cancellation, atomic installation, fixed byte counts, pinned URLs,
  and SHA-256 verification before an automatic job can start.
