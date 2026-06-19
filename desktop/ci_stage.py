"""Stage the PyInstaller sidecars + frontend export for `tauri build` (cross-OS).

Run AFTER the frontend export and the three PyInstaller builds, BEFORE tauri build:
    python desktop/ci_stage.py

Copies desktop/dist/atelier-{launcher,backend,tools}[.exe] into
src-tauri/binaries/ with the Rust target-triple suffix Tauri's externalBin needs,
and frontend/out -> src-tauri/frontend-dist. Pure Python so it works identically on
Linux, macOS and Windows.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def host_triple() -> str:
    out = subprocess.check_output(["rustc", "-vV"], text=True)
    for line in out.splitlines():
        if line.startswith("host:"):
            return line.split(":", 1)[1].strip()
    sys.exit("ci_stage: could not determine the Rust host triple (is rustc installed?)")


def main() -> None:
    triple = host_triple()
    exe = ".exe" if os.name == "nt" else ""
    src = REPO / "desktop" / "dist"
    bindir = REPO / "desktop" / "src-tauri" / "binaries"
    bindir.mkdir(parents=True, exist_ok=True)

    for name in ("atelier-launcher", "atelier-backend", "atelier-tools"):
        s = src / f"{name}{exe}"
        if not s.is_file():
            sys.exit(f"ci_stage: missing sidecar {s} — did PyInstaller run?")
        d = bindir / f"{name}-{triple}{exe}"
        shutil.copy2(s, d)
        print(f"staged {d}")

    fout = REPO / "frontend" / "out"
    if not fout.is_dir():
        sys.exit(f"ci_stage: missing {fout} — run the DESKTOP_EXPORT frontend build first")
    fdst = REPO / "desktop" / "src-tauri" / "frontend-dist"
    if fdst.exists():
        shutil.rmtree(fdst)
    shutil.copytree(fout, fdst)
    print(f"staged {fdst}")


if __name__ == "__main__":
    main()
