# 백엔드 REST API 설계안

---

## 1. 공통 정보

* **Base URL 예시**: `https://api.trip-planner.example.com/api/v1`
* **모든 요청/응답**: `Content-Type: application/json; charset=utf-8`

### 1.1 공통 에러 응답 형식

```jsonc
{
  "error": {
    "code": "VALIDATION_ERROR",   // 혹은 "NOT_FOUND", "INTERNAL_ERROR" 등
    "message": "자세한 오류 메시지",
    "details": {
      "field": "plannerData.dateRange.start",
      "reason": "출발일은 오늘 이후여야 합니다."
    }
  }
}
```

사용할 수 있는 대표 status code:

* `400 Bad Request`  – 필수 값 누락, 형식 오류
* `404 Not Found`    – `itineraryId` 없음 등
* `500 Internal Server Error` – 서버 내부 에러

---

## 2. 도메인 모델

### 2.1 여행 계획 입력: `PlannerData`

> 출처:
>
> * `components/travel-planner.tsx` 의 `interface PlannerData`
> * `components/steps/step-*.tsx` 의 `updateData(...)` 호출

```ts
interface PlannerData {
  country: string | null
  cities: string[]
  dateRange: { start: string; end: string } | null // "YYYY-MM-DD"
  travelers: {
    adults: number
    children: number
    type: string              // 예: "Solo traveler", "커플", "가족" 등
  }
  styles: string[]            // 예: ["culture", "food", "relaxation"]
}
```

> API에서는 **null이 아니라 모두 채워진 상태**를 요구할 것 (null이면 보내지 않기)
> → `POST /itineraries` 의 body에는 `country`, `dateRange` 등 필수.

---

### 2.2 Location (지도/루트용)

> 출처:
>
> * `components/itinerary/day-sidebar.tsx`
> * `components/itinerary/itinerary-map.tsx`

```ts
interface Location {
  name: string      // 장소명 (예: "공항", "타임스퀘어")
  time: string      // 프론트는 "10:00", "10:00 AM" 등 자유 형식 문자열을 사용 중
  lat: number       // 위도
  lng: number       // 경도
}
```

---

### 2.3 DayItinerary (하루 요약)

> 출처:
>
> * `components/itinerary/day-sidebar.tsx`
> * `components/itinerary/itinerary-map.tsx`
> * `components/itinerary/overview.tsx` 의 `MOCK_ITINERARY` 상수

```ts
interface DayItinerary {
  day: number                // 1, 2, 3...
  date: string               // "YYYY-MM-DD"
  title: string              // 하루 제목 (예: "도시 도착 및 탐험")
  photo: string              // 대표 이미지 경로/URL (예: "/city-arrival.jpg")
  activities: string[]       // 간단한 키워드 목록 (예: ["공항 도착", "호텔 체크인", ...])
  locations: Location[]      // 위의 Location[]
}
```

---

### 2.4 Activity (하루 상세 타임라인)

> 출처:
>
> * `components/itinerary/daily-detail.tsx`
> * `components/itinerary/activity-timeline.tsx`
> * `components/itinerary/activity-detail.tsx`

```ts
interface Activity {
  id: string
  name: string            // "공항 도착"
  location: string        // "국제공항"
  time: string            // "10:00 AM"
  duration: string        // "1시간", "1.5시간"
  description: string
  image: string           // "/airport-arrival.jpg"
  openHours: string       // "24시간", "11:00 AM - 11:00 PM"
  price: string           // "$30-50", "무료", "1박 $150"
  tips: string[]
  nearbyFood: string[]    // 주변 추천 음식/가게 명
  estimatedDuration: string
  bestTime: string        // "오후", "저녁"
}
```

프론트에서:

* `DailyDetailPage`는 `Activity[]`를 받아 `ActivityTimeline`, `ActivityDetail`로 넘깁니다.

---

### 2.5 Itinerary (전체 일정 객체)

백엔드에서 관리할 **최상위 일정 엔티티** 구조를 아래처럼 정의합니다.

