"""RegexTableLinker — ``table_access_patterns`` 의 regex 로 소스 grep → R/W 엣지.

흐름:
1. 정규식 매칭 → (caller_function_id, op, entity) 추출.
2. ``ddl_transform`` 적용 → entity → DDL 테이블 이름 변환.
3. DDL 테이블 lookup 성공 → ``ReadsWrites`` 엣지.
4. lookup 실패 → ``unresolved`` 케이스 (Linking Agent 가 추적).

LLM 0회. 결정론.

**검증도 같은 엔진** — ``verify_table_patterns`` 는 이 링커를 그대로 한 번 돌려
판정한다. "검증된 정규식 == 링킹이 실제로 작동하는 정규식" 을 코드 한 곳으로 보장
(별도 검증 로직이 drift 하지 않음).

**성능**: 파일 1회만 read — 모든 패턴이 같은 파일에서 ``finditer`` (N*M → N+M*P).
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

from data_models.agent_output import DdlTransform, TableAccessPattern
from data_models.graph_edges import ReadsWrites, derive_dml_type
from data_models.graph_nodes import Function, Table
from shared.config.env_catalog import ENV
from shared.log.logger import log_warning

_LOG = "analysis_flow.step4_linking.stage1_regex_grep.regex_table_linker"

# samples 누적 상한 — 검증 피드백·로그용. LLM 컨텍스트 폭증 방지.
_SAMPLE_CAP = 25


@dataclass
class MatchSample:
    """정규식 매치 1건의 추출 결과 — 검증 피드백용 (정규식이 맞는지 눈으로 확인)."""
    entity: str
    op_token: str                       # named group <op_token> raw 매치 (예: 's', 'findOne', 'SELECT')
    token_recognized: bool              # token 이 op_mappings 에 있나 (→ 표준 op 변환 가능)
    resolved_table: Optional[str]       # entity → DDL fqn (None = 미해소)


@dataclass
class LinkOutcome:
    """``RegexTableLinker.link`` 결과 — 엣지 + 미해결 + 검증용 매치 통계."""
    edges: list[ReadsWrites] = field(default_factory=list)
    unresolved: list[dict] = field(default_factory=list)
    matched_sites: int = 0              # entity 를 추출한 총 매치 수
    token_recognized: int = 0           # 그중 op_token 이 op_mappings 에 인식된 수
    samples: list[MatchSample] = field(default_factory=list)   # 앞 N건 (피드백용)


@dataclass
class PatternVerdict:
    """``table_access_patterns`` 검증 판정 — 쓰기 전 작동 확인.

    수치 상세(매치/엣지/미해결/인식률)는 ``summary`` 한 줄에 담는다 — 소비자
    (scan_agent · residual_pattern_loop)는 ok · resolved_ratio · summary 만 읽는다.
    """
    ok: bool
    resolved_ratio: float               # edges / matched_sites — 검증 실패 시 점수 환산용
    summary: str                        # 사람·LLM 이 읽을 한 줄 요약


class RegexTableLinker:
    """결정론 정규식 grep → ``ReadsWrites`` 엣지 + ``unresolved`` + 검증 통계."""

    async def link(
        self,
        project_root: Path,
        functions: list[Function],
        tables: list[Table],
        patterns: list[TableAccessPattern],
        ddl_transform: DdlTransform,
        restrict_to_files: Optional[set[str]] = None,
    ) -> LinkOutcome:
        """프로젝트 소스 → ``LinkOutcome`` (엣지 · 미해결 · 매치 통계).

        ``restrict_to_files`` 가 주어지면 전체 rglob 대신 그 파일들만 스캔한다 —
        잔여 패턴 루프가 새 패턴을 잔여 케이스 파일에만 재적용할 때 (전체 재스캔 회피).
        """
        if not patterns:
            return LinkOutcome()

        compiled = _compile_patterns(patterns)
        if not compiled:
            return LinkOutcome()

        # 함수 위치 인덱스 (file:line → function_id) + 테이블 이름 lookup.
        locator = FunctionLocator(functions)
        table_lookup = _build_table_lookup(tables)

        outcome = LinkOutcome()

        # 파일 1회만 read — 모든 패턴이 같은 파일에서 finditer.
        # restrict_to_files 가 있으면 그 파일만, 없으면 프로젝트 전체.
        src_files = (
            [Path(f) for f in sorted(restrict_to_files)]
            if restrict_to_files is not None
            else sorted(Path(project_root).rglob("*"))
        )
        for src_file in src_files:
            if not src_file.is_file():
                continue
            try:
                text = src_file.read_text(encoding="utf-8", errors="ignore")
            except (OSError, UnicodeDecodeError):
                continue

            for pat, regex, op_lookup in compiled:
                for m in regex.finditer(text):
                    groups = m.groupdict()
                    entity = groups.get("entity", "")
                    if not entity:
                        continue
                    op_token = groups.get("op_token", "")
                    line = text[: m.start()].count("\n") + 1

                    token_known = op_token in op_lookup
                    table_fqn = _resolve_table(entity, table_lookup, ddl_transform)
                    caller_id = locator.locate(str(src_file), line)

                    outcome.matched_sites += 1
                    if token_known:
                        outcome.token_recognized += 1
                    if len(outcome.samples) < _SAMPLE_CAP:
                        outcome.samples.append(MatchSample(
                            entity=entity, op_token=op_token,
                            token_recognized=token_known, resolved_table=table_fqn,
                        ))

                    if table_fqn is None or caller_id is None:
                        outcome.unresolved.append({
                            "case_id": f"{pat.id}:{src_file}:{line}",
                            "file": str(src_file),
                            "line": line,
                            "call_text": m.group(0),
                            "function_id": caller_id or "",
                            "attempted_patterns": [{
                                "pattern_id": pat.id,
                                "regex": pat.call_regex,
                                "result": "matched",
                                "groups": dict(groups),
                                "ddl_lookup_result": (
                                    "not_found" if table_fqn is None
                                    else f"found:{table_fqn}"
                                ),
                            }],
                        })
                        continue

                    # token → 표준 SQL op (SELECT/INSERT/UPDATE/DELETE) → READS/WRITES 자동 매핑.
                    # token 미인식 시 보수적으로 READS (틀린 정규식은 verify 단계에서 탈락).
                    standard_op = op_lookup.get(op_token)
                    relation = _op_to_relation(standard_op) if standard_op else "READS"
                    outcome.edges.append(ReadsWrites(
                        function_id=caller_id,
                        table_fqn=table_fqn,
                        relation=relation,
                        dml_type=derive_dml_type(standard_op),
                        evidence_file=str(src_file),
                        evidence_line=line,
                    ))
        return outcome


async def verify_table_patterns(
    patterns: list[TableAccessPattern],
    ddl_transform: DdlTransform,
    project_root: Path,
    functions: list[Function],
    tables: list[Table],
    restrict_to_files: Optional[set[str]] = None,
) -> PatternVerdict:
    """정규식을 실제 소스에 적용 → 작동 여부 판정 (``RegexTableLinker`` 재사용).

    Scan Agent 가 채택한 정규식 · 잔여 패턴 루프가 발견한 정규식 모두 쓰기 전
    반드시 이 검증을 통과해야 한다. ``restrict_to_files`` 로 검증 범위를 좁힐 수
    있다 (잔여 루프 — 잔여 케이스 파일만 검증, 전체 재스캔 회피).

    ``ok`` 판정 = 매치 ≥ 1 · 엣지 ≥ 1 · op_token 인식률 ≥ 임계
    (``ROBO_AGENT_SCAN_REGEX_MIN_OP_RATIO``).
    틀린 정규식 — entity 가 SQL_KEY 통째(``zngm_comm_cd_dtl_s0024``), op_token 이 C
    변수명 — 은 엣지 0 · token 인식 0 → ``ok=False`` 로 걸린다.
    """
    outcome = await RegexTableLinker().link(
        project_root=project_root,
        functions=functions,
        tables=tables,
        patterns=patterns,
        ddl_transform=ddl_transform,
        restrict_to_files=restrict_to_files,
    )
    matched = outcome.matched_sites
    edges = len(outcome.edges)
    resolved_ratio = edges / matched if matched else 0.0
    token_ratio = outcome.token_recognized / matched if matched else 0.0
    ok = (
        matched > 0
        and edges > 0
        and token_ratio >= ENV.agent.scan_regex_min_op_ratio
    )
    summary = (
        f"매치 {matched}건 · R/W엣지 {edges}건 · 미해결 {len(outcome.unresolved)}건 · "
        f"token인식 {token_ratio:.0%} · 테이블해소 {resolved_ratio:.0%}"
    )
    return PatternVerdict(ok=ok, resolved_ratio=resolved_ratio, summary=summary)


# ── 헬퍼 ──────────────────────────────────────────────────


def _op_to_relation(op: str) -> Literal["READS", "WRITES"]:
    """표준 SQL op → 그래프 관계. SELECT=READS, INSERT/UPDATE/DELETE=WRITES.

    LLM 은 표준 op 4종만 결정, READS/WRITES 매핑은 코드가 (환각 차단 + DRY).
    """
    return "READS" if op == "SELECT" else "WRITES"


def _compile_patterns(
    patterns: list[TableAccessPattern],
) -> list[tuple[TableAccessPattern, re.Pattern, dict[str, str]]]:
    """``call_regex`` 사전 컴파일 + ``op_mappings`` list → token→op dict lookup 변환.

    컴파일 실패 패턴은 ``log_warning`` 후 건너뜀 (조용한 실패 X).
    """
    compiled: list[tuple[TableAccessPattern, re.Pattern, dict[str, str]]] = []
    for pat in patterns:
        try:
            regex = re.compile(pat.call_regex)
        except re.error as e:
            log_warning(
                _LOG, "compile_pattern",
                "call_regex 컴파일 실패 — 이 패턴 건너뜀",
                pattern_id=pat.id, call_regex=pat.call_regex, error=str(e),
            )
            continue
        op_lookup = {m.token: m.op for m in pat.op_mappings}
        compiled.append((pat, regex, op_lookup))
    return compiled


class FunctionLocator:
    """``file_path`` + ``line`` → ``function_id`` 매핑."""

    def __init__(self, functions: list[Function]):
        # file_path → [(start, end, function_id)] sorted
        self.by_file: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
        for fn in functions:
            self.by_file[fn.file_path].append((fn.start_line, fn.end_line, fn.function_id))
        for k in self.by_file:
            self.by_file[k].sort()

    def locate(self, file_path: str, line: int) -> Optional[str]:
        candidates = self.by_file.get(file_path)
        if candidates is None:
            # 절대/상대 경로 차이 — suffix 매칭
            for k, v in self.by_file.items():
                if file_path.endswith(k) or k.endswith(file_path):
                    candidates = v
                    break
        if not candidates:
            return None
        for start, end, fid in candidates:
            if start <= line <= end:
                return fid
        return None


def _build_table_lookup(tables: list[Table]) -> dict[str, str]:
    """테이블 이름 다양한 변형 → fqn lookup."""
    lookup: dict[str, str] = {}
    for t in tables:
        fqn = t.fqn
        for key in (t.name, t.name.upper(), t.name.lower()):
            lookup.setdefault(key, fqn)
    return lookup


def _resolve_table(entity: str, lookup: dict[str, str], transform: DdlTransform) -> Optional[str]:
    """entity → fqn 변환 (``ddl_transform`` 규칙 적용)."""
    rule = getattr(transform, "ddl_naming_rule", "as_is")
    candidates = [entity, entity.upper(), entity.lower()]

    if rule == "camel_to_snake_upper":
        candidates.append(_camel_to_snake(entity).upper())
    elif rule == "snake_to_camel":
        candidates.append(_snake_to_camel(entity))

    # fallback prefix
    for prefix in getattr(transform, "fallback_prefixes", None) or []:
        candidates.extend([f"{prefix}{entity}", f"{prefix}{entity.upper()}"])

    for c in candidates:
        if c in lookup:
            return lookup[c]
    return None


def _camel_to_snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _snake_to_camel(name: str) -> str:
    parts = name.lower().split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])
