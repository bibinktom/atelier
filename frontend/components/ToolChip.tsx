"use client";
import { useState } from "react";
import { ToolCallView } from "@/lib/types";
import { DiffView } from "./DiffView";

// Truncate prose labels at a word boundary so we never produce something like
// "Flask latest version 2024 popularity k" — fall back to a hard cut only if
// there's no whitespace inside the keep zone.
function clip(s: string, max = 48): string {
  if (!s) return "";
  if (s.length <= max) return s;
  const head = s.slice(0, max);
  const lastSpace = head.lastIndexOf(" ");
  const cut = lastSpace > Math.floor(max * 0.5) ? head.slice(0, lastSpace) : head;
  return cut.trimEnd() + "…";
}

const META: Record<string, { label: (a: any) => string; running: string; tone: ToneKey; icon: string }> = {
  web_search: {
    label: a => `“${clip(String(a?.query ?? ""))}”`,
    running: "Searching the web",
    tone: "cobalt",
    icon: "✦",
  },
  web_fetch: {
    label: a => new URL(a?.url ?? "https://x").host.replace(/^www\./, ""),
    running: "Reading page",
    tone: "cobalt",
    icon: "↗",
  },
  generate_pdf:  { label: a => a?.filename ?? "document.pdf",   running: "Drafting PDF",       tone: "brick", icon: "▰" },
  generate_xlsx: { label: a => a?.filename ?? "workbook.xlsx",  running: "Composing workbook", tone: "moss",  icon: "▤" },
  generate_pptx: { label: a => a?.filename ?? "deck.pptx",      running: "Building deck",      tone: "brick", icon: "◆" },

  workspace_list:  { label: a => a?.path ?? ".",                running: "Listing files",     tone: "ink",  icon: "◇" },
  workspace_read:  { label: a => a?.path ?? "?",                running: "Reading file",      tone: "ink",  icon: "◇" },
  workspace_write: { label: a => a?.path ?? "?",                running: "Writing file",      tone: "ink",  icon: "✎" },
  workspace_edit:  { label: a => a?.path ?? "?",                running: "Editing file",      tone: "ink",  icon: "✎" },
  workspace_grep:  { label: a => `/${clip(String(a?.pattern ?? ""), 32)}/`, running: "Searching files", tone: "ink", icon: "✦" },
  workspace_glob:  { label: a => a?.pattern ?? "?",             running: "Finding files",     tone: "ink",  icon: "✦" },
  workspace_bash:  { label: a => `$ ${clip(String(a?.command ?? ""))}`, running: "Running command", tone: "brick", icon: "▶" },
  codebase_search: { label: a => `“${clip(String(a?.query ?? ""), 40)}”`, running: "Searching codebase", tone: "moss", icon: "⌗" },
  workspace_git_clone: { label: a => { try { return new URL(a?.url ?? "https://x").pathname.replace(/^\//, "").replace(/\.git$/, ""); } catch { return "repo"; } }, running: "Cloning repo", tone: "moss", icon: "⎇" },
  workspace_apply_patch: { label: a => "patch", running: "Applying patch", tone: "brick", icon: "✎" },

  list_skills: { label: () => "skills", running: "Listing skills", tone: "ink", icon: "❖" },
  apply_skill: { label: a => a?.name ?? "skill", running: "Applying skill", tone: "ink", icon: "❖" },

  task_create: { label: a => `+ ${clip(String(a?.subject ?? "task"), 50)}`, running: "Adding task", tone: "moss", icon: "▢" },
  task_list:   { label: ()  => "tasks",                                  running: "Reading tasks", tone: "moss", icon: "▢" },
  task_get:    { label: a => `#${(a?.id ?? "").slice(0, 6)}`,            running: "Reading task",  tone: "moss", icon: "▢" },
  task_update: { label: a => `${(a?.status ?? "update")}`,               running: "Updating task", tone: "moss", icon: "▣" },
  task_stop:   { label: ()  => "stop",                                   running: "Stopping task", tone: "moss", icon: "✕" },
  task_output: { label: a => `#${(a?.id ?? "").slice(0, 6)} log`,        running: "Logging task",  tone: "moss", icon: "▢" },

  schedule_create:  { label: a => clip(String(a?.name ?? "schedule"), 36),    running: "Scheduling",          tone: "ink", icon: "⌛" },
  schedule_list:    { label: ()  => "schedules",                              running: "Reading schedules",   tone: "ink", icon: "⌛" },
  schedule_delete:  { label: a => `#${(a?.id ?? "").slice(0, 6)} ✕`,           running: "Removing schedule",   tone: "ink", icon: "⌛" },
  schedule_run_now: { label: a => `#${(a?.id ?? "").slice(0, 6)} ▶`,           running: "Firing schedule",     tone: "ink", icon: "⌛" },
};

type ToneKey = "brick" | "moss" | "cobalt" | "ink";
// Tool-family colour coding — the chips are the differentiated surface (see
// CLAUDE.md). brick = generation/shell (primary accent), moss = file/workspace
// semantics, cobalt = web/research, ink = neutral utility. Each tone is an
// accent-on-soft-wash with an accent border so families read at a glance.
const TONES: Record<ToneKey, { fg: string; bg: string; bd: string }> = {
  brick:  { fg: "var(--color-brick)",  bg: "var(--color-brick-soft)",  bd: "var(--color-brick)" },
  moss:   { fg: "var(--color-moss)",   bg: "var(--color-moss-soft)",   bd: "var(--color-moss)" },
  cobalt: { fg: "var(--color-cobalt)", bg: "var(--color-cobalt-soft)", bd: "var(--color-cobalt)" },
  ink:    { fg: "var(--color-ink)",    bg: "var(--color-paper-3)",     bd: "var(--color-rule)" },
};

export function ToolChip({ tc }: { tc: ToolCallView }) {
  const meta = META[tc.name] ?? { label: () => tc.name, running: tc.name, tone: "ink" as ToneKey, icon: "•" };
  const tone = TONES[meta.tone];

  const isRunning = tc.status === "running";
  const isError = tc.status === "error" || tc.result?.error;
  // While running, append elapsed seconds so the user can tell the tool is
  // alive (vs. frozen). Updated by tool_progress heartbeats from the backend.
  const elapsedS = isRunning && tc.startedAt
    ? Math.max(0, Math.floor((Date.now() - tc.startedAt) / 1000))
    : 0;
  const labelText = isRunning
    ? (elapsedS > 0 ? `${meta.running} · ${elapsedS}s` : meta.running)
    : meta.label(tc.args);
  const hasDiff = typeof tc.result?.diff === "string" && tc.result.diff.length > 0;
  const hasSubagent = !!tc.subagent && tc.subagent.trace.length > 0;
  const expandable = hasSubagent || hasDiff;
  const [open, setOpen] = useState(false);

  // Hover-tooltip with the full prose argument when the chip label was truncated.
  // The truncator inside `clip` adds a trailing "…" — we look for that to decide
  // whether a tooltip is useful. When not truncated, we don't bother repeating it.
  const isTruncated = !isRunning && labelText.includes("…");
  const fullText = (() => {
    if (!isTruncated) return "";
    const a = tc.args ?? {};
    return String(a.query ?? a.command ?? a.pattern ?? a.subject ?? "");
  })();

  const chip = (
    <span
      className={`chip ${isRunning ? "chip-running" : ""} ${expandable ? "cursor-pointer" : ""}`}
      onClick={expandable ? () => setOpen(o => !o) : undefined}
      style={{
        color: isError ? "var(--color-ink)" : tone.fg,
        background: isError ? "var(--color-paper-3)" : tone.bg,
        borderColor: isError ? "var(--color-ink)" : tone.bd,
      }}
      title={isError ? (tc.result?.error || "error") : (fullText || (expandable ? "click to expand" : ""))}
    >
      <span aria-hidden style={{ color: tone.fg, fontSize: "0.95em" }}>{meta.icon}</span>
      <span className={`chip-dot ${isRunning ? "live" : ""}`} style={{ background: isRunning ? tone.fg : "transparent" }} />
      <span style={{ fontVariantNumeric: "tabular-nums" }}>
        {labelText}
        {tc.cached && <span className="ml-1.5" style={{ opacity: 0.6, fontSize: "0.85em" }} aria-label="cached">⟲</span>}
      </span>
      {expandable && (
        <span aria-hidden className="ml-1" style={{ opacity: 0.65, fontSize: "0.85em" }}>
          {open ? "▾" : "▸"}
        </span>
      )}
      {isError && <span style={{ marginLeft: 4, opacity: 0.8 }}>· failed</span>}
    </span>
  );

  const fw = tc.firewall;
  // Show a panel below the chip when expanded, OR whenever a firewall finding is
  // attached (always surface a security warning, even on a non-expandable chip).
  if (!(expandable && open) && !fw) return chip;

  return (
    <span className="inline-flex flex-col items-start gap-2" style={{ width: "100%", maxWidth: 560 }}>
      {chip}
      {fw && <ToolFirewallWarn fw={fw} />}
      {open && hasSubagent && <SubagentTrace tc={tc} />}
      {open && hasDiff && <DiffView diff={String(tc.result.diff)} />}
    </span>
  );
}


function ToolFirewallWarn({ fw }: { fw: NonNullable<ToolCallView["firewall"]> }) {
  const isCode = fw.status === "code_flagged";
  const title = isCode ? "Insecure code flagged" : "Untrusted content — treated as data";
  const lines = isCode
    ? (fw.issues ?? []).map(i =>
        `${i.pattern_id ?? "issue"}${i.line ? ` (line ${i.line})` : ""}${i.description ? `: ${i.description}` : ""}`)
    : (fw.flagged ?? []);
  return (
    <div
      className="border-l-2 px-3 py-2 text-[12px] leading-relaxed"
      style={{ borderColor: "var(--color-brick)", background: "var(--color-brick-soft)", color: "var(--color-ink)", width: "100%" }}
    >
      <div className="mb-1 text-[10px] uppercase tracking-[0.22em]" style={{ color: "var(--color-brick)" }}>
        ⚠ Firewall · {title}{fw.treatment ? ` · ${fw.treatment.replace(/^Treatment\./, "")}` : ""}
      </div>
      {lines.length > 0 && (
        <ul className="space-y-0.5">
          {lines.slice(0, 6).map((l, i) => (
            <li key={i} className="flex gap-2"><span className="opacity-60">·</span><span>{l}</span></li>
          ))}
        </ul>
      )}
    </div>
  );
}


function SubagentTrace({ tc }: { tc: ToolCallView }) {
  const sub = tc.subagent;
  if (!sub) return null;
  const taskLine = sub.task_type ? `${sub.task_type}` : "specialist";
  const modelLine = sub.model ? sub.model.split("/").pop() : "";
  return (
    <div
      className="border-l-2 px-3 py-2.5 text-[12.5px] leading-relaxed"
      style={{
        borderColor: "var(--color-ink-2)",
        background: "var(--color-paper-3)",
        color: "var(--color-ink-2)",
        width: "100%",
      }}
    >
      <div className="mb-1.5 text-[10px] uppercase tracking-[0.22em]" style={{ color: "var(--color-muted)" }}>
        Sub-agent · {taskLine}{modelLine ? ` · ${modelLine}` : ""}
      </div>
      <ol className="space-y-1.5">
        {sub.trace.map((entry, i) => {
          if (entry.kind === "text") {
            return (
              <li key={i} className="flex gap-2">
                <span className="opacity-60">·</span>
                <span className="italic" style={{ color: "var(--color-ink-2)" }}>{entry.text}</span>
              </li>
            );
          }
          return (
            <li key={i} className="flex gap-2">
              <span aria-hidden style={{ opacity: entry.ok ? 0.6 : 0.9 }}>{entry.ok ? "▸" : "✕"}</span>
              <span>
                <code style={{ fontSize: "0.92em" }}>{entry.name}</code>
                <span className="opacity-65">
                  {" "}({Object.keys(entry.args || {}).slice(0, 2).map(k => `${k}=${String(entry.args[k]).slice(0, 24)}`).join(", ")})
                </span>
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
