# RapidRawMod — Backlog / Notes

Parked ideas to revisit later. Not yet implemented.

---

## A. Main-editor White Balance picker is disabled when WGPU is on

**Status:** parked. Use the toggle workaround for now.

**What happens:** In the *main* editor, the neutral-point (white-balance) eyedropper
is greyed out with the message **"WB Picker: Disable WGPU in Settings."**

**Why:** The picker reads its color from a CPU-side preview image (`finalPreviewUrl`
in `src/components/panel/editor/ImageCanvas.tsx`, `handleWbClick` ~line 1359). When
**WGPU Direct Rendering** is ON (Settings → Processing → "Enable Direct WGPU
Render", default ON on macOS), the app renders the live preview straight to a GPU
surface and **stops keeping that CPU preview fresh** — which is exactly the speed
win. So the picker has no reliable pixels to read, and it's hard-disabled
(`disabled={isWgpuEnabled}` in `src/components/adjustments/Color.tsx:479`).

**Workaround (no code):** Settings → Processing → turn OFF "Enable Direct WGPU
Render" → click neutral point (one shot) → turn it back ON. You're only slow during
the pick itself.

**Possible seamless fix (NOT started):** Add a small Rust command that samples the
processed color at a click coordinate on the backend (which always has the real
pixels), so the picker no longer depends on `finalPreviewUrl` or the WGPU setting.
- ⚠️ Lives in the **main rendering module**, not our negative converter. Bigger
  surface area, higher merge-conflict risk on upstream updates. Outside the
  "keep mods in the inversion module" boundary. Only do this if the toggle
  workflow becomes genuinely annoying.

---

## B. Add a neutral-point picker to the NEGATIVE CONVERSION module ⭐

**Status:** parked, but this is the attractive one — clean and self-contained.

**Why it's easy here (unlike A):** The Negative Conversion modal does NOT use WGPU.
Its preview is a plain CPU-side base64 JPEG (`previewUrl`) shown in a normal
`<img>`. The pixels are directly readable in the frontend, so a click-to-pick
feature stays entirely inside **our two already-modded files**
(`negative_conversion.rs` + `NegativeConversionModal.tsx`) — no main-module changes,
no WGPU concern, minimal upstream-conflict risk.

**Goal:** Click a spot that should be neutral grey → auto-adjust `red_weight` /
`green_weight` / `blue_weight` so that spot becomes neutral.

**Approach sketch:**
- The weights scale the normalized per-channel values *before* the tone curve
  (`n_r *= red_weight`, etc. in `run_pipeline`). Output is neutral at a pixel iff
  the pre-curve values are equal there: `w_r·n_r = w_g·n_g = w_b·n_b`.
- Cleanest: do it in the **backend**. Add a Tauri command that takes the click
  coordinate (normalized 0–1) + current params, recovers that pixel's pre-curve
  channel values (it already computes `log_pixels` and `bounds`), and solves for
  the weights — e.g. anchor green, set `w_r = w_g·(n_g/n_r)`, `w_b = w_g·(n_g/n_b)`.
  Return the three weights; the modal updates its sliders and re-previews.
- Frontend: make the preview `<img>` clickable when a "pick neutral" button is
  active (mirror the crosshair-cursor pattern), convert click → normalized coords,
  invoke the new command, `setParams` with the returned weights.
- Consider averaging a small patch (e.g. 5×5) around the click for stability, like
  the main editor's picker does.

**When built:** document it as `MOD-CHANGES-negative-neutral-picker.md`, same style
as the gamma and B/W-points records.

---

## C. Port neg-invert's B/W point-pick + tweak into the negative module ⭐⭐

**Status:** ✅ DONE (2026-06-11). See `MOD-CHANGES-negative-bw-pick.md`.

**Source:** `~/myProjects/neg-invert/gui/test_inverter.py` + `core/invert.py:stretch`.

**What it does there:**
- **Hidden B/W clicks:** hold keyboard `B` or `W` + click the image. Samples that
  pixel's RGB, converts to post-inversion working space (`-log10(px)` in log mode,
  `1-px` in lin/psl), stores it as a **per-channel (3,) vector** `bp_base` / `wp_base`.
  (`mousePressEvent` → `_on_bw_clicked`, lines 250-256, 553-569.)
