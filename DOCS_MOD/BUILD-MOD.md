# Build & Run — RapidRawMod

Run all commands from `~/myProjects/neg-invert/RapidRawMod`.

## First time only
```bash
npm install        # download GUI deps (~minutes)
```

## Run (dev mode, rebuilds on save)
```bash
npm run start      # first run compiles Rust — slow (5–15 min); opens the app window
```
Leave it running while you work. Save a `.rs` or `.tsx` file → it rebuilds and reloads.
Stop it with `Ctrl-C` in the terminal.

## Build a distributable app (optional, when you're done)
```bash
npm run tauri build
```
Output app/installer lands in `src-tauri/target/release/bundle/`.

## If Rust isn't on PATH in a new terminal
```bash
source "$HOME/.cargo/env"
```

## If a build error mentions a missing system library
Paste the error to Claude — it's a one-time `brew install <thing>`.

## If a build fails with a corrupt-crate error
Symptoms (usually after an interrupted first build): `failed to map object
file: memory map must have a non-zero length`, or `ld: symbol(s) not found for
architecture arm64` referencing a crate's `.rlib`. Fix = clean just that crate
and rebuild (keeps everything else compiled):
```bash
cd ~/myProjects/neg-invert/RapidRawMod/src-tauri
cargo clean -p <crate-name>   # e.g. rawler, jxl-encoder
cargo build
```
Last resort (slow, rebuilds everything): `cargo clean` with no `-p`.
