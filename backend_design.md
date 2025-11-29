**이 문서를 그대로 따라가면** 
 
* FastAPI 기반 백엔드
* OpenAI + LangGraph 기반 AI 일정 생성/수정
* (선택) Supabase 저장소
* (선택) Google Routes API로 이동 시간 계산
* (선택) crawl4ai로 장소 정보 보강

까지 구현 가능합니다.

아래에서

1. **프로젝트 목적**
2. **사용 시나리오**
3. **FastAPI 백엔드 완전 설계서 (디렉토리 구조 + Pydantic 스키마 + 라우터/서비스/AI 파이프라인/DB/외부 API)**

순으로 정리

---

## 1) 프로젝트의 목적

### 1.1 제품 한 줄 정의

> **“사용자가 5단계 위저드로 여행 취향을 입력하면,
> AI가 루트·시간·장소 정보를 고려해 완성도 높은 일정(Itinerary)을 생성하고,
> 이후에는 챗봇과 대화하면서 일정을 자연스럽게 수정/보강할 수 있는 여행 플래너”**

### 1.2 현재 프론트 구조

* `app/page.tsx` – Hero → TravelPlanner 위저드
* `components/travel-planner.tsx` – 5단계 위저드

  * `Step1Destination`, `Step2Cities`, `Step3Dates`, `Step4Travelers`, `Step5Style`
* `components/itinerary-results.tsx`

  * `ItineraryOverview` (`components/itinerary/overview.tsx`)
  * `DailyDetailPage` (`components/itinerary/daily-detail.tsx`)
  * `ItineraryChat` (`components/itinerary-chat.tsx`)

현재는

* **모든 데이터가 프론트에 하드코딩** (`MOCK_ITINERARY`, `ACTIVITIES` 등)
* API 호출 없음 → `handleGenerateItinerary`에서 그냥 `showResults = true`로 전환
* `ItineraryChat`도 `setTimeout`으로 가짜 응답만 생성

이 백엔드 설계의 목적은:

1. 위저드를 통해 얻은 **PlannerData를 서버로 보내서 진짜 일정(Itinerary) 생성**
2. 프론트의 `MOCK_ITINERARY`, `ACTIVITIES`를 **백엔드에서 가져오는 Itinerary로 완전히 대체**
3. `ItineraryChat`이 **실제 FastAPI + OpenAI + LangGraph와 통신**해서

   * 자연어 답변 + `preview`(변경 제안/맛집 추천) + 필요 시 `updatedItinerary`를 받도록 함
4. 선택적으로 **Supabase에 일정/대화 저장, Google Routes API로 이동 시간 계산, crawl4ai로 장소 정보 보강**

---

## 2) 프로젝트 구체 사용 시나리오

### 시나리오 A. 기본 일정 생성(위저드 → 결과 화면)

1. 사용자가 Hero 화면에서 “여행 계획 시작하기” 클릭 → `TravelPlanner` 진입

2. 5단계 위저드를 채운다.

   * Step 1: 나라 선택 (프랑스, 일본 등)
   * Step 2: 도시 선택 (파리, 니스 등)
   * Step 3: 출발일/도착일 선택
   * Step 4: 인원수/여행 타입 (커플/가족/혼자)
   * Step 5: 스타일 (문화, 음식, 휴식, 모험 등)

3. “나의 여정 생성하기” 클릭 → 프론트에서 **`POST /api/v1/itineraries` 호출**

4. FastAPI 서버는:

   1. body의 `plannerData`를 검증
   2. `LangGraph` 기반 **Itinerary 생성 그래프**를 실행
      (`collect_pois → schedule_days → enrich_with_routes → enrich_with_details → finalize`)
   3. 생성된 `Itinerary`를 Supabase에 저장
   4. 생성된 `Itinerary`를 JSON으로 응답

5. 프론트는 응답 받은 `Itinerary`를 `ItineraryResults`에 넘기고,

   * `overview` → DaySidebar + ItineraryMap
   * `activitiesByDay` → DailyDetailPage + ActivityTimeline + ActivityDetail
     를 렌더링

---

### 시나리오 B. 일정 조회 (새로고침/“다시 보기”)

