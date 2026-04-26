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
from typing import Any, Dict, Generator, List, Optional, Tuple, Union

from util.code_text_utils import resolve_node_code

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# StatementNode (Framework 전용)
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class StatementNode:
    """평탄화된 AST 노드 (framework 분석용).

    DBMS 의 StatementNode (dbms/common/node_models.py) 와는 독립.
    Framework 는 노드 트리를 단순 데이터로 한 번 훑어 FunctionFacts/VariableFacts/module_index
    로 변환할 뿐이라, DBMS 의 Bottom-up LLM 컨텍스트 누적 인프라는 보유하지 않는다.
    """
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
    package_name: Optional[str] = None
    name: Optional[str] = None
    signature: Optional[str] = None
    return_type: Optional[str] = None
    parameters: Optional[str] = None
    extends_type: Optional[str] = None
    implements_types: Optional[str] = None
    variable_type: Optional[str] = None
    annotations: Optional[str] = None
    comment: Optional[str] = None
    # 변수 선언부(배열크기·초기화식 등)에서 참조한 식별자 이름 목록 — ANTLR AST에서 직접 수집
    references: List[str] = field(default_factory=list)
    lines: List[Tuple[int, str]] = field(default_factory=list)
    parent: Optional["StatementNode"] = None
    children: List["StatementNode"] = field(default_factory=list)
    summary: Optional[str] = None
    completion_event: asyncio.Event = field(init=False, repr=False)

    def __post_init__(self):
        object.__setattr__(self, "completion_event", asyncio.Event())

    def get_raw_code(self) -> str:
        return "\n".join(f"{line_no}: {text}" for line_no, text in self.lines)


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


def _log_process(_ctx: str, _stage: str, message: str) -> None:
    """Framework AST 디버그 로그 (필요 시 DEBUG)."""
    logger.debug("[%s][%s] %s", _ctx, _stage, message)


# Module-level node type constants — 전 언어 합집합. 특정 언어 필터링은 런타임에서 lang 파라미터로 처리.
from ..common.domain.ontology import get_ontology as _get_ontology

get_ontology = _get_ontology  # alias for merged ast_walker code
_onto = _get_ontology()
_ALL_LANGS = _onto.supported_languages()
MODULE_TYPES: frozenset = frozenset().union(*(_onto.module_types(l) for l in _ALL_LANGS))
FUNCTION_TYPES: frozenset = frozenset().union(*(_onto.function_types(l) for l in _ALL_LANGS))
VARIABLE_TYPES: frozenset = frozenset().union(*(_onto.variable_types(l) for l in _ALL_LANGS))
CONSTANT_TYPES: frozenset = frozenset().union(*(_onto.constant_types(l) for l in _ALL_LANGS))
ANALYZABLE_NODE_TYPES = FUNCTION_TYPES | VARIABLE_TYPES | CONSTANT_TYPES
SILENT_NODE_TYPES: frozenset = frozenset().union(*(_onto.silent_types(l) for l in _ALL_LANGS))
GRAPH_NODE_TYPES = MODULE_TYPES | FUNCTION_TYPES | VARIABLE_TYPES | CONSTANT_TYPES


# ---------------------------------------------------------------------------
# ModuleInfo
# ---------------------------------------------------------------------------

