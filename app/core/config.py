from functools import lru_cache
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    project_name: str = "Trip Planner API"
    api_v1_prefix: str = "/api/v1"

    openai_api_key: str = Field(default="", description="Optional OpenAI API key")
    openai_model_itinerary: str = "gpt-4.1"
    openai_model_chat: str = "gpt-4.1-mini"

    google_places_api_key: str | None = None
    google_routes_api_key: str | None = None
    firecrawl_api_key: str | None = None

    supabase_url: str | None = None
    supabase_anon_key: str | None = None

    use_supabase: bool = False

    cors_origins: List[str] = Field(default_factory=lambda: ["*"])

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[arg-type]


settings = get_settings()
