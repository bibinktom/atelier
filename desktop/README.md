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

## Code signing

The CI is already wired for signing — it activates the moment you add the GitHub
secrets (until then it builds unsigned, no-op). Repo → Settings → Secrets and
variables → Actions.

**macOS** (Apple Developer Program, $99/yr; Tauri signs + notarizes automatically):

| Secret | What |
|--------|------|
| `APPLE_CERTIFICATE` | base64 of your **Developer ID Application** `.p12` (`base64 -i cert.p12 \| pbcopy`) |
| `APPLE_CERTIFICATE_PASSWORD` | the password you set when exporting the `.p12` |
| `APPLE_SIGNING_IDENTITY` | e.g. `Developer ID Application: Your Name (TEAMID)` |
| `APPLE_ID` | your Apple ID email |
| `APPLE_PASSWORD` | an **app-specific password** (appleid.apple.com → Sign-In & Security) |
| `APPLE_TEAM_ID` | your 10-char Team ID (developer.apple.com → Membership) |

**Windows** (the CI step is a template for an exportable `.pfx`):

| Secret | What |
|--------|------|
| `WINDOWS_CERT_BASE64` | base64 of your code-signing `.pfx` |
| `WINDOWS_CERT_PASSWORD` | its password |

Modern OV/EV certs are HSM-only (no exportable `.pfx`) — for those use
**Azure Trusted Signing** (~$10/mo) or DigiCert KeyLocker and swap the signing
step for `azuresigntool`. Linux needs no signing; CI publishes `SHA256SUMS` instead.

## Still TODO

- Auto-update (tauri-plugin-updater) + a release feed.
- Real app icons / branding (`tauri icon path/to/icon.png`).
