# MOD CHANGE — Conversion modes (lin / psl / log)

**What:** A **Conversion Mode** selector in the Negative Conversion modal, with three
working spaces ported from neg-invert: **Linear** (`1-v`), **Pseudo-Log** (`1-v` then
`^1.6`), **Log** (`-log10(v)`, default). Experimental — meant for comparison; one will
likely be kept. Methodology: `RapidRaw-negative-conversion-modes.md`.

**Reset rule:** switching mode clears the picked B/W points (working space changes);
keeps tone controls + tweaks.

**Files touched: 2** (no `lib.rs` change — `sample_negative_point` was already
registered; only its signature gained a `mode` arg).
| File | Layer |
|---|---|
| `src-tauri/src/negative_conversion.rs` | engine |
| `src/components/modals/NegativeConversionModal.tsx` | GUI |

---

## 1. Engine — `negative_conversion.rs`
- New `enum ConversionMode { Lin, Psl, Log }` (`#[serde(rename_all="lowercase")]`,
  `Default = Log`); `const PSL_GAMMA: f32 = 1.6`.
- Helpers `to_working(v, mode)` (`-log10` for log, `1-v` for lin/psl) and
  `from_working(d, mode)` (inverse, for the readout).
- `NegativeConversionParams`: added `#[serde(default)] pub mode: ConversionMode`.
- `run_pipeline`: forward transform uses `to_working`; PSL applies `N = N^1.6` to the
  normalized value before weights (only when `mode == Psl`).
- `preview_negative_conversion` + `convert_negatives`: their density passes use
  `to_working(v, params.mode)`.
- `points_to_display_255` now takes `mode` and uses `from_working` (so the [0,255]
  readout is correct in linear modes, not just log).
- `sample_negative_point(path, x, y, mode, state)`: new `mode` arg; samples into the
  current mode's working space.

## 2. GUI — `NegativeConversionModal.tsx`
- `NegativeParams` + `DEFAULT_PARAMS`: added `mode: 'lin'|'psl'|'log'` (default `log`).
- `MODES` list + a 3-button segmented selector at the top of the controls.
- `handleModeChange(mode)`: sets mode, clears `bp_override`/`wp_override`, keeps the
  rest, re-previews.
- `sample_negative_point` invoke now passes `mode: params.mode`.

---

## 3. Test
```bash
cd ~/myProjects/RapidRawMod && npm run start
```
Negative Conversion modal → **Conversion Mode** buttons at top. Flip Linear / Pseudo-Log
/ Log on the same image and watch the look change. Tone sliders stay put across
switches; picked B/W points reset (re-click after switching). The "Points In Use"
readout stays correct in every mode.

## 4. Re-apply after upstream update
All in the two modded files. §1 (`.rs`) + §2 (`.tsx`). The only shared-file touch from
the broader negative work remains the single `sample_negative_point` registration line
in `lib.rs` (already present).
</content>
