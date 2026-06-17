"""Thin client for OpenAI-compatible chat completions, with per-user provider keys.

Routing:
- ids prefixed with `ollama/` go to the local Ollama server (OLLAMA_BASE_URL);
- otherwise, if the current request carries a per-user OpenRouter key, the call
  goes to OpenRouter on THAT user's key (user-funded inference);
- otherwise, if NIM fallback is allowed, it goes to the operator's NVIDIA NIM key;
- otherwise the call fails closed with LLMNotConnected.

The per-user credentials are carried in a ContextVar set once per turn
(chat.run_turn), so every nested call — planner, summarizer, critic, sub-agents,
parallel tool fan-out — inherits them automatically without threading a param
through every function. Background tasks that run outside the turn's context
(e.g. memory extraction) must pass `creds=` explicitly.
"""
import asyncio
import contextlib
import contextvars
import json
import random
from dataclasses import dataclass
from typing import AsyncIterator

import httpx

from . import config, telemetry


# Transient-429 retries. Free OpenRouter models share community capacity and 429
# briefly (independent of your own quota), supplying a Retry-After. We retry a few
# times honouring it before giving up.
_MAX_RETRIES = 3
_RETRY_CAP_S = 10.0


_OLLAMA_PREFIX = "ollama/"


@dataclass
class LLMCreds:
    """Per-request provider credentials. `openrouter_key` is the user's decrypted
    OpenRouter key (or None). `allow_nim_fallback` lets a not-connected user fall
    back to the operator's NIM key when ENABLE_NIM_FALLBACK is on."""
    openrouter_key: str | None = None
    allow_nim_fallback: bool = False


class LLMNotConnected(Exception):
    """Raised when a request has no usable provider key and fallback is disabled.
    The orchestrator maps this to a 'connect your provider' prompt."""


class LLMKeyRevoked(Exception):
    """Raised when the upstream returns 401 — the user's key was revoked/invalid.
    The orchestrator maps this to a 'reconnect your provider' prompt."""


class LLMRateLimited(Exception):
    """Raised when the upstream keeps returning 429 after retries. The orchestrator
    maps this to a friendly 'model is busy, try again' message."""


def _retry_after(resp: httpx.Response) -> float:
    """Seconds to wait before retrying a 429, from the Retry-After header (capped),
    plus a little jitter so parallel calls don't all retry in lockstep."""
    base = 2.0
    ra = resp.headers.get("Retry-After")
    if ra:
        try:
            base = float(ra)
        except ValueError:
            pass
    return min(base, _RETRY_CAP_S) + random.uniform(0.0, 0.5)


def _rate_limit_message(model: str) -> str:
    return (f"“{model}” is rate-limited right now — free models share capacity and "
            "are briefly unavailable when busy. Wait a few seconds and try again, "
            "pick another model, or add credits to your OpenRouter account for "
            "higher limits.")


# Set per turn in chat.run_turn; read by route() for every nested LLM call.
_CURRENT_CREDS: contextvars.ContextVar[LLMCreds | None] = contextvars.ContextVar(
    "llm_creds", default=None)


@contextlib.contextmanager
def using_creds(creds: LLMCreds):
    """Bind per-user creds for the duration of a turn. ContextVars copy into child
    tasks (asyncio.gather / create_task), so parallel tools + sub-agents inherit them."""
    token = _CURRENT_CREDS.set(creds)
    try:
        yield
    finally:
        _CURRENT_CREDS.reset(token)


def route(model: str, creds: LLMCreds | None = None) -> tuple[str, str, str, str]:
    """Resolve (base_url, api_key, upstream_model_id, provider) for a model id.

    `creds` defaults to the ContextVar bound by the current turn. Fails closed
    with LLMNotConnected when no key is available and NIM fallback is off.
    """
    if creds is None:
        creds = _CURRENT_CREDS.get()
    if creds is None:
        # No turn context (e.g. a stray call path) — honour the global fallback flag.
        creds = LLMCreds(openrouter_key=None, allow_nim_fallback=config.ENABLE_NIM_FALLBACK)

    if model.startswith(_OLLAMA_PREFIX):
        return config.OLLAMA_BASE_URL, "ollama", model[len(_OLLAMA_PREFIX):], "ollama"
    if creds.openrouter_key:
        return config.OPENROUTER_API_BASE, creds.openrouter_key, model, "openrouter"
    if creds.allow_nim_fallback:
        return config.NVIDIA_BASE_URL, config.NVIDIA_API_KEY, _to_nim_id(model), "nim"
    raise LLMNotConnected("no LLM provider connected for this user")


def _to_nim_id(model: str) -> str:
    """Normalize an OpenRouter-style id to its NIM equivalent for the fallback path:
    drop the `:free`/`:nitro`/... variant suffix and map the `meta-llama/` vendor
    prefix to NIM's `meta/`. Other vendors (qwen, openai, google, mistralai) match."""
    base = model.split(":", 1)[0]
    if base.startswith("meta-llama/"):
        base = "meta/" + base[len("meta-llama/"):]
    # OpenRouter's short coder id → NIM's fully-qualified id (verified tool-caller).
    if base == "qwen/qwen3-coder":
        base = "qwen/qwen3-coder-480b-a35b-instruct"
    return base


