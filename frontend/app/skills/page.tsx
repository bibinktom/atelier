"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { Skill, CatalogSkill } from "@/lib/types";
import { TomoseMark } from "@/components/TomoseMark";

const SAMPLE_SKILL_MD = `---
name: Weekly meal plan
description: Plan 7 dinners for a family of 4, avoiding repeats and matching what's in the fridge.
---

You are a calm, efficient family meal planner.

When invoked, ask the user (in one short message) for:
1. Anything in the fridge / pantry that needs using up.
2. Any dietary constraints or members who'll be away that week.

Then produce:
- A 7-day dinner table (Mon–Sun) with a one-line description each.
- A grouped grocery list (produce, protein, pantry, dairy, other).
- Total approx. cook time per day.

Keep prose minimal. Prefer the table + list. No emojis.
`;

export default function SkillsPage() {
  const [user, setUser] = useState<any>(null);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<Skill | null>(null);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  // Discover (GitHub catalog, refreshed daily)
  const [catalog, setCatalog] = useState<CatalogSkill[]>([]);
  const [catalogQuery, setCatalogQuery] = useState("");
  const [catalogMeta, setCatalogMeta] = useState<{ count: number; last_refreshed: number | null; enabled: boolean } | null>(null);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [installing, setInstalling] = useState<string | null>(null);

  const refresh = async () => {
    try { const sk = await api.listSkills(); setSkills(sk.skills); } catch {}
  };

  const loadCatalog = async (q = "") => {
    setCatalogLoading(true);
    try {
      const res = await api.browseCatalog(q);
      setCatalog(res.skills);
      setCatalogMeta({ count: res.count, last_refreshed: res.last_refreshed, enabled: res.enabled });
    } catch {} finally { setCatalogLoading(false); }
  };

  useEffect(() => { (async () => {
    try { const me = await api.me(); setUser(me); } catch {}
    refresh();
    loadCatalog();
  })(); }, []);

  const flash = (m: string) => { setToast(m); setTimeout(() => setToast(null), 2200); };

  const onUpload = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setBusy(true);
    let ok = 0, fail = 0;
    for (const f of Array.from(files)) {
      try { await api.uploadSkill(f); ok++; } catch { fail++; }
    }
    setBusy(false);
    await refresh();
    flash(`Imported ${ok} skill${ok === 1 ? "" : "s"}${fail ? ` · ${fail} failed` : ""}`);
    if (fileRef.current) fileRef.current.value = "";
  };

  const onRun = async (s: Skill) => {
    setBusy(true);
    try {
      const c = await api.createConversation({ skill_id: s.id });
      await api.bumpSkill(s.id);
      window.location.href = `/?c=${c.id}&fire=${encodeURIComponent(s.prompt_template)}`;
    } finally { setBusy(false); }
  };

  const accepted = skills.filter(s => !s.is_suggested);
  const suggested = skills.filter(s => s.is_suggested);
  const installedNames = new Set(accepted.map(s => s.name.trim().toLowerCase()));

  const onInstall = async (c: CatalogSkill) => {
    setInstalling(c.id);
    try {
      await api.installCatalogSkill(c.id);
      await refresh();
      setCatalog(prev => prev.map(x => x.id === c.id ? { ...x, install_count: x.install_count + 1 } : x));
      flash(`Installed “${c.name}”`);
    } catch (e: any) {
      flash(e?.message?.replace(/^\d+\s*/, "") || "Install failed");
    } finally { setInstalling(null); }
  };

  const fmtAge = (sec: number | null) => {
    if (!sec) return "not yet refreshed";
    const h = Math.floor((Date.now() / 1000 - sec) / 3600);
    if (h < 1) return "updated just now";
    if (h < 24) return `updated ${h}h ago`;
    return `updated ${Math.floor(h / 24)}d ago`;
  };

  return (
    <div className="min-h-dvh" style={{ background: "var(--color-paper)" }}>
      <header className="flex h-14 items-center justify-between border-b px-6"
              style={{ borderColor: "var(--color-rule)" }}>
        <Link href="/" className="font-display text-[20px]" style={{ color: "var(--color-ink)" }}>
          Atelier
        </Link>
        <Link href="/" className="text-sm" style={{ color: "var(--color-muted)" }}>
          ← Back to chat
        </Link>
      </header>

      <main className="mx-auto max-w-4xl px-6 py-10">
        <div className="mb-2 text-[11px] uppercase tracking-[0.22em]" style={{ color: "var(--color-muted)" }}>
          Library
        </div>
        <h1 className="h-display text-[40px] leading-tight">Skills</h1>
        <p className="mt-2 max-w-2xl text-[14px]" style={{ color: "var(--color-muted)" }}>
          Reusable instructions in the Claude SKILL.md style. Each skill carries a name,
          a one-line description, and a markdown body that steers the model whenever the
          skill is attached to a chat. Run a skill to start a new conversation with its
          instructions baked into the system prompt.
        </p>

        <div className="mt-6 flex flex-wrap gap-2">
          <button
            onClick={() => { setEditing(null); setCreating(true); }}
            className="border px-3.5 py-2 text-[13px]"
            style={{ borderColor: "var(--color-ink)", background: "var(--color-ink)", color: "var(--color-paper)" }}
          >＋ New skill</button>
          <button
            onClick={() => fileRef.current?.click()}
            disabled={busy}
            className="border px-3.5 py-2 text-[13px] disabled:opacity-50"
            style={{ borderColor: "var(--color-ink)", color: "var(--color-ink)" }}
          >↥ Upload SKILL.md</button>
          <input
            ref={fileRef} type="file" accept=".md,.markdown,text/markdown,text/plain" multiple
            className="hidden" onChange={e => onUpload(e.target.files)}
          />
          {toast && (
            <span className="self-center text-[12.5px]" style={{ color: "var(--color-muted)" }}>{toast}</span>
          )}
        </div>

        {(creating || editing) && (
          <SkillForm
            initial={editing}
            onCancel={() => { setCreating(false); setEditing(null); }}
            onSaved={async () => { setCreating(false); setEditing(null); await refresh(); }}
          />
        )}

        <section className="mt-10">
          <h2 className="mb-3 text-[12px] uppercase tracking-[0.2em]" style={{ color: "var(--color-muted)" }}>
            Your skills · {accepted.length}
          </h2>
          {accepted.length === 0 ? (
            <p className="rounded-md border border-dashed px-4 py-6 text-[13.5px]"
               style={{ borderColor: "var(--color-rule)", color: "var(--color-muted)" }}>
              You don&rsquo;t have any saved skills yet. Click <em>New skill</em>, paste the example below, or
              upload a <code className="font-mono text-[12px]">.md</code> file.
            </p>
          ) : (
            <ul className="grid gap-3 sm:grid-cols-2">
              {accepted.map(s => (
                <SkillRow
                  key={s.id} s={s}
                  onRun={() => onRun(s)}
                  onEdit={() => { setCreating(false); setEditing(s); }}
                  onDelete={async () => {
                    if (!confirm(`Delete skill "${s.name}"?`)) return;
                    await api.deleteSkill(s.id); refresh();
                  }}
                />
              ))}
            </ul>
          )}
        </section>

        {(catalogMeta?.enabled ?? true) && (
          <section className="mt-12">
            <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="text-[12px] uppercase tracking-[0.2em]" style={{ color: "var(--color-muted)" }}>
                Discover · from GitHub
                {catalogMeta && <span className="ml-2 normal-case tracking-normal opacity-70">({catalogMeta.count})</span>}
              </h2>
              <span className="text-[11px]" style={{ color: "var(--color-muted)" }}>
                Refreshed daily · {fmtAge(catalogMeta?.last_refreshed ?? null)}
              </span>
            </div>
            <p className="mb-3 max-w-2xl text-[13px]" style={{ color: "var(--color-muted)" }}>
              A curated catalog of the best public SKILL.md files, sorted by GitHub stars and
              refreshed automatically each day. Click <em>Install</em> to copy one into your library.
            </p>

            <form
              onSubmit={e => { e.preventDefault(); loadCatalog(catalogQuery); }}
              className="mb-4 flex gap-2"
            >
              <input
                value={catalogQuery}
                onChange={e => setCatalogQuery(e.target.value)}
                placeholder="Search the catalog… (e.g. pdf, research, slides)"
                className="w-full max-w-md border bg-[var(--color-paper)] px-2.5 py-1.5 text-[13.5px] outline-none"
                style={{ borderColor: "var(--color-rule)" }}
              />
              <button type="submit" disabled={catalogLoading}
                      className="border px-3 py-1.5 text-[12.5px] disabled:opacity-50"
                      style={{ borderColor: "var(--color-ink)", color: "var(--color-ink)" }}>
                {catalogLoading ? "…" : "Search"}
              </button>
            </form>

            {catalog.length === 0 ? (
              <p className="rounded-md border border-dashed px-4 py-6 text-[13.5px]"
                 style={{ borderColor: "var(--color-rule)", color: "var(--color-muted)" }}>
                {catalogLoading
                  ? "Loading catalog…"
                  : catalogMeta?.last_refreshed
                    ? "No skills matched. Try a different search."
                    : "The catalog hasn’t been populated yet — the daily refresh runs shortly after launch. Check back soon."}
              </p>
            ) : (
              <ul className="grid gap-3 sm:grid-cols-2">
                {catalog.map(c => {
                  const owned = installedNames.has(c.name.trim().toLowerCase());
                  return (
                    <li key={c.id} className="rounded-md border bg-[var(--color-paper-2)] p-4 transition hover:border-[var(--color-ink)]"
                        style={{ borderColor: "var(--color-rule)" }}>
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0 flex-1">
                          <div className="mt-0.5 truncate text-[15.5px] font-medium">{c.name}</div>
                          {c.description && (
                            <div className="mt-1 line-clamp-2 text-[12.5px]" style={{ color: "var(--color-muted)" }}>
                              {c.description}
                            </div>
                          )}
                        </div>
                        <div className="shrink-0 text-[11px] tabular-nums" style={{ color: "var(--color-muted)" }}>
                          ★ {c.stars.toLocaleString()}
                        </div>
                      </div>
                      <div className="mt-2 flex items-center gap-2 text-[11px]" style={{ color: "var(--color-muted)" }}>
                        {c.repo_url
                          ? <a href={c.repo_url} target="_blank" rel="noreferrer" className="truncate underline-offset-2 hover:underline">{c.repo}</a>
                          : <span className="truncate">{c.author}</span>}
                        {c.license && <span className="opacity-70">· {c.license}</span>}
                        {c.install_count > 0 && <span className="opacity-70">· {c.install_count} installs</span>}
                      </div>
                      <div className="mt-3 flex flex-wrap gap-2">
                        <button
                          onClick={() => onInstall(c)}
                          disabled={owned || installing === c.id}
                          className="border px-2.5 py-1 text-[12px] disabled:opacity-50"
                          style={owned
                            ? { borderColor: "var(--color-rule)", color: "var(--color-muted)" }
                            : { borderColor: "var(--color-ink)", background: "var(--color-ink)", color: "var(--color-paper)" }}>
                          {owned ? "Installed ✓" : installing === c.id ? "Installing…" : "↧ Install"}
                        </button>
                        {c.source_url && (
                          <a href={c.source_url} target="_blank" rel="noreferrer"
                             className="border px-2.5 py-1 text-[12px]"
                             style={{ borderColor: "var(--color-rule)", color: "var(--color-ink)" }}>
                            View source
                          </a>
                        )}
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
        )}

        {suggested.length > 0 && (
          <section className="mt-10">
            <h2 className="mb-3 text-[12px] uppercase tracking-[0.2em]" style={{ color: "var(--color-muted)" }}>
              Suggested by Atelier · {suggested.length}
            </h2>
            <ul className="grid gap-3 sm:grid-cols-2">
              {suggested.map(s => (
                <li key={s.id} className="rounded-md border bg-[var(--color-paper-2)] p-4"
                    style={{ borderColor: "var(--color-rule)" }}>
                  <div className="text-[15px] font-medium">{s.name}</div>
                  {s.description && (
                    <div className="mt-1 text-[12.5px]" style={{ color: "var(--color-muted)" }}>{s.description}</div>
                  )}
                  <div className="mt-3 flex gap-2">
                    <button
                      onClick={async () => { await api.patchSkill(s.id, { accept: true }); refresh(); }}
                      className="border px-2.5 py-1 text-[12px]"
                      style={{ borderColor: "var(--color-ink)", background: "var(--color-ink)", color: "var(--color-paper)" }}
                    >Save</button>
                    <button
                      onClick={async () => { await api.deleteSkill(s.id); refresh(); }}
                      className="border px-2.5 py-1 text-[12px]"
                      style={{ borderColor: "var(--color-rule)", color: "var(--color-muted)" }}
                    >Dismiss</button>
                  </div>
                </li>
              ))}
            </ul>
          </section>
        )}

        <section className="mt-12">
          <h2 className="mb-3 text-[12px] uppercase tracking-[0.2em]" style={{ color: "var(--color-muted)" }}>
            Example SKILL.md
          </h2>
          <pre className="overflow-auto rounded-md border bg-[var(--color-paper-2)] p-4 text-[12px] leading-snug"
               style={{ borderColor: "var(--color-rule)" }}>{SAMPLE_SKILL_MD}</pre>
        </section>

        <div className="mt-12 border-t pt-6" style={{ borderColor: "var(--color-rule-soft)" }}>
          <TomoseMark />
        </div>
      </main>
    </div>
  );
}

function SkillRow({ s, onRun, onEdit, onDelete }: {
  s: Skill; onRun: () => void; onEdit: () => void; onDelete: () => void;
}) {
  return (
    <li className="rounded-md border bg-[var(--color-paper-2)] p-4 transition hover:border-[var(--color-ink)]"
        style={{ borderColor: "var(--color-rule)" }}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="text-[10.5px] uppercase tracking-[0.2em]" style={{ color: "var(--color-muted)" }}>
            Skill {s.body_md ? "· instructions" : ""}
          </div>
          <div className="mt-0.5 truncate text-[15.5px] font-medium">{s.name}</div>
          {s.description && (
            <div className="mt-1 line-clamp-2 text-[12.5px]" style={{ color: "var(--color-muted)" }}>
              {s.description}
            </div>
          )}
          <div className="mt-2 text-[11px]" style={{ color: "var(--color-muted)" }}>
            {s.use_count > 0 ? `Used ${s.use_count} time${s.use_count === 1 ? "" : "s"}` : "Never used"}
          </div>
        </div>
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <button onClick={onRun}
                className="border px-2.5 py-1 text-[12px]"
                style={{ borderColor: "var(--color-ink)", background: "var(--color-ink)", color: "var(--color-paper)" }}>
          Run
        </button>
        <button onClick={onEdit}
                className="border px-2.5 py-1 text-[12px]"
                style={{ borderColor: "var(--color-rule)", color: "var(--color-ink)" }}>
          Edit
        </button>
        <button onClick={onDelete}
                className="border px-2.5 py-1 text-[12px]"
                style={{ borderColor: "var(--color-rule)", color: "var(--color-muted)" }}>
          Delete
        </button>
      </div>
    </li>
  );
}

function SkillForm({ initial, onCancel, onSaved }: {
  initial: Skill | null;
  onCancel: () => void;
  onSaved: () => void | Promise<void>;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [promptTemplate, setPromptTemplate] = useState(initial?.prompt_template ?? "");
  const [bodyMd, setBodyMd] = useState(initial?.body_md ?? "");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const save = async () => {
    if (!name.trim() || !promptTemplate.trim()) {
      setErr("Name and trigger prompt are required."); return;
    }
    setSaving(true); setErr(null);
    try {
      if (initial) {
        await api.patchSkill(initial.id, {
          name: name.trim(),
          description: description.trim(),
          prompt_template: promptTemplate.trim(),
          body_md: bodyMd.trim() || undefined,
        });
      } else {
        await api.createSkill({
          name: name.trim(),
          description: description.trim() || undefined,
          prompt_template: promptTemplate.trim(),
          body_md: bodyMd.trim() || undefined,
        });
      }
      await onSaved();
    } catch (e: any) {
      setErr(e?.message || "Save failed.");
    } finally { setSaving(false); }
  };

  return (
    <div className="mt-6 rounded-md border p-5"
         style={{ borderColor: "var(--color-ink)", background: "var(--color-paper-2)" }}>
      <div className="text-[11px] uppercase tracking-[0.2em]" style={{ color: "var(--color-muted)" }}>
        {initial ? "Edit skill" : "New skill"}
      </div>
      <label className="mt-3 block text-[12.5px]" style={{ color: "var(--color-muted)" }}>Name</label>
      <input
        autoFocus value={name} onChange={e => setName(e.target.value)}
        placeholder="e.g. Weekly meal plan"
        className="mt-1 w-full border bg-[var(--color-paper)] px-2 py-1.5 text-[14px] outline-none"
        style={{ borderColor: "var(--color-rule)" }}
      />
      <label className="mt-3 block text-[12.5px]" style={{ color: "var(--color-muted)" }}>
        Description <span className="opacity-60">(one line — used to decide when this skill is relevant)</span>
      </label>
      <input
        value={description ?? ""} onChange={e => setDescription(e.target.value)}
        placeholder="Plan 7 dinners for a family of 4, avoiding repeats…"
        className="mt-1 w-full border bg-[var(--color-paper)] px-2 py-1.5 text-[14px] outline-none"
        style={{ borderColor: "var(--color-rule)" }}
      />
      <label className="mt-3 block text-[12.5px]" style={{ color: "var(--color-muted)" }}>
        Trigger prompt <span className="opacity-60">(the user message fired when you click <em>Run</em>)</span>
      </label>
      <textarea
        value={promptTemplate} onChange={e => setPromptTemplate(e.target.value)}
        placeholder="Plan our family dinners for next week."
        rows={3}
        className="mt-1 w-full resize-y border bg-[var(--color-paper)] px-2 py-1.5 text-[13px] outline-none"
        style={{ borderColor: "var(--color-rule)" }}
      />
      <label className="mt-3 block text-[12.5px]" style={{ color: "var(--color-muted)" }}>
        Instructions (markdown) <span className="opacity-60">— injected as system context for every turn while attached</span>
      </label>
      <textarea
        value={bodyMd ?? ""} onChange={e => setBodyMd(e.target.value)}
        placeholder={"You are a calm, efficient family meal planner.\n\nWhen invoked, ask the user…"}
        rows={10}
        className="mt-1 w-full resize-y border bg-[var(--color-paper)] px-2 py-1.5 font-mono text-[12.5px] outline-none"
        style={{ borderColor: "var(--color-rule)" }}
      />
      {err && <div className="mt-2 text-[12.5px]" style={{ color: "#b34" }}>{err}</div>}
      <div className="mt-4 flex gap-2">
        <button onClick={save} disabled={saving}
                className="border px-3 py-1.5 text-[13px] disabled:opacity-50"
                style={{ borderColor: "var(--color-ink)", background: "var(--color-ink)", color: "var(--color-paper)" }}>
          {saving ? "Saving…" : initial ? "Save changes" : "Save skill"}
        </button>
        <button onClick={onCancel} disabled={saving}
                className="border px-3 py-1.5 text-[13px]"
                style={{ borderColor: "var(--color-rule)", color: "var(--color-muted)" }}>
          Cancel
        </button>
      </div>
    </div>
  );
}
