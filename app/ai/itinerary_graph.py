from __future__ import annotations

import hashlib
from datetime import timedelta
from typing import Dict, List, Tuple

from app.api.models.schemas import Activity, DayItinerary, Location, PlannerData

DEFAULT_CITY_COORDS = {
    "파리": (48.8566, 2.3522),
    "paris": (48.8566, 2.3522),
    "니스": (43.7102, 7.2620),
    "nice": (43.7102, 7.2620),
    "런던": (51.5074, -0.1278),
    "london": (51.5074, -0.1278),
    "도쿄": (35.6764, 139.6500),
    "tokyo": (35.6764, 139.6500),
    "서울": (37.5665, 126.9780),
    "seoul": (37.5665, 126.9780),
}


def _coords_for(name: str) -> Tuple[float, float]:
    key = name.lower()
    if key in DEFAULT_CITY_COORDS:
        return DEFAULT_CITY_COORDS[key]
    # deterministic pseudo coords from hash
    digest = hashlib.sha256(name.encode("utf-8")).digest()
    lat = 10 + (digest[0] / 255) * 70  # between 10 and 80
    lng = -130 + (digest[1] / 255) * 260  # between -130 and 130
    return round(lat, 4), round(lng, 4)


def _time_for_slot(slot: int) -> str:
    hour = 9 + slot * 3
    return f"{hour:02d}:00"


async def generate_itinerary(planner_data: PlannerData) -> Tuple[List[DayItinerary], Dict[str, List[Activity]]]:
    """Lightweight itinerary generator used when no external LLM is available."""
    start_date = planner_data.dateRange.start
    end_date = planner_data.dateRange.end
    num_days = max((end_date - start_date).days + 1, 1)
    overview: List[DayItinerary] = []
    activities_by_day: Dict[str, List[Activity]] = {}

    styles = planner_data.styles or ["sightseeing"]
    for idx in range(num_days):
        day = idx + 1
        current_city = planner_data.cities[min(idx, len(planner_data.cities) - 1)] if planner_data.cities else planner_data.country
        lat, lng = _coords_for(current_city)
        day_date = start_date + timedelta(days=idx)
        base_theme = styles[idx % len(styles)]

        activity_names = [
            f"{current_city} 도착 및 체크인",
            f"{base_theme} 산책과 명소 탐방",
            f"{current_city} 맛집에서 저녁",
        ]
        day_activities: List[Activity] = []
        locations: List[Location] = []
        for slot, name in enumerate(activity_names):
            time_str = _time_for_slot(slot)
            activity = Activity(
                id=f"{day}-{slot + 1}",
                name=name,
                location=current_city,
                time=time_str,
                duration="2시간",
                description=f"{current_city}에서 즐기는 {base_theme} 일정입니다.",
                image="/default-activity.jpg",
                openHours="알 수 없음",
                price="알 수 없음",
                tips=[f"{current_city}에서 여유롭게 시간을 보내세요."],
                nearbyFood=[f"{current_city} 로컬 음식"],
                estimatedDuration="2시간",
                bestTime="오후",
            )
            day_activities.append(activity)
            locations.append(
                Location(
                    name=name,
                    time=time_str,
                    lat=lat + slot * 0.01,
                    lng=lng + slot * 0.01,
                )
            )

        overview.append(
            DayItinerary(
                day=day,
                date=day_date,
                title=f"{current_city} 탐험 Day {day}",
                photo="/city-arrival.jpg",
                activities=[a.name for a in day_activities],
                locations=locations,
            )
        )
        activities_by_day[str(day)] = day_activities

    return overview, activities_by_day
