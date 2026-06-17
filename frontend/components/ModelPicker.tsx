"use client";
import { useEffect, useRef, useState } from "react";
import { Model } from "@/lib/types";

// Strip the provider prefix + ":free" suffix for a compact header label.
function shortLabel(m: Model | undefined, id: string): string {
  if (m?.label) return m.label;
  const base = id.split("/").pop() || id;
  return base.replace(/:free$/, "");
}

export function ModelPicker({
  models, value, onChange,
}: {
  models: Model[];
  value: string;            // current model id (conv.model, pending, or default)
  onChange: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const current = models.find(m => m.id === value);

  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(o => !o)}
        className="group flex items-center gap-2 rounded-md border px-3 py-1.5 text-sm transition hover:bg-[var(--color-paper-3)]"
        style={{ borderColor: "var(--color-rule)" }}
        title="Model this chat uses — switch if one is rate-limited or for vision/coding"
      >
        <span className="text-[11px] uppercase tracking-[0.16em]" style={{ color: "var(--color-muted)" }}>Model ·</span>
        <span className="max-w-[180px] truncate font-medium">{shortLabel(current, value)}</span>
        <span aria-hidden className="ml-0.5 text-xs opacity-50">▾</span>
      </button>
      {open && (
        <div
          className="absolute right-0 z-30 mt-2 w-72 overflow-hidden rounded-md border shadow-lg"
          style={{ borderColor: "var(--color-rule)", background: "var(--color-paper)" }}
        >
          <div className="px-3 py-2 text-[11px] uppercase tracking-[0.18em]" style={{ color: "var(--color-muted)" }}>
            Choose model
          </div>
          <ul className="max-h-[60vh] overflow-auto thin-scroll">
            {models.map(m => {
              const free = m.id.endsWith(":free");
              return (
                <li key={m.id}>
                  <button
                    onClick={() => { onChange(m.id); setOpen(false); }}
                    className="flex w-full items-center gap-2 px-3 py-2 text-left text-[14px] transition hover:bg-[var(--color-paper-3)]"
                    style={{ background: m.id === value ? "var(--color-paper-3)" : undefined }}
                  >
                    <span className="min-w-0 flex-1 truncate font-medium">{shortLabel(m, m.id)}</span>
                    {free && (
                      <span className="rounded px-1.5 py-0.5 text-[10px] uppercase tracking-[0.12em]"
                            style={{ background: "var(--color-moss-soft)", color: "var(--color-moss)" }}>free</span>
                    )}
                    {m.supports_images && (
                      <span aria-hidden title="vision" style={{ color: "var(--color-cobalt)" }}>◉</span>
                    )}
                    {!m.supports_tools && (
                      <span title="no tools" style={{ color: "var(--color-muted)" }} className="text-[10px]">no-tools</span>
                    )}
                  </button>
                </li>
              );
            })}
          </ul>
          <p className="border-t px-3 py-2 text-[10.5px]" style={{ borderColor: "var(--color-rule-soft)", color: "var(--color-muted)" }}>
            Switching applies from your next message. Free models share capacity and
            can rate-limit; try another if one is busy.
          </p>
        </div>
      )}
    </div>
  );
}
