"""Per-conversation/per-user sandboxed workspace.

Workspace files live in `/workspaces/<workspace_path>/...` where `<workspace_path>`
is "<user_id>/<workspace_slug>". The tools container is hardened (cap_drop:ALL +
no-new-privileges + non-root); these helpers also validate that every accessed
path stays inside the requested workspace root.
"""
import difflib
import fnmatch
import os
import re
import resource
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from .web_fetch import _host_is_blocked

WS_ROOT = Path("/workspaces")

# ---- per-user disk quota + per-exec resource isolation ----
# The tools container is SHARED across users (isolation is per-workspace-path, not
# per-container). These guards stop one user from (a) filling the host disk or
# (b) exhausting the shared container's processes/file budget and DoS-ing others.
USER_QUOTA_BYTES = int(os.environ.get("USER_QUOTA_BYTES", str(2 * 1024 * 1024 * 1024)))  # 2 GiB
_EXEC_MAX_PROCS = int(os.environ.get("EXEC_MAX_PROCS", "400"))       # RLIMIT_NPROC (fork-bomb guard)
_EXEC_MAX_FILE_MB = int(os.environ.get("EXEC_MAX_FILE_MB", "1024"))  # RLIMIT_FSIZE (runaway-file guard)


def _user_root(workspace_path: str) -> Path:
    """The user's top-level dir (/workspaces/<user_id>) — quota is summed across
    ALL their project folders, not just the current one."""
    return WS_ROOT / workspace_path.split("/", 1)[0]


def _usage_bytes(workspace_path: str) -> int:
    root = _user_root(workspace_path)
    total = 0
    if root.is_dir():
        for dp, _dn, fns in os.walk(root):
            for fn in fns:
                try:
                    total += os.lstat(os.path.join(dp, fn)).st_size
                except OSError:
                    continue
    return total


def _quota_error(workspace_path: str, incoming: int = 0) -> dict | None:
    """Return an error dict if writing `incoming` more bytes would exceed quota."""
    used = _usage_bytes(workspace_path)
    if used + incoming > USER_QUOTA_BYTES:
        gb = USER_QUOTA_BYTES / (1024 ** 3)
        return {"error": f"disk quota exceeded ({used // (1024*1024)} MB used of {gb:.1f} GB). "
                         f"Delete files in your project folders to free space."}
    return None


def _apply_exec_limits() -> None:
    """preexec_fn for subprocesses: cap processes + single-file size so one user's
    command can't starve the shared sandbox. (Deliberately NOT capping address space
    via RLIMIT_AS — that breaks node/V8, which reserves huge virtual memory.)"""
    try:
        resource.setrlimit(resource.RLIMIT_NPROC, (_EXEC_MAX_PROCS, _EXEC_MAX_PROCS))
    except (ValueError, OSError):
        pass
    try:
        fsize = _EXEC_MAX_FILE_MB * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_FSIZE, (fsize, fsize))
    except (ValueError, OSError):
        pass

_DIFF_MAX_CHARS = 20_000


def _unified_diff(old: str, new: str, path: str) -> str | None:
    """Compute a (size-capped) unified diff for the UI. None if unchanged."""
    if old == new:
        return None
    diff = "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"a/{path}", tofile=f"b/{path}",
    ))
    if not diff:
        return None
    if len(diff) > _DIFF_MAX_CHARS:
        diff = diff[:_DIFF_MAX_CHARS] + "\n… [diff truncated]"
    return diff

_PATH_RE = re.compile(r"^[a-f0-9]{32}/[a-z0-9][a-z0-9_-]{0,62}$")


def _validate_workspace(workspace_path: str) -> Path:
    if not workspace_path or not _PATH_RE.fullmatch(workspace_path):
        raise ValueError("invalid workspace path")
    p = WS_ROOT / workspace_path
    p.mkdir(parents=True, exist_ok=True)
    return p


def _safe_path(workspace_path: str, rel: str) -> Path:
    root = _validate_workspace(workspace_path).resolve()
    target = (root / (rel or "")).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise PermissionError("path escapes workspace")
    return target