def _headers(api_key: str, provider: str, *, accept_stream: bool = False) -> dict:
    h = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if accept_stream:
        h["Accept"] = "text/event-stream"
    if provider == "openrouter":
        # OpenRouter attribution headers (recommended; surface the app in their dashboard).
        h["HTTP-Referer"] = config.PUBLIC_FRONTEND_URL
        h["X-Title"] = "Atelier"
    return h


async def chat_once(
    *,
    model: str,
    messages: list[dict],
    max_tokens: int = 512,
    temperature: float = 0.2,
    creds: LLMCreds | None = None,
) -> str:
    """Single non-streaming completion. Returns the assistant's text content (or empty string)."""
    base_url, api_key, upstream_model, provider = route(model, creds)
    with telemetry.span("nim.chat", **{"nim.model": model, "nim.provider": provider,
                                        "nim.streaming": False,
                                        "nim.messages": len(messages),
                                        "nim.max_tokens": max_tokens}):
        body = {"model": upstream_model, "messages": messages,
                "max_tokens": max_tokens, "temperature": temperature}
        headers = _headers(api_key, provider)
        timeout = httpx.Timeout(connect=10.0, read=90.0, write=20.0, pool=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            for attempt in range(_MAX_RETRIES + 1):
                resp = await client.post(f"{base_url}/chat/completions",
                                         headers=headers, json=body)
                if resp.status_code == 429 and attempt < _MAX_RETRIES:
                    await asyncio.sleep(_retry_after(resp))
                    continue
                if resp.status_code == 401:
                    raise LLMKeyRevoked(f"{provider} returned 401")
                if resp.status_code == 429:
                    raise LLMRateLimited(_rate_limit_message(model))
                if resp.status_code >= 400:
                    raise RuntimeError(f"LLM error {resp.status_code}: {resp.text[:300]}")
                try:
                    return resp.json()["choices"][0]["message"].get("content") or ""
                except (KeyError, IndexError, ValueError):
                    return ""
        return ""  # unreachable (loop returns or raises)


async def stream_chat(
    *,
    model: str,
    messages: list[dict],
    tools: list[dict] | None,
    temperature: float = 0.3,
    creds: LLMCreds | None = None,
) -> AsyncIterator[dict]:
    """Yield delta chunks. Each chunk is the OpenAI-format `choices[0].delta` dict
    plus a synthetic `_finish_reason` field on the last chunk."""
    base_url, api_key, upstream_model, provider = route(model, creds)
    body: dict = {
        "model": upstream_model,
        "messages": messages,
        "stream": True,
        "temperature": temperature,
        "max_tokens": 4096,
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"

    headers = _headers(api_key, provider, accept_stream=True)
    url = f"{base_url}/chat/completions"

    with telemetry.span("nim.chat", **{"nim.model": model, "nim.provider": provider,
                                        "nim.streaming": True,
                                        "nim.messages": len(messages),
                                        "nim.tools": len(tools or [])}):
        timeout = httpx.Timeout(connect=15.0, read=300.0, write=30.0, pool=15.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            for attempt in range(_MAX_RETRIES + 1):
                delay = 0.0
                async with client.stream("POST", url, json=body, headers=headers) as resp:
                    if resp.status_code == 429 and attempt < _MAX_RETRIES:
                        # Retry-eligible: capture the backoff, close the response (on
                        # leaving this block), then sleep + retry below. Safe because
                        # no tokens have been yielded yet.
                        delay = _retry_after(resp)
                    elif resp.status_code == 401:
                        raise LLMKeyRevoked(f"{provider} returned 401")
                    elif resp.status_code == 429:
                        await resp.aread()
                        raise LLMRateLimited(_rate_limit_message(model))
                    elif resp.status_code >= 400:
                        err = await resp.aread()
                        raise RuntimeError(f"LLM error {resp.status_code}: {err.decode('utf-8', 'replace')[:500]}")
                    else:
                        async for line in resp.aiter_lines():
                            if not line or not line.startswith("data:"):
                                continue
                            payload = line[5:].strip()
                            if payload == "[DONE]":
                                break
                            try:
                                obj = json.loads(payload)
                            except json.JSONDecodeError:
                                continue
                            choices = obj.get("choices") or []
                            if not choices:
                                continue
                            ch = choices[0]
                            delta = ch.get("delta") or {}
                            if ch.get("finish_reason"):
                                delta = dict(delta)
                                delta["_finish_reason"] = ch["finish_reason"]
                            yield delta
                        return
                # Only reached on the retry-eligible 429 branch (response now closed).
                # Surface the backoff as a synthetic delta so the orchestrator can
                # emit a visible "retrying" notice — this keeps the SSE connection
                # warm (defeats the ~100s tunnel idle-timeout) and stops the turn
                # looking frozen during the silent wait.
                yield {"_rate_limit_retry": True, "delay": round(delay, 1),
                       "attempt": attempt + 1, "max": _MAX_RETRIES, "model": model}
                await asyncio.sleep(delay)
