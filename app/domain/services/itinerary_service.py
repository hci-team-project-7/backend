from __future__ import annotations

from datetime import datetime, timedelta
import re
from typing import Dict, List
from uuid import uuid4

from app.ai.itinerary_graph import _coords_for, generate_itinerary
from app.api.models.schemas import Activity, ChatChange, DayItinerary, Location, PlannerData, TransportLeg
from app.core.errors import ValidationError
from app.domain.models import ItineraryEntity
from app.domain.repositories import ItineraryRepository
from app.external.routes_api import compute_route_segments


class ItineraryService:
    def __init__(self, repo: ItineraryRepository):
        self.repo = repo
        self._MODE_LABEL = {"drive": "자동차", "walk": "도보", "transit": "대중교통", "bike": "자전거"}

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

    async def apply_changes(self, itinerary_id: str, changes: List[ChatChange]) -> tuple[ItineraryEntity, str]:
        entity = await self.repo.get(itinerary_id)
        summary = self._summarize_changes(changes)
        await self._apply_change_set(entity, changes)
        await self.repo.update(entity)
        return entity, summary

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
        affected_days: set[str] = set()
        day_modes: Dict[str, str] = {}
        regenerated_days: set[str] = set()
        day_segments: Dict[str, List[Dict[str, int | str]]] = {}
        day_locations: Dict[str, List[Location]] = {}
        segment_mode_overrides: Dict[str, Dict[int, str]] = {}

        for change in changes:
            day = change.day or 1
            day_key = str(day)
            affected_days.add(day_key)
            if day_key not in entity.activities_by_day:
                entity.activities_by_day[day_key] = []
            activities = entity.activities_by_day[day_key]
            mutated = False

            if change.action == "remove":
                self._remove_activity(activities, change.location)
                mutated = True
            elif change.action == "add":
                insert_at = self._find_insert_position(activities, change)
                new_activity = self._build_new_activity(day, len(activities) + 1, change)
                if insert_at is not None and 0 <= insert_at <= len(activities):
                    activities.insert(insert_at, new_activity)
                else:
                    activities.append(new_activity)
                mutated = True
            elif change.action == "modify":
                if self._modify_activity(activities, change):
                    mutated = True
                else:
                    activities.append(self._build_new_activity(day, len(activities) + 1, change))
                    mutated = True
            elif change.action == "transport":
                mode = _detect_mode(change.mode, change.details)
                if change.fromLocation or change.toLocation:
                    seg_idx = self._find_segment_index(activities, change.fromLocation, change.toLocation)
                    if seg_idx is not None:
                        segment_mode_overrides.setdefault(day_key, {})[seg_idx] = mode
                    else:
                        day_modes[day_key] = mode
                else:
                    day_modes[day_key] = mode
            elif change.action == "regenerate":
                await self._regenerate_day(entity, day)
                regenerated_days.add(day_key)
            elif change.action == "replace":
                if self._replace_activity(activities, change):
                    mutated = True
                else:
                    # 찾지 못한 경우 새 활동으로 추가하여 일정이 비지 않도록 처리
                    insert_at = self._find_insert_position(activities, change)
                    new_activity = self._build_new_activity(day, len(activities) + 1, change)
                    if insert_at is not None and 0 <= insert_at <= len(activities):
                        activities.insert(insert_at, new_activity)
                    else:
                        activities.append(new_activity)
                    mutated = True
            if mutated:
                self._reindex_day_activities(day, activities)

        # Recompute only affected days (skip regenerated days which already include timing)
        for day_key in affected_days:
            if day_key in regenerated_days:
                continue
            activities = entity.activities_by_day.get(day_key, [])
            existing_mode = self._extract_current_mode(entity, day_key)
            mode = day_modes.get(day_key, existing_mode or entity.planner_data.transportMode)
            seg_overrides = segment_mode_overrides.get(day_key)
            segments, locations = await self._recompute_day_schedule(
                entity, day_key, activities, mode, seg_overrides
            )
            day_segments[day_key] = segments
            day_locations[day_key] = locations

        await self._sync_overview(
            entity,
            affected_days=affected_days,
            day_modes=day_modes,
            segments_by_day=day_segments,
            locations_by_day=day_locations,
        )

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

    def _find_insert_position(self, activities: List[Activity], change: ChatChange) -> int | None:
        """
        Try to place a newly added activity right after a related one (e.g., '신주쿠 교엔 방문 후 추가됨').
        """
        if change.afterActivityName:
            anchor_lower = change.afterActivityName.casefold()
            for idx, act in enumerate(activities):
                if anchor_lower in act.name.casefold() or anchor_lower in act.location.casefold():
                    return idx + 1
        anchors: List[str] = []
        if change.details:
            anchors.append(change.details)
            match = re.search(r"(.+?)(?:을|를)?\s*방문\s*후", change.details)
            if match:
                anchors.append(match.group(1))
        for anchor in anchors:
            anchor_lower = anchor.casefold().strip()
            if not anchor_lower:
                continue
            for idx, act in enumerate(activities):
                if anchor_lower in act.name.casefold() or anchor_lower in act.location.casefold():
                    return idx + 1
        return None

    def _replace_activity(self, activities: List[Activity], change: ChatChange) -> bool:
        target_label = change.targetLocation or change.fromLocation or change.location
        if not target_label:
            return False
        target = target_label.casefold()
        new_name = change.location or target_label
        for act in activities:
            if target in act.name.casefold() or target in act.location.casefold():
                act.name = new_name
                act.location = new_name
                if change.details:
                    act.description = change.details
                if change.address:
                    act.description = f"{change.address} · {act.description or '업데이트된 장소입니다.'}"
                if change.lat is not None:
                    act.lat = change.lat
                if change.lng is not None:
                    act.lng = change.lng
                return True
        return False

    def _find_segment_index(self, activities: List[Activity], from_location: str | None, to_location: str | None) -> int | None:
        if not from_location or not to_location:
            return None
        from_lower = from_location.casefold()
        to_lower = to_location.casefold()
        for idx in range(len(activities) - 1):
            curr = activities[idx]
            nxt = activities[idx + 1]
            if (from_lower in curr.name.casefold() or from_lower in curr.location.casefold()) and (
                to_lower in nxt.name.casefold() or to_lower in nxt.location.casefold()
            ):
                return idx
        return None

    def _build_new_activity(self, day: int, idx: int, change: ChatChange) -> Activity:
        location_name = change.location or "새로운 장소"
        desc = change.details or change.address or "추가된 활동입니다."
        return Activity(
            id=f"{day}-{idx}",
            name=location_name,
            location=location_name,
            lat=change.lat,
            lng=change.lng,
            time="18:00",
            duration="2시간",
            description=desc,
            image="/default-activity.jpg",
            openHours="알 수 없음",
            price="알 수 없음",
            tips=[],
            nearbyFood=[],
            estimatedDuration="2시간",
            bestTime="오후",
        )

    def _reindex_day_activities(self, day: int, activities: List[Activity]) -> None:
        for idx, act in enumerate(activities, start=1):
            act.id = f"{day}-{idx}"

    def _extract_current_mode(self, entity: ItineraryEntity, day_key: str) -> str | None:
        for item in entity.overview:
            if str(item.day) == day_key and item.transports:
                return item.transports[0].mode
        return None

    def _location_coord_lookup(self, entity: ItineraryEntity, day_key: str) -> Dict[str, tuple[float, float]]:
        existing = next((item for item in entity.overview if str(item.day) == day_key), None)
        if not existing:
            return {}
        return {loc.name.casefold(): (loc.lat, loc.lng) for loc in existing.locations or []}

    def _build_locations(
        self, activities: List[Activity], coords_by_name: Dict[str, tuple[float, float]]
    ) -> List[Location]:
        locations: List[Location] = []
        for act in activities:
            coords = coords_by_name.get(act.name.casefold())
            lat = act.lat
            lng = act.lng
            if coords:
                lat, lng = coords
            elif lat is None or lng is None:
                lat, lng = _coords_for(act.location)
            locations.append(Location(name=act.name, time=act.time, lat=lat, lng=lng))
        return locations

    async def _recompute_day_schedule(
        self,
        entity: ItineraryEntity,
        day_key: str,
        activities: List[Activity],
        mode: str | None = "drive",
        segment_mode_overrides: Dict[int, str] | None = None,
    ) -> tuple[List[Dict[str, int | str]], List[Location]]:
        current_minutes = 8 * 60  # 08:00 시작
        coords_by_name = self._location_coord_lookup(entity, day_key)
        for act in activities:
            act.time = f"{current_minutes // 60:02d}:{current_minutes % 60:02d}"
            dwell = _duration_to_minutes(act.duration or act.estimatedDuration, 90)
            current_minutes += dwell
        routing_locations = self._build_locations(activities, coords_by_name)
        segments: List[Dict[str, int | str]] = []
        if len(routing_locations) > 1:
            per_segment_modes: List[str] = []
            overrides = segment_mode_overrides or {}
            for idx in range(len(routing_locations) - 1):
                per_segment_modes.append(overrides.get(idx, mode or "drive"))

            segments = await compute_route_segments(routing_locations, mode or "drive", modes_by_index=per_segment_modes)
            durations = [int(seg.get("durationMinutes", 0) or 0) for seg in segments]
            current_minutes = 8 * 60
            for idx, act in enumerate(activities):
                act.time = f"{current_minutes // 60:02d}:{current_minutes % 60:02d}"
                dwell = _duration_to_minutes(act.duration or act.estimatedDuration, 90)
                travel = durations[idx] if idx < len(durations) else 0
                current_minutes += dwell + travel
        final_locations = self._build_locations(activities, coords_by_name)
        return segments, final_locations

    async def _regenerate_day(self, entity: ItineraryEntity, day: int) -> None:
        """
        Regenerate a specific day by re-running the itinerary generator and swapping that day only.
        """
        day_key = str(day)
        overview, activities_by_day = await generate_itinerary(entity.planner_data)
        if day_key not in activities_by_day:
            return

        entity.activities_by_day[day_key] = activities_by_day[day_key]
        new_overview_by_day: Dict[str, DayItinerary] = {str(item.day): item for item in overview}
        if day_key in new_overview_by_day:
            replaced = False
            for idx, item in enumerate(entity.overview):
                if item.day == day:
                    entity.overview[idx] = new_overview_by_day[day_key]
                    replaced = True
                    break
            if not replaced:
                entity.overview.append(new_overview_by_day[day_key])

    async def _sync_overview(
        self,
        entity: ItineraryEntity,
        affected_days: set[str] | None = None,
        day_modes: Dict[str, str] | None = None,
        segments_by_day: Dict[str, List[Dict[str, int | str]]] | None = None,
        locations_by_day: Dict[str, List[Location]] | None = None,
    ) -> None:
        overview_by_day: Dict[str, DayItinerary] = {
            str(item.day): item for item in entity.overview
        }
        all_days = set(entity.activities_by_day.keys()) | set(overview_by_day.keys())
        mode_overrides = day_modes or {}
        for day_key in all_days:
            if affected_days is not None and day_key not in affected_days:
                continue
            activities = entity.activities_by_day.get(day_key, [])
            coords_by_name = self._location_coord_lookup(entity, day_key)
            locations = locations_by_day.get(day_key) if locations_by_day else None
            if locations is None:
                locations = self._build_locations(activities, coords_by_name)
            mode = mode_overrides.get(day_key, entity.planner_data.transportMode or "drive")
            segments = segments_by_day.get(day_key) if segments_by_day is not None else None
            if segments is None and len(locations) > 1:
                segments = await compute_route_segments(locations, mode or "drive")
            segments = segments or []
            transports: List[TransportLeg] = []
            for idx, seg in enumerate(segments):
                if idx >= len(activities) - 1:
                    break
                travel = int(seg.get("durationMinutes", 0) or 0)
                distance = int(seg.get("distanceMeters", 0) or 0)
                seg_mode = str(seg.get("mode", mode or "drive")) if isinstance(seg, dict) else (mode or "drive")
                transports.append(
                    TransportLeg(
                        fromActivityId=activities[idx].id,
                        toActivityId=activities[idx + 1].id,
                        mode=seg_mode,
                        durationMinutes=travel,
                        distanceMeters=distance,
                        summary=f"{self._MODE_LABEL.get(seg_mode, '이동')} 이동 {travel}분",
                    )
                )
            if day_key in overview_by_day:
                item = overview_by_day[day_key]
                item.activities = [act.name for act in activities]
                item.locations = locations
                item.transports = transports
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
                    transports=transports,
                )
        entity.overview = [overview_by_day[key] for key in sorted(overview_by_day.keys(), key=int)]

    def _summarize_changes(self, changes: List[ChatChange]) -> str:
        if not changes:
            return "선택하신 변경사항을 일정에 반영했습니다."
        summaries: List[str] = []
        for change in changes:
            day_label = f"Day {change.day}" if change.day else "해당 일차"
            if change.action == "add":
                target = change.location or "새 일정"
                summaries.append(f"{day_label}: {target} 추가")
            elif change.action == "remove":
                target = change.location or "일정"
                summaries.append(f"{day_label}: {target} 제거")
            elif change.action == "modify":
                target = change.location or "일정"
                summaries.append(f"{day_label}: {target} 세부정보 수정")
            elif change.action == "transport":
                mode_label = self._MODE_LABEL.get(_detect_mode(change.mode, change.details), "이동")
                if change.fromLocation and change.toLocation:
                    summaries.append(f"{day_label}: {change.fromLocation}→{change.toLocation} {mode_label} 변경")
                else:
                    summaries.append(f"{day_label}: 이동수단 {mode_label} 변경")
            elif change.action == "regenerate":
                summaries.append(f"{day_label}: 일정 재생성")
            elif change.action == "replace":
                src = change.targetLocation or change.fromLocation or "기존 일정"
                dest = change.location or "새 일정"
                summaries.append(f"{day_label}: {src}을 {dest}(으)로 교체")
        return " · ".join(summaries) if summaries else "선택하신 변경사항을 일정에 반영했습니다."


