# Log-mode inversion: RapidRawMod vs. neg-invert

Comparison of the **log-space** conversion in RapidRawMod
(`src-tauri/src/negative_conversion.rs`, `run_pipeline`) against the Python
reference neg-invert (`gui/test_inverter.py` `_run_stretch` + `core/invert.py`
`stretch`).

Scope of this comparison: **pre-tweak**, **default/guard values aside**, and
**ignoring the final display gamma**. Question answered: is the actual inversion
algorithm the same?

**TL;DR:** the inversion *core* is identical; the full pipelines are **not**, and
the differences are structural (not just parameter defaults).

---

## What is genuinely identical — the inversion core

The part that actually inverts the negative and stretches it:

| Step | neg-invert `_run_stretch` | RapidRawMod `run_pipeline` | Same? |
|---|---|---|---|
| Log-density transform | `-log10(clip(v, eps, 1))` | `-log10(clamp(v, 1e-6, 1))` | ✅ identical |
| Auto black/white points | per-channel percentile of a centre crop | per-channel percentile of a centre crop | ✅ same concept* |
| Normalize | `(work − bp) / (wp − bp)` | `(D − bp) / (wp − bp)` | ✅ same form |

\* Different default percentiles / crop margins, which this comparison ignores by
design. The *mechanism* (per-channel percentile bounds → linear normalize) is the
same.

So the step that does the inverting — log transform, then map `bp → 0`, `wp → 1`
per channel — is the same in both.

---

## Where they diverge — after the normalize

neg-invert's `stretch()` **finishes the inversion immediately** with a parabolic
soft-clip knee and defers *all* tone to a separate Stage 2. RapidRawMod's
`run_pipeline` instead **bakes tone into the same function**:

```
neg-invert (stage 1):  normalize → parabolic soft-clip knee → done
RapidRawMod:           normalize → max(0) → ×RGB weights → SIGMOID → highlight-desat → gamma
```

### Structural differences (NOT removable via parameters)

1. **Print-grade sigmoid (exposure/contrast).** RapidRawMod always applies a
   normalized S-curve. Even at default `exposure=0, contrast=1` (k=4, x0=0.6) it is
   a real mild S-curve — e.g. input 0.5 maps to ≈0.43 — **not** identity, and no
   parameter values make it a straight line. neg-invert's stretch has **no** S-curve;
   its `apply_s_curve` is a separate Stage-2 step that defaults to bypass.
2. **Highlight desaturation.** RapidRawMod pulls near-clipped highlights (max
   channel > 0.9) toward Rec.709 luma, always. neg-invert's stretch has no such step.
3. **Parabolic soft-clip knee.** neg-invert rolls off both ends with radius
   `soft_clip` (default 0.03). RapidRawMod has **no** knee — it hard-clips only the
   bottom (`max(0)`) and lets the sigmoid + final clamp handle the top.

### Default-only difference (ignorable)

- **RGB weights** (`red/green/blue_weight`) multiply the normalized value in
  RapidRawMod. At the default `1.0` they are identity, so they don't affect this
  comparison. neg-invert handles color in Stage 2 (temp/tint/saturation) instead.

---

## Verdict

- **Inversion math (log transform + per-channel percentile normalize): identical.**
- **As complete algorithms: not identical.** Even pre-tweak, with defaults, and
  ignoring the 2.2 gamma, RapidRawMod always applies a built-in sigmoid contrast
  curve **and** highlight desaturation that neg-invert's stretch does not, while
  neg-invert applies a parabolic soft-clip knee that RapidRawMod does not.
  **Same skeleton, different flesh.**

---

## If you wanted RapidRawMod's log mode to match neg-invert's "pure stretch"

Not implemented — notes only:

- **Neutralize the sigmoid:** replace `apply_curve` with a pass-through (and decide
  what exposure/contrast should then mean, or drop them from this path).
- **Optionally add the parabolic knee:** port `stretch()`'s roll-off (radius
  `soft_clip`) in place of the bare `max(0)` low-clip.
- **Optionally drop highlight desaturation** (or gate it behind a flag).
- Leave the log transform + percentile normalize as-is (already identical).

The result would be neg-invert's Stage-1 stretch, with RapidRawMod's tone applied
afterwards (or not) as separate, controllable steps — closer to neg-invert's
two-stage separation of *inversion* vs *tone*.

---

## Cross-reference
- `RapidRaw-negative-conversion-algorithm.md` — RapidRawMod's full pipeline spec
- `RapidRaw-negative-conversion-modes.md` — the lin/psl/log modes
- neg-invert: `gui/test_inverter.py` `_run_stretch`, `core/invert.py` `stretch`
</content>
