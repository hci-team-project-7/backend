from fastapi import APIRouter

router = APIRouter(prefix="/meta", tags=["meta"])


@router.get("/countries")
async def list_countries():
    return [
        {"id": "france", "name": "프랑스", "flag": "🇫🇷", "landmark": "/eiffel-tower-paris.png"},
        {"id": "japan", "name": "일본", "flag": "🇯🇵", "landmark": "/mount-fuji-japan.png"},
        {"id": "usa", "name": "미국", "flag": "🇺🇸", "landmark": "/nyc-skyline.jpg"},
    ]


@router.get("/cities")
async def list_cities(countryId: str):
    data = {
        "france": [
            {"id": "paris", "name": "파리", "image": "/paris-eiffel-tower.png"},
            {"id": "nice", "name": "니스", "image": "/nice-city-coast.jpg"},
        ],
        "japan": [
            {"id": "tokyo", "name": "도쿄", "image": "/tokyo.jpg"},
            {"id": "osaka", "name": "오사카", "image": "/osaka.jpg"},
        ],
        "usa": [
            {"id": "newyork", "name": "뉴욕", "image": "/newyork.jpg"},
            {"id": "sanfrancisco", "name": "샌프란시스코", "image": "/sanfrancisco.jpg"},
        ],
    }
    return data.get(countryId, [])


@router.get("/styles")
async def list_styles():
    return [
        {"id": "culture", "name": "문화 & 역사", "icon": "🏛️", "image": "/culture-history.jpg"},
        {"id": "food", "name": "미식", "icon": "🍽️", "image": "/food.jpg"},
        {"id": "relaxation", "name": "휴식", "icon": "🧘", "image": "/relax.jpg"},
        {"id": "adventure", "name": "모험", "icon": "🧭", "image": "/adventure.jpg"},
    ]
