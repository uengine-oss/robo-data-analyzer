"""DBMS 파이프라인 상태 (Framework state.py 미러링)

DbmsPipeline에서 사용하는 파이프라인 상태를 관리합니다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from analyzer.strategy.dbms.common.models import FileAnalysisContext


@dataclass
class DbmsPipelineState:
    """DBMS 파이프라인 상태 (모든 필드 명시적)."""
    ddl_schemas: Set[str] = field(default_factory=set)
    ddl_table_metadata: Dict[Tuple[str, str], Dict[str, Any]] = field(default_factory=dict)
    contexts: List[FileAnalysisContext] = field(default_factory=list)


@dataclass
class DbmsPhaseContext:
    """DBMS Phase 함수에 전달되는 공용 컨텍스트.

    pipeline 인스턴스를 포함하여 emit_* 메서드 접근 가능.
    """
    pipeline: Any = None  # DbmsPipeline
    client: Any = None  # Neo4jClient
    orchestrator: Any = None
    state: Optional[DbmsPipelineState] = None
    stats: Any = None  # AnalysisStats

    # pipeline에서 추출한 편의 속성
    @property
    def cypher_lock(self):
        return self.pipeline._cypher_lock if self.pipeline else None

    @property
    def file_semaphore(self):
        return self.pipeline._file_semaphore if self.pipeline else None

    @property
    def ddl_schemas(self) -> set:
        return self.pipeline._ddl_schemas if self.pipeline else set()

    @property
    def ddl_table_metadata(self) -> dict:
        return self.pipeline._ddl_table_metadata if self.pipeline else {}

    @property
    def api_key(self) -> str:
        return getattr(self.orchestrator, 'api_key', '')

    @property
    def locale(self) -> str:
        return getattr(self.orchestrator, 'locale', 'ko')

    @property
    def target(self) -> str:
        return getattr(self.orchestrator, 'target', '')

    @property
    def name_case(self) -> str:
        return getattr(self.orchestrator, 'name_case', 'original')

    @property
    def datasource_name(self) -> str:
        return getattr(self.orchestrator, 'datasource_name', '')

    @classmethod
    def create(cls, pipeline, client, orchestrator, stats) -> "DbmsPhaseContext":
        return cls(
            pipeline=pipeline,
            client=client,
            orchestrator=orchestrator,
            state=DbmsPipelineState(
                ddl_schemas=pipeline._ddl_schemas,
                ddl_table_metadata=pipeline._ddl_table_metadata,
            ),
            stats=stats,
        )
