"""Web tools — search and extract web content via multiple backends."""

import json
import os
import logging

import httpx

log = logging.getLogger(__name__)

# Backend URLs
FIRECRAWL_URL = os.environ.get("FIRECRAWL_API_URL", "https://api.firecrawl.dev/v1")
SEARXNG_URL = os.environ.get("SEARXNG_URL", "")
TAVILY_URL = "https://api.tavily.com"


def _get_backend() -> str:
    """Determine which web search backend to use."""
    backend = os.environ.get("WEB_BACKEND", "").lower()
    if backend:
        return backend
    # Auto-detect from available keys
    if os.environ.get("TAVILY_API_KEY"):
        return "tavily"
    if os.environ.get("FIRECRAWL_API_KEY"):
        return "firecrawl"
    if SEARXNG_URL:
        return "searxng"
    return "none"


async def _firecrawl_search(query: str, limit: int = 5) -> str:
    api_key = os.environ.get("FIRECRAWL_API_KEY", "")
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{FIRECRAWL_URL}/search",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"query": query, "limit": limit},
            timeout=30,
        )
    if resp.status_code != 200:
        return json.dumps({"error": f"search failed ({resp.status_code})"})
    return resp.text


async def _tavily_search(query: str, limit: int = 5) -> str:
    api_key = os.environ.get("TAVILY_API_KEY", "")
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{TAVILY_URL}/search",
            json={"api_key": api_key, "query": query, "max_results": limit},
            timeout=30,
        )
    if resp.status_code != 200:
        return json.dumps({"error": f"search failed ({resp.status_code})"})
    data = resp.json()
    # Normalize Tavily response
    results = []
    for r in data.get("results", []):
        results.append({
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": r.get("content", "")[:500],
        })
    return json.dumps({"results": results})


async def _searxng_search(query: str, limit: int = 5) -> str:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{SEARXNG_URL}/search",
            params={"q": query, "format": "json", "pageno": 1},
            timeout=30,
        )
    if resp.status_code != 200:
        return json.dumps({"error": f"search failed ({resp.status_code})"})
    data = resp.json()
    results = []
    for r in data.get("results", [])[:limit]:
        results.append({
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": r.get("content", "")[:500],
        })
    return json.dumps({"results": results})


async def web_search(args: dict) -> str:
    query = args.get("query", "")
    limit = args.get("limit", 5)
    if not query:
        return json.dumps({"error": "query is required"})

    backend = _get_backend()
    if backend == "firecrawl":
        return await _firecrawl_search(query, limit)
    elif backend == "tavily":
        return await _tavily_search(query, limit)
    elif backend == "searxng":
        return await _searxng_search(query, limit)
    else:
        return json.dumps({"error": "no web search backend configured (set FIRECRAWL_API_KEY, TAVILY_API_KEY, or SEARXNG_URL)"})


async def _firecrawl_extract(url: str) -> str:
    api_key = os.environ.get("FIRECRAWL_API_KEY", "")
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{FIRECRAWL_URL}/scrape",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"url": url, "formats": ["markdown"]},
            timeout=30,
        )
    if resp.status_code != 200:
        return json.dumps({"error": f"extraction failed ({resp.status_code})"})
    return resp.text


async def _tavily_extract(url: str) -> str:
    api_key = os.environ.get("TAVILY_API_KEY", "")
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{TAVILY_URL}/extract",
            json={"api_key": api_key, "urls": [url]},
            timeout=30,
        )
    if resp.status_code != 200:
        return json.dumps({"error": f"extraction failed ({resp.status_code})"})
    data = resp.json()
    results = data.get("results", [])
    if results:
        return json.dumps({"content": results[0].get("raw_content", "")[:10000], "url": url})
    return json.dumps({"error": "no content extracted"})


async def _httpx_extract(url: str) -> str:
    """Fallback: direct HTTP fetch with basic HTML-to-text."""
    async with httpx.AsyncClient(follow_redirects=True) as client:
        resp = await client.get(url, timeout=30)
    if resp.status_code != 200:
        return json.dumps({"error": f"fetch failed ({resp.status_code})"})
    # Basic HTML stripping
    import re
    text = re.sub(r"<script[^>]*>.*?</script>", "", resp.text, flags=re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return json.dumps({"content": text[:10000], "url": url})


async def web_extract(args: dict) -> str:
    url = args.get("url", "")
    urls = args.get("urls", [])
    if not url and not urls:
        return json.dumps({"error": "url or urls is required"})

    # Handle single url for backward compat
    if url and not urls:
        urls = [url]

    backend = _get_backend()
    results = []
    for u in urls[:5]:  # Max 5 URLs per call
        if backend == "firecrawl":
            results.append(await _firecrawl_extract(u))
        elif backend == "tavily":
            results.append(await _tavily_extract(u))
        else:
            results.append(await _httpx_extract(u))

    if len(results) == 1:
        return results[0]
    return json.dumps({"results": results})


def _check_web() -> bool:
    return _get_backend() != "none"


def register(registry):
    """Register web tools."""
    registry.register(
        name="web_search",
        description="Search the web. Supports Firecrawl, Tavily, and SearXNG backends.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "description": "Max results (default 5)", "default": 5},
            },
            "required": ["query"],
        },
        handler=web_search,
        toolset="web",
        check_fn=_check_web,
        emoji="🔍",
    )
    registry.register(
        name="web_extract",
        description="Extract content from web page URLs as text/markdown.",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Single URL to extract"},
                "urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of URLs to extract (max 5)",
                    "maxItems": 5,
                },
            },
        },
        handler=web_extract,
        toolset="web",
        emoji="📄",
    )
