# MOD CHANGE — Analysis-region overlay

**What:** A toggle (**Show analysis area**) that draws a translucent **yellow** box
over the preview marking the central percentile-analysis region (`CENTER_MARGIN`
inset on each side). Visual aid for understanding/auto-tuning the auto stretch.

**Files touched: 2** (no `lib.rs` change).
| File | Layer |
|---|---|
| `src-tauri/src/negative_conversion.rs` | engine — exposes the margin |
| `src/components/modals/NegativeConversionModal.tsx` | GUI — toggle + overlay |

---

## The dimension trap (and why this avoids it)

The analysis region is defined as a **fraction** — `CENTER_MARGIN` (0.12) trimmed
from each side. So the overlay is a pure **percentage inset** of the displayed image
element; it needs **no real-vs-displayed pixel math**:

- The overlay div is a child of the `relative inline-block` wrapper that shrink-wraps
  the `<img>`, so `inset: 12%` = 12% of the *displayed image box* = the analysis
  region, at any resolution (1080 proxy or full-res).
- It sits inside the same zoom/pan transform as the image, so it scales and pans with
  the picture automatically. A uniform scale preserves percentage insets.
- `pointer-events-none`, so it never blocks B/W picking or panning.

**Truthfulness:** rather than hardcode `0.12` in the GUI (a second source of truth),
the engine sends `CENTER_MARGIN` with each preview, and the overlay uses that. Change
the const in Rust and the box follows.

---

## 1. Engine — `negative_conversion.rs`
- `NegativePreviewResult` gained `pub center_margin: f32`.
- `preview_negative_conversion` sets it to `CENTER_MARGIN`.

## 2. GUI — `NegativeConversionModal.tsx`
- `NegPreviewResult` gained `center_margin: number`.
- State: `showAnalysisArea` (bool), `analysisMargin` (number, default 0.12).
- `updatePreview` stores `result.center_margin`; reset on modal close.
- **Show / Hide analysis area** toggle button in the Black / White Points section.
- Overlay div inside the image wrapper:
  `inset: analysisMargin*100%`, `border-yellow-400/80`, `bg-yellow-400/15`,
  `pointer-events-none`.

---

## 3. Test
```bash
cd ~/myProjects/RapidRawMod && npm run start
```
Negative Conversion → **Show analysis area** → a yellow box marks the central 76%
region used for the auto black/white percentiles. Zoom/pan and confirm it tracks the
image. Toggle off to hide. (If you change `CENTER_MARGIN` in Rust and rebuild, the box
resizes to match.)

## 4. Re-apply after upstream update
Both edits are in the two modded files (§1 `.rs`, §2 `.tsx`). No new shared-file touch.
</content>
