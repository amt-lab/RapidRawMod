# RapidRAW Negative Conversion Algorithm

This describes the negative-conversion math only. It assumes the input has
already been decoded into an RGB floating-point image:

```text
I[y, x, c] in [0, 1], where c is R, G, or B
```

OpenCV may load images as BGR, but this algorithm expects RGB.

## Controls

RapidRAW's original controls are:

```text
red_weight    default 1.0, range 0.5 to 2.0
green_weight  default 1.0, range 0.5 to 2.0
blue_weight   default 1.0, range 0.5 to 2.0
exposure      default 0.0, range -2.0 to 2.0
contrast      default 1.0, range 0.5 to 2.5
```

The Python clone also exposes the analysis parameters:

```text
central_region_percent  default 76.0
low_percentile          default 0.1
high_percentile         default 99.9
```

The central-region default of 76% matches RapidRAW's hard-coded 12% margin on
each side:

```text
100% - 12% - 12% = 76%
```

## High-Level Flow

```text
RGB [0, 1]
  -> log-density transform
  -> analyze per-channel density bounds from central image region
  -> normalize each channel by its bounds
  -> apply RGB weights
  -> apply exposure/contrast sigmoid curve
  -> reduce saturation in near-clipped highlights
  -> apply display gamma
  -> RGB [0, 1]
```

The final output is an RGB float image in `[0, 1]`.

## Step 1: Log-Density Transform

Negative film is handled in optical-density-like space:

```text
eps = 1e-6
D = -log10(clamp(I, eps, 1.0))
```

So dark scanned-negative values become high density values, and bright scanned
values become low density values.

## Step 2: Auto Analysis Region

The algorithm does not analyze the full image by default. It uses a central
region.

For a central region percentage `p`:

```text
central_fraction = p / 100
margin_fraction = (1 - central_fraction) / 2

margin_x = floor(width  * margin_fraction)
margin_y = floor(height * margin_fraction)

ROI = D[margin_y : height - margin_y,
        margin_x : width  - margin_x]
```

RapidRAW also samples the ROI for speed:

```text
estimated_pixels = (width - 2 * margin_x) * (height - 2 * margin_y)
x_step = max(floor(estimated_pixels / 40000), 1)

sampled_ROI = ROI[every 3rd row, every x_step column]
```

## Step 3: Per-Channel Bounds

For each channel independently:

```text
values = finite sampled_ROI values for channel c
sort(values)

low_index  = floor(len(values) * low_percentile  / 100)
high_index = floor(len(values) * high_percentile / 100)

bound_min[c] = values[low_index]
bound_max[c] = values[high_index]
```

Defaults:

```text
low_percentile  = 0.1
high_percentile = 99.9
```

This is similar to `numpy.percentile`, but it is not interpolated. It selects
actual sorted samples by index.

Safety rule:

```text
if bound_max[c] <= bound_min[c] + 0.0001:
    bound_max[c] = bound_min[c] + 1.0
```

## Step 4: Normalize Log-Density

Each channel is normalized with its own bounds:

```text
N[c] = (D[c] - bound_min[c]) / (bound_max[c] - bound_min[c])
```

Then values are clipped only at the lower end:

```text
N[c] = max(N[c], 0.0)
```

At this stage, values above 1.0 can still exist.

## Step 5: Color Timing Weights

The RGB sliders are applied after log-density analysis and normalization:

```text
N[R] *= red_weight
N[G] *= green_weight
N[B] *= blue_weight
```

So yes: the color tweaks are applied in the normalized log-density domain, not
as a final RGB display-space color balance.

## Step 6: Print Grade Curve

Exposure and contrast control a sigmoid curve.

```text
k  = 4.0 * max(contrast, 0.1)
x0 = 0.6 - exposure * 0.25
```

`contrast` changes sigmoid steepness. Higher contrast gives a steeper curve.

`exposure` shifts the curve horizontally. Positive exposure lowers `x0`, which
generally brightens the output.

The raw sigmoid is:

```text
S(x) = 1 / (1 + exp(-k * (x - x0)))
```

RapidRAW normalizes this sigmoid so that the useful `[0, 1]` input range maps
back to `[0, 1]`:

