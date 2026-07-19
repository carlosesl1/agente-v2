"""Pure summary and confirmation boundary for Agente v2 Phase 4."""

from .binding import classification_context, classify_and_bind
from .classifier import (
    ConfirmationClassifier,
    ReferenceConfirmationClassifier,
    classify_safely,
)
from .presentation import prepare_summary
from .properties import Phase4PropertyReport, run_phase4_properties
from .renderer import RENDERER_ID, RENDERER_VERSION, render_summary
from .types import (
    BoundConfirmation,
    ClassificationContext,
    ClassificationInput,
    DecisionCandidate,
    PreparedSummary,
    RenderedSummary,
    SummaryLocale,
    rendered_summary_hash,
)

__all__ = [
    "SummaryLocale",
    "RenderedSummary",
    "PreparedSummary",
    "ClassificationContext",
    "ClassificationInput",
    "DecisionCandidate",
    "BoundConfirmation",
    "rendered_summary_hash",
    "RENDERER_ID",
    "RENDERER_VERSION",
    "render_summary",
    "prepare_summary",
    "Phase4PropertyReport",
    "run_phase4_properties",
    "ConfirmationClassifier",
    "ReferenceConfirmationClassifier",
    "classify_safely",
    "classification_context",
    "classify_and_bind",
]
