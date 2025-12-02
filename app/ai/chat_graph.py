from __future__ import annotations

import json
import logging
from datetime import datetime
from math import atan2, cos, radians, sin, sqrt
import re
from typing import Any, Dict, List, Optional, Tuple, TypedDict
from uuid import uuid4

from langgraph.graph import END, StateGraph

from app.ai.openai_client import get_client
from app.api.models.schemas import Activity, ChatChange, ChatContext, ChatMessage, ChatPreview, ChatReply, DayItinerary, PlannerData, TransportLeg
from app.core.config import settings
from app.domain.models import ItineraryEntity
from app.external.google_places_api import search_restaurants_near
from app.ai.itinerary_graph import _coords_for

logger = logging.getLogger(__name__)


class ChatState(TypedDict):
    planner_data: PlannerData
    itinerary_overview: List[DayItinerary]
    activities_by_day: Dict[str, List[Activity]]
    messages: List[ChatMessage]
    last_user_message: ChatMessage
    context: ChatContext
    assistant_reply: Optional[ChatReply]
    intent: Optional[str]
    target_day: Optional[int]


def _fallback_change_preview(day: int, activities: List[Activity]) -> ChatPreview:
    remove_target = activities[0].name if activities else "기존 일정"
    changes: List[ChatChange] = [
        ChatChange(action="remove", day=day, location=remove_target, details="이동 간격 확보"),
        ChatChange(action="add", day=day, location="카페 휴식", details="느긋하게 커피 한잔"),
    ]
    return ChatPreview(type="change", title=f"{day}일차 일정 조정 제안", changes=changes)


def _fallback_transport_preview(day: int, mode: str, from_loc: Optional[str] = None, to_loc: Optional[str] = None) -> ChatPreview:
    readable = {"walk": "도보", "transit": "대중교통", "bike": "자전거", "drive": "자동차"}.get(mode, "자동차")
    location_label = f"{from_loc} → {to_loc}" if from_loc and to_loc else "이동 경로"
    details = f"{location_label}을 {readable} 이동으로 변경" if from_loc and to_loc else f"{readable} 이동으로 변경"
    change = ChatChange(
        action="transport",
        day=day,
        location=location_label,
        details=details,
        mode=mode,  # type: ignore[arg-type]
        fromLocation=from_loc,
        toLocation=to_loc,
    )
    return ChatPreview(type="change", title=f"{day}일차 교통 수단 변경", changes=[change])


def _detect_mode_from_text(text: str) -> str:
    lowered = text.lower()
    if "walk" in lowered or "도보" in lowered:
        return "walk"
    if "bike" in lowered or "자전거" in lowered:
        return "bike"
    if (
        "bus" in lowered
        or "버스" in lowered
        or "subway" in lowered
        or "metro" in lowered
        or "지하철" in lowered
        or "전철" in lowered
        or "트램" in lowered
        or "대중" in lowered
        or "transit" in lowered
    ):
        return "transit"
    return "drive"


def _extract_day_from_text(text: str, default_day: int) -> int:
    for pattern in [r"(\d+)\s*일차", r"day\s*(\d+)"]:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            try:
                day_value = int(match.group(1))
                if day_value > 0:
                    return day_value
            except Exception:
                continue
    return default_day


def _find_activity_match(
    activities_by_day: Dict[str, List[Activity]], message_text: str, fallback_day: int
) -> Tuple[Optional[int], Optional[Activity]]:
    lowered = message_text.casefold()
    day_order = [str(fallback_day)] + [key for key in activities_by_day.keys() if key != str(fallback_day)]
    for day_key in day_order:
        for act in activities_by_day.get(day_key, []):
            if (act.name and act.name.casefold() in lowered) or (act.location and act.location.casefold() in lowered):
                return int(day_key), act
    return None, None


def _choose_fallback_activity(activities: List[Activity]) -> Optional[Activity]:
    for act in activities:
        label = act.name or ""
        if "식사" in label or "breakfast" in label.lower():
            continue
        return act
    return activities[0] if activities else None