1. 사용자가 일정 URL을 북마크하거나, 나중에 “저장된 일정 불러오기”를 누른다.
2. 프론트는 **`GET /api/v1/itineraries/{id}`** 호출
3. FastAPI는 Supabase에서 `itinerary_id`에 해당하는 레코드를 조회하여 `Itinerary` 반환
4. 프론트는 처음 생성 때와 동일하게 `ItineraryResults`를 구성

---

### 시나리오 C. 챗봇을 통한 일정 수정

1. 사용자가 일정 화면 우측 하단의 채팅 버튼을 누른다.

2. “2일차 오후 일정을 조금 더 여유롭게 바꿔줘” 메시지를 입력하고 전송.

3. 프론트는 **`POST /api/v1/itineraries/{id}/chat`**에 다음 내용을 보냄:

   ```json
   {
     "message": {
       "text": "2일차 오후 일정을 조금 더 여유롭게 바꿔줘",
       "timestamp": "2025-05-01T10:10:00Z"
     },
     "context": {
       "currentView": "daily",
       "currentDay": 2,
       "pendingAction": null
     }
   }
   ```

4. FastAPI는:

   1. Supabase에서 기존 `Itinerary`와 최근 채팅 맥락을 로드
   2. LangGraph 기반 **Chat 그래프** 실행:

      * OpenAI LLM이 사용자 의도를 분석
      * JSON 포맷으로 `preview.changes`를 생성 (`remove` / `add` / `transport` 등)
   3. 자연어 답변 + `preview`를 `reply`로 응답
      (필요하다면 변경이 이미 반영된 `updatedItinerary`까지 반환 가능)

5. 프론트는 답변과 함께 `preview`를 렌더링하고

   * 사용자가 “변경사항 적용” 버튼을 누르면

6. 프론트가 **`POST /api/v1/itineraries/{id}/apply-preview`** 호출

   * body에는 `sourceMessageId` + `changes` 배열

7. FastAPI는:

   1. `changes`를 기준으로 `overview`, `activitiesByDay`를 실제로 수정
   2. Google Routes API로 이동 시간을 재계산
   3. 필요시 OpenAI로 새 Activity 설명/팁 생성
   4. Supabase에 업데이트된 Itinerary 저장 후 `updatedItinerary` 반환

8. 프론트는 `updatedItinerary`를 현재 state에 반영 → Overview/DailyDetail/Chat 모두 즉시 갱신

---

### 시나리오 D. 맛집 추천 워크플로우

1. 사용자가 빠른 액션에서 “맛집 추천” (`pendingAction = "restaurant"`) 선택

2. “타임스퀘어 근처에서 저녁 먹을만한 곳 추천해줘” 입력 → `POST /itineraries/{id}/chat`

3. Chat 그래프는:

   * 현재 day/location 주변을 파악
   * LLM + (선택) crawl4ai로 주변 레스토랑 후보를 뽑고
   * `preview.type="recommendation"`으로 추천 리스트를 JSON으로 반환

4. 프론트는 추천 리스트를 카드로 보여주고,

   * 사용자가 하나를 선택하면 `POST /apply-preview`로 `action="add"` 변경사항 전송

5. 서버는 해당 day의 타임라인에 **Restaurant Activity**를 추가하고 `updatedItinerary` 반환

---

## 3) FastAPI 백엔드 완전 설계서

여기서부터는 “이대로 구현하면 된다” 수준으로 **구조 + 스키마 + 주요 함수 설계**를 적어줄게요.

---

### 3.1 기술 스택 및 의존성

**프레임워크 / 런타임**

* Python 3.11+
* FastAPI
* Uvicorn (ASGI 서버)
* Pydantic v2

**AI/오케스트레이션**

* OpenAI Python SDK (`openai`)
* LangGraph (`langgraph`)
* (선택) `tiktoken` 토큰 계산

**DB**

* (권장) Supabase (PostgreSQL) + `supabase-py`
* 시작은 InMemoryRepository로 구현 후, Supabase 구현체로 교체 가능

**외부 API**

* Google Cloud Routes API
  (이동 시간, 거리 계산)
