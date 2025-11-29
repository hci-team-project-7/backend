from fastapi import Depends

from app.core.config import settings
from app.domain.repositories import InMemoryItineraryRepository, ItineraryRepository
from app.domain.services.chat_service import ChatService
from app.domain.services.itinerary_service import ItineraryService

_repo = InMemoryItineraryRepository()


def get_itinerary_repo() -> ItineraryRepository:
    return _repo


def get_itinerary_service(
    repo: ItineraryRepository = Depends(get_itinerary_repo),
) -> ItineraryService:
    return ItineraryService(repo=repo)


def get_chat_service(
    repo: ItineraryRepository = Depends(get_itinerary_repo),
) -> ChatService:
    return ChatService(repo=repo)


__all__ = [
    "get_itinerary_repo",
    "get_itinerary_service",
    "get_chat_service",
    "settings",
]
