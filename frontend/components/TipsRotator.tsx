"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

type Card =
  | { kind: "tip"; text: string }
  | { kind: "news"; title: string; url: string; snippet: string };

let _cache: Card[] | null = null;
let _inflight: Promise<Card[]> | null = null;

async function loadCards(): Promise<Card[]> {
  if (_cache) return _cache;
  if (_inflight) return _inflight;
  _inflight = (async () => {
    try {
      const r = await api.tips();
      const cards: Card[] = [];
      // Interleave: tip, news, tip, news… to mix value with novelty.
      const tips = r.tips.map((t): Card => ({ kind: "tip", text: t }));
      const news = (r.news || []).map((n): Card => ({ kind: "news", title: n.title, url: n.url, snippet: n.snippet }));
      const max = Math.max(tips.length, news.length);
      for (let i = 0; i < max; i++) {
        if (i < tips.length) cards.push(tips[i]);
        if (i < news.length) cards.push(news[i]);
      }
      _cache = cards;
      return cards;
    } finally {
      _inflight = null;
    }
  })();
  return _inflight;
}

function renderInlineEmphasis(text: string) {
  // Convert lightweight **bold** markdown into <strong> nodes — no HTML injection risk.
  const out: React.ReactNode[] = [];
  let i = 0; let key = 0;
  while (i < text.length) {
    const open = text.indexOf("**", i);
    if (open === -1) { out.push(text.slice(i)); break; }
    if (open > i) out.push(text.slice(i, open));
    const close = text.indexOf("**", open + 2);
    if (close === -1) { out.push(text.slice(open)); break; }
    out.push(<strong key={key++}>{text.slice(open + 2, close)}</strong>);
    i = close + 2;
  }
  return out;
}

export function TipsRotator() {
  const [cards, setCards] = useState<Card[]>([]);
  const [idx, setIdx] = useState(0);

  useEffect(() => {
    let cancelled = false;
    loadCards().then(c => { if (!cancelled) setCards(c); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (cards.length === 0) return;
    setIdx(Math.floor(Math.random() * cards.length));
    const t = setInterval(() => setIdx(i => (i + 1) % cards.length), 6000);
    return () => clearInterval(t);
  }, [cards.length]);

  const card = cards[idx];

  return (
    <div className="border-l-2 pl-3" style={{ borderColor: "var(--color-ink)" }}>
      <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.18em]"
           style={{ color: "var(--color-muted)" }}>
        <ThinkingDots />
        <span>{card?.kind === "news" ? "Latest in AI" : "Tip"}</span>
      </div>
      <div className="mt-1.5 max-w-[560px] text-[13.5px] leading-relaxed" style={{ color: "var(--color-ink-2)" }}>
        {!card && <span style={{ color: "var(--color-muted)" }}>thinking…</span>}
        {card?.kind === "tip" && <span>{renderInlineEmphasis(card.text)}</span>}
        {card?.kind === "news" && (
          <a href={card.url} target="_blank" rel="noopener noreferrer"
             className="block underline-offset-4 hover:underline">
            <div className="font-medium">{card.title}</div>
            {card.snippet && (
              <div className="mt-0.5 text-[12.5px]" style={{ color: "var(--color-muted)" }}>{card.snippet}</div>
            )}
          </a>
        )}
      </div>
    </div>
  );
}

function ThinkingDots() {
  return (
    <span aria-hidden className="inline-flex gap-1">
      {[0, 1, 2].map(i => (
        <span
          key={i}
          className="inline-block h-[5px] w-[5px] rounded-full"
          style={{
            background: "var(--color-ink)",
            animation: `tipdot 1.2s ease-in-out ${i * 0.15}s infinite`,
          }}
        />
      ))}
      <style>{`@keyframes tipdot { 0%,100% { opacity: 0.2; transform: translateY(0); } 50% { opacity: 1; transform: translateY(-2px); } }`}</style>
    </span>
  );
}
