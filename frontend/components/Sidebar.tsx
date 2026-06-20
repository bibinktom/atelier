"use client";
import Link from "next/link";
import { Conversation } from "@/lib/types";
import { useEffect, useMemo, useRef, useState } from "react";
import { TomoseMark } from "./TomoseMark";
import { SearchBar } from "./SearchBar";
import { ThemeToggle } from "./ThemeToggle";
import { api } from "@/lib/api";

function groupByDay(conversations: Conversation[]) {
  const groups: Record<string, Conversation[]> = {};
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const yesterday = new Date(today); yesterday.setDate(yesterday.getDate() - 1);
  const week = new Date(today); week.setDate(week.getDate() - 7);

  for (const c of conversations) {
    const d = new Date(c.updated_at * 1000);
    let bucket: string;
    if (d >= today) bucket = "Today";
    else if (d >= yesterday) bucket = "Yesterday";
    else if (d >= week) bucket = "Earlier this week";
    else bucket = d.toLocaleDateString(undefined, { month: "long", year: "numeric" });
    (groups[bucket] ||= []).push(c);
  }
  return Object.entries(groups);
}

export function Sidebar({
  conversations, currentId, onNew, onSelect, onDelete, user,
}: {
  conversations: Conversation[];
  currentId: string | null;
  onNew: () => void;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  user: { name: string; email: string; picture: string; is_admin?: boolean; local?: boolean } | null;
}) {
  const grouped = useMemo(() => groupByDay(conversations), [conversations]);
  const [adminOpen, setAdminOpen] = useState(false);
  const [pending, setPending] = useState<{ id: string; email: string; name: string; picture: string; created_at: number }[]>([]);
  const [pendingErr, setPendingErr] = useState<string | null>(null);
  const [fwEvents, setFwEvents] = useState<any[]>([]);
  const [fwCounts, setFwCounts] = useState<{ total: number; by_phase: Record<string, number>; by_status: Record<string, number> } | null>(null);
  const [fwUsers, setFwUsers] = useState<{ id: string; email: string; name?: string }[]>([]);
  const [fwPolicies, setFwPolicies] = useState<Record<string, Record<string, number | null>>>({});
  const [fwDefaults, setFwDefaults] = useState<Record<string, boolean>>({});
  const [fwKeys, setFwKeys] = useState<string[]>([]);
  const [fwPolicyUser, setFwPolicyUser] = useState<string | null>(null);
  const [fwSaving, setFwSaving] = useState(false);
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const userMenuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!userMenuOpen) return;
    const onDown = (e: MouseEvent) => {
      if (!userMenuRef.current?.contains(e.target as Node)) setUserMenuOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setUserMenuOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [userMenuOpen]);

  const onSignOut = async () => {
    try { await api.logout(); } catch {}
    window.location.href = "/login";
  };

  // Poll the queue every 30s while the admin is logged in. Cheap (one row per
  // pending user). Skips entirely for non-admin sessions.
  useEffect(() => {
    if (!user?.is_admin || user?.local) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const r = await api.listPending();
        if (!cancelled) { setPending(r.pending); setPendingErr(null); }
      } catch (e: any) {
        if (!cancelled) setPendingErr(String(e?.message || e));
      }
    };
    tick();
    const id = setInterval(tick, 30_000);
    return () => { cancelled = true; clearInterval(id); };
  }, [user?.is_admin]);

  // Load firewall events when the admin modal opens.
  useEffect(() => {
    if (!adminOpen || !user?.is_admin || user?.local) return;
    let cancelled = false;
    (async () => {
      try {
        const r = await api.listFirewallEvents(100);
        if (!cancelled) { setFwEvents(r.events || []); setFwCounts(r.counts || null); }
      } catch { /* non-fatal — pending queue is the primary admin function */ }
      try {
        const p = await api.firewallPolicies();
        if (!cancelled) {
          setFwUsers(p.users || []); setFwPolicies(p.policies || {});
          setFwDefaults(p.defaults || {}); setFwKeys(p.keys || []);
        }
      } catch { /* non-fatal */ }
    })();
    return () => { cancelled = true; };
  }, [adminOpen, user?.is_admin]);

  // Cycle a single policy knob inherit → on → off → inherit, persisting each change.
  const cyclePolicy = async (uid: string, key: string) => {
    const cur = fwPolicies[uid]?.[key];
    const next = cur === null || cur === undefined ? true : (cur ? false : null);
    setFwSaving(true);
    try {
      const r = await api.setFirewallPolicy(uid, { [key]: next });
      setFwPolicies(prev => ({ ...prev, [uid]: r.policy }));
    } catch { /* leave UI as-is on failure */ }
    finally { setFwSaving(false); }
  };

  // Friendly short labels for the policy knobs.
  const POLICY_LABELS: Record<string, string> = {
    fail_open: "fail-open", tool_scan: "tool scan", code_scan: "code scan",
    pii_output: "PII redact", buffer_output: "buffer out",
    alignment_check: "alignment", alignment_block: "align block",
  };

  const onApprove = async (uid: string) => {
    try { await api.approveUser(uid); setPending(p => p.filter(u => u.id !== uid)); } catch {}
  };
  const onDeny = async (uid: string) => {
    try { await api.denyUser(uid); setPending(p => p.filter(u => u.id !== uid)); } catch {}
  };

  return (
    <aside
      data-sidebar
      className="flex h-dvh w-72 max-w-[85vw] flex-col border-r max-md:fixed max-md:left-0 max-md:top-0 max-md:z-40 max-md:shadow-xl max-md:transition-transform max-md:duration-200 max-md:data-[open=false]:-translate-x-full"
      data-open="false"
      style={{ borderColor: "var(--color-rule)", background: "var(--color-paper-2)" }}
    >
      <div className="px-5 pb-4 pt-6 flex items-start justify-between gap-3">
        <Link href="/" className="block">
          <h1 className="h-display text-[24px] leading-none" style={{ color: "var(--color-ink)" }}>
            Atelier
          </h1>
          <p className="mt-1.5 text-[10.5px] uppercase tracking-[0.22em]" style={{ color: "var(--color-muted)" }}>
            {user?.local ? "Personal workspace" : "Family workspace"}
          </p>
        </Link>
        <div className="flex-none mt-0.5">
          <ThemeToggle />
        </div>
      </div>

      <button
        onClick={onNew}
        className="mx-4 mb-3 flex items-center justify-between border px-3.5 py-2.5 text-sm font-medium transition hover:opacity-90"
        style={{ borderColor: "var(--color-ink)", background: "var(--color-ink)", color: "var(--color-paper)" }}
      >
        <span>New chat</span>
        <span aria-hidden>＋</span>
      </button>

      <SearchBar onPick={onSelect} />

      <nav className="flex-1 overflow-auto thin-scroll px-2 pb-3">
        {grouped.length === 0 && (
          <p className="px-3 py-6 text-sm italic" style={{ color: "var(--color-muted)" }}>
            No conversations yet — start one above.
          </p>
        )}
        {grouped.map(([bucket, items]) => (
          <div key={bucket} className="mb-3">
            <div className="rule-diamond px-3 py-2"><span aria-hidden>◆</span><span>{bucket}</span><span aria-hidden>◆</span></div>
            <ul>
              {items.map(c => (
                <li key={c.id} className="group relative">
                  <button
                    onClick={() => onSelect(c.id)}
                    className="block w-full truncate rounded px-3 py-1.5 pr-9 text-left text-[14px] leading-snug transition hover:bg-[var(--color-paper-3)]"
                    style={{
                      background: currentId === c.id ? "var(--color-paper-3)" : undefined,
                      color: currentId === c.id ? "var(--color-ink)" : "var(--color-ink-2)",
                      fontWeight: currentId === c.id ? 500 : 400,
                    }}
                    title={c.title}
                  >
                    {currentId === c.id && (
                      <span aria-hidden className="absolute left-0 top-1/2 h-4 w-[2px] -translate-y-1/2" style={{ background: "var(--color-ink)" }} />
                    )}
                    {c.title}
                  </button>
                  <button
                    onClick={() => onDelete(c.id)}
                    aria-label="Delete chat"
                    className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 opacity-0 transition group-hover:opacity-60 hover:opacity-100"
                    style={{ color: "var(--color-muted)" }}
                  >×</button>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </nav>

      <div className="border-t px-4 py-3" style={{ borderColor: "var(--color-rule)" }}>
        <Link href="/identity" className="mb-1 flex items-center gap-2 rounded px-2 py-1.5 text-sm transition hover:bg-[var(--color-paper-3)]">
          <span aria-hidden style={{ color: "var(--color-ink)" }}>◉</span> Identity
        </Link>
        <Link href="/skills" className="mb-1 flex items-center gap-2 rounded px-2 py-1.5 text-sm transition hover:bg-[var(--color-paper-3)]">
          <span aria-hidden style={{ color: "var(--color-ink)" }}>◇</span> Skills
        </Link>
        <Link href="/files" className="mb-1 flex items-center gap-2 rounded px-2 py-1.5 text-sm transition hover:bg-[var(--color-paper-3)]">
          <span aria-hidden style={{ color: "var(--color-ink)" }}>▤</span> Files
        </Link>
        <Link href="/settings" className="mb-2 flex items-center gap-2 rounded px-2 py-1.5 text-sm transition hover:bg-[var(--color-paper-3)]">
          <span aria-hidden style={{ color: "var(--color-ink)" }}>⚙</span> Settings
        </Link>
        {!user?.local && user?.is_admin && (
          <button
            onClick={() => setAdminOpen(true)}
            className="mb-2 flex w-full items-center justify-between gap-2 rounded px-2 py-1.5 text-sm transition hover:bg-[var(--color-paper-3)]"
            aria-haspopup="dialog"
          >
            <span className="flex items-center gap-2">
              <span aria-hidden style={{ color: "var(--color-ink)" }}>✱</span>
              Admin queue
            </span>
            {pending.length > 0 && (
              <span
                className="grid h-5 min-w-5 place-items-center rounded-full px-1.5 text-[10px] font-medium"
                style={{ background: "var(--color-brick)", color: "var(--color-paper)" }}
                aria-label={`${pending.length} pending`}
              >
                {pending.length}
              </span>
            )}
          </button>
        )}
        {user && (
          <div ref={userMenuRef} className="relative">
            <button
              type="button"
              onClick={() => setUserMenuOpen(o => !o)}
              aria-haspopup="menu"
              aria-expanded={userMenuOpen}
              className="flex w-full items-center gap-3 rounded px-2 py-1.5 text-left transition hover:bg-[var(--color-paper-3)]"
            >
              {user.picture
                ? <img src={user.picture} alt="" className="h-8 w-8 rounded-full" referrerPolicy="no-referrer" />
                : <span className="grid h-8 w-8 place-items-center rounded-full text-sm" style={{ background: "var(--color-ink)", color: "var(--color-paper)" }}>
                    {user.name?.[0]?.toUpperCase() ?? "?"}
                  </span>}
              <div className="min-w-0 flex-1">
                <div className="truncate text-[13px]">{user.name}</div>
                {!user.local && (
                  <div className="truncate text-[11px]" style={{ color: "var(--color-muted)" }}>{user.email}</div>
                )}
              </div>
              <span aria-hidden className="text-[11px]" style={{ color: "var(--color-muted)" }}>
                {userMenuOpen ? "▾" : "▸"}
              </span>
            </button>
            {userMenuOpen && (
              <div
                role="menu"
                className="absolute bottom-full left-0 right-0 mb-2 border bg-[var(--color-paper)] shadow-md"
                style={{ borderColor: "var(--color-ink)" }}
              >
                <button
                  role="menuitem"
                  onClick={onSignOut}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-[13px] transition hover:bg-[var(--color-paper-3)]"
                  style={{ color: "var(--color-brick)" }}
                >
                  <span aria-hidden>↩</span> Sign out
                </button>
              </div>
            )}
          </div>
        )}
        <div className="mt-3 border-t pt-3" style={{ borderColor: "var(--color-rule-soft)" }}>
          <TomoseMark />
        </div>
      </div>

      {adminOpen && !user?.local && user?.is_admin && (
        <div
          className="fixed inset-0 z-50 grid place-items-center bg-black/40 px-4"
          role="dialog" aria-modal="true"
          onClick={(e) => { if (e.target === e.currentTarget) setAdminOpen(false); }}
        >
          <div
            className="w-full max-w-[520px] border bg-[var(--color-paper)] p-5"
            style={{ borderColor: "var(--color-ink)" }}
          >
            <div className="mb-3 flex items-center justify-between">
              <h2 className="h-display text-[22px]">Admin queue</h2>
              <button onClick={() => setAdminOpen(false)} aria-label="Close" className="opacity-60 hover:opacity-100">×</button>
            </div>
            <p className="mb-4 text-[12px]" style={{ color: "var(--color-muted)" }}>
              Anyone with a Google account can sign in. Approve them here to give them access; deny to remove them.
            </p>
            {pendingErr && (
              <div className="mb-3 border px-3 py-2 text-[12px]"
                   style={{ borderColor: "var(--color-ink)", background: "var(--color-paper-3)" }}>
                {pendingErr}
              </div>
            )}
            {pending.length === 0 ? (
              <div className="text-[13px]" style={{ color: "var(--color-ink-2)" }}>
                No one is waiting.
              </div>
            ) : (
              <ul className="space-y-2 max-h-[60vh] overflow-y-auto thin-scroll">
                {pending.map(p => (
                  <li
                    key={p.id}
                    className="flex items-center gap-3 border px-3 py-2"
                    style={{ borderColor: "var(--color-rule)" }}
                  >
                    {p.picture
                      ? <img src={p.picture} alt="" className="h-9 w-9 rounded-full" referrerPolicy="no-referrer" />
                      : <span className="grid h-9 w-9 place-items-center rounded-full text-sm" style={{ background: "var(--color-ink)", color: "var(--color-paper)" }}>
                          {(p.name || p.email)[0]?.toUpperCase() ?? "?"}
                        </span>}
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[14px]">{p.name || p.email}</div>
                      <div className="truncate text-[11px]" style={{ color: "var(--color-muted)" }}>{p.email}</div>
                    </div>
                    <button
                      onClick={() => onApprove(p.id)}
                      className="border px-3 py-1 text-[12px] transition hover:bg-[var(--color-paper-3)]"
                      style={{ borderColor: "var(--color-ink)", color: "var(--color-ink)" }}
                    >
                      Approve
                    </button>
                    <button
                      onClick={() => onDeny(p.id)}
                      className="border px-2 py-1 text-[12px] transition hover:bg-[var(--color-paper-3)]"
                      style={{ borderColor: "var(--color-rule)", color: "var(--color-ink-2)" }}
                      aria-label="Deny"
                    >
                      Deny
                    </button>
                  </li>
                ))}
              </ul>
            )}

            <div className="mt-5 border-t pt-4" style={{ borderColor: "var(--color-rule-soft)" }}>
              <div className="mb-2 flex items-baseline justify-between">
                <h3 className="text-[12px] uppercase tracking-[0.18em]" style={{ color: "var(--color-muted)" }}>
                  Firewall activity
                </h3>
                {fwCounts && (
                  <span className="text-[11px]" style={{ color: "var(--color-ink-2)" }}>
                    {fwCounts.total} total
                    {fwCounts.by_status && Object.keys(fwCounts.by_status).length > 0 && (
                      <> · {Object.entries(fwCounts.by_status).map(([k, v]) => `${v} ${k}`).join(" · ")}</>
                    )}
                  </span>
                )}
              </div>
              {fwEvents.length === 0 ? (
                <div className="text-[12px]" style={{ color: "var(--color-ink-2)" }}>
                  Nothing flagged yet.
                </div>
              ) : (
                <ul className="space-y-1.5 max-h-[34vh] overflow-y-auto thin-scroll">
                  {fwEvents.map(ev => {
                    const tone = ev.status === "blocked" ? "var(--color-brick)"
                      : ev.status === "redacted" ? "var(--color-moss)" : "var(--color-cobalt)";
                    const d = ev.detail || {};
                    const bits = [
                      d.tool, ...(Array.isArray(d.flagged) ? d.flagged : []),
                      ...(Array.isArray(d.issues) ? d.issues : []),
                      d.snippet ? `“${d.snippet}”` : null,
                    ].filter(Boolean).join(" · ");
                    return (
                      <li key={ev.id} className="flex items-start gap-2 border px-2.5 py-1.5 text-[12px]"
                          style={{ borderColor: "var(--color-rule)" }}>
                        <span className="mt-0.5 inline-block h-1.5 w-1.5 flex-none rounded-full" style={{ background: tone }} />
                        <div className="min-w-0 flex-1">
                          <span style={{ color: tone, fontWeight: 500 }}>{ev.phase}/{ev.status}</span>
                          {bits && <span className="ml-1" style={{ color: "var(--color-ink-2)" }}>— {bits}</span>}
                        </div>
                        <span className="flex-none text-[10.5px]" style={{ color: "var(--color-muted)" }}>
                          {new Date((ev.created_at || 0) * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
                        </span>
                      </li>
                    );
                  })}
                </ul>
              )}
              <p className="mt-2 text-[10.5px]" style={{ color: "var(--color-muted)" }}>
                Metadata only — no secret or personal values are stored.
              </p>
            </div>

            <div className="mt-5 border-t pt-4" style={{ borderColor: "var(--color-rule-soft)" }}>
              <div className="mb-2 flex items-baseline justify-between">
                <h3 className="text-[12px] uppercase tracking-[0.18em]" style={{ color: "var(--color-muted)" }}>
                  Firewall policy
                </h3>
                {fwSaving && <span className="text-[10.5px]" style={{ color: "var(--color-ink-2)" }}>saving…</span>}
              </div>
              <p className="mb-2 text-[11px]" style={{ color: "var(--color-ink-2)" }}>
                Per-user overrides. Click a knob to cycle{" "}
                <span style={{ color: "var(--color-muted)" }}>inherit</span> →{" "}
                <span style={{ color: "var(--color-moss)" }}>on</span> →{" "}
                <span style={{ color: "var(--color-brick)" }}>off</span>. Defaults:{" "}
                {fwKeys.map(k => `${POLICY_LABELS[k] ?? k} ${fwDefaults[k] ? "on" : "off"}`).join(" · ")}.
              </p>
              {fwUsers.length === 0 ? (
                <div className="text-[12px]" style={{ color: "var(--color-ink-2)" }}>No users yet.</div>
              ) : (
                <ul className="space-y-2 max-h-[34vh] overflow-y-auto thin-scroll">
                  {fwUsers.map(u => {
                    const pol = fwPolicies[u.id] || {};
                    const isOpen = fwPolicyUser === u.id;
                    const overrides = fwKeys.filter(k => pol[k] !== null && pol[k] !== undefined).length;
                    return (
                      <li key={u.id} className="border px-2.5 py-2" style={{ borderColor: "var(--color-rule)" }}>
                        <button
                          onClick={() => setFwPolicyUser(isOpen ? null : u.id)}
                          className="flex w-full items-center justify-between gap-2 text-left"
                        >
                          <span className="min-w-0 truncate text-[13px]">{u.email}</span>
                          <span className="flex-none text-[10.5px]" style={{ color: overrides ? "var(--color-brick)" : "var(--color-muted)" }}>
                            {overrides ? `${overrides} override${overrides > 1 ? "s" : ""}` : "default"} {isOpen ? "▾" : "▸"}
                          </span>
                        </button>
                        {isOpen && (
                          <div className="mt-2 flex flex-wrap gap-1.5">
                            {fwKeys.map(k => {
                              const v = pol[k];
                              const state = v === null || v === undefined ? "inherit" : (v ? "on" : "off");
                              const fg = state === "on" ? "var(--color-moss)" : state === "off" ? "var(--color-brick)" : "var(--color-muted)";
                              return (
                                <button
                                  key={k}
                                  onClick={() => cyclePolicy(u.id, k)}
                                  className="border px-2 py-1 text-[11px] transition hover:bg-[var(--color-paper-3)]"
                                  style={{ borderColor: fg, color: fg }}
                                  title={`${k}: ${state}${state === "inherit" ? ` (global: ${fwDefaults[k] ? "on" : "off"})` : ""}`}
                                >
                                  {POLICY_LABELS[k] ?? k}: {state}
                                </button>
                              );
                            })}
                          </div>
                        )}
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          </div>
        </div>
      )}
    </aside>
  );
}
