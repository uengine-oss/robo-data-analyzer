from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_framework_error_handler_is_not_modelled_as_insufficient() -> None:
    contract = (
        ROOT
        / "pipeline"
        / "meaning"
        / "rule"
        / "prompts"
        / "common"
        / "rule_meaning_contract.md"
    ).read_text(encoding="utf-8")

    assert "프레임워크 예외 handler" in contract
    assert "`implementation_detail`" in contract
    assert "`insufficient`로 분류하지 않는다" in contract
