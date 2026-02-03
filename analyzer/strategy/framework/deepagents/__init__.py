"""
DeepAgents 런타임 해석 모듈 (FSM 기반)

Java 프레임워크의 동적 참조를 런타임에 해석합니다.

플랜 기반 안전장치:
- EvidenceManager: evidence 파일 관리 + dedup/merge
- MinimalLock: 최소 동시성 락 (table_name/elementId 단위)
- RuntimePolicy: 코드 기반 가드레일 (30KB limit, 10회 상한)
- CacheLayer: 3계층 캐시 (인메모리 → 파일 → 영속)
- FSMExecutor: 후보 처리 상태 머신

DBMS 표준 스키마:
- TABLE PK: {db, schema, name}
- COLUMN PK: {fqn}
- 관계: FROM, WRITES, HAS_COLUMN, FK_TO_TABLE, FK_TO

최우선 규칙:
1. DDL 없어도 무조건 생성
2. 테이블 발견되면 FROM/WRITES 무조건 생성
3. 소스 파일(.java) 탐색 절대 금지

금지 사항:
- REFERENCES 관계 금지 (FK_TO_TABLE 사용)
- 소스 코드 파일 docs 탐색 금지
- ReviewQueue/Artifact/RESOLVES_TO 노드/관계 금지

사용 예시:
    from analyzer.strategy.framework.deepagents import (
        RuntimeResolutionPhase,
        PhaseConfig,
        create_runtime_resolution_agent,
    )
    
    # Phase 실행
    phase = RuntimeResolutionPhase(neo4j_driver, config)
    result = await phase.run()
    
    # 리뷰 필요 노드 조회 (노드 속성 기반)
    pending = await phase.get_pending_reviews()

필수 설치:
    pip install deepagents langgraph
"""

# 공식 deepagents 라이브러리
from deepagents import create_deep_agent, CompiledSubAgent
from deepagents.backends import (
    CompositeBackend,
    StateBackend,
    FilesystemBackend,
    StoreBackend,
)

# 핵심 모듈 (Phase 실행)
from .phase import (
    create_runtime_resolution_agent,
    process_candidate,
    resume_after_hitl,
    RuntimeResolutionPhase,
    PhaseConfig,
    PhaseResult,
    run_runtime_resolution_phase,
    process_pending_reviews,
    FSMExecutor,
    FSMState,
    FSMContext,
)

# 데이터 관리
from .data import (
    CandidateEnqueuer,
    EvidenceManager,
    EvidenceData,
    EvidenceRequiredError,
    require_evidence,
    create_evidence_tool,
)

# 지원 기능
from .support import (
    RuntimePolicy,
    PolicyLimits,
    POLICY,
    validate_slice_limit,
    should_review,
    should_apply_writes,
    MinimalLock,
    GLOBAL_LOCK,
    acquire_table_lock,
    release_table_lock,
    acquire_element_lock,
    release_element_lock,
    CacheLayer,
    CacheEntry,
    hints_to_artifact_key,
    needs_namespace_lookup,
)

# 도우미
from .helpers import (
    PriorityQueue,
    ResolutionCandidate,
    DeepAgentsLogger,
    get_logger,
    configure_logger,
)

# 도구
from .tools.docs_tools import (
    DocsTools,
    SearchResult,
    SEARCH_SCOPES,
    create_docs_tools,
)
from .tools.neo4j_tools import Neo4jTools
from .tools.neo4j_schema import SchemaManager

# 파서
from .parsers.mybatis_parser import MyBatisParser
from .parsers.ofbiz_parser import OFBizParser
from .parsers.jpa_parser import JPAParser
from .parsers.yaml_parser import YAMLParser

# 리포트
from .reports.resolution_report import (
    ResolutionReport,
    CandidateReport,
    StepReport,
    ToolCallReport,
    ReportCollector,
    create_report_from_phase_result,
)

__all__ = [
    # 공식 라이브러리
    "create_deep_agent",
    "CompiledSubAgent",
    "CompositeBackend",
    "StateBackend",
    "FilesystemBackend",
    "StoreBackend",
    
    # 핵심 API
    "create_runtime_resolution_agent",
    "process_candidate",
    "resume_after_hitl",
    "RuntimeResolutionPhase",
    "PhaseConfig",
    "PhaseResult",
    "run_runtime_resolution_phase",
    "process_pending_reviews",
    
    # 후보 관리
    "CandidateEnqueuer",
    "PriorityQueue",
    "ResolutionCandidate",
    
    # 안전장치 - Evidence
    "EvidenceManager",
    "EvidenceData",
    "EvidenceRequiredError",
    "require_evidence",
    "create_evidence_tool",
    
    # RuntimePolicy (가드레일)
    "RuntimePolicy",
    "PolicyLimits",
    "POLICY",
    "validate_slice_limit",
    "should_review",
    "should_apply_writes",
    
    # MinimalLock (동시성 락)
    "MinimalLock",
    "GLOBAL_LOCK",
    "acquire_table_lock",
    "release_table_lock",
    "acquire_element_lock",
    "release_element_lock",
    
    # CacheLayer (3계층 캐시)
    "CacheLayer",
    "CacheEntry",
    "hints_to_artifact_key",
    "needs_namespace_lookup",
    
    # DocsTools (문서 검색/슬라이싱)
    "DocsTools",
    "SearchResult",
    "SEARCH_SCOPES",
    "create_docs_tools",
    
    # FSMExecutor (상태 머신)
    "FSMExecutor",
    "FSMState",
    "FSMContext",
    
    # 파서
    "MyBatisParser",
    "OFBizParser",
    "JPAParser",
    "YAMLParser",
    
    # Neo4j 도구
    "Neo4jTools",
    "SchemaManager",
    
    # 모니터링/보고서
    "ResolutionReport",
    "CandidateReport",
    "StepReport",
    "ToolCallReport",
    "ReportCollector",
    "create_report_from_phase_result",
    
    # 로깅
    "DeepAgentsLogger",
    "get_logger",
    "configure_logger",
]
