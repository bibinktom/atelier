"use client";
import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";

// One source descriptor for both generated artefacts (/files/<id>) and
// workspace files (/workspaces/<wid>/download?path=). The modal builds the
// inline-preview URL and the download URL from this; renders a different
// pane depending on mime type.
export type PreviewSource = {
  filename: string;
  mime: string;          // best-effort; may be empty for workspace files
  size?: number | null;
  previewUrl: string;    // ...?inline=1
  downloadUrl: string;   // raw download
};

const TEXT_EXTS = new Set([
  "md", "markdown", "txt", "log", "json", "yaml", "yml",
  "py", "js", "ts", "tsx", "jsx", "html", "css", "sh",
  "toml", "ini", "cfg", "csv", "tsv", "xml", "rst",
]);
const PDF_LIKE = new Set([
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.ms-excel",
  "application/vnd.ms-powerpoint",
  "application/msword",
  "application/vnd.oasis.opendocument.spreadsheet",
  "application/vnd.oasis.opendocument.presentation",
  "application/vnd.oasis.opendocument.text",
  "text/csv",
]);

function extOf(filename: string) {
  const i = filename.lastIndexOf(".");
  return i >= 0 ? filename.slice(i + 1).toLowerCase() : "";
}

function classify(src: PreviewSource): "pdf" | "image" | "markdown" | "code" | "text" | "binary" {
  const m = (src.mime || "").toLowerCase();
  const ext = extOf(src.filename);
  if (PDF_LIKE.has(m)) return "pdf";
  if (m.startsWith("image/")) return "image";
  if (ext === "md" || ext === "markdown") return "markdown";
  if (m === "application/json" || ext === "json") return "code";
  if (m === "application/javascript" || m.startsWith("text/")
      || TEXT_EXTS.has(ext)) {
    if (["py","js","ts","tsx","jsx","html","css","sh","yaml","yml","toml","ini","xml"].includes(ext)) return "code";
    return "text";
  }
  return "binary";
}

export function FilePreviewModal({
  source, onClose,
}: { source: PreviewSource | null; onClose: () => void }) {
  const [text, setText] = useState<string | null>(null);
  const [textErr, setTextErr] = useState<string | null>(null);

  useEffect(() => {
    if (!source) { setText(null); setTextErr(null); return; }
    const k = classify(source);
    if (k === "markdown" || k === "code" || k === "text") {
      let cancelled = false;
      setText(null); setTextErr(null);
      fetch(source.previewUrl, { credentials: "include" })
        .then(r => r.ok ? r.text() : Promise.reject(new Error(`HTTP ${r.status}`)))
        .then(t => { if (!cancelled) setText(t); })
        .catch(e => { if (!cancelled) setTextErr(String(e?.message || e)); });
      return () => { cancelled = true; };
    }
  }, [source]);

  // Esc to close.
  useEffect(() => {
    if (!source) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [source, onClose]);

  if (!source) return null;
  const kind = classify(source);

  return (
    <div
      className="fixed inset-0 z-50 flex items-stretch justify-center safe-pt safe-pb safe-pl safe-pr"
      style={{ background: "rgba(20,18,15,0.45)" }}
      onClick={onClose}
    >
      <div
        className="m-0 flex w-full max-w-5xl flex-col overflow-hidden border shadow-2xl md:m-6 md:rounded-md"
        style={{ borderColor: "var(--color-rule)", background: "var(--color-paper)" }}
        onClick={e => e.stopPropagation()}
      >
        <header className="flex items-center justify-between gap-3 border-b px-4 py-3"
                style={{ borderColor: "var(--color-rule)" }}>
          <div className="min-w-0">
            <div className="text-[11px] uppercase tracking-[0.18em]" style={{ color: "var(--color-muted)" }}>
              Preview · {kind}
            </div>
            <div className="truncate font-medium">{source.filename}</div>
          </div>
          <div className="flex items-center gap-2">
            <a
              href={source.downloadUrl}
              download={source.filename}
              className="rounded-md border px-3 py-1.5 text-[12px]"
              style={{ borderColor: "var(--color-rule)" }}
            >Download</a>
            <button
              onClick={onClose}
              aria-label="Close preview"
              className="rounded-md border px-3 py-1.5 text-[12px]"
              style={{ borderColor: "var(--color-rule)" }}
            >Close</button>
          </div>
        </header>

        <div className="min-h-[60vh] flex-1 overflow-auto thin-scroll" style={{ background: "var(--color-paper-2)" }}>
          {kind === "pdf" && (
            <iframe
              src={source.previewUrl}
              title={source.filename}
              className="h-[80vh] w-full border-0"
            />
          )}
          {kind === "image" && (
            <div className="flex items-center justify-center p-6">
              <img src={source.previewUrl} alt={source.filename}
                   className="max-h-[80vh] max-w-full object-contain" />
            </div>
          )}
          {kind === "markdown" && (
            <article className="prose prose-sm mx-auto max-w-3xl px-6 py-6">
              {textErr ? <p className="text-red-700">Could not load: {textErr}</p>
                : text === null ? <p className="text-[13px] italic" style={{ color: "var(--color-muted)" }}>Loading…</p>
                : <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>{text}</ReactMarkdown>}
            </article>
          )}
          {(kind === "code" || kind === "text") && (
            <div className="p-4">
              {textErr ? <p className="text-red-700">Could not load: {textErr}</p>
                : text === null ? <p className="text-[13px] italic" style={{ color: "var(--color-muted)" }}>Loading…</p>
                : <pre className="overflow-auto rounded border bg-[var(--color-paper)] p-4 font-mono text-[12.5px] leading-[1.55]"
                       style={{ borderColor: "var(--color-rule-soft)" }}>{text}</pre>}
            </div>
          )}
          {kind === "binary" && (
            <div className="flex h-full flex-col items-center justify-center gap-2 p-12 text-center">
              <p className="text-[13px]" style={{ color: "var(--color-muted)" }}>
                No inline preview for this file type.
              </p>
              <a href={source.downloadUrl} download={source.filename}
                 className="rounded-md border px-3 py-1.5 text-[12px]"
                 style={{ borderColor: "var(--color-rule)" }}>Download</a>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