class ModuleInfo:
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

    def __init__(self, antlr_data: Dict[str, Any], directory: str, file_name: str, lang: str, source_content: str = ""):
        self.antlr_data = antlr_data
        self.directory = directory
        self.file_name = file_name
        self.source_content = source_content or ""
        self.lang = lang
        onto = _get_ontology()
        self._module_types = onto.module_types(lang)
        self._function_types = onto.function_types(lang)
        self._variable_types = onto.variable_types(lang)
        self._analyzable_types = onto.analyzable_types(lang)
        self._silent_types = onto.silent_types(lang)
        self.nodes: List[StatementNode] = []
        self.classes: Dict[str, ModuleInfo] = {}
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

    def collect(self) -> Tuple[List[StatementNode], Dict[str, ModuleInfo]]:
        self._visit(self.antlr_data, None, None, None, None)
        return self.nodes, self.classes

    def _make_module_key(
        self,
        package_name: Optional[str],
        module_name: Optional[str],
        start_line: int,
    ) -> str:
        base = module_name or f"anonymous_{start_line}"
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

    @staticmethod
    def _stem_from_filename(file_name: str) -> str:
        """파일명에서 확장자 제거. 예: 'svc_plan.c' → 'svc_plan'"""
        if "." in file_name:
            return file_name.rsplit(".", 1)[0]
        return file_name

    def _visit(
        self,
        node: Dict[str, Any],
        module_key: Optional[str],
        module_name: Optional[str],
        module_kind: Optional[str],
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

            # C 언어: FILE 노드를 container(Class)로 등록 — 함수의 소속 컨테이너 역할
            if node_type in self._module_types:
                file_stem = self._stem_from_filename(self.file_name)
                module_key = self._make_module_key(package_name, file_stem, start_line)
                module_name = file_stem
                module_kind = "FILE"
                if module_key not in self.classes:
                    self.classes[module_key] = ModuleInfo(
                        key=module_key,
                        name=file_stem,
                        kind="FILE",
                        node_start=start_line,
                        node_end=end_line,
                        package_name=package_name,
                    )
                _log_process(
                    "ANALYZE", "COLLECT",
                    f"파일 컨테이너 등록: {package_name}.{file_stem} (FILE)",
                )

        if node_type in self._module_types and node_type != "FILE":
            extracted_name = node.get("name")
            module_key = self._make_module_key(package_name, extracted_name, start_line)
            module_name = extracted_name
            module_kind = node_type
            if module_key not in self.classes:
                self.classes[module_key] = ModuleInfo(
                    key=module_key,
                    name=extracted_name or module_key,
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
            cn = self._visit(ch, module_key, module_name, module_kind, package_name)
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
            unit_key=module_key,
            unit_name=module_name,
            unit_kind=module_kind,
            package_name=package_name,
            name=node.get("name"),
            signature=node.get("signature"),
            return_type=node.get("returnType"),
            parameters=node.get("parameters"),
            extends_type=extends_type,
            implements_types=implements_types,
            variable_type=node.get("variableType"),
            annotations=annotations,
            comment=node.get("comment"),
            references=list(node.get("references") or []),
            lines=line_entries,
        )
        for c in child_nodes:
            c.parent = st
        st.children.extend(child_nodes)

        if not analyzable and node_type not in self._module_types:
            st.summary = st.get_raw_code()
            st.completion_event.set()
        elif analyzable and module_key and module_key in self.classes:
            self.classes[module_key].pending_nodes += 1

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
        lang: str,
        source_content: str = "",
    ):
        super().__init__(
            antlr_data=antlr_data,
            directory=directory,
            file_name=file_name,
            last_line=last_line,
            source_content=source_content,
        )
        self._lang = lang

    def _collect_nodes(self) -> Tuple[List[StatementNode], Dict[str, ModuleInfo]]:
        collector = StatementCollector(
            self.antlr_data, self.directory, self.file_name,
            source_content=getattr(self, "source_content", ""),
            lang=self._lang,
        )
        return collector.collect()


# ---------------------------------------------------------------------------
# AST Walker — AST 순회 및 FunctionFacts/ChunkBundle 생성 (범용)
# (구 ast_walker.py에서 병합)
# ---------------------------------------------------------------------------

from pathlib import Path

from analyzer.strategy.framework.common.models.node_models import (
    VariableFacts,
    FunctionFacts,
    NODE_VARIABLE,
    NODE_NEW_INSTANCE,
)
from util.encoding_reader import read_text_with_fallback_sync as read_text_with_fallback, read_json_with_fallback
from .chunk_bundle import (
    build_chunk_bundle_from_source,
    save_chunk_bundle,
    _get_chunk_bundle_dir_from_run_dir,
)


