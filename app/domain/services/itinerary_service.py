from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List
from uuid import uuid4

from app.ai.itinerary_graph import _coords_for, generate_itinerary
from app.api.models.schemas import Activity, ChatChange, DayItinerary, Location, PlannerData
from app.core.errors import ValidationError
from app.domain.models import ItineraryEntity
from app.domain.repositories import ItineraryRepository
from app.external.routes_api import compute_route_durations


class ItineraryService:
    def __init__(self, repo: ItineraryRepository):
        self.repo = repo

    async def create_itinerary(self, planner_data: PlannerData) -> ItineraryEntity:
        self._validate_planner_data(planner_data)
        overview, activities_by_day = await generate_itinerary(planner_data)
        itinerary_id = f"itn_{uuid4().hex[:12]}"
        now = datetime.utcnow()
        entity = ItineraryEntity(
            id=itinerary_id,
            planner_data=planner_data,
            overview=overview,
            activities_by_day=activities_by_day,
            created_at=now,
            updated_at=now,
        )
        await self.repo.save(entity)
        return entity

    async def get_itinerary(self, itinerary_id: str) -> ItineraryEntity:
        return await self.repo.get(itinerary_id)

    async def apply_changes(self, itinerary_id: str, changes: List[ChatChange]) -> ItineraryEntity:
        entity = await self.repo.get(itinerary_id)
        await self._apply_change_set(entity, changes)
        await self._sync_overview(entity)
        await self.repo.update(entity)
        return entity

    def _validate_planner_data(self, planner_data: PlannerData) -> None:
        if not planner_data.country:
            raise ValidationError("country is required", {"field": "plannerData.country", "reason": "필수 값입니다."})
        if not planner_data.dateRange or planner_data.dateRange.start > planner_data.dateRange.end:
            raise ValidationError(
                "dateRange is invalid",
                {"field": "plannerData.dateRange", "reason": "출발일과 도착일을 확인하세요."},
            )
        if planner_data.dateRange.start < datetime.utcnow().date():
            raise ValidationError(
                "start date must be today or later",
                {"field": "plannerData.dateRange.start", "reason": "출발일은 오늘 이후여야 합니다."},
            )
        if planner_data.travelers.adults < 1:
            raise ValidationError(
                "at least 1 adult is required",
                {"field": "plannerData.travelers.adults", "reason": "성인은 1명 이상이어야 합니다."},
            )
        if not planner_data.styles:
            raise ValidationError(
                "styles are required", {"field": "plannerData.styles", "reason": "최소 1개 이상의 스타일을 선택하세요."}
            )
        if not planner_data.cities:
            raise ValidationError(
                "at least one city is required",
                {"field": "plannerData.cities", "reason": "최소 1개 도시를 입력하세요."},
            )

    async def _apply_change_set(self, entity: ItineraryEntity, changes: List[ChatChange]) -> None:
        for change in changes:
            day = change.day or 1
            day_key = str(day)
            if day_key not in entity.activities_by_day:
                entity.activities_by_day[day_key] = []
            activities = entity.activities_by_day[day_key]

            if change.action == "remove":
                self._remove_activity(activities, change.location)
            elif change.action == "add":
                activities.append(self._build_new_activity(day, len(activities) + 1, change))
            elif change.action == "modify":
                if not self._modify_activity(activities, change):
                    activities.append(self._build_new_activity(day, len(activities) + 1, change))
            elif change.action == "transport":
                activities.append(
                    Activity(
                        id=f"{day}-{len(activities) + 1}",
                        name="이동 경로 업데이트",
                        location=change.location or "이동",
                        time="12:00",
                        duration="30분",
                        description=change.details or "이동 수단을 조정했습니다.",
                        image="/transport.jpg",
                        openHours="항상",
                        price="알 수 없음",
                        tips=["이동 시간을 충분히 확보하세요."],
                        nearbyFood=[],
                        estimatedDuration="30분",
                        bestTime="오전",
                    )
                )

        # Recompute times and locations after all changes
        for day_key, activities in entity.activities_by_day.items():
            await self._recompute_day_schedule(day_key, activities)

    def _remove_activity(self, activities: List[Activity], location: str | None) -> None:
        if not location:
            return
        target = location.casefold()
        for idx, act in enumerate(activities):
            if target in act.name.casefold() or target in act.location.casefold():
                activities.pop(idx)
                return

    def _modify_activity(self, activities: List[Activity], change: ChatChange) -> bool:
        if not change.location:
            return False
        target = change.location.casefold()
        for act in activities:
            if target in act.name.casefold() or target in act.location.casefold():
                act.description = change.details or act.description
                return True
        return False

    def _build_new_activity(self, day: int, idx: int, change: ChatChange) -> Activity:
        location_name = change.location or "새로운 장소"
        return Activity(
            id=f"{day}-{idx}",
            name=location_name,
            location=location_name,
            time="18:00",
            duration="2시간",
            description=change.details or "추가된 활동입니다.",
            image="/default-activity.jpg",
            openHours="알 수 없음",
            price="알 수 없음",
            tips=[],
            nearbyFood=[],
            estimatedDuration="2시간",
            bestTime="오후",
        )

    async def _recompute_day_schedule(self, day_key: str, activities: List[Activity]) -> None:
        hour = 9
        locations: List[Location] = []
        for act in activities:
            act.time = f"{hour:02d}:00"
            lat, lng = _coords_for(act.location)
            locations.append(
                Location(
                    name=act.name,
                    time=act.time,
                    lat=lat,
                    lng=lng,
                )
            )
            hour += 2
        if len(locations) > 1:
            durations = await compute_route_durations(locations)
            if durations:
                current_minutes = 9 * 60
                for idx, act in enumerate(activities):
                    act.time = f"{current_minutes // 60:02d}:{current_minutes % 60:02d}"
                    if idx < len(durations):
                        current_minutes += durations[idx] + 60  # 1h dwell + travel

    async def _sync_overview(self, entity: ItineraryEntity) -> None:
        overview_by_day: Dict[str, DayItinerary] = {
            str(item.day): item for item in entity.overview
        }
        for day_key, activities in entity.activities_by_day.items():
            locations = [
                Location(name=act.name, time=act.time, lat=_coords_for(act.location)[0], lng=_coords_for(act.location)[1])
                for act in activities
            ]
            if day_key in overview_by_day:
                item = overview_by_day[day_key]
                item.activities = [act.name for act in activities]
                item.locations = locations
            else:
                day = int(day_key)
                date = entity.planner_data.dateRange.start + timedelta(days=day - 1)
                overview_by_day[day_key] = DayItinerary(
                    day=day,
                    date=date,
                    title=f"Day {day} 일정",
                    photo="/city-arrival.jpg",
                    activities=[act.name for act in activities],
                    locations=locations,
                )
        entity.overview = [overview_by_day[key] for key in sorted(overview_by_day.keys(), key=int)]
