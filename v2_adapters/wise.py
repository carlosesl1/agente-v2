"""Wise instruction adapter; initiation never claims a verified credit."""

from __future__ import annotations

from v2_adapters._payment_instruction_common import instruction_amount, percentages
from v2_contracts.payments import PaymentInstruction, PaymentMethod, PaymentObligation


_PROHIBITED_CLAIMS = ("confirmado", "confirmada", "pago", "paga", "paid", "settled")


class WiseInstructionAdapter:
    def __init__(
        self,
        *,
        instructions: dict[str, str],
        payment_percentages: dict[str, int] | None = None,
    ) -> None:
        if type(instructions) is not dict or not instructions:
            raise ValueError("instructions must be a non-empty exact dict")
        if any(
            type(profile) is not str
            or not profile
            or type(text) is not str
            or not text.strip()
            for profile, text in instructions.items()
        ):
            raise ValueError("Wise instructions must bind exact profiles to text")
        self._instructions = dict(instructions)
        self._percentages = percentages(
            payment_percentages,
            set(self._instructions),
        )

    def instruction(self, obligation: PaymentObligation) -> PaymentInstruction:
        if type(obligation) is not PaymentObligation:
            raise TypeError("obligation must be exact PaymentObligation")
        try:
            base_text = self._instructions[obligation.receiver_profile_id]
        except KeyError as exc:
            raise ValueError("Wise receiver profile is not configured") from exc
        public_text = (
            instruction_amount(
                obligation.amount_minor,
                obligation.currency,
                self._percentages[obligation.receiver_profile_id],
            )
            + " "
            + base_text
        )
        lowered = public_text.casefold()
        if any(claim in lowered for claim in _PROHIBITED_CLAIMS):
            raise ValueError("Wise instruction contains an unverified settlement claim")
        return PaymentInstruction(
            payment_id=obligation.payment_id,
            reservation_anchor_id=obligation.reservation_anchor_id,
            method=PaymentMethod.WISE,
            receiver_profile_id=obligation.receiver_profile_id,
            economic_version=obligation.economic_version,
            public_text=public_text,
        )


__all__ = ["WiseInstructionAdapter"]
