"""RegexTableLinker — ``ProfileResult.table_access_patterns`` 의 regex 로 소스 grep.

흐름:
1. 정규식 매칭 → (caller_function_id, op, entity) 추출.
2. ``ddl_transform`` 적용 → entity → DDL 테이블 이름 변환.
3. DDL 테이블 lookup 성공 → ``ReadsWrites`` 엣지.
4. lookup 실패 → ``unresolved_cases`` 큐 (Linking Agent 가 추적).

LLM 0회. 결정론.

**성능**: 파일 1회만 read — 모든 패턴이 같은 파일에서 ``finditer`` (N*M → N+M*P).
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from agent_tools.agent_output_models import ProfileResult
from data_models.graph_edges import ReadsWrites
from data_models.graph_nodes import Function, Table
from shared.logging_and_stream.logger import log_warning


class RegexTableLinker:
    """결정론 정규식 grep → ``ReadsWrites`` 엣지 + ``unresolved`` 누적."""

    async def link(
        self,
        project: Any,
        functions: list[Function],
        tables: list[Table],
        profile_result: Optional[dict] = None,
    ) -> tuple[list[ReadsWrites], list[dict]]:
        """프로젝트 소스 → (R/W 엣지 리스트, unresolved 케이스 리스트)."""
        if not profile_result:
            return [], []
        try:
            profile = ProfileResult.model_validate(profile_result)
        except Exception as e:
            log_warning(
                "analysis_flow.step4_linking.stage1_regex_grep.regex_table_linker",
                "validate_profile",
                "ProfileResult 파싱 실패 — Linking 1 단계 건너뜀 (전체 unresolved 처리됨)",
                error=f"{type(e).__name__}: {e}",
            )
            return [], []
        if not profile.table_access_patterns:
            return [], []

        # 함수 위치 인덱스 (file:line → function_id)
        locator = FunctionLocator(functions)
        # 테이블 이름 lookup (다양한 변형 → fqn)
        table_lookup = _build_table_lookup(tables)

        # 정규식 사전 컴파일 — 패턴마다 컴파일 X (성능).
        compiled_patterns: list[tuple[Any, Any]] = []  # (pat, compiled_regex)
        for pat in profile.table_access_patterns:
            try:
                compiled_patterns.append((pat, re.compile(pat.call_regex)))
            except re.error as e:
                log_warning(
                    "analysis_flow.step4_linking.stage1_regex_grep.regex_table_linker",
                    "compile_pattern",
                    "ProfileResult 의 call_regex 컴파일 실패 — 이 패턴 건너뜀",
                    pattern_id=pat.id,
                    call_regex=pat.call_regex,
                    error=str(e),
                )

        if not compiled_patterns:
            return [], []

        edges: list[ReadsWrites] = []
        unresolved: list[dict] = []

        # 파일 1회만 read — 모든 패턴이 같은 파일에서 finditer.
        source_root = Path(project.root)
        for src_file in source_root.rglob("*"):
            if not src_file.is_file():
                continue
            try:
                text = src_file.read_text(encoding="utf-8", errors="ignore")
            except (OSError, UnicodeDecodeError):
                continue

            for pat, regex in compiled_patterns:
                for m in regex.finditer(text):
                    line = text[: m.start()].count("\n") + 1
                    groups = m.groupdict()
                    op = groups.get("op", "")
                    entity = groups.get("entity", "")
                    if not entity:
                        continue

                    rel = _normalize_relation(pat.crud_map.get(op, "READ"))
                    table_fqn = _resolve_table(entity, table_lookup, profile.ddl_transform)
                    caller_id = locator.locate(str(src_file), line)

                    if table_fqn is None or caller_id is None:
                        unresolved.append({
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

                    edges.append(ReadsWrites(
                        function_id=caller_id,
                        table_fqn=table_fqn,
                        relation=rel,
                        evidence_file=str(src_file),
                        evidence_line=line,
                    ))
        return edges, unresolved


# ── 헬퍼 ──────────────────────────────────────────────────


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


def _normalize_relation(value: str) -> str:
    """``crud_map`` 값 (``'READ'`` / ``'WRITE'``) → ``ReadsWrites.relation`` (``'READS'`` / ``'WRITES'``)."""
    if value in ("READ", "READS"):
        return "READS"
    if value in ("WRITE", "WRITES"):
        return "WRITES"
    return "READS"


def _build_table_lookup(tables: list[Table]) -> dict[str, str]:
    """테이블 이름 다양한 변형 → fqn lookup."""
    lookup: dict[str, str] = {}
    for t in tables:
        fqn = f"{t.db}.{t.schema}.{t.name}"
        for key in (t.name, t.name.upper(), t.name.lower()):
            lookup.setdefault(key, fqn)
    return lookup


def _resolve_table(entity: str, lookup: dict[str, str], transform) -> Optional[str]:
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
