# RapidRawMod Negative Conversion Algorithm

This describes the negative-conversion math used by the **Negative Conversion**
modal (`src-tauri/src/negative_conversion.rs`). It assumes the input has already
been decoded into an RGB floating-point image:

```text
I[y, x, c] in [0, 1], where c is R, G, or B
```

OpenCV / camera decoders may load as BGR; this algorithm expects RGB.

> **This doc is split in two on purpose:**
> - **Part A** — how the per-channel black point `bp[c]` and white point `wp[c]`
>   are *determined* (auto percentiles, click-picking, tweaks). GUI-driven.
> - **Part B** — the **core algorithm**, which takes `bp[c]`/`wp[c]` as inputs and
>   produces the positive. **The core algorithm does not care how `bp`/`wp` were
>   found.**
>
> Everything in Part A lives in density space (see B1) and just produces two
> per-channel vectors that feed B2.

---

## GUI Controls

| Control | Default | Range | Step | Stage | Origin |
|---|---|---|---|---|---|
| Red weight | 1.0 | 0.75 – 1.5 | 0.01 | B3 | original (range halved — mod) |
| Green weight | 1.0 | 0.75 – 1.5 | 0.01 | B3 | original (range halved — mod) |
| Blue weight | 1.0 | 0.75 – 1.5 | 0.01 | B3 | original (range halved — mod) |
| Exposure | 0.0 | −2.0 – 2.0 | 0.05 | B4 | original |
| Contrast | 1.0 | 0.5 – 2.5 | 0.05 | B4 | original |
| **Mode** (lin/psl/log) | **log** | — | — | **B1** | **mod** |
| **Gamma** | **2.2** | **0.5 – 3.0** | **0.05** | **B6** | **mod** |
| **BP Tweak** | **0.0** | **−0.1 – 0.1** | **0.01** | **A3** | **mod** |
| **WP Tweak** | **0.0** | **−0.1 – 0.1** | **0.01** | **A3** | **mod** |
| **Set Black** (button) | — | — | — | A2 | **mod** |
| **Set White** (button) | — | — | — | A2 | **mod** |
| **Auto** (button) | — | — | — | A | **mod** — clears picks + tweaks |
| Reset (top-right) | — | — | — | — | resets *all* controls |

### Fixed constants (not exposed in the GUI)

```text
margin            = 0.12     # 12% on each side -> central 76% analysis region
low_percentile    = 0.1      # -> index floor(N * 0.001)
high_percentile   = 99.9     # -> index floor(N * 0.999)
sample_target     = 40000    # ROI subsampling budget
desat_threshold   = 0.9      # highlight desaturation onset (B5)
x0_base           = 0.6      # sigmoid centre before exposure shift (B4)
exposure_scale    = 0.25
contrast_scale    = 4.0
```

(The Python reference, neg-invert, exposes the central-region size and percentiles
as sliders. RapidRAW/RapidRawMod hardcode them.)

---

# Part A — Determining the black & white points

All point math happens in **density space** (`D`, see B1). The output of Part A is
two per-channel vectors:

```text
bp[c]   # black point: LOW density  -> will map to output black
wp[c]   # white point: HIGH density -> will map to output white
        # (bp[c] < wp[c] for a normal negative)
```

These are built in three layers: a **base** (auto or picked), then an optional
**tweak**.

## A1: Auto base from percentiles (default)

Used for any channel whose point has not been hand-picked.

```text
# central analysis region
mx = floor(width  * 0.12)
my = floor(height * 0.12)
ROI = D[my : height - my, mx : width - mx]

# subsample for speed
estimated_pixels = (width - 2*mx) * (height - 2*my)
x_step = max(floor(estimated_pixels / 40000), 1)
SROI   = ROI[every 3rd row, every x_step column]

# per channel, non-interpolated percentile by sorted index
for c in [R, G, B]:
    v = sort(finite SROI[c] values)
    bp_base[c] = v[ floor(len(v) * 0.001) ]      # 0.1 percentile
    wp_base[c] = v[ floor(len(v) * 0.999) ]      # 99.9 percentile
    if wp_base[c] <= bp_base[c] + 0.0001:        # degenerate-channel guard
        wp_base[c] = bp_base[c] + 1.0
```

