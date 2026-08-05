from __future__ import annotations

from scripts.audit_semantic_context_packets import audit
from tests.unittest_function_cases import load_function_cases


def load_tests(_loader, standard_tests, _pattern):
    return load_function_cases(standard_tests, globals())


def test_audit_checks_exact_evidence_presence_without_language_heuristics() -> None:
    gold = [
        {
            "func_id": "code:sample:run",
            "gold_claims": [
                {
                    "claim_id": "c1",
                    "evidence_text": (
                        "10: もし 状態 == '準備済み'\n"
                        "11:   처리하기(값)\n"
                        "12: 끝"
                    ),
                }
            ],
        }
    ]
    prompt = {
        "records": [
            {
                "user_content": (
                    "target_id: code:sample:run\n"
                    "10 | もし 状態 == '準備済み'\n"
                    "11 |   처리하기(값)\n"
                    "12 | 끝\n"
                )
            }
        ]
    }

    result = audit(gold, prompt["records"])
    assert result["counts"]["claims_with_complete_evidence"] == 1
    target = result["targets"][0]
    assert target == {"target_id": "code:sample:run", "prompt_found": True}
