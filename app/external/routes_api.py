from __future__ import annotations

from typing import List

from app.api.models.schemas import Location
from app.core.config import settings


async def compute_route_durations(locations: List[Location]) -> List[int]:
    """
    Dummy Google Routes API adapter.

    If a real API key is configured, this function could be extended to perform
    HTTP requests. For now, it returns flat durations to keep timelines spaced.
    """
    if len(locations) < 2:
        return []

    if settings.google_routes_api_key:
        # Placeholder: in production, call Google Routes here.
        return [15 for _ in range(len(locations) - 1)]

    return [15 for _ in range(len(locations) - 1)]
