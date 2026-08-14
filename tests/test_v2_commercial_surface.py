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
    assert "`reply_chunks` contém uma ou duas mensagens" in prompt
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


def test_live_prompt_preserves_whatsapp_identity_installment_and_channel_rules() -> None:
    prompt = (ROOT / "config/v2_luna_system_prompt.txt").read_text()

    assert "assistente de IA da Chapada Backpackers" in prompt
    assert "adiantamento" in prompt
    assert "parcelar presencialmente" in prompt
    assert "agência ou no hostel" in prompt
    assert "não realiza parcelamento" in prompt
    assert "conversa natural de WhatsApp" in prompt
    assert "Nunca use e-mail como canal de continuidade" in prompt
    assert "Todas as interações com o lead acontecem somente pelo WhatsApp" in prompt


def test_model_prompt_uses_existing_knowledge_then_activity_for_group_recommendations() -> None:
    from v2_adapters.hermes_model import _FORMED_GROUPS_SYSTEM_SUFFIX

    prompt = _FORMED_GROUPS_SYSTEM_SUFFIX

    assert "existing knowledge read" in prompt
    assert "formed-groups:YYYY-MM-DD:YYYY-MM-DD" in prompt
    assert "formed_groups" in prompt
    assert "suitability first" in prompt
    assert "fresh activity read" in prompt


def test_hermes_adapter_parses_closed_knowledge_request_from_live_contract() -> None:
    response = json.dumps(
        {
            "intent": "inform",
            "reply_chunks": [
                {"text": "Vou confirmar essa informação.", "expects_reply": False}
            ],
            "facts": [],
            "read_requests": [
                {
                    "kind": "knowledge",
                    "query": "Qual é o horário de check-in?",
                }
            ],
            "selected_choice_refs": [],
            "selection_requested": False,
            "pending_action_disposition": None,
            "passengers": [],
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


def test_versioned_cerebro_answers_shared_dorm_and_private_room_questions() -> None:
    adapter = KnowledgeReadAdapter(
        transport=FileKnowledgeTransport((ROOT / "config/cerebro_faq.yaml").resolve()),
        clock=FixedClock(),
        ttl=timedelta(minutes=5),
    )

    shared = adapter.read(
        ReadRequest(
            request_id="read:knowledge:shared-dorm",
            kind=ReadKind.KNOWLEDGE,
            query="Nunca fiquei em hostel. Como funciona esse negócio de quarto compartilhado?",
            locale="pt-BR",
        )
    )
    private = adapter.read(
        ReadRequest(
            request_id="read:knowledge:private-room",
            kind=ReadKind.KNOWLEDGE,
            query="Quero um quarto só para o casal. Vocês têm quarto privativo?",
            locale="pt-BR",
        )
    )
    late_arrival = adapter.read(
        ReadRequest(
            request_id="read:knowledge:late-arrival",
            kind=ReadKind.KNOWLEDGE,
            query="Meu ônibus chega de madrugada. Como funciona o check-in fora do horário?",
            locale="pt-BR",
        )
    )

    assert shared.public_payload["sources"][0] == "hostel_quarto_compartilhado"
    assert "4, 6 ou 8 camas" in shared.public_payload["answer"]
    assert "armários individuais" in shared.public_payload["answer"]
    assert private.public_payload["sources"][0] == "hostel_quarto_privativo"
    assert "banheiro privativo" in private.public_payload["answer"]
    assert "não garante silêncio" in private.public_payload["answer"]
    assert late_arrival.public_payload["sources"][0] == "hostel_chegada_fora_recepcao"
    assert "portaria funciona 24 horas" in late_arrival.public_payload["answer"]
    assert "recepção é de 7h a 22h" in late_arrival.public_payload["answer"]
