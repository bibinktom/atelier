"use client";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { WorkspaceFileEntry } from "@/lib/types";
import { FilePreviewModal, PreviewSource } from "./FilePreviewModal";

function guessMime(filename: string): string {
  const ext = filename.includes(".") ? filename.split(".").pop()!.toLowerCase() : "";
  const map: Record<string, string> = {
    pdf: "application/pdf",
    png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg",
    gif: "image/gif", webp: "image/webp", svg: "image/svg+xml",
    xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    xls: "application/vnd.ms-excel",
    ppt: "application/vnd.ms-powerpoint",
    doc: "application/msword",
    csv: "text/csv", json: "application/json",
    md: "text/markdown", txt: "text/plain", log: "text/plain",
    py: "text/x-python", js: "application/javascript", ts: "text/typescript",
    html: "text/html", css: "text/css", yaml: "application/x-yaml", yml: "application/x-yaml",
  };
  return map[ext] ?? "";
}

function fmtSize(n: number | null) {
  if (n == null) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1_048_576) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1_048_576).toFixed(1)} MB`;
}

export function FilePanel({ workspaceId, projectName, onClose }: {
  workspaceId: string; projectName: string; onClose: () => void;
}) {
  const [path, setPath] = useState("");
  const [entries, setEntries] = useState<WorkspaceFileEntry[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<PreviewSource | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const dirInputRef = useRef<HTMLInputElement>(null);

  const openPreview = (name: string, size: number | null) => {
    const rel = path ? `${path}/${name}` : name;
    setPreview({
      filename: name,
      mime: guessMime(name),
      size,
      previewUrl: api.workspaceFilePreviewUrl(workspaceId, rel),
      downloadUrl: api.workspaceFileUrl(workspaceId, rel),
    });
  };

  const refresh = async (p = path) => {
    setBusy(true); setError(null);
    try {
      const res = await api.listWorkspaceFiles(workspaceId, p);
      setEntries(res.entries || []);
      setPath(res.path || "");
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => { refresh(""); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [workspaceId]);

  const goInto = (name: string) => refresh(path ? `${path}/${name}` : name);
  const goUp = () => {
    const parts = path.split("/").filter(Boolean);
    parts.pop();
    refresh(parts.join("/"));
  };

  const upload = async (files: FileList | null, useRelative: boolean) => {
    if (!files || files.length === 0) return;
    setBusy(true); setError(null);
    try {
      for (const f of Array.from(files)) {
        // If a folder was picked (webkitdirectory), keep its relative structure.
        const rel = useRelative
          ? (f as any).webkitRelativePath || f.name
          : f.name;
        const target = path ? `${path}/${rel}` : rel;
        await api.uploadToWorkspace(workspaceId, f, target);
      }
      await refresh();
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      setBusy(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
      if (dirInputRef.current) dirInputRef.current.value = "";
    }
  };

  const onDrop = async (e: React.DragEvent) => {
    e.preventDefault(); e.stopPropagation();
    if (e.dataTransfer.files?.length) {
      await upload(e.dataTransfer.files, true);
    }
  };

  const onDeleteEntry = async (name: string) => {
    if (!confirm(`Delete "${name}"? This can't be undone.`)) return;
    setBusy(true);
    try {
      await api.deleteWorkspaceFile(workspaceId, path ? `${path}/${name}` : name);
      await refresh();
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally { setBusy(false); }
  };

  return (
    <aside
      className="fixed inset-0 z-30 flex h-dvh w-full flex-col border-l safe-pt safe-pb md:static md:inset-auto md:z-auto md:w-[340px]"
      style={{ borderColor: "var(--color-rule)", background: "var(--color-paper-2)" }}
      onDragOver={e => { e.preventDefault(); e.stopPropagation(); }}
      onDrop={onDrop}
    >
      <div className="flex items-center justify-between border-b px-4 py-3" style={{ borderColor: "var(--color-rule)" }}>
        <div>
          <div className="text-[11px] uppercase tracking-[0.2em]" style={{ color: "var(--color-muted)" }}>Project files</div>
          <div className="font-medium text-[14px]">{projectName}</div>
        </div>
        <button onClick={onClose} aria-label="Close" className="rounded p-1 text-lg leading-none hover:bg-[var(--color-paper-3)]">×</button>
      </div>

      <div className="flex items-center gap-1 border-b px-4 py-2 text-[12px]" style={{ borderColor: "var(--color-rule-soft)" }}>
        <button onClick={goUp} disabled={!path}
                className="border px-1.5 py-0.5 disabled:opacity-30"
                style={{ borderColor: "var(--color-rule)" }}
                aria-label="Up">↑</button>
        <span className="ml-1 truncate font-mono" style={{ color: "var(--color-muted)" }}>
          /{path || ""}
        </span>
      </div>

      <div className="flex-1 overflow-auto thin-scroll">
        {busy && <p className="px-4 py-3 text-[12px]" style={{ color: "var(--color-muted)" }}>Loading…</p>}
        {error && <p className="px-4 py-3 text-[12px]" style={{ color: "var(--color-ink)" }}>{error}</p>}
        {!busy && entries.length === 0 && !error && (
          <p className="px-4 py-6 text-[12.5px] italic" style={{ color: "var(--color-muted)" }}>
            Empty. Drag files here, or use the buttons below.
          </p>
        )}
        <ul>
          {entries.map(e => (
            <li key={e.name} className="group flex items-center gap-2 border-b px-4 py-1.5 hover:bg-[var(--color-paper-3)]"
                style={{ borderColor: "var(--color-rule-soft)" }}>
              {e.type === "dir" ? (
                <button onClick={() => goInto(e.name)} className="flex flex-1 items-center gap-2 text-left">
                  <span aria-hidden>▸</span>
                  <span className="flex-1 truncate text-[13.5px]">{e.name}/</span>
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => openPreview(e.name, e.size)}
                  className="flex flex-1 items-center gap-2 text-left text-[13.5px]"
                  title="Click to preview"
                >
                  <span aria-hidden style={{ color: "var(--color-muted)" }}>·</span>
                  <span className="flex-1 truncate">{e.name}</span>
                  <span className="text-[11px]" style={{ color: "var(--color-muted)" }}>{fmtSize(e.size)}</span>
                </button>
              )}
              <button onClick={() => onDeleteEntry(e.name)}
                      className="opacity-0 transition group-hover:opacity-50 hover:opacity-100"
                      style={{ color: "var(--color-muted)" }}
                      aria-label="Delete">×</button>
            </li>
          ))}
        </ul>
      </div>

      <div className="border-t px-3 py-2.5" style={{ borderColor: "var(--color-rule)" }}>
        <div className="flex gap-2">
          <button
            onClick={() => fileInputRef.current?.click()}
            className="flex-1 border px-2 py-1.5 text-[12.5px]"
            style={{ borderColor: "var(--color-ink)", background: "var(--color-ink)", color: "var(--color-paper)" }}
          >Upload files</button>
          <button
            onClick={() => dirInputRef.current?.click()}
            className="flex-1 border px-2 py-1.5 text-[12.5px]"
            style={{ borderColor: "var(--color-rule)" }}
            title="Upload a folder from your computer"
          >Upload folder</button>
        </div>
        <input ref={fileInputRef} type="file" multiple hidden
               onChange={e => upload(e.target.files, false)} />
        <input ref={dirInputRef} type="file" hidden
               // eslint-disable-next-line @typescript-eslint/ban-ts-comment
               // @ts-ignore – non-standard but supported in Chromium
               webkitdirectory=""
               directory=""
               multiple
               onChange={e => upload(e.target.files, true)} />
        <p className="mt-2 text-[10.5px]" style={{ color: "var(--color-muted)" }}>
          Folder upload works in Chrome / Edge.  Files land in the project folder on your home server.
        </p>
      </div>
      {preview && <FilePreviewModal source={preview} onClose={() => setPreview(null)} />}
    </aside>
  );
}
