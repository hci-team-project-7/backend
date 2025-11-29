from __future__ import annotations

import logging
from typing import List

import httpx

from app.api.models.schemas import Location
from app.core.config import settings

logger = logging.getLogger(__name__)


async def compute_route_durations(locations: List[Location]) -> List[int]:
    """
    Google Routes API adapter.
    Returns a list of travel durations in minutes between successive locations.
    Falls back to zeros when the API key is missing or an error occurs.
    """
    if len(locations) < 2:
        return []

    if not settings.google_routes_api_key:
        return [0 for _ in range(len(locations) - 1)]

    url = "https://routes.googleapis.com/directions/v2:computeRoutes"
    headers = {
        "X-Goog-Api-Key": settings.google_routes_api_key,
        "X-Goog-FieldMask": "routes.duration,routes.distanceMeters",
    }

    durations: List[int] = []
    async with httpx.AsyncClient(timeout=10.0) as client:
        for origin, dest in zip(locations[:-1], locations[1:]):
            body = {
                "origin": {
                    "location": {"latLng": {"latitude": origin.lat, "longitude": origin.lng}},
                },
                "destination": {
                    "location": {"latLng": {"latitude": dest.lat, "longitude": dest.lng}},
                },
                "travelMode": "DRIVE",
            }
            try:
                resp = await client.post(url, headers=headers, json=body)
                resp.raise_for_status()
                data = resp.json()
                duration_str = data["routes"][0]["duration"]  # "123s"
                seconds = int(duration_str.replace("s", ""))
                durations.append(seconds // 60)
            except Exception as exc:  # pragma: no cover - network errors handled gracefully
                logger.warning("Failed to compute route %s -> %s: %s", origin.name, dest.name, exc)
                durations.append(0)
    return durations
