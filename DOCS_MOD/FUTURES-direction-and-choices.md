# Futures — Direction & Choices

**Date:** 2026-06-12
**Status:** planning notes, nothing committed.

A future project is to **merge `neg-invert/gui/test_inverter.py` (+ its multi-image
sibling `film_strip_inverter.py`) with this repo's `python-clone`** into one coherent
standalone inverter. Today they are two lineages of the same idea with overlapping but
*inconsistent* tweaks, and it is not yet clear which parts survive. This doc inventories
the differences, lists the open choices, and records the platform decision (Python/PyQt6
vs Rust/Tauri), including the raw-decoder question that gates going Rust.

---

## The two lineages

| | `python-clone` (this repo) | `neg-invert/gui/test_inverter.py` (+ `film_strip_inverter.py`) |
|---|---|---|
| Origin | Python re-implementation of RapidRawMod's `negative_conversion.rs` modal | The original experimental inverter the Rust mods were ported *from* |
| Tone model | **Print-grade sigmoid** (exposure = sigmoid-centre shift, contrast = sigmoid steepness) + gamma | `apply_gamma` → `apply_s_curve` → `apply_brightness` (parabolic midtone lift) → `apply_local_contrast` → `apply_temperature_tint` → `apply_saturation` |
| Colour control | **3 RGB "Color Timing" weights** (per-channel multiply in working space, pre-sigmoid) | **Temp/Tint** (2-DOF WB, display space, end of chain) + per-channel bp/wp stretch |
| Modes | lin / psl / log | log / lin / psl (same family) |
| Black/White points | auto percentile + click pick + bp/wp tweak | auto percentile + click pick (B/W key-hold) + bp/wp tweak |
| Local contrast | ✗ | ✅ CLAHE/histeq soft-light blend — **very useful** |
| Soft clip (knee) | ✗ (hard clip at the stretch) | ✅ parabolic knee roll-off `stretch(..., soft_clip)` — **very useful** |
| Flip / rotate | ✗ | ✅ D4 dihedral group |
| Exposure/brightness | exposure = sigmoid shift only | **lacks a true exposure/brightness slider — begs for one** |
| Saturation | ✗ (only highlight desat inside pipeline) | ✅ centred offset (`1.0 + offset`) |
| Multi-image | ✗ (one at a time) | ✅ `film_strip_inverter.py`: thumbnails, drag-drop, shortcuts, gray-solver — **extremely useful** |
| Globals-at-top discipline | ✅ (we just did this: percentiles, margin, proxy size, sigmoid/desat/luma consts) | partial (`D_*` defaults; documented in `neg-invert/docs/gui_state_inventory.md`) |

**Takeaway:** `python-clone` is the cleaner *engine* (well-factored, all knobs global);
`test_inverter`/`film_strip` is the richer *experience* (local contrast, soft clip,
flip/rotate, saturation, multi-image). A merge wants clone's engine hygiene + neg-invert's
UX surface.

---

## Known incompatibilities & inconsistencies (what makes the merge non-trivial)

1. **Exposure & contrast are not mode-neutral.** The single sigmoid runs on the
   normalized stretch position `N`, but what `N` *measures* differs per mode, so the same
   slider value means different things in lin/log/psl. Fully written up in
   **`RapidRaw-negative-conversion-modes.md` → "Caveat: exposure & contrast are not
   mode-neutral."** (This is the doc you were trying to remember.)

2. **Two different tone philosophies.** clone = one print-grade sigmoid (exposure+contrast
   coupled into a single curve). neg-invert = a stack of independent stages (gamma,
   s-curve, parabolic brightness, local contrast). These don't map 1:1 — picking one
   tone model is the biggest design decision.

3. **Colour control mismatch.** clone's 3 RGB weights act *in working space before the
   sigmoid* (in log mode that's a per-channel **gamma/power on transmittance** — changes
   contrast as well as balance). neg-invert's temp/tint is a *display-space 2-DOF gain at
   the end of the chain*. The real "color timing" in neg-invert is its per-channel bp/wp
   stretch, not temp/tint. (Full comparison in the chat that produced this doc; worth
   migrating into a standalone note if the merge proceeds.)

4. **Default & range drift.** e.g. clone gamma default **2.2**, neg-invert `apply_gamma`
   default **1.0**; clone RGB-weight range just **halved to 0.75–1.5**
   (`MOD-CHANGES-slider-sensitivity.md`) while neg-invert has no RGB weights at all;
   saturation is a centred offset in neg-invert and absent in clone. Any merged slider set
   needs a single agreed convention (see `neg-invert/docs/gui_state_inventory.md`).

5. **bp/wp tweak conventions** are close but not identical across the two — both use
   "slide along the black→white axis by tweak·range," but ranges/steps and the
   working-space storage differ. Verify they're bit-equivalent before unifying.

6. **Hard vs soft clip.** clone clips the stretch hard; neg-invert rolls off with a
   parabolic knee (`soft_clip` radius). Different highlight/shadow behaviour at the
   extremes.

