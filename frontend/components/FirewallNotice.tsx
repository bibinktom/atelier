"use client";
import { useState } from "react";
import { FirewallView } from "@/lib/types";

// Friendly labels for the scanner names the backend reports in `flagged`.
const SCANNER_LABEL: Record<string, string> = {
  PromptInjection: "prompt injection",
  Toxicity: "toxic content",
  Secrets: "leaked secret",
  token_limit: "message too long",
  firewall_unavailable: "filter unavailable",
};

export function FirewallNotice({ firewall }: { firewall: FirewallView }) {
  const [open, setOpen] = useState(false);
  const blocked = firewall.status === "blocked";
  const alignment = firewall.status === "alignment";
  // For an alignment finding, the detail is the critic's free-text reason; for
  // input/output it's the list of scanner categories.
  const details = alignment
    ? [firewall.reason, firewall.severity ? `severity: ${firewall.severity}` : null].filter(Boolean) as string[]
    : (firewall.flagged ?? []).map(f => SCANNER_LABEL[f] ?? f);
  const expandable = details.length > 0;

  // Blocked input OR a hard-blocked alignment hijack = brick (the single accent),
  // assertive. Advisory alignment warning + redaction = quiet paper tone.
  const assertive = blocked || (alignment && firewall.blocked);
  const tone = assertive
    ? { fg: "var(--color-paper)", bg: "var(--color-brick)", bd: "var(--color-brick)" }
    : { fg: "var(--color-ink-2)", bg: "var(--color-paper-2)", bd: "var(--color-rule)" };
  const icon = blocked ? "⛒" : alignment ? "⚐" : "▤";
  const label = blocked
    ? "Blocked by the safety filter"
    : alignment
      ? (firewall.blocked ? "Answer withheld — agent off-task" : "Agent actions flagged — off-task")
      : "Sensitive content redacted";
  const title = blocked
    ? "This message was not sent to the model"
    : alignment
      ? "The agent's actions appeared to diverge from your request (possible prompt-injection hijack)"
      : "The answer was scrubbed before display";

  return (
    <span className="inline-flex flex-col items-start" style={{ width: "100%", maxWidth: 520 }}>
      <span
        className={`chip ${expandable ? "cursor-pointer" : ""}`}
        onClick={expandable ? () => setOpen(o => !o) : undefined}
        style={{ color: tone.fg, background: tone.bg, borderColor: tone.bd }}
        title={title}
      >
        <span aria-hidden style={{ fontSize: "0.95em" }}>{icon}</span>
        <span style={{ fontVariantNumeric: "tabular-nums" }}>{label}</span>
        {expandable && (
          <span aria-hidden className="ml-1" style={{ opacity: 0.7, fontSize: "0.85em" }}>
            {open ? "▾" : "▸"}
          </span>
        )}
      </span>
      {expandable && open && (
        <div
          className="mt-1.5 border-l-2 px-3 py-2 text-[12.5px] leading-relaxed"
          style={{ borderColor: "var(--color-brick)", background: "var(--color-paper-3)", color: "var(--color-ink-2)", width: "100%" }}
        >
          <div className="mb-1 text-[10px] uppercase tracking-[0.22em]" style={{ color: "var(--color-muted)" }}>
            {blocked ? "Why" : alignment ? "Audit" : "Scrubbed"}
          </div>
          <ul className="space-y-1">
            {details.map((it, i) => (
              <li key={i} className="flex gap-2"><span className="opacity-60">·</span><span>{it}</span></li>
            ))}
          </ul>
        </div>
      )}
    </span>
  );
}
