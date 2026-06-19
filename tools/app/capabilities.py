"""On-demand capability provisioning for the local desktop build.

The agent works on the user's own machine, so when a request needs an external
tool that isn't installed — `adb` to talk to a phone, `arduino-cli`/`esptool` to
program an ESP32/Arduino, etc. — we fetch it ourselves into a managed bin dir
(no sudo, no system package manager) and add that dir to the PATH of subsequent
shell commands. Tools already on the system PATH are used as-is.

Everything here is LOCAL-DESKTOP ONLY (ATELIER_LOCAL=1); the shared server build
never provisions binaries.
"""
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

BIN_DIR = Path(os.environ.get("ATELIER_BIN_DIR") or (Path.home() / ".atelier" / "bin"))
LIB_DIR = Path(os.environ.get("ATELIER_LIB_DIR") or (Path.home() / ".atelier" / "lib"))
PYENV_DIR = Path(os.environ.get("ATELIER_PYENV_DIR") or (Path.home() / ".atelier" / "pyenv"))

_DL_TIMEOUT = 180


def _osk() -> str:
    return {"linux": "linux", "darwin": "darwin", "windows": "windows"}.get(
        platform.system().lower(), platform.system().lower())


def _arch() -> str:
    m = platform.machine().lower()
    if m in ("x86_64", "amd64"):
        return "x64"
    if m in ("aarch64", "arm64"):
        return "arm64"
    if m in ("armv7l", "armv6l", "arm"):
        return "arm"
    if m in ("i386", "i686", "x86"):
        return "x86"
    return m


def _exe(name: str) -> str:
    return name + (".exe" if _osk() == "windows" else "")


# Pip-installed CLIs (small, pure-python) live in a managed venv; their console
# scripts are linked into BIN_DIR. Archive CLIs are downloaded per-OS/arch.
REGISTRY: dict[str, dict] = {
    "adb": {"binary": "adb", "method": "platform_tools",
            "desc": "Android Debug Bridge — control a USB-connected Android phone."},
    "arduino-cli": {"binary": "arduino-cli", "method": "arduino_cli",
                    "desc": "Compile and upload sketches to Arduino / ESP boards."},
    "esptool": {"binary": "esptool.py", "method": "pip", "package": "esptool",
                "desc": "Flash firmware to ESP32 / ESP8266 over serial."},
    "mpremote": {"binary": "mpremote", "method": "pip", "package": "mpremote",
                 "desc": "MicroPython remote control over serial."},
    "ampy": {"binary": "ampy", "method": "pip", "package": "adafruit-ampy",
             "desc": "Copy files to/from a MicroPython board."},
}

# Tools we only detect (installing them needs a system package manager / sudo).
SYSTEM: dict[str, dict] = {
    "ssh": {"binary": "ssh", "desc": "Connect to a remote server (OpenSSH).",
            "hint": "Install OpenSSH client from your OS (usually preinstalled on macOS/Linux)."},
    "scp": {"binary": "scp", "desc": "Copy files to/from a remote server."},
    "rsync": {"binary": "rsync", "desc": "Sync files with a remote server."},
    "git": {"binary": "git", "desc": "Version control."},
    "curl": {"binary": "curl", "desc": "HTTP client."},
    "ffmpeg": {"binary": "ffmpeg", "desc": "Audio/video processing.",
               "hint": "Install via your OS package manager (brew/apt/winget)."},
}


def _which(binary: str) -> str | None:
    """Find a binary in the managed bin dir first, then the system PATH."""
    local = BIN_DIR / _exe(binary)
    if local.exists():
        return str(local)
    return shutil.which(binary)


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "atelier-desktop"})
    with urllib.request.urlopen(req, timeout=_DL_TIMEOUT) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)


def _mark_exec(p: Path) -> None:
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _link_into_bin(src: Path, name: str | None = None) -> str:
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    target = BIN_DIR / _exe(name or src.name)
    if target.exists() or target.is_symlink():
        target.unlink()
    try:
        target.symlink_to(src)
    except OSError:
        shutil.copy2(src, target)
    _mark_exec(target)
    return str(target)


# ---- per-method installers ----

def _install_pip(spec: dict) -> str:
    """Install a CLI into a managed venv and link its console script into BIN_DIR."""
    if not (PYENV_DIR / "pyvenv.cfg").exists():
        subprocess.run([sys.executable, "-m", "venv", str(PYENV_DIR)], check=True,
                       capture_output=True, text=True, timeout=120)
    vbin = PYENV_DIR / ("Scripts" if _osk() == "windows" else "bin")
    pip = vbin / _exe("pip")
    subprocess.run([str(pip), "install", "--upgrade", spec["package"]], check=True,
                   capture_output=True, text=True, timeout=_DL_TIMEOUT)
    script = vbin / _exe(spec["binary"])
    if not script.exists():
        # Some packages name the script differently; fall back to the package name.
        alt = vbin / _exe(spec["package"])
        script = alt if alt.exists() else script
    if not script.exists():
        raise RuntimeError(f"{spec['package']} installed but no '{spec['binary']}' script found")
    return _link_into_bin(script, spec["binary"])


