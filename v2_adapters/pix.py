"""Pix instruction adapter sourced only from authorized knowledge."""

from __future__ import annotations

from v2_contracts.payments import PaymentInstruction, PaymentMethod, PaymentObligation


class PixInstructionAdapter:
    def __init__(self, *, knowledge) -> None:
        if not callable(getattr(knowledge, "pix_instruction", None)):
            raise TypeError("knowledge must implement pix_instruction")
        self._knowledge = knowledge

    def instruction(self, obligation: PaymentObligation) -> PaymentInstruction:
        if type(obligation) is not PaymentObligation:
            raise TypeError("obligation must be exact PaymentObligation")
        public_text = self._knowledge.pix_instruction(obligation.receiver_profile_id)
        if type(public_text) is not str or not public_text.strip():
            raise ValueError("knowledge returned an invalid Pix instruction")
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
