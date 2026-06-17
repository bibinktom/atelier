export type Model = {
  id: string;
  label: string;
  supports_tools: boolean;
  supports_images: boolean;
};

export type Conversation = {
  id: string;
  title: string;
  model: string;
  workspace_id: string | null;
  skill_id: string | null;
  created_at: number;
  updated_at: number;
};

export type Workspace = {
  id: string;
  name: string;
  slug: string;
  created_at: number;
};

export type Memory = {
  id: string;
  kind: "fact" | "preference" | "lesson";
  content: string;
  importance: number;
  created_at: number;
};

export type Skill = {
  id: string;
  name: string;
  description: string | null;
  prompt_template: string;
  body_md: string | null;
  use_count: number;
  is_suggested: number;     // 0 | 1
  created_at: number;
};

export type CatalogSkill = {
  id: string;
  name: string;
  description: string | null;
  repo: string | null;
  repo_url: string | null;
  source_url: string;
  author: string | null;
  stars: number;
  license: string | null;
  install_count: number;
};

export type SearchHit = {
  message_id: string;
  conversation_id: string;
  conversation_title: string;
  role: string;
  created_at: number;
  snippet: string;
};

export type WorkspaceFileEntry = {
  name: string;
  type: "file" | "dir";
  size: number | null;
  modified_at: number;
};

export type FileRec = {
  id: string;
  filename: string;
  mime: string;
  size: number;
  created_at: number;
  conversation_id?: string | null;
};

export type StoredMessage = {
  id: string;
  role: "user" | "assistant" | "tool" | "system";
  content: any;
  created_at: number;
};

export type SubagentTraceEntry =
  | { kind: "text"; hop: number; text: string }
  | { kind: "tool"; hop: number; name: string; args: any; ok: boolean; result_preview: string };

export type SubagentTrace = {
  model?: string;
  task_type?: string;
  trace: SubagentTraceEntry[];
};

export type ToolCallView = {
  id: string;
  name: string;
  args?: any;
  status: "running" | "done" | "error";
  result?: any;
  file?: FileRec;
  cached?: boolean;
  summary?: string;
  subagent?: SubagentTrace;
  // AI firewall finding attached to this tool call (untrusted-content injection or
  // insecure generated code). Advisory — the model was warned, not blocked.
  firewall?: ToolFirewallFinding;
  // Wall-clock ms when the tool_call SSE event arrived; used to render an
  // elapsed counter on running chips. Updated implicitly by tool_progress
  // heartbeats triggering re-renders.
  startedAt?: number;
};

export type ToolFirewallFinding = {
  status: "tool_flagged" | "code_flagged";
  flagged?: string[];
  treatment?: string;
  issues?: { pattern_id?: string; description?: string; severity?: string; line?: number }[];
};

export type PlanView = {
  text: string;
  model?: string;
};

export type ReflectView = {
  status: "running" | "ok" | "issues";
  issues?: string[];
};

// AI firewall verdict surfaced to the UI. "blocked" = malicious input refused
// before the model ran; "redacted" = secrets scrubbed from the answer;
// "alignment" = the agent's actions diverged from the request (goal-drift).
export type FirewallView = {
  status: "blocked" | "redacted" | "alignment";
  phase: "input" | "output" | "alignment";
  flagged?: string[];
  reason?: string;
  severity?: string;
  blocked?: boolean;
};

// Admin dashboard row. `detail` holds only non-sensitive metadata.
export type FirewallEvent = {
  id: string;
  user_id: string | null;
  conversation_id: string | null;
  phase: "input" | "output" | "tool" | "code";
  status: "blocked" | "redacted" | "flagged";
  detail: Record<string, any>;
  created_at: number;
};

export type TaskItem = {
  id: string;
  subject: string;
  description?: string | null;
  status: "pending" | "in_progress" | "completed" | "cancelled";
  output?: string | null;
  created_at: number;
  updated_at: number;
};

export type AssistantTurn = {
  id: string;
  text: string;
  plan?: PlanView;
  reflect?: ReflectView;
  firewall?: FirewallView;
  toolCalls: ToolCallView[];
  files: FileRec[];
  tasks?: TaskItem[];
  // Transient status (e.g. "rate-limited, retrying…") shown while streaming.
  notice?: string;
  done: boolean;
};
