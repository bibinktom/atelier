"use client";
import { Fragment, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { SearchHit } from "@/lib/types";

const HL_OPEN = "⟦";
const HL_CLOSE = "⟧";

function HighlightedSnippet({ text }: { text: string }) {
  // Split on the delimiters our FTS snippet emits, render <mark> for each match.
  const parts: { text: string; hl: boolean }[] = [];
  let i = 0;
  while (i < text.length) {
    const open = text.indexOf(HL_OPEN, i);
    if (open === -1) {
      parts.push({ text: text.slice(i), hl: false });
      break;
    }
    if (open > i) parts.push({ text: text.slice(i, open), hl: false });
    const close = text.indexOf(HL_CLOSE, open + 1);
    if (close === -1) {
      parts.push({ text: text.slice(open + 1), hl: true });
      break;
    }
    parts.push({ text: text.slice(open + 1, close), hl: true });
    i = close + 1;
  }
  return (
    <>
      {parts.map((p, idx) =>
        p.hl ? (
          <mark key={idx} style={{ background: "var(--color-ink)", color: "var(--color-paper)", padding: "0 2px" }}>{p.text}</mark>
        ) : (
          <Fragment key={idx}>{p.text}</Fragment>
        )
      )}
    </>
  );
}

export function SearchBar({ onPick }: { onPick: (cid: string) => void }) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [busy, setBusy] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (!q.trim()) { setHits([]); return; }
    setBusy(true);
    debounceRef.current = setTimeout(async () => {
      try {
        const res = await api.search(q.trim());
        setHits(res.results);
      } catch { setHits([]); }
      finally { setBusy(false); }
    }, 220);
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [q]);

  return (
    <div className="px-4 pb-2">
      <div className="relative">
        <input
          value={q}
          onChange={e => setQ(e.target.value)}
          placeholder="Search past chats…"
          className="w-full border bg-[var(--color-paper)] px-3 py-1.5 pr-7 text-[13px] outline-none focus:border-[var(--color-ink)]"
          style={{ borderColor: "var(--color-rule)" }}
        />
        {q && (
          <button
            onClick={() => setQ("")}
            className="absolute right-1.5 top-1/2 -translate-y-1/2 px-1 text-xs"
            style={{ color: "var(--color-muted)" }}
            aria-label="Clear"
          >×</button>
        )}
      </div>
      {q && (
        <div className="mt-2 max-h-72 overflow-auto thin-scroll">
          {busy && hits.length === 0 && (
            <p className="px-1 py-2 text-[12px]" style={{ color: "var(--color-muted)" }}>Searching…</p>
          )}
          {!busy && hits.length === 0 && (
            <p className="px-1 py-2 text-[12px]" style={{ color: "var(--color-muted)" }}>No matches.</p>
          )}
          <ul>
            {hits.map(h => (
              <li key={h.message_id}>
                <button
                  onClick={() => { onPick(h.conversation_id); setQ(""); }}
                  className="block w-full px-2 py-2 text-left transition hover:bg-[var(--color-paper-3)]"
                >
                  <div className="truncate text-[12.5px] font-medium">{h.conversation_title}</div>
                  <div className="mt-0.5 line-clamp-2 text-[12px]" style={{ color: "var(--color-muted)" }}>
                    <HighlightedSnippet text={h.snippet} />
                  </div>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
