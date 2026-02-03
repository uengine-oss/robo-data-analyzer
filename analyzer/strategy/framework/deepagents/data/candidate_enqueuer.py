"""
Candidate Enqueuer (S3, R2)

후보 선정 + 큐 추가.
S3: elementId 사용
R2: Cypher 제외 강화 + dedup 강제
R3: 파일별 그룹화 + 중첩 범위 제거

사용 예시:
    enqueuer = CandidateEnqueuer(neo4j_driver, queue, config)
    
    # 후보 수집 + 큐잉
    count = await enqueuer.enqueue_candidates()
    
    # 파일별 그룹화된 후보
    grouped = enqueuer.group_candidates_by_file(candidates)
"""

from typing import Any, Dict, List, Optional, Tuple
from collections import defaultdict
import logging

from ..helpers.priority_queue import PriorityQueue, ResolutionCandidate
from ..helpers.logger import get_logger


class CandidateEnqueuer:
    """
    후보 수집 + 큐잉
    
    S3: elementId 사용
    R2: Cypher 제외 강화 + dedup 강제
    """
    
    def __init__(
        self,
        neo4j_driver: Any,
        queue: PriorityQueue,
        idempotency_hours: int = 24
    ):
        """
        Args:
            neo4j_driver: Neo4j 드라이버
            queue: 우선순위 큐
            idempotency_hours: 멱등성 체크 시간 (시간 단위, 기본 24시간)
        """
        self.driver = neo4j_driver
        self.queue = queue
        self.idempotency_hours = idempotency_hours
    
    async def enqueue_candidates(
        self,
        limit: int = 1000,
        artifact_types: Optional[List[str]] = None
    ) -> int:
        """
        후보 수집 + 큐 추가
        
        Args:
            limit: 최대 수집 개수
            artifact_types: 필터링할 아티팩트 타입 (None이면 전체)
        
        Returns:
            추가된 후보 수
        """
        log = get_logger()
        
        # S3: elementId 사용, 제외 조건 강화
        query = self._build_candidate_query(limit, artifact_types)
        log.candidates_query(query)
        
        candidates = await self._execute_query(query)
        log.custom(f"   Neo4j returned {len(candidates)} candidate records")
        
        added = 0
        skipped = 0
        for record in candidates:
            candidate = self._record_to_candidate(record)
            if candidate:
                if self.queue.push(candidate):
                    added += 1
                else:
                    skipped += 1  # 중복
        
        if skipped > 0:
            log.custom(f"   Skipped {skipped} duplicates (dedup)")
        
        log.custom(f"   ✓ Enqueued {added} candidates to priority queue")
        
        return added
    
    def _build_candidate_query(
        self,
        limit: int,
        artifact_types: Optional[List[str]] = None
    ) -> str:
        """후보 선정 Cypher 쿼리 생성
        
        LLM 분석에서 마킹된 노드를 찾아서 후보로 수집합니다.
        ast_processor.py의 _build_analysis_queries()에서 설정하는 속성:
        - external_hints: ["entity=OrderHeader", "service=createOrder"] 등
        - resolution_candidate: true
        """
        
        # 기본 조건: resolution_candidate로 마킹되었고 3가지 값이 모두 있는 노드
        # S3: elementId 사용
        # 필수 속성: external_hints, missing_bindings, confidence
        # Neo4j 5.x 호환: 존재하지 않을 수 있는 속성은 coalesce 또는 제거
        query = f"""
        MATCH (n)
        WHERE n.resolution_candidate = true
          AND n.external_hints IS NOT NULL 
          AND size(n.external_hints) > 0
          AND n.missing_bindings IS NOT NULL
          AND n.confidence IS NOT NULL
        
        // 제외 조건 (플랜 기반)
        // 1. 이미 FROM/WRITES 관계가 있는 경우 제외 (관계 존재 여부만 체크)
        AND NOT (n)-[:FROM|WRITES]->()
        
        // 2. 이미 success 상태인 경우 제외 (멱등성)
        AND coalesce(n.deepagents_status, '') <> 'success'
        
        // 3. 작업 중인 경우 제외
        AND coalesce(n.deepagents_status, '') <> 'working'
        """
        
        # 아티팩트 타입 필터
        if artifact_types:
            types_str = ", ".join([f"'{t}'" for t in artifact_types])
            query += f"\n        AND n.artifact_type IN [{types_str}]"
        
        # 반환 필드 (S3: elementId)
        # ast_processor에서 설정하는 속성명과 일치
        # 필수: external_hints, search_tokens, missing_bindings, confidence
        query += """
        RETURN elementId(n) AS element_id,
               labels(n)[0] AS node_type,
               n.artifact_type AS artifact_type,
               n.external_hints AS external_hints,
               COALESCE(n.search_tokens, []) AS search_tokens,
               COALESCE(n.missing_bindings, []) AS missing_bindings,
               n.summary AS code_compact,
               n.directory AS file_path,
               n.file_name AS file_name,
               n.startLine AS line_start,
               n.endLine AS line_end,
               COALESCE(n.confidence, 0.5) AS confidence,
               COALESCE(n.token, 0) AS token
        // 청크 기반 정렬: directory, file_name, startLine ASC
        ORDER BY n.directory ASC,
                 n.file_name ASC,
                 n.startLine ASC
        """
        
        query += f"\n        LIMIT {limit}"
        
        return query
    
    async def _execute_query(self, query: str) -> List[Dict[str, Any]]:
        """Cypher 쿼리 실행"""
        async with self.driver.session() as session:
            result = await session.run(query)
            return [dict(record) async for record in result]
    
    def _record_to_candidate(self, record: Dict[str, Any]) -> Optional[ResolutionCandidate]:
        """쿼리 결과를 ResolutionCandidate로 변환
        
        ast_processor에서 설정한 속성과 Neo4j 기본 속성을 매핑합니다.
        """
        element_id = record.get("element_id")
        if not element_id:
            return None
        
        # external_hints에서 artifact_type 추론 (없으면 unknown_candidate)
        external_hints = record.get("external_hints") or []
        artifact_type = record.get("artifact_type")
        if not artifact_type and external_hints:
            artifact_type = self._infer_artifact_type(external_hints)
        
        candidate = ResolutionCandidate(
            element_id=element_id,
            node_type=record.get("node_type", ""),
            artifact_type=artifact_type or "unknown_candidate",
            external_hints=external_hints,
            search_tokens=record.get("search_tokens") or [],
            missing_bindings=record.get("missing_bindings") or [],
            code_compact=record.get("code_compact", ""),
            file_path=record.get("file_path", ""),
            line_start=record.get("line_start", 0),
            line_end=record.get("line_end", 0),
            confidence=record.get("confidence", 0.5),
            token=record.get("token", 0),  # 청크 구성용 토큰 수
        )
        
        # 우선순위 + cheapness 계산
        candidate.priority = self._calculate_priority(candidate)
        candidate.cheapness = self._calculate_cheapness(candidate)
        
        return candidate
    
    def _infer_artifact_type(self, external_hints: List[str]) -> str:
        """external_hints에서 artifact_type 추론"""
        for hint in external_hints:
            if hint.startswith("entity="):
                return "ofbiz_entity"
            if hint.startswith("service="):
                return "ofbiz_service"
            if hint.startswith("queryId=") or hint.startswith("namespace="):
                return "mybatis_mapper"
            if hint.startswith("bean="):
                return "spring_bean"
            if hint.startswith("configKey="):
                return "yaml_config"
        return "unknown_candidate"
    
    def _calculate_priority(self, candidate: ResolutionCandidate) -> int:
        """우선순위 계산"""
        score = 50  # 기본값
        
        # 힌트가 많으면 높은 우선순위
        if candidate.external_hints:
            score += len(candidate.external_hints) * 10
        
        # queryId, namespace 등 정확한 힌트
        for hint in candidate.external_hints:
            if any(hint.startswith(prefix) for prefix in ["queryId=", "namespace=", "entity="]):
                score += 30
                break
        
        # confidence 반영
        score += int(candidate.confidence * 20)
        
        return min(score, 200)
    
    def _calculate_cheapness(self, candidate: ResolutionCandidate) -> int:
        """
        Cheapness 점수 계산 (Locate 비용 추정)
        
        높을수록 cheap (찾기 쉬움)
        """
        # 정확한 ID 힌트
        for hint in candidate.external_hints:
            for prefix in ["queryId=", "namespace=", "entity=", "bean="]:
                if hint.startswith(prefix):
                    return 100  # exact_id
        
        # 경로 힌트
        for hint in candidate.external_hints:
            if hint.startswith("path=") or hint.startswith("resource="):
                return 70  # path
        
        # 일반 키워드
        for hint in candidate.external_hints:
            if hint.startswith("symbol=") or hint.startswith("key="):
                return 40  # keyword
        
        # 힌트 없음
        return 10  # none
    
    async def get_pending_count(self) -> int:
        """해석 대기 중인 노드 수 조회 (플랜 기반)"""
        query = """
        MATCH (n)
        WHERE n.resolution_candidate = true
          AND n.external_hints IS NOT NULL 
          AND size(n.external_hints) > 0
          AND n.missing_bindings IS NOT NULL
          AND n.confidence IS NOT NULL
          AND NOT EXISTS { (n)-[:FROM|WRITES]->(:TABLE) }
          AND (n.deepagents_status IS NULL OR n.deepagents_status <> 'success')
        RETURN count(n) AS pending_count
        """
        
        async with self.driver.session() as session:
            result = await session.run(query)
            record = await result.single()
            return record["pending_count"] if record else 0

    # =========================================================================
    # 파일별 그룹화 및 중복 제거 (File Grouping & Deduplication)
    # =========================================================================

    def group_candidates_by_file(
        self, 
        candidates: List[ResolutionCandidate]
    ) -> Dict[str, List[ResolutionCandidate]]:
        """
        후보를 파일(directory)별로 그룹화
        
        동일 파일의 후보들을 함께 처리하여 컨텍스트 공유 가능
        
        Args:
            candidates: 후보 목록
        
        Returns:
            {file_path: [candidates]} 형태의 딕셔너리
        """
        grouped: Dict[str, List[ResolutionCandidate]] = defaultdict(list)
        
        for candidate in candidates:
            file_path = candidate.file_path or "unknown"
            grouped[file_path].append(candidate)
        
        # 각 파일 내에서 startLine 기준 정렬
        for file_path in grouped:
            grouped[file_path].sort(key=lambda c: (c.line_start or 0, c.line_end or 0))
        
        return dict(grouped)

    def remove_nested_candidates(
        self, 
        candidates: List[ResolutionCandidate]
    ) -> List[ResolutionCandidate]:
        """
        중첩된 후보 제거 (부모 범위에 포함된 자식 제거)
        
        예: 148~165 범위 노드가 있으면 그 안의 150~155 후보는 제거
        부모 노드를 처리하면 자식 노드도 함께 분석되므로 중복 방지
        
        Args:
            candidates: 후보 목록 (동일 파일 내)
        
        Returns:
            중첩 제거된 후보 목록
        """
        if not candidates:
            return []
        
        # startLine 기준 정렬
        sorted_candidates = sorted(
            candidates, 
            key=lambda c: (c.line_start or 0, -(c.line_end or 0))  # 넓은 범위 우선
        )
        
        result: List[ResolutionCandidate] = []
        
        for candidate in sorted_candidates:
            is_nested = False
            
            for existing in result:
                # 현재 후보가 기존 후보의 범위 안에 완전히 포함되는지 확인
                if self._is_range_contained(
                    inner_start=candidate.line_start,
                    inner_end=candidate.line_end,
                    outer_start=existing.line_start,
                    outer_end=existing.line_end
                ):
                    is_nested = True
                    break
            
            if not is_nested:
                result.append(candidate)
        
        return result

    def _is_range_contained(
        self,
        inner_start: Optional[int],
        inner_end: Optional[int],
        outer_start: Optional[int],
        outer_end: Optional[int]
    ) -> bool:
        """
        inner 범위가 outer 범위에 완전히 포함되는지 확인
        
        동일 범위는 중첩으로 간주하지 않음 (같은 노드)
        """
        if None in (inner_start, inner_end, outer_start, outer_end):
            return False
        
        # 동일 범위면 중첩 아님
        if inner_start == outer_start and inner_end == outer_end:
            return False
        
        # inner가 outer 안에 완전히 포함되는 경우
        return outer_start <= inner_start and inner_end <= outer_end

    def deduplicate_file_groups(
        self, 
        grouped: Dict[str, List[ResolutionCandidate]]
    ) -> Dict[str, List[ResolutionCandidate]]:
        """
        파일별 그룹에서 중첩 후보 제거
        
        Args:
            grouped: group_candidates_by_file()의 결과
        
        Returns:
            중첩 제거된 그룹
        """
        result = {}
        
        for file_path, candidates in grouped.items():
            deduplicated = self.remove_nested_candidates(candidates)
            if deduplicated:
                result[file_path] = deduplicated
        
        return result

    async def enqueue_grouped_candidates(
        self,
        limit: int = 1000,
        artifact_types: Optional[List[str]] = None
    ) -> Tuple[int, Dict[str, List[ResolutionCandidate]]]:
        """
        후보 수집 + 파일별 그룹화 + 중복 제거 + 큐 추가
        
        Args:
            limit: 최대 수집 개수
            artifact_types: 필터링할 아티팩트 타입
        
        Returns:
            (추가된 후보 수, 그룹화된 후보 딕셔너리)
        """
        log = get_logger()
        
        # 쿼리 실행
        query = self._build_candidate_query(limit, artifact_types)
        log.candidates_query(query)
        
        records = await self._execute_query(query)
        log.custom(f"   Neo4j returned {len(records)} candidate records")
        
        # 후보 변환
        candidates = []
        for record in records:
            candidate = self._record_to_candidate(record)
            if candidate:
                candidates.append(candidate)
        
        # 파일별 그룹화
        grouped = self.group_candidates_by_file(candidates)
        log.custom(f"   Grouped into {len(grouped)} files")
        
        # 중첩 제거
        deduplicated = self.deduplicate_file_groups(grouped)
        
        total_before = sum(len(c) for c in grouped.values())
        total_after = sum(len(c) for c in deduplicated.values())
        removed = total_before - total_after
        
        if removed > 0:
            log.custom(f"   Removed {removed} nested candidates")
        
        # 큐에 추가
        added = 0
        skipped = 0
        for file_path, file_candidates in deduplicated.items():
            for candidate in file_candidates:
                if self.queue.push(candidate):
                    added += 1
                else:
                    skipped += 1
        
        if skipped > 0:
            log.custom(f"   Skipped {skipped} duplicates (dedup)")
        
        log.custom(f"   ✓ Enqueued {added} candidates from {len(deduplicated)} files")
        
        return added, deduplicated

