"""DBMS 코드 분석기 - PL/SQL AST → Neo4j 그래프

프로시저/함수 분석에 필요한 정보를 추출합니다.

분석 파이프라인:
1. AST 수집 (StatementCollector)
2. 정적 그래프 생성 (PROCEDURE, FUNCTION 노드)
3. DML 문 분석 (테이블/컬럼 관계)
4. LLM 배치 분석 (요약, 변수 타입)
5. 프로시저 요약 및 User Story 생성

리팩토링: BaseAstProcessor 상속으로 공통 로직 재사용
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple, Set, Union

from config.settings import settings
from util.rule_loader import RuleLoader
from util.text_utils import calculate_code_token, parse_table_identifier, log_process

from analyzer.strategy.base.statement_node import StatementNode
from analyzer.strategy.base.batch import AnalysisBatch
from analyzer.strategy.base.processor import BaseAstProcessor


# ==================== 상수 정의 ====================
# 노드 타입 분류
PROCEDURE_TYPES = frozenset(["PROCEDURE", "FUNCTION", "CREATE_PROCEDURE_BODY", "TRIGGER", "BEGIN"])
NON_ANALYSIS_TYPES = frozenset(["CREATE_PROCEDURE_BODY", "FILE", "PROCEDURE", "FUNCTION", "DECLARE", "TRIGGER", "SPEC"])
DML_STATEMENT_TYPES = frozenset(["SELECT", "INSERT", "UPDATE", "DELETE", "MERGE", "EXECUTE_IMMEDIATE", "FETCH", "CREATE_TEMP_TABLE", "CTE", "OPEN_CURSOR", "CURSOR_VARIABLE"])
VARIABLE_DECLARATION_TYPES = frozenset(["PACKAGE_VARIABLE", "DECLARE", "SPEC"])

# 관계 매핑
TABLE_RELATIONSHIP_MAP = {"r": "FROM", "w": "WRITES"}
VARIABLE_ROLE_MAP = {
    "PACKAGE_VARIABLE": "패키지 전역 변수",
    "DECLARE": "변수 선언및 초기화",
    "SPEC": "함수 및 프로시저 입력 매개변수",
}

# 설정에서 가져오는 상수
VARIABLE_CONCURRENCY = settings.concurrency.variable_concurrency
MAX_BATCH_TOKEN = settings.batch.max_batch_token
MAX_CONCURRENCY = settings.concurrency.max_concurrency
MAX_SUMMARY_CHUNK_TOKEN = settings.batch.max_summary_chunk_token
MAX_CONTEXT_TOKEN = settings.batch.max_context_token
PARENT_EXPAND_THRESHOLD = settings.batch.parent_expand_threshold


# ==================== 데이터 클래스 ====================
class ProcedureInfo:
    """프로시저/함수 정보"""
    __slots__ = ('key', 'procedure_type', 'procedure_name', 'schema_name', 'start_line', 'end_line', 'pending_nodes')
    
    def __init__(
        self,
        key: str,
        procedure_type: str,
        procedure_name: str,
        schema_name: Optional[str],
        start_line: int,
        end_line: int,
        pending_nodes: int = 0,
    ):
        self.key = key
        self.procedure_type = procedure_type
        self.procedure_name = procedure_name
        self.schema_name = schema_name
        self.start_line = start_line
        self.end_line = end_line
        self.pending_nodes = pending_nodes


# ==================== 헬퍼 함수 ====================
def get_procedure_name_from_code(code: str) -> Tuple[Optional[str], Optional[str]]:
    """코드 문자열에서 스키마/프로시저 이름을 추출합니다."""
    pattern = re.compile(
        r"\b(?:CREATE\s+(?:OR\s+REPLACE\s+)?)?(?:PROCEDURE|FUNCTION|TRIGGER)\s+"
        r"((?:\"[^\"]+\"|[A-Za-z_][\w$#]*)"
        r"(?:\s*\.\s*(?:\"[^\"]+\"|[A-Za-z_][\w$#]*)){0,2})",
        re.IGNORECASE,
    )
    prefix_pattern = re.compile(r"^\d+\s*:\s*")
    normalized = prefix_pattern.sub("", code)
    match = pattern.search(normalized)
    if not match:
        return None, None
    parts = [segment.strip().strip('"') for segment in re.split(r"\s*\.\s*", match.group(1))]
    if len(parts) == 3:
        return parts[0], f"{parts[1]}.{parts[2]}"
    if len(parts) == 2:
        return parts[0], parts[1]
    if parts:
        return None, parts[0]
    return None, None


def build_statement_name(node_type: str, start_line: int) -> str:
    """노드 타입과 시작 라인을 조합한 식별자 문자열을 생성합니다."""
    return f"{node_type}[{start_line}]"


# ==================== RuleLoader 헬퍼 ====================
_rule_loader_cache: Optional[RuleLoader] = None

def _rule_loader() -> RuleLoader:
    """RuleLoader 인스턴스를 캐싱하여 재사용"""
    global _rule_loader_cache
    if _rule_loader_cache is None:
        _rule_loader_cache = RuleLoader(target_lang="dbms")
    return _rule_loader_cache


def analyze_code(code: str, context: str, ranges: list, count: int, api_key: str, locale: str) -> Dict[str, Any]:
    """코드 분석"""
    inputs = {"code": code, "ranges": ranges, "count": count, "locale": locale}
    if context.strip():
        inputs["context"] = context
    return _rule_loader().execute("analysis", inputs, api_key)


def analyze_dml_tables(code: str, context: str, ranges: list, api_key: str, locale: str) -> Dict[str, Any]:
    """DML 테이블 분석"""
    inputs = {"code": code, "ranges": ranges, "locale": locale}
    if context.strip():
        inputs["context"] = context
    return _rule_loader().execute("dml", inputs, api_key)


def analyze_summary_only(summaries: dict, api_key: str, locale: str, previous_summary: str = "") -> Dict[str, Any]:
    """프로시저/함수 전체 요약 생성 (Summary만)."""
    return _rule_loader().execute(
        "procedure_summary_only",
        {"summaries": summaries, "locale": locale, "previous_summary": previous_summary},
        api_key,
    )


def analyze_user_story(summary: str, api_key: str, locale: str) -> Dict[str, Any]:
    """프로시저/함수 User Story + AC 생성."""
    return _rule_loader().execute(
        "procedure_user_story",
        {"summary": summary, "locale": locale},
        api_key,
    )


def summarize_table_metadata(
    table_name: str,
    table_sentences: list,
    column_sentences: dict,
    column_metadata: dict,
    api_key: str,
    locale: str,
) -> Dict[str, Any]:
    return _rule_loader().execute(
        "table_summary",
        {
            "table_name": table_name,
            "table_sentences": table_sentences,
            "column_sentences": column_sentences,
            "column_metadata": column_metadata,
            "locale": locale,
        },
        api_key,
    )


def analyze_variables(declaration_code: str, api_key: str, locale: str) -> Dict[str, Any]:
    return _rule_loader().execute(
        "variables",
        {"declaration_code": declaration_code, "locale": locale},
        api_key,
    )


def extract_node_context(
    skeleton_code: str, 
    ancestor_context: str, 
    sibling_code: str,
    api_key: str, 
    locale: str,
) -> Tuple[str, str]:
    """부모 스켈레톤 + 이전 형제 코드에서 컨텍스트 추출"""
    result = _rule_loader().execute(
        "node_context",
        {
            "skeleton_code": skeleton_code, 
            "ancestor_context": ancestor_context, 
            "sibling_code": sibling_code,
            "locale": locale,
        },
        api_key,
    )

    if not isinstance(result, dict):
        raise ValueError(f"node_context 규칙이 dict가 아닌 값을 반환했습니다: {type(result)}")
    
    parent_ctx = result.get("parent_context", "").strip()
    sibling_ctx = result.get("sibling_context", "").strip()
    
    if not parent_ctx:
        raise ValueError("node_context 규칙이 빈 parent_context를 반환했습니다")
    
    return parent_ctx, sibling_ctx


_common_rule_loader_cache: Optional[RuleLoader] = None

def analyze_variable_summary(variables: List[Dict[str, Any]], api_key: str, locale: str) -> Dict[str, Any]:
    """변수 역할 목록을 하나의 요약 설명으로 통합합니다."""
    global _common_rule_loader_cache
    if _common_rule_loader_cache is None:
        _common_rule_loader_cache = RuleLoader(target_lang="common")
    return _common_rule_loader_cache.execute(
        "variable_summary",
        {"variables": variables, "locale": locale},
        api_key,
    )


# ==================== 노드 수집기 ====================
class StatementCollector:
    """AST를 후위순회하여 StatementNode와 프로시저 정보를 수집"""
    
    def __init__(self, antlr_data: Dict[str, Any], directory: str, file_name: str):
        self.antlr_data = antlr_data
        self.directory = directory
        self.file_name = file_name
        self.nodes: List[StatementNode] = []
        self.procedures: Dict[str, ProcedureInfo] = {}
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
            if match:
                line_no = int(match.group(1))
                text = match.group(2)
                parsed_lines.append((line_no, text))
            else:
                if parsed_lines:
                    parsed_lines.append((parsed_lines[-1][0] + 1, line))
                else:
                    parsed_lines.append((start_line, line))
        
        return parsed_lines

    def collect(self) -> Tuple[List[StatementNode], Dict[str, ProcedureInfo]]:
        """AST를 후위 순회하여 노드 목록과 프로시저 정보를 생성"""
        self._visit(self.antlr_data, current_proc=None, current_type=None, current_schema=None)
        return self.nodes, self.procedures

    def _make_proc_key(self, procedure_name: Optional[str], start_line: int) -> str:
        """프로시저 고유키를 생성합니다."""
        base = procedure_name or f"anonymous_{start_line}"
        return f"{self.directory}:{self.file_name}:{base}:{start_line}"

    def _should_treat_as_procedure(self, node_type: str, current_proc: Optional[str]) -> bool:
        """노드 타입이 프로시저로 처리되어야 하는지 판단합니다."""
        if node_type not in PROCEDURE_TYPES:
            return False
        if node_type == "BEGIN":
            return current_proc is None
        return True

    def _visit(
        self,
        node: Dict[str, Any],
        current_proc: Optional[str],
        current_type: Optional[str],
        current_schema: Optional[str],
    ) -> Optional[StatementNode]:
        """재귀적으로 AST를 순회하며 StatementNode 생성 및 부모-자식 관계 구축"""
        start_line = node['startLine']
        end_line = node['endLine']
        node_type = node['type']
        children = node.get('children', []) or []

        # --- 프로시저 처리 ---
        procedure_key = current_proc
        procedure_type = current_type
        schema_name = current_schema

        comment = node.get('comment')
        raw_code = node.get('code', '')
        line_entries = self._parse_code_to_lines(raw_code, start_line, end_line)
        code = '\n'.join(f"{line_no}: {text}" for line_no, text in line_entries)
        
        # --- 주석을 코드 앞에 prepend (LLM 컨텍스트 강화) ---
        if comment and comment.strip():
            code = f"/* {comment.strip()} */\n{code}"

        if self._should_treat_as_procedure(node_type, current_proc):
            if node_type == "BEGIN":
                procedure_key = self._make_proc_key(None, start_line)
                procedure_type = "BEGIN"
                schema_name = None
                proc_name = f"anonymous_{start_line}"
            else:
                name_from_json = node.get('name')
                schema_from_json = node.get('schema')
                
                if name_from_json:
                    name_candidate = name_from_json
                    schema_candidate = schema_from_json
                else:
                    schema_candidate, name_candidate = get_procedure_name_from_code(code)
                
                procedure_key = self._make_proc_key(name_candidate, start_line)
                procedure_type = node_type
                schema_name = schema_candidate
                proc_name = name_candidate or procedure_key
            
            if procedure_key not in self.procedures:
                self.procedures[procedure_key] = ProcedureInfo(
                    key=procedure_key,
                    procedure_type=procedure_type,
                    procedure_name=proc_name,
                    schema_name=schema_name,
                    start_line=start_line,
                    end_line=end_line,
                )
                log_process("ANALYZE", "COLLECT", f"📋 프로시저 선언 발견: {proc_name} (라인 {start_line}~{end_line})")

        # --- 자식 순회 ---
        child_nodes: List[StatementNode] = []
        for child in children:
            child_node = self._visit(child, procedure_key, procedure_type, schema_name)
            if child_node is not None:
                child_nodes.append(child_node)

        # --- 노드 생성 ---
        analyzable = node_type not in NON_ANALYSIS_TYPES
        token = calculate_code_token(code)
        dml = node_type in DML_STATEMENT_TYPES
        has_children = bool(child_nodes)

        proc_info = self.procedures.get(procedure_key) if procedure_key else None
        proc_name = proc_info.procedure_name if proc_info else None

        self._node_id += 1
        statement_node = StatementNode(
            node_id=self._node_id,
            start_line=start_line,
            end_line=end_line,
            node_type=node_type,
            code=code,
            token=token,
            has_children=has_children,
            analyzable=analyzable,
            unit_key=procedure_key,
            unit_name=proc_name,
            unit_kind=procedure_type,
            schema_name=schema_name,
            dml=dml,
            signature=node.get('signature'),
            parameters=node.get('parameters'),
            comment=comment,
            lines=line_entries,
        )
        for child_node in child_nodes:
            child_node.parent = statement_node
        statement_node.children.extend(child_nodes)

        # --- 완료 처리 ---
        if not analyzable:
            statement_node.summary = statement_node.get_raw_code()
            statement_node.completion_event.set()
        elif procedure_key and procedure_key in self.procedures:
            self.procedures[procedure_key].pending_nodes += 1

        self.nodes.append(statement_node)
        log_process("ANALYZE", "COLLECT", f"✅ {node_type} 노드 수집 완료: 라인 {start_line}~{end_line}, 토큰 {token}, 자식 {len(child_nodes)}개")
        return statement_node


# ==================== AST 프로세서 본체 ====================
class DbmsAstProcessor(BaseAstProcessor):
    """DBMS AST 처리 및 LLM 분석 파이프라인"""
    
    def __init__(
        self,
        antlr_data: dict,
        directory: str,
        file_name: str,
        api_key: str,
        locale: str,
        dbms: str,
        last_line: int,
        default_schema: str = "public",
        ddl_table_metadata: Optional[Dict[Tuple[str, str], Dict[str, Any]]] = None,
        name_case: str = "original",
    ):
        super().__init__(
            antlr_data=antlr_data,
            directory=directory,
            file_name=file_name,
            api_key=api_key,
            locale=locale,
            last_line=last_line,
        )
        
        self.dbms = (dbms or 'postgres').lower()
        self.default_schema = default_schema
        self._ddl_table_metadata = ddl_table_metadata or {}
        self.name_case = (name_case or 'original').lower()
        
        self._table_summary_store: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self._variable_roles: Dict[str, Dict[str, List[str]]] = {}

    def _apply_name_case(self, name: str) -> str:
        """대소문자 변환 적용"""
        if not name:
            return name
        if self.name_case == "uppercase":
            return name.upper()
        elif self.name_case == "lowercase":
            return name.lower()
        return name
    
    def _get_effective_schema(self, schema: Optional[str]) -> str:
        """스키마가 없으면 default_schema 사용"""
        return schema if schema else (self.default_schema if self.default_schema else 'public')
    
    def _get_ddl_metadata(self, schema: Optional[str], table_name: str) -> Tuple[Dict[str, Any], bool]:
        """DDL 메타데이터 조회 및 대소문자 변환 여부 결정"""
        effective_schema = self._get_effective_schema(schema)
        ddl_lookup_key = (
            (effective_schema.lower() if effective_schema else 'public'),
            table_name.lower()
        )
        ddl_meta = self._ddl_table_metadata.get(ddl_lookup_key, {})
        skip_case_conversion = bool(ddl_meta and ddl_meta.get('original_name'))
        return ddl_meta, skip_case_conversion
    
    def _resolve_table_name_with_ddl(self, schema_raw: Optional[str], name_raw: str) -> Tuple[str, str, bool]:
        """DDL 캐시를 고려하여 테이블명 정규화"""
        ddl_meta, skip_case_conversion = self._get_ddl_metadata(schema_raw, name_raw)
        
        if skip_case_conversion:
            effective_schema = self._get_effective_schema(schema_raw)
            schema_part = ddl_meta.get('original_schema', self._apply_name_case(effective_schema))
            name_part = ddl_meta.get('original_name')
        else:
            schema_part = self._apply_name_case(schema_raw) if schema_raw else self._apply_name_case(self.default_schema)
            name_part = self._apply_name_case(name_raw)
        
        return schema_part, name_part, skip_case_conversion
    
    def _resolve_column_name_with_ddl(self, column_name_raw: str, ddl_columns: Dict[str, Any]) -> str:
        """DDL 캐시를 고려하여 컬럼명 정규화"""
        ddl_col_meta = ddl_columns.get(column_name_raw.upper() if self.name_case == 'uppercase' else column_name_raw)
        if ddl_col_meta is not None:
            return column_name_raw.upper() if self.name_case == 'uppercase' else column_name_raw
        
        for ddl_col_name in ddl_columns.keys():
            if ddl_col_name.lower() == column_name_raw.lower():
                return ddl_col_name
        
        return self._apply_name_case(column_name_raw)

    # =========================================================================
    # BaseAstProcessor 추상 메서드 구현
    # =========================================================================
    
    def _collect_nodes(self) -> Tuple[List[StatementNode], Dict[str, ProcedureInfo]]:
        """AST 수집"""
        collector = StatementCollector(
            self.antlr_data, self.directory, self.file_name
        )
        return collector.collect()

    def _get_excluded_context_types(self) -> Set[str]:
        """컨텍스트 생성에서 제외할 노드 타입"""
        return PROCEDURE_TYPES

    def _use_dml_ranges(self) -> bool:
        """DBMS는 DML 범위 포함"""
        return True

    async def _extract_node_context(
        self, 
        skeleton_code: str, 
        ancestor_context: str,
        sibling_code: str,
    ) -> Tuple[str, str]:
        """부모 + 이전 형제 컨텍스트 추출"""
        return await asyncio.to_thread(
            extract_node_context,
            skeleton_code,
            ancestor_context,
            sibling_code,
            self.api_key,
            self.locale,
        )

    def _build_static_node_queries(self, node: StatementNode) -> List[Union[str, Dict[str, Any]]]:
        """정적 노드 생성 쿼리 생성"""
        queries: List[Union[str, Dict[str, Any]]] = []
        label = node.node_type
        
        # --- 이름 결정 ---
        if label == "FILE":
            node_name = self.file_name
        elif label in PROCEDURE_TYPES and node.unit_name:
            node_name = node.unit_name
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
            "__cy_n__.endLine = $end_line",
            "__cy_n__.name = $node_name",
            "__cy_n__.node_code = $node_code",
            "__cy_n__.token = $token",
            "__cy_n__.has_children = $has_children",
        ]
        
        if node.signature:
            set_parts.append("__cy_n__.signature = $signature")
            params["signature"] = node.signature
        if node.parameters:
            set_parts.append("__cy_n__.parameters = $parameters")
            params["parameters"] = node.parameters
        
        if label in PROCEDURE_TYPES and node.unit_name:
            set_parts.append("__cy_n__.procedure_name = $procedure_name")
            set_parts.append("__cy_n__.procedure_type = $procedure_type")
            params["procedure_name"] = node.unit_name
            params["procedure_type"] = label
            if node.schema_name:
                set_parts.append("__cy_n__.schema_name = $schema_name")
                params["schema_name"] = node.schema_name
        elif node.unit_name:
            set_parts.append("__cy_n__.procedure_name = $procedure_name")
            params["procedure_name"] = node.unit_name
            if node.schema_name:
                set_parts.append("__cy_n__.schema_name = $schema_name")
                params["schema_name"] = node.schema_name
        
        if node.has_children:
            set_parts.append("__cy_n__.summarized_code = $summarized_code")
            params["summarized_code"] = node.get_placeholder_code()
        
        set_clause = ", ".join(set_parts)
        
        # --- 쿼리 생성 ---
        if label in PROCEDURE_TYPES and node.unit_name:
            if node.schema_name:
                query = (
                    f"MERGE (__cy_n__:{label} {{schema_name: $schema_name, procedure_name: $procedure_name, directory: $directory, file_name: $file_name}})\n"
                    f"SET __cy_n__.startLine = $start_line, {set_clause}\n"
                    f"RETURN __cy_n__"
                )
            else:
                query = (
                    f"MERGE (__cy_n__:{label} {{procedure_name: $procedure_name, directory: $directory, file_name: $file_name}})\n"
                    f"SET __cy_n__.startLine = $start_line, {set_clause}\n"
                    f"RETURN __cy_n__"
                )
            queries.append({"query": query, "params": params})
        else:
            query = (
                f"MERGE (__cy_n__:{label} {{startLine: $start_line, directory: $directory, file_name: $file_name}})\n"
                f"SET {set_clause}\n"
                f"RETURN __cy_n__"
            )
            queries.append({"query": query, "params": params})
        return queries

    def _build_relationship_queries(self) -> List[Union[str, Dict[str, Any]]]:
        """정적 관계 쿼리 생성 (CONTAINS, PARENT_OF, NEXT)"""
        queries: List[Union[str, Dict[str, Any]]] = []
        
        for node in self._nodes or []:
            for child in node.children:
                if node.node_type == "FILE" and child.node_type in PROCEDURE_TYPES:
                    queries.append(self._build_contains_query(node, child))
                else:
                    queries.append(self._build_parent_of_query(node, child))
            
            prev = None
            for child in node.children:
                if prev:
                    queries.append(self._build_next_query(prev, child))
                prev = child
        
        return queries
    
    def _build_next_query(self, prev: StatementNode, current: StatementNode) -> Dict[str, Any]:
        """NEXT 관계 쿼리 생성"""
        return {
            "query": (
                f"MATCH (__cy_prev__:{prev.node_type} {{startLine: $prev_line, directory: $directory, file_name: $file_name}})\n"
                f"MATCH (__cy_curr__:{current.node_type} {{startLine: $curr_line, directory: $directory, file_name: $file_name}})\n"
            f"MERGE (__cy_prev__)-[__cy_r__:NEXT]->(__cy_curr__)\n"
            f"RETURN __cy_r__"
            ),
            "params": {
                "prev_line": prev.start_line,
                "curr_line": current.start_line,
                "directory": self.full_directory,
                "file_name": self.file_name,
            }
        }

    async def _run_preprocessing(self) -> List[str]:
        """변수 선행 처리"""
        return await self._analyze_variable_nodes()

    async def _invoke_llm(self, batch: AnalysisBatch) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """LLM 호출 (일반 분석 + DML 분석)"""
        general_task = None
        if batch.ranges:
            code, context = batch.build_payload()
            general_task = asyncio.to_thread(
                analyze_code,
                code,
                context,
                batch.ranges,
                len(batch.ranges),
                self.api_key,
                self.locale,
            )

        table_task = None
        dml_payload = batch.build_dml_payload()
        if dml_payload and batch.dml_ranges:
            code, context = dml_payload
            table_task = asyncio.to_thread(
                analyze_dml_tables,
                code,
                context,
                batch.dml_ranges,
                self.api_key,
                self.locale,
            )

        if general_task and table_task:
            results = await asyncio.gather(general_task, table_task)
            return tuple(results)
        elif general_task:
            return await general_task, None
        elif table_task:
            return None, await table_task
        else:
            raise RuntimeError("LLM 분석 대상이 없습니다")

    def _build_analysis_queries(
        self, 
        batch: AnalysisBatch, 
        llm_result: Any,
        unit_summary_store: Optional[Dict[str, Dict[str, str]]],
    ) -> List[Union[str, Dict[str, Any]]]:
        """LLM 분석 결과를 쿼리로 변환"""
        queries: List[Union[str, Dict[str, Any]]] = []
        
        # --- 결과 검증 ---
        if not isinstance(llm_result, tuple):
            raise RuntimeError(f"배치#{batch.batch_id} llm_result가 tuple이 아님: {type(llm_result).__name__}")
        
        general_result, table_result = llm_result
        
        # --- Summary 처리 ---
        general_result = self.validate_dict_result(
            general_result, "general_result", batch.batch_id, allow_none=True
        )
        if general_result:
            analysis_list = general_result.get("analysis") or []
            for node, analysis in zip(batch.nodes, analysis_list):
                if not analysis:
                    continue
                
                summary = analysis.get("summary") or ""
                if summary:
                    node_name = build_statement_name(node.node_type, node.start_line)
                    queries.append({
                        "query": (
                            f"MATCH (__cy_n__:{node.node_type} {{startLine: $start_line, directory: $directory, file_name: $file_name}})\n"
                            f"SET __cy_n__.endLine = $end_line, __cy_n__.name = $node_name, "
                            f"__cy_n__.summary = $summary, __cy_n__.node_code = $node_code, "
                            f"__cy_n__.token = $token, __cy_n__.procedure_name = $procedure_name, "
                            f"__cy_n__.has_children = $has_children\n"
                            f"RETURN __cy_n__"
                        ),
                        "params": {
                            "start_line": node.start_line,
                            "end_line": node.end_line,
                            "directory": self.full_directory,
                            "file_name": self.file_name,
                            "node_name": node_name,
                            "summary": str(summary),
                            "node_code": node.code,
                            "token": node.token,
                            "procedure_name": node.unit_name or "",
                            "has_children": node.has_children,
                        }
                    })
                    
                    if unit_summary_store is not None and node.unit_key and node.unit_key in unit_summary_store:
                        key = f"{node.node_type}_{node.start_line}_{node.end_line}"
                        unit_summary_store[node.unit_key][key] = summary
                
                # --- CALL 관계 생성 ---
                for call_name in analysis.get('calls', []) or []:
                    if '.' in call_name:
                        package_raw, proc_raw = call_name.split('.', 1)
                        queries.append({
                            "query": (
                                f"MATCH (__cy_c__:{node.node_type} {{startLine: $start_line, directory: $directory, file_name: $file_name}})\n"
                                f"MERGE (__cy_target__:PROCEDURE {{directory: $package_name, procedure_name: $proc_name}})\n"
                                f"MERGE (__cy_c__)-[__cy_r__:CALL {{scope: 'external'}}]->(__cy_target__)\n"
                                f"RETURN __cy_c__, __cy_target__, __cy_r__"
                            ),
                            "params": {
                                "start_line": node.start_line,
                                "directory": self.full_directory,
                                "file_name": self.file_name,
                                "package_name": package_raw.strip(),
                                "proc_name": proc_raw.strip(),
                            }
                        })
                    else:
                        queries.append({
                            "query": (
                                f"MATCH (__cy_c__:{node.node_type} {{startLine: $start_line, directory: $directory, file_name: $file_name}})\n"
                                f"MATCH (__cy_p__ {{procedure_name: $call_name, directory: $directory, file_name: $file_name}})\n"
                                f"WHERE __cy_p__:PROCEDURE OR __cy_p__:FUNCTION\n"
                                f"MERGE (__cy_c__)-[__cy_r__:CALL {{scope: 'internal'}}]->(__cy_p__)\n"
                                f"RETURN __cy_c__, __cy_p__, __cy_r__"
                            ),
                            "params": {
                                "start_line": node.start_line,
                                "directory": self.full_directory,
                                "file_name": self.file_name,
                                "call_name": call_name,
                            }
                        })
                
                # --- 변수 처리 ---
                var_queries = self._process_variable_usage(node, analysis.get('variables', []) or [])
                queries.extend(var_queries)
        
        # --- 테이블 처리 ---
        table_result = self.validate_dict_result(
            table_result, "table_result", batch.batch_id, allow_none=True
        )
        if table_result:
            table_queries = self._build_table_queries(batch, table_result)
            queries.extend(table_queries)
        
        return queries


    async def _summarize_unit_variables(self, unit_key: str) -> List[Union[str, Dict[str, Any]]]:
        """변수 역할 요약하여 Neo4j 쿼리 생성 (역할 2개 이상은 LLM 요약, 1개는 그대로 저장)"""
        unit_vars = self._variable_roles.get(unit_key, {})
        if not unit_vars:
            return []
        
        vars_to_summarize = [
            {"name": var_name, "roles": roles}
            for var_name, roles in unit_vars.items()
            if len(roles) >= 2
        ]
        single_role_vars = [
            (var_name, roles[0])
            for var_name, roles in unit_vars.items()
            if len(roles) == 1
        ]
        
        queries: List[Union[str, Dict[str, Any]]] = []
        
        if vars_to_summarize:
            try:
                result = await asyncio.to_thread(
                    analyze_variable_summary, vars_to_summarize, self.api_key, self.locale
                )
                validated = self.validate_dict_result(result, "변수 역할 요약", allow_none=True)
                
                if validated:
                    queries.extend([
                        {
                            "query": (
                                f"MATCH (__cy_v__:Variable {{name: $var_name, directory: $directory, file_name: $file_name}})\n"
                                f"SET __cy_v__.role = $role\n"
                                f"RETURN __cy_v__"
                            ),
                            "params": {
                                "var_name": var_sum.get("name", ""),
                                "directory": self.full_directory,
                                "file_name": self.file_name,
                                "role": var_sum.get("summary", ""),
                            }
                        }
                        for var_sum in validated.get("summaries", []) or []
                        if var_sum.get("name") and var_sum.get("summary")
                    ])
                
                log_process("ANALYZE", "VARIABLE", f"✅ 변수 역할 요약 완료: {len(vars_to_summarize)}개 변수")
            except Exception as e:
                log_process("ANALYZE", "VARIABLE", f"⚠️ 변수 역할 요약 실패: {e}")
        
        queries.extend([
            {
                "query": (
                    f"MATCH (__cy_v__:Variable {{name: $var_name, directory: $directory, file_name: $file_name}})\n"
                    f"SET __cy_v__.role = $role\n"
                    f"RETURN __cy_v__"
                ),
                "params": {
                    "var_name": var_name,
                    "directory": self.full_directory,
                    "file_name": self.file_name,
                    "role": role,
                }
            }
            for var_name, role in single_role_vars
        ])
        
        return queries
    
    # =========================================================================
    # Phase 2.2: User Story 쿼리 생성 (제거됨)
    # =========================================================================
    
    # NOTE: _build_user_story_queries는 user_story.py로 이동되었습니다.
    
    def _build_user_story_queries(self, procedure_type: str, procedure_name: str, user_stories: List[Dict[str, Any]]) -> List[Union[str, Dict[str, Any]]]:
        """User Story 및 Acceptance Criteria 쿼리 생성"""
        queries: List[Union[str, Dict[str, Any]]] = []
        
        for us_idx, us in enumerate(user_stories, 1):
            us_id = us.get('id', f"US-{us_idx}")
            queries.append({
                "query": (
                    f"MATCH (__cy_p__:{procedure_type} {{procedure_name: $procedure_name, directory: $directory, file_name: $file_name}})\n"
                    f"MERGE (__cy_us__:UserStory {{id: $us_id, procedure_name: $procedure_name, directory: $directory, file_name: $file_name}})\n"
                    f"SET __cy_us__.role = $role, __cy_us__.goal = $goal, __cy_us__.benefit = $benefit\n"
                    f"MERGE (__cy_p__)-[__cy_r__:HAS_USER_STORY]->(__cy_us__)\n"
                    f"RETURN __cy_p__, __cy_us__, __cy_r__"
                ),
                "params": {
                    "procedure_name": procedure_name,
                    "directory": self.full_directory,
                    "file_name": self.file_name,
                    "us_id": us_id,
                    "role": us.get('role', ''),
                    "goal": us.get('goal', ''),
                    "benefit": us.get('benefit', ''),
                }
            })
            
            for ac_idx, ac in enumerate(us.get('acceptance_criteria', []), 1):
                if not isinstance(ac, dict):
                    continue
                ac_id = ac.get('id', f"AC-{us_idx}-{ac_idx}")
                queries.append({
                    "query": (
                        f"MATCH (__cy_us__:UserStory {{id: $us_id, directory: $directory, file_name: $file_name}})\n"
                        f"MERGE (__cy_ac__:AcceptanceCriteria {{id: $ac_id, user_story_id: $us_id, directory: $directory, file_name: $file_name}})\n"
                        f"SET __cy_ac__.title = $title, __cy_ac__.given = $given, __cy_ac__.when_clause = $when_clause, __cy_ac__.then_clause = $then_clause\n"
                        f"MERGE (__cy_us__)-[__cy_r__:HAS_AC]->(__cy_ac__)\n"
                        f"RETURN __cy_us__, __cy_ac__, __cy_r__"
                    ),
                    "params": {
                        "us_id": us_id,
                        "ac_id": ac_id,
                        "directory": self.full_directory,
                        "file_name": self.file_name,
                        "title": ac.get('title', ''),
                        "given": json.dumps(ac.get('given', []), ensure_ascii=False),
                        "when_clause": json.dumps(ac.get('when', []), ensure_ascii=False),
                        "then_clause": json.dumps(ac.get('then', []), ensure_ascii=False),
                    }
                })
        
        return queries

    # =========================================================================
    # DBMS 전용 메서드
    # =========================================================================

    async def _analyze_variable_nodes(self) -> List[Union[str, Dict[str, Any]]]:
        """변수 선언 노드 분석 및 쿼리 생성"""
        variable_nodes = [n for n in (self._nodes or []) if n.node_type in VARIABLE_DECLARATION_TYPES]
        if not variable_nodes:
            return []
        
        semaphore = asyncio.Semaphore(VARIABLE_CONCURRENCY)
        
        async def analyze_one(node: StatementNode) -> List[Union[str, Dict[str, Any]]]:
            async with semaphore:
                result = await asyncio.to_thread(
                    analyze_variables, node.code, self.api_key, self.locale
                )
                return self._build_variable_queries(node, result)
        
        results = await asyncio.gather(*[analyze_one(n) for n in variable_nodes])
        queries: List[Union[str, Dict[str, Any]]] = []
        for r in results:
            queries.extend(r)
        
        return queries

    def _process_variable_usage(self, node: StatementNode, variables: List[Any]) -> List[Union[str, Dict[str, Any]]]:
        """변수 사용 마킹 및 역할 누적 처리"""
        queries: List[Union[str, Dict[str, Any]]] = []
        
        for var_info in variables:
            if isinstance(var_info, dict):
                var_name = var_info.get("name", "")
                var_role = var_info.get("role", "")
            else:
                var_name = str(var_info)
                var_role = ""
            
            if not var_name:
                continue
            
            usage_key = f"{node.start_line}_{node.end_line}"
            queries.append({
                "query": (
                    f"MATCH (__cy_v__:Variable {{name: $var_name, directory: $directory, file_name: $file_name}})\n"
                    f"SET __cy_v__.`{usage_key}` = 'Used'\n"
                    f"RETURN __cy_v__"
                ),
                "params": {
                    "var_name": var_name,
                    "directory": self.full_directory,
                    "file_name": self.file_name,
                }
            })
            
            if var_role and node.unit_key:
                if node.unit_key not in self._variable_roles:
                    self._variable_roles[node.unit_key] = {}
                if var_name not in self._variable_roles[node.unit_key]:
                    self._variable_roles[node.unit_key][var_name] = []
                if var_role not in self._variable_roles[node.unit_key][var_name]:
                    self._variable_roles[node.unit_key][var_name].append(var_role)
        
        return queries
    
    def _build_variable_queries(self, node: StatementNode, result: Dict[str, Any]) -> List[Union[str, Dict[str, Any]]]:
        """변수 분석 결과를 쿼리로 변환"""
        if not isinstance(result, dict):
            raise RuntimeError(f"변수 분석 결과가 dict가 아님 (node={node.start_line}): {type(result).__name__}")
        
        variables = result.get("variables") or []
        if not variables:
            return []
        
        queries: List[Union[str, Dict[str, Any]]] = []
        
        for var in variables:
            var_name = var.get("name", "")
            var_type = var.get("type", "")
            var_role = var.get("role", "")
            var_desc = var.get("description", "")
            
            if not var_name:
                continue
            
            queries.append({
                "query": (
                    f"MATCH (__cy_n__:{node.node_type} {{startLine: $start_line, directory: $directory, file_name: $file_name}})\n"
                    f"MERGE (__cy_v__:Variable {{name: $var_name, directory: $directory, file_name: $file_name}})\n"
                    f"SET __cy_v__.type = $var_type, __cy_v__.role = $var_role, __cy_v__.description = $var_desc\n"
                f"MERGE (__cy_n__)-[:DECLARES]->(__cy_v__)\n"
                f"RETURN __cy_v__"
                ),
                "params": {
                    "start_line": node.start_line,
                    "directory": self.full_directory,
                    "file_name": self.file_name,
                    "var_name": var_name,
                    "var_type": var_type,
                    "var_role": VARIABLE_ROLE_MAP.get(var_role, var_role),
                    "var_desc": var_desc,
                }
            })
        
        return queries

    def _build_table_queries(
        self,
        batch: AnalysisBatch,
        table_result: Dict[str, Any]
    ) -> List[Union[str, Dict[str, Any]]]:
        """DML 테이블 분석 결과를 쿼리로 변환"""
        queries: List[Union[str, Dict[str, Any]]] = []
        node_map: Dict[Tuple[int, int], StatementNode] = {
            (node.start_line, node.end_line): node for node in batch.nodes
        }
        ranges = table_result.get('ranges', []) or []
        
        for range_entry in ranges:
            try:
                start_line = int(range_entry.get('startLine'))
                end_line = int(range_entry.get('endLine'))
            except (TypeError, ValueError) as e:
                raise RuntimeError(f"LLM 테이블 분석 결과에 잘못된 라인 번호: startLine={range_entry.get('startLine')}, endLine={range_entry.get('endLine')}") from e
            
            node = node_map.get((start_line, end_line))
            if not node:
                continue
            
            node_match_params = {
                "start_line": node.start_line,
                "directory": self.full_directory,
                "file_name": self.file_name,
            }
            
            if node.node_type == 'CREATE_TEMP_TABLE':
                queries.extend(self._process_create_temp_table(node, range_entry.get('tables', []), node_match_params))
                continue
            
            queries.extend(self._process_dml_tables(node, range_entry.get('tables', []), node_match_params))
            queries.extend(self._process_dblinks(node, range_entry.get('dbLinks', []), node_match_params))
            queries.extend(self._process_fk_relations(range_entry.get('fkRelations', [])))
        
        return queries
    
    def _process_create_temp_table(
        self,
        node: StatementNode,
        tables: List[Dict[str, Any]],
        node_match_params: Dict[str, Any]
    ) -> List[Union[str, Dict[str, Any]]]:
        """CREATE_TEMP_TABLE 처리"""
        queries: List[Union[str, Dict[str, Any]]] = []
        
        for entry in tables:
            table_name = (entry.get('table') or '').strip()
            if not table_name:
                continue
            
            schema_part_raw, name_part_raw, _ = parse_table_identifier(table_name)
            schema_part = self._apply_name_case(schema_part_raw or self.default_schema)
            name_part = self._apply_name_case(name_part_raw)
            
            queries.append({
                "query": (
                    f"MATCH (__cy_n__:{node.node_type} {{startLine: $start_line, directory: $directory, file_name: $file_name}})\n"
                    f"SET __cy_n__:Table, __cy_n__.name = $name, __cy_n__.schema = $schema, __cy_n__.db = $db\n"
                    f"RETURN __cy_n__"
                ),
                "params": {
                    **node_match_params,
                    "name": name_part,
                    "schema": schema_part,
                    "db": self.dbms,
                }
            })
        
        return queries
    
    def _process_dml_tables(
        self,
        node: StatementNode,
        tables: List[Dict[str, Any]],
        node_match_params: Dict[str, Any]
    ) -> List[Union[str, Dict[str, Any]]]:
        """일반 DML 테이블 처리"""
        queries: List[Union[str, Dict[str, Any]]] = []
        table_merge_cache: Dict[Tuple[str, str, bool], Dict[str, Any]] = {}
        
        for entry in tables:
            table_name = (entry.get('table') or '').strip()
            if not table_name:
                continue
            
            # --- 테이블 정규화 ---
            schema_part_raw, name_part_raw, db_link_value = parse_table_identifier(table_name)
            schema_part, name_part, skip_case_conversion = self._resolve_table_name_with_ddl(schema_part_raw, name_part_raw)
            
            cache_key = (name_part, schema_part, skip_case_conversion)
            if cache_key not in table_merge_cache:
                table_merge_cache[cache_key] = self._build_table_merge(
                    name_part, schema_part, preserve_vars=['__cy_n__'], skip_case_conversion=skip_case_conversion
                )
            table_merge_result = table_merge_cache[cache_key]
            
            # --- 관계 생성 ---
            access_mode = (entry.get('accessMode') or entry.get('mode') or 'r').lower()
            rel_types = [TABLE_RELATIONSHIP_MAP.get(mode, mode.upper()) for mode in ['r', 'w'] if mode in access_mode]
            
            table_desc_raw = entry.get('tableDescription') or entry.get('description') or ''
            bucket_key = self._record_table_summary(schema_part, name_part, table_desc_raw, skip_case_conversion=skip_case_conversion)
            
            rel_merge_parts = [f"MERGE (__cy_n__)-[__cy_r{i}__:{rel_type}]->(__cy_t__)" for i, rel_type in enumerate(rel_types)]
            db_link_set = f"\nSET __cy_t__.db_link = COALESCE(__cy_t__.db_link, $db_link)" if db_link_value else ""
            rel_merge_str = "\n".join(rel_merge_parts)
            
            table_query = (
                f"MATCH (__cy_n__:{node.node_type} {{startLine: $start_line, directory: $directory, file_name: $file_name}})\n"
                f"WITH __cy_n__\n"
                f"{table_merge_result['query']}\n"
                f"SET __cy_t__.db = coalesce(__cy_t__.db, $db)"
                f"{db_link_set}"
                f"\n{rel_merge_str}" if rel_merge_str else ""
                f"\nRETURN __cy_n__, __cy_t__"
            )
            
            params = {**node_match_params, **table_merge_result["params"]}
            if db_link_value:
                params["db_link"] = db_link_value
            
            queries.append({"query": table_query, "params": params})
            
            # --- 컬럼 처리 ---
            column_queries = self._process_table_columns(
                entry.get('columns', []),
                name_part,
                schema_part,
                bucket_key,
                skip_case_conversion
            )
            queries.extend(column_queries)
        
        return queries
    
    def _process_table_columns(
        self,
        columns: List[Dict[str, Any]],
        table_name: str,
        schema: str,
        bucket_key: Tuple[str, str],
        skip_case_conversion: bool
    ) -> List[Union[str, Dict[str, Any]]]:
        """컬럼 처리"""
        queries: List[Union[str, Dict[str, Any]]] = []
        
        table_merge_result = self._build_table_merge(table_name, schema, preserve_vars=None, skip_case_conversion=skip_case_conversion)
        ddl_meta, _ = self._get_ddl_metadata(schema, table_name)
        ddl_columns = ddl_meta.get('columns', {}) if ddl_meta else {}
        
        for column in columns:
            column_name_raw = (column.get('name') or '').strip()
            if not column_name_raw:
                continue
            
            column_name = self._resolve_column_name_with_ddl(column_name_raw, ddl_columns)
            
            raw_dtype = column.get('dtype') or ''
            raw_column_desc = (column.get('description') or column.get('comment') or '').strip()
            
            self._record_column_summary(
                bucket_key,
                column_name,
                raw_column_desc,
                dtype=raw_dtype,
                nullable=column.get('nullable', True),
                examples=column.get('examples') or [],
            )
            
            converted_name_part = table_merge_result["converted_table_name"]
            converted_schema_part = table_merge_result["effective_schema"]
            fqn = '.'.join(filter(None, [converted_schema_part, converted_name_part, column_name])).lower()
            
            col_query = (
                f"{table_merge_result['query']}\nWITH __cy_t__\n"
                f"MERGE (__cy_c__:Column {{fqn: $fqn}})\n"
                f"SET __cy_c__.name = $col_name, __cy_c__.dtype = $col_type, "
                f"__cy_c__.description = $col_desc, __cy_c__.description_source = 'procedure', __cy_c__.nullable = $nullable\n"
                f"MERGE (__cy_t__)-[__cy_r__:HAS_COLUMN]->(__cy_c__)\n"
                f"RETURN __cy_t__, __cy_c__, __cy_r__"
            )
            
            queries.append({
                "query": col_query,
                "params": {
                    **table_merge_result["params"],
                    "fqn": fqn,
                    "col_name": column_name,
                    "col_type": raw_dtype,
                    "col_desc": raw_column_desc,
                    "nullable": column.get('nullable', True),
                }
            })
        
        return queries
    
    def _process_dblinks(
        self,
        node: StatementNode,
        db_links: List[Dict[str, Any]],
        node_match_params: Dict[str, Any]
    ) -> List[Union[str, Dict[str, Any]]]:
        """DBLink 처리"""
        queries: List[Union[str, Dict[str, Any]]] = []
        
        for link_item in db_links:
            link_name_raw = (link_item.get('name') or '').strip()
            if not link_name_raw:
                continue
            
            mode = (link_item.get('mode') or 'r').lower()
            schema_link_raw, name_link_raw, link_name = parse_table_identifier(link_name_raw)
            schema_link = self._apply_name_case(schema_link_raw or self.default_schema)
            name_link = self._apply_name_case(name_link_raw)
            remote_merge_result = self._build_table_merge(name_link, schema_link)
            
            dblink_query = (
                f"{remote_merge_result['query']}\n"
                f"SET __cy_t__.db_link = $link_name\n"
                f"WITH __cy_t__\n"
                f"MERGE (__cy_l__:DBLink {{name: $link_name}})\n"
                f"MERGE (__cy_l__)-[__cy_r1__:CONTAINS]->(__cy_t__)\n"
                f"WITH __cy_t__, __cy_l__\n"
                f"MATCH (__cy_n__:{node.node_type} {{startLine: $start_line, directory: $directory, file_name: $file_name}})\n"
                f"MERGE (__cy_n__)-[__cy_r2__:DB_LINK {{mode: $mode}}]->(__cy_t__)\n"
                f"RETURN __cy_l__, __cy_t__, __cy_n__"
            )
            
            queries.append({
                "query": dblink_query,
                "params": {
                    **remote_merge_result["params"],
                    **node_match_params,
                    "link_name": link_name,
                    "mode": mode,
                }
            })
        
        return queries
    
    def _process_fk_relations(
        self,
        fk_relations: List[Dict[str, Any]]
    ) -> List[Union[str, Dict[str, Any]]]:
        """FK 관계 처리 (FK_TO_TABLE과 FK_TO를 한 번의 루프에서 생성)"""
        queries: List[Union[str, Dict[str, Any]]] = []
        
        for relation in fk_relations:
            src_table = (relation.get('sourceTable') or '').strip()
            tgt_table = (relation.get('targetTable') or '').strip()
            src_columns = [c.strip() for c in (relation.get('sourceColumns') or []) if c]
            tgt_columns = [c.strip() for c in (relation.get('targetColumns') or []) if c]
            
            if not (src_table and tgt_table and src_columns and tgt_columns):
                continue
            
            src_schema_raw, src_name_raw, _ = parse_table_identifier(src_table)
            tgt_schema_raw, tgt_name_raw, _ = parse_table_identifier(tgt_table)
            
            src_schema, src_name, _ = self._resolve_table_name_with_ddl(src_schema_raw, src_name_raw)
            tgt_schema, tgt_name, _ = self._resolve_table_name_with_ddl(tgt_schema_raw, tgt_name_raw)
            
            effective_src_schema = src_schema or self._apply_name_case(self.default_schema)
            effective_tgt_schema = tgt_schema or self._apply_name_case(self.default_schema)
            
            for src_col, tgt_col in zip(src_columns, tgt_columns):
                converted_src_col = self._apply_name_case(src_col)
                converted_tgt_col = self._apply_name_case(tgt_col)
                
                queries.append({
                    "query": (
                        f"MATCH (__cy_st__:Table {{schema: $src_schema, name: $src_name, db: $db}})\n"
                        f"MATCH (__cy_tt__:Table {{schema: $tgt_schema, name: $tgt_name, db: $db}})\n"
                        f"MERGE (__cy_st__)-[__cy_r__:FK_TO_TABLE {{sourceColumn: $src_col, targetColumn: $tgt_col}}]->(__cy_tt__)\n"
                        f"ON CREATE SET __cy_r__.type = 'many_to_one', __cy_r__.source = 'procedure'\n"
                        f"RETURN __cy_st__, __cy_tt__, __cy_r__"
                    ),
                    "params": {
                        "src_schema": effective_src_schema,
                        "src_name": src_name,
                        "tgt_schema": effective_tgt_schema,
                        "tgt_name": tgt_name,
                        "db": self.dbms,
                        "src_col": converted_src_col,
                        "tgt_col": converted_tgt_col,
                    }
                })
                
                src_fqn = '.'.join(filter(None, [effective_src_schema, src_name, converted_src_col])).lower()
                tgt_fqn = '.'.join(filter(None, [effective_tgt_schema, tgt_name, converted_tgt_col])).lower()
                
                queries.append({
                    "query": (
                        f"MATCH (__cy_sc__:Column {{fqn: $src_fqn}})\n"
                        f"MATCH (__cy_dc__:Column {{fqn: $tgt_fqn}})\n"
                        f"MERGE (__cy_sc__)-[__cy_r__:FK_TO]->(__cy_dc__)\n"
                        f"ON CREATE SET __cy_r__.source = 'procedure'\n"
                        f"RETURN __cy_sc__, __cy_dc__, __cy_r__"
                    ),
                    "params": {
                        "src_fqn": src_fqn,
                        "tgt_fqn": tgt_fqn,
                    }
                })
        
        return queries
    
    def _build_table_merge(self, table_name: str, schema: Optional[str], preserve_vars: Optional[List[str]] = None, skip_case_conversion: bool = False) -> Dict[str, Any]:
        """테이블 MERGE 쿼리 생성 (Schema 노드 및 BELONGS_TO 관계 포함)"""
        effective_schema = self._get_effective_schema(schema)
        
        if not skip_case_conversion:
            effective_schema = self._apply_name_case(effective_schema)
            converted_table_name = self._apply_name_case(table_name)
        else:
            converted_table_name = table_name
        
        if preserve_vars:
            with_vars = ", ".join(preserve_vars + ["__cy_s__"])
        else:
            with_vars = "__cy_s__"
        
        return {
            "query": (
                f"MERGE (__cy_s__:Schema {{db: $db, name: $schema_name}})\n"
            f"WITH {with_vars}\n"
                f"MERGE (__cy_t__:Table {{name: $table_name, schema: $schema_name, db: $db}})\n"
            f"MERGE (__cy_t__)-[:BELONGS_TO]->(__cy_s__)"
            ),
            "params": {
                "db": self.dbms,
                "schema_name": effective_schema,
                "table_name": converted_table_name,
            },
            "effective_schema": effective_schema,
            "converted_table_name": converted_table_name,
        }

    def _record_table_summary(self, schema: Optional[str], name: str, description: Optional[str], skip_case_conversion: bool = False) -> Tuple[str, str]:
        """테이블 설명 누적"""
        effective_schema = self._get_effective_schema(schema)
        
        if skip_case_conversion:
            schema_key = effective_schema
            name_key = name
        else:
            schema_key = self._apply_name_case(effective_schema)
            name_key = self._apply_name_case(name)
        
        bucket = self._table_summary_store.setdefault((schema_key, name_key), {"summaries": set(), "columns": {}})
        text = (description or '').strip()
        if text:
            bucket["summaries"].add(text)
        return (schema_key, name_key)
    
    def _record_column_summary(
        self,
        table_key: Tuple[str, str],
        column_name: str,
        description: Optional[str],
        dtype: Optional[str] = None,
        nullable: Optional[bool] = None,
        examples: Optional[List[str]] = None,
    ):
        """컬럼 설명 누적"""
        text = (description or '').strip()
        bucket = self._table_summary_store.setdefault(table_key, {"summaries": set(), "columns": {}})
        columns = bucket["columns"]
        canonical = self._apply_name_case(column_name)
        entry = columns.get(canonical)
        if entry is None:
            entry = {"name": canonical, "summaries": set(), "dtype": (dtype or ''), "nullable": True if nullable is None else bool(nullable), "examples": set()}
            columns[canonical] = entry
        if dtype is not None and not entry.get("dtype"):
            entry["dtype"] = dtype
        if nullable is not None:
            entry["nullable"] = bool(nullable)
        if text:
            entry["summaries"].add(text)
        if examples:
            for v in examples:
                if v is not None:
                    s = str(v).strip()
                    if s:
                        entry["examples"].add(s)
    
    # =========================================================================
    # Phase 2.4: 테이블/컬럼 설명 요약 (제거됨)
    # =========================================================================
    
    # NOTE: _finalize_table_summaries와 _summarize_table은 table_summary.py로 이동되었습니다.
    
    async def _finalize_table_summaries(self) -> List[str]:
        """테이블/컬럼 설명 요약"""
        log_process("ANALYZE", "TABLE_SUMMARY", f"📊 테이블 요약 시작: {len(self._table_summary_store)}개 테이블")
        if not self._table_summary_store:
            log_process("ANALYZE", "TABLE_SUMMARY", "⚠️ 테이블 요약 대상 없음 (store 비어있음)")
            return []
        
        tasks = [
            self._summarize_table(table_key, data)
            for table_key, data in list(self._table_summary_store.items())
        ]
        if not tasks:
            return []
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        all_queries: List[Union[str, Dict[str, Any]]] = []
        for result in results:
            if isinstance(result, Exception):
                log_process("ANALYZE", "TABLE_SUMMARY", f"❌ 테이블 요약 오류: {result}", logging.ERROR)
            elif result:
                all_queries.extend(result)
        
        self._table_summary_store.clear()
        return all_queries
    
    async def _summarize_table(self, table_key: Tuple[str, str], data: Dict[str, Any]) -> List[str]:
        """테이블 요약 처리"""
        schema_key, name_key = table_key
        
        # --- 메타데이터 수집 ---
        ddl_key = (schema_key.lower(), name_key.lower())
        ddl_meta = self._ddl_table_metadata.get(ddl_key, {})
        ddl_description = (ddl_meta.get('description') or '').strip()
        ddl_columns = ddl_meta.get('columns') or {}
        
        summaries = list(data.get('summaries') or [])
        columns_map = data.get('columns') or {}
        column_sentences = {
            entry['name']: list(entry['summaries'])
            for entry in columns_map.values()
            if entry.get('summaries')
        }
        
        if ddl_description:
            summaries.insert(0, f"[DDL 메타데이터] {ddl_description}")
        
        for col_name, ddl_col in ddl_columns.items():
            ddl_col_desc = (ddl_col.get('description') or '').strip()
            if ddl_col_desc and col_name not in column_sentences:
                column_sentences[col_name] = [f"[DDL 메타데이터] {ddl_col_desc}"]
            elif ddl_col_desc and col_name in column_sentences:
                column_sentences[col_name].insert(0, f"[DDL 메타데이터] {ddl_col_desc}")
        
        if not summaries and not column_sentences:
            return []
        
        table_display = f"{schema_key}.{name_key}" if schema_key else name_key
        column_metadata = {}
        for entry in columns_map.values():
            col_name = entry['name']
            ddl_col = ddl_columns.get(col_name, {})
            column_metadata[col_name] = {
                "dtype": entry.get("dtype") or ddl_col.get("dtype") or "",
                "nullable": bool(entry.get("nullable", True)) if entry.get("nullable") is not None else ddl_col.get("nullable", True),
                "examples": sorted(list(entry.get("examples") or []))[:5],
            }
        
        for col_name, ddl_col in ddl_columns.items():
            if col_name not in column_metadata:
                column_metadata[col_name] = {
                    "dtype": ddl_col.get("dtype") or "",
                    "nullable": ddl_col.get("nullable", True),
                    "examples": [],
                }
        
        # --- LLM 호출 ---
        result = await asyncio.to_thread(
            summarize_table_metadata,
            table_display,
            summaries,
            column_sentences,
            column_metadata,
            self.api_key,
            self.locale,
        )
        
        if not isinstance(result, dict):
            raise RuntimeError(f"테이블 요약 결과가 dict가 아님 ({schema_key}.{name_key}): {type(result).__name__}")
        
        # --- 쿼리 생성 ---
        queries: List[Union[str, Dict[str, Any]]] = []
        llm_table_desc = (result.get('tableDescription') or '').strip()
        
        if llm_table_desc:
            queries.append({
                "query": (
                    f"MATCH (__cy_t__:Table {{schema: $schema, name: $name, db: $db}})\n"
                    f"SET __cy_t__.analyzed_description = $llm_desc\n"
                    f"WITH __cy_t__\n"
                    f"WHERE __cy_t__.description IS NULL OR __cy_t__.description = ''\n"
                    f"SET __cy_t__.description = $llm_desc, __cy_t__.description_source = 'procedure'\n"
                    f"RETURN __cy_t__"
                ),
                "params": {
                    "schema": schema_key,
                    "name": name_key,
                    "db": self.dbms,
                    "llm_desc": llm_table_desc,
                }
            })
        
        for column_info in result.get('columns', []) or []:
            column_name = (column_info.get('name') or '').strip()
            llm_column_desc = (column_info.get('description') or '').strip()
            if not column_name or not llm_column_desc:
                continue
            
            fqn = '.'.join(filter(None, [schema_key, name_key, column_name])).lower()
            queries.append({
                "query": (
                    f"MATCH (__cy_c__:Column {{fqn: $fqn}})\n"
                    f"SET __cy_c__.analyzed_description = $llm_desc\n"
                    f"WITH __cy_c__\n"
                    f"WHERE __cy_c__.description IS NULL OR __cy_c__.description = ''\n"
                    f"SET __cy_c__.description = $llm_desc, __cy_c__.description_source = 'procedure'\n"
                    f"RETURN __cy_c__"
                ),
                "params": {
                    "fqn": fqn,
                    "llm_desc": llm_column_desc,
                }
            })
        
        return queries
