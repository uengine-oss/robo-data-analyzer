from __future__ import annotations

import unittest

from scripts.build_semantic_verification_packets import deterministic_rule_evidence


class DeterministicRuleReviewPacketTest(unittest.TestCase):
    def test_rule_stage_declares_llm_zero_and_preserves_exact_structure(self) -> None:
        record = {
            "schema": "deterministic-rule/v1",
            "target_id": "code:order.c:validate",
            "evidence": {
                "conditions": {"C1": "ready"},
                "effects": {},
                "rows": {"R1": {"role": "decision", "when": ["C1"]}},
            },
            "rules": [{
                "anchor_line": 1,
                "cond": 'F("ready")',
                "then": [],
                "nl": "",
            }],
        }

        packet = deterministic_rule_evidence(record)

        self.assertEqual("deterministic_rule_structure", packet["concern"])
        self.assertEqual(0, packet["metadata"]["llm_calls"])
        self.assertEqual(record["rules"], packet["response"]["parsed"]["rules"])
        self.assertEqual(0, packet["prompt"]["system_chars"])


if __name__ == "__main__":
    unittest.main()