def _duration_to_minutes(text: str | None, default: int = 60) -> int:
    if not text:
        return default
    try:
        lowered = text.lower()
    except Exception:
        return default
    hours = 0
    minutes = 0
    try:
        hour_match = re.search(r"(\d+(?:\.\d+)?)\s*(시간|hour|hr|h)", lowered)
        if hour_match:
            hours = float(hour_match.group(1))
        minute_match = re.search(r"(\d+)\s*(분|minute|min)", lowered)
        if minute_match:
            minutes = int(minute_match.group(1))
        if not hour_match and not minute_match:
            number_match = re.search(r"(\d+(?:\.\d+)?)", lowered)
            if number_match:
                minutes = float(number_match.group(1))
                if minutes <= 8:
                    hours = minutes
                    minutes = 0
        total = int(hours * 60 + minutes)
        return total if total > 0 else default
    except Exception:
        return default


def _detect_mode(mode: str | None, details: str | None) -> str:
    if mode:
        return str(mode)
    if not details:
        return "drive"
    lowered = details.lower()
    if "walk" in lowered or "도보" in lowered:
        return "walk"
    if "bike" in lowered or "자전거" in lowered:
        return "bike"
    if (
        "bus" in lowered
        or "버스" in lowered
        or "지하철" in lowered
        or "전철" in lowered
        or "metro" in lowered
        or "트램" in lowered
        or "transit" in lowered
        or "대중" in lowered
    ):
        return "transit"
    return "drive"
