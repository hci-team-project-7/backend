from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import timedelta
from typing import Any, Dict, List, Tuple, TypedDict

from langgraph.graph import END, StateGraph

from app.ai.openai_client import get_client
from app.ai.translation import translate_text_to_korean
from app.api.models.schemas import Activity, DayItinerary, Location, PlannerData, TransportLeg, TransportMode
from app.core.config import settings
from app.external.crawl4ai_client import fetch_poi_snippets
from app.external.google_places_api import search_places_for_planner
from app.external.routes_api import compute_route_segments

logger = logging.getLogger(__name__)


class ItineraryState(TypedDict):
    planner_data: PlannerData
    candidate_pois: List[Dict[str, Any]]
    day_plans: List[DayItinerary]
    activities_by_day: Dict[str, List[Activity]]


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
    digest = hashlib.sha256(name.encode("utf-8")).digest()
    lat = 10 + (digest[0] / 255) * 70  # 10~80
    lng = -130 + (digest[1] / 255) * 260  # -130~130
    return round(lat, 4), round(lng, 4)


_MODE_LABEL = {"drive": "자동차", "walk": "도보", "transit": "대중교통", "bike": "자전거"}


def _to_travel_mode(mode: str) -> str:
    m = mode.lower()
    if m == "walk":
        return "WALK"
    if m == "transit":
        return "TRANSIT"
    if m == "bike":
        return "BICYCLE"
    return "DRIVE"


def _time_for_slot(slot: int) -> str:
    hour = 9 + slot * 3
    return f"{hour:02d}:00"


async def collect_pois(state: ItineraryState) -> Dict[str, Any]:
    planner = state["planner_data"]
    client = get_client()
    pois: List[Dict[str, Any]] = []

    # 1) Google Places 기반 후보
    try:
        google_pois = await search_places_for_planner(planner)
    except Exception as exc:  # pragma: no cover - network dependent
        logger.warning("Google Places lookup failed: %s", exc)
        google_pois = []

    seen: set[str] = set()
    for poi in google_pois:
        name = poi.get("name")
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        pois.append(poi)

    # 2) OpenAI로 추가 후보 확보 (필요 시)
    if client:
        try:
            system_prompt = (
                "You are a travel POI planner. "
                "Return JSON with a key 'pois' that is a list of objects with fields: "
                "name, city, type, styleScore (0-10), lat, lng, highlight. "
                "All descriptive text (name, city, highlight) should be in Korean; "
                "translate English content to natural Korean while keeping proper nouns readable."
            )
            user_prompt = (
                f"Country: {planner.country}\n"
                f"Cities: {', '.join(planner.cities)}\n"
                f"Date range: {planner.dateRange.start} to {planner.dateRange.end}\n"
                f"Travelers: adults={planner.travelers.adults}, children={planner.travelers.children}, type={planner.travelers.type}\n"
                f"Styles: {', '.join(planner.styles)}"
            )
            response = await client.chat.completions.create(
                model=settings.openai_model_itinerary,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
            )
            payload = json.loads(response.choices[0].message.content)
            llm_pois = payload.get("pois", [])
            for poi in llm_pois:
                name = poi.get("name")
                if not name:
                    continue
                key = name.casefold()
                if key in seen:
                    continue
                seen.add(key)
                pois.append(poi)
        except Exception as exc:  # pragma: no cover - network dependent
            logger.warning("OpenAI POI collection failed, falling back to heuristic: %s", exc)

    # 3) 부족할 경우 휴리스틱 보완
    if not pois:
        pois = _fallback_candidate_pois(planner)
    elif len(pois) < 6:
        for poi in _fallback_candidate_pois(planner):
            name = poi.get("name")
            if not name or name.casefold() in seen:
                continue
            seen.add(name.casefold())
            pois.append(poi)

    return {"candidate_pois": pois}


async def score_and_filter_pois(state: ItineraryState) -> Dict[str, Any]:
    planner = state["planner_data"]
    pois = state["candidate_pois"]
    if not pois:
        pois = _fallback_candidate_pois(planner)

    styles = planner.styles or []
    for poi in pois:
        name = poi.get("name", "")
        score = poi.get("styleScore", 0)
        for style in styles:
            if style.lower() in name.lower():
                score += 2
        poi["styleScore"] = score

    num_days = (planner.dateRange.end - planner.dateRange.start).days + 1
    max_items = max(num_days * 4, 6)
    filtered = sorted(pois, key=lambda x: x.get("styleScore", 0), reverse=True)[:max_items]
    return {"candidate_pois": filtered}


