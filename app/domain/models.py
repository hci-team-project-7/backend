from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List

from app.api.models.schemas import Activity, DayItinerary, PlannerData


@dataclass
class ItineraryEntity:
    id: str
    planner_data: PlannerData
    overview: List[DayItinerary]
    activities_by_day: Dict[str, List[Activity]]
    created_at: datetime
    updated_at: datetime

    def to_api_model(self):
        from app.api.models.schemas import Itinerary as ItinerarySchema

        return ItinerarySchema(
            id=self.id,
            plannerData=self.planner_data,
            overview=self.overview,
            activitiesByDay=self.activities_by_day,
            createdAt=self.created_at,
            updatedAt=self.updated_at,
        )
