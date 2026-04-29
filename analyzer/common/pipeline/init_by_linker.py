"""INIT_BY 결정론 매칭 — VARIABLE 의 초기화식에서 source 식별자 추출.

source 분류 (DECLARE := 만 대상):
  - 리터럴 ('N', 100, "abc")              → 매칭 X (initValue 텍스트로만 보존)
  - 시퀀스/복합표현식 (a+b, SEQ.NEXTVAL)   → 매칭 X (initValue 텍스트로만 보존)
  - 스칼라 서브쿼리 ((SELECT ...))         → 패스 (FROM 엣지가 추적)
  - 단순 식별자 (tagsn, V_OTHER, GET_TIME) → INIT_BY 엣지 후보

호출자(phase) 책임:
  - 자기 데이터에서 variables (initValue 가진 노드들) + name_index 준비
  - 결과 페어로 strategy 별 Cypher 빌더 호출

canonical 입력:
  variables  = [{"id", "name", "init_value"}, ...]
  name_index = {name_lower: [{"id", ...}, ...], ...}  # 가능한 source 후보들
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 단일 식별자 (점 포함 가능, e.g. DBMS_UTILITY.GET_TIME)
_IDENTIFIER_RE = re.compile(r"^\s*([A-Za-z_$#][A-Za-z0-9_$#.]*)\s*$")


def find_init_by_sources(
    *,
    variables: List[Dict[str, Any]],
    name_index: Dict[str, List[Dict[str, Any]]],
) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """init_value 가 단순 식별자인 변수들과 name_index 매칭.

    Returns:
        [(variable, source), ...] 페어 리스트. self-edge 제외.
    """
    if not variables:
        return []

    pairs: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    seen: set = set()
    for v in variables:
        init_text = (v.get("init_value") or "").strip()
        if not init_text or _is_subquery(init_text):
            continue
        identifier = _extract_identifier(init_text)
        if identifier is None:
            continue
        for src in name_index.get(identifier.lower(), []):
            if src["id"] == v["id"]:
                continue
            key = (v["id"], src["id"])
            if key in seen:
                continue
            seen.add(key)
            pairs.append((v, src))

    logger.info(
        "[INIT_BY] 결정론 매칭 | variables=%d pairs=%d", len(variables), len(pairs),
    )
    return pairs


def _is_subquery(text: str) -> bool:
    return text.startswith("(") and "select" in text.lower()


def _extract_identifier(text: str) -> Optional[str]:
    m = _IDENTIFIER_RE.match(text)
    return m.group(1) if m else None


__all__ = ["find_init_by_sources"]
