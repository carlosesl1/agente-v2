"""Actual PDF bytes/rendering; controlled HTTP and private financial owners only."""

from dataclasses import replace
import base64
import hashlib
from io import BytesIO
import json

import httpx
import pytest
from PIL import Image, ImageDraw
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

from tests.test_v2_turn_executor import EVENT
from tests.test_v2_visual_proof_journey import lab, image
from tests.test_v2_visual_media_turn import response
from v2_adapters.proof_media import ProofMediaReader
from v2_adapters.hermes_model import _request_wire, _proposal
from v2_contracts.model import AttachmentContentStatus as Status, InvalidModelProposal
from v2_host.hermes_child import _closed_request


@pytest.fixture(autouse=True)
def owner_dir(tmp_path):
    (tmp_path / "owner").mkdir()


def pdf_bytes(pages=2, scanned=False, password=None, size=(595, 842)):
    out = BytesIO()
    if scanned:
        images = []
        for i in range(pages):
            im = Image.new("RGB", (600, 800), "white")
            ImageDraw.Draw(im).text(
                (20, 20), f"SYNTHETIC RECEIPT PAGE {i + 1}", fill="black", font_size=24
            )
            images.append(im)
        images[0].save(out, "PDF", save_all=True, append_images=images[1:])
    else:
        doc = canvas.Canvas(out, pagesize=size)
        for i in range(pages):
            doc.drawString(30, size[1] - 50, f"SYNTHETIC RECEIPT PAGE {i + 1}")
            doc.showPage()
        doc.save()
    raw = out.getvalue()
    if password:
        writer = PdfWriter(clone_from=BytesIO(raw))
        writer.encrypt(password)
        out = BytesIO()
        writer.write(out)
        raw = out.getvalue()
    return raw


def extract(tmp_path, raw, events=None, mime="application/pdf"):
    seen = []

    def fetch(request):
        seen.append(request)
        return httpx.Response(200, content=raw, headers={"content-type": mime})

    reader = ProofMediaReader(
        allowed_hosts=("media.example.invalid",),
        archive=tmp_path / "images",
        client=httpx.Client(transport=httpx.MockTransport(fetch)),
    )
    event = replace(
        EVENT,
        event_id="event:receipt",
        media_type="application/pdf",
        media_url="https://media.example.invalid/receipt.pdf",
    )
    received = reader.extract(events or (event,))
    assert seen and all(r.method == "GET" for r in seen)
    return received


@pytest.mark.parametrize("scanned", [False, True])
def test_pdf_all_pages_are_pixels_bound_to_original_and_native_wire(tmp_path, scanned):
    raw = pdf_bytes(scanned=scanned)
    assert len(PdfReader(BytesIO(raw)).pages) == 2
    if scanned:
        assert not PdfReader(BytesIO(raw)).pages[0].extract_text().strip()
    attachments = extract(tmp_path, raw)
    assert len(attachments) == 2
    original_hash = hashlib.sha256(raw).hexdigest()
    assert (tmp_path / "images" / original_hash).read_bytes() == raw
    for number, a in enumerate(attachments, 1):
        assert a.content_status is Status.IMAGE_READY
        assert a.document_sha256 == original_hash
        assert (a.page_number, a.page_count) == (number, 2)
        assert a.media_type == "application/pdf"
        pixels = base64.b64decode(a.image_data_url.split(",", 1)[1])
        assert hashlib.sha256(pixels).hexdigest() == a.source_sha256
        assert Image.open(BytesIO(pixels)).format == "PNG"
        assert (tmp_path / "images" / a.source_sha256).read_bytes() == pixels
    f = lab(tmp_path / "owner")
    request = replace(
        f.request, attachments=attachments, message="Legenda original preservada"
    )
    wire = _closed_request(_request_wire(request, system_prompt="offline test"))
    assert wire["images"] == [a.image_data_url for a in attachments]
    assert "Legenda original preservada" in wire["messages"][-1][1]
    assert original_hash in wire["messages"][-1][1]


@pytest.mark.parametrize(
    "raw",
    [
        b"%PDF-1.7\ncorrupt",
        b"not a pdf",
        pdf_bytes(5),
        pdf_bytes(password="secret"),
        pdf_bytes(size=(9000, 9000)),
        b"%PDF-" + b"x" * (4 * 1024 * 1024),
    ],
)
def test_unreadable_or_excessive_pdf_is_atomic_unavailable(tmp_path, raw):
    attachments = extract(tmp_path, raw)
    assert len(attachments) == 1
    assert attachments[0].content_status is Status.UNAVAILABLE
    assert attachments[0].source_event_id == "event:receipt"
    assert attachments[0].image_data_url is None


def test_combined_page_limit_preserves_first_complete_document(tmp_path):
    event = replace(
        EVENT,
        media_type="application/pdf",
        media_url="https://media.example.invalid/r.pdf",
    )
    attachments = extract(
        tmp_path,
        pdf_bytes(3),
        (replace(event, event_id="event:1"), replace(event, event_id="event:2")),
    )
    assert len(attachments) == 4
    assert [a.content_status for a in attachments] == [Status.IMAGE_READY] * 3 + [
        Status.UNAVAILABLE
    ]
    assert attachments[-1].source_event_id == "event:2"


