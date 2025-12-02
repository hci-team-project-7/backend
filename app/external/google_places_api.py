from __future__ import annotations

import logging
from typing import Any, Dict, List

import httpx

from app.api.models.schemas import PlannerData
from app.core.config import settings

logger = logging.getLogger(__name__)

PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
LEGACY_TEXTSEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
LEGACY_NEARBY_URL = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
PLACES_FIELD_MASK = (
    "places.displayName,"
    "places.formattedAddress,"
    "places.location,"
    "places.types,"
    "places.rating,"
    "places.userRatingCount,"
    "places.primaryType,"
    "places.editorialSummary,"
    "places.photos"
)


async def _search_places(query: str, max_results: int = 8) -> List[Dict[str, Any]]:
    """
    Raw Google Places text search call.
    Returns the raw place objects from the API.
    """
    if not settings.google_places_api_key:
        return []

    places: List[Dict[str, Any]] = []

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
            places = data.get("places", []) or []
    except Exception as exc:  # pragma: no cover - network dependent
        logger.warning("Google Places search failed for '%s': %s", query, exc)
        places = []

    if places:
        return places

    # Fallback to legacy Places Text Search when the new API isn't enabled
    return await _search_places_legacy(query, max_results)


async def _search_places_legacy(query: str, max_results: int = 8) -> List[Dict[str, Any]]:
    """
    Backup search using the classic Places Text Search API.
    Useful when the new Places API (v1) is not enabled for the provided key.
    """
    if not settings.google_places_api_key:
        return []

    params = {
        "query": query,
        "language": "ko",
        "key": settings.google_places_api_key,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(LEGACY_TEXTSEARCH_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
            return (data.get("results") or [])[:max_results]
    except Exception as exc:  # pragma: no cover - network dependent
        logger.warning("Legacy Places search failed for '%s': %s", query, exc)
        return []


async def search_restaurants_near(
    anchor_name: str,
    lat: float,
    lng: float,
    radius_m: int = 2000,
    max_results: int = 6,
) -> List[Dict[str, Any]]:
    """
    Location-biased restaurant search around a given coordinate using Google Places Text Search.
    Falls back to an empty list when API key is missing or errors occur.
    """
    if not settings.google_places_api_key:
        return []

    places: List[Dict[str, Any]] = []

    headers = {
        "X-Goog-Api-Key": settings.google_places_api_key,
        "X-Goog-FieldMask": PLACES_FIELD_MASK,
    }
    payload = {
        "textQuery": f"{anchor_name} 근처 맛집",
        "pageSize": max_results,
        "languageCode": "ko",
        "locationBias": {
            "circle": {
                "center": {"latitude": lat, "longitude": lng},
                "radius": radius_m,
            }
        },
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(PLACES_URL, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            places = data.get("places", []) or []
    except Exception as exc:  # pragma: no cover - network dependent
        logger.warning("Google Places nearby search failed for '%s': %s", anchor_name, exc)
        places = []

    if places:
        return places

    # Fallback: classic Places Nearby Search API
    legacy_params = {
        "location": f"{lat},{lng}",
        "radius": radius_m,
        "keyword": anchor_name,
        "type": "restaurant",
        "language": "ko",
        "key": settings.google_places_api_key,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(LEGACY_NEARBY_URL, params=legacy_params)
            resp.raise_for_status()
            data = resp.json()
            return (data.get("results") or [])[:max_results]
    except Exception as exc:  # pragma: no cover - network dependent
        logger.warning("Legacy nearby search failed for '%s': %s", anchor_name, exc)
        return []


def _normalize_place(place: Dict[str, Any], city: str, style: str) -> Dict[str, Any] | None:
    name = place.get("displayName", {}).get("text") or place.get("name")
    if not name:
        return None

    # Support both Places v1 (location.latitude) and classic API (geometry.location.lat)
    location = place.get("location", {}) or place.get("geometry", {}).get("location", {}) or {}
    lat = location.get("latitude") or location.get("lat")
    lng = location.get("longitude") or location.get("lng")

    rating = place.get("rating")
    user_count = place.get("userRatingCount", place.get("user_ratings_total", 0)) or 0
    style_score = 6.5
    if rating:
        style_score += min(3.0, rating / 2)
    if user_count > 200:
        style_score += 0.5

    types = place.get("types") or []
    place_type = place.get("primaryType") or place.get("primary_type") or (types[0] if types else style)

    highlight = (
        (place.get("editorialSummary") or {}).get("text")
        or place.get("formattedAddress")
        or place.get("formatted_address")
        or place_type
    )

    def _extract_photo_url() -> str | None:
        photos = place.get("photos") or []
        if not photos:
            return None
        photo = photos[0] or {}

        # Places API (new) photo name -> media endpoint
        photo_name = photo.get("name")
        if photo_name:
            return f"https://places.googleapis.com/v1/{photo_name}/media?maxWidthPx=800&key={settings.google_places_api_key}"

        # New API sometimes returns a direct URI
        direct_uri = photo.get("photoUri")
        if direct_uri:
            return direct_uri

        # Classic Places API photo_reference
        ref = photo.get("photo_reference") or photo.get("photoReference")
        if ref:
            return f"https://maps.googleapis.com/maps/api/place/photo?maxwidth=800&photo_reference={ref}&key={settings.google_places_api_key}"
        return None

    photo_url = _extract_photo_url()

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
        "address": place.get("formattedAddress") or place.get("formatted_address"),
        "source": "google_places",
        "image": photo_url,
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


async def fetch_place_details(query: str, city: str | None = None, style: str = "attraction") -> Dict[str, Any] | None:
    """
    Lightweight helper to fetch a single place record and normalize it for itinerary updates.
    Falls back to None when API access is unavailable.
    """
    if not query:
        return None

    search_query = query
    if city and city not in query:
        search_query = f"{query} {city}"

    places = await _search_places(search_query, max_results=3)
    for place in places:
        normalized = _normalize_place(place, city=city or query, style=style)
        if normalized:
            return normalized
    return None
