"use client";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";

type Attachment = { file_id: string; filename: string; mime: string; previewUrl: string };

export type SentAttachment = { file_id: string; filename: string; mime: string; previewUrl: string };

export function Composer({
  onSend, busy, onStop,
}: { onSend: (text: string, imageFileIds: string[], attachments: SentAttachment[]) => void; busy: boolean; onStop: () => void }) {
  const [text, setText] = useState("");
  const [attached, setAttached] = useState<Attachment[]>([]);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  // Track every object URL ever created so we can revoke on unmount even if
  // the corresponding attachment was already removed from state.
  const createdUrlsRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    const ta = taRef.current; if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(280, ta.scrollHeight) + "px";
  }, [text]);

  useEffect(() => {
    const set = createdUrlsRef.current;
    return () => {
      for (const u of set) { try { URL.revokeObjectURL(u); } catch {} }
      set.clear();
    };
  }, []);

  const removeAttached = (file_id: string) => {
    setAttached(prev => {
      const gone = prev.find(x => x.file_id === file_id);
      if (gone) {
        try { URL.revokeObjectURL(gone.previewUrl); } catch {}
        createdUrlsRef.current.delete(gone.previewUrl);
      }
      return prev.filter(x => x.file_id !== file_id);
    });
  };

  const submit = () => {
    const t = text.trim();
    if ((!t && attached.length === 0) || busy) return;
    onSend(t, attached.map(a => a.file_id), attached.map(a => ({ ...a })));
    // The user message will keep showing previewUrl until history reloads with
    // file_id-backed src. Revoke shortly after, but keep them in createdUrlsRef
    // so unmount cleanup is a no-op for them.
    const toRevoke = attached.map(a => a.previewUrl);
    setText(""); setAttached([]);
    setTimeout(() => {
      for (const u of toRevoke) {
        try { URL.revokeObjectURL(u); } catch {}
        createdUrlsRef.current.delete(u);
      }
    }, 60_000);
  };

  const onPickFiles = async (files: FileList | null) => {
    if (!files) return;
    // Snapshot the slot count up-front; awaiting inside the loop and reading
    // `attached` would observe stale state.
    const slots = Math.max(0, 4 - attached.length);
    const list = Array.from(files).slice(0, slots);
    if (fileRef.current) fileRef.current.value = "";
    for (const f of list) {
      try {
        const res = await api.uploadImage(f);
        const previewUrl = URL.createObjectURL(f);
        createdUrlsRef.current.add(previewUrl);
        setAttached(prev => {
          if (prev.length >= 4) {
            // Lost the race; drop this one and clean up.
            try { URL.revokeObjectURL(previewUrl); } catch {}
            createdUrlsRef.current.delete(previewUrl);
            return prev;
          }
          return [...prev, {
            file_id: res.file_id, filename: res.filename, mime: res.mime,
            previewUrl,
          }];
        });
      } catch (e) {
        console.error(e);
      }
    }
  };

  return (
    <div className="px-3 pb-3 pt-2 safe-pb safe-pl safe-pr md:px-6 md:pb-6 md:pt-3">
      <div
        className="rounded-xl border shadow-[0_1px_0_rgba(0,0,0,0.04)] focus-within:shadow-[0_2px_0_var(--color-brick)] transition"
        style={{ borderColor: "var(--color-rule)", background: "var(--color-paper-2)" }}
      >
        {attached.length > 0 && (
          <div className="flex flex-wrap gap-2 border-b px-3 pb-2 pt-3" style={{ borderColor: "var(--color-rule-soft)" }}>
            {attached.map(a => (
              <div key={a.file_id} className="group relative">
                <img src={a.previewUrl} alt="" className="h-14 w-14 rounded border object-cover" style={{ borderColor: "var(--color-rule)" }} />
                <button
                  onClick={() => removeAttached(a.file_id)}
                  className="absolute -right-1.5 -top-1.5 grid h-5 w-5 place-items-center rounded-full text-xs"
                  style={{ background: "var(--color-ink)", color: "var(--color-paper)" }}
                  aria-label="Remove"
                >×</button>
              </div>
            ))}
          </div>
        )}

        <textarea
          ref={taRef}
          value={text}
          onChange={e => setText(e.target.value)}
          onKeyDown={e => {
            if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); }
          }}
          placeholder="Ask, draft, calculate, build a deck…"
          rows={1}
          className="w-full resize-none bg-transparent px-4 py-3 text-[15px] leading-relaxed outline-none placeholder:opacity-50"
          style={{ color: "var(--color-ink)" }}
        />

        <div className="flex items-center justify-between gap-2 px-2.5 pb-2.5">
          <div className="flex items-center gap-1">
            <button
              onClick={() => fileRef.current?.click()}
              className="grid h-8 w-8 place-items-center rounded-full transition hover:bg-[var(--color-paper-3)]"
              title="Attach image"
              style={{ color: "var(--color-muted)" }}
            >
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
                <path d="m21 12-7.5 7.5a5 5 0 0 1-7-7L13.5 5a3.5 3.5 0 0 1 5 5L11 17.5a2 2 0 0 1-3-3l6.5-6.5"/>
              </svg>
            </button>
            <input
              ref={fileRef} type="file" hidden multiple accept="image/png,image/jpeg,image/webp,image/gif"
              onChange={e => onPickFiles(e.target.files)}
            />
            <span className="ml-1 text-[11px]" style={{ color: "var(--color-muted)" }}>
              ⏎ to send · ⇧⏎ for newline
            </span>
          </div>

          {busy ? (
            <button
              onClick={onStop}
              className="flex items-center gap-2 rounded-full border px-3.5 py-1.5 text-sm transition hover:bg-[var(--color-paper-3)]"
              style={{ borderColor: "var(--color-rule)" }}
            >
              <span className="h-2 w-2 rounded-sm" style={{ background: "var(--color-brick)" }} />
              <span>Stop</span>
            </button>
          ) : (
            <button
              onClick={submit}
              disabled={!text.trim() && attached.length === 0}
              className="flex items-center gap-2 rounded-full px-3.5 py-1.5 text-sm transition disabled:opacity-40"
              style={{ background: "var(--color-brick)", color: "var(--color-paper)" }}
            >
              Send <span aria-hidden>→</span>
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
