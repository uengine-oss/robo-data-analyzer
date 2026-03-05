"""공통 AST 프로세서 베이스 (복사본). Phase 1 수집만, Neo4j/LLM 없음."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Tuple, Union

from exp.ast.statement_node import StatementNode


class BaseAstProcessor(ABC):
    """AST 수집만 수행. 쿼리 생성/LLM 없음."""

    def __init__(
        self,
        antlr_data: dict,
        directory: str,
        file_name: str,
        last_line: int,
        source_content: str = "",
    ):
        self.antlr_data = antlr_data
        self.last_line = last_line
        self.source_content = source_content or ""
        normalized_dir = directory.replace("\\", "/") if directory else ""
        self.directory = normalized_dir
        self.file_name = file_name
        self.full_directory = f"{normalized_dir}/{file_name}" if normalized_dir else file_name
        self._nodes: List[StatementNode] = []
        self._unit_info: Dict[str, Any] = {}

    @abstractmethod
    def _collect_nodes(self) -> Tuple[List[StatementNode], Dict[str, Any]]:
        """AST를 수집하여 노드 리스트와 단위 정보를 반환."""
        raise NotImplementedError

    def build_static_graph_queries(self) -> List[Union[str, Dict[str, Any]]]:
        """수집만 수행하고 빈 쿼리 리스트 반환 (Neo4j 미사용)."""
        self._nodes, self._unit_info = self._collect_nodes()
        return []
