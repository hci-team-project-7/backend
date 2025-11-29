from __future__ import annotations

import logging
from typing import Any, Dict, List

import httpx

from app.api.models.schemas import PlannerData
from app.core.config import settings

logger = logging.getLogger(__name__)

PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
PLACES_FIELD_MASK = (
    "places.displayName,"
    "places.formattedAddress,"
    "places.location,"
    "places.types,"
    "places.rating,"
    "places.userRatingCount,"
    "places.primaryType,"
    "places.editorialSummary"
)


async def _search_places(query: str, max_results: int = 8) -> List[Dict[str, Any]]:
    """
    Raw Google Places text search call.
    Returns the raw place objects from the API.
    """
    if not settings.google_places_api_key:
        return []

    headers = {
        "X-Goog-Api-Key": settings.google_places_api_key,
        "X-Goog-FieldMask": PLACES_FIELD_MASK,
    }
    payload = {
        "textQuery": query,
        "pageSize": max_results,
        "languageCode": "ko",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(PLACES_URL, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data.get("places", [])
    except Exception as exc:  # pragma: no cover - network dependent
        logger.warning("Google Places search failed for '%s': %s", query, exc)
        return []


def _normalize_place(place: Dict[str, Any], city: str, style: str) -> Dict[str, Any] | None:
    name = place.get("displayName", {}).get("text") or place.get("name")
    if not name:
        return None

    location = place.get("location", {}) or {}
    lat = location.get("latitude")
    lng = location.get("longitude")

    rating = place.get("rating")
    user_count = place.get("userRatingCount", 0) or 0
    style_score = 6.5
    if rating:
        style_score += min(3.0, rating / 2)
    if user_count > 200:
        style_score += 0.5

    types = place.get("types") or []
    place_type = place.get("primaryType") or (types[0] if types else style)

    highlight = (
        (place.get("editorialSummary") or {}).get("text")
        or place.get("formattedAddress")
        or place_type
    )

    return {
        "name": name,
        "city": city,
        "type": place_type,
        "styleScore": style_score,
        "lat": lat,
        "lng": lng,
        "highlight": highlight,
        "rating": rating,
        "userRatingsTotal": user_count,
        "address": place.get("formattedAddress"),
        "source": "google_places",
    }


async def search_places_for_planner(planner_data: PlannerData, max_results_per_city: int = 8) -> List[Dict[str, Any]]:
    """
    Search top places for each city/style combination using Google Places API.
    Returns normalized POI candidates used by itinerary generation.
    """
    if not settings.google_places_api_key:
        return []

    pois: List[Dict[str, Any]] = []
    seen_names: set[str] = set()

    cities = planner_data.cities or [planner_data.country]
    styles = planner_data.styles or ["attraction"]

    for city in cities:
        for style in styles:
            query = f"{city} {style} attractions"
            raw_places = await _search_places(query, max_results=max_results_per_city)
            for place in raw_places:
                normalized = _normalize_place(place, city=city, style=style)
                if not normalized:
                    continue
                key = normalized["name"].casefold()
                if key in seen_names:
                    continue
                seen_names.add(key)
                pois.append(normalized)

    return pois
