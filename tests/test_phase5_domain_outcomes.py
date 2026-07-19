from __future__ import annotations

import json
import unittest

from reservation_domain import (
    ExecutionCertainty,
    ExecutionOutcome,
    dumps_outcome,
    loads_outcome,
)


class Phase5DomainOutcomeTests(unittest.TestCase):
    def test_effect_confirmed_requires_reference_and_evidence(self) -> None:
        with self.assertRaisesRegex(ValueError, "evidence"):
            ExecutionOutcome(
                command_id="command:phase5:confirmed",
                certainty=ExecutionCertainty.EFFECT_CONFIRMED,
                normalized_status="confirmed",
                provider_reference="provider:synthetic:1",
                evidence=(),
            )

    def test_not_called_rejects_provider_reference(self) -> None:
        with self.assertRaisesRegex(ValueError, "provider_reference"):
            ExecutionOutcome(
                command_id="command:phase5:not-called",
                certainty=ExecutionCertainty.NOT_CALLED,
                normalized_status="not_called",
                provider_reference="provider:impossible",
                evidence=("a" * 64,),
            )

    def test_outcome_roundtrip_is_canonical(self) -> None:
        outcome = ExecutionOutcome(
            command_id="command:phase5:unknown",
            certainty=ExecutionCertainty.CALLED_UNKNOWN,
            normalized_status="response_lost",
            evidence=("b" * 64,),
        )
        raw = dumps_outcome(outcome)
        self.assertEqual(loads_outcome(raw), outcome)
        self.assertEqual(dumps_outcome(loads_outcome(raw)), raw)

    def test_outcome_loader_rejects_duplicate_keys_bool_and_unknown_fields(self) -> None:
        valid = json.loads(
            dumps_outcome(
                ExecutionOutcome(
                    command_id="command:phase5:no-effect",
                    certainty=ExecutionCertainty.CALLED_NO_EFFECT,
                    normalized_status="declined",
                    evidence=("c" * 64,),
                )
            )
        )
        valid["data"]["unknown"] = True
        with self.assertRaises(ValueError):
            loads_outcome(json.dumps(valid))
        canonical = json.loads(
            dumps_outcome(
                ExecutionOutcome(
                    command_id="command:phase5:bool",
                    certainty=ExecutionCertainty.CALLED_UNKNOWN,
                    normalized_status="response_lost",
                    evidence=("d" * 64,),
                )
            )
        )
        canonical["schema_version"] = True
        with self.assertRaises(ValueError):
            loads_outcome(json.dumps(canonical))
        with self.assertRaises(ValueError):
            loads_outcome(
                '{"schema_version":1,"schema_version":1,'
                '"type":"execution_outcome","data":{}}'
            )


if __name__ == "__main__":
    unittest.main()
