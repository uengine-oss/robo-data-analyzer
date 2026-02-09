"""Phase 2.5: LLM 기반 스테레오타입 분류

전체 분석(Phase 1~2) 완료 후, Neo4j에 저장된 클래스/메서드의 summary를 기반으로
LLM이 아키텍처 역할(스테레오타입)을 일괄 분류합니다.

동작 흐름:
1. Neo4j에서 모든 CLASS/INTERFACE 노드 + 하위 METHOD 노드의 summary 조회
2. 토큰 제한에 맞게 배치 분할
3. LLM 호출 → 스테레오타입 분류 결과 수신
4. Neo4j 멀티 레이블 + stereotype 속성 일괄 업데이트

설계 원칙:
- 규칙 기반(Phase 1)은 어노테이션/이름 패턴으로 명확한 케이스 선처리
- LLM 기반(이 Phase)은 코드 의미 분석으로 유연한 분류
- 두 결과를 합산하여 최종 스테레오타입 결정
"""

import asyncio
import json
import logging
import time
from typing import Any, AsyncGenerator, Dict, List, Optional, Set, Union

from client.neo4j_client import Neo4jClient
from analyzer.strategy.base_analyzer import AnalysisStats
from config.settings import settings
from util.rule_loader import RuleLoader
from util.text_utils import log_process, calculate_code_token
from util.stream_event import emit_message

logger = logging.getLogger(__name__)

# LLM 배치 크기 (토큰 기준)
_MAX_BATCH_TOKEN = 6000  # 클래스 + 메서드 summary를 합산한 토큰 상한
_MAX_CLASSES_PER_BATCH = 15  # 배치당 최대 클래스 수


def _rule_loader() -> RuleLoader:
    return RuleLoader(target_lang="framework")


# ============================================================================
# 1단계: Neo4j에서 분석 데이터 조회
# ============================================================================

_FETCH_CLASSES_QUERY = """
MATCH (c)
WHERE (c:CLASS OR c:INTERFACE OR c:ENUM)
  AND c.summary IS NOT NULL
  AND c.summary <> ''
OPTIONAL MATCH (c)-[:HAS_METHOD]->(m)
WHERE (m:METHOD OR m:CONSTRUCTOR)
  AND m.summary IS NOT NULL
  AND m.summary <> ''
WITH c, 
     collect(DISTINCT {
       name: m.name,
       startLine: m.startLine,
       summary: m.summary,
       node_type: labels(m)[0],
       stereotype: m.stereotype
     }) AS methods
RETURN 
  c.class_name AS class_name,
  c.name AS name,
  c.directory AS directory,
  c.file_name AS file_name,
  c.summary AS summary,
  labels(c)[0] AS node_type,
  c.stereotype AS existing_stereotype,
  c.extendsType AS extends_type,
  c.implementsTypes AS implements_types,
  methods
ORDER BY c.file_name, c.startLine
"""


async def _fetch_analyzed_classes(
    client: Neo4jClient,
    cypher_lock: asyncio.Lock,
) -> List[Dict[str, Any]]:
    """Neo4j에서 분석 완료된 클래스/인터페이스 + 메서드 정보를 조회합니다."""
    async with cypher_lock:
        results = await client.execute_queries([_FETCH_CLASSES_QUERY])

    if not results or not results[0]:
        return []

    classes = []
    for row in results[0]:
        # methods에서 null name 필터링
        methods = [m for m in (row.get("methods") or []) if m.get("name")]
        classes.append({
            "class_name": row.get("class_name") or row.get("name"),
            "name": row.get("name"),
            "directory": row.get("directory", ""),
            "file_name": row.get("file_name", ""),
            "summary": row.get("summary", ""),
            "node_type": row.get("node_type", "CLASS"),
            "existing_stereotype": row.get("existing_stereotype"),
            "extends_type": row.get("extends_type", ""),
            "implements_types": row.get("implements_types", ""),
            "methods": methods,
        })

    return classes


# ============================================================================
# 2단계: 배치 분할
# ============================================================================

