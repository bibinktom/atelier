"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, BACKEND } from "@/lib/api";
import { streamSSE } from "@/lib/sse";
import { Sidebar } from "@/components/Sidebar";
import { Composer, SentAttachment } from "@/components/Composer";
import { MessageList, RenderMessage, fromStored } from "@/components/MessageList";
import { ModelPicker } from "@/components/ModelPicker";
import { WorkspacePicker } from "@/components/WorkspacePicker";
import { AssistantTurn, Conversation, FileRec, Model, Skill, Workspace } from "@/lib/types";
import * as fsa from "@/lib/fsa";

export default function Home() {
  const [user, setUser] = useState<any>(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [models, setModels] = useState<Model[]>([]);
  const [defaultModel, setDefaultModel] = useState<string>("");
  // Model to use for the NEXT new conversation (sticky picker selection before a
  // conversation exists). null → backend default.
  const [pendingModel, setPendingModel] = useState<string | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [conv, setConv] = useState<Conversation | null>(null);
  // Project folders + live two-way sync with a folder on the user's computer.
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [pendingWorkspace, setPendingWorkspace] = useState<string | null>(null);
  const [linkedIds, setLinkedIds] = useState<Set<string>>(new Set());
  const [syncing, setSyncing] = useState(false);
  const [syncMsg, setSyncMsg] = useState<string | null>(null);
  const [usage, setUsage] = useState<{ used: number; quota: number; percent: number } | null>(null);
  const refreshUsage = async () => { try { setUsage(await api.workspaceUsage()); } catch {} };
  // Live FileSystemDirectoryHandles by workspace id (granted this session).
  const handlesRef = useRef<Map<string, any>>(new Map());
  const flashSync = (m: string) => { setSyncMsg(m); setTimeout(() => setSyncMsg(s => (s === m ? null : s)), 3500); };
  const [stored, setStored] = useState<any[]>([]);
  const [files, setFiles] = useState<FileRec[]>([]);
  type UserRender = Extract<RenderMessage, { kind: "user" }>;
  const [pending, setPending] = useState<{ user: UserRender; assistant: AssistantTurn } | null>(null);
  const [busy, setBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const currentIdRef = useRef<string | null>(null);
  // When onSend creates a brand-new conversation it sets conv + currentId itself
  // and immediately starts streaming. Without this guard, the currentId-change
  // effect below would reload the (still empty) conversation and setPending(null),
  // wiping the in-flight first message — the classic "first prompt does nothing,
  // works on the second" bug. We record the id to skip exactly one reload.
  const skipLoadRef = useRef<string | null>(null);
  const onSendRef = useRef<((text: string, imageFileIds: string[], attachments: SentAttachment[]) => Promise<void>) | null>(null);
  useEffect(() => { currentIdRef.current = currentId; }, [currentId]);

  useEffect(() => { (async () => {
    try {
      // /auth/me runs first and is the only call non-approved users can make.
      // If pending, skip every other fetch — the PendingScreen renders below.
      const me = await api.me();
      setUser(me);
      setAuthChecked(true);
      if (me?.is_pending) return;
      const [sk, cs, md, ws] = await Promise.all([
        api.listSkills(), api.listConversations(), api.models(), api.listWorkspaces(),
      ]);
      setSkills(sk.skills); setConversations(cs.conversations);
      setModels(md.models); setDefaultModel(md.default);
      setWorkspaces(ws.workspaces);
      // Which workspaces still have a remembered local-folder link in this browser.
      try { setLinkedIds(await fsa.listLinkedWorkspaceIds()); } catch {}
      refreshUsage();

      // Handle ?c=<id>&fire=<text> from /skills "Run" — open the freshly-created
      // conversation, then auto-fire the skill's trigger prompt.
      let initialId: string | null = cs.conversations[0]?.id ?? null;
      let pendingFire: string | null = null;
      if (typeof window !== "undefined") {
        const qs = new URLSearchParams(window.location.search);
        const cParam = qs.get("c");
        const fireParam = qs.get("fire");
        if (cParam) {
          initialId = cParam;
          if (fireParam) pendingFire = fireParam;
          window.history.replaceState({}, "", "/");
        }
      }
      if (initialId) setCurrentId(initialId);
      if (pendingFire) {
        // Defer until conv is loaded so onSend has a current conversation.
        const text = pendingFire;
        setTimeout(() => { onSendRef.current?.(text, [], []); }, 350);
      }
    } catch {}
  })(); }, []);

  useEffect(() => { (async () => {
    if (!currentId) { setConv(null); setStored([]); setFiles([]); setPending(null); return; }
    // Skip exactly one reload for a conversation onSend just created — its data is
    // already set and a stream is in flight; reloading here would clear `pending`.
    if (skipLoadRef.current === currentId) { skipLoadRef.current = null; return; }
    const data = await api.getConversation(currentId);
    setConv(data.conversation);
    setStored(data.messages);
    setFiles(data.files);
    setPending(null);
  })(); }, [currentId]);

  const filesById = useMemo(() => new Map(files.map(f => [f.id, f])), [files]);
  const baseMessages = useMemo(() => fromStored(stored, filesById), [stored, filesById]);
  const messages = useMemo<RenderMessage[]>(() => {
    if (!pending) return baseMessages;
    return [...baseMessages, pending.user, { kind: "assistant", turn: pending.assistant, streaming: true }];
  }, [baseMessages, pending]);

  const onNew = async () => {
    const c = await api.createConversation({
      workspace_id: (conv?.workspace_id ?? pendingWorkspace) ?? undefined,
      model: pendingModel ?? undefined,   // null → backend default
    });
    setConversations(prev => [c, ...prev]);
    setCurrentId(c.id);
  };
  const onSelect = (id: string) => setCurrentId(id);

  // Switch model. For an existing conversation, persist it (applies next message);
  // before any conversation exists, remember it for the next new chat.
  const currentModelId = conv?.model ?? pendingModel ?? defaultModel;
  const onModelChange = async (id: string) => {
    if (currentId && conv) {
      setConv({ ...conv, model: id });
      setConversations(prev => prev.map(c => (c.id === currentId ? { ...c, model: id } : c)));
      try { await api.patchConversation(currentId, { model: id }); } catch {}
    } else {
      setPendingModel(id);
    }
  };
  const onDelete = async (id: string) => {
    await api.deleteConversation(id);
    setConversations(prev => prev.filter(c => c.id !== id));
    if (currentId === id) setCurrentId(null);
  };

  // ---- project folder + live sync ----
  const currentWorkspaceId = conv?.workspace_id ?? pendingWorkspace ?? null;

  const onSelectWorkspace = async (id: string) => {
    if (currentId && conv) {
      setConv({ ...conv, workspace_id: id });
      setConversations(prev => prev.map(c => (c.id === currentId ? { ...c, workspace_id: id } : c)));
      try { await api.patchConversation(currentId, { workspace_id: id }); } catch {}
    } else {
      setPendingWorkspace(id);
    }
  };

  const onCreateWorkspace = async (name: string, path?: string): Promise<Workspace | null> => {
    try {
      const ws = await api.createWorkspace(name, path);
      setWorkspaces(prev => [ws, ...prev]);
      return ws;
    } catch (e: any) { flashSync(e?.message?.slice(0, 120) || "Could not create folder"); return null; }
  };

  // Resolve a usable, permission-granted handle for a workspace. `prompt` may only
  // be true inside a user gesture (link / Sync now); the post-turn auto-pull passes false.
  const resolveHandle = async (wid: string, prompt: boolean): Promise<any | null> => {
    let h = handlesRef.current.get(wid);
    if (!h) {
      try { h = await fsa.getRememberedHandle(wid); } catch { h = null; }
      if (!h) return null;
    }
    const ok = prompt ? await fsa.ensureRWPermission(h) : true;
    if (prompt && !ok) return null;
    handlesRef.current.set(wid, h);
    return h;
  };

  // Pick a folder on the user's computer and start live-syncing it with a project.
  const onLinkLocalFolder = async () => {
    if (!fsa.fsaSupported()) { flashSync("Live folder sync needs Chrome or Edge over https."); return; }
    const handle = await fsa.pickProjectDirectory();
    if (!handle) return;
    if (!(await fsa.ensureRWPermission(handle))) { flashSync("Folder permission was denied."); return; }
    setSyncing(true);
    try {
      // Link to the selected project, or make a new one named after the folder.
      let wid = currentWorkspaceId;
      if (!wid) {
        const ws = await onCreateWorkspace(handle.name || "project");
        if (!ws) { setSyncing(false); return; }
        wid = ws.id;
      }
      flashSync("Uploading your folder…");
      const r = await fsa.pushLocalToServer(wid, handle);
      handlesRef.current.set(wid, handle);
      try { await fsa.rememberHandle(wid, handle); } catch {}
      setLinkedIds(prev => new Set(prev).add(wid!));
      await onSelectWorkspace(wid);
      flashSync(`Linked “${handle.name}” · uploaded ${r.uploaded} file${r.uploaded === 1 ? "" : "s"}`);
    } catch (e: any) {
      flashSync(e?.message?.slice(0, 140) || "Sync failed.");
    } finally { setSyncing(false); }
  };

  const onSyncNow = async (id: string) => {
    const handle = await resolveHandle(id, true);
    if (!handle) { flashSync("Re-link the folder to grant access again."); return; }
    setSyncing(true);
    try {
      const r = await fsa.syncNow(id, handle);
      flashSync(`Synced · ↑${r.uploaded} ↓${r.downloaded}`);
    } catch (e: any) {
      flashSync(e?.message?.slice(0, 140) || "Sync failed.");
    } finally { setSyncing(false); }
  };

  // After an agent turn, mirror its server-side edits back into the linked folder.
  const maybePullAfterTurn = async (wid: string | null | undefined) => {
    if (!wid || !linkedIds.has(wid)) return;
    const handle = await resolveHandle(wid, false);
    if (!handle) return; // permission lapsed (e.g. after reload) — user can Sync now
    setSyncing(true);
    try {
      const r = await fsa.pullServerToLocal(wid, handle);
      if (r.downloaded > 0) flashSync(`Updated ${r.downloaded} file${r.downloaded === 1 ? "" : "s"} in your folder`);
    } catch {} finally { setSyncing(false); }
  };

  // Non-Chromium fallback: copy an uploaded folder (webkitdirectory) into a new project.
  const onImportFolder = async (files: FileList): Promise<Workspace | null> => {
    const first: any = files[0];
    const top = (first?.webkitRelativePath || "").split("/")[0] || "imported";
    const ws = await onCreateWorkspace(top);
    if (!ws) return null;
    for (const f of Array.from(files)) {
      const rel = (f as any).webkitRelativePath || f.name;
      const relInside = rel.split("/").slice(1).join("/") || f.name;
      try { await api.uploadToWorkspace(ws.id, f, relInside); } catch {}
    }
    flashSync(`Imported folder into “${ws.name}”`);
    return ws;
  };
  const onSend = async (text: string, imageFileIds: string[], attachments: SentAttachment[] = []) => {
    let cid: string;
    if (currentId) {
      cid = currentId;
    } else {
      const c = await api.createConversation({
        model: pendingModel ?? undefined,
        workspace_id: pendingWorkspace ?? undefined,
      });
      setConversations(prev => [c, ...prev]);
      // Mark this id so the currentId-change effect doesn't reload + clear pending.
      skipLoadRef.current = c.id;
      cid = c.id; setCurrentId(c.id); setConv(c);
    }
    // Capture cid in a const so async/finally always uses the right one even
    // if the user switches conversations mid-stream.
    const sentCid = cid;

    const turn: AssistantTurn = { id: "pending", text: "", toolCalls: [], files: [], tasks: undefined, done: false };
    const userMsg: UserRender = {
      kind: "user",
      id: "pending",
      text,
      images: attachments.length
        ? attachments.map(a => ({
            file_id: a.file_id,
            filename: a.filename,
            mime: a.mime,
            previewSrc: a.previewUrl,
          }))
        : imageFileIds.map(fid => ({ file_id: fid, filename: "image", mime: "image/*" })),
    };
    setPending({ user: userMsg, assistant: turn });
    setBusy(true);

    // Take a fresh, immutable snapshot every state push so React always sees a
    // new reference at every nesting level.
    const snapshot = (): AssistantTurn => ({
      id: turn.id,
      text: turn.text,
      done: turn.done,
      plan: turn.plan ? { ...turn.plan } : undefined,
      reflect: turn.reflect ? { ...turn.reflect, issues: turn.reflect.issues?.slice() } : undefined,
      toolCalls: turn.toolCalls.map(tc => ({ ...tc })),
      files: turn.files.slice(),
      tasks: turn.tasks ? turn.tasks.map(t => ({ ...t })) : undefined,
      notice: turn.notice,
    });
    const push = () => setPending(p => p && { ...p, assistant: snapshot() });

    abortRef.current = new AbortController();
    let streamError: string | null = null;
    let aborted = false;

    try {
      const stream = streamSSE(`${BACKEND}/conversations/${sentCid}/messages`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        body: JSON.stringify({ content: text, image_file_ids: imageFileIds }),
        signal: abortRef.current.signal,
      });

      for await (const ev of stream) {
        if (ev.event === "start") {
          // Replace the optimistic "pending" id with the canonical conversation id
          // so React keys stay stable across reloads.
          if (ev.data?.conversation_id) turn.id = String(ev.data.conversation_id);
          push();
        } else if (ev.event === "text") {
          turn.text += String(ev.data);
          turn.notice = undefined;   // model resumed → clear any "retrying" status
          push();
        } else if (ev.event === "notice") {
          turn.notice = ev.data && ev.data.message ? String(ev.data.message) : undefined;
          push();
        } else if (ev.event === "plan") {
          turn.plan = {
            text: String(ev.data?.text || ""),
            model: ev.data?.model ? String(ev.data.model) : undefined,
          };
          push();
        } else if (ev.event === "tool_call") {
          turn.toolCalls.push({
            id: ev.data.id, name: ev.data.name, args: ev.data.arguments,
            status: "running", startedAt: Date.now(),
          });
          push();
        } else if (ev.event === "tool_progress") {
          // Backend heartbeat (every ~5s) while tools are running. We don't need
          // the payload — just trigger a re-render so any running ToolChip
          // recomputes its elapsed-seconds display and the SSE keeps Cloudflare
          // from idle-timing out.
          push();
        } else if (ev.event === "tool_result") {
          const tc = turn.toolCalls.find(t => t.id === ev.data.id);
          if (tc) {
            tc.result = ev.data.result;
            tc.status = ev.data.result?.error ? "error" : "done";
            if (ev.data.cached) tc.cached = true;
            if (ev.data.summary) tc.summary = String(ev.data.summary);
          }
          // Task tools embed the full task list snapshot in `result.tasks` —
          // pull it through onto the turn so the TaskListPanel can render live.
          if (ev.data.name?.startsWith("task_") && Array.isArray(ev.data.result?.tasks)) {
            turn.tasks = ev.data.result.tasks;
          }
          push();
        } else if (ev.event === "reflect") {
          if (ev.data?.status === "running") {
            turn.reflect = { status: "running" };
          } else if (ev.data?.status === "done") {
            turn.reflect = ev.data.ok
              ? { status: "ok" }
              : { status: "issues", issues: Array.isArray(ev.data.issues) ? ev.data.issues.map(String) : [] };
          }
          push();
        } else if (ev.event === "delegate_trace") {
          const tc = turn.toolCalls.find(t => t.id === ev.data.id);
          if (tc) {
            tc.subagent = {
              model: ev.data.model,
              task_type: ev.data.task_type,
              trace: Array.isArray(ev.data.trace) ? ev.data.trace : [],
            };
          }
          push();
        } else if (ev.event === "file") {
          turn.files.push(ev.data);
          push();
        } else if (ev.event === "firewall") {
          // AI firewall verdict. On a redaction, swap the rendered answer for the
          // sanitized text the backend persisted (the original streamed in clear).
          const status = ev.data?.status;
          if (status === "blocked" || status === "redacted") {
            turn.firewall = {
              status,
              phase: ev.data?.phase === "output" ? "output" : "input",
              flagged: Array.isArray(ev.data?.flagged) ? ev.data.flagged.map(String) : [],
            };
            if (status === "redacted" && typeof ev.data?.sanitized === "string") {
              turn.text = ev.data.sanitized;
            }
          } else if (status === "alignment_flagged") {
            // Goal-drift / injection-hijack audit. Advisory by default; when the
            // backend blocked, it includes `sanitized` (streamed mode) to swap the
            // shown answer for a safe stub. Buffered mode delivers the stub via a
            // normal text event, so no swap field is sent there.
            turn.firewall = {
              status: "alignment",
              phase: "alignment",
              reason: typeof ev.data?.reason === "string" ? ev.data.reason : undefined,
              severity: ev.data?.severity ? String(ev.data.severity) : undefined,
              blocked: !!ev.data?.blocked,
            };
            if (typeof ev.data?.sanitized === "string") {
              turn.text = ev.data.sanitized;
            }
          } else if (status === "tool_flagged" || status === "code_flagged") {
            // Attach the finding to the relevant tool-call chip (advisory).
            const tc = turn.toolCalls.find(t => t.id === ev.data.id);
            if (tc) {
              tc.firewall = {
                status,
                flagged: Array.isArray(ev.data?.flagged) ? ev.data.flagged.map(String) : undefined,
                treatment: ev.data?.treatment ? String(ev.data.treatment) : undefined,
                issues: Array.isArray(ev.data?.issues) ? ev.data.issues : undefined,
              };
            }
          }
          push();
        } else if (ev.event === "error") {
          const msg = (ev.data && (ev.data.message || ev.data.error)) || (typeof ev.data === "string" ? ev.data : "Stream error");
          streamError = String(msg);
          turn.done = true;
          turn.notice = undefined;
          push();
          break;
        } else if (ev.event === "done") {
          turn.done = true;
          turn.notice = undefined;
          push();
          break;
        }
      }
    } catch (e: any) {
      if (e?.name === "AbortError" || abortRef.current?.signal.aborted) {
        aborted = true;
      } else {
        streamError = e?.message ? String(e.message) : "The stream was interrupted.";
      }
      turn.done = true;
      push();
    } finally {
      setBusy(false);
      abortRef.current = null;
      // Refresh canonical state from the server. If something failed, surface
      // it to the user instead of silently swallowing.
      try {
        const data = await api.getConversation(sentCid);
        // If the user navigated away during the stream, don't clobber the new
        // conversation's state.
        if (currentIdRef.current === sentCid) {
          setConv(data.conversation);
          setFiles(data.files);
          if (streamError) {
            const errMsg: string = streamError;
            // Keep `pending` so the partial stream + inline error stay visible;
            // don't replace `stored` since that would duplicate the user msg.
            setPending(p => p ? {
              user: { ...p.user, error: aborted ? undefined : errMsg },
              assistant: { ...snapshot(), done: true },
            } : null);
          } else {
            setStored(data.messages);
            setPending(null);
          }
        }
        const cs = await api.listConversations(); setConversations(cs.conversations);
        // Mirror any files the agent wrote server-side back into the linked local folder.
        void maybePullAfterTurn(data.conversation?.workspace_id);
        void refreshUsage();   // the agent may have created/cloned files
      } catch {
        if (streamError) {
          setPending(p => p ? {
            user: { ...p.user, error: streamError! },
            assistant: { ...snapshot(), done: true },
          } : null);
        }
      }
    }
  };

  const onStop = () => abortRef.current?.abort();

  // Keep a ref to onSend so the initial-mount effect can fire a queued skill prompt.
  useEffect(() => { onSendRef.current = onSend; });

  // Mobile sidebar toggle. Set data-open on the <aside> via direct DOM (avoids
  // wiring a prop through Sidebar for this one cross-cutting concern).
  const [sidebarOpen, setSidebarOpen] = useState(false);
  useEffect(() => {
    const el = document.querySelector("aside[data-sidebar]");
    if (el) el.setAttribute("data-open", String(sidebarOpen));
  }, [sidebarOpen]);

  // Don't render the chat shell until we've confirmed the user is signed in
  // and approved. Otherwise the page flashes the empty-state for a moment
  // before /auth/me's 401 redirects us to /login.
  if (!authChecked || !user) {
    return <BootSplash />;
  }
  if (user.is_pending) {
    return <PendingScreen user={user} />;
  }

  return (
    <div className="flex">
      <Sidebar
        conversations={conversations}
        currentId={currentId}
        onNew={onNew}
        onSelect={(id) => { setSidebarOpen(false); onSelect(id); }}
        onDelete={onDelete}
        user={user}
      />
      {sidebarOpen && (
        <div
          onClick={() => setSidebarOpen(false)}
          className="fixed inset-0 z-30 bg-black/30 md:hidden"
          aria-hidden
        />
      )}

      <main className="flex h-dvh min-w-0 flex-1 flex-col">
        <header
          className="flex flex-col border-b safe-pt safe-pl safe-pr md:h-14 md:flex-row md:items-center md:justify-between md:gap-2"
          style={{ borderColor: "var(--color-rule)" }}
        >
          {/* Row 1: hamburger + title.  On md+, this row also holds the action
              strip (no second row) — see the action strip below. */}
          <div className="flex h-14 items-center gap-2 px-3 md:flex-1 md:px-6">
          <button
            onClick={() => setSidebarOpen(o => !o)}
            className="md:hidden tap-target grid h-10 w-10 place-items-center rounded-md border transition hover:bg-[var(--color-paper-3)]"
            style={{ borderColor: "var(--color-rule)" }}
            aria-label="Toggle conversations sidebar"
          >
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round">
              <path d="M3 6h18M3 12h18M3 18h18"/>
            </svg>
          </button>
          <div className="min-w-0 flex-1 truncate font-display text-[16px] md:text-[18px] leading-tight"
               style={{ fontVariationSettings: '"opsz" 36' }}>
            {conv?.title ?? "Begin a conversation"}
          </div>
          {conv?.skill_id && skills.find(s => s.id === conv.skill_id) && (
            <div className="mr-3 flex items-center gap-2 rounded-md border px-2.5 py-1 text-[12px]"
                 style={{ borderColor: "var(--color-ink)",
                          background: "var(--color-paper-2)",
                          color: "var(--color-ink)" }}
                 title="Skill steering this chat">
              <span aria-hidden>◇</span>
              <span className="max-w-[160px] truncate">{skills.find(s => s.id === conv.skill_id)?.name}</span>
              <button
                onClick={async () => {
                  if (!conv) return;
                  await api.patchConversation(conv.id, { clear_skill: true });
                  setConv({ ...conv, skill_id: null });
                }}
                aria-label="Detach skill"
                className="opacity-50 transition hover:opacity-100"
              >×</button>
            </div>
          )}
          <div className="ml-auto mr-3 flex shrink-0 items-center gap-2">
            {syncMsg && (
              <span className="hidden max-w-[220px] truncate text-[11.5px] md:inline" style={{ color: "var(--color-muted)" }}>
                {syncMsg}
              </span>
            )}
            <WorkspacePicker
              workspaces={workspaces}
              value={currentWorkspaceId}
              onChange={onSelectWorkspace}
              onCreate={onCreateWorkspace}
              onImportFolder={onImportFolder}
              onLinkLocalFolder={onLinkLocalFolder}
              onSyncNow={onSyncNow}
              linkedIds={linkedIds}
              syncing={syncing}
              fsaAvailable={fsa.fsaSupported()}
              usage={usage}
              local={user?.local}
              localRoot={user?.local_root}
            />
            {models.length > 0 && (
              <ModelPicker models={models} value={currentModelId} onChange={onModelChange} />
            )}
            <a
              href="https://buymeacoffee.com/bibintom"
              target="_blank"
              rel="noopener noreferrer"
              className="flex shrink-0 items-center gap-1.5 rounded-full border px-3 py-1.5 text-[12px] transition hover:bg-[var(--color-paper-3)]"
              style={{ borderColor: "var(--color-brick)", color: "var(--color-brick)" }}
              title="Support Atelier — buy me a coffee"
            >
              <span aria-hidden>☕</span>
              <span className="hidden sm:inline">Buy me a coffee</span>
            </a>
          </div>
          </div>
        </header>

        <div className="flex flex-1 min-h-0">
          <div className="relative flex-1 overflow-auto thin-scroll">
            {messages.length === 0 ? (
              <EmptyState onSuggest={(t) => onSend(t, [], [])} />
            ) : (
              <MessageList
                messages={messages}
                userInitial={user?.name?.[0]?.toUpperCase() ?? "?"}
                onAnswer={(text) => { void onSend(text, [], []); }}
              />
            )}
          </div>
        </div>

        <Composer onSend={onSend} busy={busy} onStop={onStop} />
      </main>
    </div>
  );
}

function BootSplash() {
  // Minimal full-page placeholder shown until /auth/me resolves. No chat
  // structure, no copy that could be mistaken for the empty state — just the
  // paper background, so the brief auth check is invisible visually.
  return (
    <main
      className="min-h-dvh"
      style={{ background: "var(--color-paper)" }}
      aria-hidden
    />
  );
}

function PendingScreen({ user }: { user: { name: string; email: string; picture: string } }) {
  return (
    <main className="relative grid min-h-dvh place-items-center px-6">
      <div className="relative w-full max-w-[480px]">
        <p className="mb-4 text-[11px] uppercase tracking-[0.24em]" style={{ color: "var(--color-muted)" }}>
          Awaiting admin approval
        </p>
        <h1 className="h-display text-[48px] leading-[1.02] tracking-tight">
          Hello{user?.name ? `, ${user.name.split(" ")[0]}` : ""}.
        </h1>
        <p className="mt-5 text-[15px] leading-relaxed" style={{ color: "var(--color-ink-2)" }}>
          You're signed in as <strong>{user?.email}</strong>. The admin still has to admit
          your account before you can use the workspace. You don't need to do anything —
          come back in a bit.
        </p>
        <div className="mt-7 flex gap-3">
          <button
            onClick={async () => { await api.logout(); window.location.href = "/login"; }}
            className="border bg-[var(--color-paper)] px-4 py-2 text-[13px] transition hover:bg-[var(--color-paper-3)]"
            style={{ borderColor: "var(--color-ink)", color: "var(--color-ink)" }}
          >
            Sign out
          </button>
          <button
            onClick={() => window.location.reload()}
            className="border px-4 py-2 text-[13px] transition hover:bg-[var(--color-paper-3)]"
            style={{ borderColor: "var(--color-rule)", color: "var(--color-ink-2)" }}
          >
            Check again
          </button>
        </div>
      </div>
    </main>
  );
}


function EmptyState({ onSuggest }: { onSuggest: (text: string) => void }) {
  const examples = [
    "Brief me on this week's news in 5 bullets, with sources.",
    "Build a household budget tracker for ₹40k/month, 8 categories.",
    "10-slide pitch on starting a backyard vegetable garden.",
    "Write a Python script to rename my photos by EXIF date.",
  ];
  return (
    <div className="mx-auto max-w-[760px] px-6 pb-10 pt-16">
      <p className="mb-2 text-[11px] uppercase tracking-[0.24em]" style={{ color: "var(--color-muted)" }}>
        Today's studio
      </p>
      <h2 className="h-display text-[40px] leading-[1.05]">
        What shall we make<br/>
        together?
      </h2>
      <div className="mt-9 grid gap-3 sm:grid-cols-2">
        {examples.map(t => (
          <button
            key={t}
            onClick={() => onSuggest(t)}
            className="group rounded-lg border bg-[var(--color-paper-2)] p-4 text-left transition hover:-translate-y-0.5 hover:border-[var(--color-ink)]"
            style={{ borderColor: "var(--color-rule)" }}
          >
            <div className="text-[14px] leading-snug">{t}</div>
          </button>
        ))}
      </div>
    </div>
  );
}
