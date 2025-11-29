from __future__ import annotations

import logging
from typing import List
from urllib.parse import quote_plus

import httpx

logger = logging.getLogger(__name__)

try:
    from crawl4ai import AsyncWebCrawler
except ImportError:  # pragma: no cover - optional dependency
    AsyncWebCrawler = None  # type: ignore


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
    1) Try crawl4ai when available.
    2) Fallback to a lightweight Wikipedia summary.
    """
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
