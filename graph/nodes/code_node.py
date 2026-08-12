"""코드 노드 Cypher (함수/메서드/프로시저/구문/구조).

분석 단위. Framework / DBMS 동일 노드. DBMS 의 nested range 는 ``parent_id`` 필드로
표현 + ``edges/parent_of.py`` 가 PARENT_OF 엣지 박음.

라벨 = 노드의 antlr type 하나 (``CodeNode.label`` — :METHOD/:FUNCTION/:PROCEDURE/:SELECT/:IF …).
저장 = **종류에 의미있는 속성만**(spec 020, 빈값 미저장 `SET n += item`):
- ``signature`` 는 저장 안 함 (루틴은 ``params``/``return_type`` 가 대체 — 단 필드는
  call_linker·LLM 컨텍스트가 써서 유지). ``comment``+``annotations`` → 단일 ``notes``.
"""
from __future__ import annotations

import json
from typing import Iterable

from graph.schema.graph_nodes import CodeNode
from graph.node_upsert import join_notes, nonempty, upsert_by_node_label
from graph.ownership import ANALYZER_GRAPH_OWNER
from graph.neo4j_client import Neo4jClient
from graph.nodes.stereotype_label import apply_stereotype_labels


# MERGE 쿼리·라벨 그룹핑은 ``graph/node_upsert.py`` 단일 정의처 (spec 046 FR-004).
# 빈값 미저장 — ``nonempty()`` 가 뺀 키는 ``SET n += item`` 이라 애초에 안 박힌다.


# 의미 분석 결과 갱신 (analyze_code_step). table_insights 는 노드에 안 박는다 —
# enrich_table_description_step 이 Table/Column.analyzed_description 으로 분배(제자리, spec 020).
SET_SUMMARY_QUERY = f"""
UNWIND $items AS item
MATCH (node:ANALYZER_OWNED {{id: item.id, graph_owner: '{ANALYZER_GRAPH_OWNER}'}})
// 자동 용어 생성 폐기 전 graph 속성은 동일 노드 재분석에서도 남지 않게 제거한다.
REMOVE node.domain_terms, node.domain_terms_json, node.logical_name,
       node.summary, node.stereotype, node.summary_evidence_status,
       node.summary_missing_context_json
SET node += item
"""


def to_item(fn: CodeNode) -> dict:
    """저장 속성 (비어있지 않은 것만). signature 미저장, comment+annotations→notes.

    ★ ``owner_id``(소속 모듈)를 **그래프에 저장한다** (spec 047 FR-007).
    종전엔 dataclass 에만 있고 노드 속성엔 안 박았다 → 소비자(아키텍트)가 소속 모듈을 알려면
    **id 문자열을 잘라내는 수밖에 없었다**. 그 파싱이 id 규칙을
    붙들어 매서 id 겹침을 못 고치게 만든 **병의 뿌리**였다. 소속을 알려주면 뜯을 이유가 없다.
    """
    return nonempty({
        "id": fn.id,
        "type": fn.label,
        "owner_id": fn.owner_id,          # 소속 모듈 id — 소비자가 id 를 뜯지 않게 (FR-007)
        "name": fn.name,
        "file_path": fn.file_path,
        "start_line": fn.start_line,
        "end_line": fn.end_line,
        "return_type": fn.return_type,
        "params": fn.params,
        "code_text": fn.code_text,
        # 구조 statement/제어 조건 표현 원문 (parser spec 016) — 소비자가 소스를
        # 재파싱하지 않도록 그래프에도 보존한다. 빈값은 nonempty 가 제외.
        "expression": fn.expression,
        "condition_timing": fn.condition_timing,
        "target": fn.target,
        "operator": fn.operator,
        "notes": join_notes(fn.comment, fn.annotations),
    })


def to_summary_item(fn: CodeNode) -> dict:
    """3 분할 분석 결과 → 적재 item (비어있지 않은 것만). table_insights 는 컬럼/엣지로 분배.

    ``code_text`` 는 분석 결과가 아니라 원문 — step2 가 채우고 저장한다 (여기 없음).
    """
    return nonempty({
        "id": fn.id,
        "summary": fn.summary,
        "stereotype": fn.stereotype,
        "summary_evidence_status": (fn.extra or {}).get("summary_evidence_status"),
        "summary_missing_context_json": (
            json.dumps(
                (fn.extra or {}).get("summary_missing_context"),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            if (fn.extra or {}).get("summary_missing_context")
            else None
        ),
        # spec 054 FR-023: 폴백 재검사도 이상이면 "부분 결과 + 이상 플래그"를 함수 노드에 저장 —
        # 그래프에 남아야 소비자(UI/검색)가 신뢰도를 안다. 정상경로는 키 부재 → nonempty 가 제외.
        "rules_quality_flag": (fn.extra or {}).get("rules_quality_flag"),
        # 모델 품질 finding은 trusted RULE/EXAMPLE 노드가 아니다. CodeNode의 명시적
        # 상태+JSON 증거로 보존해 검색/UI가 정상 완료와 구분하고 run 뒤 재검증할 수 있다.
        "analysis_status": (fn.extra or {}).get("analysis_status"),
        "analysis_findings_json": (
            json.dumps(
                (fn.extra or {}).get("analysis_findings"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if (fn.extra or {}).get("analysis_findings")
            else None
        ),
    })


async def upsert_code_nodes(client: Neo4jClient, code_nodes: Iterable[CodeNode]) -> None:
    """노드의 antlr type 라벨(``fn.label``)별로 묶어 MERGE — ``:METHOD``/``:FUNCTION``/``:PROCEDURE``/``:SELECT`` …"""
    await upsert_by_node_label(client, code_nodes, to_item)


async def update_summaries(client: Neo4jClient, code_nodes: Iterable[CodeNode]) -> None:
    """analyze_code 결과를 갱신한다. domain terms는 canonical JSON scalar로 저장한다.

    stereotype 은 속성 + **라벨**로 부여(spec 033) — 소비처가 위치(labels[1:])로 역할 필터/색칠.
    """
    analyzed = [
        f for f in code_nodes
        if (
            f.summary is not None
            or (f.extra or {}).get("analysis_status")
            or (f.extra or {}).get("summary_evidence_status")
        )
    ]
    await client.run_batch_unwind(SET_SUMMARY_QUERY, [to_summary_item(f) for f in analyzed])
    await apply_stereotype_labels(
        client,
        [(f.id, f.stereotype or "") for f in analyzed],
    )