```ts
interface Itinerary {
  id: string                // 예: UUID 문자열
  plannerData: PlannerData  // 사용자가 입력한 원본 정보

  // 개요 화면용 (기존 MOCK_ITINERARY 대체)
  overview: DayItinerary[]  // DayItinerary 배열

  // 하루 상세용 (기존 ACTIVITIES 상수 대체)
  activitiesByDay: Record<number, Activity[]> // 예: { "1": [...], "2": [...] }

  createdAt: string         // ISO 날짜 문자열
  updatedAt: string
}
```

> 🔗 프론트 매핑 가이드 (중요):
>
> * `overview` → 기존 `MOCK_ITINERARY` 대신 사용
> * `activitiesByDay[day]` → 기존 `ACTIVITIES[day]` 대신 사용

---

### 2.6 Chat 메시지 & Preview 타입

> 출처: `components/itinerary-chat.tsx` 의 `interface Message`

```ts
type ChatSender = "user" | "assistant"

interface ChatChange {
  action: "add" | "remove" | "modify" | "transport"
  day?: number
  location?: string
  details?: string
}

interface ChatRestaurantRecommendation {
  name: string
  location: string
  rating?: number
  cuisine?: string
}

interface ChatPreview {
  type: "change" | "recommendation"
  title: string
  changes?: ChatChange[]
  recommendations?: ChatRestaurantRecommendation[]
}

interface ChatMessage {
  id: string
  text: string
  sender: ChatSender
  timestamp: string    // API에서는 Date 객체 대신 ISO 문자열 사용
  preview?: ChatPreview
}
```

프론트에서:

* `ItineraryChat` 컴포넌트는 `preview`를 사용해
  * “변경사항 적용” 버튼
  * 맛집 추천 리스트 선택
    등을 렌더링합니다.

---

## 3. API 목록

### 3.1 여행 일정 생성 (위저드 → 일정 생성)

#### `POST /api/v1/itineraries`

**역할**

* `components/travel-planner.tsx` 의 `handleGenerateItinerary` 에서 호출할 엔드포인트
* 현재는 `setShowResults(true)`만 하지만, 실제 구현 시:
  1. `PlannerData` 전송
  2. 백엔드에서 AI/로직으로 일정 생성
  3. `Itinerary` 객체 반환
  4. 반환값을 `ItineraryResults`에 넘기도록 프론트 수정

**요청**

* Headers:
  * `Content-Type: application/json`
* Body (위저드 완료 후):

```jsonc
{
  "plannerData": {
    "country": "프랑스",
    "cities": ["파리", "니스"],
    "dateRange": {
      "start": "2025-06-01",
      "end": "2025-06-07"
    },
    "travelers": {
      "adults": 2,
      "children": 0,
      "type": "couple"
    },
    "styles": ["culture", "food"]
  }
}
```

**응답 (성공: 201 Created)**

```jsonc
{
  "id": "itn_01JABCDXYZ",
  "plannerData": {
    "country": "프랑스",
    "cities": ["파리", "니스"],
    "dateRange": {
      "start": "2025-06-01",
      "end": "2025-06-07"
    },
    "travelers": {
      "adults": 2,
      "children": 0,
      "type": "couple"
    },
    "styles": ["culture", "food"]
  },
  "overview": [
    {
      "day": 1,
      "date": "2025-06-01",
      "title": "도시 도착 및 탐험",
      "photo": "/city-arrival.jpg",
      "activities": ["공항 도착", "호텔 체크인", "저녁 시가지 산책", "지역 레스토랑 식사"],
      "locations": [
        { "name": "공항", "time": "10:00", "lat": 40.7128, "lng": -74.0060 },
        { "name": "호텔", "time": "12:00", "lat": 40.7580, "lng": -73.9855 },
        { "name": "시가지 산책", "time": "17:00", "lat": 40.7580, "lng": -73.9855 },
        { "name": "레스토랑", "time": "19:30", "lat": 40.7489, "lng": -73.9680 }
      ]
    }
    // day 2~N ...
  ],
  "activitiesByDay": {
    "1": [
      {
        "id": "1-1",
        "name": "공항 도착",
        "location": "국제공항",
        "time": "10:00 AM",
        "duration": "1시간",
        "description": "공항에 도착하여 짐을 받습니다.",
        "image": "/airport-arrival.jpg",
        "openHours": "24시간",
        "price": "무료",
        "tips": ["도착 후 바로 환전", "데이터 유심 구매"],
        "nearbyFood": ["공항 푸드코트", "카페"],
        "estimatedDuration": "1시간",
        "bestTime": "오전"
      }
      // 1-2, 1-3 ...
    ],
    "2": [
      // day 2 activities...
    ]
  },
  "createdAt": "2025-05-01T10:00:00Z",
  "updatedAt": "2025-05-01T10:00:00Z"
}
```

