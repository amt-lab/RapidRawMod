# MOD CHANGE — Gamma slider for Negative Conversion

**What:** Replaced the hardcoded output gamma (`2.2`) in the film-inversion engine
with a user-controllable **Gamma** slider (range **0.5 – 3.0**, default **2.2**).

**Files touched: 2** (kept deliberately concentrated — no i18n/locale files).
| File | Layer | Why |
|---|---|---|
| `src-tauri/src/negative_conversion.rs` | Rust engine | accept `gamma` as a parameter instead of hardcoding |
| `src/components/modals/NegativeConversionModal.tsx` | React GUI | add the slider and send `gamma` to the engine |

These are the *only* upstream files edited, so an upstream update will at most
conflict in these two spots. Re-apply with the steps below if that ever happens.

---

## 1. Engine — `src-tauri/src/negative_conversion.rs`

Three edits in this file:

**1a. Add a `gamma` field to the params struct** (`NegativeConversionParams`):
```rust
    pub exposure: f32,
    pub contrast: f32,
    pub gamma: f32,        // <-- added
}
```

**1b. Give it a default of 2.2** (in `impl Default for NegativeConversionParams`):
```rust
            exposure: 0.0,
            contrast: 1.0,
            gamma: 2.2,        // <-- added
        }
```

**1c. Use the parameter instead of the hardcoded value** (inside `run_pipeline`):
```rust
    // before:
    // let gamma_inv = 1.0 / 2.2;
    // after:
    let gamma_inv = 1.0 / params.gamma.max(0.01);
```
The `.max(0.01)` guards against divide-by-zero if gamma is ever 0.
`gamma_inv` is then applied unchanged at the end of the pixel loop
(`out_pixel[..] = ...powf(gamma_inv)`).

Both engine entry points — `preview_negative_conversion` (live preview) and
`convert_negatives` (batch save) — call `run_pipeline`, so this one change covers
preview *and* final output automatically.

---

## 2. GUI — `src/components/modals/NegativeConversionModal.tsx`

Three edits in this file:

**2a. Add `gamma` to the TypeScript params type** (`interface NegativeParams`):
```ts
  contrast: number;
  exposure: number;
  gamma: number;        // <-- added
}
```

**2b. Add its default (2.2)** (in `DEFAULT_PARAMS`):
```ts
  contrast: 1.0,
  exposure: 0.0,
  gamma: 2.2,        // <-- added
};
```

**2c. Add the slider** in the "print grade" section, right after the Contrast
slider (inside `renderControls`):
```tsx
            <Slider
              label="Gamma"
              value={params.gamma}
              min={0.5}
              max={3.0}
              step={0.05}
              defaultValue={2.2}
              onChange={(e) => handleParamChange('gamma', Number(e.target.value))}
              fillOrigin="min"
            />
```
A plain `label="Gamma"` string is used (not a `t(...)` translation key) so no
locale files need editing. The existing `handleParamChange` + `Reset` button +
`Save` path all pick up `gamma` automatically because they operate on the whole
`params` object.

---

## 3. Build & test

```bash
cd ~/myProjects/RapidRawMod
npm run start
```
Then in the app: load an image → right-click → **Productivity** →
**Negative Conversion**. The **Gamma** slider appears under Contrast.
- Default sits at **2.2** (identical output to the original hardcoded behaviour).
- Lower gamma → brighter/flatter; higher gamma → darker/contrastier.
- **Reset** button returns it to 2.2.
- The value is included when you **Convert & Save** (full-res output matches preview).

Engine-only quick check (compiles without launching the GUI):
```bash
cd ~/myProjects/RapidRawMod/src-tauri && cargo build
```

---

## 4. Re-applying after an upstream update

If a future upstream version changes these files and causes a merge conflict
(see `RapidRawMod-WORKFLOW.md` Section 8), just re-make the six small edits above:
3 in the `.rs` file (§1a–1c) and 3 in the `.tsx` file (§2a–2c). They are additive
and self-contained, so this is a copy-paste job, not a redesign.