Note this is *not* `numpy.percentile` interpolation — it selects an actual sample
by index. Because the density transform is monotonic, these endpoints select the
same pixels you would get by taking percentiles on the original image (only the
direction flips — see B1).

## A2: Manual pick (Set Black / Set White)

Clicking the preview overrides a base for **all three channels at once**, carrying
the clicked pixel's color. Backend command `sample_negative_point(path, x, y)`:

```text
# x, y normalized [0,1] over the preview
sample 5x5 patch around the click on the cached (<=1080px) negative
avg[c] = mean linear pixel value of the patch, per channel
density[c] = -log10(clamp(avg[c], 1e-6, 1.0))

Set Black -> bp_base = density   (override)
Set White -> wp_base = density   (override)
```

So "black point" picks the part of the *scene* that should be black — which is the
thin/bright area of the negative (low density). The **Auto** button discards
overrides and returns to A1.

## A3: Tweaks (slide along the black→white axis)

Each tweak shifts an endpoint proportionally to the per-channel range:

```text
for c in [R, G, B]:
    rng   = max(wp_base[c] - bp_base[c], 1e-6)
    bp[c] = bp_base[c] + bp_tweak * rng
    wp[c] = wp_base[c] + wp_tweak * rng
```

**Color direction is preserved exactly.** The resulting axis is
`wp - bp = rng * (1 + wp_tweak - bp_tweak)` — always a scalar multiple of the
original `rng`, so tweaking only slides/scales the endpoints along the existing
gray axis, it does not rotate it.

## Readout (informational)

The modal shows the effective points as standard [0,255] negative pixel values,
using the **inverse of the current mode's transform** (`from_working`):

```text
v(d)        = 10^(-d)   in log mode ;   1 - d   in lin/psl modes
black_255[c] = round(v(bp[c]) * 255)        # high value (bright film)
white_255[c] = round(v(wp[c]) * 255)        # low value  (dark film)
```

For a normal negative `black_255 > white_255` (the inversion).

---

# Part B — Core algorithm (given bp, wp)

Inputs: image `I`, per-channel `bp[c]`/`wp[c]` from Part A, and the scalar controls
(weights, exposure, contrast, gamma). Output: RGB float `[0,1]`.

## B1: Working-space transform (mode-dependent)

The negative is mapped into the space where the stretch happens. **The Mode control
selects this transform** (see `RapidRaw-negative-conversion-modes.md`):

```text
log  (default):  D = -log10(clamp(I, 1e-6, 1.0))   # optical density
lin:             D = 1 - I                          # linear inversion
psl:             D = 1 - I                          # + a ^1.6 power later (B2a)
```

`bp`/`wp` (Part A), the click-sampling, and the [0,255] readout inverse all use the
**same** mode transform. The doc below uses `D` generically; for the default log
mode it is the optical density. Dark negative → high `D`, bright negative → low `D`
(log); for lin/psl the relationship is just `1 - I`.

## B2: Normalize by the points

```text
N[c] = (D[c] - bp[c]) / max(wp[c] - bp[c], 1e-6)
N[c] = max(N[c], 0.0)        # clip only the low end; N may exceed 1.0 here
```

## B2a: PSL pseudo-log power (psl mode only)

```text
if mode == psl:
    N[c] = N[c] ** 1.6       # PSL_GAMMA; redistributes midtones toward a log look
```

In lin and log modes this step is skipped.

## B3: Color timing weights

Applied in the normalized log-density domain (not as final display-space balance):

```text
N[R] *= red_weight
N[G] *= green_weight
N[B] *= blue_weight
```

## B4: Print-grade sigmoid (exposure / contrast)

```text
k  = 4.0 * max(contrast, 0.1)      # steepness
x0 = 0.6 - exposure * 0.25         # horizontal shift (positive exposure brightens)

y0 = 1 / (1 + exp( k * x0))
y1 = 1 / (1 + exp(-k * (1 - x0)))
scale = 1 / (y1 - y0)

for c in [R, G, B]:
    S    = 1 / (1 + exp(-k * (N[c] - x0)))
    C[c] = clamp((S - y0) * scale, 0, 1)   # normalized so [0,1] -> [0,1]
```

## B5: Highlight saturation reduction

