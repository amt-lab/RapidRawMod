# MOD CHANGE — Keyboard shortcut to open Negative Conversion

**What:** `Cmd+Shift+N` (macOS) / `Ctrl+Shift+N` (Win/Linux) opens the Negative
Conversion modal for the current selection — no right-click → Productivity needed.

Target resolution (mirrors the two context-menu triggers):
1. editor's open image (`s.editor.selectedImage`), else
2. library multi-selection (`s.library.multiSelectedPaths`), else
3. library active image (`s.library.libraryActivePath`).
If nothing is selected, it does nothing.

**Files touched: 1.**
| File | Layer | Note |
|---|---|---|
| `src/hooks/useKeyboardShortcuts.ts` | GUI | ⚠ **shared upstream file** — adds one entry to `builtinShortcuts` |

> ⚠ **Conflict-surface note:** unlike the other negative-module mods, this one lives
> in a core upstream file (`useKeyboardShortcuts.ts`), not our modal. It's a single
> self-contained array entry tagged `// MOD:`, so re-applying after an upstream update
> is a copy-paste, but be aware it's the second shared-file touch (after the one line
> in `lib.rs`).

---

## The change

A new entry at the top of the `builtinShortcuts` array. Builtins run *after* the
"modal already open?" and "typing in an input?" guards but *before* the
user-configurable combo map, so it's safe and can't be accidentally shadowed:

```ts
{
  // MOD: open Negative Conversion for the current selection (Cmd/Ctrl+Shift+N).
  match: (e) => (e.metaKey || e.ctrlKey) && e.shiftKey && e.code === 'KeyN',
  execute: (e, s) => {
    e.preventDefault();
    let targetPaths: string[] = [];
    if (s.editor.selectedImage) targetPaths = [s.editor.selectedImage.path];
    else if (s.library.multiSelectedPaths?.length) targetPaths = s.library.multiSelectedPaths;
    else if (s.library.libraryActivePath) targetPaths = [s.library.libraryActivePath];
    if (targetPaths.length === 0) return;
    s.ui.setUI({ negativeModalState: { isOpen: true, targetPaths } });
  },
},
```

## Changing the combo
Edit the `match` line. `e.code` is physical-key based (`'KeyN'`); swap for another
`Key*` and/or change the modifier checks.

## Test
```bash
cd ~/myProjects/neg-invert/RapidRawMod && npm run start
```
Select an image in the library (or open one in the editor) → press **Cmd/Ctrl+Shift+N**
→ the Negative Conversion modal opens for it.
</content>