# ---- read/list/write/edit ----

def list_dir(workspace_path: str, path: str = ".") -> dict:
    p = _safe_path(workspace_path, path)
    if not p.exists():
        return {"error": f"not found: {path}"}
    if p.is_file():
        return {"error": f"{path} is a file"}
    entries = []
    for child in sorted(p.iterdir()):
        try:
            st = child.stat()
            entries.append({
                "name": child.name,
                "type": "dir" if child.is_dir() else "file",
                "size": st.st_size if child.is_file() else None,
            })
        except OSError:
            continue
    root = _validate_workspace(workspace_path)
    return {"path": str(p.relative_to(root)) or ".", "entries": entries}


def read_file(workspace_path: str, path: str, max_chars: int = 50_000) -> dict:
    p = _safe_path(workspace_path, path)
    if not p.is_file():
        return {"error": f"not a file: {path}"}
    if p.stat().st_size > 5_000_000:
        return {"error": "file too large (>5MB)"}
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {"error": str(e)}
    truncated = False
    if len(text) > max_chars:
        text = text[:max_chars]
        truncated = True
    return {"path": path, "text": text, "truncated": truncated}


def write_file(workspace_path: str, path: str, content: str) -> dict:
    p = _safe_path(workspace_path, path)
    existing = p.stat().st_size if p.is_file() else 0
    # Net new bytes this write adds toward the user's quota.
    err = _quota_error(workspace_path, max(0, len(content.encode("utf-8")) - existing))
    if err:
        return err
    before = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    root = _validate_workspace(workspace_path)
    return {
        "path": str(p.relative_to(root)),
        "bytes": p.stat().st_size,
        "created": before == "",
        "diff": _unified_diff(before, content, path),
    }


def edit_file(workspace_path: str, path: str, old: str, new: str) -> dict:
    p = _safe_path(workspace_path, path)
    if not p.is_file():
        return {"error": f"not a file: {path}"}
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count == 0:
        return {"error": "old string not found"}
    if count > 1:
        return {"error": f"old string occurs {count} times — add surrounding context to make it "
                          f"unique, or use workspace_apply_patch for a multi-spot change"}
    updated = text.replace(old, new, 1)
    p.write_text(updated, encoding="utf-8")
    return {"path": path, "ok": True, "diff": _unified_diff(text, updated, path)}


# ---- grep/glob ----

def grep(workspace_path: str, pattern: str, path: str = ".", max_matches: int = 200) -> dict:
    root_target = _safe_path(workspace_path, path)
    base = _validate_workspace(workspace_path)
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return {"error": f"bad regex: {e}"}
    out = []
    targets = [root_target] if root_target.is_file() else list(root_target.rglob("*"))
    for f in targets:
        if not f.is_file():
            continue
        try:
            for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
                if rx.search(line):
                    out.append({"path": str(f.relative_to(base)), "line": i, "text": line[:300]})
                    if len(out) >= max_matches:
                        return {"matches": out, "truncated": True}
        except OSError:
            continue
    return {"matches": out, "truncated": False}


def glob_files(workspace_path: str, pattern: str, max_results: int = 200) -> dict:
    base = _validate_workspace(workspace_path)
    out = []
    for f in base.rglob("*"):
        if f.is_file() and fnmatch.fnmatch(str(f.relative_to(base)), pattern):
            out.append(str(f.relative_to(base)))
            if len(out) >= max_results:
                break
    return {"matches": out}


# ---- bash ----

BASH_TIMEOUT_DEFAULT = 30
BASH_TIMEOUT_MAX = 300  # builds / installs / test suites need longer than a quick command
BASH_MAX_OUTPUT = 64_000

# Minimal env so secrets don't leak into model's view.
SAFE_ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "HOME": "/tmp",
    "LANG": "C.UTF-8",
}


