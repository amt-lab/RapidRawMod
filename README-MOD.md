# RapidRawMod — orientation for a new agent

This is a fork of **RapidRAW** (https://github.com/cybertimon/rapidraw), a Tauri
(Rust backend) + React/TypeScript desktop photo editor. The point of this fork is to
**experiment with the negative-film inversion module** — nothing else in the app is
being actively changed.

This README is a map, not an explanation. The real understanding lives in `DOCS_MOD/`
(read those before changing the algorithm) and in the two modded source files below.
This is work-in-progress; expect rough edges and parked ideas.

---

## Where the work is (start here)

Almost every mod lives in **two files**, by design, to keep merges with upstream clean:

| What | File |
|---|---|
| Backend: conversion engine, Tauri commands, point analysis | `src-tauri/src/negative_conversion.rs` (~570 lines) |
| Frontend: the modal UI (sliders, mode selector, B/W pick, overlay) | `src/components/modals/NegativeConversionModal.tsx` (~700 lines) |
| Command registration (3 lines only) | `src-tauri/src/lib.rs` — `preview_negative_conversion`, `convert_negatives`, `sample_negative_point` |

Everything else under `src-tauri/src/` and `src/` is upstream RapidRAW. Treat it as
read-only context unless a task explicitly reaches into it.

The Tauri commands the modal calls:
- `preview_negative_conversion` — processes a cached ≤1080px proxy for live preview.
- `convert_negatives` — full-resolution save (writes a `_Positive.tiff`).
- `sample_negative_point` — reads a clicked pixel's density (B/W point picking).

---

## The mental model in one paragraph

The converter decodes the scanned negative, maps it into a **working space**
(mode: `log` density / `lin` / `psl`), finds per-channel **black & white points**
(auto percentiles, or hand-picked + tweaked), **stretches** between them, then applies
RGB weights → exposure/contrast sigmoid → highlight desaturation → gamma, and writes a
positive TIFF. The full math is in `DOCS_MOD/RapidRaw-negative-conversion-algorithm.md`
(Part A = how the points are chosen, Part B = the fixed pipeline).

---

## Doc map (`DOCS_MOD/`)

Read these as needed; the first three are the core reference.

| File | What it gives you |
|---|---|
| `RapidRaw-negative-conversion-algorithm.md` | **The algorithm.** Part A (point selection) + Part B (pipeline) + full pseudocode. |
| `RapidRaw-negative-conversion-modes.md` | The 3 working-space modes (log/lin/psl), why PSL exists, mode caveats. |
| `negative-module-architecture-analysis.md` | **The big open question:** why the module saves an intermediate file instead of feeding the editor, and what a "feeds-the-editor" redesign would cost. |
| `NOTES-backlog.md` | Parked ideas + their status (✅ done / open). Read before picking up new work. |
| `MOD-CHANGES-*.md` | One file per shipped feature (gamma slider, B/W pick, conversion modes, B/W readout, analysis overlay, shortcut). Implementation records. |
| `RapidRaw-negative-conversion-algorithm-OLD.md` | Pre-mod algorithm, for diffing. |
| `RapidRaw-vs-neg-invert-log-comparison.md` | Comparison against the sibling Python project. |
| `BUILD-MOD.md` | Build/run + recovery from corrupt-crate build errors. |

**Sibling project:** the inversion ideas are prototyped in Python at
`~/myProjects/neg-invert/` (`gui/test_inverter.py`, `core/invert.py`). That repo has its
own `CLAUDE.md`. Several mods here are ports from there.

---

## Open questions (what to think about)

1. **Intermediate file vs. live editor.** The module saves `_Positive.tiff` and you
   re-load it to edit. Can/should this feed the editor directly? See
   `negative-module-architecture-analysis.md`.
2. **If the intermediate-save route stays:** would a dedicated film-strip GUI (batch a
   roll faster) make sense?
3. **Which conversion mode wins** (log/lin/psl)? The 3-mode selector is a test harness,
   not a final design — see `NOTES-backlog.md` item D.

---

## Run it

```bash
cd ~/myProjects/neg-invert/RapidRawMod
npm install        # first time only
npm run start      # dev mode; first Rust compile is slow (5–15 min), then opens the app
```

Save a `.rs` or `.tsx` file → it rebuilds and reloads. `Ctrl-C` to stop. If Rust isn't
on PATH: `source "$HOME/.cargo/env"`. Build-error recovery: see `DOCS_MOD/BUILD-MOD.md`.

> Note: if you move this folder, stale absolute paths get baked into
> `src-tauri/target/`. Fix by removing `src-tauri/target/debug/build/tauri-*` and
> `target/debug/.fingerprint/tauri-*`, then rebuild.

---

## Git / GitHub

Everyday git workflow, remotes, undo, and pulling upstream updates live in a separate
cheat-sheet: **`README-MOD-git.md`**.
