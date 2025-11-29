from abc import ABC, abstractmethod
import asyncio
from datetime import datetime
from typing import Dict

from app.api.models.schemas import Activity, DayItinerary, PlannerData
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


class SupabaseItineraryRepository(ItineraryRepository):
    """
    Supabase-backed repository storing itineraries in a single JSONB row.
    Falls back to raising KeyError on missing items to align with service usage.
    """

    def __init__(self, client):
        if client is None:
            raise ValueError("Supabase client is required for SupabaseItineraryRepository")
        self.client = client
        self.table_name = "itineraries"

    async def save(self, itinerary: ItineraryEntity) -> ItineraryEntity:
        await self._upsert(itinerary, insert=True)
        return itinerary

    async def get(self, itinerary_id: str) -> ItineraryEntity:
        response = await asyncio.to_thread(
            lambda: self.client.table(self.table_name).select("*").eq("id", itinerary_id).execute()
        )
        rows = getattr(response, "data", None) or []
        if not rows:
            raise KeyError("Itinerary not found")
        return self._row_to_entity(rows[0])

    async def update(self, itinerary: ItineraryEntity) -> ItineraryEntity:
        itinerary.updated_at = datetime.utcnow()
        await self._upsert(itinerary, insert=False)
        return itinerary

    async def _upsert(self, itinerary: ItineraryEntity, insert: bool) -> None:
        payload = {
            "id": itinerary.id,
            "planner_data": itinerary.planner_data.model_dump(),
            "overview": [item.model_dump() for item in itinerary.overview],
            "activities_by_day": {
                key: [activity.model_dump() for activity in value]
                for key, value in itinerary.activities_by_day.items()
            },
            "created_at": itinerary.created_at.isoformat(),
            "updated_at": itinerary.updated_at.isoformat(),
        }
        if insert:
            await asyncio.to_thread(lambda: self.client.table(self.table_name).insert(payload).execute())
        else:
            await asyncio.to_thread(
                lambda: self.client.table(self.table_name).update(payload).eq("id", itinerary.id).execute()
            )

    def _row_to_entity(self, row: Dict) -> ItineraryEntity:
        def _parse_dt(value: str) -> datetime:
            if isinstance(value, datetime):
                return value
            if value.endswith("Z"):
                value = value.replace("Z", "+00:00")
            return datetime.fromisoformat(value)

        planner = PlannerData.model_validate(row["planner_data"])
        overview = [DayItinerary.model_validate(item) for item in row["overview"]]
        activities = {
            str(key): [Activity.model_validate(act) for act in value]
            for key, value in row["activities_by_day"].items()
        }
        return ItineraryEntity(
            id=row["id"],
            planner_data=planner,
            overview=overview,
            activities_by_day=activities,
            created_at=_parse_dt(row.get("created_at", row.get("inserted_at", datetime.utcnow().isoformat()))),
            updated_at=_parse_dt(row.get("updated_at", datetime.utcnow().isoformat())),
        )