@pytest.mark.parametrize("method", ["pix", "wise"])
def test_pdf_admission_archives_all_pages_and_replay_as_image_is_duplicate(
    tmp_path, method
):
    f = lab(tmp_path / "owner", method)
    raw = pdf_bytes()
    attachments = extract(tmp_path / "owner" / "proofs", raw)
    assert len(attachments) == 2
    request = replace(f.request, attachments=attachments)
    proof = replace(f.proof, source_sha256=attachments[-1].source_sha256)
    proposal = _proposal(json.dumps(response(proof.to_dict())), request)
    result = f.service.accept(request=request, proof=proposal.payment_proof)
    assert result["analysis"] == "accepted"
    assert result["human_review"] == "pending"
    assessment = json.loads(
        (tmp_path / "owner" / "proofs")
        .joinpath("assessments", result["assessment_hash"])
        .read_bytes()
    )
    assert assessment["document"]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert assessment["document"]["pages"] == [
        {"page_number": a.page_number, "source_sha256": a.source_sha256}
        for a in attachments
    ]
    again = f.service.accept(request=f.request, proof=f.proof)
    assert again["analysis"] == "duplicate"
    assert f.followup._connection.execute(
        "select count(*) from payment_commands"
    ).fetchone() == (1,)


def test_missing_or_inconsistent_pdf_page_group_is_rejected(tmp_path):
    f = lab(tmp_path / "owner")
    attachments = extract(tmp_path, pdf_bytes())
    assert len(attachments) == 2
    for pages in (
        (attachments[0],),
        (attachments[0], attachments[0]),
        (attachments[0], replace(attachments[1], document_sha256="a" * 64)),
    ):
        with pytest.raises(InvalidModelProposal, match="document"):
            replace(f.request, attachments=pages)


def test_pdf_served_as_generic_binary_is_identified_from_bytes(tmp_path):
    attachments = extract(tmp_path, pdf_bytes(1), mime="application/octet-stream")
    assert len(attachments) == 1
    assert attachments[0].content_status is Status.IMAGE_READY
    assert attachments[0].media_type == "application/pdf"


def test_render_timeout_is_unavailable_and_preserves_original(tmp_path, monkeypatch):
    import subprocess
    import v2_adapters.pdf_render as renderer

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 20)

    monkeypatch.setattr(renderer.subprocess, "run", timeout)
    raw = pdf_bytes(1)
    attachments = extract(tmp_path, raw)
    assert attachments[0].content_status is Status.UNAVAILABLE
    assert attachments[0].document_sha256 == hashlib.sha256(raw).hexdigest()


def test_pdf_cannot_authorize_from_altered_or_missing_archived_original(tmp_path):
    f = lab(tmp_path / "owner")
    raw = pdf_bytes(1)
    attachments = extract(tmp_path / "owner" / "proofs", raw)
    request = replace(f.request, attachments=attachments)
    proof = replace(f.proof, source_sha256=attachments[0].source_sha256)
    original = tmp_path / "owner" / "proofs" / "images" / attachments[0].document_sha256
    original.write_bytes(b"altered")
    with pytest.raises(ValueError, match="document origin"):
        f.service.accept(request=request, proof=proof)
    original.unlink()
    with pytest.raises(OSError):
        f.service.accept(request=request, proof=proof)
    assert f.followup._connection.execute(
        "select count(*) from payment_commands"
    ).fetchone() == (0,)


@pytest.mark.parametrize(
    "change",
    [
        {"status": "scheduled"},
        {"amount_minor": 1},
        {"source_event_id": "event:other"},
        {"source_sha256": "a" * 64},
    ],
)
def test_pdf_uses_same_financial_and_origin_checks(tmp_path, change):
    f = lab(tmp_path / "owner")
    attachments = extract(tmp_path / "owner" / "proofs", pdf_bytes(1))
    request = replace(f.request, attachments=attachments)
    proof = replace(f.proof, source_sha256=attachments[0].source_sha256)
    proof = replace(proof, **change)
    if "source_event_id" in change or "source_sha256" in change:
        with pytest.raises((ValueError, InvalidModelProposal)):
            f.service.accept(request=request, proof=proof)
    else:
        assert f.service.accept(request=request, proof=proof)["analysis"] != "accepted"
    assert f.followup._connection.execute(
        "select count(*) from payment_commands"
    ).fetchone() == (0,)


def test_identical_pdf_pages_remain_one_document(tmp_path):
    f = lab(tmp_path / "owner")
    writer = PdfWriter()
    page = PdfReader(BytesIO(pdf_bytes(1))).pages[0]
    writer.add_page(page)
    writer.add_page(page)
    out = BytesIO()
    writer.write(out)
    attachments = extract(tmp_path / "owner" / "proofs", out.getvalue())
    assert len(attachments) == 2
    assert attachments[0].source_sha256 == attachments[1].source_sha256
    request = replace(f.request, attachments=attachments)
    proof = replace(f.proof, source_sha256=attachments[0].source_sha256)
    assert f.service.accept(request=request, proof=proof)["analysis"] == "accepted"


def test_pdf_aggregate_budget_never_exposes_partial_document(tmp_path):
    import random

    images = [
        Image.frombytes(
            "RGB", (1200, 1200), random.Random(i).randbytes(1200 * 1200 * 3)
        )
        for i in range(3)
    ]
    out = BytesIO()
    images[0].save(out, "PDF", save_all=True, append_images=images[1:], resolution=144)
    assert len(out.getvalue()) < 4 * 1024 * 1024
    attachments = extract(tmp_path, out.getvalue())
    assert len(attachments) == 1
    assert attachments[0].content_status is Status.UNAVAILABLE


def test_legacy_image_metadata_unchanged():
    assert "document_sha256" not in image().to_dict()
