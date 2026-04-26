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
    contexts: List[FileAnalysisContext] = field(default_factory=list)

    # 동시성 제어 — DbmsPipeline.run_pipeline 진입 시 채워짐
    cypher_lock: Optional[asyncio.Lock] = None
    file_semaphore: Optional[asyncio.Semaphore] = None


@dataclass
class DbmsPhaseContext:
    """DBMS Phase 함수에 전달되는 공용 컨텍스트.

    state 단일 출처 — 동시성 제어/누적 상태 모두 state 안에 두고 직접 참조.
    """
    client: Any = None  # Neo4jClient
    orchestrator: Any = None
    state: Optional[DbmsPipelineState] = None
    stats: Any = None  # AnalysisStats

    # state 패스스루 — phase 코드의 시인성 향상
    @property
    def cypher_lock(self) -> asyncio.Lock:
        return self.state.cypher_lock

    @property
    def file_semaphore(self) -> asyncio.Semaphore:
        return self.state.file_semaphore

    @property
    def ddl_schemas(self) -> Set[str]:
        return self.state.ddl_schemas

    @property
    def ddl_table_metadata(self) -> Dict[Tuple[str, str], Dict[str, Any]]:
        return self.state.ddl_table_metadata

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
        cypher_lock: asyncio.Lock,
        file_semaphore: asyncio.Semaphore,
    ) -> "DbmsPhaseContext":
        return cls(
            client=client,
            orchestrator=orchestrator,
            state=DbmsPipelineState(
                cypher_lock=cypher_lock,
                file_semaphore=file_semaphore,
            ),
            stats=stats,
        )
