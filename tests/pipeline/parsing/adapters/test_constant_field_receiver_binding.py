"""`private final Dao dao = new Dao();` 로 선언된 수신자도 묶인다 (ISS-59).

⚠ 2026-10-21 실측. `final` 필드는 `Constant`(라벨 `CONSTANT_FIELD`)로 모델링되는데
수신자 타입을 푸는 `_receiver_owner` 는 라벨이 `FIELD`/`MEMBER` 인 `Variable` 만 본다.
그래서 `employeeDao.selectByEmpNo(…)` 같은 **서비스→DAO 호출이 통째로**
`java_receiver_type_binding_environment_required` 로 떨어졌다 — 한 번의 분석에서
호출 539건 중 387건 미해결, 그 중 **266건**이 이 사유였다.

선언 타입은 원문에 적혀 있고 그래프에도 들어 있다(CONSTANT_FIELD 43개 중 42개).
`_receiver_declarations` 가 그 선언을 필드 선언으로 되돌려 넘기면 바인딩 규칙을
건드리지 않고도 묶인다.
"""
from __future__ import annotations

from graph.schema.graph_nodes import Constant, Variable
from pipeline.stages.links.calls.link_parser_calls_step import _receiver_declarations

from tests.pipeline.parsing.adapters.test_java_call_adapter import (
    _call,
    _callable,
    _classify,
    _node,
    _type_import,
)


def _dao_field(owner_id: str) -> Constant:
    """`private final Repository repository = new Repository();`"""
    return Constant(
        id="code:sample:Sample:repository",
        owner_id=owner_id,
        name="repository",
        file_path="Sample.java",
        start_line=1,
        end_line=1,
        label="CONSTANT_FIELD",
        value="newRepository()",
        var_type="Repository",
    )


def test_constant_field_declaration_is_offered_to_receiver_binding() -> None:
    owner = "code:sample:Sample"
    promoted = _receiver_declarations([], [_dao_field(owner)])

    assert len(promoted) == 1
    declaration = promoted[0]
    assert isinstance(declaration, Variable)
    assert declaration.label == "FIELD"
    assert declaration.var_type == "Repository"
    assert declaration.name == "repository"
    assert declaration.owner_id == owner


def test_constant_field_without_a_declared_type_is_not_offered() -> None:
    """선언 타입이 없으면 **추측하지 않는다** — 그대로 미해결로 남긴다."""
    untyped = Constant(
        id="code:sample:Sample:MAX",
        owner_id="code:sample:Sample",
        name="MAX",
        file_path="Sample.java",
        start_line=1,
        end_line=1,
        label="CONSTANT_FIELD",
        value="10",
    )

    assert _receiver_declarations([], [untyped]) == []


def test_plain_variables_pass_through_unchanged() -> None:
    plain = Variable(
        id="code:sample:Sample:local", owner_id="code:sample:Sample",
        name="local", file_path="Sample.java", start_line=1, end_line=1,
        label="FIELD", var_type="Repository",
    )

    assert _receiver_declarations([plain], []) == [plain]


def test_final_field_receiver_now_resolves_to_an_internal_call() -> None:
    """이 시험이 **고침 전에는 실패**한다 — 그때는 edge 가 0건이었다."""
    caller = _node("code:Sample.java:run", "run")
    target = _node(
        "code:Repository.java:find", "find", start=10,
        owner_id="code:sample.data:Repository",
    )

    result = _classify(
        _call(
            "java-final-field-receiver", caller, "find",
            receiver=True, receiver_source="repository",
        ),
        [caller, target],
        [_callable(
            "java-find", target, class_start=250, source_id="Repository.java",
        )],
        import_refs=[_type_import(
            "java-repository-import",
            path_components=["sample", "data", "Repository"],
        )],
        variables=_receiver_declarations([], [_dao_field(caller.owner_id)]),
    )

    assert [edge.callee_id for edge in result.edges] == [target.id]
    assert result.ledger[0]["resolution_status"] == "internal"


def test_without_the_promotion_the_same_call_stays_unresolved() -> None:
    """고치지 않은 쪽을 **나란히** 둔다 — 무엇이 달라졌는지 시험이 말해 준다."""
    caller = _node("code:Sample.java:run", "run")
    target = _node(
        "code:Repository.java:find", "find", start=10,
        owner_id="code:sample.data:Repository",
    )

    result = _classify(
        _call(
            "java-final-field-receiver", caller, "find",
            receiver=True, receiver_source="repository",
        ),
        [caller, target],
        [_callable(
            "java-find", target, class_start=250, source_id="Repository.java",
        )],
        import_refs=[_type_import(
            "java-repository-import",
            path_components=["sample", "data", "Repository"],
        )],
        variables=[],
    )

    assert result.edges == []
    assert result.ledger[0]["reason"] == (
        "java_receiver_type_binding_environment_required"
    )
