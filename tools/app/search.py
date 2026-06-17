import os

import httpx


async def tavily_search(query: str, max_results: int) -> dict:
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        return {"error": "TAVILY_API_KEY not configured", "results": []}

    body = {
        "api_key": api_key,
        "query": query,
        "max_results": max_results,
        "include_answer": True,
        "search_depth": "basic",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post("https://api.tavily.com/search", json=body)
    if resp.status_code >= 400:
        return {"error": f"tavily {resp.status_code}: {resp.text[:300]}", "results": []}
    data = resp.json()
    results = [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "content": r.get("content", ""),
        }
        for r in data.get("results", [])
    ]
    return {"answer": data.get("answer"), "results": results}
