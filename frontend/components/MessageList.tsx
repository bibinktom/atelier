"use client";
import { useEffect, useRef } from "react";
import { Markdown } from "./Markdown";
import { ToolChip } from "./ToolChip";
import { UserQuestionChip } from "./UserQuestionChip";
import { TaskListPanel } from "./TaskListPanel";
import { PlanChip } from "./PlanChip";
import { ReflectChip } from "./ReflectChip";
import { FirewallNotice } from "./FirewallNotice";
import { FileChip } from "./FileChip";
import { TipsRotator } from "./TipsRotator";
import { api } from "@/lib/api";
import { AssistantTurn, FileRec, StoredMessage } from "@/lib/types";

export type RenderMessage =
  | { kind: "user"; id: string; text: string; error?: string; images: { mime: string; filename: string; file_id?: string; previewSrc?: string }[] }
  | { kind: "assistant"; turn: AssistantTurn; streaming: boolean };

export function MessageList({
  messages, userInitial, onAnswer,
}: {
  messages: RenderMessage[];
  userInitial: string;
  onAnswer?: (text: string) => void;
}) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [messages]);

  return (
    <div className="mx-auto w-full max-w-[760px] px-6 py-8">
      {messages.map((m, i) =>
        m.kind === "user" ? (
          <UserMsg key={"u" + i} m={m} initial={userInitial} />
        ) : (
          <AssistantMsg key={"a" + i} turn={m.turn} streaming={m.streaming} onAnswer={onAnswer} />
        )
      )}
      <div ref={endRef} />
    </div>
  );
}

function UserMsg({ m, initial }: { m: Extract<RenderMessage, { kind: "user" }>; initial: string }) {
  return (
    <div className="mb-8 flex gap-4">
      <span
        className="mt-1 grid h-7 w-7 flex-none place-items-center rounded-full font-display text-xs"
        style={{ background: "var(--color-brick)", color: "var(--color-paper)" }}
      >
        {initial}
      </span>
      <div className="min-w-0 flex-1">
        {m.images.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-2">
            {m.images.map((im, i) => {
              const src = im.previewSrc || (im.file_id ? api.fileUrl(im.file_id) : null);
              return (
                <div key={i} className="rounded border bg-[var(--color-paper-2)]" style={{ borderColor: "var(--color-rule)" }}>
                  {src
                    ? <img src={src} alt={im.filename} className="h-28 w-28 rounded object-cover" />
                    : <div className="grid h-20 w-28 place-items-center px-3 text-[10px] uppercase tracking-widest" style={{ color: "var(--color-muted)" }}>
                        {im.filename}
                      </div>}
                </div>
              );
            })}
          </div>
        )}
        {m.text && (
          <div
            className="whitespace-pre-wrap text-[15px] leading-relaxed"
            style={{ color: "var(--color-ink)" }}
          >
            {m.text}
          </div>
        )}
        {m.error && (
          <div
            className="mt-2 border px-3 py-2 text-[13px]"
            style={{ borderColor: "var(--color-ink)", background: "var(--color-paper-3)", color: "var(--color-ink)" }}
          >
            {m.error}
          </div>
        )}
      </div>
    </div>
  );
}