```text
y0 = 1 / (1 + exp( k * x0))
y1 = 1 / (1 + exp(-k * (1 - x0)))
scale = 1 / (y1 - y0)

C = clamp((S(N) - y0) * scale, 0, 1)
```

So yes: exposure and contrast are applied to the normalized log-density values,
before the image is returned to display gamma.

## Step 7: Highlight Saturation Reduction

After the curve, highly bright pixels are desaturated toward luma to avoid
colored clipping.

Luma uses Rec.709 coefficients:

```text
Y = 0.2126 * C[R] + 0.7152 * C[G] + 0.0722 * C[B]
M = max(C[R], C[G], C[B])
```

Only pixels whose brightest channel exceeds 0.9 are affected:

```text
overflow = clamp((M - 0.9) * 10.0, 0, 1)
sat_reduction = overflow * overflow

C[c] = C[c] + (Y - C[c]) * sat_reduction
```

If `M <= 0.9`, `sat_reduction` is zero and the pixel is unchanged.

If `M >= 1.0`, `sat_reduction` approaches one and the pixel moves strongly
toward neutral luma.

## Step 8: Display Gamma

The result is clamped and gamma-encoded:

```text
O[c] = clamp(C[c], 0, 1) ** (1 / 2.2)
```

The returned image is:

```text
O[y, x, c] in [0, 1]
```

## Complete Pseudo-Code

```text
function convert_negative(I, params):
    I = rgb_float_image_clipped_to_0_1(I)

    # 1. Log-density transform
    D = -log10(clamp(I, 1e-6, 1.0))

    # 2. Choose central analysis region
    p = params.central_region_percent
    margin_fraction = (1 - p / 100) / 2
    mx = floor(width  * margin_fraction)
    my = floor(height * margin_fraction)
    ROI = D[my : height - my, mx : width - mx]

    # 3. Sample ROI similarly to RapidRAW
    estimated_pixels = (width - 2 * mx) * (height - 2 * my)
    x_step = max(floor(estimated_pixels / 40000), 1)
    SROI = ROI[every 3rd row, every x_step column]

    # 4. Per-channel percentile-like bounds
    for c in [R, G, B]:
        values = finite values from SROI[c]
        sort(values)

        low_index  = floor(len(values) * params.low_percentile  / 100)
        high_index = floor(len(values) * params.high_percentile / 100)

        bound_min[c] = values[low_index]
        bound_max[c] = values[high_index]

        if bound_max[c] <= bound_min[c] + 0.0001:
            bound_max[c] = bound_min[c] + 1.0

    # 5. Normalize density
    for c in [R, G, B]:
        N[c] = (D[c] - bound_min[c]) / (bound_max[c] - bound_min[c])
        N[c] = max(N[c], 0.0)

    # 6. Color timing weights in normalized log-density domain
    N[R] = N[R] * params.red_weight
    N[G] = N[G] * params.green_weight
    N[B] = N[B] * params.blue_weight

    # 7. Exposure/contrast sigmoid in normalized log-density domain
    k  = 4.0 * max(params.contrast, 0.1)
    x0 = 0.6 - params.exposure * 0.25

    y0 = 1 / (1 + exp( k * x0))
    y1 = 1 / (1 + exp(-k * (1 - x0)))
    scale = 1 / (y1 - y0)

    for c in [R, G, B]:
        sigmoid = 1 / (1 + exp(-k * (N[c] - x0)))
        C[c] = clamp((sigmoid - y0) * scale, 0, 1)

    # 8. Highlight saturation reduction
    Y = 0.2126 * C[R] + 0.7152 * C[G] + 0.0722 * C[B]
    M = max(C[R], C[G], C[B])
    overflow = clamp((M - 0.9) * 10.0, 0, 1)
    sat_reduction = overflow * overflow

    for c in [R, G, B]:
        C[c] = C[c] + (Y - C[c]) * sat_reduction

    # 9. Gamma encode and return
    O = clamp(C, 0, 1) ** (1 / 2.2)
    return O
```

## Preview Versus Save

RapidRAW previews on a downscaled image. When saving, it computes bounds from a
1080px reference image, then applies those bounds to the full-resolution image.

In pseudo-code:

```text
reference = downscale_to_fit(input, 1080, 1080)
bounds = analyze_bounds(reference, params)
output = convert_negative(input, params, bounds)
```
