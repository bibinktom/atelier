"""Uvicorn entrypoint for a bundled sidecar binary.

Each PyInstaller binary (`atelier-backend`, `atelier-tools`) bundles exactly ONE
`app` package and runs it via this script, so the two services never collide on
the shared package name `app`. run_local launches these binaries with --host/--port
in production and falls back to `python -m uvicorn` in dev.
"""
import argparse

import uvicorn

# Import the app module directly (not just as a "app.main:app" string) so
# PyInstaller's static analysis follows it and bundles the whole `app` package
# into the binary. Each binary's `app` is resolved from its spec's pathex
# (backend/ or tools/), so the two never collide.
import app.main  # noqa: F401


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--app", default="app.main:app")
    args = ap.parse_args()
    uvicorn.run(args.app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
