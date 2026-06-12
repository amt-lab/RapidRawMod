# MOD-CHANGES — Color Timing (RGB weight) slider sensitivity

**Date:** 2026-06-12

## What changed

The Color Timing (RGB weight) sliders were **too sensitive** — useful adjustment only
ever happened near the centre, with the outer travel rarely touched. Their range was
**halved around the neutral value 1.0**, so the same drag distance produces half the
value change and the whole track is dedicated to the useful middle. **Defaults, steps,
and the conversion math are unchanged** — only the slider min/max (the input range)
moved.

| Slider | Neutral | Old range | New range |
|---|---|---|---|
| Red / Green / Blue weight | 1.0 | 0.5 – 2.0 | **0.75 – 1.5** |

Exposure, Contrast, Gamma, and the BP/WP tweak sliders were **not** changed.

## Why halving the range (not the step)

"Half as sensitive" = half the value change per unit of slider travel. With a fixed
widget width, value-change-per-pixel = `range / width`, so halving the range halves
the sensitivity exactly — and simultaneously concentrates the track on the centre,
which is the only region that was being used. Halving the *step* instead would keep
the extremes reachable and just add ticks, which is not what was wanted.

The ranges stay slightly asymmetric about neutral (e.g. weights 0.75–1.5 around 1.0)
because the originals were asymmetric; each side was simply halved from neutral.

## Files touched

- `src/components/modals/NegativeConversionModal.tsx` — `min`/`max` on the red/green/
  blue weight `<Slider>`s.
- `python-clone/neg_conversion_gui.py` — matching `FloatSlider` ranges.
- `DOCS_MOD/RapidRaw-negative-conversion-algorithm.md` — GUI Controls table ranges.

## Reverting

Restore the old min/max in those two files (and the doc table). No backend or
pipeline change is involved, so nothing else is affected. Existing values outside the
new range (e.g. a previously-saved exposure of 1.5) would clamp to the new bounds.

## Test

```bash
cd ~/myProjects/neg-invert/RapidRawMod && npm run start
# or the clone:
cd ~/myProjects/neg-invert/RapidRawMod/python-clone && python3 neg_conversion_gui.py <scan>
```
