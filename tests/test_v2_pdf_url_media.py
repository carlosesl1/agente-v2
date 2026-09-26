"""URL-message media transport, not text-based financial interpretation."""
from dataclasses import replace
import hashlib
import json

import httpx
import pytest

from deploy.canary_router import normalize_v2_payload
from tests.test_v2_pdf_proofs import pdf_bytes
from tests.test_v2_turn_executor import EVENT
from v2_adapters.proof_media import ProofMediaReader

HOST = "media.example.invalid"
URL = f"https://{HOST}/opaque-object?signature=A%2Fb%2BC&download=1"


def reader(tmp_path, *, raw=None, status=200, headers=None):
    calls = []
    content = pdf_bytes() if raw is None else raw

    def get(request):
        calls.append(request)
        return httpx.Response(status, content=content, headers=headers or {"content-type": "application/pdf"})

    return ProofMediaReader(allowed_hosts=(HOST,), archive=tmp_path,
                            client=httpx.Client(transport=httpx.MockTransport(get))), calls, content


@pytest.mark.parametrize("text", [URL, "Segue o documento: " + URL, ">>> Leia as duas páginas.\n" + URL])
def test_url_text_becomes_pdf_pixels_without_changing_event(tmp_path, text):
    media, calls, raw = reader(tmp_path)
    event = replace(EVENT, text=text, media_url=None, media_type=None)
    before = event
    result = media.extract((event,))
    assert len(result) == 2
    assert len(calls) == 1 and calls[0].method == "GET"
    assert str(calls[0].url) == URL
    assert event == before and event.text == text
    assert [a.page_number for a in result] == [1, 2]
    assert all(a.source_event_id == event.event_id and a.image_data_url for a in result)
    assert all(a.document_sha256 == hashlib.sha256(raw).hexdigest() for a in result)


def test_explicit_and_repeated_text_url_are_downloaded_once(tmp_path):
    media, calls, _ = reader(tmp_path)
    event = replace(EVENT, text=URL + "\n" + URL, media_url=URL, media_type="application/pdf")
    assert len(media.extract((event,))) == 2
    assert len(calls) == 1


@pytest.mark.parametrize("text", [
    "https://other.invalid/x.pdf", "https://media.example.invalid.evil.test/x.pdf",
    "http://media.example.invalid/x", "https://user:pw@media.example.invalid/x",
    "https://media.example.invalid:8443/x", "https://media.example.invalid/x#fragment",
    "https://media.example.invalid:bad/x", "https://[malformed/x",
    "Um PDF foi enviado, mas não há link.",
])
def test_non_media_or_disallowed_text_urls_do_not_fetch(tmp_path, text):
    media, calls, _ = reader(tmp_path)
    assert media.extract((replace(EVENT, text=text),)) == ()
    assert calls == []


@pytest.mark.parametrize("status,raw,headers", [
    (302, b"", {"location": "https://other.invalid/redirect"}),
    (403, b"expired", {"content-type": "text/plain"}),
    (200, b"broken", {"content-type": "application/pdf"}),
])
def test_url_download_failure_is_visible_as_unavailable(tmp_path, status, raw, headers):
    media, calls, _ = reader(tmp_path, raw=raw, status=status, headers=headers)
    result = media.extract((replace(EVENT, text=URL),))
    assert len(result) == 1 and result[0].content_status.value == "unavailable"
    assert result[0].image_data_url is None and len(calls) == 1


def test_multiple_text_documents_share_existing_page_budget(tmp_path):
    media, calls, _ = reader(tmp_path)
    text = "\n".join(f"https://{HOST}/{n}" for n in range(3))
    result = media.extract((replace(EVENT, text=text),))
    assert len(result) == 5
    assert sum(a.image_data_url is not None for a in result) == 4
    assert result[-1].content_status.value == "unavailable"
    assert len(calls) == 2


def test_manychat_last_input_text_normalization_reaches_same_reader(tmp_path):
    payload = normalize_v2_payload({"id": "123456789", "last_input_text": URL,
                                    "time-message": "2026-09-26T00:00:00+00:00"})
    media, calls, _ = reader(tmp_path)
    result = media.extract((replace(EVENT, event_id=payload["event_id"], text=payload["text"]),))
    assert len(result) == 2 and len(calls) == 1


