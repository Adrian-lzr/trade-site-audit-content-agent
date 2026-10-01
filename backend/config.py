from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation


def _decimal_env(name: str, default: str) -> Decimal:
    """Read a non-negative fixed precision budget from the environment.

    Budget values are kept as ``Decimal`` so comparisons cannot silently lose
    cents or micro-dollars through binary floating point conversion.
    """

    raw = os.getenv(name, default).strip()
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be a non-negative decimal") from exc
    if value.is_nan() or value.is_infinite() or value < 0:
        raise ValueError(f"{name} must be a non-negative decimal")
    return value.quantize(Decimal("0.000001"))


def _non_negative_int_env(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a non-negative integer") from exc
    if value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./backend.db")
    request_timeout: float = float(os.getenv("CRAWLER_TIMEOUT", "10"))
    max_redirects: int = int(os.getenv("CRAWLER_MAX_REDIRECTS", "5"))
    fixture_port: int = int(os.getenv("FIXTURE_PORT", "8765"))
    allow_loopback: bool = os.getenv("ALLOW_LOOPBACK", "false").lower() in {"1", "true", "yes"}
    # A value of zero disables that cap.  The run cap limits a single visibility
    # run; the daily cap is shared by all runs in one workspace and UTC day.
    visibility_max_run_budget_usd: Decimal = field(
        default_factory=lambda: _decimal_env("VISIBILITY_MAX_RUN_BUDGET_USD", "0")
    )
    visibility_daily_budget_usd: Decimal = field(default_factory=lambda: _decimal_env("VISIBILITY_DAILY_BUDGET_USD", "0"))
    # Raw provider bodies are useful evidence, but must have an explicit finite
    # retention policy and size bound.  Parsed answer/citation fields remain.
    raw_evidence_retention_days: int = field(default_factory=lambda: _non_negative_int_env("VISIBILITY_RAW_RETENTION_DAYS", 30))
    raw_evidence_max_bytes: int = field(default_factory=lambda: _non_negative_int_env("VISIBILITY_RAW_MAX_BYTES", 200_000))


settings = Settings()
