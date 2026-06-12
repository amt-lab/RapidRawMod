# Git / GitHub cheat-sheet — RapidRawMod

A reminder for everyday git, written for this repo. Use it until the flow is automatic.
The whole everyday loop is just four commands:

```bash
git status        # what changed?
git add -A        # stage everything (moves, new files, deletions)
git commit -m "Short description of what changed"
git push          # upload to GitHub
```

**Mental model:** `git add` puts changes on a tray (the "staging area"). `git commit`
takes a photo of the tray and saves it **on your computer**. `git push` sends those
photos to **GitHub**. Nothing leaves your machine until you push.

---

## This repo's setup (one-time facts to remember)

**Two remotes** (a "remote" = a copy of the repo on GitHub):

| Remote | Points to | Use |
|---|---|---|
| `origin` | `amt-lab/RapidRawMod` (your fork) | where you **push your work** |
| `upstream` | `CyberTimon/RapidRAW` (the original) | where you **pull updates from** |

**Two branches** (a "branch" = an independent line of work):

- **`mod`** — your modifications. **This is where you work.**
- **`main`** — a clean mirror of upstream RapidRAW (don't put your work here).

Check which branch you're on anytime with `git status` (first line) or `git branch`
(the `*` marks the current one).

---

## Viewing changes — which command?

**Lost? Just pick the row that matches what you want.** (Don't read the whole section —
grab one line.)

| I want to see… | Command |
|---|---|
| Which files I touched (+ am I pushed?) | `git status` |
| The same, compact one-line-per-file | `git status -sb` |
| My edits **not yet** staged | `git diff` |
| My edits **already staged** (after `git add`) | `git diff --staged` |
| What my **last commit** recorded | `git show` |
| Just the file list of the last commit | `git show --stat` |

That's all you need 95% of the time. The rest below is "why," only if curious.

### The one distinction worth knowing: `status` vs `show`

- **`git status`** = your **desk**: what you've changed but *not committed yet*. Forward-looking.
- **`git show`** = a **filed document**: what a commit *already* recorded. Backward-looking.

So `status` won't show a commit's contents, and `show` won't show your uncommitted work.
You use both — `status` around committing, `show` to inspect the result.

### Reading a diff (the `+`/`-` screen)

- Lines starting `+` were **added**, `-` were **removed**.
- A brand-new file shows as one big block of `+` lines — that's normal.
- It opens in a scrolling pager: **space** / arrows to move, **`q`** to quit.
- Add `--stat` to any of these for just a **file summary** instead of full detail
  (`git diff --stat`, `git show --stat`).

`git status` is your friend — run it before and after anything. It also tells you the
**sync state** with GitHub (see "Reading the sync state" below).

---

## Save your work (commit + push)

```bash
git add -A                                  # stage ALL changes (or: git add <file>)
git commit -m "Short description of change"
git push                                    # upload the mod branch to origin
```

- A **commit** is a local checkpoint; commit often (it's free and local).
- **Push** when you want it backed up on GitHub / shared.
- `git add -A` correctly records **file moves and deletions too** — e.g. moving the docs
  into `DOCS_MOD/` shows up as `renamed:` once staged, not as a mysterious delete+add.

### Writing a commit message
One short line, present tense, describing *what changed*. Examples:
`Add gamma slider to negative modal`, `Fix B/W pick in lin mode`,
`Move mod docs into DOCS_MOD`.

---

## Reading the sync state (so you KNOW a push worked)

After `git push`, confirm it actually landed. Run:

```bash
git status -sb
```

- `## mod...origin/mod` → **in sync.** Local and GitHub match. 
- `## mod...origin/mod [ahead 1]` → you have **1 commit not yet on GitHub.** The push
  didn't complete — run `git push` again and watch its output.

> **Lesson from experience:** a commit can be saved locally while the push silently
> hasn't gone through (a credential prompt, a hiccup). The commit existing is *not* proof
> it's on GitHub. Always glance at `git status` after pushing — "ahead" means run
> `git push` again.

A successful push prints a line like `f19f9d62..8601a8c2  mod -> mod`.

---

## Undo (before you've pushed)

```bash
git restore <file>            # discard your changes to one file — CAREFUL, not recoverable
git restore --staged <file>   # unstage a file (un-tray it) but KEEP your changes
git restore .                 # discard ALL unstaged changes — CAREFUL
```

Already committed but want to redo the message / add a forgotten file? (only if you
haven't pushed yet):

```bash
git add <forgotten-file>
git commit --amend            # opens an editor to fix the last commit
```

When unsure, **stop and ask** rather than running an undo you're not certain about.

---

## Ignoring scratch files (`.gitignore`)

If junk keeps showing up in `git status` as untracked, add it to the `.gitignore` file so
git stops noticing it. Already ignored here: `node_modules`, `src-tauri/target/`,
`/tmp`, `__pycache__/`, `*.pyc`. To ignore something new, add its name on a new line in
`.gitignore` (e.g. `scratch/`), then it disappears from `git status`.

---

## Pull upstream RapidRAW updates (occasional, can get messy)

When the original RapidRAW gets new features you want:

```bash
git fetch upstream            # download upstream's new commits (changes NOTHING in your files yet)
git checkout main             # switch to the clean mirror branch
git merge upstream/main       # bring main up to date with upstream
git checkout mod              # switch back to your work
git merge main                # replay upstream's changes underneath your mods
```

Because the mods are confined to ~two files (`negative_conversion.rs` +
`NegativeConversionModal.tsx`), conflicts should be rare and localized.

**If a merge conflict appears:** git pauses and marks the clashing spots in the files
with `<<<<<<<`, `=======`, `>>>>>>>`. Don't force it — **stop and ask for help**, or
abort and reset with `git merge --abort` to get back to where you were.

---

## Glossary

| Term | Plain meaning |
|---|---|
| **remote** | a copy of the repo hosted on GitHub (`origin`, `upstream`) |
| **branch** | an independent line of work (`mod`, `main`) |
| **stage / `git add`** | mark changes to go into the next commit |
| **commit** | a saved checkpoint, stored locally |
| **push** | upload local commits to GitHub |
| **fetch** | download remote commits (without touching your files) |
| **merge** | combine another branch's commits into your current branch |
| **ahead / behind** | local has commits GitHub doesn't (ahead), or vice versa (behind) |

---

## When in doubt

1. `git status` — it almost always tells you what to do next.
2. Don't run a destructive command (`restore`, `reset`, `--force`) you're unsure about.
3. Ask. A confusing state is much easier to fix *before* you pile more commands on it.