async def schedule_days(state: ItineraryState) -> Dict[str, Any]:
    planner = state["planner_data"]
    pois = state["candidate_pois"]
    if not pois:
        pois = _fallback_candidate_pois(planner)
    transport_mode: TransportMode = (planner.transportMode or "drive")  # type: ignore[assignment]
    start_date = planner.dateRange.start
    num_days = (planner.dateRange.end - start_date).days + 1

    day_plans: List[DayItinerary] = []
    activities_by_day: Dict[str, List[Activity]] = {}

    idx = 0

    async def _activity_from_poi(
        day: int, slot: int, poi: Dict[str, Any], default_city: str, duration_min: int
    ) -> Tuple[Activity, Tuple[float, float]]:
        name = poi.get("name", "추천 명소")
        city = poi.get("city") or default_city
        lat = poi.get("lat")
        lng = poi.get("lng")
        if lat is None or lng is None:
            lat, lng = _coords_for(name)
        highlight = poi.get("highlight") or f"{city}에서 즐기는 추천 일정입니다."
        highlight = highlight if isinstance(highlight, str) else str(highlight)
        description = await translate_text_to_korean(highlight)
        activity = Activity(
            id=f"{day}-{slot}",
            name=name,
            location=city,
            lat=lat,
            lng=lng,
            time="00:00",  # 초기값, 이후 enrich_with_routes에서 실제 시간 재계산
            duration=f"{duration_min}분",
            description=description,
            image="/default-activity.jpg",
            openHours="알 수 없음",
            price="알 수 없음",
            tips=[f"{name} 방문 전 운영시간을 확인하세요."],
            nearbyFood=[f"{city} 로컬 맛집"],
            estimatedDuration=f"{duration_min}분",
            bestTime="오전" if slot < 3 else "오후",
        )
        return activity, (lat, lng)

    def _meal_activity(day: int, slot: int, city: str, label: str, duration_min: int, best_time: str) -> Activity:
        lat, lng = _coords_for(city)
        return Activity(
            id=f"{day}-{slot}",
            name=f"{label} - {city}",
            location=city,
            lat=lat,
            lng=lng,
            time="00:00",
            duration=f"{duration_min}분",
            description=f"{city}에서 즐기는 {label.lower()} 시간입니다.",
            image="/default-activity.jpg",
            openHours="알 수 없음",
            price="알 수 없음",
            tips=[f"{city} 로컬 맛집을 미리 찾아두면 좋아요."],
            nearbyFood=[f"{city} 로컬 식당"],
            estimatedDuration=f"{duration_min}분",
            bestTime=best_time,
        )

    def _cafe_break_activity(day: int, slot: int, city: str) -> Activity:
        lat, lng = _coords_for(city)
        return Activity(
            id=f"{day}-{slot}",
            name=f"{city} 카페 휴식",
            location=city,
            lat=lat,
            lng=lng,
            time="00:00",
            duration="60분",
            description=f"{city}에서 여유롭게 커피 한 잔하며 쉬어가는 시간입니다.",
            image="/default-activity.jpg",
            openHours="알 수 없음",
            price="알 수 없음",
            tips=["카페에서 와이파이와 콘센트 유무를 확인하세요."],
            nearbyFood=[f"{city} 디저트 카페"],
            estimatedDuration="60분",
            bestTime="오후",
        )

    for day in range(1, num_days + 1):
        day_pois = pois[idx : idx + 4] or pois[:4] or _fallback_candidate_pois(planner)
        idx += 4
        city = day_pois[0].get("city") if day_pois else (planner.cities[0] if planner.cities else planner.country)
        activities: List[Activity] = []
        activity_coords: List[Tuple[float, float]] = []

        # 1) 아침 식사
        activities.append(_meal_activity(day, len(activities) + 1, city, "아침 식사", 60, "아침"))
        activity_coords.append(_coords_for(city))

        # 2) 오전 주요 명소 2곳
        for poi in day_pois[:2]:
            act, coords = await _activity_from_poi(day, len(activities) + 1, poi, city, 90)
            activities.append(act)
            activity_coords.append(coords)

        # 3) 점심
        activities.append(_meal_activity(day, len(activities) + 1, city, "점심 식사", 75, "점심"))
        activity_coords.append(_coords_for(city))

        # 4) 오후 명소 1~2곳
        for poi in day_pois[2:4]:
            act, coords = await _activity_from_poi(day, len(activities) + 1, poi, city, 120)
            activities.append(act)
            activity_coords.append(coords)

        # 5) 카페/휴식
        activities.append(_cafe_break_activity(day, len(activities) + 1, city))
        activity_coords.append(_coords_for(city))

        # 6) 저녁 활동이 부족하면 도심 산책 추가
        if len(activities) < 7:
            activities.append(
                Activity(
                    id=f"{day}-{len(activities) + 1}",
                    name=f"{city} 야경 산책",
                    location=city,
                    time="00:00",
                    duration="90분",
                    description=f"{city}의 저녁 풍경을 즐기며 걷는 시간입니다.",
                    image="/default-activity.jpg",
                    openHours="항상",
                    price="무료",
                    tips=[f"{city} 야경 명소를 미리 확인하세요."],
                    nearbyFood=[f"{city} 길거리 음식"],
                    estimatedDuration="90분",
                    bestTime="저녁",
                )
            )
            activity_coords.append(_coords_for(city))

        # 7) 저녁 식사
        activities.append(_meal_activity(day, len(activities) + 1, city, "저녁 식사", 90, "저녁"))
        activity_coords.append(_coords_for(city))

        locations: List[Location] = []
        for act, coords in zip(activities, activity_coords):
            lat, lng = coords
            locations.append(Location(name=act.name, time=act.time, lat=lat, lng=lng))

        day_date = start_date + timedelta(days=day - 1)
        day_plans.append(
            DayItinerary(
                day=day,
                date=day_date,
                title=f"{day}일차 {city} 일정",
                photo="/city-arrival.jpg",
                activities=[a.name for a in activities],
                locations=locations,
            )
        )
        activities_by_day[str(day)] = activities
    return {"day_plans": day_plans, "activities_by_day": activities_by_day}


