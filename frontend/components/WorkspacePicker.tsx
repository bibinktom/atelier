"use client";
import { useEffect, useRef, useState } from "react";
import { Workspace } from "@/lib/types";

export function WorkspacePicker({
  workspaces, value, onChange, onCreate, onImportFolder,
  onLinkLocalFolder, onSyncNow, linkedIds, syncing, fsaAvailable, usage,
  local, localRoot,
}: {
  workspaces: Workspace[];
  value: string | null;
  onChange: (id: string) => void;
  onCreate: (name: string, path?: string) => Promise<Workspace | null>;
  // Local desktop build: workspaces map to real host folders under localRoot.
  local?: boolean;
  localRoot?: string | null;
  onImportFolder?: (files: FileList) => Promise<Workspace | null>;
  // Live two-way sync with a folder on the user's computer (File System Access API).
  onLinkLocalFolder?: () => void;
  onSyncNow?: (id: string) => void;
  linkedIds?: Set<string>;
  syncing?: boolean;
  fsaAvailable?: boolean;
  usage?: { used: number; quota: number; percent: number } | null;
}) {
  const fmtMB = (b: number) => b >= 1024 ** 3 ? `${(b / 1024 ** 3).toFixed(1)} GB` : `${Math.round(b / 1024 / 1024)} MB`;
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [importing, setImporting] = useState(false);
  const [newName, setNewName] = useState("");
  const [newPath, setNewPath] = useState("");
  const ref = useRef<HTMLDivElement>(null);
  const dirInputRef = useRef<HTMLInputElement>(null);
  const current = workspaces.find(w => w.id === value);
  const isLinked = (id: string) => !!linkedIds?.has(id);
  const currentLinked = !!value && isLinked(value);

  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (!ref.current?.contains(e.target as Node)) { setOpen(false); setCreating(false); }
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const submitNew = async () => {
    const n = newName.trim();
    if (!n) return;
    const p = local ? (newPath.trim() || undefined) : undefined;
    const ws = await onCreate(n, p);
    if (ws) {
      onChange(ws.id);
      setNewName(""); setNewPath(""); setCreating(false); setOpen(false);
    }
  };

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(o => !o)}
        className="group flex items-center gap-2 rounded-md border px-3 py-1.5 text-sm transition hover:bg-[var(--color-paper-3)]"
        style={{ borderColor: "var(--color-rule)" }}
        title="Project folder this chat works in"
      >
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"/>
        </svg>
        <span className="text-[11px] uppercase tracking-[0.16em]" style={{ color: "var(--color-muted)" }}>Project ·</span>
        <span className="font-medium">{current?.name ?? "—"}</span>
        {currentLinked && (
          <span
            title={syncing ? "Syncing with your folder…" : "Live-synced to a folder on your computer"}
            className="ml-0.5 inline-block h-1.5 w-1.5 rounded-full"
            style={{ background: "var(--color-moss)", opacity: syncing ? 0.5 : 1 }}
            aria-hidden
          />
        )}
        <span aria-hidden className="ml-0.5 text-xs opacity-50">▾</span>
      </button>
      {open && (
        <div
          className="absolute right-0 z-30 mt-2 w-64 overflow-hidden rounded-md border shadow-lg"
          style={{ borderColor: "var(--color-rule)", background: "var(--color-paper)" }}
        >
          <div className="px-3 py-2 text-[11px] uppercase tracking-[0.18em]" style={{ color: "var(--color-muted)" }}>
            Project folders
          </div>
          <ul className="max-h-64 overflow-auto thin-scroll">
            {workspaces.map(w => (
              <li key={w.id}>
                <button
                  onClick={() => { onChange(w.id); setOpen(false); }}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-[14px] transition hover:bg-[var(--color-paper-3)]"
                  style={{ background: w.id === value ? "var(--color-paper-3)" : undefined }}
                >
                  <span className="font-medium">{w.name}</span>
                  {isLinked(w.id) && (
                    <span title="Live-synced to a folder on your computer"
                          className="inline-block h-1.5 w-1.5 rounded-full"
                          style={{ background: "var(--color-moss)" }} aria-hidden />
                  )}
                  <span className="ml-auto font-mono text-[10.5px] opacity-60">{w.slug}/</span>
                </button>
              </li>
            ))}
          </ul>
          <div className="border-t" style={{ borderColor: "var(--color-rule-soft)" }}>
            {creating ? (
              <div className="flex flex-col gap-1.5 p-2">
                <div className="flex gap-1.5">
                  <input
                    autoFocus
                    value={newName}
                    onChange={e => setNewName(e.target.value)}
                    onKeyDown={e => { if (e.key === "Enter") submitNew(); else if (e.key === "Escape") setCreating(false); }}
                    placeholder="Folder name"
                    className="flex-1 border bg-[var(--color-paper)] px-2 py-1.5 text-[13px] outline-none"
                    style={{ borderColor: "var(--color-rule)" }}
                  />
                  <button onClick={submitNew}
                    className="border px-2 py-1.5 text-[13px]"
                    style={{ borderColor: "var(--color-ink)", background: "var(--color-ink)", color: "var(--color-paper)" }}
                  >Add</button>
                </div>
                {local && (
                  <input
                    value={newPath}
                    onChange={e => setNewPath(e.target.value)}
                    onKeyDown={e => { if (e.key === "Enter") submitNew(); else if (e.key === "Escape") setCreating(false); }}
                    placeholder={localRoot ? `${localRoot}/my-project` : "/absolute/path/to/folder"}
                    spellCheck={false}
                    className="w-full border bg-[var(--color-paper)] px-2 py-1.5 font-mono text-[11.5px] outline-none"
                    style={{ borderColor: "var(--color-rule)" }}
                  />
                )}
                {local && (
                  <p className="px-0.5 text-[10.5px]" style={{ color: "var(--color-muted)" }}>
                    Absolute folder on this computer{localRoot ? ` (under ${localRoot})` : ""}. Leave blank for a folder inside the data dir.
                  </p>
                )}
              </div>
            ) : (
              <button
                onClick={() => setCreating(true)}
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-[13px] transition hover:bg-[var(--color-paper-3)]"
                style={{ color: "var(--color-ink-2)" }}
              >
                <span aria-hidden>＋</span> {local ? "Open a folder by path…" : "New empty project folder…"}
              </button>
            )}
            {onLinkLocalFolder && (
              <button
                onClick={() => { if (fsaAvailable) { onLinkLocalFolder(); setOpen(false); } }}
                disabled={!fsaAvailable || syncing}
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-[13px] transition hover:bg-[var(--color-paper-3)] disabled:opacity-50"
                style={{ color: "var(--color-ink-2)" }}
                title={fsaAvailable
                  ? "Pick a folder on your computer; the agent's files stay in sync with it"
                  : "Live folder sync needs Chrome or Edge over https"}
              >
                <span aria-hidden>⇄</span>
                {syncing ? "Syncing…" : "Open a folder on my computer (live sync)…"}
              </button>
            )}
            {currentLinked && onSyncNow && value && (
              <button
                onClick={() => onSyncNow(value)}
                disabled={syncing}
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-[13px] transition hover:bg-[var(--color-paper-3)] disabled:opacity-50"
                style={{ color: "var(--color-moss)" }}
              >
                <span aria-hidden>↻</span> {syncing ? "Syncing…" : "Sync now"}
              </button>
            )}
            {onLinkLocalFolder && !fsaAvailable && (
              <p className="px-3 pb-1 pt-0.5 text-[10.5px]" style={{ color: "var(--color-muted)" }}>
                Live folder sync works in Chrome / Edge (https). Other browsers can use “Import folder” below.
              </p>
            )}
            {onImportFolder && (
              <>
                <button
                  onClick={() => dirInputRef.current?.click()}
                  disabled={importing}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-[13px] transition hover:bg-[var(--color-paper-3)] disabled:opacity-50"
                  style={{ color: "var(--color-ink-2)" }}
                  title="Pick a folder from your computer; copies it into a new project"
                >
                  <span aria-hidden>↥</span>
                  {importing ? "Importing folder…" : "Import folder from my computer…"}
                </button>
                <input
                  ref={dirInputRef}
                  type="file" hidden multiple
                  // @ts-ignore non-standard but works in Chrome/Edge
                  webkitdirectory=""
                  directory=""
                  onChange={async e => {
                    const files = e.target.files;
                    if (!files || files.length === 0) return;
                    setImporting(true);
                    try {
                      const ws = await onImportFolder(files);
                      if (ws) { onChange(ws.id); setOpen(false); }
                    } finally {
                      setImporting(false);
                      if (dirInputRef.current) dirInputRef.current.value = "";
                    }
                  }}
                />
                <p className="px-3 pb-2 text-[10.5px]" style={{ color: "var(--color-muted)" }}>
                  Folder import works in Chrome / Edge.
                </p>
              </>
            )}
            {usage && (
              <div className="border-t px-3 py-2 text-[10.5px]" style={{ borderColor: "var(--color-rule-soft)", color: "var(--color-muted)" }}>
                Storage · {fmtMB(usage.used)} / {fmtMB(usage.quota)}
                <div className="mt-1 h-1 w-full overflow-hidden rounded-full" style={{ background: "var(--color-rule-soft)" }}>
                  <div className="h-full rounded-full" style={{
                    width: `${Math.min(100, usage.percent)}%`,
                    background: usage.percent >= 90 ? "var(--color-brick)" : "var(--color-moss)",
                  }} />
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
