"""Scan Agent 의 정규식 검증 도구.

``verify_table_regex`` — 에이전트가 차용/작성한 table-access 정규식을 실제 소스에
돌려보고 매치·해소 결과를 받아본다. 에이전트는 이 피드백으로 정규식을 고친다.

검증 엔진은 step5 stage1의 ``RegexTableLinker`` 그대로 — "에이전트가 검증한
정규식 == 링킹이 실제로 쓰는 정규식" 을 코드 한 곳으로 보장한다.
"""
from __future__ import annotations

import re
from pathlib import Path

from langchain.tools import tool

from agents.contracts import DdlTransform, TableAccessPattern
from pipeline.steps.step5_linking.link_tables.regex_table_linker import RegexTableLinker
from graph.schema.graph_nodes import CodeNode, Table

_SAMPLE_SHOWN = 20


def make_verify_table_regex_tool(
    project_root: Path,
    code_nodes: list[CodeNode],
    tables: list[Table],
):
    """``verify_table_regex`` 도구 생성 — project_root / code_nodes / tables 를 묶는다."""

    @tool
    async def verify_table_regex(call_regex: str) -> str:
        """table-access 정규식을 실제 소스에 적용 → 매치·해소 결과 리포트.

        **언제 사용**:
        - SKILL contract 정규식을 복사했거나 직접 작성한 뒤, 출력 전 **반드시** 1회.
        - 직접 작성한 패턴이 나쁘면 소스를 다시 보고 수정 후 재호출한다.
        - 매칭된 SKILL contract가 실패하면 정규식을 변형하지 말고 Skill 매칭부터 재판정한다.

        **인자**:
        - `call_regex`: 검증할 정규식. 명명 그룹 `(?P<entity>...)` 필수,
          `(?P<op_token>...)` 권장. `entity` = 테이블 이름,
          `op_token` = 프레임워크 raw 매치 (예: 's', 'findOne', 'SELECT').

        **결과**: 매치 건수 / R/W 엣지 건수 / 미해결·부분추출 건수 + 추출 샘플
        (entity·op_token·DDL 해소 여부). 샘플을 보고 정규식이 맞는지 판단한다.

        **실패 응답**:
        - `INVALID_REGEX: ...` — 정규식 문법 오류.
        - `MISSING_GROUP: ...` — `(?P<entity>...)` 명명 그룹 없음.
        """
        if "(?P<entity>" not in call_regex:
            return ("MISSING_GROUP: 정규식에 (?P<entity>...) 명명 그룹이 없음 — "
                    "테이블 이름을 잡는 그룹은 필수")
        try:
            re.compile(call_regex)
        except re.error as e:
            return f"INVALID_REGEX: {e}"

        candidate = TableAccessPattern(
            id="candidate",
            call_regex=call_regex,
            op_mappings=[],
        )
        outcome = await RegexTableLinker().link(
            project_root=project_root,
            code_nodes=code_nodes,
            tables=tables,
            patterns=[candidate],
            ddl_transform=DdlTransform(ddl_naming_rule="as_is"),
        )

        if outcome.matched_sites == 0:
            return ("매치 0건 — 이 정규식은 소스의 어떤 호출과도 안 맞음. "
                    "wrapper 이름 · 따옴표 · 공백(\\s*) · 인자 구분자를 다시 확인하라.")

        shown = outcome.samples[:_SAMPLE_SHOWN]
        lines = [
            f"매치 {outcome.matched_sites}건 · R/W엣지 {len(outcome.edges)}건 · "
            f"미해결 {len(outcome.unresolved)}건",
            f"추출 샘플 (앞 {len(shown)}건):",
        ]
        for s in shown:
            mark = "O" if s.resolved_table and s.entity_complete else "X"
            tail = (
                s.resolved_table if s.resolved_table
                else "부분 추출" if not s.entity_complete
                else "DDL 테이블 아님"
            )
            lines.append(f"  [{mark}] entity='{s.entity}' op_token='{s.op_token}' -> {tail}")
        lines.append(
            "힌트: '부분 추출'은 entity 뒤에 `_`, `-`, `.`, `$` 등 실제 이름 문자가 "
            "남았다는 뜻이다. 전체 테이블 토큰을 캡처하라. entity 에 '_s0024' 같은 꼬리표가 "
            "붙으면 반대로 그룹이 너무 넓다 — 경계를 좁혀라. op_token 이 변수명이면 교정하라. "
            "[X] 가 일부뿐이면 그 테이블은 분석 범위 밖(external)일 수 있다."
        )
        return "\n".join(lines)

    return verify_table_regex
