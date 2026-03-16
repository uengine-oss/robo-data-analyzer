"""Framework AST 프로세서. StatementNode, BaseAstProcessor, FrameworkAstProcessor, StatementCollector.

Framework 전략 전용 AST 처리 모듈.
- StatementNode: Framework용 AST 노드 (DBMS의 StatementNode와 독립)
- BaseAstProcessor: AST 수집만 수행 (Neo4j/LLM 없음)
- FrameworkAstProcessor: AST 수집 구현
- StatementCollector: AST 후위순회 수집기
"""

from __future__ import annotations

import asyncio
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from util.text_utils import resolve_node_code

logger = logging.getLogger(__name__)

try:
    from config.settings import settings
    MAX_CONTEXT_TOKEN = settings.batch.max_context_token
except Exception:
    MAX_CONTEXT_TOKEN = 300


# ---------------------------------------------------------------------------
# StatementNode (Framework 전용)
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class StatementNode:
    """평탄화된 AST 노드 (framework 분석용)."""
    node_id: int
    start_line: int
    end_line: int
    node_type: str
    code: str
    token: int
    has_children: bool
    analyzable: bool
    unit_key: Optional[str] = None
    unit_name: Optional[str] = None
    unit_kind: Optional[str] = None
    schema_name: Optional[str] = None
    dml: bool = False
    package_name: Optional[str] = None
    name: Optional[str] = None
    signature: Optional[str] = None
    modifiers: Optional[str] = None
    return_type: Optional[str] = None
    parameters: Optional[str] = None
    generic_type: Optional[str] = None
    extends_type: Optional[str] = None
    implements_types: Optional[str] = None
    field_type: Optional[str] = None
    annotations: Optional[str] = None
    comment: Optional[str] = None
    lines: List[Tuple[int, str]] = field(default_factory=list)
    parent: Optional["StatementNode"] = None
    children: List["StatementNode"] = field(default_factory=list)
    summary: Optional[str] = None
    context: Optional[str] = None
    sibling_context: Optional[str] = None
    ok: bool = True
    external_refs: Optional[List[dict]] = None
    completion_event: asyncio.Event = field(init=False, repr=False)
    context_ready_event: asyncio.Event = field(init=False, repr=False)

    def __post_init__(self):
        object.__setattr__(self, "completion_event", asyncio.Event())
        object.__setattr__(self, "context_ready_event", asyncio.Event())

    def get_raw_code(self) -> str:
        return "\n".join(f"{line_no}: {text}" for line_no, text in self.lines)

    def get_placeholder_code(
        self, preserve_types: Optional[Set[str]] = None, include_assigns: bool = False
    ) -> str:
        if not self.children:
            return self.get_raw_code()
        preserve_types = preserve_types or set()
        assign_node_set: Set[Tuple[int, int]] = set()
        if include_assigns:
            ASSIGN_TYPES = {"ASSIGNMENT", "NEW_INSTANCE"}

            def find_assign_nodes_recursive(node: StatementNode) -> List[StatementNode]:
                results = []
                for child in node.children:
                    if child.node_type in ASSIGN_TYPES:
                        results.append(child)
                    results.extend(find_assign_nodes_recursive(child))
                return results

            assign_nodes = find_assign_nodes_recursive(self)
            assign_node_set = {(n.start_line, n.end_line) for n in assign_nodes}
        filtered_children = [c for c in self.children if c.node_type != "VARIABLE"]
        if not filtered_children:
            return self.get_raw_code()
        result_lines: List[str] = []
        line_index = 0
        total_lines = len(self.lines)
        sorted_children = sorted(filtered_children, key=lambda c: c.start_line)
        for child in sorted_children:
            while line_index < total_lines and self.lines[line_index][0] < child.start_line:
                line_no, text = self.lines[line_index]
                result_lines.append(f"{line_no}: {text}")
                line_index += 1
            child_span = (child.start_line, child.end_line)
            should_preserve = (
                child.node_type in preserve_types
                or (include_assigns and child_span in assign_node_set)
            )
            if should_preserve:
                while line_index < total_lines and self.lines[line_index][0] <= child.end_line:
                    line_no, text = self.lines[line_index]
                    result_lines.append(f"{line_no}: {text}")
                    line_index += 1
            else:
                result_lines.append(f"{child.start_line}: ...code...")
                while line_index < total_lines and self.lines[line_index][0] <= child.end_line:
                    line_index += 1
        while line_index < total_lines:
            line_no, text = self.lines[line_index]
            result_lines.append(f"{line_no}: {text}")
            line_index += 1
        return "\n".join(result_lines)

    def get_skeleton_code(self) -> str:
        if not self.children:
            return self.get_raw_code()
        filtered_children = [c for c in self.children if c.node_type != "VARIABLE"]
        if not filtered_children:
            return self.get_raw_code()
        result_lines: List[str] = []
        sorted_children = sorted(filtered_children, key=lambda c: c.start_line)
        in_child_block = False
        for line_no, text in self.lines:
            is_child_line = any(
                c.start_line <= line_no <= c.end_line for c in sorted_children
            )
            if is_child_line:
                if not in_child_block:
                    result_lines.append("    ....")
                    in_child_block = True
            else:
                in_child_block = False
                result_lines.append(f"{line_no}: {text}")
        return "\n".join(result_lines)

    def get_previous_sibling(self) -> Optional[StatementNode]:
        if not self.parent:
            return None
        siblings = self.parent.children
        if not siblings or len(siblings) < 2:
            return None
        sorted_siblings = sorted(siblings, key=lambda n: n.start_line)
        current_idx = -1
        for i, sib in enumerate(sorted_siblings):
            if sib.node_id == self.node_id:
                current_idx = i
                break
        if current_idx <= 0:
            return None
        return sorted_siblings[current_idx - 1]


