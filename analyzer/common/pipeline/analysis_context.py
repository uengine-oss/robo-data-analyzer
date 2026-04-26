"""Phase 3 LLM 프롬프트용 컨텍스트 수집/포맷팅.

두 가지 컨텍스트를 제공:
1) 상수 컨텍스트 — Neo4j CONSTANT 노드 조회 → 프롬프트 문자열
2) 테이블 컨텍스트 — catalog API 응답(sample_map) → 프롬프트 섹션

테이블 흐름:
  phase_analysis.build_table_sample_map
    → CatalogClient.sample_context  (네트워크 1회)
    → sample_map: {요청 이름: {resolved, score, columns, sample_rows} | None}
  format_table_context
    → 함수가 쓰는 테이블 이름들만 골라 프롬프트 섹션 문자열 반환

상수 흐름:
  fetch_constants_context(client)
    → MATCH (c:CONSTANT) 조회
    → "[상수 목록]\\n..." 문자열 반환 (빈 경우 "" 반환)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

from client.neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)

# 프롬프트에 인라인으로 넣을 샘플 값 최대 개수
_MAX_SAMPLE_VALUES_INLINE = 5


async def fetch_constants_context(client: Neo4jClient) -> str:
    """Neo4j에서 CONSTANT 노드를 조회하여 LLM 컨텍스트 문자열로 반환.

    Returns:
        "[상수 목록]\\n{code1}\\n{code2}..." 형식. 상수가 없으면 빈 문자열.
    """
    results = await client.execute_queries([
        "MATCH (c:CONSTANT) RETURN c.name AS name, c.code_text AS code ORDER BY c.name"
    ])
    rows = results[0] if results and results[0] else []
    if not rows:
        return ""
    parts: List[str] = []
    for r in rows:
        name = r.get("name", "")
        code = (r.get("code") or "").strip()
        if code:
            parts.append(code)
        elif name:
            parts.append(name)
    if not parts:
        return ""
    context = "[상수 목록]\n" + "\n".join(parts)
    logger.info("[CONST_CTX] 상수 컨텍스트 생성 | count=%d len=%d", len(parts), len(context))
    return context


def format_table_context(
    sample_map: Dict[str, Optional[Dict[str, Any]]],
    table_names: Sequence[str],
) -> str:
    """함수가 접근하는 테이블 중 sample_map 에 매칭된 것만 프롬프트 섹션으로.

    매칭 실패(null) 테이블은 skip. 모두 실패면 빈 문자열.
    """
    sections: List[str] = []
    for name in table_names:
        ctx = sample_map.get(name)
        if ctx is None:
            continue
        sections.append(_format_section(name, ctx))
    return "\n\n".join(sections)


def _format_section(code_name: str, ctx: Dict[str, Any]) -> str:
    """catalog 응답 1건을 프롬프트 섹션 문자열로."""
    resolved = ctx.get("resolved") or code_name
    columns: List[Dict[str, Any]] = ctx.get("columns") or []
    sample_rows: List[Dict[str, Any]] = ctx.get("sample_rows") or []

    # 컬럼별 인라인 샘플값 수집 (최대 5개, 중복·null 제거)
    col_samples: Dict[str, List[Any]] = {}
    for row in sample_rows:
        for col_name, val in row.items():
            if val is None:
                continue
            values = col_samples.setdefault(col_name, [])
            if val not in values and len(values) < _MAX_SAMPLE_VALUES_INLINE:
                values.append(val)

    # 컬럼 라인 포맷
    col_strs: List[str] = []
    for c in columns:
        cname = c.get("name") or ""
        if not cname:
            continue
        dtype = c.get("dtype") or ""
        flag = _format_flags(c)
        desc = _format_desc(c.get("description"))
        samples = col_samples.get(cname, [])
        sample_str = _format_samples(samples)
        col_strs.append(f"{cname}({dtype}{flag}){sample_str}{desc}")

    body = ", ".join(col_strs) if col_strs else "(컬럼 메타 없음)"
    header = f"[{resolved}]" if resolved == code_name else f"[{code_name} → {resolved}]"
    return f"{header} 테이블\n컬럼: {body}"


def _format_flags(col: Dict[str, Any]) -> str:
    flags: List[str] = []
    if col.get("is_primary_key"):
        flags.append("PK")
    if col.get("nullable") is False:
        flags.append("NOT NULL")
    return f", {', '.join(flags)}" if flags else ""


def _format_samples(values: List[Any]) -> str:
    if not values:
        return ""
    formatted = ", ".join(str(v) for v in values)
    return f" [예: {formatted}]"


def _format_desc(description: Optional[str]) -> str:
    if not description:
        return ""
    return f" — {description}"