function AssistantMsg({ turn, streaming, onAnswer }: { turn: AssistantTurn; streaming: boolean; onAnswer?: (text: string) => void }) {
  const showCursor = streaming && !turn.done;
  // ask_user_question chips render full-width below the row of normal tool chips,
  // because they're an interactive prompt not a status indicator.
  const questionChips = turn.toolCalls.filter(tc => tc.name === "ask_user_question");
  const otherChips = turn.toolCalls.filter(tc => tc.name !== "ask_user_question");
  return (
    <div className="mb-10 flex gap-4">
      <span
        className="mt-1 grid h-7 w-7 flex-none place-items-center rounded-full font-display text-xs"
        style={{ background: "var(--color-paper-3)", color: "var(--color-ink)", border: "1px solid var(--color-rule)" }}
        aria-hidden
      >
        ✦
      </span>
      <div className="min-w-0 flex-1">
        {turn.plan && <PlanChip plan={turn.plan} />}
        {turn.tasks && turn.tasks.length > 0 && <TaskListPanel tasks={turn.tasks} />}
        {turn.reflect && <div className="mb-2.5"><ReflectChip reflect={turn.reflect} /></div>}
        {turn.firewall && <div className="mb-2.5"><FirewallNotice firewall={turn.firewall} /></div>}
        {otherChips.length > 0 && (
          <div className="mb-2.5 flex flex-wrap gap-1.5">
            {otherChips.map(tc => <ToolChip key={tc.id} tc={tc} />)}
          </div>
        )}
        {questionChips.map(tc => (
          <UserQuestionChip key={tc.id} tc={tc} onAnswer={onAnswer ?? (() => {})} />
        ))}
        {showCursor && turn.notice && (
          <div className="mb-2.5 inline-flex items-center gap-2 rounded-md border px-2.5 py-1 text-[12px]"
               style={{ borderColor: "var(--color-rule)", background: "var(--color-paper-2)", color: "var(--color-muted)" }}>
            <span aria-hidden>⏳</span>{turn.notice}
          </div>
        )}
        {turn.text && (
          <div className={showCursor ? "cursor-blink" : ""}>
            <Markdown>{turn.text}</Markdown>
          </div>
        )}
        {!turn.text && showCursor && <TipsRotator />}
        {turn.files.length > 0 && (
          <div className="mt-3 grid max-w-[520px] grid-cols-1 gap-2 sm:grid-cols-2">
            {turn.files.map(f => <FileChip key={f.id} file={f} />)}
          </div>
        )}
      </div>
    </div>
  );
}

export function fromStored(stored: StoredMessage[], filesById: Map<string, FileRec>): RenderMessage[] {
  const out: RenderMessage[] = [];
  let cur: AssistantTurn | null = null;
  const flush = () => { if (cur) { out.push({ kind: "assistant", turn: cur, streaming: false }); cur = null; } };

  for (const m of stored) {
    if (m.role === "user") {
      flush();
      const c = m.content;
      const text = typeof c === "string" ? c : c?.text ?? "";
      const images = (typeof c === "object" && c?.images) ? c.images.map((im: any) => ({
        filename: im.filename, mime: im.mime, file_id: im.file_id,
      })) : [];
      out.push({ kind: "user", id: m.id, text, images });
    } else if (m.role === "assistant") {
      if (!cur) cur = { id: m.id, text: "", toolCalls: [], files: [], done: true };
      const c = m.content;
      const text = typeof c === "string" ? c : c?.content ?? "";
      cur.text = text;
      // Attach a persisted plan from the first hop's assistant message.
      if (!cur.plan && c && typeof c === "object" && c.plan?.text) {
        cur.plan = { text: String(c.plan.text), model: c.plan.model ? String(c.plan.model) : undefined };
      }
      const tcalls = (typeof c === "object" && c?.tool_calls) ? c.tool_calls : [];
      for (const tc of tcalls) {
        let args = {};
        try { args = JSON.parse(tc.function.arguments || "{}"); } catch {}
        cur.toolCalls.push({
          id: tc.id, name: tc.function.name, args, status: "done",
        });
      }
    } else if (m.role === "tool") {
      if (!cur) continue;
      const c = m.content;
      const tc = cur.toolCalls.find(x => x.id === c.tool_call_id);
      if (tc) {
        tc.result = c.result; tc.status = c.result?.error ? "error" : "done";
        if (c.result?.file_id && filesById.has(c.result.file_id)) {
          const f = filesById.get(c.result.file_id)!;
          tc.file = f;
          if (!cur.files.find(ff => ff.id === f.id)) cur.files.push(f);
        }
      }
      // Task tool results embed a full snapshot of the task list. Pull it onto
      // the turn so reload restores the panel state.
      if (typeof c?.name === "string" && c.name.startsWith("task_") && Array.isArray(c.result?.tasks)) {
        cur.tasks = c.result.tasks;
      }
    }
  }
  flush();
  return out;
}