**에러 예시**

* `400 Bad Request`

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "country, dateRange는 필수입니다."
  }
}
```

---

### 3.2 일정 조회

#### `GET /api/v1/itineraries/{itineraryId}`

**역할**

* 새로고침 후 복원, 나중에 “저장된 일정 다시 불러오기” 등을 할 때 사용
* 응답 형식은 `POST /itineraries`와 동일한 `Itinerary`

**요청**

* Path:
  * `itineraryId`: `itn_01JABCDXYZ` 같은 문자열

**응답 (성공: 200)**

```jsonc
{
  "id": "itn_01JABCDXYZ",
  "plannerData": { ... },
  "overview": [ ... ],
  "activitiesByDay": { ... },
  "createdAt": "2025-05-01T10:00:00Z",
  "updatedAt": "2025-05-01T10:05:00Z"
}
```

**에러**

* `404 Not Found` – 없는 ID

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Itinerary not found"
  }
}
```

---

### 3.3 일정 수정/추천을 위한 챗봇 API

> 프론트 출처: `components/itinerary-chat.tsx`
>
> * 현재는 setTimeout으로 가짜 응답 생성
> * `Message` 타입, `preview.type === "change" | "recommendation"` 구조를 사용

#### `POST /api/v1/itineraries/{itineraryId}/chat`

**역할**

* 유저가 메시지를 보내거나, “빠른 액션(장소 제거/추가/교통 변경/맛집 추천)”으로 질문을 보낼 때 호출
* 백엔드는
  * 자연어 응답(`text`)
  * 필요 시 `preview`(변경 또는 추천)
  * 필요 시 `updatedItinerary` (실제 일정 업데이트 결과)
    를 반환

**요청**

```jsonc
{
  "message": {
    "text": "2일차 오후 일정을 조금 더 여유롭게 바꿔줘",
    "timestamp": "2025-05-01T10:10:00Z"
  },
  "context": {
    "currentView": "daily",          // "overview" | "daily"
    "currentDay": 2,
    "pendingAction": null            // "remove" | "add" | "transport" | "restaurant" | null
  }
}
```

> * `pendingAction`은 프론트의 `handleQuickAction`에서 결정되던 값과 동일한 의미
> * 백엔드는 이 정보를 참고해서 더 구조화된 `preview.changes` 또는 `preview.recommendations`를 생성

**응답 (성공: 200)**

```jsonc
{
  "reply": {
    "id": "msg_01JABCDEF",
    "text": "2일차 오후 일정을 조금 더 여유롭게 조정해 보았습니다. 주요 관광지는 유지하면서 이동 간격을 넓혔어요.",
    "sender": "assistant",
    "timestamp": "2025-05-01T10:10:02Z",
    "preview": {
      "type": "change",
      "title": "2일차 오후 일정 조정 제안",
      "changes": [
        {
          "action": "remove",
          "day": 2,
          "location": "박물관 B",
          "details": "이동 시간이 길어져서 방문 제거 제안"
        },
        {
          "action": "add",
          "day": 2,
          "location": "근처 카페",
          "details": "산책 후 휴식 장소 추가"
        }
      ]
    }
  },

  // 옵션: 실제로 이미 일정을 수정해서 반환하고 싶다면 포함
  "updatedItinerary": {
    "id": "itn_01JABCDXYZ",
    "plannerData": { ... },
    "overview": [ ... ],          // 변경 반영 후 데이터
    "activitiesByDay": { ... },
    "createdAt": "2025-05-01T10:00:00Z",
    "updatedAt": "2025-05-01T10:10:02Z"
  }
}
```

#### 맛집 추천 케이스 예시

`pendingAction = "restaurant"` 인 상태에서:

**요청**