def _build_llm_input(cls: Dict[str, Any]) -> str:
    """단일 클래스의 LLM 입력 텍스트를 생성합니다."""
    parts = [f"### {cls['node_type']}: {cls['class_name']}"]
    if cls.get("file_name"):
        parts.append(f"  파일: {cls['file_name']}")
    if cls.get("extends_type"):
        parts.append(f"  상속: {cls['extends_type']}")
    if cls.get("implements_types"):
        parts.append(f"  구현: {cls['implements_types']}")
    if cls.get("existing_stereotype"):
        parts.append(f"  규칙기반 스테레오타입: {cls['existing_stereotype']}")
    parts.append(f"  요약: {cls['summary']}")
    
    methods = cls.get("methods", [])
    if methods:
        parts.append(f"  메서드 ({len(methods)}개):")
        for m in methods:
            st_info = f" [규칙: {m['stereotype']}]" if m.get("stereotype") else ""
            parts.append(f"    - {m['name']} (L{m.get('startLine', '?')}): {m.get('summary', '')}{st_info}")
    
    return "\n".join(parts)


def _split_into_batches(
    classes: List[Dict[str, Any]],
) -> List[List[Dict[str, Any]]]:
    """클래스 목록을 토큰 제한에 맞게 배치로 분할합니다."""
    batches: List[List[Dict[str, Any]]] = []
    current_batch: List[Dict[str, Any]] = []
    current_tokens = 0

    for cls in classes:
        text = _build_llm_input(cls)
        token = calculate_code_token(text)

        if current_batch and (
            current_tokens + token > _MAX_BATCH_TOKEN
            or len(current_batch) >= _MAX_CLASSES_PER_BATCH
        ):
            batches.append(current_batch)
            current_batch = []
            current_tokens = 0

        current_batch.append(cls)
        current_tokens += token

    if current_batch:
        batches.append(current_batch)

    return batches


# ============================================================================
# 3단계: LLM 호출
# ============================================================================

async def _classify_batch(
    batch: List[Dict[str, Any]],
    api_key: str,
    locale: str,
) -> Optional[Dict[str, Any]]:
    """단일 배치의 스테레오타입을 LLM으로 분류합니다."""
    # 입력 텍스트 구성
    input_text = "\n\n".join(_build_llm_input(cls) for cls in batch)

    try:
        result = await asyncio.to_thread(
            _rule_loader().execute,
            "stereotype",
            {"classes": input_text, "locale": locale},
            api_key,
        )
        return result
    except Exception as e:
        logger.error(f"[STEREOTYPE] LLM 호출 실패: {e}")
        return None


# ============================================================================
# 4단계: Neo4j 업데이트
# ============================================================================

# 허용된 스테레오타입 (표준화 목록)
VALID_CLASS_STEREOTYPES: Set[str] = {
    "Controller", "Service", "Repository", "Entity", "DTO",
    "Config", "Component", "Aspect", "Filter", "Listener",
    "Validator", "Mapper", "Util", "ExceptionHandler",
    "Gateway", "Facade", "Factory", "Scheduler", "Client", "Enum",
}

VALID_METHOD_STEREOTYPES: Set[str] = {
    "Endpoint", "Query", "Command", "Getter", "Setter",
    "Lifecycle", "Scheduled", "EventHandler", "Init",
    "Transactional", "Callback", "Validation", "Transformation",
    "BusinessLogic",
}


