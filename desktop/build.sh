#!/usr/bin/env bash
# Build the Atelier desktop app for the current OS.
#
# Prereqs (install once, per build machine):
#   - Python 3.12 + the backend & tools deps, and pyinstaller:
#       python -m venv .venv && . .venv/bin/activate
#       pip install -r backend/requirements.txt -r tools/requirements.txt pyinstaller
#   - Node 18+ (for the frontend export).
#   - Rust + Tauri CLI:  cargo install tauri-cli --version '^2'
#   - Tauri OS deps: https://tauri.app/start/prerequisites/
#
# Run from the repo root:  bash desktop/build.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> 1/4  Static frontend export"
( cd frontend && npm ci 2>/dev/null || npm install
  DESKTOP_EXPORT=1 NEXT_PUBLIC_BACKEND_URL="" npm run build )

echo "==> 2/4  Bundle Python sidecars (PyInstaller)"
pyinstaller --noconfirm --distpath desktop/dist --workpath desktop/build desktop/pyinstaller/atelier-backend.spec
pyinstaller --noconfirm --distpath desktop/dist --workpath desktop/build desktop/pyinstaller/atelier-tools.spec
# The launcher (run_local.py) — onefile; it finds atelier-backend/atelier-tools as siblings.
pyinstaller --noconfirm --onefile --name atelier-launcher \
  --distpath desktop/dist --workpath desktop/build desktop/run_local.py

echo "==> 3/4  Stage sidecars + frontend for Tauri"
TRIPLE="$(rustc -vV | sed -n 's/host: //p')"
mkdir -p desktop/src-tauri/binaries desktop/src-tauri/frontend-dist
EXE=""; [ "$(uname)" = "MINGW"* ] && EXE=".exe" || true
for b in atelier-launcher atelier-backend atelier-tools; do
  cp "desktop/dist/$b$EXE" "desktop/src-tauri/binaries/$b-$TRIPLE$EXE"
done
rm -rf desktop/src-tauri/frontend-dist && cp -r frontend/out desktop/src-tauri/frontend-dist

echo "==> 4/4  Tauri build"
( cd desktop/src-tauri && cargo tauri build )

echo "Done. Installers are under desktop/src-tauri/target/release/bundle/"