```jsonc
{
  "message": {
    "text": "타임스퀘어 근처에서 저녁 먹을만한 곳 추천해줘",
    "timestamp": "2025-05-01T10:15:00Z"
  },
  "context": {
    "currentView": "daily",
    "currentDay": 1,
    "pendingAction": "restaurant"
  }
}
```

**응답**

```jsonc
{
  "reply": {
    "id": "msg_01JFOOD123",
    "text": "타임스퀘어 근처 추천 맛집입니다. 마음에 드는 곳을 선택하시면 일정에 추가해 드릴게요.",
    "sender": "assistant",
    "timestamp": "2025-05-01T10:15:02Z",
    "preview": {
      "type": "recommendation",
      "title": "타임스퀘어 근처 맛집 추천",
      "recommendations": [
        {
          "name": "전통 파스타 레스토랑",
          "location": "타임스퀘어 도보 5분",
          "rating": 4.5,
          "cuisine": "이탈리안"
        },
        {
          "name": "로컬 맛집 카페",
          "location": "타임스퀘어 도보 3분",
          "rating": 4.8,
          "cuisine": "카페/브런치"
        },
        {
          "name": "시장 음식 스탠드",
          "location": "타임스퀘어 도보 10분",
          "rating": 4.2,
          "cuisine": "스트리트푸드"
        }
      ]
    }
  },
  "updatedItinerary": null
}
```

프론트에서는:

* `preview.recommendations`를 카드로 렌더링 (지금 `ItineraryChat`가 하고 있는 것 그대로)
* `handleSelectRestaurant(...)`에서 사용자가 선택 시 아래 3.4의 apply API를 치도록 변경

---

### 3.4 Preview 기반 일정 변경 적용 API

> 출처: `ItineraryChat` 의 `handleApplyChanges`, `handleSelectRestaurant`
> 현재는 “UI만 - 실제 로직은 나중에”라고 주석만 있고, 실제 일정 데이터는 안 바뀜.

#### `POST /api/v1/itineraries/{itineraryId}/apply-preview`

**역할**

* `preview.changes` 또는 `preview.recommendations`를 **실제로 `Itinerary`에 반영**하는 엔드포인트
* 사용 위치:
  * “변경사항 적용” 버튼 클릭 시
  * 맛집 추천 중 하나 선택 시

**요청 (변경사항 적용)**

```jsonc
{
  "sourceMessageId": "msg_01JABCDEF",   // preview를 가진 assistant 메시지 ID
  "changes": [
    {
      "action": "remove",
      "day": 2,
      "location": "박물관 B",
      "details": "이동 시간이 길어 제거"
    },
    {
      "action": "add",
      "day": 2,
      "location": "근처 카페",
      "details": "여유로운 휴식 장소 추가"
    }
  ]
}
```

**요청 (맛집 선택 케이스)**

```jsonc
{
  "sourceMessageId": "msg_01JFOOD123",
  "changes": [
    {
      "action": "add",
      "day": 1,
      "location": "전통 파스타 레스토랑",
      "details": "타임스퀘어 근처 저녁 식사 장소로 추가"
    }
  ]
}
```

**응답 (성공: 200)**

```jsonc
{
  "updatedItinerary": {
    "id": "itn_01JABCDXYZ",
    "plannerData": { ... },
    "overview": [ ... ],          // DayItinerary에 음식점이 summary에 반영될 수도 있음
    "activitiesByDay": { ... },   // 해당 day의 Activity[]에 새 Activity 추가 등
    "createdAt": "2025-05-01T10:00:00Z",
    "updatedAt": "2025-05-01T10:20:00Z"
  },
  "systemMessage": "선택하신 변경사항을 일정에 반영했습니다."
}
```

프론트에서는:

* 응답의 `updatedItinerary`를 상위 상태(예: `ItineraryResults`)에 반영
* `ItineraryOverview`, `DailyDetailPage`, `ItineraryChat`가 모두 **동일 Itinerary 상태를 바라보도록** 구조 조정

---

### 3.5 (선택) 메타 데이터 API (나라/도시/스타일)

현재는 전부 프론트 하드코딩:

