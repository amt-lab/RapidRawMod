# Negative Conversion Modes — Methodology

RapidRawMod's negative converter can invert + stretch the negative in three
different **working spaces**, selectable in the modal (`Conversion Mode`). They are
ported from the experimental Python project `neg-invert`
(`gui/test_inverter.py`). Default is **Log**.

> **Status: experimental.** These modes exist to *play with* and compare. The Python
> source never reached a conclusion about which to keep — it was still in the
> exploration phase. Expect to eventually keep one and drop the others.

The mode only changes **how the negative is mapped into the space where the
black/white-point stretch happens** (and, for PSL, one extra power step). The rest
of the pipeline — per-channel normalization, RGB weights, exposure/contrast sigmoid,
highlight desaturation, display gamma — is identical across modes. See
`RapidRaw-negative-conversion-algorithm.md` for that shared pipeline.

---

## The three modes

Let `v` = scanned-negative pixel value in `[0,1]` (per channel).

### Log — optical density (default)
```text
work = -log10(clamp(v, 1e-6, 1))
```
Treats the scan as transmittance and inverts in **optical-density** space, which is
how film density physically behaves. Dark negative → high density; bright negative →
low density. This is the most physically grounded model and what RapidRawMod shipped
with.

### Lin — linear inversion
```text
work = 1 - v
```
The simplest possible inversion: flip the values. No tone model. Tends to look flat
/ low-contrast in the deep tones because film is not linear, but it's a clean
baseline and sometimes preferable for already-linearized scans.

### PSL — pseudo-log
```text
work = 1 - v                      # linear inversion
... stretch + normalize ...
N    = N ** 1.6                   # PSL_GAMMA, applied to the normalized result
```
A **linear inversion followed by a fixed gamma > 1** that redistributes tones to
*approximate* the log look: midtones darken, highlights compress, shadows expand.
It's a cheap stand-in for true density inversion — no `log10`, just a power.

> **Why PSL exists (rationale recovered).** For a large batch of negatives the
> author preferred the **colors** of linear inversion over log, but those images came
> out too bright/flat and needed darkening with gamma every time. PSL bakes that in:
> it keeps linear's color rendering (it *is* linear inversion) and adds a fixed
> midtone-darkening power. The insight is that **log inversion darkens AND re-tints**
> (it's a per-channel nonlinearity), whereas **linear + a darkening power gives log's
> tonality without log's color shift** — hence "pseudo-log": log tone, linear color.
> `PSL_GAMMA = 1.6` is the empirical darkening amount (a top-level const, tweakable).
>
> Caveat: PSL applies the power *before* the weights/sigmoid, while the display-gamma
> control acts at the very end, so PSL is not pixel-identical to "linear then gamma,"
> just qualitatively the same darkening.

---

## Where each mode acts (one diagram)

```text
v ──► [working transform]──► stretch by bp/wp ──► (PSL: ^1.6) ──► weights
        log: -log10(v)            normalize          only in        ─► sigmoid
        lin/psl: 1 - v            (Part A points)     PSL mode       ─► desat ─► gamma
```

Only the bracketed bits depend on the mode. The downstream **code** is shared — but
its perceptual **meaning** is not mode-neutral; see the next section.

---

## Caveat: exposure & contrast are not mode-neutral

The exposure and contrast controls drive a single sigmoid that runs on **N**, the
normalized stretch position (`0` = black point, `1` = white point). That `[0,1]` axis
exists in every mode, but **what N measures differs by mode**:

| Mode | N is linear in… | A contrast change behaves like… |
|---|---|---|
| log | optical density (`-log10 v`) — stops-like | changing **film gamma** (density slope) — the natural home for contrast |
| lin | `1 - v` (linear light) | a linear-space slope change — a different animal |
| psl | `(1 - v)` then `^1.6` before the sigmoid | a slope change on a power-warped axis |

Consequences:

1. **The pivot `x0 = 0.6` and steepness `k = 4·contrast` are constants in N-space.**
   Because scene-tone → N differs per mode, `x0 = 0.6` sits on a *different actual
   brightness* in each mode. So the same **contrast** value pivots around a different
   tone, and the same **exposure** value shifts tone by a different perceptual amount.
2. **"Exposure" here is a sigmoid-center shift, not a linear-light gain.** In
   log/density space that shift is roughly stops-like (defensible); in linear space it
   isn't, so the label is even looser there.
3. **Net:** identical exposure/contrast slider values do **not** mean the same thing
   across modes. Those constants were effectively calibrated for **log** (RapidRAW's
   original, native space); lin/psl inherit them un-retuned.

**Practical takeaway:** treat exposure/contrast as *mode-local*. When A/B-comparing
modes, re-tune them per mode rather than expecting a fixed setting to transfer — and
don't read too much into the fact that the same slider produces a different look in
lin vs log; that's expected, not a bug.

**Possible cleanups (not done):** per-mode `x0`/`k`; or a redesign that redefines
exposure as a true linear-light gain applied *before* the working transform and
contrast as a density-space slope, making both physically defined and mode-consistent.
This would also clarify the conceptual layering (inversion space vs. tone controls).

---

## Reset behavior on mode switch

Switching mode **resets the picked black/white points** (`bp_override`/
`wp_override` → cleared) and keeps everything else (weights, exposure, contrast,
gamma, tweaks).

**Why the picks must reset:** a picked point is stored as a number *in the working
space*. A density value (e.g. `0.8` from log mode) is meaningless as a `1 - v`
linear value, so carrying it over would be wrong. The auto-percentile points always
recompute in the new space, so conversion still works immediately — you'd just
re-click if you want manual points.

**Why the rest is kept:** the tone controls act on the normalized `[0,1]` value,
which is mode-independent, so they carry over and let you A/B-compare modes under
identical settings. (This matches `neg-invert`'s `_on_cycle_mode`.)

> If, while experimenting, you decide you'd rather wipe *everything* on a mode
> switch, it's a one-line change in `handleModeChange` (spread `DEFAULT_PARAMS`
> instead of `params`). Easy to flip later.

---

## Mode-dependent transforms — the four places it threads through

Because the working space changes, the mode must be honored consistently
(`src-tauri/src/negative_conversion.rs`):

1. **Forward transform** in `run_pipeline` / preview / batch (`to_working`).
2. **PSL post-gamma** inside the per-pixel loop (only when mode == psl).
3. **Click sampling** `sample_negative_point` — converts the clicked pixel using the
   *current* mode's `to_working`.
4. **Readout inverse** `points_to_display_255` — maps the density-space endpoints
   back to `[0,255]` with `from_working` (`10^(-d)` for log, `1 - d` for lin/psl).

If any one of these used the wrong transform, picks/readout would silently
disagree with the displayed image.

---

## When you pick a winner

This is a temporary multi-mode test harness. Once a mode is chosen:
- Drop the `ConversionMode` enum to the single survivor (or keep `log` and delete the
  branch logic), remove the selector UI, and simplify `to_working`/`from_working`.
- Update `RapidRaw-negative-conversion-algorithm.md` to remove the mode parameter.

Implementation record: `MOD-CHANGES-conversion-modes.md`.
</content>
