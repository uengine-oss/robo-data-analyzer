"""Framework 코드 분석기 - Java/Kotlin AST → Neo4j 그래프

ANTLR JSON 결과물만 사용. 소스 파일 직접 읽기 없음.

분석 파이프라인:
1. AST 수집 (StatementCollector) - ANTLR JSON 순회
2. 정적 그래프 생성 (CLASS, METHOD, FIELD 노드)
3. 상속/구현 관계 추출 (EXTENDS, IMPLEMENTS) - AST 속성 기반
4. 필드 기반 UML ASSOCIATION 후보 생성 (association.yaml LLM)
5. LLM 배치 분석 (summary, dependencies, calls → DEPENDENCY/CALLS/USES 관계)
6. 클래스 요약 + 스테레오타입 통합 (stereotype.yaml LLM)
7. User Story 생성
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Dict, List, Optional, Tuple, Set, Union

from config.settings import settings
from util.rule_loader import RuleLoader
from util.text_utils import calculate_code_token, log_process

from client.neo4j_client import Neo4jClient

from analyzer.strategy.base.statement_node import StatementNode
from analyzer.strategy.base.batch import AnalysisBatch
from analyzer.strategy.base.processor import BaseAstProcessor


# ==================== 상수 정의 ====================
# 노드 타입 분류
CLASS_TYPES = frozenset(["CLASS", "INTERFACE", "ENUM"])
FIELD_TYPES = frozenset(["FIELD"])
METHOD_TYPES = frozenset(["METHOD", "CONSTRUCTOR"])

# 분석 대상 노드 타입 (LLM 분석을 수행할 노드)
ANALYZABLE_NODE_TYPES = METHOD_TYPES | FIELD_TYPES

# 조용히 처리할 노드 타입 (로그 없이 별도 처리)
SILENT_NODE_TYPES = frozenset(["VARIABLE"])

# 그래프에 생성되는 노드 타입 (나머지는 분석 입력용)
GRAPH_NODE_TYPES = CLASS_TYPES | METHOD_TYPES | FIELD_TYPES

# 설정에서 가져오는 상수
MAX_BATCH_TOKEN = settings.batch.max_batch_token
MAX_SUMMARY_CHUNK_TOKEN = settings.batch.max_summary_chunk_token

# ==================== 데이터 클래스 ====================
class ClassInfo:
    """클래스/인터페이스 정보"""
    __slots__ = ('key', 'name', 'kind', 'node_start', 'node_end', 'pending_nodes', 'finalized', 'package_name')
    
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


# ==================== RuleLoader 헬퍼 ====================
def _rule_loader() -> RuleLoader:
    return RuleLoader(target_lang="framework")


def analyze_code(code: str, context: str, ranges: list, count: int, api_key: str, locale: str) -> Dict[str, Any]:
    """코드 범위별 분석"""
    inputs = {"code": code, "ranges": ranges, "count": count, "locale": locale}
    if context.strip():
        inputs["context"] = context
    return _rule_loader().execute("analysis", inputs, api_key)


def analyze_class_user_story(summary: str, api_key: str, locale: str) -> Dict[str, Any]:
    """클래스 User Story + AC 생성"""
    return _rule_loader().execute("class_user_story", {"summary": summary, "locale": locale}, api_key)


def analyze_association(class_name: str, class_meta: str, fields: List[Dict[str, Any]], api_key: str, locale: str) -> Dict[str, Any]:
    """필드 기반 Association 후보 관계 분석"""
    return _rule_loader().execute(
        "association",
        {"class_name": class_name, "class_meta": class_meta, "fields": fields, "locale": locale},
        api_key,
    )


def analyze_stereotype(summaries: dict, api_key: str, locale: str, class_meta: str = "") -> Dict[str, Any]:
    """클래스 전체 요약 + 스테레오타입 통합 분석"""
    inputs: Dict[str, Any] = {"summaries": summaries, "locale": locale}
    if class_meta:
        inputs["class_meta"] = class_meta
    return _rule_loader().execute("stereotype", inputs, api_key)


def analyze_method_summary_enhancement(
    current_summary: str,
    called_methods: List[Dict[str, Any]],
    api_key: str,
    locale: str,
    class_name: Optional[str] = None,
    class_comment: Optional[str] = None,
    class_annotations: Optional[str] = None,
) -> Dict[str, Any]:
    """메서드 summary 보강 - 호출된 메서드 정보를 통합"""
    inputs: Dict[str, Any] = {
        "current_summary": current_summary,
        "called_methods": called_methods,
        "locale": locale,
    }
    if class_name:
        inputs["class_name"] = class_name
    if class_comment:
        inputs["class_comment"] = class_comment
    if class_annotations:
        inputs["class_annotations"] = class_annotations
    return _rule_loader().execute("method_summary_enhancement", inputs, api_key)


# ==================== 노드 수집기 ====================
class StatementCollector:
    """AST를 후위순회하여 StatementNode와 클래스 정보를 수집"""

    def __init__(self, antlr_data: Dict[str, Any], directory: str, file_name: str):
        self.antlr_data = antlr_data
        self.directory = directory
        self.file_name = file_name
        self.nodes: List[StatementNode] = []
        self.classes: Dict[str, ClassInfo] = {}
        self._node_id = 0

    def _parse_code_to_lines(self, code: str, start_line: int, end_line: int) -> List[Tuple[int, str]]:
        """코드를 라인별로 파싱하여 [(line_no, text), ...] 형태로 반환"""
        if not code:
            return []
        
        lines = code.replace('\r\n', '\n').split('\n')
        parsed_lines: List[Tuple[int, str]] = []
        
        for line in lines:
            if not line:
                continue
            
            match = re.match(r'^(\d+):\s?(.*)', line)
            if not match:
                raise RuntimeError(f"코드 라인 파싱 실패: '{line}' (라인 {start_line}~{end_line})")
            
            line_no = int(match.group(1))
            text = match.group(2)
            parsed_lines.append((line_no, text))
        
        return parsed_lines

    def collect(self) -> Tuple[List[StatementNode], Dict[str, ClassInfo]]:
        """AST 전역을 순회하여 노드 목록과 클래스 정보를 생성"""
        self._visit(self.antlr_data, None, None, None, None)
        return self.nodes, self.classes

    def _make_class_key(self, package_name: Optional[str], class_name: Optional[str], start_line: int) -> str:
        """클래스 고유키 생성"""
        base = class_name or f"anonymous_{start_line}"
        if package_name:
            return f"{package_name}.{base}"
        return f"{self.directory}:{self.file_name}:{base}:{start_line}"
    
    def _package_from_filepath(self, file_path: str) -> Optional[str]:
        """filePath에서 패키지명 추출"""
        if not file_path:
            return None
        normalized = file_path.replace('\\', '/')
        if '/' in normalized:
            dir_part = normalized.rsplit('/', 1)[0]
            return dir_part.replace('/', '.')
        return None

    def _visit(
        self,
        node: Dict[str, Any],
        class_key: Optional[str],
        class_name: Optional[str],
        class_kind: Optional[str],
        package_name: Optional[str],
    ) -> Optional[StatementNode]:
        """재귀적으로 AST를 순회하며 StatementNode 생성"""
        node_type = node["type"]
        start_line = node.get("startLine", 0)
        end_line = node.get("endLine", start_line)
        children = node.get("children", [])

        code = node.get('code', '')
        line_entries = self._parse_code_to_lines(code, start_line, end_line)

        # --- FILE 처리 ---
        if node_type == "FILE":
            file_path = node.get('filePath', '')
            extracted_pkg = self._package_from_filepath(file_path)
            if extracted_pkg:
                package_name = extracted_pkg
            log_process("ANALYZE", "COLLECT", f"📦 패키지 발견: {package_name} (from filePath: {file_path})")

        # --- CLASS 처리 ---
        if node_type in CLASS_TYPES:
            extracted_name = node.get('name')
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
                log_process("ANALYZE", "COLLECT", f"📋 클래스 발견: {package_name}.{extracted_name} ({node_type}, 라인 {start_line}~{end_line})")

        # --- 자식 순회 ---
        child_nodes: List[StatementNode] = []
        for ch in children:
            cn = self._visit(ch, class_key, class_name, class_kind, package_name)
            if cn:
                child_nodes.append(cn)

        # --- 분석 대상 여부 확인 ---
        analyzable = node_type in ANALYZABLE_NODE_TYPES
        is_silent = node_type in SILENT_NODE_TYPES
        
        # --- 노드 생성 ---
        token = calculate_code_token(code)

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
            name=node.get('name'),
            signature=node.get('signature'),
            modifiers=node.get('modifiers'),
            return_type=node.get('returnType'),
            parameters=node.get('parameters'),
            generic_type=node.get('genericType'),
            extends_type=node.get('extendsType'),
            implements_types=node.get('implementsTypes'),
            field_type=node.get('fieldType'),
            annotations=node.get('annotations'),
            comment=node.get('comment'),
            lines=line_entries,
        )
        for c in child_nodes:
            c.parent = st
        st.children.extend(child_nodes)

        # --- 완료 처리 ---
        if not analyzable and node_type not in CLASS_TYPES:
            st.summary = st.get_raw_code()
            st.completion_event.set()
        elif analyzable and class_key and class_key in self.classes:
            self.classes[class_key].pending_nodes += 1

        self.nodes.append(st)

        # --- 로그 출력 (조용히 처리할 노드는 제외) ---
        if not is_silent:
            log_process(
                "ANALYZE",
                "COLLECT",
                f"✅ {node_type} 노드 수집 완료: 라인 {start_line}~{end_line}, 토큰 {token}, 자식 {len(child_nodes)}개",
            )
        return st


# ==================== AST 프로세서 본체 ====================
class FrameworkAstProcessor(BaseAstProcessor):
    """Framework AST 처리 및 LLM 분석 파이프라인"""

    def __init__(
        self,
        antlr_data: dict,
        directory: str,
        file_name: str,
        api_key: str,
        locale: str,
        last_line: int,
    ):
        super().__init__(
            antlr_data=antlr_data,
            directory=directory,
            file_name=file_name,
            api_key=api_key,
            locale=locale,
            last_line=last_line,
        )

    # =========================================================================
    # BaseAstProcessor 추상 메서드 구현
    # =========================================================================
    
    def _collect_nodes(self) -> Tuple[List[StatementNode], Dict[str, ClassInfo]]:
        """AST 수집"""
        collector = StatementCollector(
            self.antlr_data, self.directory, self.file_name,
        )
        return collector.collect()

    def _get_excluded_context_types(self) -> Set[str]:
        """컨텍스트 생성에서 제외할 노드 타입"""
        return CLASS_TYPES

    async def _generate_node_contexts(self) -> None:
        """정적 컨텍스트 구성 (LLM 호출 없음)"""
        if not self._nodes:
            return
        
        for node in self._nodes:
            ctx_parts = []
            
            if node.node_type in CLASS_TYPES:
                if node.unit_name:
                    ctx_parts.append(f"클래스: {node.unit_name}")
                if node.comment:
                    ctx_parts.append(f"클래스 주석: {node.comment}")
                if node.annotations:
                    ctx_parts.append(f"클래스 어노테이션: {node.annotations}")
                if ctx_parts:
                    node.context = "[CONTEXT]\n" + "\n".join(ctx_parts) + "\n[/CONTEXT]"
            elif node.parent and node.parent.node_type in CLASS_TYPES:
                parent = node.parent
                if parent.unit_name:
                    ctx_parts.append(f"클래스: {parent.unit_name}")
                if parent.comment:
                    ctx_parts.append(f"클래스 주석: {parent.comment}")
                if parent.annotations:
                    ctx_parts.append(f"클래스 어노테이션: {parent.annotations}")
                if ctx_parts:
                    node.context = "[CONTEXT]\n" + "\n".join(ctx_parts) + "\n[/CONTEXT]"
            
            node.context_ready_event.set()

    async def _extract_node_context(
        self, 
        skeleton_code: str, 
        ancestor_context: str,
        sibling_code: str,
    ) -> Tuple[str, str]:
        """Framework에서는 미사용 (정적 컨텍스트 구성으로 대체)"""
        return ("", "")

    # =========================================================================
    # 정적 그래프 쿼리 생성
    # =========================================================================

    def _build_static_node_queries(self, node: StatementNode) -> List[Union[str, Dict[str, Any]]]:
        """정적 노드 생성 쿼리 생성"""
        queries: List[Union[str, Dict[str, Any]]] = []
        label = node.node_type
        
        # --- 필터링 ---
        if label not in GRAPH_NODE_TYPES:
            return queries
        
        # --- 이름 결정 ---
        if label in CLASS_TYPES:
            if not node.unit_name:
                raise RuntimeError(f"CLASS 노드에 unit_name이 없습니다: 라인 {node.start_line}")
            node_name = node.unit_name
        elif label in METHOD_TYPES:
            if not node.name:
                raise RuntimeError(f"METHOD 노드에 name이 없습니다: 라인 {node.start_line}")
            node_name = node.name
        elif label in FIELD_TYPES:
            if not node.name:
                raise RuntimeError(f"FIELD 노드에 name이 없습니다: 라인 {node.start_line}")
            node_name = node.name
        else:
            node_name = f"{label}[{node.start_line}]"
        
        # --- 파라미터 구성 ---
        params: Dict[str, Any] = {
            "start_line": node.start_line,
            "end_line": node.end_line,
            "directory": self.full_directory,
            "file_name": self.file_name,
            "node_name": node_name,
            "node_code": node.code,
            "token": node.token,
            "has_children": node.has_children,
        }

        set_parts = [
            "__cy_n__.end_line = $end_line",
            "__cy_n__.name = $node_name",
            "__cy_n__.node_code = $node_code",
            "__cy_n__.token = $token",
            "__cy_n__.has_children = $has_children",
            "__cy_n__.updated_at = timestamp()",
        ]
        
        if node.signature:
            set_parts.append("__cy_n__.signature = $signature")
            params["signature"] = node.signature
        if node.modifiers:
            set_parts.append("__cy_n__.modifiers = $modifiers")
            params["modifiers"] = node.modifiers
        if node.return_type:
            set_parts.append("__cy_n__.return_type = $return_type")
            params["return_type"] = node.return_type
        if node.parameters:
            set_parts.append("__cy_n__.parameters = $parameters")
            params["parameters"] = node.parameters
        if node.generic_type:
            set_parts.append("__cy_n__.generic_type = $generic_type")
            params["generic_type"] = node.generic_type
        if node.extends_type:
            set_parts.append("__cy_n__.extends_type = $extends_type")
            params["extends_type"] = node.extends_type
        if node.implements_types:
            set_parts.append("__cy_n__.implements_types = $implements_types")
            params["implements_types"] = node.implements_types
        if node.field_type:
            set_parts.append("__cy_n__.field_type = $field_type")
            params["field_type"] = node.field_type
        if node.annotations:
            set_parts.append("__cy_n__.annotations = $annotations")
            params["annotations"] = node.annotations
        if node.comment:
            set_parts.append("__cy_n__.comment = $comment")
            params["comment"] = node.comment

        if label in CLASS_TYPES and node.unit_name:
            set_parts.append("__cy_n__.class_name = $class_name")
            set_parts.append("__cy_n__.type = $class_type")
            params["class_name"] = node.unit_name
            params["class_type"] = label
            fqn = f"{node.package_name}.{node.unit_name}" if node.package_name else (node.unit_name or "")
            if node.package_name:
                set_parts.append("__cy_n__.package_name = $package_name")
                params["package_name"] = node.package_name
                set_parts.append("__cy_n__.fqn = $fqn")
                params["fqn"] = fqn
            set_parts.append("__cy_n__.node_id = $node_id")
            params["node_id"] = f"CLASS:{fqn}"
        elif node.unit_name:
            set_parts.append("__cy_n__.class_name = $class_name")
            params["class_name"] = node.unit_name

        if node.has_children:
            preserve_types = METHOD_TYPES
            set_parts.append("__cy_n__.summarized_code = $summarized_code")
            params["summarized_code"] = node.get_placeholder_code(preserve_types)

        set_clause = ", ".join(set_parts)
        
        # --- 쿼리 생성 ---
        if label in ("CLASS", "INTERFACE", "ENUM") and node.unit_name:
            if node.package_name:
                query = (
                    f"MERGE (__cy_n__:{label}:INTERNAL {{package_name: $package_name, class_name: $class_name}})\n"
                    f"ON CREATE SET __cy_n__.created_at = timestamp()\n"
                    f"SET __cy_n__.start_line = $start_line, __cy_n__.directory = $directory, __cy_n__.file_name = $file_name, {set_clause}\n"
                    f"RETURN __cy_n__"
                )
            else:
                query = (
                    f"MERGE (__cy_n__:{label}:INTERNAL {{class_name: $class_name}})\n"
                    f"ON CREATE SET __cy_n__.created_at = timestamp()\n"
                    f"SET __cy_n__.start_line = $start_line, __cy_n__.directory = $directory, __cy_n__.file_name = $file_name, {set_clause}\n"
                    f"RETURN __cy_n__"
                )
            queries.append({"query": query, "params": params})
            
            if node.package_name:
                package_queries = self._build_package_hierarchy_queries(node.package_name, node.unit_name, label)
                queries.extend(package_queries)
        else:
            query = (
                f"MERGE (__cy_n__:{label} {{start_line: $start_line, directory: $directory, file_name: $file_name}})\n"
                f"ON CREATE SET __cy_n__.created_at = timestamp()\n"
                f"SET {set_clause}\n"
                f"RETURN __cy_n__"
            )
            queries.append({"query": query, "params": params})
        return queries
    
    def _build_package_hierarchy_queries(
        self, 
        package_name: str, 
        class_name: str, 
        class_type: str
    ) -> List[Dict[str, Any]]:
        """패키지 계층 구조 노드와 관계 생성"""
        queries: List[Dict[str, Any]] = []
        parts = package_name.split(".")
        
        for i in range(len(parts)):
            segment = parts[i]
            fqn = ".".join(parts[:i + 1])
            parent_fqn = ".".join(parts[:i]) if i > 0 else None
            
            queries.append({
                "query": (
                    "MERGE (__cy_pkg__:PACKAGE {fqn: $fqn})\n"
                    "ON CREATE SET __cy_pkg__.created_at = timestamp()\n"
                    "SET __cy_pkg__.name = $segment, __cy_pkg__.updated_at = timestamp()\n"
                    "RETURN __cy_pkg__"
                ),
                "params": {
                    "fqn": fqn,
                    "segment": segment,
                }
            })
            
            if parent_fqn:
                queries.append({
                    "query": (
                        "MATCH (__cy_parent__:PACKAGE {fqn: $parent_fqn})\n"
                        "MATCH (__cy_child__:PACKAGE {fqn: $child_fqn})\n"
                        "MERGE (__cy_parent__)-[__cy_r__:CONTAINS_PACKAGE]->(__cy_child__)\n"
                        "RETURN __cy_r__"
                    ),
                    "params": {
                        "parent_fqn": parent_fqn,
                        "child_fqn": fqn,
                    }
                })
        
        queries.append({
            "query": (
                f"MATCH (__cy_class__:{class_type} {{package_name: $package_name, class_name: $class_name}})\n"
                "MATCH (__cy_pkg__:PACKAGE {fqn: $package_fqn})\n"
                "MERGE (__cy_class__)-[__cy_r__:BELONGS_TO_PACKAGE]->(__cy_pkg__)\n"
                "RETURN __cy_r__"
            ),
            "params": {
                "package_name": package_name,
                "class_name": class_name,
                "package_fqn": package_name,
            }
        })
        
        return queries

    def _build_relationship_queries(self) -> List[Union[str, Dict[str, Any]]]:
        """정적 관계 쿼리 생성"""
        queries: List[Union[str, Dict[str, Any]]] = []
        
        # --- 구조적 관계 생성 ---
        for node in self._nodes:
            if not node.parent:
                continue
            
            parent = node.parent
            
            if parent.node_type == "FILE" and node.node_type in CLASS_TYPES:
                queries.append(self._build_contains_query(parent, node))
            elif node.node_type in METHOD_TYPES and parent.node_type in CLASS_TYPES:
                queries.append(self._build_has_method_query(parent, node))
            elif node.node_type in FIELD_TYPES and parent.node_type in CLASS_TYPES:
                queries.append(self._build_has_field_query(parent, node))
        
        # --- UML: 상속/구현 관계 (uml_relations) ---
        from analyzer.strategy.framework import uml_relations
        class_nodes = [n for n in self._nodes if n.node_type in CLASS_TYPES]
        queries.extend(uml_relations.build_inheritance_queries(class_nodes))
        queries.extend(uml_relations.build_external_resolution_queries())
        
        return queries
    
    def _build_has_relationship_query(self, parent: StatementNode, child: StatementNode, rel_type: str) -> Dict[str, Any]:
        """HAS_METHOD 또는 HAS_FIELD 관계 쿼리 생성"""
        if parent.package_name and parent.unit_name:
            return {
                "query": (
                    f"MATCH (__cy_p__:{parent.node_type} {{package_name: $package_name, class_name: $class_name}})\n"
                    f"MATCH (__cy_c__:{child.node_type} {{start_line: $child_line, directory: $directory, file_name: $file_name}})\n"
                    f"MERGE (__cy_p__)-[__cy_r__:{rel_type}]->(__cy_c__)\n"
                    f"SET __cy_r__.updated_at = timestamp()\n"
                    f"RETURN __cy_r__"
                ),
                "params": {
                    "package_name": parent.package_name,
                    "class_name": parent.unit_name,
                    "child_line": child.start_line,
                    "directory": self.full_directory,
                    "file_name": self.file_name,
                }
            }
        else:
            return {
                "query": (
                    f"MATCH (__cy_p__:{parent.node_type} {{start_line: $parent_line, directory: $directory, file_name: $file_name}})\n"
                    f"MATCH (__cy_c__:{child.node_type} {{start_line: $child_line, directory: $directory, file_name: $file_name}})\n"
                    f"MERGE (__cy_p__)-[__cy_r__:{rel_type}]->(__cy_c__)\n"
                    f"SET __cy_r__.updated_at = timestamp()\n"
                    f"RETURN __cy_r__"
                ),
                "params": {
                    "parent_line": parent.start_line,
                    "child_line": child.start_line,
                    "directory": self.full_directory,
                    "file_name": self.file_name,
                }
            }
    
    def _build_has_method_query(self, parent: StatementNode, child: StatementNode) -> Dict[str, Any]:
        """HAS_METHOD 관계 쿼리 생성"""
        return self._build_has_relationship_query(parent, child, "HAS_METHOD")
    
    def _build_has_field_query(self, parent: StatementNode, child: StatementNode) -> Dict[str, Any]:
        """HAS_FIELD 관계 쿼리 생성"""
        return self._build_has_relationship_query(parent, child, "HAS_FIELD")

    async def _invoke_llm(self, batch: AnalysisBatch) -> Optional[Dict[str, Any]]:
        """LLM 호출 (일반 분석만)"""
        if not batch.ranges:
            raise RuntimeError(f"배치 #{batch.batch_id}에 분석할 범위가 없습니다")

        code, context = batch.build_payload()
        result = await asyncio.to_thread(
            analyze_code,
            code,
            context,
            batch.ranges,
            len(batch.ranges),
            self.api_key,
            self.locale,
        )
        return result

    def _build_analysis_queries(
        self, 
        batch: AnalysisBatch, 
        llm_result: Any,
        unit_summary_store: Optional[Dict[str, Dict[str, str]]],
    ) -> List[Union[str, Dict[str, Any]]]:
        """LLM 분석 결과를 쿼리로 변환"""
        from analyzer.strategy.framework import uml_relations

        queries: List[Union[str, Dict[str, Any]]] = []

        llm_result = self.validate_dict_result(llm_result, "llm_result", batch.batch_id)
        analysis_list = llm_result.get("analysis", [])

        field_map = uml_relations.build_field_type_map(self._nodes)

        for node, analysis in zip(batch.nodes, analysis_list):
            param_type_map = uml_relations.build_param_type_map(node) if node.node_type in METHOD_TYPES else {}
            local_type_map = uml_relations.build_local_var_type_map(node) if node.node_type in METHOD_TYPES else {}

            # --- Summary 처리 ---
            summary = analysis.get("summary")
            if summary:
                queries.append({
                    "query": (
                        f"MATCH (__cy_n__:{node.node_type} {{start_line: $start_line, directory: $directory, file_name: $file_name}})\n"
                        f"SET __cy_n__.summary = $summary\n"
                        f"RETURN __cy_n__"
                    ),
                    "params": {
                        "start_line": node.start_line,
                        "directory": self.full_directory,
                        "file_name": self.file_name,
                        "summary": str(summary),
                    }
                })
                
                if unit_summary_store and node.unit_key and node.unit_key in unit_summary_store:
                    key = f"{node.node_type}_{node.start_line}_{node.end_line}"
                    unit_summary_store[node.unit_key][key] = summary
        
            # --- UML: Dependencies + Calls (uml_relations) ---
            queries.extend(uml_relations.build_dependency_and_call_queries(
                node, analysis, param_type_map, local_type_map, field_map,
                self.full_directory, self.file_name,
            ))
        
        return queries