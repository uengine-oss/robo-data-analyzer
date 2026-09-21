"""spec 108 — DSL 표현력 증명 (채택 조건 a) + 컴파일러 계약.

기존 스킬 6종의 유효 패턴 9개 전부를 DSL 로 표현하고, 원본 정규식과의 **행동 동일성**을
양성/음성 샘플로 증명한다. (원본이 entity group 을 빠뜨린 4패턴은 스킬 문서의 의도대로
교정된 행동을 기준으로 한다 — evidence/expressiveness.md 경위.)
"""
from __future__ import annotations

import re

import pytest
from tests.unittest_function_cases import load_function_cases

from agents.agent_contracts import MethodCallRule, OpMapping, SqlFragmentRule, WrapperKeyRule
from agents.rule_compiler import RuleCompileError, compile_rule

_OPS = [OpMapping(token="s", op="SELECT")]


def _match(rule, text):
    pattern = compile_rule(rule)
    m = re.search(pattern.call_regex, text)
    return (m.group("entity"), m.group("op_token")) if m else None


# ── ① proframe (가장 어려운 케이스 — lookbehind) ─────────────


def _proframe_main():
    return WrapperKeyRule(
        id="proframe_mpfmdbio", wrapper="mpfmdbio", separator="_",
        op_tokens=["s", "i", "u", "d"], suffix_digits=4, entity_not_ending="_s",
        op_mappings=[OpMapping(token=t, op=o) for t, o in
                     [("s", "SELECT"), ("i", "INSERT"), ("u", "UPDATE"), ("d", "DELETE")]],
    )


def test_proframe_main_rw_behavior_identical():
    """실코퍼스 형태 — 본 R/W 매치 + 시퀀스 변종 거부 (`_s` lookbehind)."""
    rule = _proframe_main()
    assert _match(rule, 'rc = mpfmdbio( "zord_jncard_s0011" , &in, &out );') == \
        ("zord_jncard", "s")
    assert _match(rule, 'mpfmdbio("zsys_ctz_corp_biz_num_sprat_i0001", &in, NULL)') == \
        ("zsys_ctz_corp_biz_num_sprat", "i")
    # 시퀀스 변종(entity 가 _s 로 끝나게 되는 매치)은 본 패턴이 거부해야 함 — 원본 lookbehind 계약
    got = _match(rule, 'mpfmdbio( "dual_zord_cntr_s_s0001", &in, &out );')
    assert got is None or not got[0].endswith("_s")


def test_wrapper_key_supports_a_multi_character_separator():
    rule = WrapperKeyRule(
        id="generic_wrapper_infix", wrapper="db_call", separator="__",
        op_tokens=["read"], suffix_digits=3,
        op_mappings=[OpMapping(token="read", op="SELECT")],
    )
    assert _match(rule, 'db_call("account__read001", input);') == \
        ("account", "read")


# ── ② method_call — receiver entity (jpa/ejb/mybatis 교정 포함) ──


def test_jpa_repository_receiver_entity():
    """원본은 entity group 누락(죽은 규칙) — DSL 은 구조상 누락 불가."""
    rule = MethodCallRule(
        id="jpa_repository_call", receiver_suffix="Repository",
        op_prefixes=["find", "save", "delete", "update", "exists", "count"],
        entity_from="receiver",
        op_mappings=[OpMapping(token="find", op="SELECT"), OpMapping(token="save", op="INSERT")],
    )
    assert _match(rule, "orderItemRepository.findByUserId(uid);") == ("orderItem", "find")
    assert _match(rule, "userRepository.save(user);") == ("user", "save")
    assert _match(rule, "helper.compute(x);") is None


def test_ejb_home_receiver_entity():
    rule = MethodCallRule(
        id="ejb_home_call", receiver_suffix="Home",
        op_prefixes=["find", "create", "remove"], entity_from="receiver",
        op_mappings=[OpMapping(token="find", op="SELECT")],
    )
    assert _match(rule, "orderHome.findByPrimaryKey(pk);") == ("order", "find")


