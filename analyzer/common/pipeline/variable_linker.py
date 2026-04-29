"""변수 REFERENCES 결정론 매칭 — strategy-agnostic.

호출자(phase) 책임: 자기 데이터 구조 (StatementNode / ChunkBundle 등) 를
canonical dict 로 변환해서 넘기기 + 결과 (target, source) 페어로 Cypher 빌더 호출.

이 모듈 책임: 정규식 워드 매칭 + scope 필터 + (target, source) 페어 반환.
Cypher 빌딩은 strategy 별 노드 식별자가 다르므로 호출자 측에서 처리.

canonical 입력:
  targets = [{"name", "id", "scope_id"}, ...]
    - scope_id 가 "*" (와일드카드) 면 모든 source 와 매칭 (예: PACKAGE_VARIABLE)
    - 그 외엔 sources 의 scope_id 와 일치하는 경우만
  sources = [{"id", "scope_id", "code"}, ...]
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

WILDCARD_SCOPE = "*"
_TOKEN_RE_CACHE: Dict[str, re.Pattern] = {}


def find_variable_references(
    *,
    targets: List[Dict[str, Any]],
    sources: List[Dict[str, Any]],
    min_name_length: int = 2,
) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """결정론 REFERENCES 매칭. (target, source) 페어 리스트 반환.

    target.scope_id 가 ``WILDCARD_SCOPE`` ("*") 면 모든 source 와 매칭.
    그 외엔 source.scope_id 와 일치하는 경우만.
    self-reference (target.id == source.id) 는 제외.
    """
    if not targets or not sources:
        return []

    pairs: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    seen: set = set()
    for t in targets:
        name = t.get("name") or ""
        if len(name) < min_name_length:
            continue
        pattern = _token_pattern(name)
        t_scope = t.get("scope_id")
        for s in sources:
            if t.get("id") == s.get("id"):
                continue
            if t_scope != WILDCARD_SCOPE and t_scope != s.get("scope_id"):
                continue
            if not pattern.search(s.get("code") or ""):
                continue
            key = (t["id"], s["id"])
            if key in seen:
                continue
            seen.add(key)
            pairs.append((t, s))

    logger.info(
        "[VAR_MATCHER] REFERENCES 결정론 매칭 | targets=%d sources=%d pairs=%d",
        len(targets), len(sources), len(pairs),
    )
    return pairs


def _token_pattern(name: str) -> re.Pattern:
    cached = _TOKEN_RE_CACHE.get(name)
    if cached is None:
        cached = re.compile(rf"\b{re.escape(name)}\b")
        _TOKEN_RE_CACHE[name] = cached
    return cached


__all__ = ["find_variable_references", "WILDCARD_SCOPE"]
