"""Host-layer ManyChat handoff delivery composition."""

from __future__ import annotations

import hashlib

from reservation_followup.handoff import HandoffEffectJob, HandoffEffectKind
from reservation_followup.types import HandoffReceipt
from reservation_followup.workers import HandoffDeliveryNotCalled, HandoffDeliveryUnknown
from v2_adapters.manychat import ManyChatTransportNotCalled, ManyChatTransportResponse


class ManyChatHandoffDeliveryAdapter:
    """Apply the handoff tag; ManyChat owns the automatic-reply pause."""

    delivery_id = "delivery:manychat-handoff-v2"
    delivery_version = 2

    def __init__(
        self,
        *,
        transport: object,
        tag_id: int,
        clock: object,
        subscriber_id: str = "",
        lead_resolver: object | None = None,
    ) -> None:
        if not callable(getattr(transport, "add_tag", None)):
            raise TypeError("transport must expose add_tag")
        if type(subscriber_id) is not str or (
            subscriber_id and not subscriber_id.isdecimal()
        ):
            raise ValueError("subscriber_id must be empty or exact decimal text")
        if lead_resolver is not None and not callable(
            getattr(lead_resolver, "subscriber_id_for_handoff", None)
        ):
            raise TypeError("lead_resolver must resolve handoff owners")
        if bool(subscriber_id) == (lead_resolver is not None):
            raise ValueError("handoff delivery requires one fixed or durable lead source")
        if type(tag_id) is not int or tag_id < 1:
            raise ValueError("tag_id must be a positive exact integer")
        if not callable(getattr(clock, "now", None)):
            raise TypeError("clock must expose now")
        self._transport = transport
        self._subscriber_id = subscriber_id
        self._lead_resolver = lead_resolver
        self._tag_id = tag_id
        self._clock = clock

    def deliver(self, message: HandoffEffectJob) -> HandoffReceipt:
        if type(message) is not HandoffEffectJob:
            raise TypeError("message must be exact HandoffEffectJob")
        if message.kind is not HandoffEffectKind.CUSTOMER_ACKNOWLEDGEMENT:
            raise RuntimeError("ManyChat handoff adapter forbids internal e-mail")
        try:
            subscriber_id = (
                self._lead_resolver.subscriber_id_for_handoff(message.handoff_id)
                if self._lead_resolver is not None
                else self._subscriber_id
            )
        except Exception as exc:
            raise HandoffDeliveryUnknown(
                "ManyChat handoff lacks one durable subscriber owner"
            ) from exc
        try:
            tag = self._transport.add_tag(
                subscriber_id=subscriber_id,
                tag_id=self._tag_id,
                idempotency_key=message.effect_id + ":tag",
            )
            if type(tag) is not ManyChatTransportResponse:
                raise RuntimeError("ManyChat tag receipt is invalid")
        except ManyChatTransportNotCalled as exc:
            raise HandoffDeliveryNotCalled("ManyChat handoff tag was not called") from exc
        except HandoffDeliveryUnknown:
            raise
        except Exception as exc:
            raise HandoffDeliveryUnknown(
                "ManyChat handoff effect outcome is unknown"
            ) from exc
        digest = hashlib.sha256(
            b"v2-manychat-handoff-tag-acceptance-v2\0"
            + (tag.provider_request_id or "").encode("utf-8")
            + b"\0"
            + tag.dispatch_correlation_id.encode("utf-8")
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
