"""RegexTableLinker — ``table_access_patterns`` 의 regex 로 소스 grep → R/W 엣지.

흐름:
1. 정규식 매칭 → (caller_id, op, entity) 추출.
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

from agents.contracts import DdlTransform, TableAccessPattern
from graph.schema.graph_edges import ReadsWrites, derive_dml_type
from graph.schema.graph_nodes import CodeNode, Table
from shared.config.settings import SETTINGS
from shared.events.runtime_logger import log_warning

_LOG = "pipeline.steps.step5_linking.link_tables.regex_table_linker"

# samples 누적 상한 — 검증 피드백·로그용. LLM 컨텍스트 폭증 방지.
_SAMPLE_CAP = 25
_SQL_KEYWORD_RE = re.compile(r"\b(?:SELECT|INSERT|UPDATE|DELETE)\b", re.IGNORECASE)
_C_SQL_START_RE = re.compile(
    r"^\s*(?:WITH\b.*?\b)?(SELECT|INSERT|UPDATE|DELETE|MERGE)\b",
    re.IGNORECASE | re.DOTALL,
)
_C_SQL_WRITE_TARGET_RE = re.compile(
    r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM|MERGE\s+INTO)\s+"
    r"(?:[\w$]+\.)?(?P<entity>[A-Za-z_][\w$]*)",
    re.IGNORECASE,
)
_C_SQL_READ_TARGET_RE = re.compile(
    r"\b(?:FROM|JOIN)\s+(?:[\w$]+\.)?(?P<entity>[A-Za-z_][\w$]*)",
    re.IGNORECASE,
)


@dataclass
class MatchSample:
    """정규식 매치 1건의 추출 결과 — 검증 피드백용 (정규식이 맞는지 눈으로 확인)."""
    entity: str
    op_token: str                       # named group <op_token> raw 매치 (예: 's', 'findOne', 'SELECT')
    entity_complete: bool               # 소스의 전체 식별자 토큰을 캡처했나 (prefix 부분매치 방지)
    token_recognized: bool              # token 이 op_mappings 에 있나 (→ 표준 op 변환 가능)
    resolved_table: Optional[str]       # entity → DDL fqn (None = 미해소)


@dataclass
class LinkOutcome:
    """``RegexTableLinker.link`` 결과 — 엣지 + 미해결 + 검증용 매치 통계."""
    edges: list[ReadsWrites] = field(default_factory=list)
    unresolved: list[dict] = field(default_factory=list)
    matched_sites: int = 0              # entity 를 추출한 총 매치 수
    partial_entities: int = 0            # entity 앞뒤에 식별자 문자가 남은 부분 추출 수
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


@dataclass(frozen=True)
class _SourceView:
    """Regex input plus logical-index → raw-source-offset mapping."""

    text: str
    offsets: tuple[int, ...] | None = None

    def raw_offset(self, logical_offset: int) -> int:
        return logical_offset if self.offsets is None else self.offsets[logical_offset]


@dataclass(frozen=True)
class _CStringToken:
    """One ordinary C/C++ string token and its raw source bounds."""

    start: int
    quote_start: int
    content_start: int
    content_end: int
    end: int


def _c_adjacent_string_views(text: str) -> list[_SourceView]:
    """Return compiler-like views for adjacent C string literal groups.

    C concatenates string tokens separated only by whitespace or comments.  The
    returned offset map keeps diagnostics attached to the original token that
    contributed each logical character.
    """
    tokens = _c_string_tokens(text)
    views: list[_SourceView] = []
    index = 0
    while index < len(tokens):
        group = [tokens[index]]
        cursor = index + 1
        while cursor < len(tokens) and _c_gap_is_trivia(
            text[group[-1].end:tokens[cursor].start]
        ):
            group.append(tokens[cursor])
            cursor += 1

        if len(group) > 1:
            logical = ['"']
            offsets = [group[0].quote_start]
            for token in group:
                logical.append(text[token.content_start:token.content_end])
                offsets.extend(range(token.content_start, token.content_end))
            logical.append('"')
            offsets.append(group[-1].end - 1)
            views.append(_SourceView("".join(logical), tuple(offsets)))

        index = cursor
    return views


def _c_nested_sql_views(text: str) -> list[_SourceView]:
    """Expose nested DML clauses from C strings whose first token is SQL.

    A pattern starting at the opening quote sees the outer statement only. A
    virtual quote-prefixed suffix lets that same verified pattern also inspect
    a nested clause while retaining raw source offsets.
    """
    views: list[_SourceView] = []
    for group in _c_literal_groups(text):
        content = "".join(text[token.content_start:token.content_end] for token in group)
        offsets = tuple(
            offset
            for token in group
            for offset in range(token.content_start, token.content_end)
        )
        if not content or not offsets:
            continue
        matches = list(_SQL_KEYWORD_RE.finditer(content))
        first_nonspace = len(content) - len(content.lstrip())
        if not matches or matches[0].start() != first_nonspace:
            continue
        for match in matches[1:]:
            start = match.start()
            views.append(_SourceView(
                '"' + content[start:] + '"',
                (offsets[start], *offsets[start:], offsets[-1]),
            ))
    return views


def _c_literal_groups(text: str) -> list[list[_CStringToken]]:
    """Group C string tokens exactly where the compiler concatenates them."""
    tokens = _c_string_tokens(text)
    groups: list[list[_CStringToken]] = []
    index = 0
    while index < len(tokens):
        group = [tokens[index]]
        cursor = index + 1
        while cursor < len(tokens) and _c_gap_is_trivia(
            text[group[-1].end:tokens[cursor].start]
        ):
            group.append(tokens[cursor])
            cursor += 1
        groups.append(group)
        index = cursor
    return groups


def _c_sql_semantic_edges(
    text: str,
    src_file: Path,
    locator: "CodeLocator",
    table_lookup: dict[str, str],
    ddl_transform: DdlTransform,
) -> list[ReadsWrites]:
    """Extract C literal SQL with statement-aware read/write semantics.

    Scan-generated patterns remain the primary project-specific matcher. This
    deterministic pass closes syntax holes a generic ``FROM`` pattern cannot
    distinguish: ``DELETE FROM`` is a write target, while
    ``UPDATE table alias SET`` is still an update target. Nested FROM/JOIN
    targets remain reads.
    """
    edges: list[ReadsWrites] = []
    seen: set[tuple[str, str, str]] = set()
    for group in _c_literal_groups(text):
        content = "".join(text[token.content_start:token.content_end] for token in group)
        offsets = tuple(
            offset
            for token in group
            for offset in range(token.content_start, token.content_end)
        )
        start_match = _C_SQL_START_RE.search(content)
        if start_match is None or not offsets:
            continue
        outer_op = start_match.group(1).upper()
        write_matches = list(_C_SQL_WRITE_TARGET_RE.finditer(content))
        write_entity_spans = {match.span("entity") for match in write_matches}
        accesses = [(match, "WRITES", outer_op) for match in write_matches]
        accesses.extend(
            (match, "READS", "SELECT")
            for match in _C_SQL_READ_TARGET_RE.finditer(content)
            if match.span("entity") not in write_entity_spans
        )
        for match, relation, dml_type in accesses:
            logical_offset = match.start("entity")
            raw_offset = offsets[logical_offset]
            line = text[:raw_offset].count("\n") + 1
            caller_id = locator.locate(str(src_file), line)
            table_fqn = _resolve_table(
                match.group("entity"), table_lookup, ddl_transform,
            )
            if caller_id is None or table_fqn is None:
                continue
            key = (caller_id, table_fqn, relation)
            if key in seen:
                continue
            seen.add(key)
            edges.append(ReadsWrites(
                source_id=caller_id,
                target_id=table_fqn,
                relation=relation,
                dml_type=derive_dml_type(dml_type),
                evidence_file=str(src_file),
                evidence_line=line,
            ))
    return edges


def _c_string_tokens(text: str) -> list[_CStringToken]:
    """Lex ordinary C/C++ string tokens while ignoring comments and chars."""
    tokens: list[_CStringToken] = []
    index = 0
    length = len(text)
    while index < length:
        if text.startswith("//", index):
            newline = text.find("\n", index + 2)
            index = length if newline < 0 else newline + 1
            continue
        if text.startswith("/*", index):
            close = text.find("*/", index + 2)
            index = length if close < 0 else close + 2
            continue
        if text[index] == "'":
            index = _c_quoted_token_end(text, index, "'")
            continue

        prefix_length = _c_string_prefix_length(text, index)
        if prefix_length is None:
            index += 1
            continue

        quote_start = index + prefix_length
        end = _c_quoted_token_end(text, quote_start, '"')
        if end <= length and end > quote_start and text[end - 1] == '"':
            tokens.append(_CStringToken(
                start=index,
                quote_start=quote_start,
                content_start=quote_start + 1,
                content_end=end - 1,
                end=end,
            ))
        index = end
    return tokens


def _c_string_prefix_length(text: str, index: int) -> int | None:
    """Return the encoding-prefix length when a string token starts at index."""
    for prefix in ('u8"', 'u"', 'U"', 'L"', '"'):
        if text.startswith(prefix, index):
            if prefix != '"' and index > 0 and (
                text[index - 1].isalnum() or text[index - 1] == "_"
            ):
                return None
            return len(prefix) - 1
    return None


def _c_quoted_token_end(text: str, quote_start: int, quote: str) -> int:
    """Return the exclusive end of a quoted token, tolerating malformed EOF."""
    index = quote_start + 1
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == quote:
            return index + 1
        index += 1
    return len(text)


def _c_gap_is_trivia(gap: str) -> bool:
    """Whether a gap contains only C whitespace and comments."""
    index = 0
    while index < len(gap):
        if gap[index].isspace():
            index += 1
        elif gap.startswith("//", index):
            newline = gap.find("\n", index + 2)
            index = len(gap) if newline < 0 else newline + 1
        elif gap.startswith("/*", index):
            close = gap.find("*/", index + 2)
            if close < 0:
                return False
            index = close + 2
        else:
            return False
    return True


class RegexTableLinker:
    """결정론 정규식 grep → ``ReadsWrites`` 엣지 + ``unresolved`` + 검증 통계."""

    async def link(
        self,
        project_root: Path,
        code_nodes: list[CodeNode],
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

        # 코드노드 위치 인덱스 (file:line → code_id) + 테이블 이름 lookup.
        locator = CodeLocator(code_nodes)
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
            except (OSError, UnicodeDecodeError) as e:
                # 조용히 건너뛰면 이 파일의 테이블 R/W 엣지가 통째로 0건이 되는데
                # 아무도 그 사실을 모른다 (헌법 IV / spec 045 FR-006).
                log_warning(
                    _LOG, "source_read_failed",
                    f"파일 read 실패 — 이 파일의 테이블 R/W 링킹이 통째로 누락된다: {e!r}",
                    file_path=str(src_file),
                )
                continue

            views = [_SourceView(text)]
            is_c_family = src_file.suffix.lower() in {
                ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp",
            }
            if is_c_family:
                views.extend(_c_adjacent_string_views(text))
                views.extend(_c_nested_sql_views(text))
            seen_sites: set[tuple[str, int, str, str]] = set()
            for pat, regex, op_lookup in compiled:
                for view in views:
                    for m in regex.finditer(view.text):
                        groups = m.groupdict()
                        entity = groups.get("entity", "")
                        if not entity:
                            continue
                        op_token = groups.get("op_token", "")
                        entity_offset = view.raw_offset(m.start("entity"))
                        site = (pat.id, entity_offset, entity, op_token)
                        if site in seen_sites:
                            continue
                        seen_sites.add(site)
                        line = text[:entity_offset].count("\n") + 1

                        entity_complete = _entity_group_complete(view.text, m)
                        token_known = op_token in op_lookup
                        table_fqn = (
                            _resolve_table(entity, table_lookup, ddl_transform)
                            if entity_complete else None
                        )
                        caller_id = locator.locate(str(src_file), line)

                        outcome.matched_sites += 1
                        if not entity_complete:
                            outcome.partial_entities += 1
                        if token_known:
                            outcome.token_recognized += 1
                        if len(outcome.samples) < _SAMPLE_CAP:
                            outcome.samples.append(MatchSample(
                                entity=entity, op_token=op_token,
                                entity_complete=entity_complete,
                                token_recognized=token_known, resolved_table=table_fqn,
                            ))

                        if table_fqn is None or caller_id is None:
                            outcome.unresolved.append({
                                "case_id": f"{pat.id}:{src_file}:{line}",
                                "file": str(src_file),
                                "line": line,
                                "call_text": m.group(0),
                                "source_id": caller_id or "",
                                "attempted_patterns": [{
                                    "pattern_id": pat.id,
                                    "regex": pat.call_regex,
                                    "result": "matched",
                                    "groups": dict(groups),
                                    "ddl_lookup_result": (
                                        "partial_entity" if not entity_complete
                                        else "not_found" if table_fqn is None
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
                            source_id=caller_id,
                            target_id=table_fqn,
                            relation=relation,
                            dml_type=derive_dml_type(standard_op),
                            evidence_file=str(src_file),
                            evidence_line=line,
                        ))

            if is_c_family:
                semantic_edges = _c_sql_semantic_edges(
                    text, src_file, locator, table_lookup, ddl_transform,
                )
                semantic_relations = {
                    (edge.source_id, edge.target_id, edge.relation)
                    for edge in semantic_edges
                }
                semantic_pairs = {
                    (edge.source_id, edge.target_id)
                    for edge in semantic_edges
                }
                # A generic FROM pattern also matches DELETE FROM. For C
                # literal SQL, the statement-aware result is authoritative
                # for every routine/table pair it observed.
                outcome.edges = [
                    edge for edge in outcome.edges
                    if (edge.source_id, edge.target_id) not in semantic_pairs
                    or (edge.source_id, edge.target_id, edge.relation) in semantic_relations
                ]
                existing = {
                    (edge.source_id, edge.target_id, edge.relation)
                    for edge in outcome.edges
                }
                outcome.edges.extend(
                    edge for edge in semantic_edges
                    if (edge.source_id, edge.target_id, edge.relation) not in existing
                )
        return outcome


async def verify_table_patterns(
    patterns: list[TableAccessPattern],
    ddl_transform: DdlTransform,
    project_root: Path,
    code_nodes: list[CodeNode],
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
        code_nodes=code_nodes,
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
        and outcome.partial_entities == 0
        and token_ratio >= SETTINGS.analysis_agents.scan_regex_min_op_ratio
    )
    summary = (
        f"매치 {matched}건 · R/W엣지 {edges}건 · 미해결 {len(outcome.unresolved)}건 · "
        f"부분추출 {outcome.partial_entities}건 · "
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


class CodeLocator:
    """``file_path`` + ``line`` → ``code_id`` 매핑."""

    def __init__(self, code_nodes: list[CodeNode]):
        # file_path → [(start, end, code_id)] sorted
        self.by_file: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
        for fn in code_nodes:
            self.by_file[fn.file_path].append((fn.start_line, fn.end_line, fn.id))
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
    """실제 DDL 테이블 이름 → fqn. 이름 변형은 만들지 않는다."""
    return {t.name: t.id for t in tables}


def _resolve_table(entity: str, lookup: dict[str, str], transform: DdlTransform) -> Optional[str]:
    """entity → fqn 변환 (``ddl_transform`` 규칙 적용)."""
    rule = getattr(transform, "ddl_naming_rule", "as_is")
    candidates = [entity]

    if rule == "camel_to_snake_upper":
        candidates = [_camel_to_snake(entity).upper()]
    elif rule == "snake_to_camel":
        candidates = [_snake_to_camel(entity)]

    # fallback prefix
    for prefix in getattr(transform, "fallback_prefixes", None) or []:
        candidates.append(f"{prefix}{entity}")

    for c in candidates:
        if c in lookup:
            return lookup[c]
    return None


def _entity_group_complete(text: str, match: re.Match) -> bool:
    """entity 그룹이 실제 식별자 전체인지 확인한다.

    `_`, quoted-name의 `-`, schema 구분 `.` 등이 그룹 직전·직후에 남으면 DDL에 우연히
    존재하는 짧은 접두 이름으로 잘못 해소될 수 있으므로 부분 추출로 거부한다.
    """
    start, end = match.span("entity")

    def continues(ch: str) -> bool:
        return ch.isalnum() or ch in "_.$-"

    return not (
        (start > 0 and continues(text[start - 1]))
        or (end < len(text) and continues(text[end]))
    )


def _camel_to_snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _snake_to_camel(name: str) -> str:
    parts = name.lower().split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])
