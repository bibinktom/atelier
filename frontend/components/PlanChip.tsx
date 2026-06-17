"use client";
import { useState } from "react";
import { PlanView } from "@/lib/types";
import { Markdown } from "./Markdown";

export function PlanChip({ plan }: { plan: PlanView }) {
  const [open, setOpen] = useState(false);
  const tone = {
    fg: "var(--color-ink-2)",
    bg: "var(--color-paper-2)",
    bd: "var(--color-rule)",
  };

  return (
    <div className="mb-2.5 max-w-[640px]">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className="chip cursor-pointer transition hover:opacity-90"
        style={{
          color: tone.fg,
          background: tone.bg,
          borderColor: tone.bd,
        }}
        aria-expanded={open}
        title={plan.model ? `Pre-flight plan from ${plan.model}` : "Pre-flight plan"}
      >
        <span aria-hidden style={{ color: tone.fg, fontStyle: "italic" }}>✧</span>
        <span className="chip-dot" style={{ background: "transparent" }} />
        <span style={{ fontFeatureSettings: '"smcp"' }}>
          Pre-flight plan
        </span>
        <span aria-hidden className="ml-1" style={{ opacity: 0.65, fontSize: "0.85em" }}>
          {open ? "▾" : "▸"}
        </span>
      </button>
      {open && (
        <div
          className="mt-2 border-l-2 px-4 py-3 text-[13.5px] leading-relaxed"
          style={{
            borderColor: "var(--color-ink-2)",
            background: "var(--color-paper-3)",
            color: "var(--color-ink-2)",
          }}
        >
          <div
            className="mb-1.5 text-[10px] uppercase tracking-[0.22em]"
            style={{ color: "var(--color-muted)" }}
          >
            Plan{plan.model ? ` · ${plan.model.split("/").pop()}` : ""}
          </div>
          <Markdown>{plan.text}</Markdown>
        </div>
      )}
    </div>
  );
}