async def enrich_with_routes(state: ItineraryState) -> Dict[str, Any]:
    planner = state["planner_data"]
    mode: TransportMode = (planner.transportMode or "drive")  # type: ignore[assignment]
    api_mode = _to_travel_mode(mode)
    day_plans = state["day_plans"]
    activities_by_day = state["activities_by_day"]

    for plan in day_plans:
        segments = await compute_route_segments(plan.locations, api_mode)
        # 길이가 부족하면 기본 이동시간(30분)으로 채움
        if len(segments) < max(0, len(plan.locations) - 1):
            segments.extend(
                [{"mode": "drive", "durationMinutes": 30, "distanceMeters": 2000}]
                * (len(plan.locations) - 1 - len(segments))
            )
        current_minutes = 8 * 60  # 08:00 시작
        activities = activities_by_day.get(str(plan.day), [])
        transports: List[TransportLeg] = []
        for idx, location in enumerate(plan.locations):
            time_str = f"{current_minutes // 60:02d}:{current_minutes % 60:02d}"
            location.time = time_str
            dwell = 90
            if idx < len(activities):
                activities[idx].time = time_str
                dwell = _duration_to_minutes(activities[idx].duration or activities[idx].estimatedDuration, 90)
                if not activities[idx].duration:
                    activities[idx].duration = f"{dwell}분"
                if not activities[idx].estimatedDuration:
                    activities[idx].estimatedDuration = f"{dwell}분"
            travel = segments[idx]["durationMinutes"] if idx < len(segments) else 30
            distance = segments[idx]["distanceMeters"] if idx < len(segments) else 0
            mode = str(segments[idx].get("mode", "drive")) if idx < len(segments) else "drive"
            if idx < len(activities) - 1:
                transports.append(
                    TransportLeg(
                        fromActivityId=activities[idx].id,
                        toActivityId=activities[idx + 1].id,
                        mode=mode if mode else "drive",
                        durationMinutes=int(travel) if travel else 0,
                        distanceMeters=int(distance) if distance else 0,
                        summary=f"{_MODE_LABEL.get(mode, '이동')} 이동 {int(travel) if travel else 0}분",
                    )
                )
            current_minutes += dwell + travel
        plan.activities = [act.name for act in activities]
        plan.transports = transports
    return {"day_plans": day_plans, "activities_by_day": activities_by_day}


