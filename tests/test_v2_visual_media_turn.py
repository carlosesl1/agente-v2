import asyncio
import base64
from dataclasses import replace
import json
from types import SimpleNamespace

import httpx
import pytest
from tests.test_v2_visual_proof_journey import image, lab
from v2_adapters.proof_media import ProofMediaReader
from v2_adapters.hermes_model import HermesModelAdapter, _request_wire, _proposal
from v2_host.hermes_child import run_structured
from v2_contracts.model import ModelRequest, InvalidModelProposal


def response(proof=None, text="Resultado da conferência recebido."):
    return dict(
        schema="v2-model-proposal-v9",
        intent="inform",
        reply_chunks=[{"text": text, "expects_reply": False}],
        facts=[],
        read_requests=[],
        selected_choice_refs=[],
        selection_requested=False,
        pending_action_disposition=None,
        passengers=[],
        payment_proof=proof,
    )


def test_image_hash_is_bound_to_downloaded_bytes_and_native_child_pixels(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_PROFILE", "fixture")
    from tests.test_v2_turn_executor import EVENT

    attachment = image()
    raw = base64.b64decode(attachment.image_data_url.split(",", 1)[1])
    seen = []

    def get(req):
        seen.append(req)
        return httpx.Response(200, content=raw, headers={"content-type": "image/png"})

    reader = ProofMediaReader(
        allowed_hosts=("media.example.invalid",),
        archive=tmp_path,
        client=httpx.Client(transport=httpx.MockTransport(get)),
    )
    event = replace(
        EVENT,
        event_id=attachment.source_event_id,
        media_url="https://media.example.invalid/p.png",
        media_type="image/png",
    )
    received = reader.extract((event,))
    assert received == (attachment,)
    request = ModelRequest(
        "request:test",
        EVENT.lead_id,
        "event:batch",
        "Segue",
        "pt-BR",
        0,
        attachments=received,
    )
    wire = _request_wire(request, system_prompt="synthetic isolated system")
    captured = {}

    class Agent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run_conversation(self, prompt, **kwargs):
            captured["prompt"] = prompt
            return {"final_response": json.dumps(response())}

    result = asyncio.run(
        run_structured(
            (
                "hermes",
                "--profile",
                "fixture",
                "--model",
                "fixture",
                "--provider",
                "fixture",
                "--reasoning-effort",
                "high",
                "--contract",
                "maya-v9",
            ),
            wire,
            agent_factory=Agent,
            profile_resolver=lambda _: str(tmp_path),
            session_db_factory=lambda _: None,
        )
    )
    assert captured["enabled_toolsets"] == []
    assert captured["prompt"][-1] == {
        "type": "image_url",
        "image_url": {"url": attachment.image_data_url},
    }
    assert (
        captured["request_overrides"]["extra_body"]["text"]["format"]["schema"][
            "properties"
        ]["schema"]["const"]
        == "v2-model-proposal-v9"
    )
    assert result.startswith(b"PHASE8_RESULT\x00")
    assert len(seen) == 1 and seen[0].method == "GET"


@pytest.mark.parametrize(
    "case", ("unknown_host", "redirect", "pdf", "corrupt", "oversized")
)
def test_failed_media_is_unavailable_never_an_observation(tmp_path, case):
    from tests.test_v2_turn_executor import EVENT

    seen = []

    def get(req):
        seen.append(req)
        if case == "redirect":
            return httpx.Response(
                302, headers={"location": "https://other.invalid/p.png"}
            )
        return httpx.Response(
            200,
            content=b"z" * ((4 * 1024 * 1024 + 1) if case == "oversized" else 9),
            headers={
                "content-type": "application/pdf" if case == "pdf" else "image/png"
            },
        )

    reader = ProofMediaReader(
        allowed_hosts=("media.example.invalid",),
        archive=tmp_path,
        client=httpx.Client(transport=httpx.MockTransport(get)),
    )
    event = replace(
        EVENT,
        media_url="https://"
        + ("other.invalid" if case == "unknown_host" else "media.example.invalid")
        + "/p",
        media_type="image/png",
    )
    result = reader.extract((event,))[0]
    assert result.content_status.value == "unavailable"
    assert result.image_data_url is None
    assert len(seen) == (0 if case == "unknown_host" else 1)


@pytest.mark.parametrize(
    "change",
    (
        {"source_sha256": "a" * 64},
        {"source_event_id": "event:other"},
        {"status": "approved"},
        {"approved": True},
    ),
)
def test_model_cannot_mint_approval_or_rebind_image(tmp_path, change):
    f = lab(tmp_path)
    raw = response({**f.proof.to_dict(), **change})
    with pytest.raises(InvalidModelProposal):
        _proposal(json.dumps(raw).encode(), f.request)


def test_native_executor_discards_prevalidation_prose_and_publishes_tool_result_once(
    tmp_path,
):
    from tests.test_v2_turn_executor import (
        BATCH,
        EVENT,
        AUTHORITY,
        MappingAuthority,
        FixedClock,
        FakeProfile,
        _install_public_authority,
        _enabled_reducer,
        TRANSCRIPT_KEY,
    )
    from reservation_boundary.sqlite_store import SQLiteBoundaryStore
    from v2_application.turn_executor import V2TurnExecutor
    from v2_application.reads import V2ReadService
    from v2_application.private_customer_facts import SQLitePrivateCustomerFactStore

    f = lab(tmp_path)
    f.leads.lead_id_for_command = lambda _: BATCH.lead_id
    event = replace(
        EVENT,
        event_id=f.proof.source_event_id,
        media_url="https://media.example.invalid/p.png",
        media_type="image/png",
    )
    batch = replace(BATCH, events=(event,))
    boundary = SQLiteBoundaryStore.open_memory_v8()
    _install_public_authority(boundary)
    calls = []
    replies = [
        response(f.proof.to_dict(), "PREVALIDATION MUST NOT PUBLISH"),
        response(text="Comprovante conferido; a baixa foi encaminhada."),
    ]

    def run(command, **kwargs):
        calls.append(json.loads(kwargs["input"]))
        return SimpleNamespace(
            returncode=0,
            stdout=b"PHASE8_RESULT\x00" + json.dumps(replies.pop(0)).encode(),
            stderr=b"",
        )

    model = HermesModelAdapter(
        command=("synthetic", "--contract", "maya-v9"),
        system_prompt="synthetic",
        timeout=30,
        transcript_key=TRANSCRIPT_KEY,
        run=run,
    )
    media = SimpleNamespace(extract=lambda events: (f.attachment,))
    executor = V2TurnExecutor(
        store=boundary,
        model=model,
        reads=V2ReadService({}),
        profile=FakeProfile(boundary),
        private_customer_facts=SQLitePrivateCustomerFactStore.open_memory(),
        reducer=_enabled_reducer(),
        public_authority=MappingAuthority({batch.batch_id: AUTHORITY}),
        clock=FixedClock(),
        locale="pt-BR",
        turn_timeout=__import__("datetime").timedelta(seconds=30),
        max_commit_attempts=2,
        proof_media=media,
        visual_proofs=f.service,
    )
    result = executor.execute(batch)
    assert result.reply_chunks == ("Comprovante conferido; a baixa foi encaminhada.",)
    assert len(calls) == 2
    tool = json.loads(calls[1]["messages"][-1][1])["payment_proof_result"]
    assert tool["analysis"] == "accepted" and tool["settlement"] == "settlement_queued"
    assert "images" not in calls[1]
    assert executor.execute(batch).replayed
    assert len(calls) == 2
    assert f.followup._connection.execute(
        "select count(*) from payment_commands"
    ).fetchone() == (1,)