@dataclass
class AstVisitorContext:
    symbol_type_index: Dict[str, Dict[str, str]] = field(default_factory=dict)
    function_index: Dict[str, str] = field(default_factory=dict)
    function_index_by_module: Dict[str, Dict[str, List[str]]] = field(default_factory=dict)
    variable_types_by_module: Dict[str, Dict[str, str]] = field(default_factory=dict)
    chunk_functions: List[Dict[str, Any]] = field(default_factory=list)
    chunk_function_calls: List[Dict[str, Any]] = field(default_factory=list)
    chunk_variables: List[Dict[str, Any]] = field(default_factory=list)
    ref_candidates_by_function: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    function_call_seq: Dict[str, int] = field(default_factory=dict)
    node_kinds: Dict[str, str] = field(default_factory=dict)  # function_id → 원래 노드 타입 (FUNCTION, METHOD 등)


def _last_line_from_ast(ast_data: dict) -> int:
    """AST에서 마지막 라인 번호 추출."""
    last = 0
    for child in ast_data.get("children", []):
        end = child.get("endLine", 0)
        if end > last:
            last = end
    return last


def load_ast_json(path: str) -> dict:
    """AST JSON 파일 로드 (인코딩 fallback)."""
    return read_json_with_fallback(path)


def _visit_ast_for_indices(
    node: Dict[str, Any],
    package_name: Optional[str],
    module_name: Optional[str],
    module_fqn: Optional[str],
    function_id: Optional[str],
    ctx: AstVisitorContext,
    lang: str,
) -> None:
    """Raw AST 순회: symbol_type_index, function_index, ChunkBundle용 리스트 수집 (범용)."""
    onto = get_ontology()
    module_types = onto.module_types(lang)
    function_types = onto.function_types(lang)
    call_types = onto.call_types(lang)

    node_type = node.get("type", "")
    start_line = node.get("startLine", 0)
    end_line = node.get("endLine", 0)
    children = node.get("children", [])

    if node_type == "FILE":
        pkg = node.get("packageName") or node.get("filePath", "").replace("\\", "/")
        if "/" in pkg:
            pkg = ".".join(pkg.rsplit("/", 1)[0].split("/"))
        if pkg:
            package_name = pkg

        # C 언어: FILE이 container일 때 파일명을 module_fqn으로 등록
        if node_type in module_types:
            file_path = node.get("filePath", "")
            file_basename = file_path.replace("\\", "/").rsplit("/", 1)[-1] if file_path else ""
            file_stem = file_basename.rsplit(".", 1)[0] if "." in file_basename else file_basename
            if file_stem:
                module_name = file_stem
                module_fqn = f"{package_name}.{file_stem}" if package_name else file_stem

    if node_type in module_types and node_type != "FILE":
        module_name = node.get("name") or module_name
        if package_name and module_name:
            module_fqn = f"{package_name}.{module_name}"
        else:
            module_fqn = module_name

    if node_type in function_types:
        function_name = node.get("name", "")
        if module_fqn and function_name:
            function_id = f"{module_fqn}.{function_name}"
        elif package_name and function_name:
            # fallback: package.functionName
            function_id = f"{package_name}.{function_name}"
        else:
            function_id = function_id or f"unknown_{start_line}"
        if function_id and function_id not in ctx.function_index:
            ctx.function_index[function_id] = function_id
            ctx.node_kinds[function_id] = node_type  # FUNCTION, METHOD 등 원래 타입 저장
        if module_fqn and function_name:
            ctx.function_index_by_module.setdefault(module_fqn, {}).setdefault(function_name, []).append(function_id)
        ctx.chunk_functions.append({"function_id": function_id, "start_line": start_line, "end_line": end_line})

    for ch in children:
        ch_type = ch.get("type", "")
        ch_start = ch.get("startLine", 0)
        ch_end = ch.get("endLine", 0)

        if ch_type == NODE_VARIABLE and function_id:
            var_name = ch.get("name", "")
            var_type = ch.get("variableType")
            if ch.get("initializerContainsNewInstance"):
                for sub in ch.get("children", []):
                    if isinstance(sub, dict) and sub.get("type") == NODE_NEW_INSTANCE:
                        var_type = sub.get("name") or var_type
                        break
            if var_name:
                ctx.symbol_type_index.setdefault(function_id, {})[var_name] = var_type or ""
            flags: Dict[str, Any] = {}
            if ch.get("initializerContainsMethodCall"):
                flags["initializerContainsMethodCall"] = True
            if ch.get("initializerContainsNewInstance"):
                flags["initializerContainsNewInstance"] = True
            ctx.chunk_variables.append(
                {
                    "parent_function_id": function_id,
                    "var_name": var_name,
                    "var_type": var_type,
                    "start_line": ch_start,
                    "end_line": ch_end,
                    "flags": flags if flags else None,
                }
            )

        # GLOBAL_VARIABLE: 함수 밖 변수 → __global__ 키에 수집
        if ch_type == NODE_VARIABLE and not function_id:
            var_name = ch.get("name", "")
            var_type = ch.get("variableType")
            if var_name:
                ctx.symbol_type_index.setdefault("__global__", {})[var_name] = var_type or ""

        if ch_type in call_types and function_id:
            seq = ctx.function_call_seq.get(function_id, 0)
            call_id = f"C:{ch_start}:{ch_end}:{seq}"
            ctx.function_call_seq[function_id] = seq + 1
            ctx.chunk_function_calls.append(
                {
                    "parent_function_id": function_id,
                    "call_id": call_id,
                    "start_line": ch_start,
                    "end_line": ch_end,
                }
            )
            ctx.ref_candidates_by_function.setdefault(function_id, []).append(
                {
                    "kind": "CALL_REF",
                    "receiver": None,
                    "function_name": ch.get("name"),
                    "line_range": (ch_start, ch_end),
                }
            )

        if ch_type == NODE_NEW_INSTANCE and function_id:
            ctx.ref_candidates_by_function.setdefault(function_id, []).append(
                {
                    "kind": "NEW_INSTANCE_REF",
                    "moduleName": ch.get("name"),
                    "line_range": (ch_start, ch_end),
                }
            )

        _visit_ast_for_indices(ch, package_name, module_name, module_fqn, function_id, ctx, lang=lang)


