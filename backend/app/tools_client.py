"""Tool definitions exposed to the LLM, plus dispatch to the sandboxed tools sidecar."""
import httpx

from . import config, db


# Tools that need the user_id injected by the backend (not exposed to the LLM).
WORKSPACE_TOOLS = {
    "workspace_list", "workspace_read", "workspace_write", "workspace_edit",
    "workspace_grep", "workspace_glob", "workspace_bash",
    "workspace_git_clone", "workspace_apply_patch", "codebase_search",
}

SCHEDULE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "schedule_create",
            "description": (
                "Create a recurring scheduled prompt. The cron expression fires "
                "in UTC. Each fire creates a NEW conversation titled "
                "'<name> — <date>' with this prompt as the user message; the user "
                "sees the result in their sidebar next time they open the app. "
                "Use this when the user explicitly asks to be reminded, briefed, "
                "or updated on a recurring basis (e.g. 'every Monday morning, "
                "summarise tech news')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Short label, used as the conversation title prefix."},
                    "cron_expr": {
                        "type": "string",
                        "description": (
                            "Standard 5-field cron expression in UTC: "
                            "'minute hour day-of-month month day-of-week'. "
                            "Examples: '0 8 * * *' = 08:00 UTC daily; "
                            "'30 14 * * 1' = 14:30 UTC every Monday; "
                            "'0 9 1 * *' = 09:00 UTC on the 1st of each month."
                        ),
                    },
                    "prompt_text": {"type": "string", "description": "The prompt that will be posted as the user message on each fire."},
                },
                "required": ["name", "cron_expr", "prompt_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_list",
            "description": "Return all scheduled prompts for this user with their cron expressions and last-run info.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_delete",
            "description": "Permanently delete a scheduled prompt by id.",
            "parameters": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_run_now",
            "description": (
                "Fire a scheduled prompt once, immediately, in addition to its "
                "regular cadence. Useful for testing a fresh schedule or for "
                "ad-hoc 'run my morning brief now'. Creates a new conversation "
                "exactly as the cron fire would."
            ),
            "parameters": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
        },
    },
]


TASK_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "task_create",
            "description": (
                "Add a todo item to this conversation's task list. Use at the start of "
                "multi-step work to plan visibly — the user can see what you're about to "
                "do, and you've got a checklist to track. Status starts as 'pending'. "
                "Returns the created task with its id (use that id for task_update / "
                "task_output / task_stop)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string", "description": "Short imperative title (e.g. 'Fetch latest sales numbers')."},
                    "description": {"type": "string", "description": "Optional longer detail."},
                },
                "required": ["subject"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "task_list",
            "description": (
                "Return all tasks in this conversation, in creation order, with their "
                "current status. Useful for re-reading the plan or checking what's left."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "task_get",
            "description": "Fetch one task by id, including its output log.",
            "parameters": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "task_update",
            "description": (
                "Mutate a task. Most-common use: flip status to 'in_progress' when you "
                "start working on it, then 'completed' when done. You can also rename "
                "(subject) or refine (description)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["pending", "in_progress", "completed", "cancelled"],
                    },
                    "subject": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "task_stop",
            "description": (
                "Mark a task cancelled (won't-do). Use when the plan changes mid-stream "
                "and a task is no longer needed. For finished work use task_update with "
                "status='completed' instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "reason": {"type": "string", "description": "Optional brief note about why it was stopped."},
                },
                "required": ["id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "task_output",
            "description": (
                "Append a progress note to a task's output log. Use sparingly — only "
                "when the work produced something the USER might want to read later "
                "(a summary, a file path, a finding). Don't dump intermediate tool "
                "outputs here."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["id", "text"],
            },
        },
    },
]


