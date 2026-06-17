"use client";
import { useState } from "react";
import { ToolCallView } from "@/lib/types";

type AskArgs = {
  question?: string;
  options?: string[];
  allow_other?: boolean;
  multiple?: boolean;
};

export function UserQuestionChip({
  tc,
  onAnswer,
}: {
  tc: ToolCallView;
  onAnswer: (text: string) => void;
}) {
  // The question + options are echoed back from the tool result; while the
  // tool is still "running" (rare — it's instant), fall back to args.
  const result = (tc.result ?? {}) as AskArgs;
  const args = (tc.args ?? {}) as AskArgs;
  const question = result.question || args.question || "(question)";
  const options: string[] = result.options || args.options || [];
  const allowOther = result.allow_other !== false && args.allow_other !== false;
  const multiple = !!(result.multiple || args.multiple);

  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [otherText, setOtherText] = useState("");
  const [submitted, setSubmitted] = useState(false);

  const submit = (text: string) => {
    if (!text.trim() || submitted) return;
    setSubmitted(true);
    onAnswer(text);
  };

  const submitMulti = () => {
    const list = Array.from(picked);
    if (otherText.trim()) list.push(otherText.trim());
    if (list.length === 0) return;
    submit(list.join("; "));
  };

  return (
    <div
      className="mb-2.5 max-w-[640px] border-l-2 px-4 py-3"
      style={{
        borderColor: "var(--color-brick)",
        background: "var(--color-paper-3)",
        color: "var(--color-ink)",
      }}
    >
      <div
        className="mb-1.5 text-[10px] uppercase tracking-[0.22em]"
        style={{ color: "var(--color-muted)" }}
      >
        {submitted ? "Answer sent" : multiple ? "Pick any" : "Pick one"}
      </div>
      <div className="mb-2.5 text-[14.5px] leading-relaxed">{question}</div>
      <div className="flex flex-wrap gap-1.5">
        {options.map((opt, i) => {
          const isPicked = picked.has(opt);
          return (
            <button
              key={i}
              type="button"
              disabled={submitted}
              onClick={() => {
                if (multiple) {
                  setPicked(prev => {
                    const next = new Set(prev);
                    if (next.has(opt)) next.delete(opt); else next.add(opt);
                    return next;
                  });
                } else {
                  submit(opt);
                }
              }}
              className="chip transition hover:opacity-90"
              style={{
                color: isPicked ? "var(--color-paper)" : "var(--color-ink)",
                background: isPicked ? "var(--color-brick)" : "var(--color-paper)",
                borderColor: isPicked ? "var(--color-brick)" : "var(--color-ink)",
                cursor: submitted ? "default" : "pointer",
                opacity: submitted ? 0.55 : 1,
              }}
            >
              <span style={{ fontFeatureSettings: '"smcp"' }}>{opt}</span>
            </button>
          );
        })}
      </div>
      {allowOther && !submitted && (
        <div className="mt-2.5 flex items-stretch gap-1.5">
          <input
            type="text"
            placeholder="Other…"
            value={otherText}
            onChange={e => setOtherText(e.target.value)}
            onKeyDown={e => {
              if (e.key === "Enter" && !multiple && otherText.trim()) {
                e.preventDefault();
                submit(otherText.trim());
              }
            }}
            className="flex-1 border px-2 py-1 text-[13px] outline-none"
            style={{
              borderColor: "var(--color-rule)",
              background: "var(--color-paper)",
              color: "var(--color-ink)",
            }}
          />
          {multiple ? (
            <button
              type="button"
              onClick={submitMulti}
              disabled={picked.size === 0 && !otherText.trim()}
              className="chip"
              style={{
                color: "var(--color-paper)",
                background: "var(--color-brick)",
                borderColor: "var(--color-brick)",
                cursor: "pointer",
              }}
            >
              Send
            </button>
          ) : (
            <button
              type="button"
              onClick={() => otherText.trim() && submit(otherText.trim())}
              disabled={!otherText.trim()}
              className="chip"
              style={{
                color: "var(--color-paper)",
                background: "var(--color-brick)",
                borderColor: "var(--color-brick)",
                cursor: otherText.trim() ? "pointer" : "default",
                opacity: otherText.trim() ? 1 : 0.5,
              }}
            >
              ↵
            </button>
          )}
        </div>
      )}
    </div>
  );
}
