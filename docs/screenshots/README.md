# Screenshots

Real captures from a live Hermes Desktop render (Playwright driving the
actual Desktop app on the operator PC, 12 Nov 2026). No mockups, no
invented images.

- `_01-main.png` — main chat view with the floating widget at the
  bottom-right (timer running, manual mode).
- `_float.png` — the floating widget dragged to the centre of the
  screen, fully visible: FOCUS label, thin progress ring, 21:06
  countdown, three icon-only controls, collapsed "Session stats".
- `_page.png` — the full Focus Widget page: big countdown, phase
  label, "Manual mode — wall-clock timer running", three controls,
  collapsed stats.
- `_settings.png` — the settings page (25/5/15/4, agent-aware toggle,
  idle threshold 120 s, auto-start options, sound, "Clear statistics"
  and "Save settings").
- `_chip.png` — status-bar chip popover (short stats + "Open full
  page"), bottom-right clip.
- `_banner.png` — the full Focus Widget page, used as the catalog
  banner.

Capture note: the operator's WSLg session runs at device pixel ratio
1.1, so a full-window Playwright screenshot covers only the top-left
~1432x859 layout pixels — the bottom-right float and chip sit at the
edge of (or just beyond) the captured frame even though they render
fully inside the app viewport. That is a capture artifact, not a
widget defect: the DOM rects were verified fully inside the viewport
(float 1347–1559 x 647–929 within 1575x945, one float, stable over
21 s), the float was dragged to the centre and back (see `_float.png`),
and the chip popover was verified open with its stats rows readable.
The final visual check on the user's own Desktop remains open.
