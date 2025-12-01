from __future__ import annotations

import json
import logging
from datetime import datetime
import re
from typing import Any, Dict, List, Optional, Tuple, TypedDict
from uuid import uuid4

from langgraph.graph import END, StateGraph

from app.ai.openai_client import get_client
from app.api.models.schemas import Activity, ChatChange, ChatContext, ChatMessage, ChatPreview, ChatReply, DayItinerary, PlannerData
from app.core.config import settings
from app.domain.models import ItineraryEntity

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


def _fallback_transport_preview(day: int, mode: str) -> ChatPreview:
    readable = {"walk": "도보", "transit": "대중교통", "bike": "자전거", "drive": "자동차"}.get(mode, "자동차")
    change = ChatChange(
        action="transport",
        day=day,
        location="이동 경로",
        details=f"{readable} 이동으로 변경",
        mode=mode,  # type: ignore[arg-type]
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


def _classify_intent(message: ChatMessage, context: ChatContext) -> Tuple[str, int]:
    text_lower = message.text.lower()
    day = _extract_day_from_text(message.text, context.currentDay or 1)

    regen_keywords = ["재생성", "다시 짜", "다시짜", "다시 계획", "replan", "regen", "refresh", "다시 만들어"]
    transport_keywords = ["교통", "이동", "버스", "subway", "지하철", "대중", "transit", "metro", "트램"]
    restaurant_keywords = ["맛집", "식당", "레스토랑", "restaurant", "먹을", "카페"]

    if any(kw in text_lower for kw in regen_keywords):
        return "regenerate", day
    if context.pendingAction == "restaurant" or any(kw in text_lower for kw in restaurant_keywords):
        return "restaurant", day
    if context.pendingAction == "transport" or any(kw in text_lower for kw in transport_keywords):
        return "transport", day
    return "activity_change", day


async def _llm_classify_intent(message: ChatMessage, context: ChatContext, planner: PlannerData) -> Tuple[Optional[str], Optional[int]]:
    client = get_client()
    if not client:
        return None, None
    try:
        schema_prompt = (
            "Return JSON with keys 'intent' and 'day'. "
            "intent should be one of ['transport','restaurant','regenerate','activity_change']. "
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
        if intent not in {"transport", "restaurant", "regenerate", "activity_change"}:
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

    if context.pendingAction == "restaurant" or "맛집" in text_lower or "restaurant" in text_lower:
        preview = ChatPreview(
            type="recommendation",
            title=f"{city} 맛집 추천",
            recommendations=[
                {"name": f"{city} 로컬 레스토랑 A", "location": f"{city} 시내", "rating": 4.6, "cuisine": "local"},
                {"name": f"{city} 인기 카페", "location": f"{city} 중심가", "rating": 4.5, "cuisine": "cafe"},
            ],
        )
        return preview, f"{city} 주변에서 시도해볼 만한 맛집을 추천했어요."

    transport_keywords = ["교통", "이동", "버스", "subway", "지하철", "대중", "transit", "metro", "트램"]
    if context.pendingAction == "transport" or any(keyword in text_lower for keyword in transport_keywords):
        mode = _detect_mode_from_text(message.text)
        preview = _fallback_transport_preview(day, mode)
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


async def classify_intent(state: ChatState) -> Dict[str, Any]:
    message = state["last_user_message"]
    context = state["context"]
    planner = state["planner_data"]
    intent, day = await _llm_classify_intent(message, context, planner)
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
    return "activity_change"


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
                preview = ChatPreview.model_validate(preview_data)
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
    preview = _fallback_transport_preview(day, mode)
    details = preview.changes[0].details if preview.changes else "이동을 변경"
    reply = ChatReply(
        id=f"msg_{uuid4().hex[:10]}",
        text=f"{day}일차 이동 수단을 {details}로 적용할까요?",
        sender="assistant",
        timestamp=datetime.utcnow(),
        preview=preview,
    )
    return {"assistant_reply": reply}


async def plan_restaurant(state: ChatState) -> Dict[str, Any]:
    planner = state["planner_data"]
    message = state["last_user_message"]
    context = state["context"]
    activities_by_day = state["activities_by_day"]
    day = state.get("target_day") or (context.currentDay or 1)
    preview, reply_text = _build_rule_based_preview(message, context, planner, activities_by_day)
    reply = ChatReply(
        id=f"msg_{uuid4().hex[:10]}",
        text=reply_text or "근처 맛집을 추천했어요. 마음에 드는 곳을 선택해 주세요.",
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

    preview, reply_text = await _llm_plan(planner, message, context, activities_by_day, day)
    if preview is None:
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
    builder.add_node("plan_restaurant", plan_restaurant)
    builder.add_node("plan_regenerate", plan_regenerate)
    builder.add_node("plan_activity", plan_activity)
    builder.set_entry_point("classify_intent")
    builder.add_conditional_edges("classify_intent", _route_after_classify, {"transport": "plan_transport", "restaurant": "plan_restaurant", "regenerate": "plan_regenerate", "activity_change": "plan_activity"})
    builder.add_edge("plan_transport", END)
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
