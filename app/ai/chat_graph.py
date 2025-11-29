from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Tuple
from uuid import uuid4

from app.api.models.schemas import (
    Activity,
    ChatChange,
    ChatContext,
    ChatMessage,
    ChatPreview,
    ChatReply,
)
from app.domain.models import ItineraryEntity


def _build_recommendations(city: str) -> ChatPreview:
    return ChatPreview(
        type="recommendation",
        title=f"{city} 추천 맛집",
        recommendations=[
            {
                "name": f"{city} 로컬 레스토랑 A",
                "location": f"{city} 시내",
                "rating": 4.6,
                "cuisine": "local",
            },
            {
                "name": f"{city} 인기 카페 B",
                "location": f"{city} 중심가",
                "rating": 4.5,
                "cuisine": "cafe",
            },
            {
                "name": f"{city} 스트리트 푸드",
                "location": f"{city} 야시장",
                "rating": 4.3,
                "cuisine": "street food",
            },
        ],
    )


def _build_change_preview(day: int, itinerary: ItineraryEntity) -> ChatPreview:
    activities = itinerary.activities_by_day.get(str(day)) or []
    remove_target = activities[0].name if activities else "기존 일정"
    changes: List[ChatChange] = [
        ChatChange(action="remove", day=day, location=remove_target, details="여유 시간 확보"),
        ChatChange(action="add", day=day, location="카페 휴식", details="느긋하게 커피 한잔"),
    ]
    return ChatPreview(type="change", title=f"{day}일차 일정 조정 제안", changes=changes)


def generate_chat_reply(
    itinerary: ItineraryEntity, user_message: ChatMessage, context: ChatContext
) -> Tuple[ChatReply, Optional[ItineraryEntity]]:
    """Rule-based chat reply generator that mimics the intended LangGraph output."""
    now = datetime.utcnow()
    day = context.currentDay or 1
    text_lower = user_message.text.lower()
    city = itinerary.planner_data.cities[0] if itinerary.planner_data.cities else itinerary.planner_data.country

    preview: Optional[ChatPreview] = None
    if context.pendingAction == "restaurant" or "맛집" in text_lower or "restaurant" in text_lower:
        preview = _build_recommendations(city)
        reply_text = f"{city} 주변에서 시도해볼 만한 맛집을 추천했어요. 마음에 드는 곳을 선택해 주세요."
    else:
        preview = _build_change_preview(day, itinerary)
        reply_text = f"{day}일차를 조금 더 여유롭게 조정해 보았어요."

    reply = ChatReply(
        id=f"msg_{uuid4().hex[:10]}",
        text=reply_text,
        sender="assistant",
        timestamp=now,
        preview=preview,
    )
    # This simple implementation does not apply changes immediately; apply-preview handles it.
    return reply, None
