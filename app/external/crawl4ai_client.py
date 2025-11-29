from __future__ import annotations

import logging
from typing import List
from urllib.parse import quote_plus

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

try:
    from crawl4ai import AsyncWebCrawler
except ImportError:  # pragma: no cover - optional dependency
    AsyncWebCrawler = None  # type: ignore


async def _fetch_with_firecrawl(query: str) -> List[str]:
    if not settings.firecrawl_api_key:
        return []

    url = "https://api.firecrawl.dev/v1/search"
    headers = {
        "Authorization": f"Bearer {settings.firecrawl_api_key}",
        "Content-Type": "application/json",
    }
    payload = {"query": query, "limit": 3}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            candidates = data.get("results") or data.get("data") or []
            snippets: List[str] = []
            for item in candidates:
                content = (
                    item.get("content")
                    or item.get("description")
                    or item.get("snippet")
                    or item.get("title")
                )
                url_hint = item.get("url") or item.get("link")
                if content:
                    snippets.append(content[:600])
                if url_hint:
                    snippets.append(f"출처: {url_hint}")
            return [s for s in snippets if s]
    except Exception as exc:  # pragma: no cover - network dependent
        logger.warning("Firecrawl fetch failed for %s: %s", query, exc)
        return []


async def _fetch_with_wikipedia(query: str) -> List[str]:
    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote_plus(query)}"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            extract = data.get("extract")
            if extract:
                return [extract]
    except Exception as exc:  # pragma: no cover - network issues
        logger.debug("Wikipedia snippet fetch failed for %s: %s", query, exc)
    return []


async def fetch_poi_snippets(query: str) -> List[str]:
    """
    Retrieve descriptive snippets for a given POI.
    1) Try Firecrawl search API when configured.
    2) Try crawl4ai when available.
    3) Fallback to a lightweight Wikipedia summary.
    """
    firecrawl = await _fetch_with_firecrawl(query)
    if firecrawl:
        return firecrawl

    if AsyncWebCrawler:
        try:
            async with AsyncWebCrawler() as crawler:
                result = await crawler.arun(query)
                snippets: List[str] = []
                if result and getattr(result, "markdown", None):
                    snippets.append(result.markdown[:500])
                if result and getattr(result, "meta", None):
                    meta_text = " ".join(str(v) for v in result.meta.values() if v)
                    if meta_text:
                        snippets.append(meta_text[:300])
                if snippets:
                    return snippets
        except Exception as exc:  # pragma: no cover - optional path
            logger.warning("crawl4ai fetch failed for %s: %s", query, exc)

    return await _fetch_with_wikipedia(query)
