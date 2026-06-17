#!/usr/bin/env python3
"""Multi-provider tool-calling reliability probe.

Tool-calling reliability is model-capability × provider-serving correctness, and
provider metadata lies (a model can advertise `tools` and still emit the call as
plain text — see meta/llama-3.3-70b on NIM, 2026-06-10). This script does a REAL
probe: it sends a 2-city parallel weather request with a tool definition to each
(provider, model) pair you list and checks whether the response is a valid
OpenAI-format `tool_calls` array with parseable args — mirroring config.py's
"re-probe before trusting" discipline.

All target providers are OpenAI-compatible, so one client probes them all; only
the base_url + key + model id change.

Usage
-----
  # keys come from env (never hard-code):
  export OPENROUTER_API_KEY=...   GROQ_API_KEY=...   GEMINI_API_KEY=...
  export CEREBRAS_API_KEY=...     SAMBANOVA_API_KEY=... NVIDIA_API_KEY=...
  python3 tools/probe_toolcalling.py                  # probes every provider with a key set
  python3 tools/probe_toolcalling.py openrouter groq  # only the named providers

Exit code is non-zero if any probed model FAILs, so it can gate a deploy.
"""
import json
import os
import re
import sys
import time
import urllib.request

# (base_url, env var holding the key, [candidate model ids]).
# base_url is the OpenAI-compatible /chat/completions root (no trailing slash).
PROVIDERS: dict[str, dict] = {
    "openrouter": {
        "base": "https://openrouter.ai/api/v1",
        "key_env": "OPENROUTER_API_KEY",
        "models": [
            "qwen/qwen3-next-80b-a3b-instruct:free",
            "openai/gpt-oss-120b:free",
            "openai/gpt-oss-20b:free",
            "meta-llama/llama-3.3-70b-instruct:free",
            "google/gemma-4-31b-it:free",
        ],
    },
    "gemini": {
        # Google's OpenAI-compatibility shim.
        "base": "https://generativelanguage.googleapis.com/v1beta/openai",
        "key_env": "GEMINI_API_KEY",
        "models": ["gemini-2.5-flash", "gemini-2.0-flash"],
    },
    "groq": {
        "base": "https://api.groq.com/openai/v1",
        "key_env": "GROQ_API_KEY",
        "models": ["llama-3.3-70b-versatile", "openai/gpt-oss-120b", "qwen/qwen3-32b"],
    },
    "cerebras": {
        "base": "https://api.cerebras.ai/v1",
        "key_env": "CEREBRAS_API_KEY",
        "models": ["llama-3.3-70b", "qwen-3-32b", "gpt-oss-120b"],
    },
    "sambanova": {
        "base": "https://api.sambanova.ai/v1",
        "key_env": "SAMBANOVA_API_KEY",
        "models": ["Meta-Llama-3.3-70B-Instruct", "gpt-oss-120b"],
    },
    "nim": {
        "base": "https://integrate.api.nvidia.com/v1",
        "key_env": "NVIDIA_API_KEY",
        "models": [
            "qwen/qwen3-next-80b-a3b-instruct",
            "mistralai/mistral-large-3-675b-instruct-2512",
            "meta/llama-3.3-70b-instruct",
            "openai/gpt-oss-120b",
        ],
    },
}

TOOL = [{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get current weather for a city",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string", "description": "city name"}},
            "required": ["city"],
        },
    },
}]
MSGS = [
    {"role": "system", "content": "You are a helpful assistant. Use tools when needed."},
    {"role": "user", "content": "What's the weather in Paris and Tokyo right now? Call get_weather for each."},
]


def probe(base: str, key: str, model: str) -> dict:
    body = json.dumps({
        "model": model, "messages": MSGS, "tools": TOOL,
        "tool_choice": "auto", "max_tokens": 512, "temperature": 0,
    }).encode()
    req = urllib.request.Request(
        base + "/chat/completions", data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    t = time.time()
    try:
        r = json.load(urllib.request.urlopen(req, timeout=90))
    except Exception as e:  # noqa: BLE001 — probe reports the error, never raises
        return {"model": model, "verdict": "ERROR", "detail": str(e)[:140],
                "sec": round(time.time() - t, 1)}
    dt = round(time.time() - t, 1)
    ch = (r.get("choices") or [{}])[0]
    msg = ch.get("message") or {}
    tcs = msg.get("tool_calls") or []
    content = msg.get("content") or ""
    fr = ch.get("finish_reason")
    # Harmony / special-token leakage (gpt-oss family under some serving stacks).
    harmony = bool(re.search(r"<\|", content)) or any(
        re.search(r"<\|", (tc.get("function") or {}).get("name", "")) for tc in tcs)
    n_valid = 0
    names: set = set()
    for tc in tcs:
        fn = tc.get("function") or {}
        names.add(fn.get("name"))
        try:
            a = json.loads(fn.get("arguments") or "{}")
            if isinstance(a, dict) and (a.get("city") or a.get("location")):
                n_valid += 1
        except Exception:  # noqa: BLE001
            pass
    if tcs and n_valid == len(tcs) and names == {"get_weather"} and not harmony:
        verdict = "PASS" if len(tcs) >= 2 else "PASS(1call,no-parallel)"
    elif harmony:
        verdict = "LEAK(harmony)"
    elif tcs and n_valid >= 1:
        verdict = "PARTIAL"
    elif not tcs:
        verdict = "FAIL(tool-as-text)" if "get_weather" in content else "FAIL(no tool_calls)"
    else:
        verdict = "PARTIAL"
    return {"model": model, "verdict": verdict, "calls": len(tcs),
            "finish": fr, "sec": dt, "preview": content[:50].replace("\n", " ")}


def main() -> int:
    wanted = [a.lower() for a in sys.argv[1:]] or list(PROVIDERS)
    any_fail = False
    any_probed = False
    for name in wanted:
        spec = PROVIDERS.get(name)
        if not spec:
            print(f"\n## {name}: unknown provider (known: {', '.join(PROVIDERS)})")
            continue
        key = os.environ.get(spec["key_env"], "").strip()
        if not key:
            print(f"\n## {name}: SKIP — set {spec['key_env']} to probe")
            continue
        any_probed = True
        print(f"\n## {name}  ({spec['base']})")
        print(f"{'MODEL':<46}{'VERDICT':<26}{'calls':<6}{'sec':<6}finish")
        print("-" * 96)
        for model in spec["models"]:
            res = probe(spec["base"], key, model)
            v = res["verdict"]
            if v.startswith(("FAIL", "ERROR", "LEAK")):
                any_fail = True
            print(f"{res['model']:<46}{v:<26}{str(res.get('calls', '-')):<6}"
                  f"{str(res['sec']):<6}{res.get('finish', '-')}  "
                  f"{res.get('detail') or res.get('preview', '')}")
    if not any_probed:
        print("No providers probed — set at least one *_API_KEY env var.")
        return 2
    print("\nLegend: PASS=valid parallel tool_calls · PASS(1call,no-parallel)=gpt-oss "
          "limitation · FAIL(tool-as-text)=serving emits JSON in content · "
          "LEAK=harmony tokens bled into output")
    return 1 if any_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
