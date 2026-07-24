"""Host-layer ManyChat handoff delivery composition."""

from __future__ import annotations

import hashlib

from reservation_followup.handoff import HandoffEffectJob, HandoffEffectKind
from reservation_followup.types import HandoffReceipt
from reservation_followup.workers import HandoffDeliveryUnknown
from v2_adapters.manychat import ManyChatTransportNotCalled, ManyChatTransportResponse


class ManyChatHandoffDeliveryAdapter:
    """Apply one handoff tag and flow with terminal partial-unknown handling."""

    delivery_id = "delivery:manychat-handoff-v2"
    delivery_version = 1

    def __init__(
        self,
        *,
        transport: object,
        subscriber_id: str,
        tag_id: int,
        flow_ns: str,
        clock: object,
    ) -> None:
        for method in ("add_tag", "trigger_flow"):
            if not callable(getattr(transport, method, None)):
                raise TypeError(f"transport must expose {method}")
        if type(subscriber_id) is not str or not subscriber_id.isdecimal():
            raise ValueError("subscriber_id must be exact decimal text")
        if type(tag_id) is not int or tag_id < 1:
            raise ValueError("tag_id must be a positive exact integer")
        if type(flow_ns) is not str or not flow_ns or "\x00" in flow_ns:
            raise ValueError("flow_ns must be non-empty NUL-free text")
        if not callable(getattr(clock, "now", None)):
            raise TypeError("clock must expose now")
        self._transport = transport
        self._subscriber_id = subscriber_id
        self._tag_id = tag_id
        self._flow_ns = flow_ns
        self._clock = clock

    def deliver(self, message: HandoffEffectJob) -> HandoffReceipt:
        if type(message) is not HandoffEffectJob:
            raise TypeError("message must be exact HandoffEffectJob")
        if message.kind is not HandoffEffectKind.CUSTOMER_ACKNOWLEDGEMENT:
            raise RuntimeError("ManyChat handoff adapter forbids internal e-mail")
        tagged = False
        try:
            tag = self._transport.add_tag(
                subscriber_id=self._subscriber_id,
                tag_id=self._tag_id,
                idempotency_key=message.effect_id + ":tag",
            )
            if type(tag) is not ManyChatTransportResponse:
                raise RuntimeError("ManyChat tag receipt is invalid")
            tagged = True
            flow = self._transport.trigger_flow(
                subscriber_id=self._subscriber_id,
                flow_ns=self._flow_ns,
                idempotency_key=message.effect_id + ":flow",
            )
            if type(flow) is not ManyChatTransportResponse:
                raise RuntimeError("ManyChat handoff flow receipt is invalid")
        except ManyChatTransportNotCalled as exc:
            if not tagged:
                raise RuntimeError("ManyChat handoff tag was not called") from exc
            raise HandoffDeliveryUnknown(
                "ManyChat handoff flow is unknown after tag mutation"
            ) from exc
        except HandoffDeliveryUnknown:
            raise
        except Exception as exc:
            raise HandoffDeliveryUnknown(
                "ManyChat handoff effect outcome is unknown"
            ) from exc
        digest = hashlib.sha256(
            b"v2-manychat-handoff-receipt-v1\0"
            + tag.provider_message_id.encode("utf-8")
            + b"\0"
            + flow.provider_message_id.encode("utf-8")
        ).hexdigest()
        return HandoffReceipt.for_message(
            message,
            receipt_id=f"receipt:manychat-handoff:{digest[:32]}",
            delivery_reference=f"manychat-handoff:{digest[:32]}",
            delivery_id=self.delivery_id,
            delivery_version=self.delivery_version,
            delivered_at=self._clock.now(),
        )


__all__ = ["ManyChatHandoffDeliveryAdapter"]
