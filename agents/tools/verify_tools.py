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

from agents.agent_contracts import DdlTransform, TableAccessPattern
from pipeline.stages.links.tables.regex_table_linker import (
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
    next_offsets: dict[tuple[str, int], int | None] = {}

    @tool
    async def verify_table_regex(call_regex: str, offset: int = 0) -> str:
        """정규식을 production table linker로 실제 source·DDL에 검증한다.

        `(?P<entity>...)`는 필수이고 `(?P<op_token>...)`은 권장한다. page는 전체
        match/RW/unresolved와 대표 entity·operation·DDL 해소를 보여 준다. 반환된 다음
        offset으로 모든 검증 자료를 읽고, partial·미해소·미인식 operation을 제출 전에 고친다.
        """
        page_key = (call_regex, offset)
        if page_key in rendered_pages:
            next_offset = next_offsets[page_key]
            action = (
                f"offset={next_offset}로 이동하라"
                if next_offset is not None
                else "재검증하지 말고 다음 단계로 진행하라"
            )
            return f"ALREADY_VERIFIED_PAGE: 결과는 앞선 tool message와 동일하다. {action}."

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
            next_offsets[page_key] = None
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
        if any(not sample.entity_complete for sample in shown):
            lines.append(
                "교정: 부분 추출된 entity가 있다. 실제 테이블 토큰 전체를 캡처하도록 경계를 고쳐라."
            )
        if any(sample.entity_complete and not sample.resolved_table for sample in shown):
            lines.append(
                "판정: 완전 추출됐지만 DDL에 없는 entity가 있다. 범위 밖 table인지 pattern 오류인지 확인하라."
            )
        if any(sample.op_token and not sample.token_recognized for sample in shown):
            lines.append(
                "교정: 인식되지 않은 op_token이 있다. operation token capture/mapping을 확인하라."
            )
        next_offset = page_end if page_end < len(outcome.samples) else None
        if page_end < len(outcome.samples):
            lines.append(
                f"남은 자료가 있다. 같은 call_regex와 offset={page_end}로 이어서 읽어라."
            )
        report = "\n".join(lines)
        rendered_pages[page_key] = report
        next_offsets[page_key] = next_offset
        return report

    return verify_table_regex


def make_verify_external_rule_tool(samples: list[str], negative_samples: list[str]):
    """``verify_external_rule`` 도구 생성 — 미지 형식 samples/negative 를 묶는다."""
    from pipeline.stages.scan.external_skills import (
        ExternalSkillValidationError,
        probe_extraction_rule,
    )

    @tool
    def verify_external_rule(pattern: str) -> str:
        """pattern을 production extractor로 모든 positive·negative sample에 검증한다.

        전체 match 하나가 record 하나여야 하고 capture 이름은 `key`다. sample별 match 수와
        대표 key·길이·원문, unmatched tail, `REJECTED|NEGATIVE_MATCH`를 반환한다. 모든 규칙을
        제출 전에 검증하고 대표 원문이 record 전체를 덮는지 확인한다.
        """
        lines: list[str] = []
        for index, sample in enumerate(samples):
            try:
                matches = probe_extraction_rule(pattern, "key", sample)
            except ExternalSkillValidationError as error:
                return f"REJECTED: {error}"
            lines.append(f"sample[{index}]: 매치 {len(matches)}건")
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
