"""Narrow visibility source interfaces and explicit provider adapters.

The visibility monitor deliberately keeps this interface separate from the
content-generation gateway. A provider response is evidence: callers must
persist its raw body and the parsed fields together, and an unavailable source
must never be represented as a successful sample.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from .config import settings
from .models import VisibilitySampleStatus


PROVIDER_KINDS = {"model_api", "consumer_search_surface", "manual_capture"}


_SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(
        r"(?i)(\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password|secret)\b\s*[:=]\s*[\"']?)[^\s,}\"']+"
    ),
    re.compile(r"(?i)([?&](?:api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password)=)[^&#\s]+"),
    re.compile(r"\b(?:sk|rk|ghp|github_pat|xox[baprs])-[A-Za-z0-9_.-]{8,}\b", re.IGNORECASE),
)


def redact_sensitive_text(value: str | None, *, max_bytes: int | None = None) -> str | None:
    """Remove credentials from provider evidence and bound its stored size.

    Provider bodies are evidence and may contain arbitrary text, so this uses
    conservative token/key patterns rather than assuming JSON.  The helper is
    also used for answer and error fields before they can reach logs or the
    database.  Truncation is byte based and preserves valid UTF-8.
    """

    if value is None:
        return None
    text = str(value)
    for pattern in _SENSITIVE_VALUE_PATTERNS:
        if pattern.pattern.startswith("\\b(?:sk"):
            text = pattern.sub("<redacted>", text)
        else:
            text = pattern.sub(lambda match: f"{match.group(1)}<redacted>", text)
    limit = settings.raw_evidence_max_bytes if max_bytes is None else max_bytes
    if limit and len(text.encode("utf-8")) > limit:
        marker = "\n[raw evidence truncated]"
        marker_bytes = marker.encode("utf-8")
        encoded = text.encode("utf-8")
        if limit <= len(marker_bytes):
            text = marker_bytes[:limit].decode("utf-8", errors="ignore")
        else:
            clipped = encoded[: limit - len(marker_bytes)].decode("utf-8", errors="ignore")
            text = f"{clipped}{marker}"
    return text


@dataclass(slots=True)
class VisibilityResponse:
    status: str = VisibilitySampleStatus.succeeded.value
    answered_question: bool | None = None
    raw_response: str | None = None
    answer_text: str | None = None
    citations: list[str] = field(default_factory=list)
    mentioned_domains: list[str] = field(default_factory=list)
    provider_request_id: str | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: Decimal | None = None
    error_code: str | None = None
    error_message: str | None = None


class VisibilityProvider(Protocol):
    """The only operation needed by the visibility sampling worker."""

    name: str
    kind: str
    model: str | None
    is_synthetic: bool

    def capabilities(self) -> dict[str, Any]: ...

    def pricing_basis(self) -> dict[str, Any]: ...

    def estimate_cost(self, question: str) -> Decimal: ...

    def sample(
        self,
        question: str,
        *,
        market: str,
        language: str,
        target_domain: str,
        request_id: str | None = None,
    ) -> VisibilityResponse: ...


class FixtureVisibilityProvider:
    """Deterministic offline evidence source used only when explicitly selected."""

    name = "fixture"
    kind = "model_api"
    model = "fixture-visibility-v1"
    is_synthetic = True

    def __init__(self, *, kind: str = "model_api"):
        if kind not in PROVIDER_KINDS:
            raise ValueError(f"unsupported visibility provider kind: {kind}")
        self.kind = kind

    def capabilities(self) -> dict[str, Any]:
        return {
            "available": True,
            "synthetic": True,
            "network": False,
            "supports_market": True,
            "supports_language": True,
            "supports_citations": True,
            "source_boundary": "offline_fixture",
        }

    def pricing_basis(self) -> dict[str, Any]:
        return {"kind": "fixed", "currency": "USD", "per_sample_usd": "0.000000", "source": "offline_fixture"}

    def estimate_cost(self, question: str) -> Decimal:
        return Decimal("0")

    def sample(
        self,
        question: str,
        *,
        market: str,
        language: str,
        target_domain: str,
        request_id: str | None = None,
    ) -> VisibilityResponse:
        normalized_domain = _normalize_domain(target_domain)
        request_id = "fixture-" + hashlib.sha256(f"{question}|{market}|{language}|{normalized_domain}".encode()).hexdigest()[:20]
        citations = [f"https://{normalized_domain}/"] if normalized_domain else []
        answer = (
            f"Synthetic fixture answer for the purchasing question: {question} "
            f"Target site reference: {citations[0] if citations else 'unavailable'}."
        )
        raw = json.dumps(
            {
                "synthetic": True,
                "question": question,
                "market": market,
                "language": language,
                "answer": answer,
                "citations": citations,
            },
            ensure_ascii=True,
            sort_keys=True,
        )
        return VisibilityResponse(
            raw_response=redact_sensitive_text(raw),
            answer_text=redact_sensitive_text(answer),
            citations=citations,
            mentioned_domains=[normalized_domain] if normalized_domain else [],
            provider_request_id=request_id,
            model=self.model,
            input_tokens=max(1, len(question.split())),
            output_tokens=max(1, len(answer.split())),
            cost_usd=Decimal("0"),
        )


class StructuredHTTPVisibilityProvider:
    """Configurable structured HTTP adapter for an actually reachable source.

    The endpoint is intentionally generic: a deployment can point it to an
    approved model API or search-surface adapter that returns JSON. Without an
    endpoint and credential the adapter returns ``unavailable`` and makes no
    network request. This prevents a missing integration from looking like a
    successful online measurement.
    """

    name = "structured_http"
    kind = "model_api"
    is_synthetic = False

    def __init__(
        self,
        *,
        url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        provider_kind: str = "model_api",
        timeout: float | None = None,
        input_price_per_1k: Decimal | str | None = None,
        output_price_per_1k: Decimal | str | None = None,
        client: httpx.Client | None = None,
    ):
        if provider_kind not in PROVIDER_KINDS:
            raise ValueError(f"unsupported visibility provider kind: {provider_kind}")
        self.kind = provider_kind
        self.url = (url or os.getenv("VISIBILITY_PROVIDER_URL", "")).strip() or None
        self.api_key = api_key or os.getenv("VISIBILITY_PROVIDER_API_KEY")
        self.model = model or os.getenv("VISIBILITY_PROVIDER_MODEL") or None
        self.timeout = timeout if timeout is not None else float(os.getenv("VISIBILITY_PROVIDER_TIMEOUT", "20"))
        self.input_price_per_1k = _decimal_env(input_price_per_1k, "VISIBILITY_INPUT_PRICE_PER_1K", Decimal("0"))
        self.output_price_per_1k = _decimal_env(output_price_per_1k, "VISIBILITY_OUTPUT_PRICE_PER_1K", Decimal("0"))
        self._client = client

    @property
    def available(self) -> bool:
        return bool(self.url and self.api_key)

    def capabilities(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "synthetic": False,
            "network": True,
            "supports_market": True,
            "supports_language": True,
            "supports_citations": True,
            "source_boundary": self.kind,
            "unavailable_reason": None if self.available else "credentials_or_endpoint_missing",
        }

    def pricing_basis(self) -> dict[str, Any]:
        return {
            "kind": "token_estimate",
            "currency": "USD",
            "input_price_per_1k": str(self.input_price_per_1k),
            "output_price_per_1k": str(self.output_price_per_1k),
            "source": "VISIBILITY_INPUT_PRICE_PER_1K/VISIBILITY_OUTPUT_PRICE_PER_1K",
            "estimated": True,
        }

    def estimate_cost(self, question: str) -> Decimal:
        # Reserve a conservative output allowance before making a paid call.
        input_tokens = max(1, len(question.split()))
        output_tokens = max(64, min(512, input_tokens * 4))
        return ((Decimal(input_tokens) / Decimal(1000)) * self.input_price_per_1k + (Decimal(output_tokens) / Decimal(1000)) * self.output_price_per_1k).quantize(Decimal("0.000001"))

    def sample(
        self,
        question: str,
        *,
        market: str,
        language: str,
        target_domain: str,
        request_id: str | None = None,
    ) -> VisibilityResponse:
        if not self.available:
            return VisibilityResponse(
                status=VisibilitySampleStatus.unavailable.value,
                model=self.model,
                error_code="provider_unavailable",
                error_message="structured visibility provider requires VISIBILITY_PROVIDER_URL and VISIBILITY_PROVIDER_API_KEY",
            )
        payload = {
            "question": question,
            "market": market,
            "language": language,
            "target_domain": _normalize_domain(target_domain),
            "model": self.model,
            "source_kind": self.kind,
        }
        if request_id:
            payload["request_id"] = request_id
        headers = {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json", "Content-Type": "application/json"}
        own_client = self._client is None
        client = self._client or httpx.Client(timeout=self.timeout)
        try:
            response = client.post(self.url, json=payload, headers=headers)
            raw = redact_sensitive_text(response.text)
            provider_request_id = response.headers.get("x-request-id")
            if response.status_code >= 400:
                return VisibilityResponse(
                    status=VisibilitySampleStatus.failed.value,
                    raw_response=raw,
                    provider_request_id=provider_request_id,
                    model=self.model,
                    error_code=f"http_{response.status_code}",
                    error_message=f"visibility provider returned HTTP {response.status_code}",
                )
            try:
                body = response.json()
            except (ValueError, json.JSONDecodeError) as exc:
                return VisibilityResponse(
                    status=VisibilitySampleStatus.failed.value,
                    raw_response=raw,
                    provider_request_id=provider_request_id,
                    model=self.model,
                    error_code="invalid_json",
                    error_message="visibility provider returned a non-JSON response",
                )
            if not isinstance(body, dict):
                return VisibilityResponse(
                    status=VisibilitySampleStatus.failed.value,
                    raw_response=raw,
                    provider_request_id=provider_request_id,
                    model=self.model,
                    error_code="invalid_shape",
                    error_message="visibility provider response must be a JSON object",
                )
            answer = _first_text(body, "answer_text", "answer", "text", "content")
            if not answer:
                return VisibilityResponse(
                    status=VisibilitySampleStatus.failed.value,
                    raw_response=raw,
                    provider_request_id=str(body.get("request_id") or provider_request_id or "") or None,
                    model=str(body.get("model") or self.model or "") or None,
                    error_code="missing_answer",
                    error_message="visibility provider response did not contain answer text",
                )
            citations = [redact_sensitive_text(item) or "" for item in _string_list(body.get("citations") or body.get("references") or body.get("sources"))]
            mentioned_domains = [redact_sensitive_text(item) or "" for item in _string_list(body.get("mentioned_domains") or body.get("domains"))]
            usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
            input_tokens = _int_or_none(body.get("input_tokens", usage.get("input_tokens", usage.get("prompt_tokens"))))
            output_tokens = _int_or_none(body.get("output_tokens", usage.get("output_tokens", usage.get("completion_tokens"))))
            cost = body.get("cost_usd")
            if cost is None and (input_tokens is not None or output_tokens is not None):
                cost = (Decimal(input_tokens or 0) / Decimal(1000)) * self.input_price_per_1k + (Decimal(output_tokens or 0) / Decimal(1000)) * self.output_price_per_1k
            return VisibilityResponse(
                raw_response=raw,
                answer_text=redact_sensitive_text(answer),
                answered_question=body.get("answered_question") if isinstance(body.get("answered_question"), bool) else None,
                citations=citations,
                mentioned_domains=mentioned_domains,
                provider_request_id=str(body.get("request_id") or provider_request_id or "") or None,
                model=str(body.get("model") or self.model or "") or None,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=_decimal_or_none(cost),
            )
        except httpx.TimeoutException:
            return VisibilityResponse(status=VisibilitySampleStatus.failed.value, error_code="timeout", error_message="visibility provider request timed out", model=self.model)
        except httpx.HTTPError as exc:
            return VisibilityResponse(status=VisibilitySampleStatus.failed.value, error_code="network_error", error_message=f"visibility provider request failed: {type(exc).__name__}", model=self.model)
        finally:
            if own_client:
                client.close()


class ManualCaptureVisibilityProvider:
    name = "manual_capture"
    kind = "manual_capture"
    model = None
    is_synthetic = False

    def capabilities(self) -> dict[str, Any]:
        return {
            "available": False,
            "synthetic": False,
            "network": False,
            "supports_market": False,
            "supports_language": False,
            "supports_citations": True,
            "source_boundary": "manual_capture",
            "unavailable_reason": "submit_manual_capture",
        }

    def pricing_basis(self) -> dict[str, Any]:
        return {"kind": "none", "currency": "USD", "per_sample_usd": "0.000000", "source": "manual_capture"}

    def estimate_cost(self, question: str) -> Decimal:
        return Decimal("0")

    def sample(
        self,
        question: str,
        *,
        market: str,
        language: str,
        target_domain: str,
        request_id: str | None = None,
    ) -> VisibilityResponse:
        return VisibilityResponse(
            status=VisibilitySampleStatus.unavailable.value,
            error_code="manual_capture_required",
            error_message="manual_capture provider waits for a human evidence submission",
        )


def build_visibility_provider(name: str, *, kind: str | None = None, model: str | None = None) -> VisibilityProvider:
    normalized = name.strip().lower()
    if normalized in {"fixture", "demo", "offline_fixture"}:
        if kind is not None and kind not in PROVIDER_KINDS:
            raise ValueError(f"unsupported visibility provider kind: {kind}")
        return FixtureVisibilityProvider(kind=kind or "model_api")
    if normalized in {"manual", "manual_capture"}:
        return ManualCaptureVisibilityProvider()
    if normalized in {"structured_http", "model_api", "real", "api", "consumer_search_surface", "search_surface"}:
        inferred_kind = kind or ("consumer_search_surface" if normalized in {"consumer_search_surface", "search_surface"} else "model_api")
        return StructuredHTTPVisibilityProvider(provider_kind=inferred_kind, model=model)
    raise ValueError(f"unknown visibility provider: {name}")


def _normalize_domain(value: str) -> str:
    candidate = value.strip()
    if "://" in candidate:
        candidate = urlsplit(candidate).hostname or ""
    return candidate.lower().strip(".")


def _decimal_env(value: Decimal | str | None, env_name: str, default: Decimal) -> Decimal:
    if value is not None:
        return _decimal_or_none(value) or default
    return _decimal_or_none(os.getenv(env_name)) or default


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if decimal.is_nan() or decimal.is_infinite() or decimal < 0:
        return None
    return decimal.quantize(Decimal("0.000001"))


def _int_or_none(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _first_text(body: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
