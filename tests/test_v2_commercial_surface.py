from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace

from v2_adapters.hermes_model import HermesModelAdapter
from v2_adapters.knowledge import KnowledgeReadAdapter
from v2_adapters.provider_http import FileKnowledgeTransport
from v2_contracts.model import ModelRequest
from v2_contracts.providers import ReadKind, ReadRequest

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


class FixedClock:
    def now(self) -> datetime:
        return NOW


def test_commercial_catalog_and_private_bokun_map_are_closed_and_consistent() -> None:
    catalog = json.loads(
        (ROOT / "config/v2_public_commercial_catalog.json").read_text()
    )
    private_map = json.loads((ROOT / "config/v2_bokun_product_map.json").read_text())
    prompt = (ROOT / "config/v2_luna_system_prompt.txt").read_text()

    assert catalog["schema"] == "v2-public-commercial-catalog-v1"
    canonical_ids = [item["canonical_id"] for item in catalog["products"]]
    assert len(canonical_ids) == 16
    assert len(set(canonical_ids)) == len(canonical_ids)
    assert set(private_map) == set(canonical_ids)
    assert all(value.isdecimal() for value in private_map.values())
    assert all(canonical_id in prompt for canonical_id in canonical_ids)
    assert all(provider_id not in prompt for provider_id in private_map.values())
    assert "Mixila 1D e Mixila 2D são produtos diferentes" in prompt
    assert "Só sai às quartas-feiras" in prompt


def test_live_prompt_requires_reply_and_exposes_all_safe_read_contracts() -> None:
    prompt = (ROOT / "config/v2_luna_system_prompt.txt").read_text()

    assert len(prompt.encode()) < 65_536
    assert "uma ou duas strings não vazias" in prompt
    assert '"kind":"knowledge"' in prompt
    assert '"kind":"lodging"' in prompt
    assert '"kind":"room_description"' in prompt
    assert '"kind":"activity"' in prompt
    assert '"kind":"activity_description"' in prompt
    assert "Saudação, descoberta, FAQ, recomendação, descrição, preço e disponibilidade não exigem perfil completo" in prompt
    assert "Nunca transforme Pix em Stripe" in prompt
    assert "inclua sempre o product_id canônico em facts" in prompt
    assert "REGRA OBRIGATÓRIA EM QUALQUER IDIOMA" in prompt
    assert "uma lodging e uma activity" in prompt
    assert "rosileidebastos557@gmail.com" in prompt
    assert "75999979532" in prompt


def test_hermes_adapter_parses_closed_knowledge_request_from_live_contract() -> None:
    response = json.dumps(
        {
            "schema": "v2-model-proposal-v2",
            "source_event_id": "event:knowledge-contract",
            "intent": "inform",
            "reply_chunks": ["Vou confirmar essa informação."],
            "facts": [],
            "read_requests": [
                {
                    "request_id": "event:knowledge-contract:read:knowledge",
                    "kind": "knowledge",
                    "query": "Qual é o horário de check-in?",
                    "locale": "pt-BR",
                }
            ],
            "effect_proposals": [],
            "target_offer_id": None,
            "target_offer_ids": [],
            "confirmed_summary_version": None,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()

    def run(*args, **kwargs):
        del args, kwargs
        return SimpleNamespace(
            returncode=0,
            stdout=b"PHASE8_RESULT\x00" + response,
            stderr=b"",
        )

    adapter = HermesModelAdapter(
        command=("hermes-child",),
        system_prompt=(ROOT / "config/v2_luna_system_prompt.txt").read_text(),
        timeout=30,
        transcript_key=b"commercial-contract-transcript-key",
        run=run,
        environ={"PATH": "/usr/bin"},
    )
    proposal = adapter.complete(
        ModelRequest(
            request_id="request:knowledge-contract",
            lead_id="manychat:knowledge-contract",
            source_event_id="event:knowledge-contract",
            message="Qual é o horário de check-in?",
            locale="pt-BR",
            state_version=0,
        )
    )

    assert len(proposal.read_requests) == 1
    assert proposal.read_requests[0].kind is ReadKind.KNOWLEDGE
    assert proposal.read_requests[0].query == "Qual é o horário de check-in?"


def test_versioned_cerebro_answers_through_productive_knowledge_adapter() -> None:
    request = ReadRequest(
        request_id="read:knowledge:checkin",
        kind=ReadKind.KNOWLEDGE,
        query="Qual é o horário de check-in e check-out?",
        locale="pt-BR",
    )
    adapter = KnowledgeReadAdapter(
        transport=FileKnowledgeTransport((ROOT / "config/cerebro_faq.yaml").resolve()),
        clock=FixedClock(),
        ttl=timedelta(minutes=5),
    )

    observation = adapter.read(request)

    assert observation.provider == "cerebro"
    assert observation.observed_at == NOW
    assert observation.public_payload["sources"]
    assert "check-in" in observation.public_payload["answer"].lower()
