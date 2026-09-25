from dataclasses import replace
from io import BytesIO
import random
import httpx
from PIL import Image
from tests.test_v2_turn_executor import EVENT
from v2_adapters.proof_media import ProofMediaReader
from v2_contracts.model import AttachmentContentStatus, ModelRequest
from v2_adapters.hermes_model import _request_wire
from v2_host.hermes_child import _closed_request


def exercise(tmp_path, raw, count):
    reader=ProofMediaReader(allowed_hosts=("media.example.invalid",),archive=tmp_path,client=httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,content=raw,headers={"content-type":"image/png"}))))
    events=tuple(replace(EVENT,event_id=f"event:{i}",media_type="image/png",media_url="https://media.example.invalid/r.png") for i in range(count))
    attachments=reader.extract(events)
    request=ModelRequest("request:bounds",EVENT.lead_id,"batch:bounds","Texto preservado","pt-BR",0,attachments=attachments)
    wire=_closed_request(_request_wire(request,system_prompt="isolated test"))
    assert "Texto preservado" in wire["messages"][-1][1]
    return attachments,wire


def test_fifth_image_is_unavailable_without_losing_text_or_event(tmp_path):
    out=BytesIO();Image.new("RGB",(2,2),"white").save(out,"PNG")
    attachments,wire=exercise(tmp_path,out.getvalue(),5)
    assert len(wire["images"])==4
    assert attachments[-1].source_event_id=="event:4"
    assert attachments[-1].content_status is AttachmentContentStatus.UNAVAILABLE


def test_encoded_aggregate_budget_not_only_per_image_size(tmp_path):
    out=BytesIO();Image.frombytes("RGB",(1024,1024),random.Random(4).randbytes(1024*1024*3)).save(out,"PNG")
    attachments,wire=exercise(tmp_path,out.getvalue(),2)
    assert len(wire["images"])==1
    assert attachments[0].content_status is AttachmentContentStatus.IMAGE_READY
    assert attachments[1].content_status is AttachmentContentStatus.UNAVAILABLE