* (선택) crawl4ai SDK
  (식당/관광지 상세 정보 웹 크롤링)

**기타**

* `python-dotenv` (로컬 개발용 env)
* `httpx` (비동기 HTTP 클라이언트)
* `tenacity` (재시도 로직)

---

### 3.2 디렉토리 구조 (백엔드)

```bash
backend/
  app/
    main.py               # FastAPI 엔트리 포인트
    dependencies.py       # DI용 공통 Depends
    core/
      config.py           # 환경변수/설정
      logging.py          # 로깅 설정
      errors.py           # 공통 예외/에러 응답
    api/
      routers/
        itineraries.py    # /api/v1/itineraries
        chat.py           # /api/v1/itineraries/{id}/chat, /apply-preview
        meta.py           # /api/v1/meta/*
      models/
        schemas.py        # Pydantic Request/Response 스키마 (프론트와 1:1 매핑)
    domain/
      models.py           # 내부 도메인 객체 (Itinerary, Activity 등)
      repositories.py     # 인터페이스 + 구현 (InMemory, Supabase)
      services/
        itinerary_service.py  # 일정 생성/조회/수정 서비스
        chat_service.py       # 챗봇 로직 서비스
    ai/
      openai_client.py    # OpenAI 클라이언트 초기화
      prompts.py          # 시스템 프롬프트/템플릿
      itinerary_graph.py  # LangGraph 그래프 (Itinerary 생성)
      chat_graph.py       # LangGraph 그래프 (챗봇)
    external/
      routes_api.py       # Google Routes API 호출
      crawl4ai_client.py  # crawl4ai 연동(선택)
      supabase_client.py  # Supabase 클라이언트
  pyproject.toml or requirements.txt
```

---

### 3.3 공통 설정 (config)

```python
# app/core/config.py
from pydantic import BaseSettings

class Settings(BaseSettings):
    project_name: str = "Trip Planner API"
    api_v1_prefix: str = "/api/v1"

    openai_api_key: str
    openai_model_itinerary: str = "gpt-4.1"       # 예시
    openai_model_chat: str = "gpt-4.1-mini"

    google_routes_api_key: str | None = None

    supabase_url: str | None = None
    supabase_anon_key: str | None = None

    use_supabase: bool = False

    cors_origins: list[str] = ["*"]  # 개발용, 운영 시 도메인 제한

    class Config:
        env_file = ".env"

settings = Settings()
```

---

### 3.4 Pydantic 스키마 (프론트와 동일한 JSON 구조)

`app/api/models/schemas.py`

```python
from datetime import date, datetime
from pydantic import BaseModel, Field
from typing import Literal, Optional, List, Dict

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
    id: str
    text: str
    sender: ChatSender
    timestamp: datetime
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
    message: ChatMessage  # 프론트는 text/timestamp만 채워서 보내도 됨
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
```

> 이 스키마는 프론트에서 기대하는 JSON 구조와 1:1로 매핑됩니다.

---

### 3.5 도메인 모델/리포지토리

`app/domain/models.py`

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List
from app.api.models.schemas import PlannerData, DayItinerary, Activity

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
            UpdatedAt=self.updated_at,
        )
```

`app/domain/repositories.py`

```python
from abc import ABC, abstractmethod
from typing import List
from .models import ItineraryEntity
from datetime import datetime

class ItineraryRepository(ABC):
    @abstractmethod
    async def save(self, itinerary: ItineraryEntity) -> ItineraryEntity: ...
    @abstractmethod
    async def get(self, itinerary_id: str) -> ItineraryEntity: ...
    @abstractmethod
    async def update(self, itinerary: ItineraryEntity) -> ItineraryEntity: ...

# InMemory 구현 (개발용)

