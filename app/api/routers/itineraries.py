from fastapi import APIRouter, Depends, HTTPException, status

from app.api.models.schemas import CreateItineraryRequest, CreateItineraryResponse, Itinerary
from app.domain.services.itinerary_service import ItineraryService
from app.dependencies import get_itinerary_service

router = APIRouter(prefix="/itineraries", tags=["itineraries"])


@router.post("", response_model=CreateItineraryResponse, status_code=status.HTTP_201_CREATED)
async def create_itinerary(
    body: CreateItineraryRequest, svc: ItineraryService = Depends(get_itinerary_service)
):
    try:
        entity = await svc.create_itinerary(body.plannerData)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return entity.to_api_model()


@router.get("/{itinerary_id}", response_model=Itinerary)
async def get_itinerary(itinerary_id: str, svc: ItineraryService = Depends(get_itinerary_service)):
    try:
        entity = await svc.get_itinerary(itinerary_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Itinerary not found")
    return entity.to_api_model()
