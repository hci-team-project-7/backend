from fastapi import APIRouter, Depends

from app.api.models.schemas import ApplyPreviewRequest, ApplyPreviewResponse, ChatRequest, ChatResponse
from app.core.errors import NotFoundError
from app.domain.services.chat_service import ChatService
from app.domain.services.itinerary_service import ItineraryService
from app.dependencies import get_chat_service, get_itinerary_service

router = APIRouter(prefix="/itineraries", tags=["chat"])


@router.post("/{itinerary_id}/chat", response_model=ChatResponse)
async def chat_with_itinerary(
    itinerary_id: str,
    body: ChatRequest,
    chat_svc: ChatService = Depends(get_chat_service),
):
    try:
        return await chat_svc.handle_chat(itinerary_id, body)
    except KeyError:
        raise NotFoundError("Itinerary not found")


@router.post("/{itinerary_id}/apply-preview", response_model=ApplyPreviewResponse)
async def apply_preview(
    itinerary_id: str,
    body: ApplyPreviewRequest,
    svc: ItineraryService = Depends(get_itinerary_service),
):
    try:
        entity = await svc.apply_changes(itinerary_id, body.changes)
    except KeyError:
        raise NotFoundError("Itinerary not found")

    return ApplyPreviewResponse(
        updatedItinerary=entity.to_api_model(),
        systemMessage="선택하신 변경사항을 일정에 반영했습니다.",
    )