def collect_ast_indices_and_chunk_data(
    ast_data: dict,
    lang: str,
) -> Tuple[
    Dict[str, Dict[str, str]],
    Dict[str, str],
    Dict[str, Dict[str, List[str]]],
    List[Dict[str, Any]],
    List[Dict[str, Any]],
    List[Dict[str, Any]],
    Dict[str, List[Dict[str, Any]]],
    Dict[str, str],
]:
    """AST에서 symbol_type_index, function_index, function_index_by_module, ChunkBundle용 functions/calls/variables, ref_candidates_by_function 반환."""
    ctx = AstVisitorContext()

    if ast_data.get("type") == "FILE":
        _visit_ast_for_indices(ast_data, None, None, None, None, ctx, lang=lang)
    else:
        for node in ast_data.get("children", [ast_data]):
            if isinstance(node, dict):
                _visit_ast_for_indices(node, None, None, None, None, ctx, lang=lang)

    return (
        ctx.symbol_type_index,
        ctx.function_index,
        ctx.function_index_by_module,
        ctx.chunk_functions,
        ctx.chunk_function_calls,
        ctx.chunk_variables,
        ctx.ref_candidates_by_function,
        ctx.node_kinds,
    )


def build_function_facts_and_module_index(
    ast_path: str,
    ast_data: dict,
    lang: str,
    data_dir: Optional[str] = None,
) -> Tuple[List[FunctionFacts], Dict[str, Any], List[VariableFacts], Dict[str, List[str]]]:
    """한 AST 파일에서 FunctionFacts, module_index, VariableFacts 리스트 생성."""
    onto = get_ontology()
    module_types = onto.module_types(lang)
    function_types = onto.function_types(lang)
    variable_types = onto.variable_types(lang) | onto.constant_types(lang)

    p = Path(ast_path)
    parts = p.parts
    if "analysis" in parts and "target" in parts:
        idx = parts.index("target") + 1
        dir_parts = parts[idx:-1]
        directory = "/".join(dir_parts) if dir_parts else ""
    else:
        directory = "/".join(p.parts[:-1]) if len(p.parts) > 1 else ""
    file_name = p.name  # AST JSON 파일명 (fallback)

    source_content = ""
    if data_dir:
        source_path = _source_path_for_ast(data_dir, ast_path, lang=lang)
        if source_path:
            # 원본 소스 경로로 directory/file_name 덮어쓰기
            sp = Path(source_path)
            src_parts = sp.parts
            if "source" in src_parts:
                idx = src_parts.index("source") + 1
                dir_parts = src_parts[idx:-1]
                directory = "/".join(dir_parts) if dir_parts else ""
            file_name = sp.name
            try:
                source_content = read_text_with_fallback(source_path)
            except Exception as e:  # noqa: SILENT_FAIL — 소스 파일 읽기 실패는 AST JSON 의 code 필드로 진행 (보강 데이터 부재만)
                logger.error("[PHASE:1:AST] 소스 파일 읽기 실패 | path=%s | %s: %s", source_path, type(e).__name__, e)

    last_line = _last_line_from_ast(ast_data)
    processor = FrameworkAstProcessor(
        antlr_data=ast_data,
        directory=directory,
        file_name=file_name,
        last_line=last_line,
        source_content=source_content,
        lang=lang,
    )
    processor.build_static_graph_queries()

    nodes = processor._nodes
    classes = processor._unit_info  # Dict[str, ModuleInfo]  # noqa: F841

    function_facts_list: List[FunctionFacts] = []
    module_index: Dict[str, Any] = {}
    variable_facts_list: List[VariableFacts] = []
    node_kinds: Dict[str, str] = {}  # variable_id/function_id → AST 노드 타입 (서브레이블용)

    for node in nodes:
        if node.node_type in variable_types and node.name:
            # C 전역변수 등은 unit_key가 없을 수 있음 → 파일 경로 기반 fallback
            container_key = node.unit_key or f"{directory}/{file_name}"
            variable_id = f"{container_key}#{node.name}_{node.start_line}"
            code_ref = f"{directory}/{file_name}:{node.start_line}-{node.end_line}"
            variable_facts_list.append(
                VariableFacts(
                    variable_id=variable_id,
                    module_key=container_key,
                    name=node.name,
                    start_line=node.start_line,
                    end_line=node.end_line,
                    variable_type=node.variable_type,
                    code_ref=code_ref,
                    comment=node.comment,
                    annotations=node.annotations,
                    references=list(node.references),
                )
            )
            # variable_id → 원래 AST 노드 타입 저장 (서브레이블용)
            node_kinds[variable_id] = node.node_type
        if node.node_type in module_types and node.unit_name:
            # FILE 노드: unit_kind를 통해 kind를 "FILE"로 저장
            kind = node.unit_kind if node.unit_kind else node.node_type
            fqn = f"{node.package_name}.{node.unit_name}" if node.package_name else node.unit_name
            module_index[fqn] = {
                "module_key": node.unit_key,
                "fqn": fqn,
                "package_name": node.package_name,
                "name": node.unit_name,
                "kind": kind,
                "start_line": node.start_line,
                "end_line": node.end_line,
                "comment": node.comment,
                "annotations": node.annotations,
                "extends_type": node.extends_type,
                "implements_types": node.implements_types,
            }

        if node.node_type in function_types and node.name:
            if node.package_name and node.unit_name:
                # OOP: package.Class.method / procedural: package.fileName.functionName
                fqn = f"{node.package_name}.{node.unit_name}.{node.name}"
            elif node.package_name:
                # fallback: package.functionName
                fqn = f"{node.package_name}.{node.name}"
            else:
                fqn = node.name
            function_id = fqn or f"{node.unit_key}#{node.name}"
            code_ref = f"{directory}/{file_name}:{node.start_line}-{node.end_line}"
            function_facts_list.append(
                FunctionFacts(
                    function_id=function_id,
                    fqn=fqn,
                    signature=node.signature or "",
                    start_line=node.start_line,
                    end_line=node.end_line,
                    annotations=node.annotations,
                    comment=node.comment,
                    return_type=node.return_type,
                    params=node.parameters,
                    code_ref=code_ref,
                    module_key=node.unit_key,
                    package_name=node.package_name,
                    code=node.code,
                )
            )

    module_name_to_fqn: Dict[str, List[str]] = {}
    for fqn, info in module_index.items():
        if isinstance(info, dict):
            name = info.get("name")
            if name:
                module_name_to_fqn.setdefault(name, []).append(fqn)

    return function_facts_list, module_index, variable_facts_list, module_name_to_fqn, node_kinds


