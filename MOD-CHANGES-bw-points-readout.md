# MOD CHANGE — Auto Black/White point readout

**What:** Display the engine's auto-selected black & white points back in the
Negative Conversion modal, as standard **[0,255]** RGB values.

**Background (how the auto selection works):**
- `analyze_bounds()` works in **log/density space**: `density = -log10(v)`.
- Per channel it takes the **0.1% percentile → `min`** and **99.9% percentile → `max`**
  of density (sampled from a centre crop, every few pixels).
- `run_pipeline` normalizes each pixel `(density - min)/(max - min)`, then the tone
  curve maps that to output. So:
  - `bounds.min` (low density = thin/bright film) → output **black**.
  - `bounds.max` (high density = dense/dark film) → output **white**.
- Reported back as negative pixel values: `value = 10^(-density) * 255`.
  **For a negative the black point is the *higher* number** (brighter film).

**Files touched: 2** (same two as always).
| File | Layer | Why |
|---|---|---|
| `src-tauri/src/negative_conversion.rs` | Rust engine | return the points with the preview |
| `src/components/modals/NegativeConversionModal.tsx` | React GUI | show the readout |

---

## 1. Engine — `src-tauri/src/negative_conversion.rs`

**1a. New result type + density→[0,255] helper** (added after `struct ChannelBounds`):
```rust
#[derive(Serialize, Clone)]
pub struct NegativePreviewResult {
    pub image: String,
    pub black_point: [u16; 3],   // maps to output black
    pub white_point: [u16; 3],   // maps to output white
}

fn bounds_to_display_points(bounds: &[ChannelBounds; 3]) -> ([u16; 3], [u16; 3]) {
    let to_255 = |density: f32| -> u16 {
        let v = 10f32.powf(-density);            // negative pixel value (0,1]
        (v * 255.0).round().clamp(0.0, 255.0) as u16
    };
    let mut black = [0u16; 3];
    let mut white = [0u16; 3];
    for c in 0..3 {
        black[c] = to_255(bounds[c].min);        // low density  -> output BLACK
        white[c] = to_255(bounds[c].max);        // high density -> output WHITE
    }
    (black, white)
}
```

**1b. Change the preview command return type:**
```rust
// before: ) -> Result<String, String> {
) -> Result<NegativePreviewResult, String> {
```

**1c. Compute bounds explicitly, return the struct** (end of
`preview_negative_conversion`, replacing the old `run_pipeline(..., None)` + return):
```rust
    let rgb = base_image_for_processing.to_rgb32f();
    let (w, h) = rgb.dimensions();
    let log_pixels: Vec<f32> = rgb
        .as_raw()
        .par_iter()
        .map(|&v| -v.clamp(1e-6, 1.0).log10())
        .collect();
    let bounds = analyze_bounds(&log_pixels, w as usize, h as usize);
    let (black_point, white_point) = bounds_to_display_points(&bounds);

    let processed = run_pipeline(&base_image_for_processing, &params, Some(bounds));
    // ...encode to jpeg/base64 as before...
    Ok(NegativePreviewResult {
        image: format!("data:image/jpeg;base64,{}", base64_str),
        black_point,
        white_point,
    })
```
> Note: only the **preview** path reports points. `convert_negatives` (batch save)
> is unchanged — it already computes the same bounds internally.

---

## 2. GUI — `src/components/modals/NegativeConversionModal.tsx`

**2a. Type for the new return shape** (added near the top):
```ts
interface NegPreviewResult {
  image: string;
  black_point: number[];
  white_point: number[];
}
```

**2b. State to hold the points:**
```ts
const [stretchPoints, setStretchPoints] =
  useState<{ black: number[]; white: number[] } | null>(null);
```

**2c. Use the new return shape in `updatePreview`:**
```ts
const result: NegPreviewResult = await invoke('preview_negative_conversion', { ... });
setPreviewUrl(result.image);                  // was: setPreviewUrl(result)
setStretchPoints({ black: result.black_point, white: result.white_point });
```

**2d. Clear it on close** (in the modal-close cleanup): `setStretchPoints(null);`

**2e. Readout panel** rendered in `renderControls`, just above the NegPy info
notice — shows per-channel `Black (→ 0)` and `White (→ 255)` with a caption noting
that on a negative the black point is the higher value.

---

## 3. Test
```bash
cd ~/myProjects/RapidRawMod && npm run start
```
Load image → right-click → **Productivity** → **Negative Conversion**. Under the
sliders a panel shows the auto black/white points in [0,255], updating live.
Sanity check on a normal negative: **Black RGB > White RGB** (black point is the
brighter film value).

## 4. Re-applying after an upstream update
Re-make edits §1a–1c (`.rs`) and §2a–2e (`.tsx`). All additive and contained to the
same two files as the gamma change — see `MOD-CHANGES-gamma-slider.md`.
