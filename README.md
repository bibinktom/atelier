# Atelier

A self-hosted AI workspace that runs on **your own machine** — real shell, real
filesystem, and connected USB hardware (program an ESP32, drive a phone over ADB) —
with the agent able to **install the tools it needs on demand**. Inference runs on
your own OpenRouter account, so it costs the operator nothing.

It ships two ways:

- **Desktop app** (Tauri) — a native window for Windows / macOS / Linux. The agent
  works on a real folder you pick, runs your native shell, talks to USB devices, and
  asks before anything destructive.
- **Self-hosted server** (Docker Compose) — the original multi-user, Google-OAuth
  workspace with a hardened tools sandbox and an AI firewall.

## ⬇️ Download (desktop app)

Grab the installer for your OS from the **[latest release](../../releases/latest)**:

| OS | File | Install |
|----|------|---------|
| **Windows** | `Atelier_*_x64-setup.exe` or `.msi` | run the installer |
| **macOS** (Apple Silicon) | `Atelier_*_aarch64.dmg` | open, drag **Atelier** to Applications |
| **macOS** (Intel) | `Atelier_*_x64.dmg` | open, drag **Atelier** to Applications |
| **Linux** | `Atelier_*_amd64.AppImage` | `chmod +x` and double-click |
| **Linux** (Debian/Ubuntu) | `Atelier_*_amd64.deb` | `sudo apt install ./Atelier_*_amd64.deb` |
| **Linux** (Fedora/RHEL) | `Atelier-*.x86_64.rpm` | `sudo dnf install ./Atelier-*.x86_64.rpm` |

> **First launch:** the app is not yet code-signed, so your OS will warn once.
> **macOS:** right-click the app → **Open** (or `xattr -dr com.apple.quarantine
> /Applications/Atelier.app`). **Windows:** SmartScreen → **More info → Run anyway**.
> Linux has no warning.

After it opens: connect your OpenRouter account in **Settings**, pick a project
folder, and start working.

## What it can do

- Work on real files in a folder you choose — find, edit, organize, build, run.
- Run your **native shell** (bash/zsh, or PowerShell on Windows).
- **Provision tools on demand** — `adb`, `arduino-cli`, `esptool`, etc. are installed
  automatically the first time a task needs them.
- **Program microcontrollers** over USB (ESP32 / Arduino) and **drive Android phones**
  over ADB.
- **Confirm before damage** — destructive or device-writing commands (`rm -rf`, flashing,
  `adb install`, …) pause for an Allow / Always / Deny prompt.
- Web search + fetch, and generate PDF / xlsx / pptx / flyers.

## Build from source

- **Desktop app:** see [`desktop/README.md`](desktop/README.md). In short:
  install Rust + Node + Python + PyInstaller + Tauri's OS prerequisites, then
  `bash desktop/build.sh`. CI (`.github/workflows/desktop.yml`) builds all three
  OSes on every `v*` tag.
- **Server:** `cp .env.example .env` (fill in secrets) and `docker compose up --build`.
  See [`CLAUDE.md`](CLAUDE.md) for the full architecture.

## License

See repository for license terms.
