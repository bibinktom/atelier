"use client";

/**
 * Minimal unified-diff renderer for workspace_write / workspace_edit / apply_patch
 * results. No Monaco/CodeMirror — a styled <pre> with red/green rows, reusing the
 * paper-and-ink accents (moss = additions, brick = deletions).
 */
export function DiffView({ diff }: { diff: string }) {
  const lines = diff.replace(/\n$/, "").split("\n");
  return (
    <div
      className="w-full overflow-auto rounded-md border font-mono text-[11.5px] leading-[1.5]"
      style={{ borderColor: "var(--color-rule)", background: "var(--color-paper-2)", maxHeight: 360 }}
    >
      <pre className="m-0 p-0">
        {lines.map((ln, i) => {
          let bg = "transparent";
          let fg = "var(--color-ink-2)";
          if (ln.startsWith("+++") || ln.startsWith("---")) {
            fg = "var(--color-muted)";
          } else if (ln.startsWith("@@")) {
            fg = "var(--color-cobalt)";
            bg = "var(--color-paper-3)";
          } else if (ln.startsWith("+")) {
            fg = "var(--color-moss)";
            bg = "var(--color-moss-soft)";
          } else if (ln.startsWith("-")) {
            fg = "var(--color-brick)";
            bg = "var(--color-brick-soft)";
          }
          return (
            <div key={i} style={{ background: bg, color: fg, padding: "0 10px", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
              {ln || " "}
            </div>
          );
        })}
      </pre>
    </div>
  );
}
