from __future__ import annotations

import hashlib
import json
import logging
from datetime import timedelta
from typing import Any, Dict, List, Tuple, TypedDict

from langgraph.graph import END, StateGraph

from app.ai.openai_client import get_client
from app.api.models.schemas import Activity, DayItinerary, Location, PlannerData
from app.core.config import settings
from app.external.crawl4ai_client import fetch_poi_snippets
from app.external.google_places_api import search_places_for_planner
from app.external.routes_api import compute_route_durations

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
                "name, city, type, styleScore (0-10), lat, lng, highlight."
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
    start_date = planner.dateRange.start
    num_days = (planner.dateRange.end - start_date).days + 1

    day_plans: List[DayItinerary] = []
    activities_by_day: Dict[str, List[Activity]] = {}

    idx = 0
    for day in range(1, num_days + 1):
        day_pois = pois[idx : idx + 3] or pois[:3]
        idx += 3
        locations: List[Location] = []
        activities: List[Activity] = []
        city = day_pois[0].get("city") if day_pois else (planner.cities[0] if planner.cities else planner.country)
        for slot, poi in enumerate(day_pois):
            lat = poi.get("lat")
            lng = poi.get("lng")
            if lat is None or lng is None:
                lat, lng = _coords_for(poi.get("name", city or "location"))
            time_str = _time_for_slot(slot)
            locations.append(Location(name=poi.get("name", "명소"), time=time_str, lat=lat, lng=lng))
            activities.append(
                Activity(
                    id=f"{day}-{slot + 1}",
                    name=poi.get("name", "명소"),
                    location=poi.get("city", city) or planner.country,
                    time=time_str,
                    duration="2시간",
                    description=poi.get("highlight") or f"{planner.country}에서 즐기는 일정입니다.",
                    image="/default-activity.jpg",
                    openHours="알 수 없음",
                    price="알 수 없음",
                    tips=[],
                    nearbyFood=[],
                    estimatedDuration="2시간",
                    bestTime="오후",
                )
            )

        day_date = start_date + timedelta(days=day - 1)
        day_plans.append(
            DayItinerary(
                day=day,
                date=day_date,
                title=f"{city} 탐험 Day {day}",
                photo="/city-arrival.jpg",
                activities=[a.name for a in activities],
                locations=locations,
            )
        )
        activities_by_day[str(day)] = activities
    return {"day_plans": day_plans, "activities_by_day": activities_by_day}


async def enrich_with_routes(state: ItineraryState) -> Dict[str, Any]:
    day_plans = state["day_plans"]
    activities_by_day = state["activities_by_day"]

    for plan in day_plans:
        durations = await compute_route_durations(plan.locations)
        current_minutes = 9 * 60
        for idx, location in enumerate(plan.locations):
            time_str = f"{current_minutes // 60:02d}:{current_minutes % 60:02d}"
            location.time = time_str
            activities = activities_by_day.get(str(plan.day), [])
            if idx < len(activities):
                activities[idx].time = time_str
            if idx < len(durations):
                current_minutes += durations[idx] + 60  # dwell time + travel
        plan.activities = [act.name for act in activities_by_day.get(str(plan.day), [])]
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
                    "tips(list), nearbyFood(list), estimatedDuration, bestTime."
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
