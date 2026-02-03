"""
Runtime Resolution Phase (FSM 기반)

FrameworkAnalyzer 통합 Phase.
DeepAgents 파이프라인 실행.

플랜 기반 안전장치:
- EvidenceManager: 증거 관리
- MinimalLock: 동시 처리 방지
- RuntimePolicy: 정책 강제

레거시 제거:
- CommitLock → MinimalLock
- ReviewQueue → 노드 속성 기반
- GuardrailsEnforcer → RuntimePolicy

사용 예시:
    phase = RuntimeResolutionPhase(neo4j_driver, config)
    
    # Phase 실행
    results = await phase.run(context)
"""

import asyncio
import uuid
import os
import logging
import time
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from config.settings import settings

from .main_agent import (
    create_runtime_resolution_agent,
    process_chunk,  # 청크 단위 처리 (권장)
    process_candidate,  # 레거시 호환용 (deprecated)
    resume_after_hitl,
    normalize_file_path,
)
from ..data.candidate_enqueuer import CandidateEnqueuer
from ..helpers.priority_queue import PriorityQueue, ResolutionCandidate
from ..data.evidence_manager import EvidenceManager
from ..support.minimal_lock import MinimalLock
from ..support.runtime_policy import RuntimePolicy
from ..reports.resolution_report import (
    ResolutionReport,
    CandidateReport,
    StepReport,
    ToolCallReport,
    ReportCollector,
)
from ..helpers.logger import get_logger, configure_logger
from ..support.cache_layer import CacheLayer, hints_to_artifact_key


@dataclass
class PhaseConfig:
    """Phase 설정
    
    경로 구조:
    - docs_root: 외부 설정 파일 (XML, YAML) - 읽기 전용
    - ddl_root: DDL 파일 - 읽기 전용  
    - runs_root: 작업 워크스페이스 - 읽기/쓰기
    - skills_root: 스킬 파일 (SKILL.md) - 읽기 전용
    - memory_root: (미사용) 모든 지침은 SYSTEM_PROMPT에 통합됨
    """
    docs_root: str       # 외부 설정 파일 (data/docs - 프로젝트 루트)
    ddl_root: str        # DDL 파일 (data/ddl - 프로젝트 루트)
    runs_root: str = "data/runs"  # 작업 워크스페이스 (프로젝트 루트)
    skills_root: Optional[str] = None      # 스킬 파일 (기본값: analyzer/strategy/framework/deepagents/resources/skills)
    memory_root: Optional[str] = None      # 메모리 파일 (기본값: analyzer/strategy/framework/deepagents/resources/memory)
    reports_root: Optional[str] = None     # 보고서 출력 (기본값: analyzer/strategy/framework/deepagents/resources/reports)
    
    # 병렬 처리
    max_workers: int = 4
    max_queue_size: int = 10000
    max_candidates_per_run: int = 1000
    
    # 신뢰도
    min_confidence: float = 0.7
    
    # 멱등성 (재처리 방지 시간 - 시간 단위)
    idempotency_hours: int = 24
    
    # 안전장치
    enable_hitl: bool = True              # Human-in-the-Loop 활성화
    enable_evidence_check: bool = True    # Evidence 필수 검증
    enable_ddl_check: bool = True         # DDL 존재 검증
    enable_commit_lock: bool = True       # 커밋 락 활성화
    enable_report: bool = True            # 보고서 생성 활성화
    
    # 청크 처리 (Phase 2)
    max_tokens_per_chunk: int = 1000      # 청크당 최대 토큰
    enable_memory_learning: bool = True    # 메모리 학습 활성화


@dataclass
class PhaseResult:
    """Phase 결과"""
    total_candidates: int
    processed: int
    success: int          # active 승격 성공
    failed: int           # 실패
    pending_review: int   # 보류 (human 검토 필요)
    
    # 시간
    started_at: datetime
    completed_at: datetime
    duration_seconds: float
    
    # 상세
    errors: List[str] = field(default_factory=list)
    
    # 추가 통계
    evidence_created: int = 0       # 생성된 Evidence 수
    ddl_missing: int = 0            # DDL 미존재 건수
    low_confidence: int = 0         # 낮은 confidence 건수
    lock_conflicts: int = 0         # 락 충돌 건수
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_candidates": self.total_candidates,
            "processed": self.processed,
            "success": self.success,
            "failed": self.failed,
            "pending_review": self.pending_review,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "duration_seconds": self.duration_seconds,
            "errors": self.errors,
            "evidence_created": self.evidence_created,
            "ddl_missing": self.ddl_missing,
            "low_confidence": self.low_confidence,
            "lock_conflicts": self.lock_conflicts,
        }
    
    @property
    def success_rate(self) -> float:
        """성공률"""
        if self.processed == 0:
            return 0.0
        return self.success / self.processed