LIST_SKILLS_TOOL = {
    "type": "function",
    "function": {
        "name": "list_skills",
        "description": (
            "List the user's saved skills (named prompt fragments). Returns each "
            "skill's name, description, and whether it's already attached to this "
            "conversation. Use this when the user asks 'what skills do I have' or "
            "when you want to see if a relevant skill exists before composing one "
            "from scratch. Skills attached to a conversation are auto-injected "
            "into your system prompt on every turn."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


APPLY_SKILL_TOOL = {
    "type": "function",
    "function": {
        "name": "apply_skill",
        "description": (
            "Attach a saved skill to the current conversation by name. The skill's "
            "body becomes part of your system prompt from your NEXT turn onward — "
            "it does NOT take effect inside the current turn. Use this when the "
            "user asks for a skill by name, or when `list_skills` shows a clearly "
            "relevant skill. If the skill is already attached, this is a no-op. "
            "If no skill matches the name, returns an error listing the closest "
            "available names."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Exact or close match for the skill's name (case-insensitive). The skill must already exist — this tool does NOT create skills.",
                },
            },
            "required": ["name"],
        },
    },
}


ASK_USER_QUESTION_TOOL = {
    "type": "function",
    "function": {
        "name": "ask_user_question",
        "description": (
            "Surface a structured pick-list to the user when their request is genuinely ambiguous "
            "and you need their input to proceed. The UI renders the options as clickable buttons; "
            "the user's selection arrives as the next user message in the conversation. Use SPARINGLY — "
            "only when guessing would lead you to do significantly wrong work. Do NOT use for confirmation "
            "('shall I continue?'), trivial choices, or things you can reasonably decide yourself.\n"
            "\n"
            "After calling this tool, your turn ends — you do NOT write a final answer or call other "
            "tools in the same turn. The user's response will continue the conversation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The clarifying question, phrased plainly. ~1 sentence.",
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "2-6 short, distinct options. Each becomes a clickable button.",
                    "minItems": 2,
                    "maxItems": 6,
                },
                "allow_other": {
                    "type": "boolean",
                    "description": "If true (default), the UI also shows a free-text 'Other…' input.",
                    "default": True,
                },
                "multiple": {
                    "type": "boolean",
                    "description": "If true, the UI lets the user pick more than one option. Default false.",
                    "default": False,
                },
            },
            "required": ["question", "options"],
        },
    },
}


DELEGATE_TOOL = {
    "type": "function",
    "function": {
        "name": "delegate",
        "description": (
            "Spin up a focused specialist helper for one sub-task and return its final answer. "
            "Use this when a request has multiple parts that benefit from different specialists "
            "(e.g. vision for an image, a document writer for prose, a researcher for current facts). "
            "You can call delegate MULTIPLE TIMES IN PARALLEL in a single turn — that's the whole "
            "point: each helper picks the LLM best suited for its task_type. After all helpers "
            "return, write the final unified answer (or call generate_pdf/docx/xlsx/pptx) using their outputs.\n"
            "\n"
            "task_type values:\n"
            "  • vision    — image is involved (read screenshots, charts, photos)\n"
            "  • research  — current/web facts; the helper should web_search + web_fetch\n"
            "  • document  — long-form prose, polish, summarisation, narrative structure\n"
            "  • code      — write/debug/explain code\n"
            "  • reasoning — math, multi-step logic, careful step-by-step thinking\n"
            "  • quick     — short factual answer, no special skill needed (default)"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task": {"type": "string",
                         "description": "Self-contained instruction for the helper. Include ALL context — the helper sees no prior conversation."},
                "task_type": {
                    "type": "string",
                    "enum": ["vision", "research", "document", "code", "reasoning", "quick"],
                    "description": "Pick the helper specialty so the right LLM is used.",
                },
                "role": {
                    "type": "string",
                    "enum": ["leaf", "orchestrator"],
                    "description": "Default 'leaf' — the helper is a focused worker that can use normal tools (web_search, workspace_*, etc.) but cannot delegate further. Pick 'orchestrator' ONLY when the sub-task itself decomposes into sub-sub-tasks the helper should farm out (e.g. 'produce a multi-section report with citations' → orchestrator that runs research sub-tasks in parallel). Orchestrator role is depth-capped to prevent runaway fan-out.",
                },
            },
            "required": ["task"],
        },
    },
}


