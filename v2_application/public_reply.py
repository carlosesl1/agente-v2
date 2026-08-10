from __future__ import annotations

from v2_contracts.model import ModelProposal
from v2_contracts.providers import ReadObservation


def apply_positive_grounding(
    proposal: ModelProposal,
    observations: tuple[ReadObservation, ...],
    *,
    locale: str,
    force: bool,
) -> ModelProposal:
    """Validate grounding inputs without rewriting model-owned public text."""

    if (
        type(proposal) is not ModelProposal
        or type(observations) is not tuple
        or any(type(item) is not ReadObservation for item in observations)
        or type(locale) is not str
        or type(force) is not bool
    ):
        raise TypeError("positive grounding requires exact contracts")
    return proposal
