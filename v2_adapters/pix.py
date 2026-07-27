"""Pix instruction adapter sourced only from authorized knowledge."""

from __future__ import annotations

from v2_adapters._payment_instruction_common import instruction_amount, percentages
from v2_contracts.payments import PaymentInstruction, PaymentMethod, PaymentObligation


class PixInstructionAdapter:
    def __init__(
        self,
        *,
        knowledge,
        receiver_profiles: tuple[str, ...] = (),
        payment_percentages: dict[str, int] | None = None,
    ) -> None:
        if not callable(getattr(knowledge, "pix_instruction", None)):
            raise TypeError("knowledge must implement pix_instruction")
        if type(receiver_profiles) is not tuple or any(
            type(profile) is not str or not profile for profile in receiver_profiles
        ):
            raise ValueError("receiver_profiles must contain exact non-empty text")
        if len(set(receiver_profiles)) != len(receiver_profiles):
            raise ValueError("receiver_profiles must be unique")
        self._knowledge = knowledge
        self._percentages = percentages(
            payment_percentages,
            set(receiver_profiles),
        ) if receiver_profiles else None

    def instruction(self, obligation: PaymentObligation) -> PaymentInstruction:
        if type(obligation) is not PaymentObligation:
            raise TypeError("obligation must be exact PaymentObligation")
        base_text = self._knowledge.pix_instruction(obligation.receiver_profile_id)
        if type(base_text) is not str or not base_text.strip():
            raise ValueError("knowledge returned an invalid Pix instruction")
        percentage = (
            100
            if self._percentages is None
            else self._percentages[obligation.receiver_profile_id]
        )
        public_text = (
            instruction_amount(
                obligation.amount_minor,
                obligation.currency,
                percentage,
            )
            + " "
            + base_text
        )
        lowered = public_text.casefold()
        if any(claim in lowered for claim in ("pagamento confirmado", "pix confirmado")):
            raise ValueError("Pix instruction contains an unverified settlement claim")
        return PaymentInstruction(
            payment_id=obligation.payment_id,
            reservation_anchor_id=obligation.reservation_anchor_id,
            method=PaymentMethod.PIX,
            receiver_profile_id=obligation.receiver_profile_id,
            economic_version=obligation.economic_version,
            public_text=public_text,
        )


__all__ = ["PixInstructionAdapter"]