TOOL_DEFINITIONS = [
    # ----- research -----
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for current information. Returns title/url/snippet results. Use for any question that needs up-to-date facts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "default": 5, "minimum": 1, "maximum": 10},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch a URL and return its readable text content. Use after web_search to read a specific result in full, or when the user provides a link.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "max_chars": {"type": "integer", "default": 20000, "minimum": 500, "maximum": 80000},
                },
                "required": ["url"],
            },
        },
    },
    # ----- documents -----
    {
        "type": "function",
        "function": {
            "name": "generate_pdf",
            "description": "Create a PDF document from markdown content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string"},
                    "title": {"type": "string"},
                    "body_markdown": {"type": "string"},
                },
                "required": ["filename", "title", "body_markdown"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_docx",
            "description": (
                "Create a Microsoft Word (.docx) document from markdown content. "
                "Supports headings, paragraphs, bold/italic/code/links, bullet and "
                "numbered lists, code blocks, and tables. Use this when the user "
                "needs an editable Word file (vs generate_pdf for a fixed-layout PDF)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "Filename ending in .docx"},
                    "title": {"type": "string", "description": "Document title (rendered as the heading)"},
                    "body_markdown": {"type": "string", "description": "Markdown body content"},
                },
                "required": ["filename", "title", "body_markdown"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_xlsx",
            "description": (
                "Create an Excel workbook with one or more sheets. "
                "Each sheet has `name` and `rows`. `rows` MUST be a list of ROWS, "
                "and each row MUST be an ARRAY of cell values (strings, numbers, booleans, or null) — "
                "NOT a dictionary/object. The first row is usually the header. "
                "Formulas: pass strings starting with '=' (e.g. '=SUM(B2:B9)'). "
                "Correct: {\"filename\":\"budget.xlsx\",\"sheets\":[{\"name\":\"Budget\","
                "\"rows\":[[\"Category\",\"Amount\"],[\"Food\",5000],[\"Rent\",15000],"
                "[\"Total\",\"=SUM(B2:B3)\"]]}]}. "
                "WRONG (will fail validation): rows like [{\"category\":\"Food\",\"amount\":5000}]."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "Filename ending in .xlsx"},
                    "sheets": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "Sheet/tab name"},
                                "rows": {
                                    "type": "array",
                                    "description": "List of rows. Each row is an array of cell values (NOT an object).",
                                    "items": {
                                        "type": "array",
                                        "description": "One row: array of cell values in column order.",
                                        "items": {},
                                    },
                                },
                            },
                            "required": ["name", "rows"],
                        },
                    },
                },
                "required": ["filename", "sheets"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_flyer",
            "description": "Create a single-page poster / flyer PDF. Use for marketing flyers, event posters, promotional one-pagers — anything where the user wants a visual one-page deliverable. The flyer has a coloured header band with the title, an optional hero image, an accent-bulleted feature list, a CTA pill, and a coloured footer band.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "Filename ending in .pdf"},
                    "title": {"type": "string", "description": "Big headline (printed in the header band)"},
                    "subtitle": {"type": "string", "description": "Tagline shown under the title"},
                    "features": {"type": "array", "items": {"type": "string"},
                                  "description": "Bullet points / features. 4-8 short lines work best."},
                    "cta_text": {"type": "string", "description": "Call-to-action button text e.g. 'Learn More'"},
                    "footer": {"type": "string", "description": "Footer line: contact info, copyright, tagline."},
                    "accent_color": {"type": "string", "description": "Hex like '#E63946' — header band, bullets, CTA."},
                    "background_color": {"type": "string", "description": "Hex page bg, e.g. '#FFFFFF'."},
                    "text_color": {"type": "string", "description": "Hex body text colour, e.g. '#1A1A1A'."},
                    "hero_image_path": {"type": "string",
                                         "description": "Optional absolute path to an image to embed (e.g. the user's uploaded file path under /files/). Will be skipped silently if not found."},
                },
                "required": ["filename", "title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_pptx",
            "description": "Create a PowerPoint presentation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string"},
                    "title": {"type": "string"},
                    "subtitle": {"type": "string"},
                    "slides": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "bullets": {"type": "array", "items": {"type": "string"}},
                                "notes": {"type": "string"},
                            },
                            "required": ["title"],
                        },
                    },
                },
                "required": ["filename", "title", "slides"],
            },
        },
    },
    # ----- workspace (per-user sandboxed scratch) -----
    {
        "type": "function",
        "function": {
            "name": "workspace_list",
            "description": "List files and directories in your private workspace. Use '.' for the root.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "default": "."}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workspace_read",
            "description": "Read the contents of a file in your private workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "max_chars": {"type": "integer", "default": 50000},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workspace_write",
            "description": "Create or overwrite a file in your private workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workspace_edit",
            "description": "Replace exactly one occurrence of a string in a workspace file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old": {"type": "string"},
                    "new": {"type": "string"},
                },
                "required": ["path", "old", "new"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workspace_grep",
            "description": "Search workspace files for a regex pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "default": "."},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workspace_glob",
            "description": "Find workspace files matching a glob pattern (e.g., '**/*.csv').",
            "parameters": {
                "type": "object",
                "properties": {"pattern": {"type": "string"}},
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workspace_bash",
            "description": "Run a bash command in your private workspace. git, node/npm, python3, pip, ripgrep and a C toolchain are available. Use for installing deps, building, and running tests. The workspace is the current directory. Raise `timeout` (up to 300s) for installs/builds/test suites.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "integer", "default": 30, "maximum": 300},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "codebase_search",
            "description": "Search the project for code relevant to a natural-language query (ranked file:line snippets). Use this FIRST to locate where something lives before reading or editing files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What you're looking for, e.g. 'where option parsing happens'."},
                    "max_results": {"type": "integer", "default": 12},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workspace_git_clone",
            "description": "Clone a public https git repository into the project so you can work on it. Only https URLs are allowed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "https git URL, e.g. https://github.com/owner/repo"},
                    "subdir": {"type": "string", "description": "Target subfolder (defaults to the repo name)."},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workspace_apply_patch",
            "description": "Apply a unified diff (git-style, with 'a/' and 'b/' paths) to the project in one shot. Prefer this for multi-file changes instead of many workspace_edit calls.",
            "parameters": {
                "type": "object",
                "properties": {
                    "patch": {"type": "string", "description": "A unified diff. Each file hunk starts with '--- a/path' and '+++ b/path'."},
                },
                "required": ["patch"],
            },
        },
    },
]