async def enrich_with_details(state: ItineraryState) -> Dict[str, Any]:
    planner = state["planner_data"]
    client = get_client()
    for plan in state["day_plans"]:
        activities = state["activities_by_day"].get(str(plan.day), [])
        if not activities:
            continue

        snippets: List[str] = []
        for act in activities:
            fetched = await fetch_poi_snippets(f"{act.name} {act.location}")
            snippets.extend(fetched)
        snippet_text = "\n".join(snippets[:3])

        enriched = False
        if client:
            try:
                system_prompt = (
                    "You are a travel planner. "
                    "Return JSON with key 'activities' as a list matching the provided names with "
                    "fields: name, location, time, duration, description, image, openHours, price, "
                    "tips(list), nearbyFood(list), estimatedDuration, bestTime. "
                    "All text should be in Korean; translate English inputs into natural Korean while "
                    "preserving proper nouns."
                )
                user_prompt = (
                    f"Country: {planner.country}\n"
                    f"Cities: {', '.join(planner.cities)}\n"
                    f"Styles: {', '.join(planner.styles)}\n"
                    f"Day {plan.day} locations: {[a.name for a in activities]}\n"
                    f"Snippets:\n{snippet_text}"
                )
                resp = await client.chat.completions.create(
                    model=settings.openai_model_itinerary,
                    messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                    response_format={"type": "json_object"},
                )
                payload = json.loads(resp.choices[0].message.content)
                llm_activities = payload.get("activities", [])
                for idx, llm_act in enumerate(llm_activities):
                    if idx >= len(activities):
                        break
                    activities[idx] = Activity.model_validate(
                        {
                            **llm_act,
                            "id": activities[idx].id,
                            "time": activities[idx].time,
                            "location": llm_act.get("location") or activities[idx].location,
                        }
                    )
                enriched = True
            except Exception as exc:  # pragma: no cover - network dependent
                logger.warning("OpenAI activity enrichment failed: %s", exc)

        if not enriched:
            for act in activities:
                detail_suffix = snippet_text[:120] if snippet_text else f"{act.name}에 대한 짧은 설명을 추가했습니다."
                act.description = f"{planner.country} 여행 스타일({', '.join(planner.styles)})에 맞춘 추천 활동입니다. {detail_suffix}"
                act.tips = [f"{act.name} 방문 전 현지 상황을 확인하세요."]
                act.nearbyFood = [f"{act.location} 로컬 맛집"]
                act.price = act.price or "알 수 없음"
                act.estimatedDuration = act.estimatedDuration or "2시간"
                act.bestTime = act.bestTime or "오후"

        state["activities_by_day"][str(plan.day)] = activities
        plan.activities = [a.name for a in activities]
    return state


def build_itinerary_graph():
    builder = StateGraph(ItineraryState)
    builder.add_node("collect_pois", collect_pois)
    builder.add_node("score_and_filter_pois", score_and_filter_pois)
    builder.add_node("schedule_days", schedule_days)
    builder.add_node("enrich_with_routes", enrich_with_routes)
    builder.add_node("enrich_with_details", enrich_with_details)

    builder.set_entry_point("collect_pois")
    builder.add_edge("collect_pois", "score_and_filter_pois")
    builder.add_edge("score_and_filter_pois", "schedule_days")
    builder.add_edge("schedule_days", "enrich_with_routes")
    builder.add_edge("enrich_with_routes", "enrich_with_details")
    builder.add_edge("enrich_with_details", END)
    return builder.compile()


_GRAPH = build_itinerary_graph()


def _fallback_candidate_pois(planner_data: PlannerData) -> List[Dict[str, Any]]:
    pois: List[Dict[str, Any]] = []
    styles = planner_data.styles or ["culture"]
    for city in planner_data.cities or [planner_data.country]:
        for style in styles:
            lat, lng = _coords_for(city)
            pois.append(
                {
                    "name": f"{city} {style} 명소",
                    "city": city,
                    "type": style,
                    "styleScore": 7,
                    "lat": lat,
                    "lng": lng,
                    "highlight": f"{city}에서 즐기는 {style} 활동",
                }
            )
    return pois


def _duration_to_minutes(text: str | None, default: int = 90) -> int:
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


async def generate_itinerary(planner_data: PlannerData) -> Tuple[List[DayItinerary], Dict[str, List[Activity]]]:
    """
    Run the LangGraph-powered itinerary generator with graceful fallbacks.
    """
    initial_state: ItineraryState = {
        "planner_data": planner_data,
        "candidate_pois": [],
        "day_plans": [],
        "activities_by_day": {},
    }
    try:
        result = await _GRAPH.ainvoke(initial_state)
        return result["day_plans"], result["activities_by_day"]
    except Exception as exc:  # pragma: no cover - ensures API still works without graph
        logger.exception("Itinerary graph failed, falling back to heuristic generator: %s", exc)
        fallback_state = _fallback_candidate_pois(planner_data)
        planner_data.styles = planner_data.styles or ["culture"]
        initial_state["candidate_pois"] = fallback_state
        after_schedule = await schedule_days(initial_state)  # type: ignore[arg-type]
        return after_schedule["day_plans"], after_schedule["activities_by_day"]