def test_mybatis_sqlsession_first_arg():
    rule = MethodCallRule(
        id="mybatis_sqlsession", receiver_name="sqlSession",
        op_prefixes=["selectOne", "selectList", "insert", "update", "delete"],
        entity_from="first_arg_string", first_arg_charset=r"[\w.]",
        op_mappings=[OpMapping(token="selectOne", op="SELECT")],
    )
    assert _match(rule, 'sqlSession.selectOne("Order.findById", id);') == \
        ("Order.findById", "selectOne")


def test_mybatis_mapper_receiver_entity():
    rule = MethodCallRule(
        id="mybatis_mapper", receiver_suffix="Mapper",
        op_prefixes=["select", "find", "insert", "update", "delete", "count"],
        entity_from="receiver",
        op_mappings=[OpMapping(token="select", op="SELECT")],
    )
    assert _match(rule, "orderMapper.selectByExample(ex);") == ("order", "select")


def test_ofbiz_delegator_first_arg():
    rule = MethodCallRule(
        id="ofbiz_delegator", receiver_name="delegator",
        op_prefixes=["findOne", "findByPrimaryKey", "findList", "findAll", "find",
                     "create", "store", "remove", "removeAll"],
        entity_from="first_arg_string",
        op_mappings=[OpMapping(token="findOne", op="SELECT")],
    )
    # 긴 prefix 우선 — findOne 이 find 에 잡아먹히면 op_mappings 매칭 실패 (원본 계약)
    assert _match(rule, 'delegator.findOne("Product", pk);') == ("Product", "findOne")


# ── ③ sql_fragment (shopmall — C 문자열 개행 이음) ───────────


def test_shopmall_sql_fragments():
    cases = [
        ("INSERT", "INTO", "", '"INSERT INTO "\n       "CUST_ORDER (A,B)"', "CUST_ORDER"),
        ("UPDATE", "", "SET", '"UPDATE CUST_ORDER "  "SET A = 1"', "CUST_ORDER"),
        ("DELETE", "FROM", "", '"DELETE FROM CUST_ORDER WHERE 1=1"', "CUST_ORDER"),
        ("FROM", "", "", '"SELECT A FROM CUST_ORDER"', "CUST_ORDER"),
    ]
    for kw, before, after, src, entity in cases:
        rule = SqlFragmentRule(
            id=f"shopmall_{kw.lower()}", keyword=kw,
            before_entity=before, after_entity=after,
            op_mappings=[OpMapping(token=kw, op="SELECT")],
        )
        assert _match(rule, src) == (entity, kw)


def test_sql_fragment_preserves_schema_qualified_entity():
    rule = SqlFragmentRule(
        id="jdbc_insert", keyword="INSERT", before_entity="INTO",
        op_mappings=[OpMapping(token="INSERT", op="INSERT")],
    )
    assert _match(
        rule, 'conn.prepareStatement("INSERT INTO HRM.HR_LEAVE_REQUEST (ID) VALUES (?)")'
    ) == ("HRM.HR_LEAVE_REQUEST", "INSERT")


# ── 컴파일러 계약 (FR-002·FR-006) ────────────────────────────


def test_compiled_regex_always_has_named_groups():
    for rule in (_proframe_main(),
                 MethodCallRule(id="x", receiver_suffix="Repo", op_prefixes=["find"],
                                entity_from="receiver", op_mappings=_OPS),
                 SqlFragmentRule(id="y", keyword="FROM", op_mappings=_OPS)):
        compiled = re.compile(compile_rule(rule).call_regex)
        assert {"entity", "op_token"} <= set(compiled.groupindex)


def test_compile_errors_are_loud():
    with pytest.raises(RuleCompileError):
        compile_rule(WrapperKeyRule(id="bad", wrapper=" ", separator="_",
                                    op_tokens=["s"], op_mappings=_OPS))
    with pytest.raises(RuleCompileError):
        compile_rule(WrapperKeyRule(id="bad", wrapper="w", separator="_",
                                    op_tokens=[" "], op_mappings=_OPS))
    with pytest.raises(RuleCompileError):
        compile_rule(MethodCallRule(id="bad", op_prefixes=["find"],
                                    entity_from="receiver", op_mappings=_OPS))  # suffix 없음
    with pytest.raises(RuleCompileError):
        compile_rule(SqlFragmentRule(id="bad", keyword="", op_mappings=_OPS))


def load_tests(_loader, standard_tests, _pattern):
    return load_function_cases(standard_tests, globals())
