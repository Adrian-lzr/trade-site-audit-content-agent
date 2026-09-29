from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./backend.db")
    request_timeout: float = float(os.getenv("CRAWLER_TIMEOUT", "10"))
    max_redirects: int = int(os.getenv("CRAWLER_MAX_REDIRECTS", "5"))
    allow_loopback: bool = os.getenv("ALLOW_LOOPBACK", "false").lower() in {"1", "true", "yes"}


settings = Settings()
