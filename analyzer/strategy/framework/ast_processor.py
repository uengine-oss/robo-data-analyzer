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
# Exceptions: 모든 커스텀 예외는 RuntimeError로 대체됨
from util.text_utils import calculate_code_token, log_process

from analyzer.strategy.base.statement_node import StatementNode
from analyzer.strategy.base.batch import AnalysisBatch
from analyzer.strategy.base.processor import BaseAstProcessor


# ==================== 상수 정의 ====================
# 노드 타입 분류
CLASS_TYPES = frozenset(["CLASS", "INTERFACE", "ENUM"])
FIELD_TYPES = frozenset(["FIELD"])
METHOD_TYPES = frozenset(["METHOD", "CONSTRUCTOR"])

# 그래프에 생성되는 노드 타입 (나머지는 분석 입력용)
GRAPH_NODE_TYPES = CLASS_TYPES | METHOD_TYPES | FIELD_TYPES

# 설정에서 가져오는 상수 (DBMS와 공통)
MAX_BATCH_TOKEN = settings.batch.max_batch_token
MAX_CONCURRENCY = settings.concurrency.max_concurrency
STATIC_QUERY_BATCH_SIZE = settings.batch.static_query_batch_size
MAX_SUMMARY_CHUNK_TOKEN = settings.batch.max_summary_chunk_token
MAX_CONTEXT_TOKEN = settings.batch.max_context_token
PARENT_EXPAND_THRESHOLD = settings.batch.parent_expand_threshold

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
        self.package_name = package_name  # 패키지명 (com.example.service)


# ==================== RuleLoader 헬퍼 ====================
def _rule_loader() -> RuleLoader:
    return RuleLoader(target_lang="framework")


def analyze_code(code: str, context: str, ranges: list, count: int, api_key: str, locale: str) -> Dict[str, Any]:
    """코드 범위별 분석"""
    inputs = {"code": code, "ranges": ranges, "count": count, "locale": locale}
    if context.strip():
        inputs["context"] = context
    return _rule_loader().execute(
        "analysis",
        inputs,
        api_key,
    )


def analyze_class_user_story(summary: str, api_key: str, locale: str) -> Dict[str, Any]:
    """클래스 User Story + AC 생성."""
    return _rule_loader().execute(
        "class_user_story",
        {"summary": summary, "locale": locale},
        api_key,
    )


def analyze_association(class_name: str, class_meta: str, fields: List[Dict[str, Any]], api_key: str, locale: str) -> Dict[str, Any]:
    """필드 기반 Association 후보 관계 분석."""
    return _rule_loader().execute(
        "association",
        {
            "class_name": class_name,
            "class_meta": class_meta,
            "fields": fields,
            "locale": locale,
        },
        api_key,
    )


def analyze_stereotype(summaries: dict, api_key: str, locale: str, class_meta: str = "") -> Dict[str, Any]:
    """클래스 전체 요약 + 스테레오타입 통합 분석."""
    inputs: Dict[str, Any] = {"summaries": summaries, "locale": locale}
    if class_meta:
        inputs["class_meta"] = class_meta
    return _rule_loader().execute(
        "stereotype",
        inputs,
        api_key,
    )


# ==================== 노드 수집기 ====================
class StatementCollector:
    """AST를 후위순회하여 StatementNode와 클래스 정보를 수집합니다.
    
    ANTLR JSON의 code 속성을 직접 사용합니다.
    """

    def __init__(self, antlr_data: Dict[str, Any], directory: str, file_name: str):
        self.antlr_data = antlr_data
        self.directory = directory
        self.file_name = file_name
        self.nodes: List[StatementNode] = []
        self.classes: Dict[str, ClassInfo] = {}
        self._node_id = 0

    def _parse_code_to_lines(self, code: str, start_line: int, end_line: int) -> List[Tuple[int, str]]:
        """JSON code 속성을 [(line_no, text), ...] 형태로 파싱합니다.
        
        Args:
            code: '1: public class...\n2: ...' 형태의 문자열 (빈 문자열 가능)
            start_line: 노드 시작 라인
            end_line: 노드 종료 라인
            
        Returns:
            [(line_no, text), ...] 형태의 튜플 리스트
        """
        if not code:
            return []
        
        # \r\n 또는 \n으로 분리
        lines = code.replace('\r\n', '\n').split('\n')
        parsed_lines: List[Tuple[int, str]] = []
        
        for line in lines:
            if not line:
                continue
            # '123: text' 형태 파싱
            match = re.match(r'^(\d+):\s?(.*)', line)
            if not match:
                raise RuntimeError(f"코드 라인 파싱 실패: '{line}' (라인 {start_line}~{end_line})")
                line_no = int(match.group(1))
                text = match.group(2)
                parsed_lines.append((line_no, text))
        
        return parsed_lines

    def collect(self) -> Tuple[List[StatementNode], Dict[str, ClassInfo]]:
        """AST 전역을 순회하여 노드 목록과 클래스 정보를 생성합니다."""
        self._visit(self.antlr_data, None, None, None, None, None)
        return self.nodes, self.classes

    def _make_class_key(self, package_name: Optional[str], class_name: Optional[str], start_line: int) -> str:
        """클래스 고유키를 생성합니다 (패키지 + 클래스명으로 유일성 보장)."""
        base = class_name or f"anonymous_{start_line}"
        if package_name:
            # 패키지명 + 클래스명으로 고유키 생성
            return f"{package_name}.{base}"
        return f"{self.directory}:{self.file_name}:{base}:{start_line}"
    
    def _package_from_filepath(self, file_path: str) -> Optional[str]:
        """FILE 노드의 filePath 속성에서 패키지명을 추출합니다.
        
        예: 'com/example/service/UserService.java' → 'com.example.service'
        """
        if not file_path:
            return None
        # 경로 구분자 정규화
        normalized = file_path.replace('\\', '/')
        # 파일명 제거
        if '/' in normalized:
            dir_part = normalized.rsplit('/', 1)[0]
            return dir_part.replace('/', '.')
        return None

    def _visit(
        self,
        node: Dict[str, Any],
        parent: Optional[StatementNode],
        class_key: Optional[str],
        class_name: Optional[str],
        class_kind: Optional[str],
        package_name: Optional[str],
    ) -> Optional[StatementNode]:
        """재귀적으로 AST를 내려가며 StatementNode를 생성합니다.
        
        ANTLR JSON 속성만 사용. 정규식/소스파일 폴백 없음.
        모든 노드를 생성하되, 그래프에는 CLASS/METHOD/FIELD만 적재 (분석 입력용 노드 포함).
        """
        node_type = node["type"]
        start_line = node.get("startLine", 0)
        end_line = node.get("endLine", start_line)
        children = node.get("children", [])

        # AST JSON의 code 속성 (이미 "9: @Service\n10: ..." 형태)
        code = node.get('code', '')
        # lines는 get_compact_code()에서 사용되므로 파싱 필요
        line_entries = self._parse_code_to_lines(code, start_line, end_line)

        # FILE 노드: filePath에서 패키지 추출
        if node_type == "FILE":
            file_path = node.get('filePath', '')
            extracted_pkg = self._package_from_filepath(file_path)
            if extracted_pkg:
                package_name = extracted_pkg
            log_process("ANALYZE", "COLLECT", f"📦 패키지 발견: {package_name} (from filePath: {file_path})")

        # CLASS/INTERFACE/ENUM 노드 처리
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

        # 자식 전부 순회 (필터링/스킵 없음)
        child_nodes: List[StatementNode] = []
        for ch in children:
            cn = self._visit(ch, None, class_key, class_name, class_kind, package_name)
            if cn:
                child_nodes.append(cn)

        # 분석 가능 여부: METHOD/CONSTRUCTOR/FIELD가 LLM 분석 대상
        analyzable = node_type in METHOD_TYPES or node_type in FIELD_TYPES
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
            # 통합 필드
            unit_key=class_key,
            unit_name=class_name,
            unit_kind=class_kind,
            # Framework 전용 필드
            package_name=package_name,
            # AST JSON 메타데이터 (ANTLR 속성 직접 사용)
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

        # 분석 대상 노드 카운트
        if not analyzable and node_type not in CLASS_TYPES:
            st.summary = st.get_raw_code()
            st.completion_event.set()
        elif analyzable and class_key and class_key in self.classes:
            self.classes[class_key].pending_nodes += 1

        self.nodes.append(st)

        # VARIABLE 노드는 타입 매핑용 내부 노드일 뿐이라 수집 로그를 남기지 않는다
        if node_type != "VARIABLE":
            log_process(
                "ANALYZE",
                "COLLECT",
                f"✅ {node_type} 노드 수집 완료: 라인 {start_line}~{end_line}, 토큰 {token}, 자식 {len(child_nodes)}개",
            )
        return st


