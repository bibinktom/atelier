export const BACKEND =
  process.env.NEXT_PUBLIC_BACKEND_URL?.replace(/\/$/, "") || "http://localhost:8000";

function bounceToLogin(reason?: "unreachable") {
  if (typeof window === "undefined") return;
  if (window.location.pathname.startsWith("/login")) return;
  window.location.href = reason ? `/login?error=${reason}` : "/login";
}

async function jfetch<T = any>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(BACKEND + path, {
      credentials: "include",
      headers: { "Content-Type": "application/json", ...(init.headers || {}) },
      ...init,
    });
  } catch (e) {
    // Network / DNS / CORS failure — backend is unreachable. Send user to /login
    // so they at least see the sign-in screen and a hint instead of an empty page.
    if (path === "/auth/me") bounceToLogin("unreachable");
    throw e;
  }
  if (res.status === 401) {
    // Expected when not signed in — bounce to /login without the "unreachable" warning.
    if (path !== "/auth/logout") bounceToLogin();
    throw new Error("not authenticated");
  }
  if (!res.ok) {
    let body = ""; try { body = await res.text(); } catch {}
    throw new Error(`${res.status}: ${body.slice(0, 200)}`);
  }
  return res.json();
}

export const api = {
  me: () => jfetch("/auth/me"),
  logout: () => jfetch("/auth/logout", { method: "POST" }),
  loginUrl: () => `${BACKEND}/auth/google/login`,

  // OpenRouter OAuth: full-page nav (like loginUrl) → backend bounces to openrouter.ai.
  connectOpenRouterUrl: () => `${BACKEND}/auth/openrouter/connect`,
  disconnectOpenRouter: () =>
    jfetch("/auth/openrouter/disconnect", { method: "POST", body: JSON.stringify({}) }),

  models: () => jfetch<{ models: any[]; default: string }>("/models"),

  listConversations: () => jfetch<{ conversations: any[] }>("/conversations"),
  createConversation: (opts: { model?: string; workspace_id?: string; skill_id?: string } = {}) =>
    jfetch("/conversations", { method: "POST", body: JSON.stringify(opts) }),
  getConversation: (id: string) => jfetch(`/conversations/${id}`),
  patchConversation: (id: string, patch: { title?: string; model?: string; workspace_id?: string; skill_id?: string; clear_skill?: boolean }) =>
    jfetch(`/conversations/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
  deleteConversation: (id: string) =>
    jfetch(`/conversations/${id}`, { method: "DELETE" }),

  listWorkspaces: () => jfetch<{ workspaces: any[] }>("/workspaces"),
  workspaceUsage: () => jfetch<{ used: number; quota: number; percent: number }>("/workspaces/usage"),
  // `path` is honored only by the local desktop build: attach an absolute host
  // folder (confined to the local root) instead of a container scratch dir.
  createWorkspace: (name: string, path?: string) =>
    jfetch("/workspaces", { method: "POST", body: JSON.stringify(path ? { name, path } : { name }) }),
  deleteWorkspace: (id: string) =>
    jfetch(`/workspaces/${id}`, { method: "DELETE" }),

  listWorkspaceFiles: (wid: string, path = "") =>
    jfetch<{ entries: any[]; path: string; type: string }>(
      `/workspaces/${wid}/files?path=${encodeURIComponent(path)}`),
  uploadToWorkspace: async (wid: string, file: File, relPath?: string) => {
    const fd = new FormData();
    fd.append("file", file);
    if (relPath) fd.append("rel_path", relPath);
    const res = await fetch(`${BACKEND}/workspaces/${wid}/upload`, {
      method: "POST", credentials: "include", body: fd,
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },
  // Recursive file manifest for two-way sync with a linked local folder.
  workspaceManifest: (wid: string) =>
    jfetch<{ files: { path: string; size: number; modified_at: number }[]; truncated: boolean }>(
      `/workspaces/${wid}/manifest`),
  // Raw bytes of one workspace file (for pulling server changes into the local folder).
  downloadWorkspaceBlob: async (wid: string, path: string): Promise<Blob> => {
    const res = await fetch(`${BACKEND}/workspaces/${wid}/download?path=${encodeURIComponent(path)}`, {
      credentials: "include",
    });
    if (!res.ok) throw new Error(`${res.status}: ${(await res.text()).slice(0, 120)}`);
    return res.blob();
  },
  workspaceFileUrl: (wid: string, path: string) =>
    `${BACKEND}/workspaces/${wid}/download?path=${encodeURIComponent(path)}`,
  workspaceFilePreviewUrl: (wid: string, path: string) =>
    `${BACKEND}/workspaces/${wid}/download?path=${encodeURIComponent(path)}&inline=1`,
  deleteWorkspaceFile: (wid: string, path: string) =>
    jfetch(`/workspaces/${wid}/files?path=${encodeURIComponent(path)}`, { method: "DELETE" }),

  listSkills: () => jfetch<{ skills: any[] }>("/skills"),
  getSkill: (id: string) => jfetch<any>(`/skills/${id}`),
  createSkill: (s: { name: string; description?: string; prompt_template: string; body_md?: string }) =>
    jfetch("/skills", { method: "POST", body: JSON.stringify(s) }),
  patchSkill: (id: string, patch: any) =>
    jfetch(`/skills/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
  bumpSkill: (id: string) =>
    jfetch(`/skills/${id}/use`, { method: "POST", body: JSON.stringify({}) }),
  deleteSkill: (id: string) =>
    jfetch(`/skills/${id}`, { method: "DELETE" }),
  browseCatalog: (q?: string) =>
    jfetch<{ skills: any[]; count: number; last_refreshed: number | null; refreshing: boolean; enabled: boolean }>(
      `/skills/catalog${q && q.trim() ? `?q=${encodeURIComponent(q.trim())}` : ""}`,
    ),
  installCatalogSkill: (id: string) =>
    jfetch(`/skills/catalog/${id}/install`, { method: "POST", body: JSON.stringify({}) }),
  refreshCatalog: () =>
    jfetch(`/skills/catalog/refresh`, { method: "POST", body: JSON.stringify({}) }),
  uploadSkill: async (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(`${BACKEND}/skills/upload`, {
      method: "POST", credentials: "include", body: fd,
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  listMemories: () => jfetch<{ memories: any[] }>("/memories"),
  deleteMemory: (id: string) =>
    jfetch(`/memories/${id}`, { method: "DELETE" }),

  getIdentity: () => jfetch<{ markdown: string }>("/me/identity"),
  putIdentity: (markdown: string) =>
    jfetch<{ ok: boolean; count: number; markdown: string }>("/me/identity",
      { method: "PUT", body: JSON.stringify({ markdown }) }),

  search: (q: string) =>
    jfetch<{ results: any[]; query: string }>(`/search?q=${encodeURIComponent(q)}`),

  tips: () => jfetch<{ tips: string[]; news: { title: string; url: string; snippet: string }[] }>("/tips"),

  // Admin: pending-user approval queue. Returns 403 unless caller is admin.
  listPending: () => jfetch<{ pending: { id: string; email: string; name: string; picture: string; created_at: number }[]; max: number }>("/auth/admin/pending"),
  approveUser: (uid: string) => jfetch(`/auth/admin/approve/${uid}`, { method: "POST", body: JSON.stringify({}) }),
  denyUser: (uid: string) => jfetch(`/auth/admin/deny/${uid}`, { method: "POST", body: JSON.stringify({}) }),
  // Admin: AI-firewall event log + aggregate counts. 403 unless caller is admin.
  // Local desktop build: resolve a paused permission_request (allow|deny|always).
  submitPermission: (cid: string, requestId: string, decision: "allow" | "deny" | "always") =>
    jfetch(`/conversations/${cid}/permission`, {
      method: "POST", body: JSON.stringify({ request_id: requestId, decision }),
    }),

  listFirewallEvents: (limit = 100) =>
    jfetch<{ events: any[]; counts: { total: number; by_phase: Record<string, number>; by_status: Record<string, number> } }>(
      `/auth/admin/firewall?limit=${limit}`),
  // Admin: per-user firewall policy overrides + global defaults. 403 unless admin.
  firewallPolicies: () =>
    jfetch<{ users: { id: string; email: string; name?: string }[]; policies: Record<string, Record<string, number | null>>; defaults: Record<string, boolean>; keys: string[] }>(
      `/auth/admin/firewall/policies`),
  setFirewallPolicy: (uid: string, patch: Record<string, boolean | null>) =>
    jfetch<{ policy: Record<string, number | null> }>(
      `/auth/admin/firewall/policy/${uid}`, { method: "POST", body: JSON.stringify(patch) }),

  uploadImage: async (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(BACKEND + "/uploads/image", {
      method: "POST", credentials: "include", body: fd,
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  fileUrl: (fileId: string) => `${BACKEND}/files/${fileId}`,
  filePreviewUrl: (fileId: string) => `${BACKEND}/files/${fileId}?inline=1`,
};