---

## Open choices (decide before/while merging)

- **One tone model or a superset?** Keep the print-grade sigmoid, the neg-invert stage
  stack, or expose both behind a toggle (messy)? Leaning: pick neg-invert's stack
  (richer, more orthogonal) and treat the sigmoid as an optional "print grade" stage.
- **Colour control:** keep 3 RGB weights, temp/tint, or both? They do different jobs
  (per-channel contrast vs WB nudge); a merged tool might keep *both* but be explicit
  about where each sits.
- **Add the missing exposure/brightness slider** to whatever survives — both tools want a
  real linear-ish exposure separate from the contrast curve.
- **Which mode(s) survive** (`NOTES-backlog.md` item D is still open). A standalone tool
  could afford to keep all three as a comparison harness, or commit to log.
- **Adopt soft clip + local contrast + flip/rotate + saturation** from neg-invert into the
  merged engine (these are the clearly-worth-keeping features).
- **Multi-image first-class:** base the merged GUI on the `film_strip_inverter` shell
  (thumbnails/drag-drop/shortcuts) rather than the single-image window.
- **Carry the globals-at-top discipline** from `python-clone` across the whole merged
  engine.

---

## Platform: Python/PyQt6 vs Rust/Tauri

**Your lean:** standalone inverter, Python/PyQt6 for familiarity; curious about Rust +
the web-like Tauri GUI; would only switch to Rust if its raw decoding is genuinely better
than rawpy.

### My honest take on Tauri/Rust

The Tauri model (Rust backend + React/TS web frontend + IPC) is genuinely attractive and
RapidRAW proves it can be fast and polished: flexible/modern UI, easy theming, and a real
GPU pipeline (`wgpu`/WGSL shaders) that PyQt won't match for real-time full-res work. The
distributable is a small native binary.

But for *this* project the costs are real:
- **Two languages + an IPC boundary.** Every feature is split across Rust commands and TS
  UI with serialization in between — more ceremony, slower iteration than a numpy REPL.
- **Learning curve on two fronts at once** (Rust *and* the web stack) while also doing the
  image-science exploration — that's three hard things at the same time.
- **Iteration speed** matters most when you're constantly tweaking the math; Python wins
  decisively there (edit-save-see, no compile).

Where Rust/Tauri genuinely pays off: shipping to other people, real-time GPU on full-res,
or wanting the web UI toolkit. For a personal, math-heavy, single-user inverter, those are
weak pulls.

**Suggestion:** treat Rust/Tauri as a *separate learning project* (you already have the
RapidRAW fork as a live reference), not as the vehicle for the merge. Build the unified
inverter in Python/PyQt6 now; if the design stabilizes and you later want a distributable
or GPU version, port it then — and learn Rust on its own terms rather than under feature
pressure.

### The raw-decoder question (the thing that would actually change the decision)

- **rawpy = Python bindings to LibRaw** — the industry-standard C++ decoder (dcraw
  heritage). Broadest camera coverage, mature demosaic options (AHD/DCB/…), highlight
  recovery, well-tested colour matrices. The safe, capable default.
- **RapidRAW uses `rawler`** (a fork of the dnglab pure-Rust decoder:
  `CyberTimon/RapidRAW-DngLab` in `src-tauri/Cargo.toml`). Clean, embeddable in a Rust GPU
  pipeline, strong DNG support — but **younger and historically narrower** than LibRaw in
  camera coverage and demosaic choices.

**Verdict:** there's no strong evidence `rawler` decodes *better* than LibRaw; if anything
LibRaw is more proven and more capable. So if "better raw decode" is your bar for going
Rust, that bar is unlikely to be cleared — it actually argues *for* staying with
Python/rawpy. The honest way to settle it: **A/B the same RAW** (a NEF you know well)
through rawpy/LibRaw vs RapidRAW/rawler at matched white-balance/demosaic settings and look
at mask removal, highlight roll-off, and colour. Decide on your own eyes, but don't expect
rawler to be clearly superior.

### Bottom line

- **Recommended near-term:** build the merged standalone inverter in **Python/PyQt6**,
  starting from the `film_strip_inverter` shell, with `python-clone`'s clean globally-tuned
  engine, and pull in neg-invert's local contrast / soft clip / flip-rotate / saturation.
- **Rust/Tauri:** keep as a parallel learning track + reference; revisit for a
  distributable/GPU build once the design is settled and only if an A/B convinces you the
  decode (or GPU speed) is worth it.

---

## Pointers
- Exposure/contrast mode caveat: `RapidRaw-negative-conversion-modes.md`.
- Slider-range change: `MOD-CHANGES-slider-sensitivity.md`.
- Mode-survivor decision: `NOTES-backlog.md` item D.
- Architecture (silo vs feed-the-editor): `negative-module-architecture-analysis.md`.
- neg-invert state contract: `neg-invert/docs/gui_state_inventory.md`.
- Raw decoder: `src-tauri/Cargo.toml` (`rawler` git dep).
