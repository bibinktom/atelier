"use client";
import { useState } from "react";
import { ReflectView } from "@/lib/types";

export function ReflectChip({ reflect }: { reflect: ReflectView }) {
  const [open, setOpen] = useState(false);
  const isRunning = reflect.status === "running";
  const isOk = reflect.status === "ok";
  const hasIssues = reflect.status === "issues";

  const label = isRunning ? "Auditing answer" : isOk ? "Verified" : "Revised after audit";
  const icon = isRunning ? "⌬" : isOk ? "✓" : "△";
  const tone = isOk
    ? { fg: "var(--color-ink-2)", bg: "var(--color-paper-2)", bd: "var(--color-rule)" }
    : { fg: "var(--color-ink)", bg: "var(--color-paper-3)", bd: "var(--color-ink)" };
  const expandable = hasIssues && (reflect.issues?.length ?? 0) > 0;

  return (
    <span className="inline-flex flex-col items-start" style={{ width: "100%", maxWidth: 520 }}>
      <span
        className={`chip ${isRunning ? "chip-running" : ""} ${expandable ? "cursor-pointer" : ""}`}
        onClick={expandable ? () => setOpen(o => !o) : undefined}
        style={{ color: tone.fg, background: tone.bg, borderColor: tone.bd }}
        title={hasIssues ? "Click to see what was flagged" : ""}
      >
        <span aria-hidden style={{ color: tone.fg, fontSize: "0.95em" }}>{icon}</span>
        <span className={`chip-dot ${isRunning ? "live" : ""}`} style={{ background: isRunning ? tone.fg : "transparent" }} />
        <span style={{ fontVariantNumeric: "tabular-nums" }}>{label}</span>
        {expandable && (
          <span aria-hidden className="ml-1" style={{ opacity: 0.65, fontSize: "0.85em" }}>
            {open ? "▾" : "▸"}
          </span>
        )}
      </span>
      {expandable && open && (
        <div
          className="mt-1.5 border-l-2 px-3 py-2 text-[12.5px] leading-relaxed"
          style={{
            borderColor: "var(--color-ink-2)",
            background: "var(--color-paper-3)",
            color: "var(--color-ink-2)",
            width: "100%",
          }}
        >
          <div className="mb-1 text-[10px] uppercase tracking-[0.22em]" style={{ color: "var(--color-muted)" }}>
            Verifier flagged
          </div>
          <ul className="space-y-1">
            {reflect.issues?.map((it, i) => (
              <li key={i} className="flex gap-2"><span className="opacity-60">·</span><span>{it}</span></li>
            ))}
          </ul>
        </div>
      )}
    </span>
  );
}
