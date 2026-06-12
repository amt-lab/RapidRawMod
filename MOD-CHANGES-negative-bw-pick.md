# MOD CHANGE — B/W point pick + tweaks (ported from neg-invert)

**What:** Click the preview to set the **black** or **white** point of the negative
(per-channel, carrying color direction), plus **BP/WP Tweak** sliders that slide each
endpoint along the black→white axis. Ported from `neg-invert`'s `test_inverter.py`.

**Files touched: 3.**
| File | Layer | Why |
|---|---|---|
| `src-tauri/src/negative_conversion.rs` | Rust engine | overrides + tweaks + sampling command |
| `src-tauri/src/lib.rs` | Rust (1 line) | register the new command |
| `src/components/modals/NegativeConversionModal.tsx` | React GUI | pick buttons, click handler, tweak sliders |

Math note: with `bp = bp_base + bp_tweak·rng`, `wp = wp_base + wp_tweak·rng`
(`rng = wp_base − bp_base`, per channel), the axis `wp − bp = rng·(1 + wp_tweak − bp_tweak)`
stays a scalar multiple of the original — **color direction is preserved exactly.**

---

## 1. Engine — `src-tauri/src/negative_conversion.rs`

**1a. Four new params** on `NegativeConversionParams` (all `#[serde(default)]`):
```rust
pub bp_override: Option<[f32; 3]>,   // density working space, None = auto percentile
pub wp_override: Option<[f32; 3]>,
pub bp_tweak: f32,
pub wp_tweak: f32,
```
Defaults: overrides `None`, tweaks `0.0`.

**1b. `resolve_points(bounds, params)`** — returns the effective per-channel
`(bp, wp)` in density space: start from override-or-auto base, then
`base ± tweak·rng`. (This is the neg-invert `_run_stretch` math.)

**1c. `run_pipeline`** now calls `resolve_points` and normalizes with the result:
```rust
let (bp, wp) = resolve_points(&bounds, params);
...
let mut n_r = (log_pixels[idx] - bp[0]) / (wp[0] - bp[0]).max(1e-6);
// (same for g, b)
```
Both `preview_negative_conversion` and `convert_negatives` go through `run_pipeline`,
so picks/tweaks affect preview **and** saved output.

**1d. Readout** now reports the *effective* points: the old
`bounds_to_display_points` became `points_to_display_255(&bp, &wp)`, fed by
`resolve_points`. So the "Points In Use" panel reflects picks + tweaks.

**1e. New command `sample_negative_point(path, x, y)`** (`x`,`y` normalized 0–1):
reads the cached downscaled negative from `state.geometry_cache`, averages a 5×5
patch, and returns the per-channel density `-log10(px)` — ready to drop into an
override. Returns an error if the preview cache isn't populated yet.

## 2. Register — `src-tauri/src/lib.rs`
One line added to the `tauri::generate_handler![...]` list:
```rust
negative_conversion::sample_negative_point,
```

## 3. GUI — `src/components/modals/NegativeConversionModal.tsx`
- `NegativeParams` + `DEFAULT_PARAMS`: added `bp_override`/`wp_override`
  (`number[] | null`) and `bp_tweak`/`wp_tweak` (`0`).
- New state `pickMode: 'black' | 'white' | null`; cleared on close.
- `handlePickClick`: normalized click → `invoke('sample_negative_point')` →
  store density as `bp_override`/`wp_override` → re-preview. Uses
  `getBoundingClientRect` so zoom/pan don't affect the mapping.
- `resetPoints`: clears overrides + tweaks back to auto.
- Preview `<img>`: when `pickMode` is set, becomes `pointer-events-auto`
  `cursor-crosshair`, swallows the pan `mousedown`, and `onClick={handlePickClick}`.
- New "Black / White Points" control block: **Set Black** / **Set White** / **Auto**
  buttons + **BP Tweak** / **WP Tweak** sliders (−0.1…0.1, step 0.01).

---

## 4. Test
```bash
cd ~/myProjects/RapidRawMod && npm run start
```
Load image → right-click → **Productivity** → **Negative Conversion**:
1. Click **Set Black**, then click a spot that should be black (e.g. deepest shadow /
   the densest part) → the image re-stretches and "Points In Use" updates.
2. Click **Set White**, click a spot that should be white.
3. Nudge **BP/WP Tweak** for fine adjustment (color balance is preserved).
4. **Auto** returns to the percentile-picked points. **Reset** (top-right) clears
   everything including picks/tweaks.

Sanity: picking a neutral-but-dark patch as black and a neutral-but-bright patch as
white should neutralize a color cast better than the auto percentiles.

## 5. Re-applying after an upstream update
Edits §1a–1e + §2 (`.rs` / `lib.rs`) and §3 (`.tsx`). Only `lib.rs` is a shared
upstream file (one line); the rest is in our two already-modded files.
</content>
