"""spec 054 US2 — 제어 흐름 뼈대 보존 + owner routine 스레딩 회귀 테스트.

parser 007 이 emit 하는 C 제어문(IF/ELSE/LOOP/SWITCH/CASE)을 reader 가:
  (a) CodeNode 로 그래프에 보존하되,
  (b) 그 아래 FUNCTION_CALL 의 CALLS 는 구조 부모(IF)가 아니라 소유 routine(함수)에 귀속,
  (c) PARENT_OF 로 중첩을 보존,
  (d) routine 이 아니라 분석 대상(ROUTINE_LABELS)에서 제외되는지
검증한다.
"""
from pathlib import Path

from analysis_flow.step2_load_ast.antlr_json_reader import _read_framework
from shared.lang.ontology import ROUTINE_LABELS


def _sample_raw() -> dict:
    """C 파서가 만들 법한 AST: 함수 아래 IF(→ELSE)·LOOP, 그 아래 호출."""
    return {
        "type": "FILE", "fileName": "cart.c", "filePath": "cart.c",
        "children": [
            {"type": "FUNCTION", "name": "checkout", "startLine": 1, "endLine": 20,
             "signature": "int checkout(int paid)", "children": [
                {"type": "IF", "startLine": 2, "endLine": 8, "children": [
                    {"type": "FUNCTION_CALL", "name": "save_order", "startLine": 3},
                    {"type": "ELSE", "startLine": 5, "endLine": 7, "children": [
                        {"type": "FUNCTION_CALL", "name": "log_fail", "startLine": 6},
                    ]},
                ]},
                {"type": "LOOP", "startLine": 9, "endLine": 12, "children": [
                    {"type": "FUNCTION_CALL", "name": "tick", "startLine": 10},
                ]},
             ]},
            {"type": "FUNCTION", "name": "plain", "startLine": 22, "endLine": 24, "children": []},
        ],
    }


def _read() -> object:
    return _read_framework(_sample_raw(), "c", Path("."), Path("cart.c.json"))


def test_control_nodes_preserved_as_codenodes():
    r = _read()
    labels = {n.label for n in r.code_nodes}
    # 함수 2개 + 제어문 IF/ELSE/LOOP 가 CodeNode 로 존재
    assert "FUNCTION" in labels
    assert {"IF", "ELSE", "LOOP"} <= labels, f"제어문 노드 누락: {labels}"


def test_calls_owned_by_routine_not_control_node():
    r = _read()
    checkout = next(n for n in r.code_nodes if n.label == "FUNCTION" and n.name == "checkout")
    # save_order / log_fail / tick 모두 IF/ELSE/LOOP 아래지만 caller 는 함수여야 한다
    called = {c["call_text"]: c["caller_id"] for c in r.call_sites}
    assert called.keys() >= {"save_order", "log_fail", "tick"}, called
    for name in ("save_order", "log_fail", "tick"):
        assert called[name] == checkout.id, (
            f"{name} 의 caller 가 함수가 아님: {called[name]} (기대 {checkout.id})"
        )
    # 어떤 호출도 제어문 노드를 caller 로 갖지 않는다
    control_ids = {n.id for n in r.code_nodes if n.label in ("IF", "ELSE", "LOOP")}
    assert not (set(called.values()) & control_ids), "CALLS 가 제어문 노드에 귀속됨(회귀)"


def test_parent_of_nesting_preserved():
    r = _read()
    checkout = next(n for n in r.code_nodes if n.label == "FUNCTION" and n.name == "checkout")
    if_node = next(n for n in r.code_nodes if n.label == "IF")
    else_node = next(n for n in r.code_nodes if n.label == "ELSE")
    loop_node = next(n for n in r.code_nodes if n.label == "LOOP")
    pairs = {(e.parent_id, e.child_id) for e in r.parent_of_edges}
    assert (checkout.id, if_node.id) in pairs, "함수→IF PARENT_OF 누락"
    assert (if_node.id, else_node.id) in pairs, "IF→ELSE PARENT_OF 누락"
    assert (checkout.id, loop_node.id) in pairs, "함수→LOOP PARENT_OF 누락"


def test_control_nodes_excluded_from_analysis_targets():
    r = _read()
    # routine(분석 대상)은 FUNCTION 만. 제어문 라벨은 ROUTINE_LABELS 에 없어 step6 가 제외.
    for n in r.code_nodes:
        if n.label in ("IF", "ELSE", "LOOP", "SWITCH", "CASE"):
            assert n.label not in ROUTINE_LABELS, f"{n.label} 이 routine 으로 오분류"
    routines = [n for n in r.code_nodes if n.label in ROUTINE_LABELS]
    assert len(routines) == 2, f"routine 은 함수 2개여야 함: {[n.name for n in routines]}"


def test_control_nodes_have_no_llm_fields():
    r = _read()
    # 구조 노드는 결정론 필드만 — summary/domain_terms 등 LLM 산물은 비어야 한다.
    for n in r.code_nodes:
        if n.label in ("IF", "ELSE", "LOOP"):
            assert n.summary is None
            assert not n.domain_terms
            assert n.stereotype is None
            assert n.owner_id  # 소속은 채워짐(결정론)
