# Trip Planner 백엔드 (FastAPI) 실행 가이드

로컬에서 FastAPI 서버를 실행할 때 필요한 환경 변수와 절차를 정리했습니다.

## 기본 정보
- 기본 포트: `8000`
- API prefix: `/api/v1`

## 사전 요구사항
- Python 3.10+ 권장 (venv 사용)
- OpenAI/Google/Firecrawl/Supabase 키는 선택 사항이며, 없으면 기본/샘플 동작으로 대체됩니다.

## 환경 변수 (`backend/.env`)
```
# OpenAI (여행 일정/챗 품질 향상)
OPENAI_API_KEY=sk-...
OPENAI_MODEL_ITINERARY=gpt-4.1          # 선택, 기본 gpt-4.1
OPENAI_MODEL_CHAT=gpt-4.1-mini          # 선택, 기본 gpt-4.1-mini

# Google API (장소/경로 추천 고도화)
GOOGLE_PLACES_API_KEY=your-places-key   # 없으면 내부 기본 POI만 사용
GOOGLE_ROUTES_API_KEY=your-routes-key   # 없으면 이동 시간/거리 기본값 사용

# 외부 검색
FIRECRAWL_API_KEY=your-firecrawl-key    # 없으면 위키 요약으로 대체

# 데이터 영속화 (선택)
USE_SUPABASE=false                      # true로 설정 시 Supabase 사용
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_ANON_KEY=your-anon-key

# CORS 설정 (프론트 도메인 화이트리스트)
CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
```
- 리스트 계열(`CORS_ORIGINS`)은 쉼표로 구분하거나 JSON 배열 형태(`["http://localhost:3000"]`)로 넣어도 됩니다.

## 설치 및 실행
```
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```
- 실행 후 헬스 체크: `GET http://localhost:8000/health`
- Swagger UI: `http://localhost:8000/docs`
- API 상세 스펙: `backend/rest_api_spec.md`