def _extract_package_fqns(module_index: Dict[str, Any]) -> List[str]:
    """module_index에서 고유 package_name 수집."""
    seen: set = set()
    out: List[str] = []
    for info in module_index.values():
        if not isinstance(info, dict):
            continue
        pkg = info.get("package_name")
        if pkg and pkg not in seen:
            seen.add(pkg)
            out.append(pkg)
    return sorted(out)


def _ast_json_path_for_source(data_dir: str, source_path: str, lang: str) -> Optional[str]:
    """source 파일 경로에 대응하는 target AST JSON 경로 (같은 상대경로, 확장자만 .json)."""
    onto = get_ontology()
    source_exts = onto.source_extensions(lang)
    p = Path(source_path)
    if p.suffix.lower() not in source_exts:
        return None
    source_dir = (Path(data_dir) / "source").resolve()
    try:
        rel = p.resolve().relative_to(source_dir)
    except ValueError:
        return None
    rel_json = str(Path(rel).with_suffix(".json")).replace("\\", "/")
    target_path = Path(data_dir) / "analysis" / "target" / rel_json
    return str(target_path) if target_path.is_file() else None


def _source_path_for_ast(data_dir: str, ast_path: str, lang: str) -> Optional[str]:
    """AST JSON 경로에 대응하는 원본 소스 파일 경로."""
    onto = get_ontology()
    source_exts = onto.source_extensions(lang)
    base = Path(data_dir)
    p = Path(ast_path)
    parts = p.parts
    if "analysis" in parts and "target" in parts:
        idx = parts.index("target") + 1
        rel_parts = parts[idx:]
    else:
        rel_parts = p.parts
    rel_stem = "/".join(rel_parts[:-1]) if len(rel_parts) > 1 else ""
    stem_name = Path(rel_parts[-1]).stem if rel_parts else p.stem
    for ext in source_exts:
        rel_source = f"{rel_stem}/{stem_name}{ext}" if rel_stem else f"{stem_name}{ext}"
        candidates = [
            base / "source" / rel_source,
            base / rel_source,
        ]
        for c in candidates:
            if c.is_file():
                return str(c)
    return None


