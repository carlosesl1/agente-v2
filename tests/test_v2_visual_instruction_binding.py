from v2_adapters.wise import WiseInstructionAdapter
from v2_contracts.payments import PaymentObligation, BusinessUnit, DueKind


def test_instruction_persists_exact_requested_money_not_current_config():
    from v2_application.payments import _offer_bytes, _offer_from_bytes

    obligation = PaymentObligation(
        "payment:test",
        "anchor:test",
        BusinessUnit.AGENCY,
        73080,
        "BRL",
        DueKind.PREPAYMENT,
        1,
        "receiver:test",
    )
    instruction = WiseInstructionAdapter(
        instructions={"receiver:test": "Dados de transferência"},
        payment_percentages={"receiver:test": 20},
    ).instruction(obligation)
    assert instruction.requested_amount_minor == 14616
    assert instruction.currency == "BRL"
    assert _offer_from_bytes(_offer_bytes(instruction)) == instruction