def _install_platform_tools(_spec: dict) -> str:
    """Google Android platform-tools (adb + fastboot)."""
    osk = {"linux": "linux", "darwin": "darwin", "windows": "windows"}[_osk()]
    url = f"https://dl.google.com/android/repository/platform-tools-latest-{osk}.zip"
    with tempfile.TemporaryDirectory() as td:
        zp = Path(td) / "pt.zip"
        _download(url, zp)
        LIB_DIR.mkdir(parents=True, exist_ok=True)
        dest = LIB_DIR / "platform-tools"
        if dest.exists():
            shutil.rmtree(dest)
        with zipfile.ZipFile(zp) as z:
            z.extractall(LIB_DIR)
    adb = dest / _exe("adb")
    if not adb.exists():
        raise RuntimeError("platform-tools downloaded but adb not found")
    _mark_exec(adb)
    fastboot = dest / _exe("fastboot")
    if fastboot.exists():
        _mark_exec(fastboot)
        _link_into_bin(fastboot, "fastboot")
    return _link_into_bin(adb, "adb")


def _install_arduino_cli(_spec: dict) -> str:
    osmap = {"linux": "Linux", "darwin": "macOS", "windows": "Windows"}
    archmap = {"x64": "64bit", "arm64": "ARM64", "arm": "ARMv7", "x86": "32bit"}
    osn, an = osmap[_osk()], archmap.get(_arch(), "64bit")
    ext = "zip" if _osk() == "windows" else "tar.gz"
    url = f"https://downloads.arduino.cc/arduino-cli/arduino-cli_latest_{osn}_{an}.{ext}"
    with tempfile.TemporaryDirectory() as td:
        arc = Path(td) / f"acli.{ext}"
        _download(url, arc)
        out = Path(td) / "x"
        out.mkdir()
        if ext == "zip":
            with zipfile.ZipFile(arc) as z:
                z.extractall(out)
        else:
            with tarfile.open(arc) as t:
                t.extractall(out)
        binp = out / _exe("arduino-cli")
        if not binp.exists():
            raise RuntimeError("arduino-cli archive missing the binary")
        _mark_exec(binp)
        final = LIB_DIR / _exe("arduino-cli")
        LIB_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(binp, final)
    _mark_exec(final)
    return _link_into_bin(final, "arduino-cli")


_INSTALLERS = {
    "pip": _install_pip,
    "platform_tools": _install_platform_tools,
    "arduino_cli": _install_arduino_cli,
}


def ensure(name: str) -> dict:
    """Make a capability available, installing it if needed. Returns a status dict
    with present/path/installed (or error). Idempotent."""
    name = (name or "").strip().lower()
    if name in SYSTEM:
        p = shutil.which(SYSTEM[name]["binary"])
        return {"name": name, "present": bool(p), "path": p, "installed": False,
                "managed": False, "note": None if p else SYSTEM[name].get("hint")}
    spec = REGISTRY.get(name)
    if not spec:
        known = sorted(REGISTRY) + sorted(SYSTEM)
        return {"name": name, "error": f"unknown capability '{name}'. Known: {', '.join(known)}"}
    existing = _which(spec["binary"])
    if existing:
        return {"name": name, "present": True, "path": existing, "installed": False, "managed": True}
    try:
        path = _INSTALLERS[spec["method"]](spec)
    except subprocess.CalledProcessError as e:
        return {"name": name, "present": False, "installed": False,
                "error": f"install failed: {(e.stderr or e.stdout or str(e))[:400]}"}
    except Exception as e:  # noqa: BLE001
        return {"name": name, "present": False, "installed": False,
                "error": f"install failed: {type(e).__name__}: {e}"}
    return {"name": name, "present": True, "path": path, "installed": True, "managed": True}


def catalog() -> dict:
    """List every known capability and whether it's currently available."""
    out = []
    for n, s in REGISTRY.items():
        out.append({"name": n, "desc": s["desc"], "installable": True,
                    "present": bool(_which(s["binary"]))})
    for n, s in SYSTEM.items():
        out.append({"name": n, "desc": s["desc"], "installable": False,
                    "present": bool(shutil.which(s["binary"]))})
    return {"bin_dir": str(BIN_DIR), "capabilities": out}
