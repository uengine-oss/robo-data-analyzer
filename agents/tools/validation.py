"""Agent 정규식 검증 도구 모음.

``verify_table_regex`` — Scan Agent 가 table-access 정규식을 실제 소스에 돌려보고
매치·해소 결과를 받아본다. 엔진은 step5 stage1의 ``RegexTableLinker`` 그대로.

``verify_external_rule`` — External Format Agent 가 청크 분할 규칙을 실제 samples 에
돌려보고 스팬·키 결과를 받아본다. 엔진은 step4 ``external_skills`` 의 production
적용 경로 그대로 — "에이전트가 검증한 규칙 == 검증기/운영이 실행할 규칙".
"""
from __future__ import annotations

import re
from pathlib import Path

from langchain.tools import tool

from agents.contracts import DdlTransform, TableAccessPattern
from pipeline.steps.step5_linking.link_tables.regex_table_linker import (
    LinkOutcome,
    RegexTableLinker,
)
from graph.schema.graph_nodes import CodeNode, Table

_SAMPLE_PAGE_SIZE = 20


def make_verify_table_regex_tool(
    project_root: Path,
    code_nodes: list[CodeNode],
    tables: list[Table],
):
    """``verify_table_regex`` 도구 생성 — project_root / code_nodes / tables 를 묶는다."""

    # 멱등 가드 — 같은 입력은 결정론이므로 캐시 반환 + "다음 단계로" 명시로
    # 약한 모델의 재검증 루프 연료를 끊는다.
    validated: dict[str, LinkOutcome] = {}
    rendered_pages: dict[tuple[str, int], str] = {}
    repeat_count: dict[tuple[str, int], int] = {}

    @tool
    async def verify_table_regex(call_regex: str, offset: int = 0) -> str:
        """table-access 정규식을 실제 소스에 적용 → 매치·해소 결과 리포트.

        **언제 사용**:
        - SKILL contract 정규식을 복사했거나 직접 작성한 뒤, 출력 전 **반드시** 1회.
        - 직접 작성한 패턴이 나쁘면 소스를 다시 보고 수정 후 재호출한다.
        - 매칭된 SKILL contract가 실패하면 정규식을 변형하지 말고 Skill 매칭부터 재판정한다.

        **인자**:
        - `call_regex`: 검증할 정규식. 명명 그룹 `(?P<entity>...)` 필수,
          `(?P<op_token>...)` 권장. `entity` = 테이블 이름,
          `op_token` = 프레임워크 raw 매치 (예: 's', 'findOne', 'SELECT').
        - `offset`: 전체 검증 자료 중 읽기 시작할 위치. 응답에 다음 offset이 있으면
          같은 정규식을 다시 스캔하지 않고 이어서 읽는다.

        **결과**: 매치 건수 / R/W 엣지 건수 / 미해결·부분추출 건수 + 추출 샘플
        (entity·op_token·DDL 해소 여부). 샘플을 보고 정규식이 맞는지 판단한다.

        **실패 응답**:
        - `INVALID_REGEX: ...` — 정규식 문법 오류.
        - `MISSING_GROUP: ...` — `(?P<entity>...)` 명명 그룹 없음.
        """
        page_key = (call_regex, offset)
        if page_key in rendered_pages:
            n = repeat_count[page_key] = repeat_count.get(page_key, 1) + 1
            return (
                f"이미 검증 완료된 페이지다 (동일 입력 {n}회째 호출). 같은 입력은 "
                "결과가 결정론으로 동일하다 — 재검증 불필요. 아래는 이전 결과 그대로.\n"
                "남은 자료가 표시되면 다음 offset으로 이동하고, 없으면 다음 단계로 진행하라.\n"
                + rendered_pages[page_key]
            )

        if offset < 0:
            return "INVALID_OFFSET: offset은 0 이상이어야 함"

        if "(?P<entity>" not in call_regex:
            return ("MISSING_GROUP: 정규식에 (?P<entity>...) 명명 그룹이 없음 — "
                    "테이블 이름을 잡는 그룹은 필수")
        try:
            re.compile(call_regex)
        except re.error as e:
            return f"INVALID_REGEX: {e}"

        outcome = validated.get(call_regex)
        if outcome is None:
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
                collect_samples=True,
            )
            validated[call_regex] = outcome

        if outcome.matched_sites == 0:
            report = ("매치 0건 — 이 정규식은 소스의 어떤 호출과도 안 맞음. "
                      "wrapper 이름 · 따옴표 · 공백(\\s*) · 인자 구분자를 다시 확인하라.")
            rendered_pages[page_key] = report
            return report

        if offset >= len(outcome.samples):
            return (
                f"INVALID_OFFSET: 검증 자료는 {len(outcome.samples)}건이며 "
                f"offset={offset}부터 읽을 자료가 없음"
            )

        page_end = min(offset + _SAMPLE_PAGE_SIZE, len(outcome.samples))
        shown = outcome.samples[offset:page_end]
        lines = [
            f"매치 {outcome.matched_sites}건 · R/W엣지 {len(outcome.edges)}건 · "
            f"미해결 {len(outcome.unresolved)}건",
            f"전체 검증 자료 {len(outcome.samples)}건 중 {offset + 1}~{page_end}:",
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
        if page_end < len(outcome.samples):
            lines.append(
                f"남은 자료가 있다. 같은 call_regex와 offset={page_end}로 이어서 읽어라."
            )
        report = "\n".join(lines)
        rendered_pages[page_key] = report
        return report

    return verify_table_regex


def make_verify_external_rule_tool(samples: list[str], negative_samples: list[str]):
    """``verify_external_rule`` 도구 생성 — 미지 형식 samples/negative 를 묶는다."""
    from pipeline.steps.step4_scan_project.external_skills import (
        ExternalSkillValidationError,
        probe_extraction_rule,
    )

    @tool
    def verify_external_rule(pattern: str) -> str:
        """청크 분할 규칙 후보 1개를 실제 samples 에 적용 → 스팬·키 결과 리포트.

        **언제 사용**: 규칙을 설계한 뒤 submit_external 전에 **반드시** 규칙마다 1회.
        결과가 나쁘면 규칙을 고쳐 재호출한다.

        **인자**:
        - `pattern`: MULTILINE 로 컴파일되는 정규식. 전체 매치 = 청크 하나.
          key 는 named group 으로 (예: `(?P<key>...)`).
        - key capture 이름은 항상 `key`다.

        **결과 읽는 법**:
        - 각 sample 의 매치 수와 (key → 매치 길이·원문 앞부분) 목록. 매치가 레코드 전체를
          덮는지, key 가 올바른지 여기서 직접 확인한다.
        - `REJECTED:` = 규칙 자체가 실격 (금지 구문·빈 키·zero-width 등) — 원인대로 수정.
        - `NEGATIVE_MATCH` = 다른 형식 decoy 까지 삼킴 — 규칙이 너무 넓다.
        - 어떤 sample 이 매치 0건이면 그 구조를 다시 관찰하라.
        """
        lines: list[str] = []
        for index, sample in enumerate(samples):
            try:
                matches = probe_extraction_rule(pattern, "key", sample)
            except ExternalSkillValidationError as error:
                return f"REJECTED: {error}"
            lines.append(f"sample[{index}]: 매치 {len(matches)}건 (파일 {len(sample):,}자)")
            for match in matches[:8]:
                excerpt = sample[match.start:match.end].replace("\n", "\\n")
                if len(excerpt) > 90:
                    excerpt = excerpt[:90] + "…"
                lines.append(
                    f"  key='{match.key}' chars={match.end - match.start} text='{excerpt}'"
                )
            if len(matches) > 8:
                lines.append(f"  … 외 {len(matches) - 8}건")
            # 큰 파일 안전망: 매치가 앞부분에만 몰려 있으면 뒤쪽에 다른 구조가 있다는 신호.
            if matches:
                tail_start = matches[-1].end
                tail = sample[tail_start:]
                if len(tail.strip()) > 200:
                    tail_line = sample.count("\n", 0, tail_start) + 1
                    lines.append(
                        f"  ⚠ 마지막 매치 이후 {len(tail):,}자({tail_line}줄 이후)에 매치가 없다 — "
                        f"read_sample_range({index}, {tail_line}) 로 뒤쪽 구조를 확인하라."
                    )
        negative_hits = 0
        for sample in negative_samples:
            try:
                if probe_extraction_rule(pattern, "key", sample):
                    negative_hits += 1
            except ExternalSkillValidationError as error:
                # 검증기는 negative 에도 규칙을 돌리므로 여기서 죽는 규칙은 승격에서
                # 거부된다 — positive 가 멀쩡해도 삼키지 말고 그대로 드러낸다
                # ("검증한 규칙 == 검증기가 실행할 규칙" 계약).
                return f"REJECTED (decoy 적용 중): {error}"
        if negative_hits:
            lines.append(
                f"NEGATIVE_MATCH: 다른 형식 decoy {negative_hits}개 파일에서 매치 발생 — "
                "규칙이 너무 넓다. 이 형식 고유의 구조(고정 토큰·구분자)로 좁혀라."
            )
        return "\n".join(lines)

    return verify_external_rule