def _lookup_activity_coords(overview: List[DayItinerary], day: int, name: str | None) -> Optional[Tuple[float, float]]:
    if not name:
        return None
    target = name.casefold()
    for item in overview:
        if item.day != day:
            continue
        for loc in item.locations or []:
            if target in loc.name.casefold():
                return loc.lat, loc.lng
    return None


def _haversine_distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> int:
    r = 6371000
    phi1, phi2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlambda = radians(lng2 - lng1)
    a = sin(dphi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(dlambda / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return int(r * c)


def _extract_segment_targets(
    message_text: str, activities_by_day: Dict[str, List[Activity]], default_day: int
) -> Tuple[Optional[str], Optional[str]]:
    lowered = message_text.casefold()
    day_key = str(default_day)
    acts = activities_by_day.get(day_key, [])
    candidates: List[Tuple[int, str]] = []
    for idx, act in enumerate(acts):
        name_lower = (act.name or "").casefold()
        loc_lower = (act.location or "").casefold()
        if name_lower and name_lower in lowered:
            candidates.append((idx, act.name))
            continue
        if loc_lower and loc_lower in lowered:
            candidates.append((idx, act.name))
    if len(candidates) < 2:
        return None, None
    candidates = sorted(candidates, key=lambda x: x[0])
    for i in range(len(candidates) - 1):
        if candidates[i + 1][0] - candidates[i][0] == 1:
            return candidates[i][1], candidates[i + 1][1]
    return candidates[0][1], candidates[1][1]


def _find_leg_between(
    overview: List[DayItinerary],
    activities_by_day: Dict[str, List[Activity]],
    first: Activity,
    second: Activity,
) -> Optional[Tuple[int, TransportLeg, Activity, Activity]]:
    first_key = first.name.casefold()
    second_key = second.name.casefold()
    for day_key, acts in activities_by_day.items():
        a_idx = None
        b_idx = None
        for idx, act in enumerate(acts):
            label = act.name.casefold()
            if a_idx is None and first_key in label:
                a_idx = idx
            if b_idx is None and second_key in label:
                b_idx = idx
        if a_idx is None or b_idx is None:
            continue
        if abs(a_idx - b_idx) > 1:
            continue
        leg_idx = min(a_idx, b_idx)
        overview_item = next((item for item in overview if str(item.day) == day_key), None)
        if overview_item and leg_idx < len(overview_item.transports):
            return int(day_key), overview_item.transports[leg_idx], acts[a_idx], acts[b_idx]
    return None


def _is_question_like(text: str) -> bool:
    """
    Heuristic to detect informational questions (e.g., '~이 뭐야?', '알려줘') to avoid forcing change previews.
    """
    lowered = text.lower()
    change_keywords = [
        "추가",
        "더해",
        "빼",
        "제거",
        "삭제",
        "없애",
        "교체",
        "변경",
        "바꿔",
        "재생성",
        "추천",
    ]
    if any(keyword in lowered for keyword in change_keywords):
        return False
    question_mark = "?" in text
    question_keywords = ["뭐야", "알려", "설명", "어때", "어떤가", "궁금", "어디", "how", "what", "tell me"]
    return question_mark or any(keyword in lowered for keyword in question_keywords)


def _classify_intent(message: ChatMessage, context: ChatContext) -> Tuple[str, int]:
    text_lower = message.text.lower()
    day = _extract_day_from_text(message.text, context.currentDay or 1)

    regen_keywords = ["재생성", "다시 짜", "다시짜", "다시 계획", "replan", "regen", "refresh", "다시 만들어"]
    transport_keywords = ["교통", "이동", "버스", "subway", "지하철", "대중", "transit", "metro", "트램"]
    restaurant_keywords = ["맛집", "식당", "레스토랑", "restaurant", "먹을", "카페"]

    if context.pendingAction in {"remove", "add", "replace"}:
        return "activity_change", day
    if context.pendingAction == "restaurant":
        return "restaurant", day
    if context.pendingAction == "transport":
        return "transport", day
    if any(kw in text_lower for kw in regen_keywords):
        return "regenerate", day
    if _is_question_like(message.text):
        return "question", day
    if any(kw in text_lower for kw in restaurant_keywords):
        return "restaurant", day
    if any(kw in text_lower for kw in transport_keywords):
        return "transport", day
    return "activity_change", day


async def _llm_classify_intent(message: ChatMessage, context: ChatContext, planner: PlannerData) -> Tuple[Optional[str], Optional[int]]:
    client = get_client()
    if not client:
        return None, None
    try:
        schema_prompt = (
            "Return JSON with keys 'intent' and 'day'. "
            "intent should be one of ['transport','restaurant','regenerate','activity_change','question']. "
            "Use 'question' only when the user is asking for information/clarification without requesting a change. "
            "Honor pending actions strongly: 'restaurant' -> restaurant, 'transport' -> transport, 'remove/add/replace' -> activity_change. "
            "day should be a positive integer if mentioned, otherwise null. "
            "Use the provided context to infer the day if the user references a day."
        )
        user_ctx = (
            f"User message: {message.text}\n"
            f"Current day: {context.currentDay}\n"
            f"Pending action: {context.pendingAction}\n"
            f"Cities: {planner.cities}\n"
        )
        resp = await client.chat.completions.create(
            model=settings.openai_model_chat,
            messages=[{"role": "system", "content": schema_prompt}, {"role": "user", "content": user_ctx}],
            response_format={"type": "json_object"},
        )
        payload = json.loads(resp.choices[0].message.content)
        intent = payload.get("intent")
        day = payload.get("day")
        if intent not in {"transport", "restaurant", "regenerate", "activity_change", "question"}:
            intent = None
        day_int: Optional[int] = None
        if isinstance(day, int) and day > 0:
            day_int = day
        return intent, day_int
    except Exception as exc:  # pragma: no cover - network dependent
        logger.warning("LLM intent classification failed: %s", exc)
        return None, None


def _build_rule_based_preview(
    message: ChatMessage,
    context: ChatContext,
    planner: PlannerData,
    activities_by_day: Dict[str, List[Activity]],
) -> Tuple[ChatPreview, str]:
    default_day = context.currentDay or 1
    day = _extract_day_from_text(message.text, default_day)
    city = planner.cities[0] if planner.cities else planner.country
    text_lower = message.text.lower()

    if context.pendingAction == "replace":
        target_day, anchor_act = _find_activity_match(activities_by_day, message.text, day)
        target_day = target_day or day
        anchor = anchor_act or _choose_fallback_activity(activities_by_day.get(str(target_day), []))
        anchor_name = anchor.name if anchor else city
        recommendations = [
            {
                "name": f"{city} 새로운 명소",
                "location": city,
                "rating": 4.6,
                "cuisine": "볼거리",
                "anchorActivityName": anchor_name,
                "isDemo": True,
                "source": "demo",
            },
            {
                "name": f"{city} 분위기 좋은 카페",
                "location": f"{city} 시내",
                "rating": 4.5,
                "cuisine": "카페",
                "anchorActivityName": anchor_name,
                "isDemo": True,
                "source": "demo",
            },
            {
                "name": f"{city} 정원 산책",
                "location": city,
                "rating": 4.4,
                "cuisine": "산책",
                "anchorActivityName": anchor_name,
                "isDemo": True,
                "source": "demo",
            },
        ]
        preview = ChatPreview(
            type="recommendation",
            title=f"{anchor_name} 대체 후보",
            recommendations=recommendations,
        )
        return preview, f"{anchor_name}을(를) 대신할 장소를 추천했어요. 마음에 드는 곳을 선택하거나 직접 입력해 주세요."

    if context.pendingAction == "restaurant" or "맛집" in text_lower or "restaurant" in text_lower:
        preview = ChatPreview(
            type="recommendation",
            title=f"{city} 맛집 추천",
            recommendations=[
                {
                    "name": f"{city} 로컬 레스토랑 A",
                    "location": f"{city} 시내",
                    "rating": 4.6,
                    "cuisine": "local",
                    "userRatingsTotal": 120,
                    "isDemo": True,
                    "source": "demo",
                },
                {
                    "name": f"{city} 인기 카페",
                    "location": f"{city} 중심가",
                    "rating": 4.5,
                    "cuisine": "cafe",
                    "userRatingsTotal": 80,
                    "isDemo": True,
                    "source": "demo",
                },
            ],
        )
        return preview, f"{city} 주변에서 시도해볼 만한 맛집을 추천했어요."

    transport_keywords = ["교통", "이동", "버스", "subway", "지하철", "대중", "transit", "metro", "트램"]
    if context.pendingAction == "transport" or any(keyword in text_lower for keyword in transport_keywords):
        mode = _detect_mode_from_text(message.text)
        seg_from, seg_to = _extract_segment_targets(message.text, activities_by_day, day)
        preview = _fallback_transport_preview(day, mode, seg_from, seg_to)
        details = preview.changes[0].details if preview.changes else "이동을 변경"
        return preview, f"{day}일차 이동 수단을 {details}로 적용할까요?"

    target_day, target_activity = _find_activity_match(activities_by_day, message.text, day)
    activities_today = activities_by_day.get(str(target_day or day), [])
    fallback_target = target_activity or _choose_fallback_activity(activities_today)

    if any(keyword in text_lower for keyword in ["빼", "제거", "삭제", "없애"]):
        location_name = (target_activity or fallback_target).name if (target_activity or fallback_target) else "기존 일정"
        preview = ChatPreview(
            type="change",
            title=f"{day}일차 일정 수정 제안",
            changes=[
                ChatChange(
                    action="remove",
                    day=target_day or day,
                    location=location_name,
                    details="요청에 따라 일정을 제외합니다.",
                )
            ],
        )
        return preview, f"{day}일차 일정에서 {location_name}을(를) 제외하는 안을 준비했어요."

    if fallback_target:
        replace_match = re.search(r"(?:를|을)\s*([^,]+?)\s*(?:로|으로)\s*(?:바꿔|교체|변경)", message.text)
        replacement_name = replace_match.group(1).strip() if replace_match else ""
        add_location = replacement_name or f"{city} 새 추천 장소"
        preview = ChatPreview(
            type="change",
            title=f"{day}일차 일정 조정 제안",
            changes=[
                ChatChange(
                    action="remove",
                    day=target_day or day,
                    location=fallback_target.name,
                    details="요청하신 일정 교체를 위해 제외합니다.",
                ),
                ChatChange(
                    action="add",
                    day=target_day or day,
                    location=add_location,
                    details=f"{fallback_target.name} 대체 일정 추가",
                ),
            ],
        )
        return preview, f"{fallback_target.name} 대신 새로운 장소를 제안했어요. 적용할까요?"

    preview = _fallback_change_preview(day, activities_by_day.get(str(day), []))
    return preview, f"{day}일차 일정을 조금 더 여유롭게 조정해 보았어요."


def _normalize_preview_data(preview_data: Any, fallback_day: int) -> Any:
    """
    Coerce LLM preview payload into a schema-safe structure:
    - fill missing/invalid day with the current fallback day
    - drop invalid transport mode values instead of failing validation
    """
    if not isinstance(preview_data, dict):
        return preview_data
    normalized = dict(preview_data)
    changes = normalized.get("changes")
    if isinstance(changes, list):
        fixed_changes: List[Dict[str, Any]] = []
        for ch in changes:
            if not isinstance(ch, dict):
                continue
            item = dict(ch)
            action = item.get("action")
            if action not in {"add", "remove", "modify", "transport", "regenerate", "replace"}:
                item["action"] = "modify"
            day = item.get("day")
            if not isinstance(day, int) or day <= 0:
                item["day"] = fallback_day
            mode_val = item.get("mode")
            if mode_val is not None:
                try:
                    mode_str = str(mode_val).lower()
                except Exception:
                    mode_str = None
                if mode_str not in {"drive", "walk", "transit", "bike"}:
                    item["mode"] = None
                else:
                    item["mode"] = mode_str
            fixed_changes.append(item)
        normalized["changes"] = fixed_changes
    return normalized


async def classify_intent(state: ChatState) -> Dict[str, Any]:
    message = state["last_user_message"]
    context = state["context"]
    planner = state["planner_data"]
    intent, day = await _llm_classify_intent(message, context, planner)
    if context.pendingAction in {"remove", "add"} and intent in {None, "regenerate"}:
        intent = "activity_change"
    if not intent:
        intent, day = _classify_intent(message, context)
    return {"intent": intent, "target_day": day}


def _route_after_classify(state: ChatState) -> str:
    intent = state.get("intent") or "activity_change"
    if intent == "transport":
        return "transport"
    if intent == "restaurant":
        return "restaurant"
    if intent == "regenerate":
        return "regenerate"
    if intent == "question":
        return "question"
    return "activity_change"


def _answer_specific_question(
    message_text: str,
    overview: List[DayItinerary],
    activities_by_day: Dict[str, List[Activity]],
    default_day: int,
) -> Optional[str]:
    lowered = message_text.casefold()
    mentions: List[Tuple[int, Activity]] = []
    for day_key, acts in activities_by_day.items():
        for act in acts:
            name_hit = act.name and act.name.casefold() in lowered
            loc_hit = act.location and act.location.casefold() in lowered
            if name_hit or loc_hit:
                mentions.append((int(day_key), act))

    if len(mentions) >= 2 and any(keyword in lowered for keyword in ["몇분", "몇 분", "거리", "걸려", "시간", "이동"]):
        leg_info = _find_leg_between(overview, activities_by_day, mentions[0][1], mentions[1][1])
        if leg_info:
            day, leg, from_act, to_act = leg_info
            return f"{from_act.name}에서 {to_act.name}까지는 {leg.summary}로 약 {leg.durationMinutes}분 걸릴 예정이에요. (Day {day})"

    if mentions:
        mentions.sort(key=lambda x: (x[0] != default_day, x[0]))
        day, act = mentions[0]
        if any(kw in lowered for kw in ["입장", "요금", "가격", "fee", "ticket"]):
            return f"{act.name}의 입장료 정보예요. 표시된 금액: {act.price or '알 수 없음'} · 운영 시간: {act.openHours or '정보 없음'}"
        if any(kw in lowered for kw in ["몇 시", "몇시", "시간", "오픈", "닫"]):
            return f"{act.name} 운영 시간은 {act.openHours or '정보를 찾지 못했어요.'} 입니다."
        desc = act.description or f"{act.location}에 있는 방문지입니다."
        return (
            f"{day}일차 일정에 포함된 {act.name}에 대한 안내입니다.\n"
            f"- 위치: {act.location}\n"
            f"- 예정 시각: {act.time}, 소요 시간: {act.duration or act.estimatedDuration}\n"
            f"- 한 줄 설명: {desc}\n"
            f"입장료: {act.price or '알 수 없음'} / 운영 시간: {act.openHours or '정보 없음'}"
        )
    return None


def _summarize_day(
    day: int, planner: PlannerData, overview: List[DayItinerary], activities_by_day: Dict[str, List[Activity]]
) -> str:
    activities = activities_by_day.get(str(day), [])
    if not activities:
        return f"{day}일차 일정 정보를 찾지 못했어요. 다른 날짜를 알려주시면 일정 내용을 설명해 드릴게요."

    mode_label = {"drive": "자동차", "walk": "도보", "transit": "대중교통", "bike": "자전거"}
    overview_item = next((item for item in overview if item.day == day), None)
    transport_mode = None
    if overview_item and overview_item.transports:
        transport_mode = overview_item.transports[0].mode
    transport_readable = mode_label.get(transport_mode or planner.transportMode or "drive", "자동차")
    activity_lines = "\n".join([f"- {act.time} {act.name}" if act.time else f"- {act.name}" for act in activities])
    return (
        f"{day}일차에는 총 {len(activities)}개의 일정이 있어요.\n"
        f"{activity_lines}\n"
        f"현재 이동 수단은 {transport_readable} 기준으로 안내 중이에요."
    )


def _answer_user_question(
    message: ChatMessage,
    context: ChatContext,
    planner: PlannerData,
    overview: List[DayItinerary],
    activities_by_day: Dict[str, List[Activity]],
) -> Tuple[str, int]:
    day = _extract_day_from_text(message.text, context.currentDay or 1)
    specific = _answer_specific_question(message.text, overview, activities_by_day, day)
    if specific:
        return specific, day
    return _summarize_day(day, planner, overview, activities_by_day), day


async def _llm_plan(
    planner: PlannerData, message: ChatMessage, context: ChatContext, activities_by_day: Dict[str, List[Activity]], day: int
) -> Tuple[Optional[ChatPreview], str]:
    preview: Optional[ChatPreview] = None
    reply_text = ""
    client = get_client()
    if not client:
        return None, ""
    try:
        schema_prompt = (
            "Return JSON with keys 'text' and 'preview'. "
            "preview follows the ChatPreview schema: "
            "type ('change'|'recommendation'), title (string), "
            "changes (list of {action, day, location, details, mode}) or "
            "recommendations (list of {name, location, rating, cuisine}). "
            "Allowed actions include add/remove/modify/transport/regenerate. "
            "For 'add', you may set afterActivityName to place the new activity after a specific one. "
            "For 'transport', you may set fromLocation and toLocation to target a single segment. "
            "If the user is only asking for information, set preview to null and provide an informative Korean answer in 'text'. "
            "All text should be in Korean; translate any English context into natural Korean while "
            "keeping place/restaurant names readable."
        )
        user_ctx = (
            f"User message: {message.text}\n"
            f"Current day: {day}\n"
            f"Pending action: {context.pendingAction}\n"
            f"Cities: {planner.cities}\n"
            f"Styles: {planner.styles}\n"
            f"Existing activities today: {[a.name for a in activities_by_day.get(str(day), [])]}"
        )
        resp = await client.chat.completions.create(
            model=settings.openai_model_chat,
            messages=[{"role": "system", "content": schema_prompt}, {"role": "user", "content": user_ctx}],
            response_format={"type": "json_object"},
        )
        payload = json.loads(resp.choices[0].message.content)
        reply_text = payload.get("text") or ""
        preview_data = payload.get("preview")
        if preview_data:
            try:
                sanitized = _normalize_preview_data(preview_data, day)
                preview = ChatPreview.model_validate(sanitized)
            except Exception as exc:  # pragma: no cover - schema mismatch
                logger.warning("Preview validation failed, fallback to rule-based preview: %s", exc)
    except Exception as exc:  # pragma: no cover - network dependent
        logger.warning("Chat graph OpenAI call failed: %s", exc)
    return preview, reply_text


async def plan_transport(state: ChatState) -> Dict[str, Any]:
    planner = state["planner_data"]
    message = state["last_user_message"]
    day = state.get("target_day") or (state["context"].currentDay or 1)
    mode = _detect_mode_from_text(message.text)
    seg_from, seg_to = _extract_segment_targets(message.text, state["activities_by_day"], day)
    preview = _fallback_transport_preview(day, mode, seg_from, seg_to)
    details = preview.changes[0].details if preview.changes else "이동을 변경"
    reply = ChatReply(
        id=f"msg_{uuid4().hex[:10]}",
        text=f"{day}일차 이동 수단을 {details}로 적용할까요?",
        sender="assistant",
        timestamp=datetime.utcnow(),
        preview=preview,
    )
    return {"assistant_reply": reply}


async def plan_question(state: ChatState) -> Dict[str, Any]:
    planner = state["planner_data"]
    message = state["last_user_message"]
    context = state["context"]
    day = state.get("target_day") or _extract_day_from_text(message.text, context.currentDay or 1)
    reply_text, _ = _answer_user_question(
        message, context, planner, state["itinerary_overview"], state["activities_by_day"]
    )
    reply = ChatReply(
        id=f"msg_{uuid4().hex[:10]}",
        text=reply_text,
        sender="assistant",
        timestamp=datetime.utcnow(),
        preview=None,
    )
    return {"assistant_reply": reply}


async def plan_restaurant(state: ChatState) -> Dict[str, Any]:
    planner = state["planner_data"]
    message = state["last_user_message"]
    context = state["context"]
    activities_by_day = state["activities_by_day"]
    day = state.get("target_day") or (context.currentDay or 1)
    target_day, anchor_act = _find_activity_match(activities_by_day, message.text, day)
    target_day = target_day or day
    anchor = anchor_act or _choose_fallback_activity(activities_by_day.get(str(target_day), []))
    anchor_name = anchor.name if anchor else planner.cities[0] if planner.cities else planner.country
    city = planner.cities[0] if planner.cities else planner.country

    anchor_coords = _lookup_activity_coords(state["itinerary_overview"], target_day, anchor_name) or _coords_for(
        anchor_name
    )

    recommendations = []
    if anchor_coords:
        lat, lng = anchor_coords
        places = await search_restaurants_near(anchor_name, lat, lng, radius_m=2500, max_results=6)
        for place in places:
            name = (place.get("displayName") or {}).get("text") or place.get("name")
            if not name:
                continue
            location_label = place.get("formattedAddress") or place.get("location", {}).get("formattedAddress") or city
            place_loc = place.get("location", {}) or {}
            plat = place_loc.get("latitude")
            plng = place_loc.get("longitude")
            dist = None
            if plat is not None and plng is not None and lat is not None and lng is not None:
                dist = _haversine_distance_m(lat, lng, float(plat), float(plng))
            walking_minutes = int(dist / 80) if dist is not None else None  # ~4.8km/h
            driving_minutes = int(dist / 600) if dist is not None else None  # ~36km/h 도심 근사치
            recommendations.append(
                {
                    "name": name,
                    "location": location_label,
                    "address": location_label,
                    "rating": place.get("rating"),
                    "userRatingsTotal": place.get("userRatingCount"),
                    "cuisine": place.get("primaryType") or (place.get("types") or [None])[0],
                    "lat": plat,
                    "lng": plng,
                    "distanceMeters": dist,
                    "anchorActivityName": anchor_name,
                    "walkingMinutes": walking_minutes,
                    "drivingMinutes": driving_minutes,
                    "source": "google_places",
                }
            )

    if not recommendations:
        preview, reply_text = _build_rule_based_preview(message, context, planner, activities_by_day)
        reply = ChatReply(
            id=f"msg_{uuid4().hex[:10]}",
            text=reply_text or "근처 맛집을 추천했어요. 마음에 드는 곳을 선택해 주세요.",
            sender="assistant",
            timestamp=datetime.utcnow(),
            preview=preview,
        )
        return {"assistant_reply": reply}

    title = f"{anchor_name} 근처 맛집 추천"
    preview = ChatPreview(type="recommendation", title=title, recommendations=recommendations)
    reply = ChatReply(
        id=f"msg_{uuid4().hex[:10]}",
        text=f"{anchor_name} 주변에서 가볼 만한 장소를 골라봤어요. 마음에 드는 곳을 선택하면 일정에 바로 반영할게요.",
        sender="assistant",
        timestamp=datetime.utcnow(),
        preview=preview,
    )
    return {"assistant_reply": reply}


async def plan_regenerate(state: ChatState) -> Dict[str, Any]:
    day = state.get("target_day") or (state["context"].currentDay or 1)
    preview = ChatPreview(
        type="change",
        title=f"{day}일차 일정 재생성",
        changes=[
            ChatChange(
                action="regenerate",
                day=day,
                location=f"{day}일차 일정",
                details="선택한 일차를 새롭게 짭니다.",
            )
        ],
    )
    reply = ChatReply(
        id=f"msg_{uuid4().hex[:10]}",
        text=f"{day}일차 일정을 새로 짤까요? 적용을 누르면 해당 일차만 재생성합니다.",
        sender="assistant",
        timestamp=datetime.utcnow(),
        preview=preview,
    )
    return {"assistant_reply": reply}


async def plan_activity(state: ChatState) -> Dict[str, Any]:
    planner = state["planner_data"]
    message = state["last_user_message"]
    context = state["context"]
    activities_by_day = state["activities_by_day"]
    day = state.get("target_day") or (context.currentDay or 1)

    if state.get("intent") == "question" or (_is_question_like(message.text) and context.pendingAction is None):
        reply_text, _ = _answer_user_question(
            message, context, planner, state["itinerary_overview"], activities_by_day
        )
        reply = ChatReply(
            id=f"msg_{uuid4().hex[:10]}",
            text=reply_text,
            sender="assistant",
            timestamp=datetime.utcnow(),
            preview=None,
        )
        return {"assistant_reply": reply}

    preview, reply_text = await _llm_plan(planner, message, context, activities_by_day, day)
    if preview is None:
        if _is_question_like(message.text) and context.pendingAction is None:
            reply_text = reply_text or _summarize_day(day, planner, state["itinerary_overview"], activities_by_day)
        else:
            preview, fallback_reply = _build_rule_based_preview(message, context, planner, activities_by_day)
            reply_text = reply_text or fallback_reply

    reply = ChatReply(
        id=f"msg_{uuid4().hex[:10]}",
        text=reply_text or "요청을 반영한 제안을 준비했어요.",
        sender="assistant",
        timestamp=datetime.utcnow(),
        preview=preview,
    )
    return {"assistant_reply": reply}


def build_chat_graph():
    builder = StateGraph(ChatState)
    builder.add_node("classify_intent", classify_intent)
    builder.add_node("plan_transport", plan_transport)
    builder.add_node("plan_question", plan_question)
    builder.add_node("plan_restaurant", plan_restaurant)
    builder.add_node("plan_regenerate", plan_regenerate)
    builder.add_node("plan_activity", plan_activity)
    builder.set_entry_point("classify_intent")
    builder.add_conditional_edges("classify_intent", _route_after_classify, {"transport": "plan_transport", "restaurant": "plan_restaurant", "regenerate": "plan_regenerate", "question": "plan_question", "activity_change": "plan_activity"})
    builder.add_edge("plan_transport", END)
    builder.add_edge("plan_question", END)
    builder.add_edge("plan_restaurant", END)
    builder.add_edge("plan_regenerate", END)
    builder.add_edge("plan_activity", END)
    return builder.compile()


_GRAPH = build_chat_graph()


async def generate_chat_reply(
    itinerary: ItineraryEntity, user_message: ChatMessage, context: ChatContext
) -> Tuple[ChatReply, Optional[ItineraryEntity]]:
    """
    Run LangGraph-powered chat planning. We do not auto-apply updates here;
    apply-preview endpoint will persist changes.
    """
    state: ChatState = {
        "planner_data": itinerary.planner_data,
        "itinerary_overview": itinerary.overview,
        "activities_by_day": itinerary.activities_by_day,
        "messages": [user_message],
        "last_user_message": user_message,
        "context": context,
        "assistant_reply": None,
        "intent": None,
        "target_day": None,
    }
    try:
        result = await _GRAPH.ainvoke(state)
        return result["assistant_reply"], None
    except Exception as exc:  # pragma: no cover - fallback for robustness
        logger.exception("Chat graph failed, returning fallback reply: %s", exc)
        day = _extract_day_from_text(user_message.text, context.currentDay or 1)
        preview = _fallback_change_preview(day, itinerary.activities_by_day.get(str(day), []))
        reply = ChatReply(
            id=f"msg_{uuid4().hex[:10]}",
            text="요청하신 내용을 바탕으로 일정 조정 제안을 준비했습니다.",
            sender="assistant",
            timestamp=datetime.utcnow(),
            preview=preview,
        )
        return reply, None
