"""Causal reply admission and completion/channel tests; all effects simulated."""

import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from reservation_boundary.conversation import PublicReplyChunk
from reservation_boundary.reads import validate_public_text
from tests.test_v2_hermes_model_adapter import _v8_payload, _v8_request
from tests.test_v2_outcome_projector import NOW
from tests.test_v2_payment_button_completion import buttons, drain, prepare, reopen
from tests.test_v2_payment_button_completion import lab as fixture_lab
from tests.test_v2_turn_executor import TRANSCRIPT_KEY
from tests.v2_completion_helpers import authored_chunks
from v2_adapters.hermes_model import HermesModelAdapter
from v2_contracts.model import InvalidModelProposal

PROMPT = Path("config/v2_terra_system_prompt.txt").read_text()


@pytest.fixture
def lab(tmp_path):
    yield from fixture_lab.__wrapped__(tmp_path)


def adapter(texts, *, transcript_key=TRANSCRIPT_KEY):
    calls = []
    replies = iter(texts)

    def run(command, **kwargs):
        calls.append(json.loads(kwargs["input"]))
        raw = json.dumps(
            _v8_payload(reply_chunks=[{"text": next(replies), "expects_reply": False}]),
            ensure_ascii=False,
        ).encode()
        return SimpleNamespace(
            returncode=0, stdout=b"PHASE8_RESULT\x00" + raw, stderr=b""
        )

    model = HermesModelAdapter(
        command=("synthetic-maya",),
        system_prompt=PROMPT,
        timeout=10,
        transcript_key=transcript_key,
        run=run,
        environ={},
    )
    return model, calls


@pytest.mark.parametrize(
    "text",
    [
        "Sua hospedagem no Quarto nº 2 está confirmada. O pagamento ainda está pendente.",
        "Opc\u0327a\u0303o confirmada. Os boto\u0303es de pagamento serão enviados separadamente.",
        "Reserva confirmada. Você pode pagar pelos botões. 🏞️",
    ],
)
def test_unicode_reply_reaches_commit_and_channel_byte_exact(lab, text):
    model, calls = adapter([text])
    lab.projector.executor._model = model
    prepare(lab)
    before = lab.execution.list_outcome_projection_inputs()
    lab.projector.run_once(now=NOW + timedelta(seconds=5))
    drain(lab)
    assert [c.text for c in authored_chunks(lab.projector)] == [text]
    assert [
        b["field_value"] for p, b, _ in lab.http if p.endswith("setCustomField")
    ] == [text]
    assert len(buttons(lab)) == 2
    assert len(calls) == 1
    sent = list(lab.http)
    reopen(lab)
    lab.projector.run_once(now=NOW + timedelta(days=1))
    drain(lab)
    assert lab.http == sent
    assert lab.execution.list_outcome_projection_inputs() == before
    assert len(lab.stripe.calls) == 2


def test_provider_read_normalization_is_not_relaxed():
    with pytest.raises(ValueError, match="NFKC"):
        validate_public_text("Quarto nº 2", limit=256)


@pytest.mark.parametrize(
    "text",
    [
        "<script>x</script>",
        "https://example.invalid",
        "ｈｔｔｐｓ：／／example.invalid",
        "Olá\x00",
        "x" * 4097,
    ],
)
def test_public_reply_rejects_active_or_oversized_content(text):
    with pytest.raises(ValueError):
        PublicReplyChunk("turn:contract", 0, text, "a" * 64)


def test_payment_button_context_reaches_actual_child_without_url(lab):
    model, calls = adapter(
        ["Os pagamentos seguem em botões separados, sem confirmação de liquidação."]
    )
    lab.projector.executor._model = model
    prepare(lab)
    lab.projector.run_once(now=NOW + timedelta(seconds=5))
    payload = json.loads(calls[0]["messages"][-1][1])
    payments = [
        p for c in payload["execution_components"] for p in c["payment"]["initiations"]
    ]
    assert len(payments) == 2
    for payment in payments:
        assert payment.get("delivery") == "manychat_button"
        assert "public_url" not in payment
        assert payment["amount_minor"] > 0
        assert payment["status"] == "completed"
    assert "manychat_button" in calls[0]["system_prompt"]
    assert "Não copie URLs" in calls[0]["system_prompt"]
    drain(lab)
    assert {
        b["fields"][0]["field_value"]
        for p, b, _ in lab.http
        if p.endswith("setCustomFields")
    } == {o.public_url for o in lab.payments.completed_offers()}


def test_url_reply_uses_existing_bounded_protocol_repair_before_commit(lab):
    text = "Os pagamentos serão enviados em botões separados. Ainda não constam como pagos."
    model, calls = adapter(["Pague em https://buy.stripe.com/test_fixture", text])
    lab.projector.executor._model = model
    prepare(lab)
    lab.projector.run_once(now=NOW + timedelta(seconds=5))
    drain(lab)
    assert len(calls) == 2
    assert [c.text for c in authored_chunks(lab.projector)] == [text]
    assert len(buttons(lab)) == 2
    assert len(lab.stripe.calls) == 2


def test_invalid_text_repair_is_bounded_and_never_returns_rewritten_prose():
    model, calls = adapter(["https://example.invalid"] * 2)
    with pytest.raises(InvalidModelProposal, match="bounded attempts"):
        model.complete_audited(_v8_request())
    assert len(calls) == 2


@pytest.fixture
def recovery_runtime(tmp_path, monkeypatch):
    from tests.test_v2_blocking_error_handoff import runtime

    yield from runtime.__wrapped__(tmp_path, monkeypatch)


def test_repeated_invalid_reply_reaches_durable_tag_handoff(recovery_runtime):
    from tests.test_v2_blocking_error_handoff import handoff_count
    from tests.test_v2_inbox_reliability import NOW as CLOCK
    from tests.test_v2_inbox_reliability import event
    from tests.test_v2_turn_executor import FakeProfile
    from v2_host.worker_main import WorkerQueue

    open_runtime, seen = recovery_runtime
    container, workers = open_runtime()
    model, calls = adapter(["https://example.invalid"] * 6, transcript_key=b"t" * 32)
    workers[WorkerQueue.INBOX]._executor._model = model
    workers[WorkerQueue.INBOX]._executor._profile = FakeProfile(container.boundary)
    container.inbox.accept(event("invalid-reply", lead="10001"))
    for attempt in range(3):
        with pytest.raises(InvalidModelProposal):
            workers[WorkerQueue.INBOX].run_once(
                now=CLOCK + timedelta(seconds=6 * attempt)
            )
    assert len(calls) == 6
    assert container.boundary._connection.execute(
        "SELECT count(*) FROM boundary_commands"
    ).fetchone() == (0,)
    container.close()
    container, workers = open_runtime()
    workers[WorkerQueue.RECONCILIATION].run_once(now=CLOCK + timedelta(seconds=20))
    assert handoff_count(container) == 1
    workers[WorkerQueue.HANDOFF].run_once(now=CLOCK + timedelta(seconds=21))
    workers[WorkerQueue.RECONCILIATION].run_once(now=CLOCK + timedelta(seconds=31))
    workers[WorkerQueue.HANDOFF].run_once(now=CLOCK + timedelta(seconds=32))
    assert seen == [
        ("/fb/subscriber/addTag", {"subscriber_id": "10001", "tag_id": 301})
    ]
