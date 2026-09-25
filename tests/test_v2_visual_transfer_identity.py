from dataclasses import replace
import pytest
from tests.test_v2_visual_transfer import evidence
from reservation_followup.types import PaymentMethod


@pytest.mark.parametrize("value", ("0", "0000123456789"))
def test_wise_transfer_id_has_one_positive_canonical_spelling(value):
    with pytest.raises(ValueError):
        replace(evidence(PaymentMethod.WISE), transaction_id=value)
