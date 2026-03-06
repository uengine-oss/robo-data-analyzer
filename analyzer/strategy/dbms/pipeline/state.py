"""DBMS 파이프라인 상태 (Framework state.py 미러링)

DbmsPipeline에서 사용하는 파이프라인 상태를 관리합니다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from analyzer.strategy.dbms.common.models import FileAnalysisContext


@dataclass
class DbmsPipelineState:
    """DBMS 파이프라인 상태"""
    ddl_schemas: Set[str] = field(default_factory=set)
    ddl_table_metadata: Dict[Tuple[str, str], Dict[str, Any]] = field(default_factory=dict)
    contexts: List[FileAnalysisContext] = field(default_factory=list)

    def get(self, key: str, default: Any = None) -> Any:
        """dict-like get()"""
        return getattr(self, key, default)

    def update(self, data: dict) -> None:
        """dict-like update()"""
        for key, value in data.items():
            if hasattr(self, key):
                setattr(self, key, value)

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def __setitem__(self, key: str, value: Any) -> None:
        setattr(self, key, value)
