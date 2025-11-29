from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict

from .models import ItineraryEntity


class ItineraryRepository(ABC):
    @abstractmethod
    async def save(self, itinerary: ItineraryEntity) -> ItineraryEntity:
        raise NotImplementedError

    @abstractmethod
    async def get(self, itinerary_id: str) -> ItineraryEntity:
        raise NotImplementedError

    @abstractmethod
    async def update(self, itinerary: ItineraryEntity) -> ItineraryEntity:
        raise NotImplementedError


class InMemoryItineraryRepository(ItineraryRepository):
    def __init__(self):
        self._store: Dict[str, ItineraryEntity] = {}

    async def save(self, itinerary: ItineraryEntity) -> ItineraryEntity:
        self._store[itinerary.id] = itinerary
        return itinerary

    async def get(self, itinerary_id: str) -> ItineraryEntity:
        if itinerary_id not in self._store:
            raise KeyError("Itinerary not found")
        return self._store[itinerary_id]

    async def update(self, itinerary: ItineraryEntity) -> ItineraryEntity:
        itinerary.updated_at = datetime.utcnow()
        self._store[itinerary.id] = itinerary
        return itinerary