def extract_code_structure(
    source_ast_jobs: List[str],
    lang: str,
    run_dir: str,
    data_dir: Optional[str] = None,
) -> Generator[Dict[str, Any], None, None]:
    """파일별로 AST 결과를 yield. 호출자가 누적 및 Neo4j 반영.

    Yields:
        Dict with keys: facts, cidx, fields, module_name_to_fqn,
        sym_idx, meth_idx, meth_by_cls, ref_cands, file_name
    """
    onto = get_ontology()
    source_exts = set(onto.source_extensions(lang))

    logger.info(
        "[PHASE:1:AST] AST 진입 | lang=%s paths=%d data_dir=%s run_dir=%s",
        lang,
        len(source_ast_jobs),
        data_dir or "",
        run_dir or "",
    )
    logger.info(
        "[PHASE:1:AST] [섹션] 파일별 처리: AST 파싱 → FunctionFacts, ChunkBundle 생성·저장 | 파일 %d개",
        len(source_ast_jobs),
    )

    source_dir: Optional[Path] = None
    if data_dir:
        source_dir = (Path(data_dir) / "source").resolve()

    for path in source_ast_jobs:
        try:
            ast_path_val: Optional[str] = None
            source_path_for_bundle: Optional[str] = None
            path_ext = Path(path).suffix.lower()
            if path_ext in source_exts and data_dir:
                ast_path_val = _ast_json_path_for_source(data_dir, path, lang=lang)
                if not ast_path_val:
                    logger.error("[PHASE:1:AST] AST JSON 없음, 스킵 | path=%s", path)
                    continue
                source_path_for_bundle = path
            else:
                ast_path_val = path
                source_path_for_bundle = _source_path_for_ast(data_dir, path, lang=lang) if data_dir else None

            ast_data = load_ast_json(ast_path_val)
            facts, cidx, fields, module_name_to_fqn, variable_node_kinds = build_function_facts_and_module_index(
                ast_path_val, ast_data, data_dir=data_dir, lang=lang,
            )

            (
                sym_idx,
                meth_idx,
                meth_by_cls,
                chunk_m,
                chunk_calls,
                chunk_vars,
                ref_cands,
                node_kinds,
            ) = collect_ast_indices_and_chunk_data(ast_data, lang=lang)
            # variable_id → node_type 매핑도 합침 (서브레이블용)
            node_kinds.update(variable_node_kinds)

            if data_dir and chunk_m and source_path_for_bundle:
                variable_chunk_input = [
                    {"variable_id": f.variable_id, "start_line": f.start_line, "end_line": f.end_line} for f in fields
                ]
                bundle = build_chunk_bundle_from_source(
                    source_path_for_bundle,
                    None,
                    chunk_m,
                    chunk_calls,
                    chunk_vars,
                    fields=variable_chunk_input,
                )
                if bundle:
                    p_for_key = Path(source_path_for_bundle)
                    rel_key: str
                    if source_dir:
                        try:
                            rel = p_for_key.resolve().relative_to(source_dir)
                            rel_key = "_".join(rel.parts)
                            for _ext in source_exts:
                                rel_key = rel_key.replace(_ext, "")
                        except ValueError:
                            rel_key = p_for_key.stem
                    else:
                        rel_key = p_for_key.stem
                    chunk_out = _get_chunk_bundle_dir_from_run_dir(run_dir)
                    save_chunk_bundle(bundle, rel_key, chunk_bundle_out_dir=chunk_out)
                    logger.debug("[CHUNK_BUNDLE:SAVE] rel_key=%s", rel_key)
                    mid_to_code = {
                        m.function_id: (getattr(m, "function_code", None) or "")
                        for m in bundle.methods
                        if getattr(m, "function_code", None)
                    }
                    for f in facts:
                        if f.function_id in mid_to_code and mid_to_code[f.function_id]:
                            f.code = mid_to_code[f.function_id]
                    fid_to_code = {
                        fl.variable_id: (getattr(fl, "variable_code", None) or "")
                        for fl in bundle.member_vars
                        if getattr(fl, "variable_code", None)
                    }
                    for fl in fields:
                        if fl.variable_id in fid_to_code and fid_to_code[fl.variable_id]:
                            fl.code = fid_to_code[fl.variable_id]

            logger.info(
                "[PHASE:1:AST] 파일 처리 완료 | file=%s methods=%d classes=%d",
                Path(path).name,
                len(facts),
                len(cidx),
            )
            yield {
                "facts": facts,
                "cidx": cidx,
                "fields": fields,
                "module_name_to_fqn": module_name_to_fqn,
                "sym_idx": sym_idx,
                "meth_idx": meth_idx,
                "meth_by_cls": meth_by_cls,
                "ref_cands": ref_cands,
                "node_kinds": node_kinds,
                "file_name": Path(path).name,
            }
        except Exception as e:  # noqa: SILENT_FAIL — 한 파일 AST 처리 실패가 다른 파일 진행을 막지 않도록 errlog 후 generator 종료 (해당 파일만 skip)
            logger.error("[PHASE:1:AST] 처리 실패 | file=%s error=%s", Path(path).name, e, exc_info=True)


__all__ = [
    "StatementNode",
    "BaseAstProcessor",
    "FrameworkAstProcessor",
    "ModuleInfo",
    "StatementCollector",
    "MODULE_TYPES",
    "FUNCTION_TYPES",
    "VARIABLE_TYPES",
    "CONSTANT_TYPES",
    "ANALYZABLE_NODE_TYPES",
    "SILENT_NODE_TYPES",
    "GRAPH_NODE_TYPES",
    # ast_walker exports
    "AstVisitorContext",
    "load_ast_json",
    "collect_ast_indices_and_chunk_data",
    "build_function_facts_and_module_index",
    "extract_code_structure",
    "_extract_package_fqns",
]
