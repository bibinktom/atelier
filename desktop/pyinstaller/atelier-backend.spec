# PyInstaller spec for the backend sidecar binary.
#   Build from repo root:  pyinstaller desktop/pyinstaller/atelier-backend.spec
# Produces dist/atelier-backend (a single self-contained server binary).
import os
from PyInstaller.utils.hooks import collect_submodules

REPO = os.path.abspath(os.getcwd())
BACKEND = os.path.join(REPO, "backend")

a = Analysis(
    [os.path.join(REPO, "desktop", "pyinstaller", "serve.py")],
    pathex=[BACKEND],                       # so `app` == backend/app (serve.py imports it)
    binaries=[],
    datas=[],
    # uvicorn loads loop/protocol workers dynamically; force them in.
    hiddenimports=(collect_submodules("uvicorn")
                   + ["uvicorn.lifespan.on", "uvicorn.loops.auto",
                      "uvicorn.protocols.http.auto", "uvicorn.protocols.websockets.auto"]),
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [],
          name="atelier-backend", console=True, upx=False)
