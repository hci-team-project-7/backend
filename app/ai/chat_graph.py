from __future__ import annotations

import json
import logging
from datetime import datetime
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


def _fallback_change_preview(day: int, activities: List[Activity]) -> ChatPreview:
    remove_target = activities[0].name if activities else "기존 일정"
    changes: List[ChatChange] = [
        ChatChange(action="remove", day=day, location=remove_target, details="이동 간격 확보"),
        ChatChange(action="add", day=day, location="카페 휴식", details="느긋하게 커피 한잔"),
    ]
    return ChatPreview(type="change", title=f"{day}일차 일정 조정 제안", changes=changes)


async def plan_change(state: ChatState) -> Dict[str, Any]:
    planner = state["planner_data"]
    message = state["last_user_message"]
    context = state["context"]
    activities_by_day = state["activities_by_day"]
    client = get_client()

    city = planner.cities[0] if planner.cities else planner.country
    day = context.currentDay or 1
    preview: Optional[ChatPreview] = None
    reply_text = ""

    if client:
        try:
            schema_prompt = (
                "Return JSON with keys 'text' and 'preview'. "
                "preview follows the ChatPreview schema: "
                "type ('change'|'recommendation'), title (string), "
                "changes (list of {action, day, location, details}) or "
                "recommendations (list of {name, location, rating, cuisine})."
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

    if preview is None:
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
            reply_text = reply_text or f"{city} 주변에서 시도해볼 만한 맛집을 추천했어요."
        else:
            preview = _fallback_change_preview(day, activities_by_day.get(str(day), []))
            reply_text = reply_text or f"{day}일차 일정을 조금 더 여유롭게 조정해 보았어요."

    reply = ChatReply(
        id=f"msg_{uuid4().hex[:10]}",
        text=reply_text,
        sender="assistant",
        timestamp=datetime.utcnow(),
        preview=preview,
    )
    return {"assistant_reply": reply}


def build_chat_graph():
    builder = StateGraph(ChatState)
    builder.add_node("plan_change", plan_change)
    builder.set_entry_point("plan_change")
    builder.add_edge("plan_change", END)
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
    }
    try:
        result = await _GRAPH.ainvoke(state)
        return result["assistant_reply"], None
    except Exception as exc:  # pragma: no cover - fallback for robustness
        logger.exception("Chat graph failed, returning fallback reply: %s", exc)
        day = context.currentDay or 1
        preview = _fallback_change_preview(day, itinerary.activities_by_day.get(str(day), []))
        reply = ChatReply(
            id=f"msg_{uuid4().hex[:10]}",
            text="요청하신 내용을 바탕으로 일정 조정 제안을 준비했습니다.",
            sender="assistant",
            timestamp=datetime.utcnow(),
            preview=preview,
        )
        return reply, None
