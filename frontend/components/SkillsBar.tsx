"use client";
import { useState } from "react";
import { Skill } from "@/lib/types";

export function SkillsBar({
  skills, onUse, onAccept, onDelete, onCreate,
}: {
  skills: Skill[];
  onUse: (s: Skill) => void;
  onAccept: (s: Skill) => void;
  onDelete: (s: Skill) => void;
  onCreate: (name: string, prompt: string) => Promise<void>;
}) {
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [prompt, setPrompt] = useState("");

  const accepted = skills.filter(s => !s.is_suggested);
  const suggested = skills.filter(s => s.is_suggested);

  return (
    <div className="mt-9 grid gap-3 sm:grid-cols-2">
      {accepted.map(s => (
        <SkillCard key={s.id} s={s} onUse={onUse} onDelete={onDelete} />
      ))}
      {suggested.length > 0 && (
        <div className="sm:col-span-2">
          <p className="mb-2 text-[11px] uppercase tracking-[0.2em]" style={{ color: "var(--color-muted)" }}>
            Suggested skills · the AI noticed you do this
          </p>
          <div className="grid gap-3 sm:grid-cols-2">
            {suggested.map(s => (
              <div key={s.id} className="rounded-lg border bg-[var(--color-paper-2)] p-4"
                   style={{ borderColor: "var(--color-rule)" }}>
                <div className="font-medium text-[15px]">{s.name}</div>
                {s.description && (
                  <div className="mt-1 text-[12.5px]" style={{ color: "var(--color-muted)" }}>{s.description}</div>
                )}
                <div className="mt-2.5 flex gap-2">
                  <button onClick={() => onAccept(s)}
                          className="border px-2 py-1 text-[12px]"
                          style={{ borderColor: "var(--color-ink)", background: "var(--color-ink)", color: "var(--color-paper)" }}>
                    Save
                  </button>
                  <button onClick={() => onDelete(s)}
                          className="border px-2 py-1 text-[12px]"
                          style={{ borderColor: "var(--color-rule)", color: "var(--color-muted)" }}>
                    Dismiss
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {creating ? (
        <div className="rounded-lg border p-4 sm:col-span-2"
             style={{ borderColor: "var(--color-ink)", background: "var(--color-paper-2)" }}>
          <input
            autoFocus value={name} onChange={e => setName(e.target.value)}
            placeholder="Skill name (e.g. Weekly grocery list)"
            className="w-full border bg-[var(--color-paper)] px-2 py-1.5 text-[14px] outline-none"
            style={{ borderColor: "var(--color-rule)" }}
          />
          <textarea
            value={prompt} onChange={e => setPrompt(e.target.value)}
            placeholder="Prompt to fire when this skill is used…"
            rows={4}
            className="mt-2 w-full resize-y border bg-[var(--color-paper)] px-2 py-1.5 text-[13px] outline-none"
            style={{ borderColor: "var(--color-rule)" }}
          />
          <div className="mt-2 flex gap-2">
            <button
              onClick={async () => {
                if (!name.trim() || !prompt.trim()) return;
                await onCreate(name.trim(), prompt.trim());
                setName(""); setPrompt(""); setCreating(false);
              }}
              className="border px-3 py-1 text-[13px]"
              style={{ borderColor: "var(--color-ink)", background: "var(--color-ink)", color: "var(--color-paper)" }}
            >Save skill</button>
            <button onClick={() => setCreating(false)}
                    className="border px-3 py-1 text-[13px]"
                    style={{ borderColor: "var(--color-rule)", color: "var(--color-muted)" }}>Cancel</button>
          </div>
        </div>
      ) : (
        <button
          onClick={() => setCreating(true)}
          className="rounded-lg border border-dashed p-4 text-left transition hover:border-[var(--color-ink)]"
          style={{ borderColor: "var(--color-rule)", color: "var(--color-muted)" }}
        >
          <span aria-hidden className="mr-1">＋</span>
          <span className="text-[14px]">New skill — save a reusable prompt</span>
        </button>
      )}
    </div>
  );
}

function SkillCard({ s, onUse, onDelete }: { s: Skill; onUse: (s: Skill) => void; onDelete: (s: Skill) => void }) {
  return (
    <div className="group relative rounded-lg border bg-[var(--color-paper-2)] p-4 transition hover:-translate-y-0.5 hover:border-[var(--color-ink)]"
         style={{ borderColor: "var(--color-rule)" }}>
      <button onClick={() => onUse(s)} className="block w-full text-left">
        <div className="text-[11px] uppercase tracking-[0.18em]" style={{ color: "var(--color-ink)" }}>
          Skill
        </div>
        <div className="mt-1 font-medium text-[15.5px]">{s.name}</div>
        {s.description && (
          <div className="mt-1 line-clamp-2 text-[13px]" style={{ color: "var(--color-muted)" }}>
            {s.description}
          </div>
        )}
      </button>
      <button onClick={() => onDelete(s)}
              className="absolute right-2 top-2 rounded p-1 opacity-0 transition group-hover:opacity-50 hover:opacity-100"
              style={{ color: "var(--color-muted)" }}
              aria-label="Delete skill">×</button>
    </div>
  );
}
