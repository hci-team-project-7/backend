from fastapi import Depends

from app.core.config import settings
from app.domain.repositories import (
    InMemoryItineraryRepository,
    ItineraryRepository,
    SupabaseItineraryRepository,
)
from app.domain.services.chat_service import ChatService
from app.domain.services.itinerary_service import ItineraryService
from app.external.supabase_client import get_supabase_client

_supabase_client = get_supabase_client()
if settings.use_supabase and _supabase_client:
    _repo: ItineraryRepository = SupabaseItineraryRepository(_supabase_client)
else:
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
