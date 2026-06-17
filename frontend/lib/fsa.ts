/**
 * Live two-way sync between a folder the user picks on their own computer and the
 * server-side project workspace the AI agent operates in.
 *
 * Uses the File System Access API (`showDirectoryPicker`) — Chromium/Edge only, and
 * only on a secure context (https / localhost). The picked directory handle is
 * persisted in IndexedDB per workspace id, so the link survives reloads (the browser
 * still re-prompts for permission once per session — a security requirement).
 *
 * Direction model (matches the product decision):
 *   • Link / push  → your local folder is the source of truth, uploaded to the server.
 *   • After a turn → the agent's server-side edits are pulled down into your folder.
 *   • Sync now     → reconcile both ways by size + mtime.
 *
 * The agent always executes server-side in the sandbox; this just mirrors the bytes.
 */
import { api } from "./api";

// Minimal ambient types — the TS DOM lib's coverage of the File System Access API
// is uneven across versions, so we keep our own loose shapes and cast at the edges.
type DirHandle = any; // FileSystemDirectoryHandle
type FileHandle = any; // FileSystemFileHandle

const SKIP_DIRS = new Set([
  ".git", ".hg", ".svn", "node_modules", ".trash", ".next", ".nuxt",
  "dist", "build", "out", "target", "__pycache__", ".venv", "venv",
  ".cache", ".mypy_cache", ".pytest_cache", ".gradle", ".idea", ".turbo",
]);
const MAX_FILE_BYTES = 50 * 1024 * 1024; // mirror backend MAX_UPLOAD_BYTES
const MTIME_SLACK = 3; // seconds — absorbs clock/skew so we don't ping-pong files

export function fsaSupported(): boolean {
  return typeof window !== "undefined"
    && "showDirectoryPicker" in window
    && window.isSecureContext;
}

/** Open the OS folder picker. Returns the handle, or null if the user cancelled. */
export async function pickProjectDirectory(): Promise<DirHandle | null> {
  try {
    // @ts-ignore - showDirectoryPicker not in all TS dom libs
    return await window.showDirectoryPicker({ id: "atelier-project", mode: "readwrite" });
  } catch (e: any) {
    if (e?.name === "AbortError") return null; // user closed the dialog
    throw e;
  }
}

// ---- IndexedDB handle persistence ----

const IDB_NAME = "atelier-fsa";
const IDB_STORE = "handles";