# ---------------------------------------------------------------------------
# BaseAstProcessor (Framework 전용 — 수집만, Neo4j/LLM 없음)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Helper functions and constants
# ---------------------------------------------------------------------------

def _token_count(code: str) -> int:
    """간단 토큰 근사 (tiktoken 미사용)."""
    if not code:
        return 0
    return max(1, len(code) // 4)


def _log_process(_ctx: str, _stage: str, message: str, **kwargs: Any) -> None:
    """Framework AST 디버그 로그 (필요 시 DEBUG)."""
    logger.debug("[%s][%s] %s", _ctx, _stage, message)


# 노드 타입 — ontology에서 Java 기본값 로딩 (하위호환, backward compat)
# 실제 파이프라인에서는 StatementCollector가 lang 파라미터로 동적 로딩
from .common.ontology import get_ontology as _get_ontology

_onto = _get_ontology()
CLASS_TYPES = _onto.container_types("java")
FIELD_TYPES = _onto.field_types("java")
METHOD_TYPES = _onto.method_types("java")
ANALYZABLE_NODE_TYPES = METHOD_TYPES | FIELD_TYPES
SILENT_NODE_TYPES = _onto.silent_types("java")
GRAPH_NODE_TYPES = CLASS_TYPES | METHOD_TYPES | FIELD_TYPES


# ---------------------------------------------------------------------------
# ClassInfo
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# StatementCollector
# ---------------------------------------------------------------------------

class StatementCollector:
    """AST를 후위순회하여 StatementNode와 클래스 정보를 수집."""

    def __init__(self, antlr_data: Dict[str, Any], directory: str, file_name: str, source_content: str = "", lang: str = "java"):
        self.antlr_data = antlr_data
        self.directory = directory
        self.file_name = file_name
        self.source_content = source_content or ""
        self.lang = lang
        onto = _get_ontology()
        self._container_types = onto.container_types(lang)
        self._method_types = onto.method_types(lang)
        self._field_types = onto.field_types(lang)
        self._analyzable_types = onto.analyzable_types(lang)
        self._silent_types = onto.silent_types(lang)
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
            extracted_pkg = node.get("packageName") or self._package_from_filepath(file_path)
            if extracted_pkg:
                package_name = extracted_pkg.replace("/", ".") if "/" in extracted_pkg else extracted_pkg
            _log_process("ANALYZE", "COLLECT", f"패키지 발견: {package_name}")

        if node_type in self._container_types:
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
                f"컨테이너 발견: {package_name}.{extracted_name} ({node_type})",
            )

        child_nodes: List[StatementNode] = []
        for ch in children:
            cn = self._visit(ch, class_key, class_name, class_kind, package_name)
            if cn:
                child_nodes.append(cn)

        analyzable = node_type in self._analyzable_types
        is_silent = node_type in self._silent_types
        token = _token_count(code)

        extends_type = node.get("extendsType") or node.get("extends")
        implements_types = node.get("implementsTypes") or node.get("implements")
        annotations = node.get("annotations") or node.get("annotation")
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
            extends_type=extends_type,
            implements_types=implements_types,
            field_type=node.get("fieldType"),
            annotations=annotations,
            comment=node.get("comment"),
            lines=line_entries,
        )
        for c in child_nodes:
            c.parent = st
        st.children.extend(child_nodes)

        if not analyzable and node_type not in self._container_types:
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


# ---------------------------------------------------------------------------
# FrameworkAstProcessor
# ---------------------------------------------------------------------------

class FrameworkAstProcessor(BaseAstProcessor):
    """AST 수집만 수행 (Neo4j/LLM 없음)."""

    def __init__(
        self,
        antlr_data: dict,
        directory: str,
        file_name: str,
        last_line: int,
        source_content: str = "",
        lang: str = "java",
    ):
        super().__init__(
            antlr_data=antlr_data,
            directory=directory,
            file_name=file_name,
            last_line=last_line,
            source_content=source_content,
        )
        self._lang = lang

    def _collect_nodes(self) -> Tuple[List[StatementNode], Dict[str, ClassInfo]]:
        collector = StatementCollector(
            self.antlr_data, self.directory, self.file_name,
            source_content=getattr(self, "source_content", "") or "",
            lang=self._lang,
        )
        return collector.collect()


__all__ = [
    "StatementNode",
    "BaseAstProcessor",
    "FrameworkAstProcessor",
    "ClassInfo",
    "StatementCollector",
    "CLASS_TYPES",
    "FIELD_TYPES",
    "METHOD_TYPES",
    "ANALYZABLE_NODE_TYPES",
    "SILENT_NODE_TYPES",
    "GRAPH_NODE_TYPES",
    "MAX_CONTEXT_TOKEN",
]