def _build_update_queries(
    llm_result: Dict[str, Any],
    batch: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """LLM 분류 결과를 Neo4j 업데이트 쿼리로 변환합니다."""
    queries: List[Dict[str, Any]] = []
    classifications = llm_result.get("classifications", [])

    # class_name → batch 정보 매핑
    batch_map = {cls["class_name"]: cls for cls in batch}

    for cls_result in classifications:
        class_name = cls_result.get("class_name", "")
        if class_name not in batch_map:
            continue

        cls_info = batch_map[class_name]

        # 클래스 스테레오타입 업데이트
        class_stereotypes = [
            s for s in cls_result.get("class_stereotypes", [])
            if s in VALID_CLASS_STEREOTYPES
        ]

        if class_stereotypes:
            # 멀티 레이블 추가 (동적 SET 절)
            label_sets = ", ".join(f"__cy_n__:{st}" for st in class_stereotypes)
            query = (
                f"MATCH (__cy_n__:{cls_info['node_type']} {{class_name: $class_name}})\n"
                f"SET __cy_n__.stereotype = $stereotypes,\n"
                f"    __cy_n__.stereotype_reasoning = $reasoning,\n"
                f"    {label_sets}\n"
                f"RETURN __cy_n__"
            )
            queries.append({
                "query": query,
                "params": {
                    "class_name": class_name,
                    "stereotypes": class_stereotypes,
                    "reasoning": cls_result.get("reasoning", ""),
                },
            })

            log_process(
                "STEREOTYPE", "CLASS",
                f"🏷️ {class_name}: {class_stereotypes}"
            )

        # 메서드 스테레오타입 업데이트
        for method_result in cls_result.get("methods", []):
            method_name = method_result.get("method_name", "")
            start_line = method_result.get("startLine")
            method_stereotypes = [
                s for s in method_result.get("stereotypes", [])
                if s in VALID_METHOD_STEREOTYPES
            ]

            if not method_stereotypes or not start_line:
                continue

            # 메서드는 startLine + file_name으로 매칭
            m_label_sets = ", ".join(f"__cy_m__:{st}" for st in method_stereotypes)
            m_query = (
                f"MATCH (__cy_m__:METHOD {{startLine: $start_line, file_name: $file_name}})\n"
                f"SET __cy_m__.stereotype = $stereotypes,\n"
                f"    __cy_m__.stereotype_reasoning = $reasoning,\n"
                f"    {m_label_sets}\n"
                f"RETURN __cy_m__"
            )
            queries.append({
                "query": m_query,
                "params": {
                    "start_line": start_line,
                    "file_name": cls_info["file_name"],
                    "stereotypes": method_stereotypes,
                    "reasoning": method_result.get("reasoning", ""),
                },
            })

    return queries


# ============================================================================
# Phase 실행 엔트리포인트
# ============================================================================

async def run_stereotype_phase(
    analyzer: Any,
    client: Neo4jClient,
    orchestrator: Any,
    stats: AnalysisStats,
) -> AsyncGenerator[bytes, None]:
    """Phase 2.5: LLM 기반 스테레오타입 분류 실행
    
    Phase 2(LLM 분석) 완료 후 호출됩니다.
    Neo4j에 저장된 summary를 기반으로 스테레오타입을 일괄 분류합니다.
    """
    start_time = time.time()
    api_key = orchestrator.api_key
    locale = getattr(orchestrator, "locale", "ko")

    # 1. 분석 데이터 조회
    yield emit_message("   📊 분석된 클래스/인터페이스 조회 중...")
    classes = await _fetch_analyzed_classes(client, analyzer._cypher_lock)

    if not classes:
        yield emit_message("   ℹ️ 분류할 클래스가 없습니다 (summary 없음)")
        return

    total_methods = sum(len(c.get("methods", [])) for c in classes)
    yield emit_message(
        f"   ✓ {len(classes)}개 클래스, {total_methods}개 메서드 대상"
    )

    # 2. 배치 분할
    batches = _split_into_batches(classes)
    yield emit_message(f"   ✓ {len(batches)}개 배치로 분할")

    # 3. LLM 분류 (배치 병렬 처리)
    total_class_updates = 0
    total_method_updates = 0

    for batch_idx, batch in enumerate(batches, 1):
        yield emit_message(
            f"   🤖 배치 {batch_idx}/{len(batches)} 분류 중 "
            f"({len(batch)}개 클래스)..."
        )

        llm_result = await _classify_batch(batch, api_key, locale)

        if not llm_result:
            yield emit_message(f"   ⚠️ 배치 {batch_idx} 분류 실패, 스킵")
            continue

        # 4. Neo4j 업데이트
        update_queries = _build_update_queries(llm_result, batch)

        if update_queries:
            async with analyzer._cypher_lock:
                async for batch_result in client.run_graph_query(update_queries):
                    pass  # 결과 소비

            # 통계 집계
            classifications = llm_result.get("classifications", [])
            for cls_r in classifications:
                if cls_r.get("class_stereotypes"):
                    total_class_updates += 1
                total_method_updates += len([
                    m for m in cls_r.get("methods", [])
                    if m.get("stereotypes")
                ])

        yield emit_message(
            f"   ✅ 배치 {batch_idx} 완료"
        )

    elapsed = time.time() - start_time
    yield emit_message(
        f"   📋 스테레오타입 분류 결과: "
        f"{total_class_updates}개 클래스, {total_method_updates}개 메서드 "
        f"({elapsed:.1f}초)"
    )

    log_process(
        "STEREOTYPE", "COMPLETE",
        f"스테레오타입 분류 완료: {total_class_updates} 클래스, "
        f"{total_method_updates} 메서드 ({elapsed:.1f}초)"
    )
