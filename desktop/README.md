# Atelier desktop build

Packages Atelier as a native desktop app (Tauri) that runs entirely on the user's
own machine: real shell, real filesystem, USB devices (ESP32 / phone via ADB), and
on-demand tool provisioning. Inference is the user's own OpenRouter connection.

## Architecture

```
┌─────────────────────────────┐
│ Atelier.app  (Tauri shell)  │   native window (OS webview)
│   src-tauri/src/main.rs      │
│     spawns ▼                 │
│   atelier-launcher  ─────────┼─▶ run_local.py (PyInstaller)
│     spawns ▼  ▼              │     • picks free ports, persists secrets
│   atelier-backend  atelier-tools    • writes ~/.atelier/backend_url
│     (FastAPI)      (FastAPI, native │
│        │ serves UI + API      shell/files/devices)
│        ▼                     │
│   webview loads backend_url  │   ← single origin: UI + API
└─────────────────────────────┘
```

- **One origin.** The backend serves the exported Next.js UI *and* the API, so the
  webview just loads `http://127.0.0.1:<port>/`. No CORS, no Node at runtime.
- **Two sidecar binaries** (`atelier-backend`, `atelier-tools`) so each owns its own
  `app` package — a single binary can't bundle both (name collision). `run_local`
  prefers the bundled binaries and falls back to `python -m uvicorn` in dev.

## Run in dev (no packaging — verified)

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r backend/requirements.txt -r tools/requirements.txt pyserial
# (optional) build the UI so the backend serves it:
( cd frontend && npm install && DESKTOP_EXPORT=1 NEXT_PUBLIC_BACKEND_URL="" npm run build )
python desktop/run_local.py --root "$HOME"        # open the printed backend URL
```

`run_local.py` flags: `--root DIR` (filesystem root the agent may touch, default
`~`), `--data-dir DIR` (default `~/.atelier`), `--backend-port N`, `--firewall`.

## Build the installer

> The steps below need a Rust + Tauri toolchain and PyInstaller, which were **not
> available in the authoring environment** — the Rust shell (`src-tauri/`) and the
> PyInstaller specs are authored to convention but compile/bundle on your machine.
> The Python runtime, single-origin serving, device tools, and frontend export are
> all verified; this last mile (native binaries + signed installers) is per-OS.

Prereqs: Python 3.12, Node 18+, Rust, `cargo install tauri-cli --version '^2'`, and
the [Tauri OS prerequisites](https://tauri.app/start/prerequisites/). Then:

```bash
bash desktop/build.sh
```

Outputs land in `desktop/src-tauri/target/release/bundle/` (`.dmg`/`.app` on macOS,
`.msi`/`.exe` on Windows, `.deb`/`.AppImage` on Linux). Build on each target OS (or
use CI matrix runners); Tauri does not cross-compile the installers.

Add app icons under `desktop/src-tauri/icons/` (`tauri icon path/to/icon.png`
generates every size) before the final build.

## Permission gate (Phase D — shipped)

On the local build the agent asks before genuinely destructive or device-writing
commands — `rm -rf`, `mkfs`/`dd`, `sudo`, `git push --force`, `esptool`/`arduino-cli`
flashing, `adb install/push`, `curl … | sh`, etc. The turn pauses, the UI shows
**Allow once / Always allow / Deny**, and the decision resolves the turn server-side.
"Always allow" whitelists that command class for the user (`GET/DELETE /permissions`
to review/revoke). Ordinary edits, reads, builds and package installs never prompt.
Disable with `PERMISSIONS_ENABLED=0`. Classifier + store live in
`backend/app/permissions.py`; the gate is in `chat.py`'s `_node_act`.

## Still TODO for a shippable release

- Code-signing + notarization (macOS) and signing (Windows) — needs Apple/MS
  developer accounts; configure under `bundle` in `tauri.conf.json`.
- Auto-update (tauri-plugin-updater) + a release feed.
- App icons + branding (`tauri icon path/to/icon.png`).
