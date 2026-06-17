"use client";
import { TaskItem } from "@/lib/types";

const STATUS_GLYPH: Record<TaskItem["status"], string> = {
  pending: "□",
  in_progress: "▣",
  completed: "▣",
  cancelled: "✕",
};

const STATUS_LABEL: Record<TaskItem["status"], string> = {
  pending: "Pending",
  in_progress: "In progress",
  completed: "Done",
  cancelled: "Stopped",
};

export function TaskListPanel({ tasks }: { tasks: TaskItem[] }) {
  if (!tasks || tasks.length === 0) return null;
  const total = tasks.length;
  const done = tasks.filter(t => t.status === "completed").length;

  return (
    <div
      className="mb-2.5 max-w-[640px] border-l-2 px-4 py-3"
      style={{
        borderColor: "var(--color-ink-2)",
        background: "var(--color-paper-3)",
        color: "var(--color-ink)",
      }}
    >
      <div
        className="mb-2 flex items-baseline justify-between text-[10px] uppercase tracking-[0.22em]"
        style={{ color: "var(--color-muted)" }}
      >
        <span>Tasks</span>
        <span style={{ fontVariantNumeric: "tabular-nums" }}>{done}/{total}</span>
      </div>
      <ol className="space-y-1.5">
        {tasks.map(t => {
          const isDone = t.status === "completed";
          const isActive = t.status === "in_progress";
          const isCancel = t.status === "cancelled";
          return (
            <li
              key={t.id}
              className="flex items-start gap-2 text-[13.5px] leading-snug"
              style={{
                color: isCancel ? "var(--color-muted)" : "var(--color-ink)",
                opacity: isCancel ? 0.7 : 1,
              }}
            >
              <span
                aria-hidden
                className="mt-[2px] flex-none"
                style={{
                  fontSize: "0.95em",
                  color: isActive
                    ? "var(--color-brick)"
                    : isDone
                    ? "var(--color-ink-2)"
                    : "var(--color-muted)",
                }}
                title={STATUS_LABEL[t.status]}
              >
                {STATUS_GLYPH[t.status]}
              </span>
              <span
                className="flex-1"
                style={{
                  textDecoration: isDone || isCancel ? "line-through" : "none",
                  fontWeight: isActive ? 500 : 400,
                }}
              >
                {t.subject}
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