def bash(workspace_path: str, command: str, timeout: int = BASH_TIMEOUT_DEFAULT) -> dict:
    if not command or not isinstance(command, str):
        return {"error": "command required"}
    cwd = _validate_workspace(workspace_path)
    timeout = max(1, min(BASH_TIMEOUT_MAX, int(timeout or BASH_TIMEOUT_DEFAULT)))
    try:
        proc = subprocess.run(
            ["bash", "-c", command],
            cwd=str(cwd),
            env=SAFE_ENV,
            capture_output=True,
            text=True,
            timeout=timeout,
            preexec_fn=_apply_exec_limits,
        )
    except subprocess.TimeoutExpired:
        return {"error": f"timed out after {timeout}s"}
    except OSError as e:
        return {"error": f"exec error: {e}"}
    return {
        "exit_code": proc.returncode,
        "stdout": proc.stdout[:BASH_MAX_OUTPUT],
        "stderr": proc.stderr[:BASH_MAX_OUTPUT],
        "truncated": (len(proc.stdout) > BASH_MAX_OUTPUT or len(proc.stderr) > BASH_MAX_OUTPUT),
    }


# ---- git clone / patch / codebase search (coding agent) ----

_REPO_NAME_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def git_clone(workspace_path: str, url: str, subdir: str = "") -> dict:
    """Shallow-clone a public https git repo into the workspace. https only; the
    target host is run through the same SSRF guard as web_fetch so generated code
    can't be used to pull from the home LAN / cloud metadata."""
    base = _validate_workspace(workspace_path)
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return {"error": "only https git urls are allowed"}
    blocked, reason = _host_is_blocked(parsed.hostname or "")
    if blocked:
        return {"error": f"blocked host: {reason}"}
    # Derive a safe target directory from subdir or the repo name.
    if not subdir:
        name = (parsed.path.rstrip("/").split("/")[-1] or "repo")
        subdir = _REPO_NAME_RE.sub("-", name)[:-4] if name.endswith(".git") else _REPO_NAME_RE.sub("-", name)
    subdir = subdir.strip("/") or "repo"
    target = _safe_path(workspace_path, subdir)
    if target.exists() and any(target.iterdir()):
        return {"error": f"target '{subdir}' already exists and is not empty"}
    # Refuse to start a clone when the user is already near quota (the clone could
    # otherwise blow far past it before we can react).
    err = _quota_error(workspace_path, 0)
    if err:
        return err
    try:
        proc = subprocess.run(
            ["git", "clone", "--depth", "1", "--", url, str(target)],
            cwd=str(base), env=SAFE_ENV, capture_output=True, text=True, timeout=180,
            preexec_fn=_apply_exec_limits,
        )
    except subprocess.TimeoutExpired:
        return {"error": "clone timed out after 180s"}
    except OSError as e:
        return {"error": f"exec error: {e}"}
    if proc.returncode != 0:
        return {"error": f"git clone failed: {proc.stderr[:2000].strip()}"}
    # Count what landed so the UI can show "cloned N files".
    n = sum(1 for _ in target.rglob("*") if _.is_file())
    return {"ok": True, "path": subdir, "files": n, "stderr": proc.stderr[-500:].strip()}


def apply_patch(workspace_path: str, patch: str) -> dict:
    """Apply a unified diff to the workspace via `git apply` (falls back to `patch -p1`).
    Lets the coder land a multi-file change atomically instead of many fragile edits."""
    base = _validate_workspace(workspace_path)
    if not patch or not patch.strip():
        return {"error": "empty patch"}
    with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False, dir="/tmp") as tf:
        tf.write(patch if patch.endswith("\n") else patch + "\n")
        patch_file = tf.name
    try:
        # Prefer git apply (no repo required with --unsafe-paths off; we stay in cwd).
        proc = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", patch_file],
            cwd=str(base), env=SAFE_ENV, capture_output=True, text=True, timeout=60,
            preexec_fn=_apply_exec_limits,
        )
        if proc.returncode != 0:
            proc = subprocess.run(
                ["patch", "-p1", "-i", patch_file],
                cwd=str(base), env=SAFE_ENV, capture_output=True, text=True, timeout=60,
                preexec_fn=_apply_exec_limits,
            )
        if proc.returncode != 0:
            return {"error": f"patch did not apply cleanly: {proc.stderr[:1500].strip() or proc.stdout[:1500].strip()}"}
    except subprocess.TimeoutExpired:
        return {"error": "patch timed out"}
    except OSError as e:
        return {"error": f"exec error: {e}"}
    finally:
        try: os.remove(patch_file)
        except OSError: pass
    # Report which files the diff touched.
    changed = sorted({m.group(1) for m in re.finditer(r'^\+\+\+ b/(.+)$', patch, re.MULTILINE)})
    return {"ok": True, "files_changed": changed}


