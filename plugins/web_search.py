import os

import httpx

from plugins.fetch_url import html_to_text

ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
TIMEOUT_SECONDS = 10.0


def web_search(query: str, count: int = 5):
    api_key = os.environ.get("BRAVE_API_KEY")
    if not api_key:
        return {"error": "web search is not configured - set the BRAVE_API_KEY environment variable"}

    count = max(1, min(int(count or 5), 10))
    try:
        resp = httpx.get(
            ENDPOINT,
            params={"q": query, "count": count},
            headers={"Accept": "application/json", "X-Subscription-Token": api_key},
            timeout=TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
    except Exception as exc:
        return {"error": f"search request failed: {exc}"}

    data = resp.json()
    # Brave marks matched terms with <strong> and leaves entities encoded -
    # noise to the model, so they're flattened to plain text.
    results = [
        {"title": _plain(r.get("title")), "url": r.get("url"), "snippet": _plain(r.get("description"))}
        for r in data.get("web", {}).get("results", [])[:count]
    ]
    return {"results": results}


def _plain(fragment):
    return html_to_text(fragment)[1].replace("\n", " ") if fragment else fragment


def register(registry):
    registry.register("web_search", web_search, {
        "name": "web_search",
        "description": "Search the web (via Brave Search) and return matching results (title, url, snippet). Requires the BRAVE_API_KEY environment variable to be set.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query"},
                "count": {"type": "integer", "description": "Number of results to return (1-10, default 5)"},
            },
            "required": ["query"],
        },
    })