```text
Y = 0.2126*C[R] + 0.7152*C[G] + 0.0722*C[B]    # Rec.709 luma
M = max(C[R], C[G], C[B])
overflow = clamp((M - 0.9) * 10.0, 0, 1)
sat_reduction = overflow * overflow            # 0 below M=0.9, ->1 at M=1.0

for c in [R, G, B]:
    C[c] = C[c] + (Y - C[c]) * sat_reduction
```

## B6: Gamma encode

```text
O[c] = clamp(C[c], 0, 1) ** (1 / max(gamma, 0.01))
```

`gamma` is now a control (default 2.2 reproduces the original hardcoded behaviour;
lower = brighter/flatter, higher = darker/contrastier).

---

## Complete Pseudo-Code

```text
function resolve_points(D, params):                 # Part A
    # base: picked override, else auto percentiles
    bp_base, wp_base = auto_percentile_bounds(D)     # A1
    if params.bp_override: bp_base = params.bp_override   # A2
    if params.wp_override: wp_base = params.wp_override
    for c in [R, G, B]:                              # A3
        rng   = max(wp_base[c] - bp_base[c], 1e-6)
        bp[c] = bp_base[c] + params.bp_tweak * rng
        wp[c] = wp_base[c] + params.wp_tweak * rng
    return bp, wp

function convert_negative(I, params):
    I = rgb_float_clipped_0_1(I)
    D = -log10(clamp(I, 1e-6, 1.0))                  # B1

    bp, wp = resolve_points(D, params)               # Part A

    for c in [R, G, B]:                              # B2
        N[c] = (D[c] - bp[c]) / max(wp[c] - bp[c], 1e-6)
        N[c] = max(N[c], 0.0)

    N[R] *= params.red_weight                        # B3
    N[G] *= params.green_weight
    N[B] *= params.blue_weight

    k  = 4.0 * max(params.contrast, 0.1)             # B4
    x0 = 0.6 - params.exposure * 0.25
    y0 = 1 / (1 + exp( k * x0))
    y1 = 1 / (1 + exp(-k * (1 - x0)))
    scale = 1 / (y1 - y0)
    for c in [R, G, B]:
        S    = 1 / (1 + exp(-k * (N[c] - x0)))
        C[c] = clamp((S - y0) * scale, 0, 1)

    Y = 0.2126*C[R] + 0.7152*C[G] + 0.0722*C[B]      # B5
    M = max(C[R], C[G], C[B])
    sr = clamp((M - 0.9) * 10.0, 0, 1) ** 2
    for c in [R, G, B]:
        C[c] = C[c] + (Y - C[c]) * sr

    O = clamp(C, 0, 1) ** (1 / max(params.gamma, 0.01))   # B6
    return O
```

---

## Preview vs. Save

- **Preview** (`preview_negative_conversion`): processes a cached ≤1080px proxy;
  `bp`/`wp` (and the readout) are resolved from that proxy.
- **Save** (`convert_negatives`): computes the auto bounds from a 1080px reference,
  then runs the full-resolution image through the same `convert_negative`.

```text
reference = downscale_to_fit(input, 1080, 1080)
bounds    = auto_percentile_bounds(to_working(reference, mode))   # B1 transform
output    = convert_negative(input, params with bounds)          # same params/picks/tweaks
```

> **Batch note:** a manual pick stores one density override in `params`, so it is
> applied to *every* image in a multi-select batch. Pick per-image if a batch is
> mixed.

---

## Changes from the original (`*-OLD.md`)

1. **Gamma** is a control (B6), not a hardcoded `1/2.2`.
2. **Conversion Mode** (B1): the working-space transform is selectable
   (lin / psl / log); previously hardcoded to log. Experimental — see
   `RapidRaw-negative-conversion-modes.md`.
3. **Black/white points** can be **picked** (A2) and **tweaked** (A3); previously
   they were always the auto percentiles (A1).
4. Doc restructured into Part A (point selection) / Part B (core algorithm) to
   reflect that the algorithm is independent of how the points are obtained.
5. Clarified that the analysis region / percentiles are **fixed constants** in
   RapidRAW, not exposed parameters.

Implementation records: `MOD-CHANGES-gamma-slider.md`,
`MOD-CHANGES-bw-points-readout.md`, `MOD-CHANGES-negative-bw-pick.md`.
</content>
