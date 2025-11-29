from __future__ import annotations

from datetime import date, datetime
from uuid import uuid4
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

# ---------- PlannerData ----------


class DateRange(BaseModel):
    start: date
    end: date


class Travelers(BaseModel):
    adults: int = Field(ge=1)
    children: int = Field(ge=0)
    type: str


class PlannerData(BaseModel):
    country: str
    cities: List[str]
    dateRange: DateRange
    travelers: Travelers
    styles: List[str]


# ---------- Location / DayItinerary / Activity ----------


class Location(BaseModel):
    name: str
    time: str
    lat: float
    lng: float


class DayItinerary(BaseModel):
    day: int
    date: date
    title: str
    photo: str
    activities: List[str]
    locations: List[Location]


class Activity(BaseModel):
    id: str
    name: str
    location: str
    time: str
    duration: str
    description: str
    image: str
    openHours: str
    price: str
    tips: List[str]
    nearbyFood: List[str]
    estimatedDuration: str
    bestTime: str


# ---------- Itinerary ----------


class Itinerary(BaseModel):
    id: str
    plannerData: PlannerData
    overview: List[DayItinerary]
    activitiesByDay: Dict[str, List[Activity]]
    createdAt: datetime
    updatedAt: datetime


# ---------- Chat ----------


ChatSender = Literal["user", "assistant"]


class ChatChange(BaseModel):
    action: Literal["add", "remove", "modify", "transport"]
    day: Optional[int] = None
    location: Optional[str] = None
    details: Optional[str] = None


class ChatRestaurantRecommendation(BaseModel):
    name: str
    location: str
    rating: Optional[float] = None
    cuisine: Optional[str] = None


class ChatPreview(BaseModel):
    type: Literal["change", "recommendation"]
    title: str
    changes: Optional[List[ChatChange]] = None
    recommendations: Optional[List[ChatRestaurantRecommendation]] = None


class ChatMessage(BaseModel):
    id: str = Field(default_factory=lambda: f"msg_{uuid4().hex[:10]}")
    text: str
    sender: ChatSender = "user"
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    preview: Optional[ChatPreview] = None


# ---------- Request/Response 모델 ----------


class CreateItineraryRequest(BaseModel):
    plannerData: PlannerData


class CreateItineraryResponse(Itinerary):
    pass


class ChatContext(BaseModel):
    currentView: Literal["overview", "daily"]
    currentDay: Optional[int] = None
    pendingAction: Optional[Literal["remove", "add", "transport", "restaurant"]] = None


class ChatRequest(BaseModel):
    message: ChatMessage
    context: ChatContext


class ChatReply(BaseModel):
    id: str
    text: str
    sender: ChatSender
    timestamp: datetime
    preview: Optional[ChatPreview] = None


class ChatResponse(BaseModel):
    reply: ChatReply
    updatedItinerary: Optional[Itinerary] = None


class ApplyPreviewRequest(BaseModel):
    sourceMessageId: str
    changes: List[ChatChange]


class ApplyPreviewResponse(BaseModel):
    updatedItinerary: Itinerary
    systemMessage: str