# ==================== AST 프로세서 본체 ====================
class FrameworkAstProcessor(BaseAstProcessor):
    """Framework AST 처리 및 LLM 분석 파이프라인
    
    BaseAstProcessor를 상속하여 공통 파이프라인 재사용.
    Framework 전용 로직만 구현.
    """

    def __init__(
        self,
        antlr_data: dict,
        directory: str,
        file_name: str,
        api_key: str,
        locale: str,
        last_line: int,
    ):
        """Framework Analyzer 초기화
        
        ANTLR JSON의 code 속성을 직접 사용. 소스 파일 폴백 없음.
        """
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
        """컨텍스트 생성에서 제외할 노드 타입 (base 추상 메서드 구현)"""
        return CLASS_TYPES

    async def _generate_node_contexts(self) -> None:
        """Framework: 정적 컨텍스트 구성 (LLM 호출 없음).
        
        CLASS 노드와 그 자식 노드에 CLASS 메타 정보를 컨텍스트로 설정.
        comment와 annotations를 직접 사용.
        """
        if not self._nodes:
            return
        
        for node in self._nodes:
            ctx_parts = []
            
            # CLASS 노드 자체의 컨텍스트 설정 (get_ancestor_context에서 사용)
            if node.node_type in CLASS_TYPES:
                if node.unit_name:
                    ctx_parts.append(f"클래스: {node.unit_name}")
                if node.comment:
                    ctx_parts.append(f"클래스 주석: {node.comment}")
                if node.annotations:
                    ctx_parts.append(f"클래스 어노테이션: {node.annotations}")
                if ctx_parts:
                    node.context = "[CONTEXT]\n" + "\n".join(ctx_parts) + "\n[/CONTEXT]"
            
            # CLASS의 자식 노드 컨텍스트 설정
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
        """Framework에서는 미사용 (정적 컨텍스트 구성으로 대체).
        
        _generate_node_contexts()를 오버라이드하여 이 메서드를 호출하지 않음.
        base 추상 메서드 구현 의무를 위해 유지.
        """
        return ("", "")

    # =========================================================================
    # 정적 그래프 쿼리 생성
    # =========================================================================

    def _build_static_node_queries(self, node: StatementNode) -> List[Union[str, Dict[str, Any]]]:
        """정적 노드 생성 쿼리 리스트를 반환합니다 (파라미터화).
        
        CLASS/INTERFACE/ENUM + METHOD/CONSTRUCTOR + FIELD만 Neo4j에 생성.
        ANTLR JSON에는 FILE/CLASS/FIELD/METHOD만 존재하므로 스킵할 노드 없음 (방어적 필터링).
        스테레오타입은 stereotype.yaml LLM에서 Summary와 함께 처리.
        """
        queries: List[Union[str, Dict[str, Any]]] = []
        label = node.node_type
        
        # 그래프 노드 타입 필터링: CLASS/METHOD/FIELD만 생성
        if label not in GRAPH_NODE_TYPES:
            return queries
        
        # name: ANTLR의 name 속성 직접 사용
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
        
        # 기본 파라미터
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

        # 동적 SET 절 구성 (스네이크케이스 통일)
        set_parts = [
            "__cy_n__.end_line = $end_line",
            "__cy_n__.name = $node_name",
            "__cy_n__.node_code = $node_code",
            "__cy_n__.token = $token",
            "__cy_n__.has_children = $has_children",
            "__cy_n__.updated_at = timestamp()",
        ]
        
        # AST JSON 메타데이터 속성 추가 (스네이크케이스)
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

        # CLASS/INTERFACE 등: class_name, package_name과 type 속성 추가
        if label in CLASS_TYPES and node.unit_name:
            set_parts.append("__cy_n__.class_name = $class_name")
            set_parts.append("__cy_n__.type = $class_type")
            params["class_name"] = node.unit_name
            params["class_type"] = label
            if node.package_name:
                set_parts.append("__cy_n__.package_name = $package_name")
                params["package_name"] = node.package_name
                set_parts.append("__cy_n__.fqn = $fqn")
                params["fqn"] = f"{node.package_name}.{node.unit_name}"
        elif node.unit_name:
            set_parts.append("__cy_n__.class_name = $class_name")
            params["class_name"] = node.unit_name

        if node.has_children:
            # Framework용 preserve_types 설정 (메서드 시그니처 보존)
            preserve_types = METHOD_TYPES
            set_parts.append("__cy_n__.summarized_code = $summarized_code")
            params["summarized_code"] = node.get_placeholder_code(preserve_types)

        set_clause = ", ".join(set_parts)
        
        # CLASS/INTERFACE/ENUM 노드: package + class_name으로 MERGE (중복 방지)
        # 분석 범위 내 클래스는 :INTERNAL 레이블 추가
        if label in ("CLASS", "INTERFACE", "ENUM") and node.unit_name:
            if node.package_name:
                # 패키지명 + 클래스명으로 유일성 보장 + INTERNAL 레이블
                query = (
                    f"MERGE (__cy_n__:{label}:INTERNAL {{package_name: $package_name, class_name: $class_name}})\n"
                    f"ON CREATE SET __cy_n__.created_at = timestamp()\n"
                    f"SET __cy_n__.start_line = $start_line, __cy_n__.directory = $directory, __cy_n__.file_name = $file_name, {set_clause}\n"
                    f"RETURN __cy_n__"
                )
            else:
                # 패키지명이 없는 경우 INTERNAL 레이블만 사용
                query = (
                    f"MERGE (__cy_n__:{label}:INTERNAL {{class_name: $class_name}})\n"
                    f"ON CREATE SET __cy_n__.created_at = timestamp()\n"
                    f"SET __cy_n__.start_line = $start_line, __cy_n__.directory = $directory, __cy_n__.file_name = $file_name, {set_clause}\n"
                    f"RETURN __cy_n__"
                )
            queries.append({"query": query, "params": params})
            
            # 패키지 노드 계층 구조 생성 및 BELONGS_TO_PACKAGE 관계
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
        """패키지 계층 구조 노드와 관계를 생성하는 쿼리를 반환합니다.
        
        예: com.kbstar.kee.dc 패키지의 경우
        - PACKAGE(name: "com")
        - PACKAGE(name: "kbstar", fqn: "com.kbstar") -[:CONTAINS_PACKAGE]-> PACKAGE(name: "com")
        - PACKAGE(name: "kee", fqn: "com.kbstar.kee") -[:CONTAINS_PACKAGE]-> ...
        - PACKAGE(name: "dc", fqn: "com.kbstar.kee.dc") -[:CONTAINS_PACKAGE]-> ...
        - CLASS -[:BELONGS_TO_PACKAGE]-> PACKAGE(fqn: "com.kbstar.kee.dc")
        """
        queries: List[Dict[str, Any]] = []
        parts = package_name.split(".")
        
        # 각 패키지 세그먼트에 대해 노드 생성
        for i in range(len(parts)):
            segment = parts[i]
            fqn = ".".join(parts[:i + 1])
            parent_fqn = ".".join(parts[:i]) if i > 0 else None
            
            # PACKAGE 노드 생성 (MERGE)
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
            
            # 부모 패키지와의 CONTAINS_PACKAGE 관계 생성
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
        
        # CLASS -[:BELONGS_TO_PACKAGE]-> PACKAGE 관계 생성
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
        """정적 관계 쿼리를 생성합니다 (파라미터화).
        
        생성 순서:
        1. 구조적 관계 (HAS_METHOD, HAS_FIELD, CONTAINS) - CLASS/METHOD/FIELD 간만
        2. 상속/구현 관계 (EXTENDS, IMPLEMENTS) - AST 속성 기반
        3. 패키지 계층
        4. 후처리 (EXTERNAL → INTERNAL 변환)
        
        정규식 기반 USES 로직 전부 삭제됨.
        """
        queries: List[Union[str, Dict[str, Any]]] = []
        
        # 1. 구조적 관계 생성 (CLASS/METHOD/FIELD 간만)
        for node in self._nodes:
            if not node.parent:
                continue
            
            parent = node.parent
            
            # File → 최상위 타입만 CONTAINS
            if parent.node_type == "FILE" and node.node_type in CLASS_TYPES:
                queries.append(self._build_contains_query(parent, node))
            # Class → Method: HAS_METHOD
            elif node.node_type in METHOD_TYPES and parent.node_type in CLASS_TYPES:
                queries.append(self._build_has_method_query(parent, node))
            # Class → Field: HAS_FIELD
            elif node.node_type in FIELD_TYPES and parent.node_type in CLASS_TYPES:
                queries.append(self._build_has_field_query(parent, node))
        
        # 2. CLASS 노드의 상속/구현 관계 (AST 속성에서 직접, EXTERNAL_CLASS 포함)
        for node in self._nodes:
            if node.node_type in CLASS_TYPES:
                queries.extend(self._build_inheritance_from_ast(node))
        
        # 3. 후처리: EXTERNAL → INTERNAL 전환 (상속/구현만 - Phase 1 파일 순서 문제)
        # ASSOCIATION/COMPOSITION/AGGREGATION은 Phase 2에서 처리되며,
        # 그 시점에는 모든 INTERNAL이 이미 존재하므로 후처리 불필요.
        queries.append({
            "query": (
                "MATCH (src)-[r:EXTENDS]->(ext:EXTERNAL)\n"
                "MATCH (real:CLASS:INTERNAL) WHERE toLower(real.class_name) = toLower(ext.class_name)\n"
                "MERGE (src)-[:EXTENDS]->(real)\n"
                "DELETE r\n"
                "RETURN count(*) AS extends_resolved"
            ),
            "params": {}
        })
        queries.append({
            "query": (
                "MATCH (src)-[r:IMPLEMENTS]->(ext:EXTERNAL)\n"
                "MATCH (real:INTERFACE:INTERNAL) WHERE toLower(real.class_name) = toLower(ext.class_name)\n"
                "MERGE (src)-[:IMPLEMENTS]->(real)\n"
                "DELETE r\n"
                "RETURN count(*) AS implements_resolved"
            ),
            "params": {}
        })
        
        # EXTERNAL 노드 정리 (INTERNAL 매칭이 있는 중복 EXTERNAL만 제거)
        queries.append({
            "query": (
                "MATCH (ext:EXTERNAL)\n"
                "WHERE EXISTS { MATCH (real:INTERNAL) WHERE (real:CLASS OR real:INTERFACE)\n"
                "  AND toLower(real.class_name) = toLower(ext.class_name) }\n"
                "DETACH DELETE ext\n"
                "RETURN count(*) AS external_cleaned"
            ),
            "params": {}
        })
        
        return queries
    
    def _build_has_method_query(self, parent: StatementNode, child: StatementNode) -> Dict[str, Any]:
        """HAS_METHOD 관계 쿼리 (파라미터화)"""
        if parent.package_name and parent.unit_name:
            return {
                "query": (
                    f"MATCH (__cy_p__:{parent.node_type} {{package_name: $package_name, class_name: $class_name}})\n"
                    f"MATCH (__cy_c__:{child.node_type} {{start_line: $child_line, directory: $directory, file_name: $file_name}})\n"
                    f"MERGE (__cy_p__)-[__cy_r__:HAS_METHOD]->(__cy_c__)\n"
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
                    f"MERGE (__cy_p__)-[__cy_r__:HAS_METHOD]->(__cy_c__)\n"
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
    
    def _build_has_field_query(self, parent: StatementNode, child: StatementNode) -> Dict[str, Any]:
        """HAS_FIELD 관계 쿼리 (파라미터화)"""
        if parent.package_name and parent.unit_name:
            return {
                "query": (
                    f"MATCH (__cy_p__:{parent.node_type} {{package_name: $package_name, class_name: $class_name}})\n"
                    f"MATCH (__cy_c__:{child.node_type} {{start_line: $child_line, directory: $directory, file_name: $file_name}})\n"
                    f"MERGE (__cy_p__)-[__cy_r__:HAS_FIELD]->(__cy_c__)\n"
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
                    f"MERGE (__cy_p__)-[__cy_r__:HAS_FIELD]->(__cy_c__)\n"
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
    
    async def _run_preprocessing(self) -> List[Union[str, Dict[str, Any]]]:
        """선행 처리: 필드 기반 ASSOCIATION 후보 관계 생성.
        
        패키지 계층은 _build_static_node_queries에서 처리.
        상속/구현 관계는 _build_relationship_queries에서 정적으로 처리.
        최종 판정(COMPOSITION/DEPENDENCY)은 analysis.yaml 메서드 분석에서 수행.
        """
        queries: List[Union[str, Dict[str, Any]]] = []
        
        # 1. 필드 기반 ASSOCIATION 후보 관계 생성 (LLM)
        for node in self._nodes:
            if node.node_type not in CLASS_TYPES:
                continue
            
            # FIELD 자식 노드 수집
            fields = []
            for child in node.children:
                if child.node_type in FIELD_TYPES and child.field_type:
                    fields.append({
                        "name": child.name,
                        "field_type": child.field_type,
                        "modifiers": child.modifiers,
                        "annotations": child.annotations,
                        "comment": child.comment,
                        "code": child.code,
                    })
            
            if not fields:
                continue
            
            # CLASS 노드의 comment와 annotations 조합
            class_meta_parts = []
            if node.comment:
                class_meta_parts.append(node.comment)
            if node.annotations:
                class_meta_parts.append(node.annotations)
            class_meta = '\n'.join(class_meta_parts) if class_meta_parts else ""
            
            # association.yaml LLM 호출
            result = await asyncio.to_thread(
                analyze_association,
                node.unit_name,
                class_meta,
                fields,
                self.api_key,
                self.locale,
            )
            validated = self.validate_dict_result(result, "association")
            for rel in validated.get("fieldRelations", []):
                target_class = rel.get("targetClass")
                rel_type = rel.get("type", "ASSOCIATION")
                multiplicity = rel.get("multiplicity", "1")
                field_name = rel.get("field", "")
                
                if not target_class:
                    continue
                
                # ASSOCIATION 생성: INTERNAL 먼저 찾고, 없으면 EXTERNAL 생성
                # Phase 2 시점에서 모든 INTERNAL이 이미 존재하므로,
                # 여기서 INTERNAL에 없는 타겟은 진짜 외부 클래스 → EXTERNAL 확정
                queries.append({
                    "query": (
                        f"MATCH (__cy_src__:{node.node_type}:INTERNAL {{package_name: $package_name, class_name: $class_name}})\n"
                        f"OPTIONAL MATCH (__cy_int__:INTERNAL) WHERE (__cy_int__:CLASS OR __cy_int__:INTERFACE)\n"
                        f"  AND toLower(__cy_int__.class_name) = toLower($target_class)\n"
                        f"WITH __cy_src__, __cy_int__\n"
                        f"CALL {{\n"
                        f"  WITH __cy_src__, __cy_int__\n"
                        f"  WITH __cy_src__, __cy_int__ WHERE __cy_int__ IS NOT NULL\n"
                        f"  MERGE (__cy_src__)-[:ASSOCIATION {{field: $field_name, multiplicity: $multiplicity}}]->(__cy_int__)\n"
                        f"  RETURN 1 AS done\n"
                        f"  UNION ALL\n"
                        f"  WITH __cy_src__, __cy_int__\n"
                        f"  WITH __cy_src__ WHERE __cy_int__ IS NULL\n"
                        f"  MERGE (__cy_ext__:CLASS:EXTERNAL {{class_name: $target_class}})\n"
                        f"  SET __cy_ext__.name = $target_class\n"
                        f"  MERGE (__cy_src__)-[:ASSOCIATION {{field: $field_name, multiplicity: $multiplicity}}]->(__cy_ext__)\n"
                        f"  RETURN 1 AS done\n"
                        f"}}\n"
                        f"RETURN done"
                    ),
                    "params": {
                        "package_name": node.package_name,
                        "class_name": node.unit_name,
                        "target_class": target_class,
                        "field_name": field_name,
                        "multiplicity": multiplicity,
                    }
                })
                log_process("ANALYZE", "ASSOCIATION",
                           f"   🔗 ASSOCIATION: {node.unit_name}.{field_name} → {target_class} ({multiplicity})")
        
        log_process("ANALYZE", "PREPROCESS", 
                   f"✅ 선행 처리 완료: {len(queries)}개 쿼리 생성")
        
        return queries
    
    
    def _build_inheritance_from_ast(self, node: StatementNode) -> List[Union[str, Dict[str, Any]]]:
        """CLASS 노드의 AST 속성에서 상속/구현 관계 쿼리를 생성합니다 (파라미터화).
        
        AST JSON의 extendsType, implementsTypes 속성을 활용합니다.
        
        변경사항:
        - 분석 범위 내 클래스/인터페이스와 먼저 매칭 시도
        - 매칭 실패 시 EXTERNAL_CLASS 노드 생성 후 관계 연결
        - 유틸리티/로거 클래스는 제외
        """
        queries: List[Union[str, Dict[str, Any]]] = []
        
        if not node.unit_name:
            return queries
        
        # EXTENDS 관계 (단일 상속)
        if node.extends_type:
            target_class = node.extends_type.strip()
            if target_class:
                    # 1. 먼저 EXTERNAL:CLASS 노드 생성 (MERGE이므로 이미 존재하면 재사용)
                    # 다중 레이블: CLASS + EXTERNAL 스테레오타입
                    queries.append({
                        "query": (
                            "MERGE (__cy_ext__:CLASS:EXTERNAL {class_name: $target_class})\n"
                            "SET __cy_ext__.name = $target_class\n"
                            "RETURN __cy_ext__"
                        ),
                        "params": {"target_class": target_class}
                    })
                    
                    # 2. 소스 클래스에서 EXTERNAL CLASS로 EXTENDS 관계 생성
                    queries.append({
                        "query": (
                            f"MATCH (__cy_src__:{node.node_type} {{package_name: $package_name, class_name: $class_name}})\n"
                            f"MATCH (__cy_ext__:CLASS:EXTERNAL {{class_name: $target_class}})\n"
                            f"MERGE (__cy_src__)-[__cy_r__:EXTENDS]->(__cy_ext__)\n"
                            f"RETURN __cy_r__"
                        ),
                        "params": {
                            "package_name": node.package_name,
                            "class_name": node.unit_name,
                            "target_class": target_class,
                        }
                    })
                    log_process("ANALYZE", "INHERITANCE", 
                               f"   🔗 상속: {node.unit_name} extends {target_class} (EXTERNAL)")
        
        # IMPLEMENTS 관계 (다중 구현)
        if node.implements_types:
            # 콤마로 구분된 인터페이스 목록 파싱
            interfaces = [i.strip() for i in node.implements_types.split(',')]
            for iface in interfaces:
                if not iface:
                    continue
                    
                # 분석 범위 내 INTERFACE와 직접 매칭 시도 (OPTIONAL MATCH 사용)
                # 매칭되면 해당 노드에 연결, 아니면 INTERFACE:EXTERNAL 생성
                queries.append({
                    "query": (
                        f"MATCH (__cy_src__:{node.node_type} {{package_name: $package_name, class_name: $class_name}})\n"
                        f"OPTIONAL MATCH (__cy_iface__:INTERFACE) WHERE toLower(__cy_iface__.class_name) = toLower($target_iface)\n"
                        f"WITH __cy_src__, __cy_iface__\n"
                        f"CALL {{\n"
                        f"  WITH __cy_src__, __cy_iface__\n"
                        f"  WITH __cy_src__, __cy_iface__ WHERE __cy_iface__ IS NOT NULL\n"
                        f"  MERGE (__cy_src__)-[:IMPLEMENTS]->(__cy_iface__)\n"
                        f"  RETURN 1 AS done\n"
                        f"  UNION ALL\n"
                        f"  WITH __cy_src__, __cy_iface__\n"
                        f"  WITH __cy_src__ WHERE __cy_iface__ IS NULL\n"
                        f"  MERGE (__cy_ext__:INTERFACE:EXTERNAL {{class_name: $target_iface}})\n"
                        f"  SET __cy_ext__.name = $target_iface\n"
                        f"  MERGE (__cy_src__)-[:IMPLEMENTS]->(__cy_ext__)\n"
                        f"  RETURN 1 AS done\n"
                        f"}}\n"
                        f"RETURN done"
                    ),
                    "params": {
                        "package_name": node.package_name,
                        "class_name": node.unit_name,
                        "target_iface": iface,
                    }
                })
                log_process("ANALYZE", "INHERITANCE", 
                           f"   🔗 구현: {node.unit_name} implements {iface}")
        
        return queries
    
    def _determine_multiplicity(self, type_str: str) -> str:
        """타입 문자열에서 다중성을 결정합니다.
        
        컬렉션 타입이면 '*', 아니면 '1'을 반환합니다.
        """
        collection_patterns = [
            r'List\s*<', r'Set\s*<', r'Collection\s*<', r'Map\s*<',
            r'ArrayList\s*<', r'HashSet\s*<', r'TreeSet\s*<',
            r'\[\]$',  # 배열
        ]
        
        for pattern in collection_patterns:
            if re.search(pattern, type_str):
                return '*'
        
        return '1'

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

    def _build_field_type_map(self) -> Dict[str, str]:
        """현재 파일의 FIELD 노드에서 field_name → fieldType 매핑을 구축합니다.
        
        CALLS 관계의 타겟 클래스 결정에 사용. ANTLR 속성 직접 사용.
        """
        field_map: Dict[str, str] = {}
        for node in self._nodes:
            if node.node_type in FIELD_TYPES and node.field_type and node.name:
                field_map[node.name] = node.field_type
        return field_map

    def _build_param_type_map(self, node: StatementNode) -> Dict[str, str]:
        """METHOD/CONSTRUCTOR 노드의 parameters 문자열에서 paramName → paramType 매핑을 생성합니다.
        
        ANTLR JSON의 parameters 필드는 원시 문자열이므로, 단순 휴리스틱으로 타입을 추출합니다.
        - 예: "KBData kbData" → {"kbData": "KBData"}
        - 예: "final List<CmbsTraderDDTO> listCmbsTraderDTO" → {"listCmbsTraderDTO": "List<CmbsTraderDDTO>"}
        
        파라미터 이름이 중복되거나 타입이 충돌하는 비정상 케이스는 무시합니다.
        """
        param_map: Dict[str, str] = {}

        if not node.parameters or not isinstance(node.parameters, str):
            return param_map

        raw = node.parameters.strip()
        if not raw:
            return param_map

        # 파라미터 목록을 , 기준으로 분리
        for part in raw.split(","):
            seg = part.strip()
            if not seg:
                continue

            # 괄호/주석 등 제거 (가벼운 방어적 처리)
            seg = seg.replace("(", " ").replace(")", " ")

            tokens = [t for t in seg.split() if t]
            if len(tokens) < 2:
                continue

            # 마지막 토큰: 파라미터 이름
            param_name = tokens[-1]
            # 나머지 토큰: 타입 + 수식자(final, @Nullable 등) → 마지막 토큰을 타입으로 간주
            type_tokens = tokens[:-1]
            param_type = type_tokens[-1]

            # 이미 다른 타입으로 등록된 이름은 모호하므로 스킵
            if param_name in param_map and param_map[param_name] != param_type:
                continue

            param_map[param_name] = param_type

        return param_map

    def _build_local_var_type_map(self, method_node: StatementNode) -> Dict[str, str]:
        """METHOD/CONSTRUCTOR 내부 VARIABLE 노드에서 varName → varType 매핑을 생성합니다.
        
        - 스코프 단위: 단일 메서드 내부만 대상으로 함 (메서드 밖 VARIABLE은 무시).
        - 타입 정보는 VARIABLE.field_type를 그대로 사용.
        - 동일 이름에 서로 다른 타입이 등장하면 모호하다고 보고 해당 이름은 스킵합니다.
        """
        local_map: Dict[str, str] = {}
        conflicts: set[str] = set()

        def visit(n: StatementNode) -> None:
            # Java에서는 중첩 메서드가 없으므로 METHOD/CONSTRUCTOR는 다시 내려가지 않음
            if n is not method_node and n.node_type in METHOD_TYPES:
                return

            for child in n.children:
                if child.node_type == "VARIABLE" and child.field_type and child.name:
                    var_name = child.name
                    var_type = child.field_type
                    if var_name in local_map and local_map[var_name] != var_type:
                        conflicts.add(var_name)
                    else:
                        local_map[var_name] = var_type

                # 재귀적으로 하위 블록 탐색
                if child.children:
                    visit(child)

        visit(method_node)

        # 모호한 이름 제거
        for name in conflicts:
            local_map.pop(name, None)

        return local_map

    def _build_analysis_queries(
        self, 
        batch: AnalysisBatch, 
        llm_result: Any,
        unit_summary_store: Optional[Dict[str, Dict[str, str]]],
    ) -> List[Union[str, Dict[str, Any]]]:
        """LLM 분석 결과를 쿼리로 변환 (파라미터화)
        
        응답 형식:
        - analysis[]: 각 범위별 summary, dependencies, calls
        
        처리:
        1. summary → METHOD 노드 속성 업데이트
        2. dependencies → relationType에 따라 분기:
           - DEPENDENCY: CLASS↔CLASS DEPENDENCY 관계 생성
           - COMPOSITION/AGGREGATION: 기존 ASSOCIATION을 해당 타입으로 승격
        3. calls → CALLS 관계 생성 (INTERNAL 클래스만)
        4. USES 관계 자동 파생 (DEPENDENCY/CALLS 존재 시)
        """
        queries: List[Union[str, Dict[str, Any]]] = []
        
        # 타입 검증 (실패 시 예외 발생 → 전체 분석 중단)
        llm_result = self.validate_dict_result(llm_result, "llm_result", batch.batch_id)
        analysis_list = llm_result.get("analysis", [])
        
        # FIELD.name → fieldType 매핑 (CALLS 타겟 결정용)
        field_map = self._build_field_type_map()
        
        # 1. 각 범위별 처리
        for node, analysis in zip(batch.nodes, analysis_list):
            # 메서드 스코프별 타입 맵 (로컬 > 파라미터 > 필드)
            param_type_map: Dict[str, str] = {}
            local_type_map: Dict[str, str] = {}

            if node.node_type in METHOD_TYPES:
                param_type_map = self._build_param_type_map(node)
                local_type_map = self._build_local_var_type_map(node)

            # 1-a. 요약 업데이트
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
                
                # 클래스별 summary 저장
                if unit_summary_store is not None and node.unit_key:
                    if node.unit_key in unit_summary_store:
                        key = f"{node.node_type}_{node.start_line}_{node.end_line}"
                        unit_summary_store[node.unit_key][key] = summary
        
            # 1-b. dependencies → relationType에 따라 UML 관계 생성 (CLASS↔CLASS)
            for dep in analysis.get("dependencies", []):
                target_class = dep.get("targetClass")
                if not target_class or not node.unit_name:
                    continue
                source_member = dep.get("sourceMember", "")
                usage = dep.get("usage", "")
                rel_type = dep.get("relationType", "DEPENDENCY")
                
                if rel_type in ("COMPOSITION", "AGGREGATION"):
                    # 기존 ASSOCIATION을 해당 타입으로 승격 (field/multiplicity 보존)
                    queries.append({
                        "query": (
                            f"MATCH (__cy_src__:INTERNAL) WHERE (__cy_src__:CLASS OR __cy_src__:INTERFACE)\n"
                            f"  AND toLower(__cy_src__.class_name) = toLower($src_class)\n"
                            f"MATCH (__cy_tgt__) WHERE (__cy_tgt__:CLASS OR __cy_tgt__:INTERFACE)\n"
                            f"  AND toLower(__cy_tgt__.class_name) = toLower($target_class)\n"
                            f"OPTIONAL MATCH (__cy_src__)-[__old__:ASSOCIATION]->(__cy_tgt__)\n"
                            f"WITH __cy_src__, __cy_tgt__, __old__, __old__.field AS f, __old__.multiplicity AS m\n"
                            f"DELETE __old__\n"
                            f"MERGE (__cy_src__)-[__cy_r__:{rel_type} {{field: COALESCE(f, ''), multiplicity: COALESCE(m, '1'), source_member: $source_member}}]->(__cy_tgt__)\n"
                            f"RETURN __cy_r__"
                        ),
                        "params": {
                            "src_class": node.unit_name,
                            "target_class": target_class,
                            "source_member": source_member,
                        }
                    })
                    log_process("ANALYZE", "RELATION_UPGRADE",
                               f"   🔗 ASSOCIATION→{rel_type}: {node.unit_name} → {target_class}")
                else:
                    # DEPENDENCY: CLASS↔CLASS 의존 관계 (더 강한 관계가 있으면 생성 안 함)
                    queries.append({
                        "query": (
                            f"MATCH (__cy_src__:INTERNAL) WHERE (__cy_src__:CLASS OR __cy_src__:INTERFACE)\n"
                            f"  AND toLower(__cy_src__.class_name) = toLower($src_class)\n"
                            f"MATCH (__cy_tgt__:INTERNAL) WHERE (__cy_tgt__:CLASS OR __cy_tgt__:INTERFACE)\n"
                            f"  AND toLower(__cy_tgt__.class_name) = toLower($target_class)\n"
                            f"OPTIONAL MATCH (__cy_src__)-[__rel__:ASSOCIATION|COMPOSITION|AGGREGATION]->(__cy_tgt__)\n"
                            f"WITH __cy_src__, __cy_tgt__, __rel__\n"
                            f"WHERE __rel__ IS NULL\n"
                            f"MERGE (__cy_src__)-[__cy_r__:DEPENDENCY {{source_member: $source_member, usage: $usage}}]->(__cy_tgt__)\n"
                            f"RETURN __cy_r__"
                        ),
                        "params": {
                            "src_class": node.unit_name,
                            "target_class": target_class,
                            "source_member": source_member,
                            "usage": usage,
                        }
                    })
                
                # USES 관계 파생: CLASS → USES → 타겟 CLASS (모든 relationType 공통)
                queries.append({
                    "query": (
                        f"MATCH (__cy_src__:INTERNAL) WHERE (__cy_src__:CLASS OR __cy_src__:INTERFACE)\n"
                        f"  AND toLower(__cy_src__.class_name) = toLower($src_class)\n"
                        f"MATCH (__cy_tgt__:INTERNAL) WHERE (__cy_tgt__:CLASS OR __cy_tgt__:INTERFACE)\n"
                        f"  AND toLower(__cy_tgt__.class_name) = toLower($target_class)\n"
                        f"  AND __cy_src__ <> __cy_tgt__\n"
                        f"MERGE (__cy_src__)-[:USES]->(__cy_tgt__)\n"
                        f"RETURN __cy_src__, __cy_tgt__"
                    ),
                    "params": {
                        "src_class": node.unit_name,
                        "target_class": target_class,
                    }
                })
            
            # 1-c. calls → CALLS 관계 생성 (INTERNAL 클래스만)
            for call_expr in analysis.get("calls", []):
                if not isinstance(call_expr, str) or "." not in call_expr:
                    continue
                
                parts = call_expr.split(".", 1)
                object_name = parts[0].strip()
                method_name = parts[1].strip() if len(parts) > 1 else ""
                
                if not object_name or not method_name:
                    continue
                
                # 타겟 클래스 결정: 로컬 변수 → 파라미터 → 필드 → (대문자 시작) 클래스명
                target_class = (
                    local_type_map.get(object_name)
                    or param_type_map.get(object_name)
                    or field_map.get(object_name)
                )
                
                # 어떤 맵에서도 찾지 못했고, 대문자로 시작하면 클래스명으로 간주
                if not target_class and object_name[0].isupper():
                    target_class = object_name
                
                if not target_class:
                    continue
                
                # CALLS 관계 생성 (INTERNAL 클래스만)
                queries.append({
                    "query": (
                        f"MATCH (__cy_m__:{node.node_type} {{start_line: $method_line, directory: $directory, file_name: $file_name}})\n"
                        f"MATCH (__cy_t__:INTERNAL) WHERE (__cy_t__:CLASS OR __cy_t__:INTERFACE)\n"
                        f"  AND toLower(__cy_t__.class_name) = toLower($target_class)\n"
                        f"MERGE (__cy_m__)-[__cy_r__:CALLS {{method: $call_method, code: $call_code}}]->(__cy_t__)\n"
                        f"RETURN __cy_r__"
                    ),
                    "params": {
                        "method_line": node.start_line,
                        "directory": self.full_directory,
                        "file_name": self.file_name,
                        "target_class": target_class,
                        "call_method": method_name,
                        "call_code": call_expr[:200],
                    }
                })
                
                # USES 관계 파생: CLASS → USES → CLASS
                if node.unit_name:
                    queries.append({
                        "query": (
                            f"MATCH (__cy_src__:INTERNAL) WHERE (__cy_src__:CLASS OR __cy_src__:INTERFACE)\n"
                            f"  AND toLower(__cy_src__.class_name) = toLower($src_class)\n"
                            f"MATCH (__cy_tgt__:INTERNAL) WHERE (__cy_tgt__:CLASS OR __cy_tgt__:INTERFACE)\n"
                            f"  AND toLower(__cy_tgt__.class_name) = toLower($target_class)\n"
                            f"  AND __cy_src__ <> __cy_tgt__\n"
                            f"MERGE (__cy_src__)-[:USES]->(__cy_tgt__)\n"
                            f"RETURN __cy_src__, __cy_tgt__"
                        ),
                        "params": {
                            "src_class": node.unit_name,
                            "target_class": target_class,
                        }
                    })
                    
                    # CLASS → USES → METHOD (다른 클래스의 메서드)
                    queries.append({
                        "query": (
                            f"MATCH (__cy_src__:INTERNAL) WHERE (__cy_src__:CLASS OR __cy_src__:INTERFACE)\n"
                            f"  AND toLower(__cy_src__.class_name) = toLower($src_class)\n"
                            f"MATCH (__cy_tgt_cls__:INTERNAL) WHERE (__cy_tgt_cls__:CLASS OR __cy_tgt_cls__:INTERFACE)\n"
                            f"  AND toLower(__cy_tgt_cls__.class_name) = toLower($target_class)\n"
                            f"MATCH (__cy_tgt_cls__)-[:HAS_METHOD]->(__cy_tgt_m__:METHOD)\n"
                            f"  WHERE toLower(__cy_tgt_m__.name) = toLower($call_method)\n"
                            f"MERGE (__cy_src__)-[:USES]->(__cy_tgt_m__)\n"
                            f"RETURN __cy_src__, __cy_tgt_m__"
                        ),
                        "params": {
                            "src_class": node.unit_name,
                            "target_class": target_class,
                            "call_method": method_name,
                        }
                    })
        
        return queries
    
    async def _process_unit_summaries(
        self, 
        unit_summary_store: Dict[str, Dict[str, str]]
    ) -> List[Union[str, Dict[str, Any]]]:
        """클래스별 Summary + Stereotype 통합 처리 (stereotype.yaml)"""
        queries: List[Union[str, Dict[str, Any]]] = []
        
        classes = self._unit_info
        if not classes:
            return queries
        
        for class_key, info in classes.items():
            summaries = unit_summary_store.get(class_key, {})
            if not summaries:
                continue
            
            # 클래스 노드 찾기
            class_node = next(
                (n for n in self._nodes if n.start_line == info.node_start and n.node_type == info.kind),
                None,
            )
            if not class_node:
                continue
            
            if not class_node.ok:
                log_process("ANALYZE", "SUMMARY", f"⚠️ {info.name}: 하위 분석 실패로 최종 summary 생성 스킵")
                continue
            
            all_user_stories: List[Dict[str, Any]] = []
            final_summary = ""
            class_stereotype = ""
            method_stereotypes: Dict[str, str] = {}
            
            # 청크 분할
            chunks = self._split_summaries_by_token(summaries, MAX_SUMMARY_CHUNK_TOKEN)
            if not chunks:
                continue
            
            log_process("ANALYZE", "SUMMARY", f"📦 {info.name}: summary 청크 분할 완료 ({len(chunks)}개 청크)")
            
            # CLASS 노드의 comment와 annotations 조합
            class_meta_parts = []
            if class_node.comment:
                class_meta_parts.append(class_node.comment)
            if class_node.annotations:
                class_meta_parts.append(class_node.annotations)
            class_meta = '\n'.join(class_meta_parts) if class_meta_parts else ""
            
            # 청크별 처리 (stereotype.yaml 호출)
            async def process_chunk(chunk_idx: int, chunk: dict) -> Dict[str, Any]:
                chunk_tokens = calculate_code_token(json.dumps(chunk, ensure_ascii=False))
                log_process("ANALYZE", "SUMMARY", f"  → 청크 {chunk_idx + 1}/{len(chunks)} 처리 시작 (토큰: {chunk_tokens})")
                
                result = await asyncio.to_thread(
                    analyze_stereotype,
                    chunk,
                    self.api_key,
                    self.locale,
                    class_meta,
                )
                return self.validate_dict_result(result, "청크 분석")
            
            chunk_results_raw = await asyncio.gather(
                *[process_chunk(idx, chunk) for idx, chunk in enumerate(chunks)]
            )
            
            chunk_results = [r for r in chunk_results_raw if r]
            
            if not chunk_results:
                raise RuntimeError(f"{info.name}: 청크 처리 결과가 모두 비어있음")
            
            # 청크 통합
            if len(chunk_results) == 1:
                final_summary = chunk_results[0].get('summary', '')
                class_stereotype = chunk_results[0].get('classStereotype', '')
                method_stereotypes = chunk_results[0].get('methodStereotypes', {})
            else:
                # 다수 청크: summary만 통합, stereotype은 마지막 청크의 것 사용
                combined_summaries = {f"CHUNK_{idx + 1}": r.get('summary', '') for idx, r in enumerate(chunk_results)}
                result = await asyncio.to_thread(
                    analyze_stereotype,
                    combined_summaries,
                    self.api_key,
                    self.locale,
                    class_meta,
                )
                validated = self.validate_dict_result(result, "청크 통합")
                final_summary = validated.get('summary', '')
                class_stereotype = validated.get('classStereotype', '')
                method_stereotypes = validated.get('methodStereotypes', {})
            
            log_process("ANALYZE", "SUMMARY", f"✅ {info.name}: summary + stereotype 통합 완료")
            
            # User Story 생성
            if final_summary:
                us_result = await asyncio.to_thread(
                    analyze_class_user_story, final_summary, self.api_key, self.locale
                )
                validated = self.validate_dict_result(us_result, "User Story")
                all_user_stories = validated.get('user_stories', [])
            
            if all_user_stories:
                log_process("ANALYZE", "SUMMARY", f"✅ {info.name}: User Story {len(all_user_stories)}개")
            
            if not final_summary:
                continue
            
            # Neo4j 쿼리: CLASS summary + stereotype
            set_parts = ["__cy_n__.summary = $summary"]
            params: Dict[str, Any] = {
                "start_line": info.node_start,
                "directory": self.full_directory,
                "file_name": self.file_name,
                "summary": str(final_summary),
            }
            
            if class_stereotype:
                set_parts.append(f"__cy_n__.stereotype = $stereotype")
                params["stereotype"] = class_stereotype
            
            queries.append({
                "query": (
                    f"MATCH (__cy_n__:{info.kind} {{start_line: $start_line, directory: $directory, file_name: $file_name}})\n"
                    f"SET {', '.join(set_parts)}\n"
                    f"RETURN __cy_n__"
                ),
                "params": params,
            })
            
            # CLASS 멀티 레이블 추가 (stereotype)
            if class_stereotype:
                queries.append({
                    "query": (
                        f"MATCH (__cy_n__:{info.kind} {{start_line: $start_line, directory: $directory, file_name: $file_name}})\n"
                        f"SET __cy_n__:{class_stereotype}\n"
                f"RETURN __cy_n__"
                ),
                "params": {
                    "start_line": info.node_start,
                    "directory": self.full_directory,
                    "file_name": self.file_name,
                    }
                })
                log_process("ANALYZE", "STEREOTYPE", f"   🏷️ {info.name}: {class_stereotype}")
            
            # METHOD 멀티 레이블 추가 (stereotypes)
            for method_name, method_st in method_stereotypes.items():
                if not method_st:
                    continue
                # 해당 메서드 노드 찾기
                method_node = next(
                    (n for n in self._nodes 
                     if n.node_type in METHOD_TYPES 
                     and n.unit_key == class_key
                     and n.signature and method_name in n.signature),
                    None,
                )
                if method_node:
                    queries.append({
                        "query": (
                            f"MATCH (__cy_m__:METHOD {{start_line: $start_line, directory: $directory, file_name: $file_name}})\n"
                            f"SET __cy_m__.stereotype = $stereotype, __cy_m__:{method_st}\n"
                            f"RETURN __cy_m__"
                        ),
                        "params": {
                            "start_line": method_node.start_line,
                            "directory": self.full_directory,
                            "file_name": self.file_name,
                            "stereotype": method_st,
                }
            })
            
            # User Story 노드 생성
            if all_user_stories:
                for us_idx, us in enumerate(all_user_stories, 1):
                    us_id = us.get('id', f"US-{us_idx}")
                    
                    queries.append({
                        "query": (
                            f"MATCH (__cy_c__:{info.kind} {{start_line: $start_line, directory: $directory, file_name: $file_name}})\n"
                            f"MERGE (__cy_us__:UserStory {{id: $us_id, class_name: $class_name, directory: $directory, file_name: $file_name}})\n"
                            f"SET __cy_us__.role = $role, __cy_us__.goal = $goal, __cy_us__.benefit = $benefit\n"
                        f"MERGE (__cy_c__)-[:HAS_USER_STORY]->(__cy_us__)\n"
                        f"RETURN __cy_us__"
                        ),
                        "params": {
                            "start_line": info.node_start,
                            "directory": self.full_directory,
                            "file_name": self.file_name,
                            "us_id": us_id,
                            "class_name": info.name,
                            "role": us.get('role', ''),
                            "goal": us.get('goal', ''),
                            "benefit": us.get('benefit', ''),
                        }
                    })
                    
                    for ac_idx, ac in enumerate(us.get('acceptance_criteria', []), 1):
                        ac_id = ac.get('id', f"AC-{us_idx}-{ac_idx}")
                        
                        queries.append({
                            "query": (
                                f"MATCH (__cy_us__:UserStory {{id: $us_id, class_name: $class_name, directory: $directory, file_name: $file_name}})\n"
                                f"MERGE (__cy_ac__:AcceptanceCriteria {{id: $ac_id, user_story_id: $us_id, directory: $directory, file_name: $file_name}})\n"
                                f"SET __cy_ac__.title = $ac_title, __cy_ac__.given = $ac_given, __cy_ac__.when_clause = $ac_when, __cy_ac__.then_clause = $ac_then\n"
                            f"MERGE (__cy_us__)-[:HAS_AC]->(__cy_ac__)\n"
                            f"RETURN __cy_ac__"
                            ),
                            "params": {
                                "us_id": us_id,
                                "class_name": info.name,
                                "directory": self.full_directory,
                                "file_name": self.file_name,
                                "ac_id": ac_id,
                                "ac_title": ac.get('title', ''),
                                "ac_given": ac.get('given', ''),
                                "ac_when": ac.get('when', ''),
                                "ac_then": ac.get('then', ''),
                }
            })
        
        return queries
