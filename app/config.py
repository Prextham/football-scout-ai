from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    # LLM
    anthropic_api_key: str
    fast_model: str = "claude-haiku-4-5-20251001"
    report_model: str = "claude-sonnet-4-20250514"

    # Search
    tavily_api_key: str

    # Server
    port: int = 8000
    cors_origins: str = "http://localhost:3000"

    # Database
    database_path: str = "./data/sessions.db"

    # Limits
    max_concurrent_sessions: int = 3
    max_verification_attempts: int = 2
    max_search_results_per_topic: int = 5

    # Mode
    default_mode: str = "deep"

    def get_cors_origins(self) -> List[str]:
        """Parse cors_origins whether it's a string or JSON array."""
        import json
        try:
            parsed = json.loads(self.cors_origins)
            if isinstance(parsed, list):
                return parsed
            return [str(parsed)]
        except Exception:
            return [o.strip() for o in self.cors_origins.split(",")]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings() #type: ignore