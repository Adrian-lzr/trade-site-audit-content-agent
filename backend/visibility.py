"""Public Phase 5 visibility monitoring surface.

Compatibility exports keep the provider and worker narrow interfaces discoverable
from one module while their implementations remain separately testable.
"""

from .visibility_provider import (
    FixtureVisibilityProvider,
    ManualCaptureVisibilityProvider,
    StructuredHTTPVisibilityProvider,
    VisibilityProvider,
    VisibilityResponse,
    build_visibility_provider,
)
from .models import VisibilityRunStatus, VisibilitySampleStatus
from .visibility_worker import VisibilityWorker, calculate_visibility_metrics

__all__ = [
    "FixtureVisibilityProvider",
    "ManualCaptureVisibilityProvider",
    "StructuredHTTPVisibilityProvider",
    "VisibilityProvider",
    "VisibilityResponse",
    "VisibilityRunStatus",
    "VisibilitySampleStatus",
    "VisibilityWorker",
    "build_visibility_provider",
    "calculate_visibility_metrics",
]