# Device / connectivity tools — only exposed in the local desktop build (chat.py
# gates them on config.ATELIER_LOCAL). They provision external CLIs on demand and
# talk to USB hardware on the user's own machine.
DEVICE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "ensure_capability",
            "description": (
                "Make an external tool available on this computer, installing it on demand if missing. "
                "Use this BEFORE a shell command that needs it, then call it via workspace_bash. "
                "Installable: 'adb' (control a USB-connected Android phone), 'arduino-cli' (compile/upload "
                "to Arduino & ESP boards), 'esptool' (flash ESP32/ESP8266), 'mpremote'/'ampy' (MicroPython). "
                "Detect-only system tools: 'ssh','scp','rsync','git','curl','ffmpeg'."),
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "Capability name, e.g. 'adb' or 'arduino-cli'."}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_capabilities",
            "description": "List the device/connectivity tools you can provision and whether each is already installed.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "serial_list",
            "description": "List serial ports — USB-connected ESP32 / Arduino / microcontrollers. Returns each port's device path and a board hint. Use before flashing or opening a serial monitor.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


async def execute_tool(name: str, args: dict, *, user_id: str, conversation_id: str,
                       workspace_path: str | None = None) -> dict:
    """Call the tools sidecar. Inject workspace_path for workspace tools. Register generated files."""
    if name in WORKSPACE_TOOLS:
        if not workspace_path:
            return {"error": "no project folder is set for this chat"}
        args = {**args, "workspace_path": workspace_path}

    # read budget must exceed the sidecar's max bash/clone timeout (300s) so the
    # backend doesn't abandon a long build/test before the sandbox returns.
    timeout = httpx.Timeout(connect=10.0, read=320.0, write=30.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.post(f"{config.TOOLS_URL}/{name}", json=args)
        except httpx.RequestError as e:
            return {"error": f"tool transport error: {e}"}
    if resp.status_code >= 400:
        return {"error": f"tool {name} failed: {resp.status_code} {resp.text[:300]}"}
    try:
        data = resp.json()
    except ValueError:
        return {"error": f"tool {name} returned non-JSON response"}
    if data.get("file"):
        f = data["file"]
        rec = db.add_file(
            user_id=user_id,
            conversation_id=conversation_id,
            filename=f["filename"],
            path=f["path"],
            mime=f["mime"],
            size=f["size"],
        )
        return {
            "ok": True,
            "file_id": rec["id"],
            "filename": rec["filename"],
            "size": rec["size"],
            "download_url": f"/files/{rec['id']}",
            "_frontend_file": rec,
        }
    return data