* `components/steps/step-1-destination.tsx` 의 `popularCountries`
* `components/steps/step-2-cities.tsx` 의 `cityDatabase`
* `components/steps/step-5-style.tsx` 의 style card 데이터

나중에 서버에서 관리하고 싶다면:

#### `GET /api/v1/meta/countries`

```jsonc
[
  { "id": "france", "name": "프랑스", "flag": "🇫🇷", "landmark": "/eiffel-tower-paris.png" },
  { "id": "japan",  "name": "일본",   "flag": "🇯🇵", "landmark": "/mount-fuji-japan.png" }
]
```

#### `GET /api/v1/meta/cities?countryId=france`

```jsonc
[
  { "id": "paris", "name": "파리", "image": "/paris-eiffel-tower.png" },
  { "id": "nice",  "name": "니스", "image": "/nice-city-coast.jpg" }
]
```

#### `GET /api/v1/meta/styles`

```jsonc
[
  { "id": "culture", "name": "문화 & 역사", "icon": "🏛️", "image": "/culture-history.jpg" },
  { "id": "relaxation", "name": "휴식", "icon": "🧘", "image": "/relax.jpg" }
]
```

---

## 4. 프론트 연동 포인트 요약

1. **`components/travel-planner.tsx`**

   * 현재:
     * `PlannerData` 상태 수집 (`country`, `cities`, `dateRange`, `travelers`, `styles`)
     * `handleGenerateItinerary` → `setShowResults(true)`만 수행
   * 변경:
     * `handleGenerateItinerary`에서 `POST /api/v1/itineraries` 호출
     * 응답으로 받은 `Itinerary`를 상태로 관리 (`const [itinerary, setItinerary] = useState<Itinerary | null>(null)`)
     * `ItineraryResults`에 `plannerData`와 함께 `itinerary`를 props로 전달

2. **`components/itinerary-results.tsx`**

   * 현재:
     * `MOCK_ITINERARY`와 `ACTIVITIES`를 내부에서 사용 (실제로는 `overview.tsx`, `daily-detail.tsx` 안에 하드코딩)
   * 변경:
     * props로 `itinerary: Itinerary`를 받음
     * `ItineraryOverview`에는 `itinerary.overview` 전달
     * `DailyDetailPage`에는 `itinerary.activitiesByDay[currentDay]` 전달하도록 구조 조정

3. **`components/itinerary/overview.tsx`**

   * 현재:
     * `MOCK_ITINERARY` 상수 사용
   * 변경:
     * `const MOCK_ITINERARY` 제거
     * props로 `itinerary: DayItinerary[]` 받아서 그대로 렌더

4. **`components/itinerary/daily-detail.tsx`**

   * 현재:
     * `const ACTIVITIES: Record<number, Activity[]>` 하드코딩 사용
   * 변경:
     * `ACTIVITIES` 제거
     * props로 `activities: Activity[]` 또는 `itinerary: Itinerary` + `day`를 받아서 `activitiesByDay[day]` 사용

5. **`components/itinerary-chat.tsx`**

   * 현재:
     * 모든 로직이 클라이언트에서만 동작 (setTimeout)
     * `handleSend`, `handleQuickAction`, `handleApplyChanges`, `handleSelectRestaurant` 등에서 네트워크 사용 없음
   * 변경:
     * `handleSend` → `POST /api/v1/itineraries/{id}/chat` 호출
     * 응답의 `reply`를 `messages`에 추가
     * `updatedItinerary`가 있을 경우 상위 상태로 올려서 `ItineraryResults`의 일정과 동기화
     * `handleApplyChanges`, `handleSelectRestaurant` → `POST /api/v1/itineraries/{id}/apply-preview` 호출로 변경

---

## 5. 요약

* 이 문서대로 백엔드를 구현하면:
  1. 위저드 입력(`PlannerData`)을 백엔드로 보내 **실제 일정(`Itinerary`)** 을 생성하고
  2. 개요/지도/타임라인/상세 화면은 `overview`, `activitiesByDay` 기반으로 렌더하며
  3. 챗봇은 `/chat` + `/apply-preview`를 통해
     * 변경 제안(`preview`)
     * 실제 일정 업데이트(`updatedItinerary`)
       를 백엔드와 주고받을 수 있습니다.
