"""AST 노드 수집기 - _ast_core.py에서 추출

AST를 후위순회하여 StatementNode와 프로시저 정보를 수집합니다.
"""

import re
import logging
from typing import Any, Dict, List, Optional, Tuple

from util.logger import log_process
from util.text_utils import calculate_code_token, resolve_node_code
from analyzer.strategy.dbms.common.constants import (
    PROCEDURE_TYPES, NON_ANALYSIS_TYPES, DML_STATEMENT_TYPES,
)
from analyzer.strategy.dbms.common.models import StatementNode, ProcedureInfo
from analyzer.strategy.dbms.common.utils import get_procedure_name_from_code


class StatementCollector:
    """AST를 후위순회하여 StatementNode와 프로시저 정보를 수집"""

    def __init__(self, antlr_data: Dict[str, Any], directory: str, file_name: str, source_content: str = ""):
        self.antlr_data = antlr_data
        self.directory = directory
        self.file_name = file_name
        self.source_content = source_content or ""
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
        raw_code = resolve_node_code(node, self.source_content)
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
                log_process("PHASE:1", "COLLECT", f"📋 프로시저 선언 발견: {proc_name} (라인 {start_line}~{end_line})")

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

        self.nodes.append(statement_node)
        log_process("PHASE:1", "COLLECT", f"✅ {node_type} 노드 수집 완료: 라인 {start_line}~{end_line}, 토큰 {token}, 자식 {len(child_nodes)}개")
        return statement_node