_SEARCH_SKIP = {".git", "node_modules", ".trash", ".next", "dist", "build", "out",
                "target", "__pycache__", ".venv", "venv", ".cache"}
_DEF_RE = re.compile(r"\b(def|class|function|func|fn|interface|type|struct|impl)\b")


def codebase_search(workspace_path: str, query: str, max_results: int = 12) -> dict:
    """Agentic code retrieval: ripgrep the query tokens across the workspace and
    return ranked file:line snippets. No embeddings — fast, deterministic, offline."""
    base = _validate_workspace(workspace_path)
    query = (query or "").strip()
    if not query:
        return {"error": "query required"}
    max_results = max(1, min(50, int(max_results or 12)))
    tokens = [t for t in re.split(r"[^A-Za-z0-9_]+", query) if len(t) >= 2][:8]
    if not tokens:
        tokens = [query]

    rg = shutil.which("rg")
    # path -> {hits, tokens_hit:set, lines:[(line,text)]}
    scored: dict[str, dict] = {}
    if rg:
        cmd = [rg, "-n", "--no-heading", "-S", "-m", "5", "--max-columns", "300"]
        for d in _SEARCH_SKIP:
            cmd += ["-g", f"!{d}/"]
        for t in tokens:
            cmd += ["-e", t]
        cmd += ["."]  # explicit search path — without it, rg reads stdin under subprocess (no TTY)
        try:
            proc = subprocess.run(cmd, cwd=str(base), env=SAFE_ENV, stdin=subprocess.DEVNULL,
                                  capture_output=True, text=True, timeout=20)
            raw = proc.stdout
        except (subprocess.TimeoutExpired, OSError):
            raw = ""
        for ln in raw.splitlines():
            # format: relpath:lineno:text
            parts = ln.split(":", 2)
            if len(parts) < 3:
                continue
            path, lineno, text = parts[0], parts[1], parts[2]
            if path.startswith("./"):
                path = path[2:]
            rec = scored.setdefault(path, {"hits": 0, "tokens": set(), "lines": []})
            rec["hits"] += 1
            low = text.lower()
            for t in tokens:
                if t.lower() in low or t.lower() in path.lower():
                    rec["tokens"].add(t.lower())
            if len(rec["lines"]) < 3:
                rec["lines"].append({"line": int(lineno) if lineno.isdigit() else 0, "snippet": text.strip()[:300]})
    else:
        # Fallback to the in-process regex grep if ripgrep isn't installed.
        g = grep(workspace_path, "|".join(re.escape(t) for t in tokens), ".", max_matches=200)
        for m in g.get("matches", []):
            rec = scored.setdefault(m["path"], {"hits": 0, "tokens": set(), "lines": []})
            rec["hits"] += 1
            if len(rec["lines"]) < 3:
                rec["lines"].append({"line": m["line"], "snippet": m["text"][:300]})

    def score(path: str, rec: dict) -> tuple:
        fname = path.rsplit("/", 1)[-1].lower()
        name_boost = sum(2 for t in tokens if t.lower() in fname)
        def_boost = sum(1 for l in rec["lines"] if _DEF_RE.search(l["snippet"]))
        return (len(rec["tokens"]) + name_boost, rec["hits"] + def_boost)

    ranked = sorted(scored.items(), key=lambda kv: score(*kv), reverse=True)[:max_results]
    results = [{"path": p, "lines": rec["lines"]} for p, rec in ranked]
    return {"query": query, "results": results, "engine": "ripgrep" if rg else "grep"}