- **Two tweaks** (`_run_stretch` 155-160):
  `rng = wp_base - bp_base`; `bp = bp_base + bp_tweak·rng`; `wp = wp_base + wp_tweak·rng`.
- **Direction is preserved EXACTLY:** `wp_new - bp_new = rng·(1 + t_wp - t_bp)`, always a
  scalar multiple of the original axis. Endpoints just slide along the black→white
  line. ("Good enough for small tweaks" only refers to knee/clip distortion at the
  extremes, not the color direction.)

**Why it ports cleanly:** the working space + normalization are already identical in
RapidRawMod's `negative_conversion.rs`:
- `work = -log10(src)` == `log_pixels = -log10(v)`
- auto percentiles == `analyze_bounds` min/max
- `stretch`'s `(v-bp)/(wp-bp)` == `(density - min)/(max - min)`

**What to add:**
- Rust: params `bp_override: Option<[f32;3]>`, `wp_override: Option<[f32;3]>`
  (density space, None=auto), `bp_tweak: f32`, `wp_tweak: f32`. In `run_pipeline`
  swap `bounds.min/max` for `base ± tweak·rng`. New `#[tauri::command]
  sample_negative_point(path, x, y)` reading the cached downscaled negative →
  returns `-log10` density at that pixel.
- Frontend: "Set black / Set white" buttons (or B/W key-hold), click handler on the
  preview `<img>` (CPU-side, no WGPU issue — see item A), two tweak sliders, a reset.

**Conflict surface:** our two modded files + **one line** in `lib.rs` to register the
new command. Document as `MOD-CHANGES-negative-bw-pick.md` when built.

---

## D. Decide which conversion mode to keep (lin / psl / log)

**Status:** open — experimental modes added (`MOD-CHANGES-conversion-modes.md`).
The 3-mode selector is a test harness, not a final design. Once you settle on one:
collapse `ConversionMode` to the survivor, remove the selector UI, simplify
`to_working`/`from_working`, and trim `RapidRaw-negative-conversion-modes.md` +
the algorithm doc's mode parameter. (Default today: log.)

---

## E. Visualize the analysis region (transparent yellow overlay)

**Status:** ✅ DONE (2026-06-11). See `MOD-CHANGES-analysis-overlay.md`. Implemented
option (b): engine sends `CENTER_MARGIN` in `NegativePreviewResult`, overlay uses a
percentage inset (no real/displayed pixel math needed).

Show the central percentile-analysis region (`CENTER_MARGIN`, currently 12% each
side → central 76%) as a semi-transparent **yellow** overlay on the preview, toggled
by a "hidden" gesture (e.g. a key-hold or a small button, mirroring the Set Black /
Set White interaction).

**Notes / sketch:**
- Pure frontend — the region is just `CENTER_MARGIN` insets of the displayed image,
  so an absolutely-positioned div over the preview `<img>` (e.g.
  `inset: 12%`, `bg-yellow-400/20`, outline) does it. No backend call needed.
- To stay truthful, the overlay's inset must read the *same* value the engine uses.
  Since `CENTER_MARGIN` lives in Rust and isn't sent to the GUI today, either:
  (a) hardcode the same 0.12 in the overlay (simple, but two sources of truth), or
  (b) expose it once — e.g. include it in `NegativePreviewResult` so the overlay
  always matches the engine. Prefer (b) if it's ever more than a quick debug aid.
- Useful companion to item D / the percentile globals when tuning what gets analyzed.

---

## Cross-reference
- `MOD-CHANGES-gamma-slider.md` — gamma slider
- `MOD-CHANGES-bw-points-readout.md` — auto black/white point readout
- `MOD-CHANGES-slider-sensitivity.md` — halved Color Timing (RGB weight) slider range
- `FUTURES-direction-and-choices.md` — merging clone + neg-invert; Python vs Rust/Tauri; rawpy vs rawler
- `RapidRawMod-WORKFLOW.md` (in `~/myProjects/`) — git/update workflow
