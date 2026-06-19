#!/usr/bin/env python3
"""Native local runner for the Atelier desktop build (Phase A).

Starts the FastAPI **backend** and **tools** services as plain host processes
(uvicorn) — NO Docker — so the agent's tools run natively on the user's machine
with real filesystem + shell + (later) USB device access. This is what the Tauri
shell will spawn as its sidecar; for now it's also runnable standalone.

Local posture (ATELIER_LOCAL=1):
  - single auto-logged-in user, no Google OAuth (see auth._local_user);
  - inference is the user's own OpenRouter connection (user-funded);
  - tools operate on real files under ATELIER_LOCAL_ROOT (default: your home dir);
  - data (SQLite, generated files) lives under the data dir (default ~/.atelier).

Usage:
    python desktop/run_local.py [--root DIR] [--data-dir DIR] [--backend-port N]

The guard (firewall) and preview (LibreOffice→PDF) sidecars are optional locally
and are OFF by default here — the firewall fail-opens and preview is on-demand.
"""
import argparse
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO / "backend"
TOOLS_DIR = REPO / "tools"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_health(url: str, timeout: float = 40.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status < 500:
                    return True
        except Exception:
            time.sleep(0.4)
    return False


def _persisted(data_dir: Path, name: str, gen) -> str:
    """Read a persisted secret from the data dir, generating + storing it once.
    Stable across restarts so sessions / encrypted keys keep working."""
    f = data_dir / name
    if f.is_file():
        return f.read_text().strip()
    val = gen()
    f.write_text(val)
    try:
        f.chmod(0o600)
    except OSError:
        pass
    return val


def _fernet_key() -> str:
    # Valid Fernet key format (urlsafe base64, 32 bytes) without importing cryptography.
    import base64
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()


def _sidecar_cmd(name: str, cwd: Path, port: int) -> tuple[list[str], Path | None]:
    """How to launch a service. Prefer a bundled PyInstaller binary (production:
    `atelier-backend` / `atelier-tools`, which collision-avoid by each owning its
    own `app` package); fall back to dev `python -m uvicorn`."""
    explicit = os.environ.get(f"ATELIER_{name.upper().replace('-', '_')}_BIN")
    sibling = Path(sys.executable).parent / f"atelier-{name}{'.exe' if os.name == 'nt' else ''}"
    binary = explicit or (str(sibling) if getattr(sys, "frozen", False) and sibling.exists() else None)
    if binary:
        return [binary, "--host", "127.0.0.1", "--port", str(port)], None
    return ([sys.executable, "-m", "uvicorn", "app.main:app",
             "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"], cwd)


def _spawn(name: str, cwd: Path, port: int, env: dict) -> subprocess.Popen:
    cmd, run_cwd = _sidecar_cmd(name, cwd, port)
    print(f"[run_local] starting {name} on 127.0.0.1:{port}")
    return subprocess.Popen(cmd, cwd=(str(run_cwd) if run_cwd else None), env=env)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.environ.get("ATELIER_LOCAL_ROOT", str(Path.home())),
                    help="filesystem root the agent may operate under (default: home dir)")
    ap.add_argument("--data-dir", default=os.environ.get("ATELIER_DATA_DIR", str(Path.home() / ".atelier")),
                    help="where SQLite + generated files live (default: ~/.atelier)")
    ap.add_argument("--backend-port", type=int, default=int(os.environ.get("ATELIER_BACKEND_PORT", "0") or 0))
    ap.add_argument("--firewall", action="store_true", help="enable the guard firewall (needs the guard sidecar)")
    args = ap.parse_args()

    data_dir = Path(args.data_dir).expanduser().resolve()
    (data_dir / "files").mkdir(parents=True, exist_ok=True)
    root = Path(args.root).expanduser().resolve()

    tools_port = _free_port()
    backend_port = args.backend_port or _free_port()
    backend_url = f"http://127.0.0.1:{backend_port}"

    common = {
        **os.environ,
        "ATELIER_LOCAL": "1",
        "ATELIER_LOCAL_ROOT": str(root),
        # Where ensure_capability provisions external CLIs (adb, arduino-cli, …).
        "ATELIER_BIN_DIR": str(data_dir / "bin"),
        "ATELIER_LIB_DIR": str(data_dir / "lib"),
        "ATELIER_PYENV_DIR": str(data_dir / "pyenv"),
        "PYTHONUNBUFFERED": "1",
    }
    # If a static frontend export exists (built or bundled), serve it from the
    # backend so a single URL gives the whole app. FRONTEND_DIST env overrides.
    frontend_dist = os.environ.get("FRONTEND_DIST") or str(REPO / "frontend" / "out")
    serve_frontend = os.path.isdir(frontend_dist)

    tools_env = {**common, "FILES_DIR": str(data_dir / "files")}
    backend_env = {
        **common,
        "TOOLS_URL": f"http://127.0.0.1:{tools_port}",
        "DATABASE_URL": f"sqlite:///{data_dir / 'app.db'}",
        "FILES_DIR": str(data_dir / "files"),
        "WORKSPACES_DIR": str(root),
        "SESSION_SECRET": _persisted(data_dir, "session_secret", lambda: secrets.token_urlsafe(32)),
        "KEY_ENCRYPTION_KEY": _persisted(data_dir, "key_encryption_key", _fernet_key),
        "PUBLIC_BACKEND_URL": backend_url,
        "PUBLIC_FRONTEND_URL": f"http://127.0.0.1:{backend_port}",
        # Offline-friendly defaults for a local boot.
        "SKILLS_CATALOG_ENABLED": os.environ.get("SKILLS_CATALOG_ENABLED", "0"),
        "FIREWALL_ENABLED": "1" if args.firewall else os.environ.get("FIREWALL_ENABLED", "0"),
        **({"FRONTEND_DIST": frontend_dist} if serve_frontend else {}),
    }

    # Trap SIGTERM (what the Tauri shell / a `kill` sends on close) and turn it into
    # the same clean shutdown path as Ctrl+C, so child uvicorns are never orphaned.
    def _on_term(_sig, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _on_term)

    procs: list[subprocess.Popen] = []
    try:
        procs.append(_spawn("tools", TOOLS_DIR, tools_port, tools_env))
        procs.append(_spawn("backend", BACKEND_DIR, backend_port, backend_env))

        if not _wait_health(f"http://127.0.0.1:{tools_port}/healthz"):
            print("[run_local] tools failed health check", file=sys.stderr)
            return 1
        if not _wait_health(f"{backend_url}/auth/me"):
            print("[run_local] backend failed health check", file=sys.stderr)
            return 1

        # Hand the chosen URL to whatever wraps us (the Tauri shell reads this).
        (data_dir / "backend_url").write_text(backend_url)
        print(f"[run_local] READY — backend at {backend_url}  (root: {root})")
        print("[run_local] Ctrl+C to stop.")

        while True:
            for p in procs:
                if p.poll() is not None:
                    print(f"[run_local] a service exited (code {p.returncode}); shutting down", file=sys.stderr)
                    return p.returncode or 1
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[run_local] stopping…")
        return 0
    finally:
        for p in procs:
            if p.poll() is None:
                p.send_signal(signal.SIGTERM)
        for p in procs:
            try:
                p.wait(timeout=8)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    sys.exit(main())
