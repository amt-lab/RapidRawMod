# python-clone — RapidRawMod negative converter, in Python

A standalone **Python/PyQt6 replica** of RapidRawMod's modified Negative Conversion
module. It exists so the inversion math + UX can be explored, tuned, and A/B-compared
fast — without the Rust/Tauri compile loop. It is a *clone for experimentation*, not the
shipping code.

It mirrors two files in the main app:

| Main app (Rust/TS) | Here (Python) |
|---|---|
| `src-tauri/src/negative_conversion.rs` | `core/rapidraw_negative.py` (the engine) |
| `src/components/modals/NegativeConversionModal.tsx` | `neg_conversion_gui.py` (the UI) |

> **`python-clone-old/`** is the previous version of this clone, kept for reference. It
> predates the mods below (no modes, no gamma slider, no B/W picking) and exposed the
> percentiles as sliders instead of globals.

---

## Run it

```bash
cd ~/myProjects/neg-invert/RapidRawMod/python-clone
python3 neg_conversion_gui.py [optional_image_path]
```
Needs `numpy`, `opencv-python` (cv2), `PyQt6`, and optionally `rawpy` for RAW scans.
Open a scanned negative → tune → **Save TIFF** writes a 16-bit `_Positive.tiff`.

---

## Feature parity with the modded module

| Feature | Status | Notes |
|---|---|---|
| Conversion modes: **lin / psl / log** | ✅ | `to_working` / `from_working`; psl applies `^1.6` after the stretch. Default **log**. |
| Color-timing **RGB weights** | ✅ | applied in the normalized working domain |
| **Exposure / contrast** print-grade sigmoid | ✅ | same `k = 4·contrast`, `x0 = 0.6 − 0.25·exposure` |
| Highlight desaturation | ✅ | onset at max-channel 0.9 |
| **Gamma** control | ✅ | default 2.2, range 0.5–3.0 |
| **B/W point picking** (Set Black / Set White → click image) | ✅ | samples a 5×5 patch on the preview, stores per-channel density override |
| **BP / WP tweaks** (slide endpoints along the axis) | ✅ | `bp = bp_base + tweak·range` |
| **Auto** button (clear picks + tweaks) | ✅ | |
| **Points-in-use readout** (negative [0,255]) | ✅ | text panel under the controls |
| **Analysis-area overlay** (yellow inset) | ✅ | toggled by "Show analysis area"; inset = `CENTER_MARGIN` |
| Mode switch resets picked points, keeps tone + tweaks | ✅ | matches `handleModeChange` in the TSX |
| Reset (all params to default) | ✅ | |
| Preview on ≤1080px proxy; save analyzes 1080 ref then full-res | ✅ | `convert_negative_preview` vs `convert_negative_like_rapidraw_save` |

---

## Where the constants live (per your request)

The black/white-point **percentiles** and the **center margin** are **globals at the top
of `core/rapidraw_negative.py`** (not sliders), matching the consts in
`negative_conversion.rs`:

```python
PSL_GAMMA           = 1.6    # psl post-stretch power
PCT_LOW             = 0.05   # black-point base percentile (Rust ships 0.05)
PCT_HIGH            = 0.995  # white-point base percentile
CENTER_MARGIN       = 0.12   # trim each side -> central 76% analysis region
SAMPLE_TARGET       = 40000  # ROI subsample budget (column step)
SAMPLE_ROW_STRIDE   = 3      # analyze every Nth row
SAMPLE_PATCH_RADIUS = 2      # B/W pick patch half-size: 2 -> 5x5, 0 -> single pixel
PREVIEW_MAX_DIM     = 1080   # interactive proxy size (also the save analysis reference)

# Print-grade sigmoid (exposure/contrast):  k = K_SCALE*contrast,  x0 = X0_BASE - exposure*EXPOSURE_SCALE
SIGMOID_X0_BASE        = 0.6
SIGMOID_EXPOSURE_SCALE = 0.25
SIGMOID_K_SCALE        = 4.0
# Highlight desaturation toward luma above DESAT_THRESHOLD
DESAT_THRESHOLD     = 0.9
DESAT_SLOPE         = 10.0
# Rec.709 luma weights
LUMA_R, LUMA_G, LUMA_B = 0.2126, 0.7152, 0.0722
```

Edit those to retune globally. `PREVIEW_MAX_DIM` is the one to raise for a sharper proxy
(slower preview recomputes; no lag observed at 1080 on an M-series Mac).

---

## Engine API (`core/rapidraw_negative.py`)

- `convert_negative_preview(img, params)` → `{image, black_point, white_point, center_margin}`
  — the preview path (mirrors `preview_negative_conversion`).
- `convert_negative_like_rapidraw_save(img, params)` → full-res positive (mirrors the save path).
- `analyze_bounds(img, mode)` → 3× `ChannelBounds` (auto percentiles in working space).
- `resolve_points(bounds, params)` → `(bp[3], wp[3])` after override + tweak.
- `points_to_display_255(bp, wp, mode)` → `(black[3], white[3])` for the readout.
- `sample_point(img, x, y, mode)` → per-channel working-space density at a click
  (mirrors `sample_negative_point`).
- `to_working` / `from_working` — mode-dependent transforms.

`NegativeConversionParams` fields: `red_weight, green_weight, blue_weight, exposure,
contrast, gamma, mode, bp_override, wp_override, bp_tweak, wp_tweak`.

---

## Known differences from the Rust app

- **Image loading** uses this project's `core/img_io.py` (cv2 / rawpy), not RapidRAW's
  Tauri `load_base_image_from_bytes`. RAW decoding/white-balance defaults may differ from
  the app, so absolute colors won't be pixel-identical — the **conversion math** is what's
  cloned.
- Preview is computed synchronously on a downscaled proxy (no caching/threading beyond a
  debounce timer).
- No batch/multi-file save UI; one image at a time.
- `core/invert.py`, `core/tone.py`, `core/local_contrast.py` are inherited from the
  `neg-invert` Python project and are **not used** by this clone (kept for reference).

The reference algorithm write-up lives in
`../DOCS_MOD/RapidRaw-negative-conversion-algorithm.md` and the modes rationale in
`../DOCS_MOD/RapidRaw-negative-conversion-modes.md`.
