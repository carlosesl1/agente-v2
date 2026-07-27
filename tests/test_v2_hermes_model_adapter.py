from __future__ import annotations

import json
import unicodedata

from v2_adapters.hermes_model import _proposal


def test_model_public_reply_chunks_are_nfkc_normalized_before_boundary_validation() -> None:
    raw_text = "Opc\u0327a\u0303o econo\u0302mica disponi\u0301vel."
    assert raw_text != unicodedata.normalize("NFKC", raw_text)
    payload = {
        "schema": "v2-model-proposal-v2",
        "source_event_id": "batch:nfkc-model-reply-001",
        "intent": "inform",
        "reply_chunks": [f"  {raw_text}  "],
        "facts": [],
        "read_requests": [],
        "effect_proposals": [],
        "target_offer_id": None,
        "target_offer_ids": [],
        "confirmed_summary_version": None,
    }

    proposal = _proposal(
        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        "batch:nfkc-model-reply-001",
    )

    assert proposal.reply_chunks == (
        unicodedata.normalize("NFKC", raw_text),
    )
    assert proposal.reply_chunks[0] == unicodedata.normalize(
        "NFKC", proposal.reply_chunks[0]
    )
