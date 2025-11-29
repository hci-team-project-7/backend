import logging

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routers import chat, itineraries, meta
from app.core.config import settings
from app.core.errors import APIError, error_content
from app.core.logging import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

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


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if isinstance(exc, APIError):
        return JSONResponse(
            status_code=exc.status_code,
            content=error_content(exc.code, str(exc.detail), exc.details),
        )
    code = "NOT_FOUND" if exc.status_code == status.HTTP_404_NOT_FOUND else "VALIDATION_ERROR" if 400 <= exc.status_code < 500 else "INTERNAL_ERROR"
    return JSONResponse(status_code=exc.status_code, content=error_content(code, str(exc.detail)))


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
    first_error = exc.errors()[0] if exc.errors() else {}
    loc = first_error.get("loc", [])
    # Strip initial "body" to match spec path style
    path = ".".join(str(item) for item in loc if item != "body")
    details = {"field": path, "reason": first_error.get("msg")}
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content=error_content("VALIDATION_ERROR", "요청이 유효하지 않습니다.", details),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled server error: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=error_content("INTERNAL_ERROR", "서버에서 오류가 발생했습니다."),
    )
