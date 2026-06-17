"use client";
import { useState } from "react";
import { FileRec } from "@/lib/types";
import { api } from "@/lib/api";
import { FilePreviewModal, PreviewSource } from "./FilePreviewModal";

const EXT_LABEL: Record<string, string> = {
  "application/pdf": "PDF",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "XLSX",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation": "PPTX",
};

function fmtSize(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1_048_576) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1_048_576).toFixed(1)} MB`;
}

export function FileChip({ file }: { file: FileRec }) {
  const tag = EXT_LABEL[file.mime] ?? (file.filename.split(".").pop() || "FILE").toUpperCase();
  const [open, setOpen] = useState(false);

  const source: PreviewSource = {
    filename: file.filename,
    mime: file.mime,
    size: file.size,
    previewUrl: api.filePreviewUrl(file.id),
    downloadUrl: api.fileUrl(file.id),
  };

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="group flex w-full min-w-0 items-center gap-3 rounded-md border px-3 py-2 text-left transition hover:-translate-y-px"
        style={{
          borderColor: "var(--color-rule)", background: "var(--color-paper-2)",
        }}
        title={file.filename}
      >
        <span
          className="grid h-9 w-9 shrink-0 place-items-center rounded font-display text-[10px] font-medium tracking-widest"
          style={{ background: "var(--color-moss)", color: "var(--color-paper)" }}
        >
          {tag}
        </span>
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="break-all text-sm leading-tight">{file.filename}</span>
          <span className="text-xs" style={{ color: "var(--color-muted)" }}>
            {fmtSize(file.size)} · preview
          </span>
        </span>
        <span aria-hidden className="ml-2 shrink-0 opacity-50 transition group-hover:translate-x-0.5 group-hover:opacity-100">↗</span>
      </button>
      {open && <FilePreviewModal source={source} onClose={() => setOpen(false)} />}
    </>
  );
}
