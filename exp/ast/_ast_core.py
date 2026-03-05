"""Framework AST 수집 전용 (복사본). RuleLoader/LLM/Neo4j 없음."""

from __future__ import annotations

import re
import logging
from typing import Any, Dict, List, Optional, Tuple

from exp.ast.statement_node import StatementNode
from exp.ast.processor import BaseAstProcessor
from util.text_utils import resolve_node_code

logger = logging.getLogger(__name__)


def _token_count(code: str) -> int:
    """간단 토큰 근사 (tiktoken 미사용)."""
    if not code:
        return 0
    return max(1, len(code) // 4)


def _log_process(_ctx: str, _stage: str, message: str, **kwargs: Any) -> None:
    """exp용 로그 (필요 시 DEBUG)."""
    logger.debug("[%s][%s] %s", _ctx, _stage, message)


# 노드 타입
CLASS_TYPES = frozenset(["CLASS", "INTERFACE", "ENUM"])
FIELD_TYPES = frozenset(["FIELD"])
METHOD_TYPES = frozenset(["METHOD", "CONSTRUCTOR"])
ANALYZABLE_NODE_TYPES = METHOD_TYPES | FIELD_TYPES
SILENT_NODE_TYPES = frozenset(["VARIABLE"])
GRAPH_NODE_TYPES = CLASS_TYPES | METHOD_TYPES | FIELD_TYPES


class ClassInfo:
    """클래스/인터페이스 정보."""
    __slots__ = (
        "key", "name", "kind", "node_start", "node_end",
        "pending_nodes", "finalized", "package_name",
    )

    def __init__(
        self,
        key: str,
        name: str,
        kind: str,
        node_start: int,
        node_end: int,
        pending_nodes: int = 0,
        finalized: bool = False,
        package_name: Optional[str] = None,
    ):
        self.key = key
        self.name = name
        self.kind = kind
        self.node_start = node_start
        self.node_end = node_end
        self.pending_nodes = pending_nodes
        self.finalized = finalized
        self.package_name = package_name


class StatementCollector:
    """AST를 후위순회하여 StatementNode와 클래스 정보를 수집."""

    def __init__(self, antlr_data: Dict[str, Any], directory: str, file_name: str, source_content: str = ""):
        self.antlr_data = antlr_data
        self.directory = directory
        self.file_name = file_name
        self.source_content = source_content or ""
        self.nodes: List[StatementNode] = []
        self.classes: Dict[str, ClassInfo] = {}
        self._node_id = 0

    def _parse_code_to_lines(
        self, code: str, start_line: int, end_line: int
    ) -> List[Tuple[int, str]]:
        if not code:
            return []
        lines = code.replace("\r\n", "\n").split("\n")
        parsed: List[Tuple[int, str]] = []
        for line in lines:
            if not line:
                continue
            match = re.match(r"^(\d+):\s?(.*)", line)
            if not match:
                raise RuntimeError(
                    f"코드 라인 파싱 실패: '{line}' (라인 {start_line}~{end_line})"
                )
            line_no = int(match.group(1))
            text = match.group(2)
            parsed.append((line_no, text))
        return parsed

    def collect(self) -> Tuple[List[StatementNode], Dict[str, ClassInfo]]:
        self._visit(self.antlr_data, None, None, None, None)
        return self.nodes, self.classes

    def _make_class_key(
        self,
        package_name: Optional[str],
        class_name: Optional[str],
        start_line: int,
    ) -> str:
        base = class_name or f"anonymous_{start_line}"
        if package_name:
            return f"{package_name}.{base}"
        return f"{self.directory}:{self.file_name}:{base}:{start_line}"

    def _package_from_filepath(self, file_path: str) -> Optional[str]:
        if not file_path:
            return None
        normalized = file_path.replace("\\", "/")
        if "/" in normalized:
            dir_part = normalized.rsplit("/", 1)[0]
            return dir_part.replace("/", ".")
        return None

    def _visit(
        self,
        node: Dict[str, Any],
        class_key: Optional[str],
        class_name: Optional[str],
        class_kind: Optional[str],
        package_name: Optional[str],
    ) -> Optional[StatementNode]:
        node_type = node["type"]
        start_line = node.get("startLine", 0)
        end_line = node.get("endLine", start_line)
        children = node.get("children", [])
        code = resolve_node_code(node, self.source_content)
        line_entries = self._parse_code_to_lines(code, start_line, end_line)

        if node_type == "FILE":
            file_path = node.get("filePath", "")
            # ChunkBundle(_visit_ast_for_indices)는 packageName 우선 사용 → 동일하게 해서 method_id 일치 (MATCH 성공)
            extracted_pkg = node.get("packageName") or self._package_from_filepath(file_path)
            if extracted_pkg:
                # packageName이 "com/foo/bar" 형태일 수 있음
                package_name = extracted_pkg.replace("/", ".") if "/" in extracted_pkg else extracted_pkg
            _log_process("ANALYZE", "COLLECT", f"패키지 발견: {package_name}")

        if node_type in CLASS_TYPES:
            extracted_name = node.get("name")
            class_key = self._make_class_key(package_name, extracted_name, start_line)
            class_name = extracted_name
            class_kind = node_type
            if class_key not in self.classes:
                self.classes[class_key] = ClassInfo(
                    key=class_key,
                    name=extracted_name or class_key,
                    kind=node_type,
                    node_start=start_line,
                    node_end=end_line,
                    package_name=package_name,
                )
            _log_process(
                "ANALYZE", "COLLECT",
                f"클래스 발견: {package_name}.{extracted_name} ({node_type})",
            )

        child_nodes: List[StatementNode] = []
        for ch in children:
            cn = self._visit(ch, class_key, class_name, class_kind, package_name)
            if cn:
                child_nodes.append(cn)

        analyzable = node_type in ANALYZABLE_NODE_TYPES
        is_silent = node_type in SILENT_NODE_TYPES
        token = _token_count(code)

        self._node_id += 1
        st = StatementNode(
            node_id=self._node_id,
            start_line=start_line,
            end_line=end_line,
            node_type=node_type,
            code=code,
            token=token,
            has_children=bool(child_nodes),
            analyzable=analyzable,
            unit_key=class_key,
            unit_name=class_name,
            unit_kind=class_kind,
            package_name=package_name,
            name=node.get("name"),
            signature=node.get("signature"),
            modifiers=node.get("modifiers"),
            return_type=node.get("returnType"),
            parameters=node.get("parameters"),
            generic_type=node.get("genericType"),
            extends_type=node.get("extendsType"),
            implements_types=node.get("implementsTypes"),
            field_type=node.get("fieldType"),
            annotations=node.get("annotations"),
            comment=node.get("comment"),
            lines=line_entries,
        )
        for c in child_nodes:
            c.parent = st
        st.children.extend(child_nodes)

        if not analyzable and node_type not in CLASS_TYPES:
            st.summary = st.get_raw_code()
            st.completion_event.set()
        elif analyzable and class_key and class_key in self.classes:
            self.classes[class_key].pending_nodes += 1

        self.nodes.append(st)
        if not is_silent:
            _log_process(
                "ANALYZE", "COLLECT",
                f"{node_type} 노드 수집: 라인 {start_line}~{end_line}",
            )
        return st


class FrameworkAstProcessor(BaseAstProcessor):
    """AST 수집만 수행 (Neo4j/LLM 없음)."""

    def __init__(
        self,
        antlr_data: dict,
        directory: str,
        file_name: str,
        last_line: int,
        source_content: str = "",
    ):
        super().__init__(
            antlr_data=antlr_data,
            directory=directory,
            file_name=file_name,
            last_line=last_line,
            source_content=source_content,
        )

    def _collect_nodes(self) -> Tuple[List[StatementNode], Dict[str, ClassInfo]]:
        collector = StatementCollector(
            self.antlr_data, self.directory, self.file_name,
            source_content=getattr(self, "source_content", "") or "",
        )
        return collector.collect()
