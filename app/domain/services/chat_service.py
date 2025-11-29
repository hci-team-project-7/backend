from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from app.ai.chat_graph import generate_chat_reply
from app.api.models.schemas import ChatRequest, ChatResponse
from app.domain.repositories import ItineraryRepository


class ChatService:
    def __init__(self, repo: ItineraryRepository):
        self.repo = repo

    async def handle_chat(self, itinerary_id: str, chat_request: ChatRequest) -> ChatResponse:
        itinerary = await self.repo.get(itinerary_id)
        # Ensure incoming message has an id and sender
        if not chat_request.message.id:
            chat_request.message.id = f"msg_{uuid4().hex[:10]}"
        if not chat_request.message.sender:
            chat_request.message.sender = "user"
        if not chat_request.message.timestamp:
            chat_request.message.timestamp = datetime.utcnow()

        reply, updated_entity = await generate_chat_reply(itinerary, chat_request.message, chat_request.context)
        if updated_entity:
            await self.repo.update(updated_entity)
        return ChatResponse(reply=reply, updatedItinerary=updated_entity.to_api_model() if updated_entity else None)