def test_productive_media_reader_is_independent_of_financial_gates(tmp_path):
    from tests.test_v2_production_composition import _settings
    from v2_host.composition import V2Container, V2Role
    from v2_host.production import build_worker_set, ClosedCapabilityWorker
    from v2_host.settings import RuntimeMode
    from v2_host.worker_main import WorkerQueue

    knowledge = tmp_path / "knowledge.yaml"
    knowledge.write_text("entries:\n  - id: faq\n    topic: geral\n    question: Oi?\n    answer: Olá.\n")
    settings = _settings(tmp_path, runtime_mode=RuntimeMode.GENERAL_AVAILABILITY,
        hermes_model="openai-codex/gpt-5.6-terra", candidate_git_sha="a" * 40,
        candidate_image_digest="sha256:" + "b" * 64, manychat_api_key="synthetic",
        hermes_command=("python", "-m", "v2_host.hermes_child", "hermes"),
        hermes_system_prompt="Synthetic system", hermes_transcript_key=b"t" * 32,
        public_authority_hmac_key=b"a" * 32, knowledge_base_path=knowledge,
        proof_media_hosts=(HOST,))
    container = V2Container.open(settings=settings, role=V2Role.WORKER)
    try:
        workers = build_worker_set(container=container, settings=settings)
        executor = workers[WorkerQueue.INBOX]._executor
        assert isinstance(executor._proof_media, ProofMediaReader)
        assert executor._visual_proofs is None
        assert settings.global_kill_switch_engaged
        for queue in (WorkerQueue.SETTLEMENT, WorkerQueue.RESERVATION,
                      WorkerQueue.PAYMENT_INITIATION, WorkerQueue.PUBLIC_DELIVERY):
            assert isinstance(workers[queue], ClosedCapabilityWorker)
        assert container.readiness().capabilities["media_reading"] == "ready"
    finally:
        container.close()

def test_url_executor_commits_maya_pixels_reply_once_without_payment_service(tmp_path):
    from datetime import timedelta
    from types import SimpleNamespace
    from tests.test_v2_turn_executor import (BATCH, AUTHORITY, MappingAuthority,
        FixedClock, FakeProfile, _install_public_authority, TRANSCRIPT_KEY)
    from tests.test_v2_visual_media_turn import response
    from reservation_boundary.sqlite_store import SQLiteBoundaryStore
    from v2_application.turn_executor import V2TurnExecutor
    from v2_application.conversation import V2ConversationReducer
    from v2_application.reads import V2ReadService
    from v2_application.private_customer_facts import SQLitePrivateCustomerFactStore
    from v2_adapters.hermes_model import HermesModelAdapter

    media, downloads, _ = reader(tmp_path)
    boundary = SQLiteBoundaryStore.open_memory_v8()
    _install_public_authority(boundary)
    calls = []
    def run(command, **kwargs):
        wire = json.loads(kwargs["input"])
        calls.append(wire)
        assert len(wire["images"]) == 2
        payload = json.loads(wire["messages"][-1][1])
        assert payload["message"] == URL
        assert [a["page_number"] for a in payload["attachments"]] == [1, 2]
        assert "payment_candidates" not in payload
        reply = response(text="Li as duas páginas do documento técnico.")
        del reply["schema"], reply["payment_proof"]
        return SimpleNamespace(returncode=0, stdout=b"PHASE8_RESULT\x00" + json.dumps(reply).encode(), stderr=b"")
    executor = V2TurnExecutor(store=boundary,
        model=HermesModelAdapter(command=("synthetic", "--contract", "maya-v8"),
            system_prompt="Synthetic", timeout=30, transcript_key=TRANSCRIPT_KEY, run=run),
        reads=V2ReadService({}), profile=FakeProfile(boundary),
        private_customer_facts=SQLitePrivateCustomerFactStore.open_memory(),
        reducer=V2ConversationReducer(), public_authority=MappingAuthority({BATCH.batch_id: AUTHORITY}),
        clock=FixedClock(), locale="pt-BR", turn_timeout=timedelta(seconds=30),
        max_commit_attempts=2, proof_media=media)
    batch = replace(BATCH, events=(replace(EVENT, text=URL),), combined_text=URL)
    result = executor.execute(batch)
    assert result.reply_chunks == ("Li as duas páginas do documento técnico.",)
    assert len(downloads) == len(calls) == 1
    assert executor.execute(batch).replayed
    assert len(downloads) == len(calls) == 1

