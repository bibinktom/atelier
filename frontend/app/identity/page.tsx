"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";

export default function IdentityPage() {
  const [user, setUser] = useState<any>(null);
  const [markdown, setMarkdown] = useState("");
  const [original, setOriginal] = useState("");
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => { (async () => {
    try {
      const me = await api.me(); setUser(me);
      if (me?.is_pending) return;
      const r = await api.getIdentity();
      setMarkdown(r.markdown);
      setOriginal(r.markdown);
    } catch {}
  })(); }, []);

  const dirty = markdown !== original;

  const save = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const r = await api.putIdentity(markdown);
      setMarkdown(r.markdown);
      setOriginal(r.markdown);
      setToast(`Saved · ${r.count} ${r.count === 1 ? "memory" : "memories"}`);
      setTimeout(() => setToast(null), 2400);
    } catch (e: any) {
      setToast(`Save failed: ${e?.message ?? e}`);
      setTimeout(() => setToast(null), 4000);
    } finally {
      setBusy(false);
    }
  };

  const revert = () => setMarkdown(original);

  return (
    <main className="min-h-dvh px-6 py-10">
      <div className="mx-auto max-w-[820px]">
        <div className="mb-6 flex items-center justify-between gap-4">
          <Link href="/" className="text-[12px] uppercase tracking-[0.2em] opacity-70 hover:opacity-100">
            ← back
          </Link>
          {user?.email && (
            <div className="text-[12px]" style={{ color: "var(--color-muted)" }}>
              {user.email}
            </div>
          )}
        </div>

        <p className="mb-2 text-[11px] uppercase tracking-[0.24em]" style={{ color: "var(--color-muted)" }}>
          Identity
        </p>
        <h1 className="h-display text-[40px] leading-[1.05]">Who I am</h1>
        <p className="mt-3 max-w-[640px] text-[14px]" style={{ color: "var(--color-ink-2)" }}>
          The AI uses these notes silently in every reply — to know who it's talking
          to, what you care about, and how you like to work. Add what you want it to
          remember; remove anything wrong. The AI also adds entries automatically as it
          learns about you from chats.
        </p>

        <div className="mt-6 rounded-md border" style={{ borderColor: "var(--color-rule)" }}>
          <textarea
            value={markdown}
            onChange={(e) => setMarkdown(e.target.value)}
            spellCheck={false}
            className="h-[60vh] w-full resize-none bg-[var(--color-paper)] p-4 font-mono text-[13px] leading-[1.55] outline-none"
            placeholder={SAMPLE_PLACEHOLDER}
          />
        </div>

        <div className="mt-4 flex items-center gap-3">
          <button
            onClick={save}
            disabled={!dirty || busy}
            className="border px-4 py-2 text-[13px] disabled:opacity-40"
            style={{ borderColor: "var(--color-ink)",
                     background: dirty ? "var(--color-ink)" : undefined,
                     color: dirty ? "var(--color-paper)" : "var(--color-ink)" }}
          >
            {busy ? "Saving…" : "Save"}
          </button>
          <button
            onClick={revert}
            disabled={!dirty || busy}
            className="border px-3 py-2 text-[13px] disabled:opacity-40"
            style={{ borderColor: "var(--color-rule)", color: "var(--color-muted)" }}
          >
            Revert
          </button>
          <p className="ml-2 text-[12px]" style={{ color: "var(--color-muted)" }}>
            Each <code>- bullet</code> under <code>## Heading</code> becomes a memory.
            Other text is ignored.
          </p>
        </div>

        {toast && (
          <div className="fixed inset-x-0 bottom-6 mx-auto w-fit rounded-md border bg-[var(--color-paper-2)] px-4 py-2 text-[13px]"
               style={{ borderColor: "var(--color-ink)" }}>
            {toast}
          </div>
        )}
      </div>
    </main>
  );
}

const SAMPLE_PLACEHOLDER = `# Who I am

## About me
- I prefer concise answers, no preamble.

## Family
- Wife and two kids; weeknights are tight on time.

## Work
- Self-host a family AI workspace called Atelier.

## Preferences
- Australian English when applicable.
- Tables over prose for comparisons.
`;
