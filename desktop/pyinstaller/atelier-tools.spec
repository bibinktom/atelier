# PyInstaller spec for the tools sidecar binary.
#   Build from repo root:  pyinstaller desktop/pyinstaller/atelier-tools.spec
# Produces dist/atelier-tools. Kept separate from the backend binary so each owns
# its own `app` package (no single-process collision on the shared name).
import os
from PyInstaller.utils.hooks import collect_submodules

REPO = os.path.abspath(os.getcwd())
TOOLS = os.path.join(REPO, "tools")

a = Analysis(
    [os.path.join(REPO, "desktop", "pyinstaller", "serve.py")],
    pathex=[TOOLS],                         # so `app` == tools/app
    binaries=[],
    datas=[],
    hiddenimports=(collect_submodules("app") + collect_submodules("uvicorn")
                   + ["uvicorn.lifespan.on", "uvicorn.loops.auto",
                      "uvicorn.protocols.http.auto", "uvicorn.protocols.websockets.auto",
                      "serial.tools.list_ports"]),
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [],
          name="atelier-tools", console=True, upx=False)
