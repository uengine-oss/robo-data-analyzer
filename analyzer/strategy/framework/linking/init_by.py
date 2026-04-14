"""INIT_BY 관계 빌더.

변수 선언부(배열 크기, 초기화식 등)에서 참조한 다른 변수/상수로
(VARIABLE)-[:INIT_BY]->(VARIABLE|CONSTANT) 엣지를 생성한다.

참조 식별자는 AST 파서(ANTLR)가 선언 트리를 순회하며 직접 수집한 것을
VariableFacts.references 로 전달받는다. 본 모듈은 이름 매칭으로
엣지를 생성할 뿐, 어떤 텍스트 파싱도 수행하지 않는다.

예: static char gc_get_date_time [LEN_GET_DATE_TIME +1];
  → references=["LEN_GET_DATE_TIME"]
  → (VARIABLE:gc_get_date_time)-[:INIT_BY]->(CONSTANT:LEN_GET_DATE_TIME)
"""

from __future__ import annotations

import logging
from typing import Dict, List

from analyzer.strategy.framework.common.models import InitByEdge, VariableFacts

logger = logging.getLogger(__name__)


def build_init_by_edges(variable_facts_list: List[VariableFacts]) -> List[InitByEdge]:
    """VariableFacts.references를 이름 매칭으로 InitByEdge 리스트로 변환한다.

    동일 이름이 여러 모듈에 존재하면 전부 엣지 대상이 된다
    (같은 파일 범위 우선 해석은 상위 파이프라인에서 필요 시 수행).
    """
    if not variable_facts_list:
        return []

    name_to_ids = _build_name_index(variable_facts_list)
    edges: List[InitByEdge] = []
    seen: set = set()

    for v in variable_facts_list:
        for ref_name in v.references:
            for source_id in name_to_ids.get(ref_name, ()):
                if source_id == v.variable_id:
                    continue
                pair = (v.variable_id, source_id)
                if pair in seen:
                    continue
                seen.add(pair)
                edges.append(InitByEdge(
                    variable_id=v.variable_id,
                    init_source_id=source_id,
                ))

    logger.info(
        "[INIT_BY] AST 기반 엣지 생성 완료 | edges=%d (variables=%d)",
        len(edges), len(variable_facts_list),
    )
    return edges


def _build_name_index(variable_facts_list: List[VariableFacts]) -> Dict[str, List[str]]:
    index: Dict[str, List[str]] = {}
    for v in variable_facts_list:
        if v.name and v.variable_id:
            index.setdefault(v.name, []).append(v.variable_id)
    return index
