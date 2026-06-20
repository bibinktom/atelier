"use client";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { api, BACKEND } from "@/lib/api";
import { FileChip } from "@/components/FileChip";
import { Conversation, FileRec } from "@/lib/types";
import { TomoseMark } from "@/components/TomoseMark";

type WithConv = FileRec & { conversation_title?: string };

export default function FilesPage() {
  const [user, setUser] = useState<any>(null);
  const [files, setFiles] = useState<WithConv[]>([]);
  const [convs, setConvs] = useState<Conversation[]>([]);

  useEffect(() => { (async () => {
    try {
      const me = await api.me(); setUser(me);
      const cs = await api.listConversations(); setConvs(cs.conversations);
      const all: WithConv[] = [];
      for (const c of cs.conversations) {
        try {
          const d = await api.getConversation(c.id);
          for (const f of d.files) all.push({ ...f, conversation_title: c.title });
        } catch {}
      }
      all.sort((a, b) => b.created_at - a.created_at);
      setFiles(all);
    } catch {}
  })(); }, []);

  const grouped = useMemo(() => {
    const out: Record<string, WithConv[]> = {};
    for (const f of files) {
      const d = new Date(f.created_at * 1000);
      const key = d.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric", year: "numeric" });
      (out[key] ||= []).push(f);
    }
    return Object.entries(out);
  }, [files]);

  return (
    <main className="mx-auto min-h-dvh max-w-[940px] px-8 py-12">
      <div className="mb-10 flex items-end justify-between">
        <div>
          <Link href="/" className="text-[12px] uppercase tracking-[0.22em]" style={{ color: "var(--color-muted)" }}>
            ← back to atelier
          </Link>
          <h1 className="h-display mt-2 text-[44px] leading-none">
            Files
          </h1>
          <p className="mt-2 text-[14px]" style={{ color: "var(--color-ink-2)" }}>
            Everything the studio has produced for you, by date.
          </p>
        </div>
        {user && !user.local && <span className="text-[12px]" style={{ color: "var(--color-muted)" }}>{user.email}</span>}
      </div>

      {grouped.length === 0 && (
        <p className="rounded-md border bg-[var(--color-paper-2)] px-5 py-8 italic"
           style={{ borderColor: "var(--color-rule)", color: "var(--color-muted)" }}>
          No files yet. Ask the assistant to draft a PDF, build a workbook, or create a deck.
        </p>
      )}

      {grouped.map(([day, items]) => (
        <section key={day} className="mb-10">
          <div className="rule-diamond mb-4">
            <span>{day}</span>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            {items.map(f => (
              <div key={f.id} className="flex flex-col gap-1.5">
                <FileChip file={f} />
                {f.conversation_title && (
                  <span className="px-1 text-[11px]" style={{ color: "var(--color-muted)" }}>
                    from “{f.conversation_title}”
                  </span>
                )}
              </div>
            ))}
          </div>
        </section>
      ))}

      <footer className="mt-16 flex justify-center border-t pt-8" style={{ borderColor: "var(--color-rule-soft)" }}>
        <TomoseMark />
      </footer>
    </main>
  );
}
