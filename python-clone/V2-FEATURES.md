# film_strip_inverter_v2 — features & decisions

Terse running list. v2 = the standalone-inverter merge target. Base: `film_strip_inverter.py`.
Run: `python3 film_strip_inverter_v2.py`.

## Base (inherited from film_strip_inverter.py)
- Multi-image film strip: thumbnails, drag-drop, Open many, click-to-switch.
- Per-image `Settings` (edits preserved per image).
- Two-stage pipeline: stretch cache (`base_f`) + cheap tone recompute.
- Proxy 2000px preview; full-res save; resolution-independent math.
- Engine: neg-invert lineage (`core.invert.stretch` + `core.tone` + `core.local_contrast`).

## Changed in v2
- Sliders
  - slider + read-only value label (spinboxes removed — unused).
- Conversion mode
  - explicit **LIN / LOG / PSL** buttons (was a cycle button).
  - switching mode clears picked B/W points (working space changes); keeps tone + tweaks.
- Analysis overlay
  - **Show/Hide Analysis Area** toggle (Stretch section).
  - yellow inset rectangle = central percentile-analysis region.
  - inset tracks **Analysis Buffer %** slider live.
- Point picking — **hybrid**
  - key-hold `B`/`W`/`N` + click (power-user, retained).
  - explicit arm buttons: **Set Black / Set White / Set Neutral**.
  - armed feedback: crosshair cursor + blue status line ("▶ Picking … — click the image").
  - a pick auto-disarms.
  - **Auto** button = revert to auto percentiles (clears overrides + tweaks); replaced "Reset BP/WP".
- Save — **hybrid**
  - **Save** button + **Ctrl+S** → auto-name `_inv` next to source, current image.
  - **Save As…** → dialog (pick path), current image.
  - **Save All** → auto-name `_inv` every loaded image (film-strip batch).
  - 16-bit preserved for TIFF/PNG when source was 16-bit.

## Inherited, unchanged
- Tone: brightness, gamma, s-curve, local contrast (CLAHE), temperature, tint, saturation.
- Stretch: analysis buffer %, BP tweak, WP tweak, soft clip.
- Orientation: Flip H, Rotate CW (D4).
- Readout: B/W/N colour swatches; info panel (BP/WP values, auto/click source, dims, index).
- Mode-switch behaviour; Reset (all) ; Clear (whole session).

## Design principles
- Prefer visible controls + on-screen state over hidden gestures (user gets distracted).
  - hidden gestures (key-hold) are *additive shortcuts*, never the only path.

## Open / not yet done
- Engine choice: neg-invert pipeline vs RapidRawMod (`rapidraw_negative.py`) — see
  `../DOCS_MOD/FUTURES-direction-and-choices.md`.
- Add a true exposure/brightness slider (begged-for in neg-invert).
- Decide surviving mode(s) (lin/log/psl).
- Overlay alignment over rotated/letterboxed image: verify by eye.