function openIDB(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(IDB_NAME, 1);
    req.onupgradeneeded = () => {
      if (!req.result.objectStoreNames.contains(IDB_STORE)) req.result.createObjectStore(IDB_STORE);
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function idbPut(key: string, val: any): Promise<void> {
  const db = await openIDB();
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(IDB_STORE, "readwrite");
    tx.objectStore(IDB_STORE).put(val, key);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
  db.close();
}

async function idbGet<T = any>(key: string): Promise<T | undefined> {
  const db = await openIDB();
  const out = await new Promise<T | undefined>((resolve, reject) => {
    const tx = db.transaction(IDB_STORE, "readonly");
    const r = tx.objectStore(IDB_STORE).get(key);
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
  db.close();
  return out;
}

async function idbDel(key: string): Promise<void> {
  const db = await openIDB();
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(IDB_STORE, "readwrite");
    tx.objectStore(IDB_STORE).delete(key);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
  db.close();
}

async function idbKeys(): Promise<string[]> {
  const db = await openIDB();
  const out = await new Promise<string[]>((resolve, reject) => {
    const tx = db.transaction(IDB_STORE, "readonly");
    const r = tx.objectStore(IDB_STORE).getAllKeys();
    r.onsuccess = () => resolve((r.result as any[]).map(String));
    r.onerror = () => reject(r.error);
  });
  db.close();
  return out;
}

export const rememberHandle = (wid: string, handle: DirHandle) => idbPut(`ws:${wid}`, handle);
export const getRememberedHandle = (wid: string) => idbGet<DirHandle>(`ws:${wid}`);
export const forgetHandle = (wid: string) => idbDel(`ws:${wid}`);
export async function listLinkedWorkspaceIds(): Promise<Set<string>> {
  if (typeof indexedDB === "undefined") return new Set();
  try {
    const keys = await idbKeys();
    return new Set(keys.filter(k => k.startsWith("ws:")).map(k => k.slice(3)));
  } catch { return new Set(); }
}

/** Ensure we still hold read/write permission on the handle (re-prompts if needed). */
export async function ensureRWPermission(handle: DirHandle): Promise<boolean> {
  try {
    const opts = { mode: "readwrite" };
    if ((await handle.queryPermission(opts)) === "granted") return true;
    return (await handle.requestPermission(opts)) === "granted";
  } catch { return false; }
}

// ---- local folder walking / IO ----

type LocalEntry = { path: string; handle: FileHandle };

async function* walkLocal(dir: DirHandle, prefix = ""): AsyncGenerator<LocalEntry> {
  for await (const [name, handle] of (dir as any).entries()) {
    const path = prefix ? `${prefix}/${name}` : name;
    if (handle.kind === "directory") {
      if (SKIP_DIRS.has(name)) continue;
      yield* walkLocal(handle, path);
    } else {
      yield { path, handle };
    }
  }
}

type LocalInfo = { handle: FileHandle; size: number; mtime: number }; // mtime in seconds

async function localManifest(dir: DirHandle): Promise<Map<string, LocalInfo>> {
  const map = new Map<string, LocalInfo>();
  for await (const { path, handle } of walkLocal(dir)) {
    try {
      const f: File = await handle.getFile();
      map.set(path, { handle, size: f.size, mtime: Math.floor(f.lastModified / 1000) });
    } catch { /* unreadable — skip */ }
  }
  return map;
}

async function writeLocalFile(root: DirHandle, path: string, blob: Blob): Promise<void> {
  const parts = path.split("/");
  const fname = parts.pop()!;
  let dir = root;
  for (const seg of parts) dir = await dir.getDirectoryHandle(seg, { create: true });
  const fh = await dir.getFileHandle(fname, { create: true });
  const w = await fh.createWritable();
  await w.write(blob);
  await w.close();
}

export type SyncResult = { uploaded: number; downloaded: number; total: number; skippedLarge: number };

/** Upload local files to the server workspace (your folder wins). Skips unchanged. */
export async function pushLocalToServer(
  wid: string, handle: DirHandle, onProgress?: (done: number, total: number) => void,
): Promise<SyncResult> {
  const local = await localManifest(handle);
  let server = new Map<string, { size: number; modified_at: number }>();
  try {
    const m = await api.workspaceManifest(wid);
    server = new Map(m.files.map(f => [f.path, { size: f.size, modified_at: f.modified_at }]));
  } catch { /* empty server → upload everything */ }

  let uploaded = 0, skippedLarge = 0, done = 0;
  const total = local.size;
  for (const [path, info] of local) {
    done++;
    onProgress?.(done, total);
    if (info.size > MAX_FILE_BYTES) { skippedLarge++; continue; }
    const s = server.get(path);
    const changed = !s || s.size !== info.size || info.mtime > s.modified_at + MTIME_SLACK;
    if (!changed) continue;
    try {
      const file: File = await info.handle.getFile();
      await api.uploadToWorkspace(wid, file, path);
      uploaded++;
    } catch { /* one file failing shouldn't abort the whole sync */ }
  }
  return { uploaded, downloaded: 0, total, skippedLarge };
}

/** Pull server files (e.g. the agent's edits / a freshly cloned repo) into the folder. */
export async function pullServerToLocal(wid: string, handle: DirHandle): Promise<SyncResult> {
  const m = await api.workspaceManifest(wid);
  const local = await localManifest(handle);
  let downloaded = 0, skippedLarge = 0;
  for (const f of m.files) {
    if (f.size > MAX_FILE_BYTES) { skippedLarge++; continue; }
    const l = local.get(f.path);
    const changed = !l || l.size !== f.size || f.modified_at > l.mtime + MTIME_SLACK;
    if (!changed) continue;
    try {
      const blob = await api.downloadWorkspaceBlob(wid, f.path);
      await writeLocalFile(handle, f.path, blob);
      downloaded++;
    } catch { /* skip the file that failed; keep going */ }
  }
  return { uploaded: 0, downloaded, total: m.files.length, skippedLarge };
}

/** Full reconcile: push local-newer up, then pull server-newer down. */
export async function syncNow(wid: string, handle: DirHandle): Promise<SyncResult> {
  const up = await pushLocalToServer(wid, handle);
  const down = await pullServerToLocal(wid, handle);
  return {
    uploaded: up.uploaded,
    downloaded: down.downloaded,
    total: Math.max(up.total, down.total),
    skippedLarge: up.skippedLarge + down.skippedLarge,
  };
}
