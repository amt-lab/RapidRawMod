# Why is the Negative module a separate tool? — analysis

Research notes on why RapidRAW's negative conversion saves a positive file you then
edit, instead of feeding the converted result straight into the editor — and what a
lighter "feeds-the-editor" design would actually take.

---

## Part 1 — Why it is separate today

### Evidence

**It follows the "Productivity tools" pattern.** Every tool in that submenu emits a
*new file*, not a live edit:

| Tool | Output |
|---|---|
| Panorama | `_Pano.png` |
| Denoise | `_Denoised.tiff` |
| Collage | `_Collage.png` |
| **Negative** | **`_Positive.tiff`** |

So "why doesn't it feed the editor?" is partly *"because none of them do"* — the
category's contract is standalone batch processors that hand a finished file back to
the library. Negative was built in that mold → consistency, not oversight.

**Timing — a real but secondary afterthought.** Git history:
- Core editor (`image_processing.rs`, `gpu_processing.rs`): **June 2025**.
- `negative_conversion.rs`: **2026-02-06** — ~**8 months later**.

The editor architecture was mature/frozen before negatives arrived, so a
self-contained tool was lower-risk than retrofitting central code.

**Genuine architectural friction.** Two concrete blockers to making it a *live editor
stage*:

- **GPU per-pixel vs. a global analysis pass.** The editor is a per-pixel GPU shader
  pipeline (`shaders/*.wgsl`). Negative conversion needs a whole-image analysis first
  — per-channel percentiles computed by **sorting samples** (`vals.sort_by` in
  `analyze_bounds`). A sort/histogram reduction doesn't map onto a per-pixel shader;
  the pipeline has no "analyze whole frame, then process" stage.
- **No slot in the non-destructive state model.** The editor applies a live JSON
  adjustment blob per pixel. A live inversion stage would need all its params folded
  into the per-image adjustment record, serialized, and run *before* the normal
  adjustments with its own ordering and cached analysis.

### Conclusion (Part 1)
Emitting a 16-bit positive is the path of least resistance that's also coherent: the
editor never has to learn about negatives. Cost: a disk round-trip and losing the
ability to re-tune the inversion after editing starts.

> **Fork upside:** that silo is exactly why the mods in this fork stay clean — all of
> them live in one Rust file + one modal. Integrating inversion into the core pipeline
> would smear those features across shaders, the adjustment schema, and serialization.

---

## Part 2 — A lighter design that *would* feed the editor

The friction in Part 1 assumed inversion becomes a *live, GPU, per-frame* stage. Drop
those assumptions and it gets easy. The key relaxations:

- **The inversion may stay on the CPU** (it does not need the GPU engine).
- **It runs once at ingest**, producing the base image the editor then edits.
- **"One chance"** is acceptable: you convert at the start and don't necessarily go
  back to it mid-edit.

### Why this dissolves both blockers

- **GPU/analysis blocker → gone.** Conversion stays CPU and runs **at load time**, not
  per frame. The percentile sort happens once per load (or per param change), never in
  a shader. There's already a precedent: `load_base_image_from_bytes(... settings ...)`
  applies a load-time variant transform today (`linear_raw_mode`,
  `image_loader.rs:70`). The inversion slots in at the same point.
- **State blocker → small.** You don't touch the live GPU adjustment chain at all. You
  only need a per-image params blob, and per-image persistence already exists: the
  **`.rrdata` sidecar** (`file_management.rs`). Negative params ride there alongside
  the normal adjustments.

### The shape of it

```text
decode raw/scan ─► [CPU negative conversion, gated on sidecar params] ─► base image
                                                                            │
                                       ─► GPU editor + non-destructive adjustments ─► export
```

Concretely:
1. Store negative params (mode, bp/wp, tweaks, weights…) in the image's `.rrdata`.
2. In the load path (`load_base_image_from_bytes`), if those params are present, run the
   existing `run_pipeline` once on the decoded float image and return that as the base.
3. Cache the converted base like any other proxy; the editor proceeds normally.
4. The conversion modal becomes the *ingest* step — it commits params to the sidecar
   instead of saving a `_Positive.tiff`.

### Two insights this surfaces

- **"One chance" is a UX choice, not a technical limit.** Because conversion is a pure
  function `positive = convert(scan, params)` recomputed at load, storing the params
  gives you **re-tunability nearly for free** — re-open the params, re-run convert,
  refresh the base. The only reason to *enforce* one-shot is to avoid building the
  re-entry panel + cache-invalidation wiring. So "convert once and move on" is a fine
  MVP, but the architecture doesn't force it.
- **An integrated negative stage should shed its duplicate tone controls.** The module
  currently re-implements exposure, contrast, gamma, and saturation — which the editor
  already does, better. In a "convert then edit" model the negative stage should keep
  only what's *intrinsically* about inverting a negative (working-space mode, black/
  white points, maybe RGB weights) and hand all tone/color to the editor. That also
  removes the mode-dependent-exposure/contrast confusion documented in
  `RapidRaw-negative-conversion-modes.md`.

### The catch (for a fork)

This design touches **core upstream files** — `image_loader.rs` (load path),
the `.rrdata` serialization, and the editor's base-image/caching path. That breaks the
"all changes in one module" property that's kept this fork clean. So it's architecturally
elegant and **not hard in absolute terms**, but it's a *worse* position for tracking
upstream than the current silo. The honest trade: tighter UX vs. messier merges.

### Verdict
The reason it doesn't feed the editor is **not** "too hard to track settings" — the
sidecar makes that trivial, and the CPU-at-ingest path already has a precedent. It's a
combination of (a) following the Productivity-tools pattern, (b) arriving after the
core froze, and (c) avoiding the genuinely hard *live-GPU* version. The restricted
CPU-ingest version you sketched is very doable; the main cost is that it spreads into
core files rather than living in the silo.

---

## Source references
- Productivity outputs: `panorama_stitching.rs` `_Pano.png`, `denoising.rs`
  `_Denoised.tiff`, `lib.rs` `_Collage.png`, `negative_conversion.rs` `_Positive.tiff`.
- Load-time transform precedent: `image_loader.rs:70` (`linear_raw_mode`).
- Per-image persistence: `.rrdata` sidecar in `file_management.rs`.
- Global analysis: `analyze_bounds` (`vals.sort_by`) in `negative_conversion.rs`.
- Git: core pipeline June 2025; `negative_conversion.rs` 2026-02-06.
</content>
