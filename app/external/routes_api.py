from __future__ import annotations

import logging
from typing import Dict, List, Literal

import httpx

from app.api.models.schemas import Location
from app.core.config import settings

logger = logging.getLogger(__name__)


TravelMode = Literal["DRIVE", "WALK", "BICYCLE", "TRANSIT", "TWO_WHEELER"]
MAX_TRAVEL_MINUTES = 240  # 이동 시간 폭주 방지 상한선 (4시간)


def _normalize_travel_mode(mode: str | TravelMode | None) -> tuple[TravelMode, str]:
    """
    Normalize user-facing transport names into Google Routes enum strings while keeping a display mode.
    """
    if not mode:
        return "DRIVE", "drive"
    raw = str(mode).strip().lower()
    if raw in {"walk", "walking"}:
        return "WALK", "walk"
    if raw in {"bike", "bicycle", "cycle"}:
        return "BICYCLE", "bike"
    if raw in {"transit", "bus", "metro", "subway", "train", "rail", "tram"}:
        return "TRANSIT", "transit"
    if raw in {"two_wheeler", "scooter", "moped"}:
        return "TWO_WHEELER", "two_wheeler"
    if raw in {"drive", "driving", "car"}:
        return "DRIVE", "drive"
    # Fallback to drive on unknown input
    return "DRIVE", "drive"


async def compute_route_segments(locations: List[Location], mode: str | TravelMode = "DRIVE") -> List[Dict[str, int | str]]:
    """
    Google Routes API adapter.
    Returns a list of segments between successive locations with duration/distance.
    Falls back to default values when the API key is missing or an error occurs.
    """
    if len(locations) < 2:
        return []

    if not settings.google_routes_api_key:
        return [
            {"mode": mode.lower(), "durationMinutes": 30, "distanceMeters": 2000}
            for _ in range(len(locations) - 1)
        ]

    travel_mode, display_mode = _normalize_travel_mode(mode)
    url = "https://routes.googleapis.com/directions/v2:computeRoutes"
    headers = {
        "X-Goog-Api-Key": settings.google_routes_api_key,
        "X-Goog-FieldMask": "routes.duration,routes.distanceMeters",
    }

    segments: List[Dict[str, int | str]] = []
    async with httpx.AsyncClient(timeout=10.0) as client:
        for origin, dest in zip(locations[:-1], locations[1:]):
            body = {
                "origin": {
                    "location": {"latLng": {"latitude": origin.lat, "longitude": origin.lng}},
                },
                "destination": {
                    "location": {"latLng": {"latitude": dest.lat, "longitude": dest.lng}},
                },
                "travelMode": travel_mode,
            }
            try:
                resp = await client.post(url, headers=headers, json=body)
                resp.raise_for_status()
                data = resp.json()
                routes = data["routes"] if isinstance(data, dict) and "routes" in data else None
                if not routes:
                    raise ValueError("No routes returned from API")
                duration_str = routes[0]["duration"]  # "123s"
                seconds = int(duration_str.replace("s", ""))
                minutes = max(1, seconds // 60)
                if minutes > MAX_TRAVEL_MINUTES:
                    logger.info(
                        "Clamping travel time %s -> %s minutes (%s -> %s)",
                        minutes,
                        MAX_TRAVEL_MINUTES,
                        origin.name,
                        dest.name,
                    )
                minutes = min(minutes, MAX_TRAVEL_MINUTES)
                distance = int(routes[0].get("distanceMeters", 0))
                segments.append(
                    {"mode": display_mode, "durationMinutes": minutes, "distanceMeters": max(0, distance)}
                )
            except Exception as exc:  # pragma: no cover - network errors handled gracefully
                logger.warning("Failed to compute route %s -> %s: %s", origin.name, dest.name, exc)
                segments.append({"mode": display_mode, "durationMinutes": 30, "distanceMeters": 2000})
    return segments


async def compute_route_durations(locations: List[Location], mode: str | TravelMode = "DRIVE") -> List[int]:
    segments = await compute_route_segments(locations, mode)
    return [min(MAX_TRAVEL_MINUTES, int(seg.get("durationMinutes", 0))) for seg in segments]
