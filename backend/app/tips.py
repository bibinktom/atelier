"""Tips + latest AI news shown while the model is thinking.

Static tips list rotates client-side. AI news is fetched once every 6h via Tavily
and cached in-process — keeps the 'thinking' UX lively without a Tavily hit per turn.
"""
import os
import time

import httpx
from fastapi import APIRouter, Depends, Request

from .auth import require_approved_user as require_user

router = APIRouter()

TIPS = [
    "Try the **Skills** cards on the empty page — save your favourite prompts for one-click reuse.",
    "Drop an image into a chat — switch to the **Llama 3.2 Vision** model and the AI can read screenshots, charts, and photos.",
    "Ask for a 10-slide deck and the AI will build a real `.pptx` file you can download.",
    "Use the **Files** button (top-right) to browse and upload to your project folder.",
    "**Project · Import folder from my computer…** — pick a folder on your laptop, the AI gets full read-write access to its contents.",
    "Search past chats from the box at the top of the sidebar — finds matches across every conversation you've ever had.",
    "The AI **remembers** durable facts across chats: your preferences, your family's names, recurring patterns. Manage them under settings.",
    "For complex requests, the AI now spins up specialist helpers in parallel — vision for images, document writers for prose, etc.",
    "Multiple family members can sign in with their own Google account — each has private chats, files, and memories.",
    "Files the AI creates land in your project folder on the home server — open them in Excel/PowerPoint/Pages directly.",
    "Ask 'use the research you already have' to make the AI commit to an answer when it's been searching too long.",
    "Switch model per chat from the picker (top right). **GPT-OSS 120B** is a great upgrade for serious documents.",
    "The AI runs Python in a sandbox — ask it to plot data, transform CSVs, or rename your photos by date.",
    "Press **⏎** to send · **⇧⏎** for a newline.",
    "Stuck reply? Click **Stop**, reload, and either delete the chat (×) or ask the AI to 'use what you have and write the answer'.",
]


# In-process cache. (Process restarts re-fetch — fine since news refreshes every 6h.)
_NEWS_CACHE: dict[str, object] = {"t": 0.0, "items": []}
_NEWS_TTL = 6 * 60 * 60   # 6 hours


async def _fetch_news() -> list[dict]:
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        return []
    body = {
        "api_key": api_key,
        "query": "latest AI news this week — frontier model releases, agent breakthroughs, NVIDIA NIM, OpenAI, Anthropic, DeepSeek, Llama, Qwen, Gemini",
        "max_results": 6,
        "include_answer": False,
        "search_depth": "basic",
        "topic": "news",
    }
    timeout = httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post("https://api.tavily.com/search", json=body)
        if resp.status_code >= 400:
            return []
        data = resp.json()
    except (httpx.RequestError, ValueError):
        return []
    out = []
    for r in data.get("results", []):
        out.append({
            "title": (r.get("title") or "").strip()[:140],
            "url": r.get("url") or "",
            "snippet": (r.get("content") or "").strip()[:240],
        })
    return out


@router.get("/tips")
async def get_tips(_: Request, user=Depends(require_user)):
    now = time.time()
    if now - float(_NEWS_CACHE.get("t", 0)) > _NEWS_TTL:
        _NEWS_CACHE["items"] = await _fetch_news()
        _NEWS_CACHE["t"] = now
    return {"tips": TIPS, "news": _NEWS_CACHE.get("items", [])}