class InMemoryItineraryRepository(ItineraryRepository):
    def __init__(self):
        self._store: dict[str, ItineraryEntity] = {}

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
```

`SupabaseItineraryRepository`는 JSONB로 단일 row에 저장하는 방식으로 설계:

* 테이블: `itineraries`

```sql
create table itineraries (
  id text primary key,
  planner_data jsonb not null,
  overview jsonb not null,
  activities_by_day jsonb not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
```

---

### 3.6 Itinerary 서비스 (핵심 비즈니스 로직)

`app/domain/services/itinerary_service.py`

역할:

* `POST /itineraries` → 일정 생성
* `GET /itineraries/{id}` → 일정 조회
* `POST /itineraries/{id}/apply-preview` → 변경사항 실제 반영

핵심 함수 시그니처:

```python
from app.domain.repositories import ItineraryRepository
from app.api.models.schemas import PlannerData, Itinerary as ItinerarySchema, ChatChange
from app.domain.models import ItineraryEntity
from typing import List
from datetime import datetime
import uuid

class ItineraryService:
    def __init__(self, repo: ItineraryRepository):
        self.repo = repo

    async def create_itinerary(self, planner_data: PlannerData) -> ItineraryEntity:
        # 1) validation
        # 2) LangGraph로 itinerary 생성
        # 3) repo.save
        ...

    async def get_itinerary(self, itinerary_id: str) -> ItineraryEntity:
        return await self.repo.get(itinerary_id)

    async def apply_changes(
        self,
        itinerary_id: str,
        changes: List[ChatChange],
    ) -> ItineraryEntity:
        # 1) itinerary 로드
        # 2) changes 반영
        # 3) 이동시간 재계산 (Google Routes API)
        # 4) repo.update
        ...
```

#### 3.6.1 create_itinerary 내부 알고리즘 (개략)

1. **PlannerData 검증**

   * `dateRange.start >= 오늘`
   * `dateRange.end >= dateRange.start`
   * `len(cities) >= 1`
   * `travelers.adults >= 1`
   * `len(styles) >= 1`

2. **여행 일수 계산**

   ```python
   num_days = (planner_data.dateRange.end - planner_data.dateRange.start).days + 1
   ```

3. **LangGraph 실행**

   * 아래 3.7 LangGraph 섹션에서 자세히 기술
   * 결과: `overview: List[DayItinerary]`, `activities_by_day: Dict[str, List[Activity]]`

4. **엔티티 생성 및 저장**

   ```python
   itinerary_id = "itn_" + uuid.uuid4().hex[:12]
   now = datetime.utcnow()

   entity = ItineraryEntity(
       id=itinerary_id,
       planner_data=planner_data,
       overview=overview,
       activities_by_day=activities_by_day,
       created_at=now,
       updated_at=now,
   )
   await self.repo.save(entity)
   return entity
   ```

#### 3.6.2 apply_changes 알고리즘 (간략 설계)

`changes: List[ChatChange]`를 순회하면서 다음 규칙으로 적용:

* `action == "remove"`

  * `day`와 `location` 이름으로 `activities_by_day[str(day)]`에서 매칭 Activity를 찾고 제거
    (대소문자 무시, 부분 매칭 허용)
  * `overview[day-1].activities`에서도 해당 이름/유사한 설명 삭제

* `action == "add"`

  * 해당 day에 새 `Activity` 삽입

  * 기본 스켈레톤:

    ```python
    Activity(
      id=f"{day}-{len(day_activities)+1}",
      name=change.location or "새로운 장소",
      location=change.location or "TBD",
      time="19:00",
      duration="2시간",
      description="추가된 활동입니다. (나중에 LLM으로 상세 보강 가능)",
      image="/default-activity.jpg",
      openHours="알 수 없음",
      price="TBD",
      tips=[],
      nearbyFood=[],
      estimatedDuration="2시간",
      bestTime="오후"
    )
    ```

  * 이후 `enrich_activity_details` 함수에서 OpenAI로 설명/팁을 보강 (선택)

* `action == "transport"`

  * 해당 day의 이동 수단(예: 버스 → 도보) 변경을 표현하는 Activity 또는 메타데이터 추가

* 변경이 끝나면, 해당 day의 `locations` 순서를 새로 계산하고,
  **Google Routes API**로 이동 시간, 거리 추출 후 `time` 필드를 조정.

---

### 3.7 LangGraph 기반 Itinerary 생성 파이프라인

`app/ai/itinerary_graph.py`

#### 3.7.1 State 정의

```python
from typing import TypedDict, List, Dict
from app.api.models.schemas import PlannerData, DayItinerary, Activity, Location

class ItineraryState(TypedDict):
    planner_data: PlannerData
    candidate_pois: List[dict]          # {name, city, type, style_score, lat, lng, ...}
    day_plans: List[DayItinerary]
    activities_by_day: Dict[str, List[Activity]]
```

#### 3.7.2 노드 설계

1. **collect_pois**

   * 입력: `planner_data`
   * 역할:

     * OpenAI에 `"프랑스 파리/니스, 문화+음식 스타일, 7일"`과 같은 프롬프트로

       * 각 도시별 추천 POI 리스트를 JSON으로 받음
     * (선택) crawl4ai를 사용해 주요 POI의 웹페이지에서 평점/영업시간을 긁어와 보강
   * 출력: `candidate_pois` (리스트)

2. **score_and_filter_pois**

   * planner_data.styles, travelers.type 등 기준으로 중요도 점수 계산
   * 하루당 3~5개 주요 액티비티 수준으로 필터링

3. **schedule_days**

   * 입력: 필터링된 POI + 여행 일수
   * 로직:

     * 날짜별로 POI를 분배
     * 도시 이동(파리 → 니스 등)이 필요한 날은 “이동일”로 설정
     * 각 day에 `DayItinerary`를 구성 (title, activities, locations초안)

4. **enrich_with_routes** (Google Routes API 사용)

   * 각 day의 locations를 순서대로 전달하여 이동 시간/거리 계산
   * 이동 시간에 맞춰 `time` 조정
   * 장거리 이동일 경우 title·activities 수정

5. **enrich_with_details** (OpenAI)

   * 각 POI마다 Activity 상세 정보(설명, 팁, 가격 범위, bestTime 등)를 생성
   * `Activity` 객체 리스트로 변환

6. **finalize_itinerary**

   * `day_plans`와 `activities_by_day`를 최종 스테이트에 세팅

#### 3.7.3 LangGraph 구성 예시 (개념)

```python
from langgraph.graph import StateGraph, END

def build_itinerary_graph():
    builder = StateGraph(ItineraryState)

    builder.add_node("collect_pois", collect_pois)
    builder.add_node("score_and_filter_pois", score_and_filter_pois)
    builder.add_node("schedule_days", schedule_days)
    builder.add_node("enrich_with_routes", enrich_with_routes)
    builder.add_node("enrich_with_details", enrich_with_details)

    builder.set_entry_point("collect_pois")
    builder.add_edge("collect_pois", "score_and_filter_pois")
    builder.add_edge("score_and_filter_pois", "schedule_days")
    builder.add_edge("schedule_days", "enrich_with_routes")
    builder.add_edge("enrich_with_routes", "enrich_with_details")
    builder.add_edge("enrich_with_details", END)

    return builder.compile()
```

`ItineraryService.create_itinerary`에서는:

```python
graph = build_itinerary_graph()
state = {
  "planner_data": planner_data,
  "candidate_pois": [],
  "day_plans": [],
  "activities_by_day": {},
}
result_state = await graph.ainvoke(state)
overview = result_state["day_plans"]
activities_by_day = result_state["activities_by_day"]
```

---

### 3.8 Chat 그래프 (LangGraph + OpenAI)

`app/ai/chat_graph.py`

State:

```python
class ChatState(TypedDict):
    planner_data: PlannerData
    itinerary_overview: List[DayItinerary]
    activities_by_day: Dict[str, List[Activity]]
    messages: List[ChatMessage]   # 대화 히스토리
    last_user_message: ChatMessage
    context: ChatContext
    assistant_reply: Optional[ChatReply]
```

핵심 노드: `plan_change`

* 역할:

  * 마지막 user 메시지 + context(pendingAction 등) 기반으로
  * 자연어 응답 + `preview` JSON 생성
* 프롬프트 예시:

  > 시스템 프롬프트:
  > “당신은 여행 일정 어시스턴트입니다.
  > 아래 JSON 스키마(ChatPreview)를 따르는 변경 제안을 생성하세요.
  > … (스키마 설명) …
  > 사용자의 자연어 메시지와 currentDay, pendingAction을 참고해
  > remove/add/transport/change를 적절히 사용하세요.”

OpenAI 응답은 `response_format="json_schema"`(지원 모델 사용 시)를 이용해
`ChatPreview` 형식으로 바로 파싱할 수 있게 합니다.

그래프는 단일 노드로도 충분:

```python
builder = StateGraph(ChatState)
builder.add_node("plan_change", plan_change)
builder.set_entry_point("plan_change")
builder.add_edge("plan_change", END)
```

`ChatService.handle_chat`은:

1. Supabase에서 Itinerary + 최근 messages 로드
2. `ChatState` 구성 후 `graph.ainvoke`
3. `assistant_reply`를 `ChatResponse.reply`로 리턴
4. (선택) preview를 DB에 저장해둬서 `/apply-preview`에서 sourceMessageId 검증에 사용

---

### 3.9 외부 API 어댑터

#### 3.9.1 Google Routes API

`app/external/routes_api.py`

```python
import httpx
from app.core.config import settings
from app.api.models.schemas import Location

async def compute_route_durations(locations: list[Location]) -> list[int]:
    """
    locations[i] -> locations[i+1] 간 이동 시간(분)을 리스트로 반환
    """
    if not settings.google_routes_api_key or len(locations) < 2:
        return [0] * (len(locations) - 1)

    url = "https://routes.googleapis.com/directions/v2:computeRoutes"
    headers = {
        "X-Goog-Api-Key": settings.google_routes_api_key,
        "X-Goog-FieldMask": "routes.duration,routes.distanceMeters",
    }

    # 간단히 첫 route만 사용
    durations = []
    async with httpx.AsyncClient(timeout=10) as client:
        for i in range(len(locations) - 1):
            origin = locations[i]
            dest = locations[i+1]
            body = {
              "origin": {
                "location": {
                  "latLng": {"latitude": origin.lat, "longitude": origin.lng}
                }
              },
              "destination": {
                "location": {
                  "latLng": {"latitude": dest.lat, "longitude": dest.lng}
                }
              },
              "travelMode": "DRIVE"
            }
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
            duration_str = data["routes"][0]["duration"]  # "1234s"
            seconds = int(duration_str.replace("s",""))
            durations.append(seconds // 60)
    return durations
```

이 함수를 `enrich_with_routes`나 `apply_changes`에서 호출해 `time` 조정에 활용.

#### 3.9.2 crawl4ai (선택)

`app/external/crawl4ai_client.py`

* 간단한 래퍼만 두고,

  * 특정 POI 이름 + 도시로 검색 → 상위 1~3개 페이지의 설명/리뷰 텍스트 추출
* `enrich_with_details` 노드에서 이 텍스트를 LLM에 함께 넘겨 보다 현실적인 설명/팁 생성

---

### 3.10 OpenAI 클라이언트

`app/ai/openai_client.py`

```python
from openai import AsyncOpenAI
from app.core.config import settings

client = AsyncOpenAI(api_key=settings.openai_api_key)
```

각 노드에서:

```python
from .openai_client import client

async def call_llm_for_pois(planner_data: PlannerData) -> list[dict]:
    prompt = "..."  # 여행 목적/취향을 설명하는 prompt
    resp = await client.chat.completions.create(
        model=settings.openai_model_itinerary,
        messages=[...],
        response_format={"type": "json_object"},
    )
    data = json.loads(resp.choices[0].message.content)
    return data["pois"]
```

---

### 3.11 FastAPI 라우터 설계

#### 3.11.1 의존성 (DI)

`app/dependencies.py`

```python
from fastapi import Depends
from app.domain.repositories import InMemoryItineraryRepository, ItineraryRepository
from app.domain.services.itinerary_service import ItineraryService
from app.domain.services.chat_service import ChatService
from app.core.config import settings

_repo = InMemoryItineraryRepository()  # 나중에 Supabase 구현체로 교체

def get_itinerary_repo() -> ItineraryRepository:
    return _repo

def get_itinerary_service(repo: ItineraryRepository = Depends(get_itinerary_repo)):
    return ItineraryService(repo)

def get_chat_service(repo: ItineraryRepository = Depends(get_itinerary_repo)):
    return ChatService(repo)
```

#### 3.11.2 Itineraries 라우터

`app/api/routers/itineraries.py`

```python
from fastapi import APIRouter, Depends, HTTPException, status
from app.api.models.schemas import (
    CreateItineraryRequest, CreateItineraryResponse,
    Itinerary as ItinerarySchema,
)
from app.domain.services.itinerary_service import ItineraryService
from app.dependencies import get_itinerary_service

router = APIRouter(prefix="/itineraries", tags=["itineraries"])

@router.post("", response_model=CreateItineraryResponse, status_code=status.HTTP_201_CREATED)
async def create_itinerary(
    body: CreateItineraryRequest,
    svc: ItineraryService = Depends(get_itinerary_service),
):
    try:
        entity = await svc.create_itinerary(body.plannerData)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return entity.to_api_model()

@router.get("/{itinerary_id}", response_model=ItinerarySchema)
async def get_itinerary(
    itinerary_id: str,
    svc: ItineraryService = Depends(get_itinerary_service),
):
    try:
        entity = await svc.get_itinerary(itinerary_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Itinerary not found")
    return entity.to_api_model()
```

#### 3.11.3 Chat 라우터

`app/api/routers/chat.py`

```python
from fastapi import APIRouter, Depends, HTTPException
from app.api.models.schemas import ChatRequest, ChatResponse, ApplyPreviewRequest, ApplyPreviewResponse
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
        raise HTTPException(status_code=404, detail="Itinerary not found")

@router.post("/{itinerary_id}/apply-preview", response_model=ApplyPreviewResponse)
async def apply_preview(
    itinerary_id: str,
    body: ApplyPreviewRequest,
    svc: ItineraryService = Depends(get_itinerary_service),
):
    try:
        entity = await svc.apply_changes(itinerary_id, body.changes)
    except KeyError:
        raise HTTPException(status_code=404, detail="Itinerary not found")

    return ApplyPreviewResponse(
        updatedItinerary=entity.to_api_model(),
        systemMessage="선택하신 변경사항을 일정에 반영했습니다.",
    )
```

#### 3.11.4 Meta 라우터 (선택)

`app/api/routers/meta.py`

* `GET /api/v1/meta/countries`
* `GET /api/v1/meta/cities?countryId=france`
* `GET /api/v1/meta/styles`

→ 초기에는 단순 JSON 상수 리턴, 나중에 DB로 이전.

---

### 3.12 main.py

`app/main.py`

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api.routers import itineraries, chat, meta

app = FastAPI(title=settings.project_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(itineraries.router, prefix=settings.api_v1_prefix)
app.include_router(chat.router, prefix=settings.api_v1_prefix)
app.include_router(meta.router, prefix=settings.api_v1_prefix)

@app.get("/health")
async def health():
    return {"status": "ok"}
```

---

### 3.13 프론트 연동 요약 (최종)

1. **`TravelPlanner`**

   * `handleGenerateItinerary`에서:

     ```ts
     const res = await fetch("https://backend-domain/api/v1/itineraries", {
       method: "POST",
       headers: { "Content-Type": "application/json" },
       body: JSON.stringify({ plannerData: data }),
     })
     const itinerary: Itinerary = await res.json()
     setItinerary(itinerary)
     setShowResults(true)
     ```

2. **`ItineraryResults`**

   * `data` 대신 `itinerary`를 props로 받고,
   * `ItineraryOverview` → `itinerary.overview`
   * `DailyDetailPage` → `itinerary.activitiesByDay[selectedDay]`

3. **`ItineraryChat`**

   * `handleSend`, `handleQuickAction`, `handleApplyChanges`, `handleSelectRestaurant`에서

     * `POST /api/v1/itineraries/{itinerary.id}/chat`
     * `POST /api/v1/itineraries/{itinerary.id}/apply-preview`
   * 응답의 `reply`는 `messages`에 push
   * 응답의 `updatedItinerary`가 있으면 상위 state로 올려 Itinerary 전체 갱신

---

여기까지가 **FastAPI + OpenAI + LangGraph + (Supabase/Google Routes/crawl4ai 선택)**를 포함한
완전한 백엔드 설계서입니다

