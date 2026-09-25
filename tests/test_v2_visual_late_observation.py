from dataclasses import replace
from datetime import timedelta
import json
import pytest
from tests.test_v2_visual_proof_journey import lab, NOW
from tests.test_v2_visual_media_turn import response
from v2_adapters.hermes_model import _proposal
from v2_contracts.providers import ReadObservation
from v2_contracts.model import InvalidModelProposal


def test_receipt_cannot_be_silently_dropped_in_post_read_round(tmp_path):
    f=lab(tmp_path)
    observation=ReadObservation('a'*64,'knowledge',NOW,NOW+timedelta(minutes=1),{},'b'*64)
    request=replace(f.request,observations=(observation,))
    with pytest.raises(InvalidModelProposal):
        _proposal(json.dumps(response(f.proof.to_dict())).encode(),request)
