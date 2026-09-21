"""RegexTableLinker — ``table_access_patterns`` 의 regex 로 소스 grep → R/W 엣지.

흐름:
1. 정규식 매칭 → (caller_id, op, entity) 추출.
2. ``ddl_transform`` 적용 → entity → DDL 테이블 이름 변환.
3. DDL 테이블 lookup 성공 → ``ReadsWrites`` 엣지.
4. lookup 실패 → ``unresolved`` 케이스 (Linking Agent 가 추적).

LLM 0회. 결정론.

**검증도 같은 엔진** — ``verify_table_patterns`` 는 이 링커의 같은 pass에서 전체와
규칙별 지표를 함께 판정한다. "검증된 정규식 == 링킹이 실제로 작동하는 정규식" 을 코드
한 곳으로 보장하며, 정상 규칙의 실적이 죽은 규칙을 가리지 못한다.

**성능**: 파일 1회만 read — 모든 패턴이 같은 파일에서 ``finditer`` (N*M → N+M*P).
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

from agents.agent_contracts import (
    DdlTransform,
    EmbeddedSqlRule,
    TableAccessExclusionPattern,
    TableAccessPattern,
)
from graph.schema.graph_edges import ReadsWrites
from graph.schema.graph_nodes import CodeNode, Table
from pipeline.dml_semantics import dml_semantics, dml_type_of
from pipeline.source_languages import ROUTINE_LABELS, lang_of, source_lexicon
from pipeline.checks.source_lexing import split_source_for_linking
from shared.files.run_cache_context import read_run_text

# DML 키워드·판정 정규식은 전부 의미표 파생.


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
    # 같은 소스 지점을 다른 패턴이 해소했는지 판별하는 내부 증거다.
    # pattern ID는 의도적으로 제외한다.
    resolved_sites: set[tuple[str, int, str]] = field(default_factory=set)
    matched_sites: int = 0              # entity 를 추출한 총 매치 수
    partial_entities: int = 0            # entity 앞뒤에 식별자 문자가 남은 부분 추출 수
    token_recognized: int = 0           # 그중 op_token 이 op_mappings 에 인식된 수
    unresolved_table_names: int = 0     # 완전한 entity지만 현재 업로드 DDL에는 없음
    ambiguous_sites: int = 0            # ambiguity: entity 가 단어장에서 복수 해석됨
    samples: list[MatchSample] = field(default_factory=list)   # 요청 시 수집하는 전체 검증 자료
    rule_stats: dict[str, "RuleStats"] = field(default_factory=dict)
    exclusion_stats: dict[str, "RuleStats"] = field(default_factory=dict)
    exclusion_conflicts: list["TableAccessExclusionConflict"] = field(default_factory=list)


@dataclass
class RuleStats:
    """한 정규식/embedded rule의 독립 검증 지표."""
    matched_sites: int = 0
    resolved_sites: int = 0
    partial_entities: int = 0
    token_recognized: int = 0
    unresolved_table_names: int = 0
    ambiguous_sites: int = 0


@dataclass
class PatternVerdict:
    """``table_access_patterns`` 검증 판정 — 쓰기 전 작동 확인.

    수치 상세(매치/엣지/미해결/인식률)는 ``summary`` 한 줄에 담는다 — 소비자
    (scan_agent · residual_pattern_loop)는 ok · resolved_ratio · summary 만 읽는다.
    """
    ok: bool
    resolved_ratio: float               # 현재 업로드 DDL에서 해소된 비율(진단값)
    summary: str                        # 사람·LLM 이 읽을 한 줄 요약
    invalid_access_rule_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class TableAccessExclusionConflict:
    access_rule_id: str
    exclusion_rule_id: str
    file: str
    line: int


class TableAccessExclusionConflictError(RuntimeError):
    """A TABLE rule claimed a physical source region declared non-TABLE."""

    def __init__(self, conflicts: list[TableAccessExclusionConflict]):
        self.conflicts = tuple(conflicts)
        detail = "; ".join(
            f"{item.access_rule_id}<->{item.exclusion_rule_id}@{item.file}:{item.line}"
            for item in conflicts[:20]
        )
        super().__init__("TABLE/non-TABLE source-shape conflict: " + detail)


@dataclass(frozen=True)
class _SourceView:
    """Regex input plus logical-index → raw-source-offset mapping."""

    text: str
    offsets: tuple[int, ...] | None = None

    def raw_offset(self, logical_offset: int) -> int:
        return logical_offset if self.offsets is None else self.offsets[logical_offset]


@dataclass(frozen=True)
class _PreparedLinkSource:
    """One fail-closed source read and its immutable language-aware views."""

    path: Path
    scan_text: str
    views: tuple[_SourceView, ...]
    adjacent_string_concat: bool


@dataclass(frozen=True)
class PreparedLinkSources:
    """Reusable source preparation for independent pattern-verification passes."""

    project_root: Path
    selected_files: tuple[str, ...]
    sources: tuple[_PreparedLinkSource, ...]


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
        matches = list(dml_semantics().detect_regex.finditer(content))
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
    seen: set[tuple[str, str, str, str, int]] = set()
    semantics = dml_semantics()
    for group in _c_literal_groups(text):
        content = "".join(text[token.content_start:token.content_end] for token in group)
        offsets = tuple(
            offset
            for token in group
            for offset in range(token.content_start, token.content_end)
        )
        if semantics.first_statement(content) is None or not offsets:
            continue
        # write 대상 → 그 구문의 dml_type (DBMS 경로와 동일 판정 —  SC-005).
        write_matches = list(semantics.iter_write_targets(content))
        write_entity_spans = {match.span("entity") for match, _ in write_matches}
        accesses = [
            (match, "WRITES", rule.dml_type) for match, rule in write_matches
        ]
        accesses.extend(
            (match, "READS", "SELECT")
            for match in semantics.read_target_regex.finditer(content)
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
            key = (caller_id, table_fqn, relation, str(src_file), line)
            if key in seen:
                continue
            seen.add(key)
            edges.append(ReadsWrites(
                source_id=caller_id,
                target_id=table_fqn,
                relation=relation,
                dml_type=dml_type_of(dml_type),
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


def _selected_source_files(
    code_nodes: list[CodeNode], restrict_to_files: Optional[set[str]],
) -> tuple[str, ...]:
    return tuple(
        sorted(restrict_to_files)
        if restrict_to_files is not None
        else sorted({node.file_path for node in code_nodes if node.file_path})
    )


def prepare_link_sources(
    project_root: Path,
    code_nodes: list[CodeNode],
    restrict_to_files: Optional[set[str]] = None,
) -> PreparedLinkSources:
    """Read and lex each CodeNode-owned file once for repeated verification.

    Source decoding, language lookup, live-source splitting, and C-family virtual
    views depend only on immutable run input.  Pattern compilation, exclusions,
    DDL resolution, and verdict calculation deliberately remain per verifier.
    """
    selected_files = _selected_source_files(code_nodes, restrict_to_files)
    root = Path(project_root).resolve()
    sources: list[_PreparedLinkSource] = []
    for file_path in selected_files:
        path = Path(file_path)
        src_file = path if path.is_absolute() else root / path
        try:
            text = read_run_text(src_file, encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise RuntimeError(
                f"R/W linking source read failed: {src_file}: {error}"
            ) from error

        scan_text = split_source_for_linking(
            text, lang_of(src_file.suffix)
        ).live()
        views = [_SourceView(scan_text)]
        adjacent_string_concat = _adjacent_string_concat(src_file.suffix)
        if adjacent_string_concat:
            views.extend(_c_adjacent_string_views(scan_text))
            views.extend(_c_nested_sql_views(scan_text))
        sources.append(_PreparedLinkSource(
            path=src_file,
            scan_text=scan_text,
            views=tuple(views),
            adjacent_string_concat=adjacent_string_concat,
        ))
    return PreparedLinkSources(
        project_root=root,
        selected_files=selected_files,
        sources=tuple(sources),
    )


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
        embedded_rules: tuple[EmbeddedSqlRule, ...] | list[EmbeddedSqlRule] = (),
        exclusion_patterns: tuple[TableAccessExclusionPattern, ...]
        | list[TableAccessExclusionPattern] = (),
        collect_exclusion_conflicts: bool = False,
        collect_samples: bool = False,
        prepared_sources: PreparedLinkSources | None = None,
    ) -> LinkOutcome:
        """프로젝트 소스 → ``LinkOutcome`` (엣지 · 미해결 · 매치 통계).

        ``restrict_to_files`` 가 주어지면 전체 rglob 대신 그 파일들만 스캔한다 —
        잔여 패턴 루프가 새 패턴을 잔여 케이스 파일에만 재적용할 때 (전체 재스캔 회피).
        ``embedded_rules`` 는 울타리 블록 SQL  — 블록을 추출해 DML 의미표로
        직접 판정한다 (정규식 패턴과 같은 LinkOutcome·검문소 지표 공유).
        """
        if not patterns and not embedded_rules and not exclusion_patterns:
            return LinkOutcome()

        rule_ids = [rule.id for rule in (*patterns, *embedded_rules)]
        exclusion_ids = [rule.id for rule in exclusion_patterns]
        all_rule_ids = [*rule_ids, *exclusion_ids]
        duplicate_ids = sorted(
            rule_id for rule_id, count in Counter(all_rule_ids).items() if count > 1
        )
        if duplicate_ids:
            raise ValueError(f"duplicate table access rule ids: {', '.join(duplicate_ids)}")

        compiled = _compile_patterns(patterns)
        compiled_exclusions = _compile_exclusion_patterns(exclusion_patterns)
        if not compiled and not embedded_rules and not compiled_exclusions:
            return LinkOutcome()

        # 코드노드 위치 인덱스 (file:line → code_id) + 테이블 이름 lookup.
        locator = CodeLocator(code_nodes)
        table_lookup = _build_table_lookup(tables)

        outcome = LinkOutcome(
            rule_stats={rule_id: RuleStats() for rule_id in rule_ids},
            exclusion_stats={rule_id: RuleStats() for rule_id in exclusion_ids},
        )

        selected_files = _selected_source_files(code_nodes, restrict_to_files)
        root = Path(project_root).resolve()
        if prepared_sources is None:
            prepared_sources = prepare_link_sources(
                root, code_nodes, restrict_to_files,
            )
        elif (
            prepared_sources.project_root != root
            or prepared_sources.selected_files != selected_files
        ):
            raise ValueError(
                "prepared R/W linking sources do not match project/file scope"
            )

        for prepared in prepared_sources.sources:
            src_file = prepared.path
            scan_text = prepared.scan_text
            views = prepared.views
            # 언어 특성은 어휘표(ontology_roles.yaml source_lexicon)가 단일 정의처 —
            # 하드코딩 확장자 나열 금지. 헌법 II의 감시 항목이다.
            is_c_family = prepared.adjacent_string_concat
            excluded_spans: list[tuple[int, int, str]] = []
            seen_exclusion_sites: set[tuple[str, int, int]] = set()
            for exclusion, regex in compiled_exclusions:
                stats = outcome.exclusion_stats[exclusion.id]
                for view in views:
                    for match in regex.finditer(view.text):
                        raw_start, raw_end = _raw_match_span(view, match)
                        site = (exclusion.id, raw_start, raw_end)
                        if site in seen_exclusion_sites:
                            continue
                        seen_exclusion_sites.add(site)
                        stats.matched_sites += 1
                        excluded_spans.append((raw_start, raw_end, exclusion.id))
            seen_sites: set[tuple[str, int, str, str]] = set()
            for pat, regex, op_lookup in compiled:
                stats = outcome.rule_stats[pat.id]
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
                        line = scan_text[:entity_offset].count("\n") + 1

                        raw_start, raw_end = _raw_match_span(view, m)
                        overlapping_exclusions = sorted({
                            exclusion_id
                            for excluded_start, excluded_end, exclusion_id in excluded_spans
                            if raw_start < excluded_end and excluded_start < raw_end
                        })

                        entity_complete = _entity_group_complete(view.text, m)
                        token_known = op_token in op_lookup
                        hits = (
                            _resolve_table_hits(entity, table_lookup, ddl_transform)
                            if entity_complete else []
                        )
                        table_fqn = hits[0] if hits else None
                        caller_id = locator.locate(str(src_file), line)

                        outcome.matched_sites += 1
                        stats.matched_sites += 1
                        if not entity_complete:
                            outcome.partial_entities += 1
                            stats.partial_entities += 1
                        if token_known:
                            outcome.token_recognized += 1
                            stats.token_recognized += 1
                        if overlapping_exclusions:
                            outcome.exclusion_conflicts.extend(
                                TableAccessExclusionConflict(
                                    access_rule_id=pat.id,
                                    exclusion_rule_id=exclusion_id,
                                    file=str(src_file),
                                    line=line,
                                )
                                for exclusion_id in overlapping_exclusions
                            )
                            continue
                        # 검문소 3지표  — 판정 재료만 계수, 엣지 동작은 불변.
                        if entity_complete and not hits:
                            outcome.unresolved_table_names += 1
                            stats.unresolved_table_names += 1
                        if len(set(hits)) > 1:
                            outcome.ambiguous_sites += 1
                            stats.ambiguous_sites += 1
                        if collect_samples:
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
                                "evidence_offset": entity_offset,
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

                        stats.resolved_sites += 1

                        # token → 표준 SQL op (SELECT/INSERT/UPDATE/DELETE) → READS/WRITES 자동 매핑.
                        # token 미인식 시 보수적으로 READS (틀린 정규식은 verify 단계에서 탈락).
                        standard_op = op_lookup.get(op_token)
                        relation = _op_to_relation(standard_op) if standard_op else "READS"
                        outcome.edges.append(ReadsWrites(
                            source_id=caller_id,
                            target_id=table_fqn,
                            relation=relation,
                            dml_type=dml_type_of(standard_op),
                            evidence_file=str(src_file),
                            evidence_line=line,
                        ))
                        outcome.resolved_sites.add(
                            (str(src_file), entity_offset, caller_id)
                        )

            if embedded_rules:
                _embedded_sql_edges(
                    scan_text, src_file, locator, table_lookup, ddl_transform,
                    embedded_rules, outcome,
                )

            if is_c_family and (patterns or embedded_rules):
                semantic_edges = _c_sql_semantic_edges(
                    scan_text, src_file, locator, table_lookup, ddl_transform,
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
                    (
                        edge.source_id,
                        edge.target_id,
                        edge.relation,
                        edge.evidence_file,
                        edge.evidence_line,
                    )
                    for edge in outcome.edges
                }
                outcome.edges.extend(
                    edge for edge in semantic_edges
                    if (
                        edge.source_id,
                        edge.target_id,
                        edge.relation,
                        edge.evidence_file,
                        edge.evidence_line,
                    )
                    not in existing
                )
        if outcome.exclusion_conflicts and not collect_exclusion_conflicts:
            raise TableAccessExclusionConflictError(outcome.exclusion_conflicts)
        return outcome


async def verify_table_patterns(
    patterns: list[TableAccessPattern],
    ddl_transform: DdlTransform,
    project_root: Path,
    code_nodes: list[CodeNode],
    tables: list[Table],
    restrict_to_files: Optional[set[str]] = None,
    embedded_rules: tuple[EmbeddedSqlRule, ...] | list[EmbeddedSqlRule] = (),
    exclusion_patterns: tuple[TableAccessExclusionPattern, ...]
    | list[TableAccessExclusionPattern] = (),
    prepared_sources: PreparedLinkSources | None = None,
    require_each_access_rule_match: bool = True,
    require_each_exclusion_rule_match: bool = True,
    require_local_table_resolution: bool = True,
) -> PatternVerdict:
    """정규식을 실제 소스에 적용 → 작동 여부 판정 (``RegexTableLinker`` 재사용).

    Scan Agent 가 채택한 정규식 · 잔여 패턴 루프가 발견한 정규식 모두 쓰기 전
    반드시 이 검증을 통과해야 한다. ``restrict_to_files`` 로 검증 범위를 좁힐 수
    있다 (잔여 루프 — 잔여 케이스 파일만 검증, 전체 재스캔 회피).

    모델이 새로 제안한 규칙은 기본적으로 각 규칙의 실제 매치와 로컬 DDL 해소를
    요구한다. 등록된 SKILL 계약을 partial upload에서 식별할 때는 세 ``require_*``
    인자를 끄고, 실제 source shape·op token·완전한 entity 추출·중의성만 검증한다.
    어느 경로에서도 TABLE/비TABLE exclusion 충돌은 허용하지 않는다.
    """
    outcome = await RegexTableLinker().link(
        project_root=project_root,
        code_nodes=code_nodes,
        tables=tables,
        patterns=patterns,
        ddl_transform=ddl_transform,
        restrict_to_files=restrict_to_files,
        embedded_rules=embedded_rules,
        exclusion_patterns=exclusion_patterns,
        collect_exclusion_conflicts=True,
        prepared_sources=prepared_sources,
    )
    matched = outcome.matched_sites
    edges = len(outcome.edges)
    resolved_ratio = edges / matched if matched else 0.0
    token_ratio = outcome.token_recognized / matched if matched else 0.0
    unresolved_table_ratio = outcome.unresolved_table_names / matched if matched else 0.0
    ambiguity_ratio = outcome.ambiguous_sites / matched if matched else 0.0
    has_access_rules = bool(patterns or embedded_rules)
    aggregate_ok = not has_access_rules or (
        matched > 0
        and outcome.partial_entities == 0
        and outcome.token_recognized == matched
        and outcome.ambiguous_sites == 0
        and (
            not require_local_table_resolution
            or (
                edges > 0
                and outcome.unresolved_table_names == 0
            )
        )
    )
    summary = (
        f"매치 {matched}건 · R/W엣지 {edges}건 · 미해결 {len(outcome.unresolved)}건 · "
        f"부분추출 {outcome.partial_entities}건 · "
        f"token인식 {token_ratio:.0%} · 로컬테이블해소 {resolved_ratio:.0%} · "
        f"DDL미해소 {unresolved_table_ratio:.0%} · 중의 {ambiguity_ratio:.0%}"
    )
    invalid_access_rule_ids = tuple(
        rule_id
        for rule_id, stats in outcome.rule_stats.items()
        if not _rule_stats_ok(
            stats,
            require_match=require_each_access_rule_match,
            require_local_table_resolution=require_local_table_resolution,
        )
    )
    if invalid_access_rule_ids:
        failed = "; ".join(
            f"{rule_id}({_rule_stats_summary(outcome.rule_stats[rule_id])})"
            for rule_id in invalid_access_rule_ids
        )
        summary += f" · 규칙별 실패 [{failed}]"
    unobserved_exclusion_rule_ids = tuple(
        rule_id
        for rule_id, stats in outcome.exclusion_stats.items()
        if require_each_exclusion_rule_match and stats.matched_sites == 0
    )
    if unobserved_exclusion_rule_ids:
        summary += " · 미관찰 제외규칙 [" + ", ".join(unobserved_exclusion_rule_ids) + "]"
    if outcome.exclusion_conflicts:
        conflicts = "; ".join(
            f"{item.access_rule_id}<->{item.exclusion_rule_id}@{item.file}:{item.line}"
            for item in outcome.exclusion_conflicts[:20]
        )
        summary += f" · TABLE/비TABLE 충돌 [{conflicts}]"
    return PatternVerdict(
        ok=(
            aggregate_ok
            and not invalid_access_rule_ids
            and not unobserved_exclusion_rule_ids
            and not outcome.exclusion_conflicts
        ),
        resolved_ratio=resolved_ratio,
        summary=summary,
        invalid_access_rule_ids=invalid_access_rule_ids,
    )


def _rule_stats_ok(
    stats: RuleStats,
    *,
    require_match: bool,
    require_local_table_resolution: bool,
) -> bool:
    """집계에 기대지 않고 한 규칙 자체가 검문소를 통과하는지 판정한다."""
    matched = stats.matched_sites
    if matched == 0:
        return not require_match
    token_ratio = stats.token_recognized / matched if matched else 0.0
    unresolved_table_ratio = stats.unresolved_table_names / matched if matched else 0.0
    ambiguity_ratio = stats.ambiguous_sites / matched if matched else 0.0
    return (
        stats.partial_entities == 0
        and stats.token_recognized == matched
        and stats.ambiguous_sites == 0
        and (
            not require_local_table_resolution
            or (
                stats.resolved_sites > 0
                and stats.unresolved_table_names == 0
            )
        )
    )


def _rule_stats_summary(stats: RuleStats) -> str:
    matched = stats.matched_sites
    token_ratio = stats.token_recognized / matched if matched else 0.0
    unresolved_table_ratio = stats.unresolved_table_names / matched if matched else 0.0
    ambiguity_ratio = stats.ambiguous_sites / matched if matched else 0.0
    return (
        f"매치 {matched}, 해소 {stats.resolved_sites}, 부분 {stats.partial_entities}, "
        f"token {token_ratio:.0%}, DDL미해소 {unresolved_table_ratio:.0%}, "
        f"중의 {ambiguity_ratio:.0%}"
    )


# ── 헬퍼 ──────────────────────────────────────────────────


def _adjacent_string_concat(suffix: str) -> bool:
    """이 확장자의 언어가 인접 문자열 리터럴을 이어붙이는가 — 어휘표 플래그.

    미등록 확장자·어휘표는 상위 R/W 링킹을 실패시켜 ontology 누락을 드러낸다.
    """
    return bool(source_lexicon(lang_of(suffix)).get("adjacent_string_concat"))


def _embedded_sql_edges(
    scan_text: str,
    src_file: Path,
    locator: "CodeLocator",
    table_lookup: dict[str, str],
    ddl_transform: DdlTransform,
    rules,
    outcome: LinkOutcome,
) -> None:
    """울타리 블록 SQL → 엣지. 판정은 전부 DML 의미표 — R/W 로직 중복 0.

    블록마다: 첫 토큰이 DML 인지 의미표로 확인 → write 대상(그 구문의 dml_type) +
    read 절(FROM/JOIN/USING) 추출 → 단어장 해소. 검문소 3지표를 패턴과
    동일하게 계수한다. 잔여 큐(unresolved_cases)는 정규식 패턴 영역 — 여기선 계수만.
    """
    semantics = dml_semantics()
    for rule in rules:
        stats = outcome.rule_stats[rule.id]
        block_re = re.compile(
            re.escape(rule.delimiter_start.strip())
            + r"(.*?)"
            + re.escape(rule.delimiter_end.strip()),
            re.IGNORECASE | re.DOTALL,
        )
        for block in block_re.finditer(scan_text):
            content = block.group(1)
            base = block.start(1)
            if semantics.first_statement(content) is None:
                continue                     # SQL 블록 아님 (DECLARE 등 비 DML)
            write_matches = list(semantics.iter_write_targets(content))
            write_spans = {m.span("entity") for m, _ in write_matches}
            accesses = [(m, "WRITES", rule_stmt.dml_type) for m, rule_stmt in write_matches]
            accesses.extend(
                (m, "READS", "SELECT")
                for m in semantics.read_target_regex.finditer(content)
                if m.span("entity") not in write_spans
            )
            for m, relation, dml_type in accesses:
                entity = m.group("entity")
                raw_offset = base + m.start("entity")
                line = scan_text[:raw_offset].count("\n") + 1
                hits = _resolve_table_hits(entity, table_lookup, ddl_transform)
                caller_id = locator.locate(str(src_file), line)

                outcome.matched_sites += 1
                outcome.token_recognized += 1   # 구문 키워드 = 항상 인식 (의미표 유래)
                stats.matched_sites += 1
                stats.token_recognized += 1
                if not hits:
                    outcome.unresolved_table_names += 1
                    stats.unresolved_table_names += 1
                    continue
                if len(set(hits)) > 1:
                    outcome.ambiguous_sites += 1
                    stats.ambiguous_sites += 1
                if caller_id is None:
                    continue
                stats.resolved_sites += 1
                key = (str(src_file), raw_offset, caller_id)
                if key in outcome.resolved_sites:
                    continue
                outcome.resolved_sites.add(key)
                outcome.edges.append(ReadsWrites(
                    source_id=caller_id,
                    target_id=hits[0],
                    relation=relation,
                    dml_type=dml_type,
                    evidence_file=str(src_file),
                    evidence_line=line,
                ))


def _op_to_relation(op: str) -> Literal["READS", "WRITES"]:
    """표준 SQL op → 그래프 관계. SELECT=READS, INSERT/UPDATE/DELETE=WRITES.

    LLM 은 표준 op 4종만 결정, READS/WRITES 매핑은 코드가 (환각 차단 + DRY).
    """
    return "READS" if op == "SELECT" else "WRITES"


def _compile_patterns(
    patterns: list[TableAccessPattern],
) -> list[tuple[TableAccessPattern, re.Pattern, dict[str, str]]]:
    """``call_regex`` 사전 컴파일 + ``op_mappings`` list → token→op dict lookup 변환."""
    compiled: list[tuple[TableAccessPattern, re.Pattern, dict[str, str]]] = []
    for pat in patterns:
        try:
            regex = re.compile(pat.call_regex)
        except re.error as e:
            raise ValueError(
                f"invalid table access pattern {pat.id!r}: {e}"
            ) from e
        op_lookup = {m.token: m.op for m in pat.op_mappings}
        compiled.append((pat, regex, op_lookup))
    return compiled


def _compile_exclusion_patterns(
    patterns: tuple[TableAccessExclusionPattern, ...]
    | list[TableAccessExclusionPattern],
) -> list[tuple[TableAccessExclusionPattern, re.Pattern]]:
    compiled: list[tuple[TableAccessExclusionPattern, re.Pattern]] = []
    for pattern in patterns:
        try:
            regex = re.compile(pattern.call_regex)
        except re.error as error:
            raise ValueError(
                f"invalid table access exclusion pattern {pattern.id!r}: {error}"
            ) from error
        missing = {"entity", "op_token"} - set(regex.groupindex)
        if missing:
            raise ValueError(
                f"table access exclusion pattern {pattern.id!r} missing groups: "
                + ", ".join(sorted(missing))
            )
        compiled.append((pattern, regex))
    return compiled


def _raw_match_span(view: _SourceView, match: re.Match) -> tuple[int, int]:
    """Map a logical regex match to one enclosing raw-source interval."""
    start = view.raw_offset(match.start())
    end = view.raw_offset(match.end() - 1) + 1
    return (min(start, end), max(start, end))


class CodeLocator:
    """``file_path`` + ``line`` → owning routine id mapping.

    READS/WRITES의 주체는 CALLS와 동일하게 호출 가능한 routine이다. IF/ELSE/CASE 같은
    구조 노드는 분기 RULE의 근거이지 R/W edge의 source가 아니다.
    """

    def __init__(self, code_nodes: list[CodeNode]):
        # file_path → [(start, end, code_id)] sorted
        self.by_file: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
        for fn in code_nodes:
            if fn.label not in ROUTINE_LABELS:
                continue
            self.by_file[fn.file_path].append((fn.start_line, fn.end_line, fn.id))
        for k in self.by_file:
            self.by_file[k].sort()

    def locate(self, file_path: str, line: int) -> Optional[str]:
        candidates = self.by_file.get(file_path)
        if candidates is None:
            # 절대/상대 경로 차이는 경로 구성요소 suffix로만 해소한다. 문자열
            # suffix의 첫 후보를 고르면 a/x.c와 ba/x.c 같은 경계 오탐이나 중복
            # basename을 임의 귀속하게 된다.
            source_parts = _path_parts(file_path)
            matched_paths = [
                path for path in self.by_file
                if _is_component_suffix(source_parts, _path_parts(path))
            ]
            if len(matched_paths) > 1:
                raise ValueError(
                    "ambiguous CodeNode file ownership: source path matched "
                    f"{len(matched_paths)} graph paths"
                )
            if matched_paths:
                candidates = self.by_file[matched_paths[0]]
        if not candidates:
            return None
        owners = [item for item in candidates if item[0] <= line <= item[1]]
        if not owners:
            return None
        # 중첩 함수/메서드는 가장 좁은 소스 범위가 실제 소유자다. 같은 범위의
        # 서로 다른 노드는 근거 없이 고르지 않고 실패시킨다.
        narrowest_span = min(end - start for start, end, _ in owners)
        narrowest_ids = {
            fid for start, end, fid in owners if end - start == narrowest_span
        }
        if len(narrowest_ids) != 1:
            raise ValueError(
                "ambiguous CodeNode line ownership: equally specific graph nodes"
            )
        return next(iter(narrowest_ids))


def _path_parts(path: str) -> tuple[str, ...]:
    return tuple(part for part in path.replace("\\", "/").split("/") if part and part != ".")


def _is_component_suffix(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    if not left or not right:
        return False
    shorter = min(len(left), len(right))
    return left[-shorter:] == right[-shorter:]


def _build_table_lookup(tables: list[Table]) -> dict[str, str]:
    """실제 DDL 테이블 이름 → fqn. 이름 변형은 만들지 않는다."""
    return {t.name: t.id for t in tables}


def _resolve_table_hits(
    entity: str, lookup: dict[str, str], transform: DdlTransform,
) -> list[str]:
    """entity 의 단어장 해석 **전부** (``ddl_transform`` 규칙 적용, 순서 보존).

    복수 해석(fallback prefix 로 T_X 와 TB_X 둘 다 존재 등)은 검문소 ambiguity
    지표의 재료다. 엣지 생성은 종전대로 첫 해석을 쓴다 — 판정만 강화.
    """
    rule = getattr(transform, "ddl_naming_rule", "as_is")
    # DDL lookup is keyed by the physical table name while source SQL may use
    # catalog/schema qualification.  Preserve the exact captured entity for
    # evidence, but resolve its terminal component against that lookup.
    terminal_entity = entity.rsplit(".", 1)[-1]
    candidates = [entity]
    if terminal_entity != entity:
        candidates.append(terminal_entity)

    if rule == "camel_to_snake_upper":
        candidates = [_camel_to_snake(entity).upper()]
        if terminal_entity != entity:
            candidates.append(_camel_to_snake(terminal_entity).upper())
    elif rule == "snake_to_camel":
        candidates = [_snake_to_camel(entity)]
        if terminal_entity != entity:
            candidates.append(_snake_to_camel(terminal_entity))

    # fallback prefix
    for prefix in getattr(transform, "fallback_prefixes", None) or []:
        candidates.append(f"{prefix}{entity}")

    return [lookup[c] for c in candidates if c in lookup]


def _resolve_table(entity: str, lookup: dict[str, str], transform: DdlTransform) -> Optional[str]:
    """entity → fqn 변환 — 첫 해석 (C 경로 소비용, 동작 불변)."""
    hits = _resolve_table_hits(entity, lookup, transform)
    return hits[0] if hits else None


def _entity_group_complete(text: str, match: re.Match) -> bool:
    """entity 그룹이 실제 식별자 전체인지 확인한다.

    `_`, quoted-name의 `-`, schema 구분 `.` 등이 그룹 직전·직후에 남으면 DDL에 우연히
    존재하는 짧은 접두 이름으로 잘못 해소될 수 있으므로 부분 추출로 거부한다.
    """
    start, end = match.span("entity")

    def continues(ch: str) -> bool:
        return ch.isalnum() or ch in "_.$-"

    # 후방 검사는 매치 전체의 끝 기준 — 패턴이 소비한 꼬리(SQL_KEY 의 ``_<op>\d{4}``)를
    # entity 그룹 끝 기준으로 보면 부분 추출로 오판한다.
    tail_pos = end if end == match.end() else match.end()

    return not (
        (start > 0 and continues(text[start - 1]))
        or (tail_pos < len(text) and continues(text[tail_pos]))
    )


def _camel_to_snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _snake_to_camel(name: str) -> str:
    parts = name.lower().split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])