class RuntimeResolutionPhase:
    """
    런타임 해석 Phase (FSM 기반)
    
    FrameworkAnalyzer에 통합되는 Phase.
    
    플랜 기반 안전장치:
    - EvidenceManager: 증거 관리
    - MinimalLock: 동시 처리 방지
    - RuntimePolicy: 정책 강제
    """
    
    def __init__(
        self,
        neo4j_driver: Any,
        config: Optional[PhaseConfig] = None
    ):
        """
        Args:
            neo4j_driver: Neo4j 드라이버
            config: Phase 설정
        """
        self.driver = neo4j_driver
        self.config = config or PhaseConfig(docs_root="data/docs", ddl_root="data/ddl")
        
        # 기본 경로 설정 (framework 내부 resources 폴더)
        if self.config.skills_root is None:
            current_file = Path(__file__).resolve()
            resources_dir = current_file.parent / "resources"
            self.config.skills_root = str(resources_dir / "skills")
        
        if self.config.memory_root is None:
            current_file = Path(__file__).resolve()
            resources_dir = current_file.parent / "resources"
            self.config.memory_root = str(resources_dir / "memory")
        
        if self.config.reports_root is None:
            current_file = Path(__file__).resolve()
            resources_dir = current_file.parent / "resources"
            self.config.reports_root = str(resources_dir / "reports")
        
        # 작업 디렉토리 생성
        os.makedirs(self.config.runs_root, exist_ok=True)
        os.makedirs(self.config.reports_root, exist_ok=True)
        
        # 큐
        self.queue = PriorityQueue(
            max_size=self.config.max_queue_size,
            overflow_policy="drop_lowest_priority"
        )
        
        # Enqueuer
        self.enqueuer = CandidateEnqueuer(
            neo4j_driver,
            self.queue,
            idempotency_hours=self.config.idempotency_hours
        )
        
        # RuntimePolicy (가드레일 대체)
        self.policy = RuntimePolicy()
        
        # MinimalLock (커밋 락 대체)
        self.lock = MinimalLock()
        
        # DeepAgent (공식 라이브러리) + 안전장치 인스턴스 + 스토어
        self.agent, self.safety_instances, self.store = create_runtime_resolution_agent(
            neo4j_driver,
            self.config.docs_root,
            self.config.ddl_root,
            self.config.runs_root,
            skills_root=self.config.skills_root,
            memory_root=self.config.memory_root,
            min_confidence=self.config.min_confidence,
            enable_hitl=self.config.enable_hitl,
            api_key=settings.llm.api_key,
            model=settings.llm.model,
        )
        
        # Evidence Manager 참조
        self.evidence_manager: EvidenceManager = self.safety_instances.get("evidence_manager")
    
    # =========================================================================
    # Phase3 6단계 플로우: 정렬 → 중첩제거 → 청크 → 공통탐색 → 노드별확정 → 노드별커밋
    # =========================================================================
    
    # Node type priorities for nested removal (higher = keep)
    NODE_TYPE_PRIORITY = {
        "METHOD": 100,
        "CONSTRUCTOR": 95,
        "CLASS": 80,
        "INTERFACE": 75,
        "ENUM": 70,
        "UNIT": 60,
        # 기타 노드 타입
        "TRY": 50,
        "IF": 50,
        "FOR": 50,
        "WHILE": 50,
        "SWITCH": 50,
        "RETURN": 40,
        "VARIABLE": 40,
        "ANNOTATION": 30,
    }
    
    def _sort_candidates(
        self, 
        candidates: List[ResolutionCandidate]
    ) -> List[ResolutionCandidate]:
        """Step 1: 후보 정렬 (directory, file_name, startLine ASC)
        
        Args:
            candidates: 정렬 전 후보 목록
        
        Returns:
            정렬된 후보 목록
        """
        return sorted(
            candidates,
            key=lambda c: (
                c.file_path or "",
                c.line_start or 0,
                c.line_end or 0
            )
        )
    
    def _remove_nested_candidates(
        self, 
        candidates: List[ResolutionCandidate]
    ) -> List[ResolutionCandidate]:
        """Step 2: 중첩 제거 (부모-자식 포함 제거, 우선순위 METHOD > CLASS > 기타)
        
        같은 파일 내에서:
        - 후보 A의 범위(startLine~endLine)가 후보 B를 완전히 포함하면 하나만 남긴다
        - 우선순위: METHOD > CLASS > 기타
        
        Args:
            candidates: 중첩 제거 전 후보 목록 (이미 정렬됨)
        
        Returns:
            중첩 제거된 후보 목록
        """
        if not candidates:
            return []
        
        # 파일별 그룹화
        by_file: Dict[str, List[ResolutionCandidate]] = {}
        for c in candidates:
            file_path = c.file_path or "unknown"
            if file_path not in by_file:
                by_file[file_path] = []
            by_file[file_path].append(c)
        
        result: List[ResolutionCandidate] = []
        
        for file_path, file_candidates in by_file.items():
            # 범위 넓은 순 + 우선순위 높은 순으로 정렬
            sorted_candidates = sorted(
                file_candidates,
                key=lambda c: (
                    (c.line_start or 0),  # 시작 라인 오름차순
                    -((c.line_end or 0) - (c.line_start or 0)),  # 범위 넓은 것 우선
                    -self.NODE_TYPE_PRIORITY.get(c.node_type.upper(), 0)  # 우선순위 높은 것 우선
                )
            )
            
            kept: List[ResolutionCandidate] = []
            
            for candidate in sorted_candidates:
                is_nested = False
                
                for existing in kept:
                    # 현재 후보가 기존 후보의 범위 안에 완전히 포함되는지 확인
                    if self._is_range_contained(
                        inner_start=candidate.line_start,
                        inner_end=candidate.line_end,
                        outer_start=existing.line_start,
                        outer_end=existing.line_end
                    ):
                        # 우선순위 비교: 현재 후보가 더 높은 우선순위면 기존 것 교체
                        curr_priority = self.NODE_TYPE_PRIORITY.get(
                            candidate.node_type.upper(), 0
                        )
                        exist_priority = self.NODE_TYPE_PRIORITY.get(
                            existing.node_type.upper(), 0
                        )
                        
                        if curr_priority > exist_priority:
                            # 기존 것을 제거하고 현재 것을 추가
                            kept.remove(existing)
                        else:
                            # 현재 것은 제거 (중첩)
                            is_nested = True
                        break
                
                if not is_nested:
                    kept.append(candidate)
            
            result.extend(kept)
        
        # 다시 정렬 (파일 순서 유지)
        return self._sort_candidates(result)
    
    def _is_range_contained(
        self,
        inner_start: Optional[int],
        inner_end: Optional[int],
        outer_start: Optional[int],
        outer_end: Optional[int]
    ) -> bool:
        """inner 범위가 outer 범위에 완전히 포함되는지 확인
        
        동일 범위는 중첩으로 간주하지 않음
        """
        if None in (inner_start, inner_end, outer_start, outer_end):
            return False
        
        # 동일 범위면 중첩 아님
        if inner_start == outer_start and inner_end == outer_end:
            return False
        
        # inner가 outer 안에 완전히 포함되는 경우
        return outer_start <= inner_start and inner_end <= outer_end
    
    def _build_chunks(
        self, 
        candidates: List[ResolutionCandidate], 
        max_tokens: int = 1000
    ) -> List[List[ResolutionCandidate]]:
        """Step 3: 청크 빌더 (≤1000 tokens, 같은 파일 우선, startLine 근접 우선)
        
        Args:
            candidates: 전체 후보 목록 (중첩 제거 후, 정렬됨)
            max_tokens: 청크당 최대 토큰 (기본 1000)
        
        Returns:
            청크 리스트 (각 청크는 후보 목록)
        """
        log = get_logger()
        
        if not candidates:
            log.custom("No candidates to chunk")
            return []
        
        # 청크 구성 시작 로깅
        log.chunk_building_start(len(candidates), max_tokens)
        
        chunks: List[List[ResolutionCandidate]] = []
        current_chunk: List[ResolutionCandidate] = []
        current_tokens = 0
        current_file = ""
        chunk_token_totals: List[int] = []
        
        for candidate in candidates:
            # Neo4j에서 가져온 token 값 (없으면 code_compact 길이로 추정)
            node_tokens = candidate.token if candidate.token > 0 else len(candidate.code_compact) // 4
            node_tokens = max(node_tokens, 1)  # 최소 1 토큰
            
            candidate_file = candidate.file_path or ""
            
            # 새 청크 시작 조건:
            # 1. 토큰 초과
            # 2. 다른 파일
            should_start_new = (
                (current_tokens + node_tokens > max_tokens and current_chunk) or
                (current_file and candidate_file != current_file and current_chunk)
            )
            
            if should_start_new:
                # 현재 청크 완료
                chunk_id = len(chunks) + 1
                candidate_details = [
                    {
                        'element_id': c.element_id,
                        'token': c.token or len(c.code_compact) // 4,
                        'hints': c.external_hints[:2] if c.external_hints else [],
                    }
                    for c in current_chunk
                ]
                log.chunk_formed(chunk_id, current_chunk, current_tokens, candidate_details)
                chunk_token_totals.append(current_tokens)
                
                # 새 청크 시작
                chunks.append(current_chunk)
                current_chunk = [candidate]
                current_tokens = node_tokens
                current_file = candidate_file
            else:
                current_chunk.append(candidate)
                current_tokens += node_tokens
                if not current_file:
                    current_file = candidate_file
        
        # 마지막 청크 추가
        if current_chunk:
            chunk_id = len(chunks) + 1
            candidate_details = [
                {
                    'element_id': c.element_id,
                    'token': c.token or len(c.code_compact) // 4,
                    'hints': c.external_hints[:2] if c.external_hints else [],
                }
                for c in current_chunk
            ]
            log.chunk_formed(chunk_id, current_chunk, current_tokens, candidate_details)
            chunk_token_totals.append(current_tokens)
            chunks.append(current_chunk)
        
        # 청크 구성 완료 로깅
        avg_tokens = sum(chunk_token_totals) / len(chunk_token_totals) if chunk_token_totals else 0
        log.chunk_building_complete(len(chunks), avg_tokens)
        
        return chunks
    
    def _merge_chunk_hints(
        self, 
        chunk: List[ResolutionCandidate]
    ) -> tuple[List[str], Dict[str, str]]:
        """Step 4-1: 청크 힌트 통합 (중복 제거 + artifact_key 정규화)
        
        Args:
            chunk: 후보 목록
        
        Returns:
            (unique_hints, artifact_keys) - 통합 힌트 목록과 artifact_key 매핑
        """
        all_hints = []
        for candidate in chunk:
            all_hints.extend(candidate.external_hints or [])
        
        # 중복 제거
        unique_hints = list(set(all_hints))
        
        # artifact_key 정규화
        artifact_keys = {}
        for hint in unique_hints:
            # 개별 힌트를 artifact_key로 변환
            artifact_key = hints_to_artifact_key([hint])
            if artifact_key:
                artifact_keys[hint] = artifact_key
        
        return unique_hints, artifact_keys
    
    async def _search_artifact_in_cache_and_neo4j(
        self,
        artifact_key: str,
        cache_layer: CacheLayer,
        hint: str,
        search_tokens: Optional[List[str]] = None
    ) -> Optional[Dict[str, Any]]:
        """Step 4-2: 캐시 → Neo4j 순서로 아티팩트 조회
        
        🔧 수정: 유사도 기반 매칭 사용 (get_with_fallback)
        
        Args:
            artifact_key: 정규화된 아티팩트 키
            cache_layer: 캐시 레이어
            hint: 원본 힌트
            search_tokens: 검색용 토큰 (유사도 매칭용)
        
        Returns:
            캐시된 결과 또는 None
        """
        # 1. 🔧 유사도 기반 캐시 조회 (정확 → fuzzy fallback)
        # 힌트를 리스트로 변환
        hints = [hint] if hint else []
        
        # get_with_fallback 사용 (유사도 매칭 포함!)
        cached_entry, hit_source, score = cache_layer.get_with_fallback(
            hints=hints,
            missing_bindings=[],
            search_tokens=search_tokens or [],
            source_file=""  # 빈 문자열 (None이 아님!)
        )
        
        if cached_entry and score >= 50:  # 유사도 50 이상이면 히트
            # hit_source에서 타입 추출 (exact:memory, fuzzy:sim=0.8 등)
            source_type = "memory" if "memory" in hit_source else "file" if "file" in hit_source else "fuzzy"
            return {
                "source": source_type,
                "artifact_key": cached_entry.key,  # 실제 매칭된 키 반환
                "data": cached_entry.data,
                "score": score
            }
        
        # 2. Neo4j 보조 조회 (이미 존재하는 TABLE/COLUMN 확인)
        from ..tools.neo4j_tools import Neo4jTools
        neo4j_tools = Neo4jTools(self.driver)
        
        # 힌트에서 테이블명 추출 시도
        table_name = None
        if hint.startswith("entity="):
            table_name = hint.split("=", 1)[1].upper()
        
        if table_name:
            query = """
            MATCH (t:TABLE {name: $table_name})
            OPTIONAL MATCH (t)-[:HAS_COLUMN]->(c:COLUMN)
            RETURN t.name AS table_name, collect(c.name) AS columns
            """
            result = await neo4j_tools.query(query, {"table_name": table_name})
            if result.records:
                record = result.records[0]
                return {
                    "source": "neo4j",
                    "artifact_key": artifact_key,
                    "data": {
                        "tables": [{"name": record.get("table_name"), "columns": record.get("columns", [])}]
                    }
                }
        
        return None
    
    def _build_file_groups(
        self, 
        candidates: List[ResolutionCandidate]
    ) -> Dict[str, List[ResolutionCandidate]]:
        """파일 기반 그룹 구성 (권장)
        
        후보를 파일(directory)별로 그룹화하고, 
        중첩된 범위(부모-자식)를 제거합니다.
        
        Args:
            candidates: 전체 후보 목록
        
        Returns:
            {file_path: [candidates]} 형태의 딕셔너리
        """
        log = get_logger()
        
        if not candidates:
            log.custom("후보 없음: 파일 그룹 구성 건너뜀")
            return {}
        
        # 1. 파일별 그룹화
        grouped = self.enqueuer.group_candidates_by_file(candidates)
        log.custom(f"📁 {len(grouped)}개 파일로 그룹화됨")
        
        # 2. 중첩 제거
        deduplicated = self.enqueuer.deduplicate_file_groups(grouped)
        
        total_before = sum(len(c) for c in grouped.values())
        total_after = sum(len(c) for c in deduplicated.values())
        removed = total_before - total_after
        
        if removed > 0:
            log.custom(f"   └─ 중첩 후보 {removed}개 제거됨")
        
        # 파일별 상세 로깅
        for file_path, file_candidates in deduplicated.items():
            total_tokens = sum(c.token or len(c.code_compact) // 4 for c in file_candidates)
            log.custom(f"   • {file_path}: {len(file_candidates)}개 후보, ~{total_tokens} 토큰")
        
        return deduplicated
    
    async def run(
        self,
        context: Optional[Dict[str, Any]] = None,
        artifact_types: Optional[List[str]] = None
    ) -> PhaseResult:
        """
        Phase 실행
        
        Args:
            context: 실행 컨텍스트 (옵션)
            artifact_types: 처리할 아티팩트 타입 필터 (옵션)
        
        Returns:
            PhaseResult
        """
        run_id = str(uuid.uuid4())
        started_at = datetime.now()
        errors = []
        
        # 로거 초기화
        log = get_logger()
        
        # Phase 시작 로깅
        log.phase_start(
            "RuntimeResolution",
            run_id,
            config={
                "max_workers": self.config.max_workers,
                "max_candidates_per_run": self.config.max_candidates_per_run,
                "max_tokens_per_chunk": self.config.max_tokens_per_chunk,
                "min_confidence": self.config.min_confidence,
                "enable_hitl": self.config.enable_hitl,
                "enable_memory_learning": self.config.enable_memory_learning,
            }
        )
        
        # docs 폴더 구조 표시 (Phase 시작 시 한 번만)
        from .main_agent import scan_docs_structure
        docs_structure = scan_docs_structure(self.config.docs_root)
        log.custom("")
        log.custom("=" * 70)
        log.custom("📁 docs 폴더 구조 (외부 문서 탐색 대상)")
        log.custom("=" * 70)
        for line in docs_structure.split("\n"):
            log.custom(f"   {line}")
        log.custom("=" * 70)
        log.custom("")
        
        # 보고서 수집기 초기화
        report_collector = ReportCollector()
        report_collector.start_run(run_id)
        
        # 통계 카운터
        stats = {
            "processed": 0,
            "success": 0,
            "failed": 0,
            "pending_review": 0,
            "evidence_created": 0,
            "ddl_missing": 0,
            "low_confidence": 0,
            "lock_conflicts": 0,
        }
        
        # =========================================================================
        # Phase3 6단계 플로우 시작
        # =========================================================================
        
        # Step 1: 후보 수집 + 정렬
        log.custom("📋 Step 1: Collecting and sorting candidates from Neo4j...")
        candidate_count = await self.enqueuer.enqueue_candidates(
            limit=self.config.max_candidates_per_run,
            artifact_types=artifact_types
        )
        log.candidates_collected(candidate_count)
        
        # Step 2: 큐에서 후보 수집 + 정렬
        log.custom("📋 Step 2: Collecting candidates from queue and sorting...")
        candidates = []
        while not self.queue.is_empty():
            candidate = self.queue.pop()
            if candidate:
                candidates.append(candidate)
        log.candidates_collected(len(candidates), from_queue=True)
        
        # 정렬: directory, file_name, startLine ASC
        candidates = self._sort_candidates(candidates)
        log.custom(f"   ✓ Candidates sorted by directory/file/startLine")
        
        # Step 3: 중첩 제거 (우선순위 METHOD > CLASS > 기타)
        log.custom("🔄 Step 3: Removing nested candidates (METHOD > CLASS > others)...")
        before_count = len(candidates)
        candidates = self._remove_nested_candidates(candidates)
        after_count = len(candidates)
        removed_count = before_count - after_count
        if removed_count > 0:
            log.custom(f"   └─ Removed {removed_count} nested candidates")
        log.custom(f"   ✓ {after_count} candidates remaining after deduplication")
        
        # Step 4: 청크 구성 (≤1000 tokens, 같은 파일 우선)
        log.custom(f"📦 Step 4: Building chunks (≤{self.config.max_tokens_per_chunk} tokens per chunk)...")
        chunks = self._build_chunks(candidates, max_tokens=self.config.max_tokens_per_chunk)
        log.custom(f"   ✓ Built {len(chunks)} chunks")
        
        # 캐시 레이어 초기화 (청크 공통 탐색용)
        # 싱글턴 패턴: main_agent.py와 동일한 인스턴스 공유 (메모리 캐시 공유!)
        cache_dir = os.path.join(self.config.runs_root, "cache")
        os.makedirs(cache_dir, exist_ok=True)
        cache_layer = CacheLayer.get_instance(cache_dir)
        
        # Step 5: 청크별 처리 (공통 탐색 + 노드별 확정)
        log.custom(f"⚡ Step 5: Processing {len(chunks)} chunks (common search + per-node resolution)...")
        log.separator()
        
        candidate_results = []
        
        # 전체 캐시 통계 (모든 청크 누적)
        total_cache_stats = {"memory": 0, "file": 0, "fuzzy": 0, "neo4j": 0, "miss": 0}
        
        async def _process_chunk_batch(
            chunk_id: int, 
            chunk: List[ResolutionCandidate]
        ) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
            """청크 처리 (🚨 LLM 1회 호출로 청크 전체 처리!)
            
            Phase3 핵심 변경: 노드별 개별 LLM 호출 → 청크 단위 일괄 호출
            
            Returns:
                (결과 리스트, 캐시 통계)
            """
            import time
            chunk_start = time.time()
            
            # 청크 시작 시 중복 호출 추적 초기화
            log.reset_duplicate_tracking()
            
            # 청크 정보 로깅
            total_tokens = sum(c.token or len(c.code_compact) // 4 for c in chunk)
            file_paths = list(set(c.file_path for c in chunk if c.file_path))
            log.custom(f"\n{'='*60}")
            log.custom(f"📦 Chunk {chunk_id}: {len(chunk)} candidates, ~{total_tokens} tokens")
            log.custom(f"   Files: {', '.join(file_paths[:3])}{'...' if len(file_paths) > 3 else ''}")
            
            # Step 5-1: 청크 힌트 통합
            unique_hints, artifact_keys = self._merge_chunk_hints(chunk)
            log.custom(f"   Hints: {len(unique_hints)} unique hints → {len(artifact_keys)} artifact_keys")
            
            # Step 5-2: 청크 공통 탐색 (캐시 → Neo4j → docs)
            # 🔧 청크 내 모든 search_tokens 수집 (유사도 매칭용)
            all_search_tokens = set()
            for c in chunk:
                all_search_tokens.update(c.search_tokens or [])
                # missing_bindings에서도 토큰 추출
                for mb in (c.missing_bindings or []):
                    all_search_tokens.add(mb.lower())
            
            chunk_search_results = {}
            cache_stats = {"memory": 0, "file": 0, "fuzzy": 0, "neo4j": 0, "miss": 0}
            
            for hint in unique_hints:
                artifact_key = artifact_keys.get(hint)
                if artifact_key and artifact_key not in chunk_search_results:
                    # 🔧 search_tokens 전달하여 유사도 매칭 지원
                    result = await self._search_artifact_in_cache_and_neo4j(
                        artifact_key, cache_layer, hint,
                        search_tokens=list(all_search_tokens)
                    )
                    source = result.get("source", "miss") if result else "miss"
                    matched_key = result.get("artifact_key", artifact_key) if result else artifact_key
                    score = result.get("score", 0) if result else 0
                    
                    # 캐시 통계 수집
                    if source in cache_stats:
                        cache_stats[source] += 1
                    else:
                        cache_stats["miss"] += 1
                    
                    # 상세 로그 (아이콘으로 히트/미스 구분)
                    if source == "memory":
                        log.custom(f"   ✅ CACHE HIT (memory): {artifact_key}")
                    elif source == "file":
                        log.custom(f"   📁 CACHE HIT (file): {artifact_key}")
                    elif source == "fuzzy":
                        log.custom(f"   🔍 CACHE HIT (fuzzy, score={score:.0f}): {artifact_key} → {matched_key}")
                    elif source == "neo4j":
                        log.custom(f"   🔗 NEO4J HIT: {artifact_key}")
                    else:
                        log.custom(f"   ❌ CACHE MISS: {artifact_key}")
                    
                    if result:
                        chunk_search_results[artifact_key] = result
                        
                        # 청크 공통 탐색 결과를 영속 캐시에도 저장 (청크 간 공유!)
                        if source != "memory":  # 이미 메모리 캐시에 있는 것이 아니면
                            cache_layer.set(artifact_key, result.get("data", {}))
            
            # 청크별 캐시 통계 요약
            log.custom(f"   📊 Cache Stats: ✅memory={cache_stats['memory']} | 📁file={cache_stats['file']} | 🔍fuzzy={cache_stats['fuzzy']} | 🔗neo4j={cache_stats['neo4j']} | ❌miss={cache_stats['miss']}")
            
            # ═══════════════════════════════════════════════════════════════════
            # 🚨 핵심 변경: LLM 1회 호출로 청크 전체 처리!
            # ═══════════════════════════════════════════════════════════════════
            
            # 청크 내 모든 후보를 딕셔너리로 변환
            chunk_dicts = [candidate.to_dict() for candidate in chunk]
            
            # 보고서: 청크 처리 시작
            for candidate in chunk:
                report_collector.start_candidate({
                    "element_id": candidate.element_id,
                    "external_hints": candidate.external_hints,
                    "missing_bindings": candidate.missing_bindings,
                    "file_path": candidate.file_path,
                    "line_start": candidate.line_start,
                    "line_end": candidate.line_end,
                })
            
            # 🚀 청크 전체를 LLM에게 한 번에 전달!
            log.custom(f"   🚀 LLM 호출 (청크 {chunk_id}: {len(chunk)}개 후보 일괄 처리)")
            
            callbacks = [report_collector] if self.config.enable_report else None
            try:
                result = await process_chunk(
                    self.agent,
                    chunk_dicts,
                    run_id,  # 🔑 전체 실행에서 하나의 run_id 사용
                    chunk_id=chunk_id,
                    docs_root=self.config.docs_root,
                    safety_instances=self.safety_instances,
                    callbacks=callbacks,
                    cached_artifacts=chunk_search_results,
                )
                
                # 결과 분석 (청크 전체 결과)
                result_status, result_data = self._parse_agent_result(result, stats)
                
                # 플랜/TODO 로깅
                if result_data.get("plan"):
                    log.log_plan(result_data["plan"])
                
                if result_data.get("todos"):
                    log.log_todo_list(result_data["todos"])
                    report_collector.record_todos(result_data["todos"])
                
                stats["processed"] += len(chunk)
                
            except Exception as e:
                error_str = str(e)
                log.custom(f"   ❌ 청크 처리 실패: {error_str}")
                stats["failed"] += len(chunk)
                result_status = "failed"
                result_data = {"error": error_str}
            
            # 결과 생성 (각 후보별로)
            results = []
            success_count = 0
            pending_count = 0
            
            for candidate in chunk:
                # 청크 결과에서 개별 후보 상태 추출 (기본: success)
                candidate_status = result_status if result_status != "unknown" else "success"
                
                results.append({
                    "element_id": candidate.element_id,
                    "status": candidate_status,
                    "chunk_id": chunk_id,
                    "data": result_data,
                })
                
                if candidate_status == "success":
                    success_count += 1
                    stats["success"] += 1  # 🔧 전역 통계에도 반영
                elif candidate_status == "pending_review":
                    pending_count += 1
                    stats["pending_review"] += 1  # 🔧 전역 통계에도 반영
            
            # 청크 완료 로깅
            duration = time.time() - chunk_start
            log.custom(f"   ✓ Chunk {chunk_id} completed: {success_count} success, {pending_count} pending ({duration:.2f}s)")
            log.custom(f"   💡 LLM 호출: 1회 (청크 전체)")
            log.custom(f"{'='*60}")
            
            return results, cache_stats
        
        # 청크 순차 처리 (Step 5-6) - 🚨 청크당 LLM 1회 호출!
        for chunk_idx, chunk in enumerate(chunks, 1):
            try:
                chunk_results, chunk_cache_stats = await _process_chunk_batch(chunk_idx, chunk)
                candidate_results.extend(chunk_results)
                
                # 캐시 통계 누적
                for key in total_cache_stats:
                    total_cache_stats[key] += chunk_cache_stats.get(key, 0)
            except Exception as e:
                errors.append(f"Chunk {chunk_idx} error: {str(e)}")
                stats["failed"] += len(chunk)
        
        completed_at = datetime.now()
        
        # Phase 종료 로깅
        duration = (completed_at - started_at).total_seconds()
        log.phase_end(stats, duration)
        
        # 전체 캐시 통계 출력
        total_lookups = sum(total_cache_stats.values())
        total_hits = total_cache_stats["memory"] + total_cache_stats["file"] + total_cache_stats["fuzzy"] + total_cache_stats["neo4j"]
        hit_rate = (total_hits / total_lookups * 100) if total_lookups > 0 else 0
        log.custom(f"\n{'='*60}")
        log.custom(f"📊 TOTAL CACHE STATISTICS")
        log.custom(f"   ✅ Memory Hits: {total_cache_stats['memory']}")
        log.custom(f"   📁 File Hits:   {total_cache_stats['file']}")
        log.custom(f"   🔍 Fuzzy Hits:  {total_cache_stats['fuzzy']}")
        log.custom(f"   🔗 Neo4j Hits:  {total_cache_stats['neo4j']}")
        log.custom(f"   ❌ Misses:      {total_cache_stats['miss']}")
        log.custom(f"   📈 Hit Rate:    {hit_rate:.1f}%")
        log.custom(f"{'='*60}")
        
        # 5. 보고서 생성 및 저장
        if self.config.enable_report:
            report = report_collector.build_report(run_id)
            report.total = candidate_count
            report.success = stats["success"]
            report.failed = stats["failed"]
            report.pending_review = stats["pending_review"]
            report.evidence_created = stats["evidence_created"]
            report.ddl_missing = stats["ddl_missing"]
            report.lock_conflicts = stats["lock_conflicts"]
            report.errors = errors
            
            # 보고서 저장
            os.makedirs(self.config.reports_root, exist_ok=True)
            saved_paths = report.save(self.config.reports_root)
            
            # 결과에 보고서 경로 추가
            report_paths = saved_paths
        else:
            report_paths = {}
        
        return PhaseResult(
            total_candidates=candidate_count,
            processed=stats["processed"],
            success=stats["success"],
            failed=stats["failed"],
            pending_review=stats["pending_review"],
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=(completed_at - started_at).total_seconds(),
            errors=errors,
            evidence_created=stats["evidence_created"],
            ddl_missing=stats["ddl_missing"],
            low_confidence=stats["low_confidence"],
            lock_conflicts=stats["lock_conflicts"],
        )
    
    def _parse_agent_result(
        self, 
        result: Dict[str, Any], 
        stats: Dict[str, int]
    ) -> tuple:
        """에이전트 응답을 구조화된 방식으로 파싱
        
        Returns:
            (status, result_data) 튜플
        """
        messages = result.get("messages", [])
        
        # 결과 데이터 수집
        result_data = {
            "tool_calls": [],
            "tables_found": [],
            "entities_found": [],
            "patch_id": None,
            "evidence_id": None,
        }
        
        # 모든 메시지에서 도구 호출 추적
        for msg in messages:
            # AIMessage의 tool_calls 속성 확인
            if hasattr(msg, 'tool_calls') and msg.tool_calls:
                for tc in msg.tool_calls:
                    tool_name = tc.get('name', '') if isinstance(tc, dict) else getattr(tc, 'name', '')
                    tool_args = tc.get('args', {}) if isinstance(tc, dict) else getattr(tc, 'args', {})
                    result_data["tool_calls"].append({
                        "tool": tool_name,
                        "args": tool_args,
                    })
                    
                    # 특정 도구 결과에서 정보 추출 (플랜 기반)
                    if tool_name == "apply_graph_mutations":
                        result_data["mutations_applied"] = True
                    if tool_name == "create_evidence":
                        result_data["evidence_id"] = tool_args.get("patch_id")
                    
                    # 테이블 관련 도구에서 테이블명 추출
                    if tool_name in ["check_table_exists", "check_ddl_exists", 
                                     "create_table_schema", "enrich_table_schema", 
                                     "normalize_table_name"]:
                        table_name = tool_args.get("table_name", "")
                        if table_name and len(table_name) > 2:  # 최소 3자 이상
                            result_data["tables_found"].append(table_name.upper())
                    
                    # DDL 체크 결과에서 추출
                    if tool_name == "add_fk_relationship":
                        from_table = tool_args.get("from_table", "")
                        to_table = tool_args.get("to_table", "")
                        if from_table:
                            result_data["tables_found"].append(from_table.upper())
                        if to_table:
                            result_data["tables_found"].append(to_table.upper())
            
            # content에서도 정보 추출 (더 엄격한 패턴)
            content = getattr(msg, 'content', str(msg)) if msg else ""
            
            # 테이블 추출: 대문자+언더스코어 패턴 (최소 4자, 숫자로 시작 안함)
            if content:
                import re
                # 명확한 테이블명 패턴: ORDER_HEADER, CUSTOMER 등
                table_patterns = [
                    r'table[_\s=:]*["\']?([A-Z][A-Z0-9_]{2,})["\']?',  # table=ORDER_HEADER
                    r'테이블[:\s]*["\']?([A-Z][A-Z0-9_]{2,})["\']?',   # 테이블: ORDER_HEADER
                    r'TABLE_NAME[:\s=]*["\']?([A-Z][A-Z0-9_]{2,})["\']?',  # TABLE_NAME=ORDER_HEADER
                ]
                for pattern in table_patterns:
                    tables = re.findall(pattern, content)
                    for t in tables:
                        if len(t) >= 3 and not t.startswith('_'):
                            result_data["tables_found"].append(t.upper())
            
            # 엔티티 추출
            if "entity" in content.lower() or "엔티티" in content:
                import re
                # 엔티티명: CamelCase 패턴
                entity_patterns = [
                    r'entity[_\s=:\-]*["\']?([A-Z][A-Za-z0-9]+(?:[A-Z][a-z0-9]*)*)["\']?',
                    r'entity-name[=:]*["\']([A-Z][A-Za-z0-9]+)["\']',
                ]
                for pattern in entity_patterns:
                    entities = re.findall(pattern, content)
                    for e in entities:
                        if len(e) >= 3:
                            result_data["entities_found"].append(e)
        
        # 중복 제거
        result_data["tables_found"] = list(set(result_data["tables_found"]))
        result_data["entities_found"] = list(set(result_data["entities_found"]))
        
        # 전체 대화 내용에서 상태 판단 (마지막 메시지만 보면 누락될 수 있음)
        all_content = ""
        for msg in messages:
            msg_content = getattr(msg, 'content', str(msg))
            all_content += " " + msg_content
        all_content_lower = all_content.lower()
        
        # 마지막 메시지
        last_message = messages[-1] if messages else None
        status = "pending"  # 기본값을 pending으로 변경 (failed가 아님)
        
        if last_message:
            content = getattr(last_message, 'content', str(last_message))
            
            # 구조화된 결과 JSON 파싱 시도
            import json
            try:
                # 마지막 메시지가 JSON 형태인 경우
                if content.strip().startswith('{'):
                    parsed = json.loads(content)
                    if parsed.get("status"):
                        status = parsed["status"]
                        result_data.update(parsed)
            except json.JSONDecodeError:
                pass
        
        # 전체 대화에서 상태 키워드 검색 (우선순위 순)
        if status == "pending":
            # 1. 성공 (committed, active 승격)
            if any(kw in all_content_lower for kw in ["committed", "active로 승격", "커밋 완료", "success"]):
                status = "success"
                stats["success"] += 1
            
            # 2. 검토 대기 (review queue에 추가됨)
            elif any(kw in all_content_lower for kw in [
                "added to review queue", "review queue", "pending_review", 
                "검토 대기열", "검토가 필요", "사람의 확인"
            ]):
                status = "pending_review"
                stats["pending_review"] += 1
                
                # 세부 사유 분류
                if any(kw in all_content_lower for kw in ["ddl missing", "ddl 없", "테이블 없"]):
                    stats["ddl_missing"] += 1
                if any(kw in all_content_lower for kw in ["confidence", "신뢰도", "low_confidence"]):
                    stats["low_confidence"] += 1
            
            # 3. Evidence 생성됨 (중간 상태)
            elif any(kw in all_content_lower for kw in ["evidence created", "증거 생성", "증거 파일"]):
                stats["evidence_created"] += 1
                # evidence만 있고 커밋 안됐으면 pending_review
                status = "pending_review"
                stats["pending_review"] += 1
            
            # 4. 명시적 실패
            elif any(kw in all_content_lower for kw in ["error", "실패", "failed", "exception"]):
                status = "failed"
                stats["failed"] += 1
            
            # 5. 분류 불가 → pending_review (안전하게)
            else:
                status = "pending_review"
                stats["pending_review"] += 1
        
        # 플랜과 TODO 파싱 (에이전트 상태 + LLM 응답에서 추출)
        # deepagents는 state에 todos를 저장하므로 result에서 가져옴
        agent_state = result.get("state", result)  # state가 없으면 result 자체가 state일 수 있음
        plan_and_todos = self._extract_plan_and_todos(all_content, state=agent_state)
        result_data["plan"] = plan_and_todos.get("plan", "")
        result_data["todos"] = plan_and_todos.get("todos", [])
        
        return status, result_data
    
    def _extract_plan_and_todos(self, content: str, state: Optional[Dict] = None) -> Dict[str, Any]:
        """LLM 응답 및 에이전트 상태에서 플랜과 TODO 목록을 추출
        
        Args:
            content: 전체 대화 내용
            state: 에이전트 상태 (deepagents의 todos 포함 가능)
        
        Returns:
            {"plan": "...", "todos": [{"id": 1, "content": "...", "status": "done|pending"}]}
        """
        import re
        
        result = {
            "plan": "",
            "todos": []
        }
        
        # 1. deepagents 상태에서 todos 직접 추출 (우선)
        if state and isinstance(state, dict):
            state_todos = state.get("todos", [])
            if state_todos and isinstance(state_todos, list):
                for i, todo in enumerate(state_todos, 1):
                    if isinstance(todo, dict):
                        # deepagents 형식: content, status 필드
                        todo_content = todo.get("content", todo.get("description", todo.get("text", "")))
                        todo_status = todo.get("status", "pending")
                        if todo_content:  # 내용이 있는 경우만
                            result["todos"].append({
                                "id": i,
                                "content": todo_content,
                                "status": todo_status
                            })
                    elif isinstance(todo, str) and todo.strip():
                        result["todos"].append({
                            "id": i,
                            "content": todo,
                            "status": "pending"
                        })
        
        # 2. 플랜 추출: "📋 처리 플랜:" 또는 "플랜:" 이후 내용
        plan_patterns = [
            r'📋\s*처리\s*플랜[:\s]*\n?(.*?)(?=📝|TODO|$)',
            r'플랜[:\s]*\n?(.*?)(?=📝|TODO|$)',
            r'목표[:\s]*([^\n]+)',
            r'계획[:\s]*([^\n]+)',
        ]
        for pattern in plan_patterns:
            match = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
            if match:
                result["plan"] = match.group(1).strip()[:500]  # 최대 500자
                break
        
        # 3. TODO 추출 (상태에서 못 찾은 경우만): 다양한 패턴 지원
        if not result["todos"]:
            # 패턴 1: "1. [ ] 내용", "1. [x] 내용", "1. [○] 내용" 등
            todo_patterns = [
                r'(\d+)\.\s*\[([^\]]*)\]\s*([^\n]+)',  # 모든 괄호 내용 허용
                r'[-•]\s*\[([^\]]*)\]\s*([^\n]+)',     # 불릿 포인트
                r'TODO\s*(\d+)[:\s]*([^\n]+)',         # TODO 1: 내용
            ]
            
            for pattern in todo_patterns:
                todo_matches = re.findall(pattern, content)
                if todo_matches:
                    for match in todo_matches:
                        if len(match) == 3:
                            # 패턴: (번호, 상태, 내용)
                            todo_id = int(match[0]) if match[0].isdigit() else len(result["todos"]) + 1
                            status_marker = match[1].strip().lower()
                            description = match[2].strip()
                        elif len(match) == 2:
                            # 패턴: (상태, 내용) 또는 (번호, 내용)
                            todo_id = len(result["todos"]) + 1
                            if match[0].isdigit():
                                description = match[1].strip()
                                status_marker = ""
                            else:
                                status_marker = match[0].strip().lower()
                                description = match[1].strip()
                        else:
                            continue
                        
                        # 상태 판단
                        is_done = any(m in status_marker for m in ['x', '✓', '✅', 'done', 'complete', '완료'])
                        is_progress = any(m in status_marker for m in ['○', '●', 'progress', '진행'])
                        
                        if description:  # 내용이 있는 경우만
                            result["todos"].append({
                                "id": todo_id,
                                "content": description,
                                "status": "done" if is_done else ("in_progress" if is_progress else "pending")
                            })
                    break  # 첫 번째 매칭 패턴만 사용
        
        return result
    
    def _extract_neo4j_work_summary(self, result_data: Dict[str, Any]) -> Dict[str, Any]:
        """도구 호출에서 Neo4j 작업 요약 추출
        
        Args:
            result_data: 파싱된 결과 데이터 (tool_calls 포함)
        
        Returns:
            {"has_work": bool, "relationships": int, "nodes_created": int, "nodes_updated": int}
        """
        summary = {
            "has_work": False,
            "relationships": 0,
            "nodes_created": 0,
            "nodes_updated": 0,
            "relationship_details": [],
            "node_details": [],
        }
        
        tool_calls = result_data.get("tool_calls", [])
        
        for tc in tool_calls:
            tool_name = tc.get("tool", "")
            args = tc.get("args", {})
            
            # 관계/노드 생성 도구 (플랜 기반)
            if tool_name == "apply_graph_mutations":
                mutations = args.get("mutations", [])
                if isinstance(mutations, str):
                    import json
                    try:
                        mutations = json.loads(mutations)
                    except:
                        mutations = []
                
                for mut in mutations:
                    mut_type = mut.get("type", "")
                    if mut_type.startswith("MERGE_"):
                        if mut_type in ("MERGE_FROM", "MERGE_WRITES"):
                            summary["relationships"] += 1
                            detail = {
                                "type": mut_type,
                                "rel_type": "from" if mut_type == "MERGE_FROM" else "writes",
                                "source": str(mut.get("from_element_id", "?"))[:20],
                                "target": mut.get("to_table", "?")
                            }
                            summary["relationship_details"].append(detail)
                        elif mut_type == "MERGE_TABLE":
                            summary["nodes_created"] += 1
                            detail = {"type": "MERGE_TABLE", "node_type": "TABLE", "name": mut.get("table", "?")}
                            summary["node_details"].append(detail)
                
                summary["has_work"] = True
            
            elif tool_name == "add_fk_relationship":
                summary["relationships"] += 1
                summary["has_work"] = True
                detail = {
                    "type": tool_name,
                    "rel_type": "FK",
                    "source": f"{args.get('from_table', '?')}.{args.get('fk_column', '?')}",
                    "target": args.get("to_table", "?")
                }
                summary["relationship_details"].append(detail)
            
            # 노드 생성 도구
            elif tool_name == "create_table_schema":
                summary["nodes_created"] += 1
                summary["has_work"] = True
                detail = {"type": tool_name, "node_type": "Table", "name": args.get("table_name", "?")}
                summary["node_details"].append(detail)
            
            # 노드 업데이트 도구
            elif tool_name in ("enrich_table_schema",):
                summary["nodes_updated"] += 1
                summary["has_work"] = True
                
                detail = {"type": tool_name}
                detail["node_type"] = "Table"
                detail["name"] = args.get("table_name", "?")
                summary["node_details"].append(detail)
        
        return summary
    
    async def run_single(
        self,
        element_id: str,
        external_hints: Optional[List[str]] = None,
        artifact_type: str = "unknown_candidate",
        confidence: float = 0.5,
    ) -> Dict[str, Any]:
        """
        단일 후보 처리
        
        Args:
            element_id: 노드 elementId
            external_hints: 외부 힌트 목록
            artifact_type: 아티팩트 타입
            confidence: 신뢰도
        
        Returns:
            처리 결과
        """
        candidate = {
            "element_id": element_id,
            "external_hints": external_hints or [],
            "artifact_type": artifact_type,
            "confidence": confidence,
            "file_path": "",
            "line_start": 0,
            "line_end": 0,
        }
        
        run_id = str(uuid.uuid4())
        return await process_candidate(
            self.agent, 
            candidate, 
            run_id,
            docs_root=self.config.docs_root,
            safety_instances=self.safety_instances,
        )
    
    async def get_pending_reviews(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        리뷰 필요 노드 조회 (노드 속성 기반)
        
        플랜 규칙: ReviewQueue 노드 대신 deepagents_status='review' 속성 사용
        
        Args:
            limit: 최대 조회 수
        
        Returns:
            리뷰 필요 노드 목록
        """
        from ..tools.neo4j_tools import Neo4jTools
        neo4j_tools = Neo4jTools(self.driver)
        
        query = """
        MATCH (n)
        WHERE n.deepagents_status = 'review'
        RETURN elementId(n) AS element_id,
               labels(n)[0] AS node_type,
               n.deepagents_status_reason AS reason,
               n.deepagents_evidence_path AS evidence_path,
               n.deepagents_last_processed_at AS last_processed
        ORDER BY n.deepagents_last_processed_at DESC
        LIMIT $limit
        """
        
        result = await neo4j_tools.query(query, {"limit": limit})
        return result.records
    
    async def approve_review(self, element_id: str, reviewer: str) -> bool:
        """
        리뷰 승인 → 재처리 트리거
        
        플랜 규칙: status='working'으로 변경하여 재처리 대상에 포함
        
        Args:
            element_id: 노드 ID
            reviewer: 승인자
        
        Returns:
            성공 여부
        """
        from ..tools.neo4j_tools import Neo4jTools
        neo4j_tools = Neo4jTools(self.driver)
        
        query = """
        MATCH (n) WHERE elementId(n) = $element_id
        SET n.deepagents_status = 'working',
            n.deepagents_reviewed_by = $reviewer,
            n.deepagents_reviewed_at = datetime()
        RETURN n
        """
        
        result = await neo4j_tools.query(query, {
            "element_id": element_id,
            "reviewer": reviewer
        })
        return len(result.records) > 0
    
    async def reject_review(self, element_id: str, reviewer: str, reason: str) -> bool:
        """
        리뷰 거부 → 실패 처리
        
        Args:
            element_id: 노드 ID
            reviewer: 거부자
            reason: 거부 사유
        
        Returns:
            성공 여부
        """
        from ..tools.neo4j_tools import Neo4jTools
        neo4j_tools = Neo4jTools(self.driver)
        
        return (await neo4j_tools.set_status_failed(element_id, f"rejected:{reason}"))["updated"]
    
    def get_stats(self) -> Dict[str, Any]:
        """통계 조회"""
        return {
            "queue": self.queue.get_stats(),
            "active_locks": len(self.lock._locks) if hasattr(self.lock, '_locks') else 0,
        }
    
    async def get_review_stats(self) -> Dict[str, Any]:
        """리뷰 통계 조회 (노드 속성 기반)"""
        from ..tools.neo4j_tools import Neo4jTools
        neo4j_tools = Neo4jTools(self.driver)
        
        query = """
        MATCH (n)
        WHERE n.deepagents_status = 'review'
        RETURN count(n) AS pending_count
        """
        
        result = await neo4j_tools.query(query, {})
        pending_count = result.records[0].get("pending_count", 0) if result.records else 0
        
        return {
            "pending_review_count": pending_count,
        }


# FrameworkAnalyzer 통합 헬퍼
async def run_runtime_resolution_phase(
    neo4j_driver: Any,
    docs_root: str,
    ddl_root: str,
    runs_root: str = "data/runs",
    artifact_types: Optional[List[str]] = None,
    max_workers: int = 4,
    max_candidates: int = 1000,
    enable_hitl: bool = True,
) -> PhaseResult:
    """
    런타임 해석 Phase 실행 (간편 함수)
    
    Args:
        neo4j_driver: Neo4j 드라이버
        docs_root: 외부 설정 파일 루트 (data/docs)
        ddl_root: DDL 파일 루트 (data/ddl)
        runs_root: 작업 워크스페이스 루트 (data/runs)
        artifact_types: 처리할 아티팩트 타입 (옵션)
        max_workers: 최대 워커 수
        max_candidates: 최대 후보 수
        enable_hitl: Human-in-the-Loop 활성화
    
    Returns:
        PhaseResult
    """
    config = PhaseConfig(
        docs_root=docs_root,
        ddl_root=ddl_root,
        runs_root=runs_root,
        max_workers=max_workers,
        max_candidates_per_run=max_candidates,
        enable_hitl=enable_hitl,
    )
    
    phase = RuntimeResolutionPhase(neo4j_driver, config)
    return await phase.run(artifact_types=artifact_types)


async def process_pending_reviews(
    neo4j_driver: Any,
    docs_root: str = "data/docs",
    ddl_root: str = "data/ddl",
    runs_root: str = "data/runs",
    auto_approve_threshold: float = 0.9,
) -> Dict[str, Any]:
    """
    보류 항목 처리 (자동 승인/수동 검토용)
    
    Args:
        neo4j_driver: Neo4j 드라이버
        docs_root: 외부 설정 파일 루트
        ddl_root: DDL 파일 루트
        runs_root: 작업 워크스페이스 루트
        auto_approve_threshold: 자동 승인 임계값 (이 이상이면 자동 승인)
    
    Returns:
        처리 결과 (auto_approved, pending_manual, rejected)
    """
    config = PhaseConfig(
        docs_root=docs_root,
        ddl_root=ddl_root,
        runs_root=runs_root,
    )
    
    phase = RuntimeResolutionPhase(neo4j_driver, config)
    
    # 보류 항목 조회
    pending_items = await phase.get_pending_reviews()
    
    results = {
        "auto_approved": 0,
        "pending_manual": 0,
        "processed": [],
    }
    
    for item in pending_items:
        confidence = item.get("confidence", 0.0)
        reasons = item.get("reasons", [])
        
        # 🚨 DDL 미존재도 자동 승인 가능 (새 정책!)
        # DDL 없으면 오히려 DeepAgents가 스키마를 만들어야 함
        
        # 높은 confidence는 자동 승인 (DDL 여부 무관)
        if confidence >= auto_approve_threshold:
            success = await phase.approve_review(
                patch_id=item["patch_id"],
                reviewer="auto_approve_system",
            )
            if success:
                results["auto_approved"] += 1
                results["processed"].append({
                    "patch_id": item["patch_id"],
                    "action": "auto_approved",
                })
            else:
                results["pending_manual"] += 1
        else:
            results["pending_manual"] += 1
    
    return results
