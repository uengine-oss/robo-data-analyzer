"""DBMS 파이프라인 상태 + Phase 컨텍스트 (Framework PhaseContext 와 동일 형태로 통일)

DbmsPipelineState: 파이프라인 전체 상태 (Phase 간 데이터 전달 + 동시성 제어 객체)
DbmsPhaseContext: 각 Phase 함수에 전달되는 공용 컨텍스트
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from analyzer.strategy.base_analyzer import AnalysisStats
from analyzer.strategy.dbms.common.models.node_models import FileAnalysisContext


@dataclass
class DbmsPipelineState:
    """DBMS 파이프라인 상태 (모든 필드 명시적).

    Phase 간 데이터 전달 + 동시성 제어 객체(파이프라인 시작 시점에 초기화).
    """
    ddl_schemas: Set[str] = field(default_factory=set)
    ddl_table_metadata: Dict[Tuple[str, str], Dict[str, Any]] = field(default_factory=dict)
    # DDL 적재 후 빌드되어 Phase 2B 의 dml_extract LLM 프롬프트 ddl_context 슬롯 주입에 사용.
    # key = 테이블명 (대문자 정규화), value = {schema, description, columns: [{name, dtype, description, nullable}]}
    ddl_table_index: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # ddl_table_index 의 테이블명들로 컴파일된 단일 정규식 패턴 (alternation, \b, IGNORECASE).
    # ddl_table_matcher.match_in_code 가 range 별 단어 매칭에 사용. 비어있으면 None.
    ddl_pattern: Optional[Any] = None
    contexts: List[FileAnalysisContext] = field(default_factory=list)

    # 파일별 동시 처리 제한 — DbmsPipeline.run_pipeline 진입 시 채워짐.
    # Neo4j 쓰기 충돌은 client.run_graph_query 의 managed transaction 이 자동 재시도 → 락 X.
    file_semaphore: Optional[asyncio.Semaphore] = None


@dataclass
class DbmsPhaseContext:
    """DBMS Phase 함수에 전달되는 공용 컨텍스트.

    state 단일 출처 — 누적 상태 / 동시성 제어 모두 state 안에 두고 직접 참조.
    """
    client: Any = None  # Neo4jClient
    orchestrator: Any = None
    state: Optional[DbmsPipelineState] = None
    stats: Any = None  # AnalysisStats

    # state 패스스루 — phase 코드의 시인성 향상
    @property
    def file_semaphore(self) -> asyncio.Semaphore:
        return self.state.file_semaphore

    @property
    def ddl_schemas(self) -> Set[str]:
        return self.state.ddl_schemas

    @property
    def ddl_table_metadata(self) -> Dict[Tuple[str, str], Dict[str, Any]]:
        return self.state.ddl_table_metadata

    @property
    def ddl_table_index(self) -> Dict[str, Dict[str, Any]]:
        return self.state.ddl_table_index

    @property
    def ddl_pattern(self) -> Optional[Any]:
        return self.state.ddl_pattern

    # AnalysisContext (orchestrator) 의 필드는 service 진입점에서 검증되어 항상 존재.
    @property
    def api_key(self) -> str:
        return self.orchestrator.api_key

    @property
    def locale(self) -> str:
        return self.orchestrator.locale

    @property
    def target(self) -> str:
        return self.orchestrator.target

    @property
    def name_case(self) -> str:
        return self.orchestrator.name_case

    @property
    def datasource_name(self) -> str:
        return self.orchestrator.datasource_name

    @classmethod
    def create(
        cls,
        client: Any,
        orchestrator: Any,
        stats: AnalysisStats,
        file_semaphore: asyncio.Semaphore,
    ) -> "DbmsPhaseContext":
        return cls(
            client=client,
            orchestrator=orchestrator,
            state=DbmsPipelineState(file_semaphore=file_semaphore),
            stats=stats,
        )
