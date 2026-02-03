"""
MyBatis XML Parser (결정론적)

MyBatis Mapper XML 파싱.
<select>, <insert>, <update>, <delete>, <sql>, <include> 처리.

사용 예시:
    parser = MyBatisParser()
    result = parser.parse_mapper("mapper/Order.xml", content)
"""

from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
import re
import xml.etree.ElementTree as ET


@dataclass
class MyBatisStatement:
    """MyBatis Statement"""
    id: str
    type: str  # select | insert | update | delete | sql
    namespace: str
    sql: str
    tables_read: List[str] = field(default_factory=list)
    tables_write: List[str] = field(default_factory=list)
    includes: List[str] = field(default_factory=list)
    parameters: List[str] = field(default_factory=list)
    result_type: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "namespace": self.namespace,
            "sql": self.sql,
            "tables_read": self.tables_read,
            "tables_write": self.tables_write,
            "includes": self.includes,
            "parameters": self.parameters,
            "result_type": self.result_type,
        }


@dataclass
class MyBatisMapperResult:
    """MyBatis Mapper 파싱 결과"""
    file_path: str
    namespace: str
    statements: List[MyBatisStatement]
    sql_fragments: Dict[str, str]  # id -> sql
    errors: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_path": self.file_path,
            "namespace": self.namespace,
            "statements": [s.to_dict() for s in self.statements],
            "sql_fragments": self.sql_fragments,
            "errors": self.errors,
        }


class MyBatisParser:
    """MyBatis XML 파서 (결정론적)"""
    
    # DML 타입별 statement 태그
    STATEMENT_TYPES = ['select', 'insert', 'update', 'delete']
    
    def parse_mapper(
        self, 
        file_path: str, 
        content: str
    ) -> MyBatisMapperResult:
        """
        MyBatis Mapper XML 파싱
        
        Args:
            file_path: 파일 경로
            content: XML 내용
        
        Returns:
            MyBatisMapperResult
        """
        statements = []
        sql_fragments = {}
        errors = []
        namespace = ""
        
        try:
            # XML 파싱
            root = ET.fromstring(content)
            
            # namespace 추출
            namespace = root.attrib.get('namespace', '')
            
            # <sql> fragments 추출
            for sql_elem in root.findall('.//sql'):
                sql_id = sql_elem.attrib.get('id', '')
                sql_content = self._get_element_text(sql_elem)
                if sql_id:
                    sql_fragments[sql_id] = sql_content
            
            # statements 추출
            for stmt_type in self.STATEMENT_TYPES:
                for elem in root.findall(f'.//{stmt_type}'):
                    stmt = self._parse_statement(elem, stmt_type, namespace, sql_fragments)
                    if stmt:
                        statements.append(stmt)
        
        except ET.ParseError as e:
            errors.append(f"XML parse error: {e}")
            # Regex fallback
            namespace, statements, sql_fragments = self._regex_fallback(content)
        
        return MyBatisMapperResult(
            file_path=file_path,
            namespace=namespace,
            statements=statements,
            sql_fragments=sql_fragments,
            errors=errors,
        )
    
    def _parse_statement(
        self, 
        elem: ET.Element, 
        stmt_type: str,
        namespace: str,
        sql_fragments: Dict[str, str]
    ) -> Optional[MyBatisStatement]:
        """Statement 요소 파싱"""
        stmt_id = elem.attrib.get('id', '')
        if not stmt_id:
            return None
        
        # SQL 내용 추출
        sql = self._get_element_text(elem)
        
        # include 확장
        includes = []
        for include in elem.findall('.//include'):
            refid = include.attrib.get('refid', '')
            if refid:
                includes.append(refid)
                # sql_fragment로 대체
                if refid in sql_fragments:
                    sql = sql.replace(f'<include refid="{refid}"/>', sql_fragments[refid])
        
        # 테이블 추출
        tables_read, tables_write = self._extract_tables(sql, stmt_type)
        
        # 파라미터 추출
        parameters = self._extract_parameters(sql)
        
        # resultType 추출
        result_type = elem.attrib.get('resultType') or elem.attrib.get('resultMap')
        
        return MyBatisStatement(
            id=stmt_id,
            type=stmt_type,
            namespace=namespace,
            sql=sql,
            tables_read=tables_read,
            tables_write=tables_write,
            includes=includes,
            parameters=parameters,
            result_type=result_type,
        )
    
    def _get_element_text(self, elem: ET.Element) -> str:
        """요소의 전체 텍스트 추출 (자식 포함)"""
        text_parts = []
        
        if elem.text:
            text_parts.append(elem.text)
        
        for child in elem:
            # 동적 태그는 내용만 추출
            child_text = self._get_element_text(child)
            text_parts.append(child_text)
            
            if child.tail:
                text_parts.append(child.tail)
        
        return ''.join(text_parts)
    
    def _extract_tables(
        self, 
        sql: str, 
        stmt_type: str
    ) -> tuple[List[str], List[str]]:
        """SQL에서 테이블 추출"""
        tables_read = []
        tables_write = []
        
        # FROM/JOIN 뒤의 테이블 (read)
        from_pattern = r'(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)'
        for match in re.finditer(from_pattern, sql, re.IGNORECASE):
            table = match.group(1).upper()
            if table not in ['SELECT', 'WHERE', 'AND', 'OR', 'ON']:
                tables_read.append(table)
        
        # INSERT/UPDATE/DELETE 대상 (write)
        if stmt_type == 'insert':
            insert_pattern = r'INSERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)'
            for match in re.finditer(insert_pattern, sql, re.IGNORECASE):
                tables_write.append(match.group(1).upper())
        
        elif stmt_type == 'update':
            update_pattern = r'UPDATE\s+([A-Za-z_][A-Za-z0-9_]*)'
            for match in re.finditer(update_pattern, sql, re.IGNORECASE):
                tables_write.append(match.group(1).upper())
        
        elif stmt_type == 'delete':
            delete_pattern = r'DELETE\s+FROM\s+([A-Za-z_][A-Za-z0-9_]*)'
            for match in re.finditer(delete_pattern, sql, re.IGNORECASE):
                tables_write.append(match.group(1).upper())
        
        return list(set(tables_read)), list(set(tables_write))
    
    def _extract_parameters(self, sql: str) -> List[str]:
        """MyBatis 파라미터 추출 (#{...}, ${...})"""
        params = set()
        
        # #{param}
        for match in re.finditer(r'#\{([^}]+)\}', sql):
            params.add(match.group(1))
        
        # ${param}
        for match in re.finditer(r'\$\{([^}]+)\}', sql):
            params.add(match.group(1))
        
        return list(params)
    
    def _regex_fallback(
        self, 
        content: str
    ) -> tuple[str, List[MyBatisStatement], Dict[str, str]]:
        """XML 파싱 실패 시 Regex fallback"""
        statements = []
        sql_fragments = {}
        
        # namespace
        ns_match = re.search(r'namespace\s*=\s*["\']([^"\']+)["\']', content)
        namespace = ns_match.group(1) if ns_match else ""
        
        # sql fragments
        for match in re.finditer(r'<sql\s+id\s*=\s*["\']([^"\']+)["\'][^>]*>([\s\S]*?)</sql>', content):
            sql_fragments[match.group(1)] = match.group(2).strip()
        
        # statements
        for stmt_type in self.STATEMENT_TYPES:
            pattern = rf'<{stmt_type}\s+[^>]*id\s*=\s*["\']([^"\']+)["\'][^>]*>([\s\S]*?)</{stmt_type}>'
            for match in re.finditer(pattern, content, re.IGNORECASE):
                stmt_id = match.group(1)
                sql = match.group(2).strip()
                
                # include 추출
                includes = re.findall(r'<include\s+refid\s*=\s*["\']([^"\']+)["\']', sql)
                
                tables_read, tables_write = self._extract_tables(sql, stmt_type)
                
                statements.append(MyBatisStatement(
                    id=stmt_id,
                    type=stmt_type,
                    namespace=namespace,
                    sql=sql,
                    tables_read=tables_read,
                    tables_write=tables_write,
                    includes=includes,
                    parameters=self._extract_parameters(sql),
                ))
        
        return namespace, statements, sql_fragments
    
    def expand_includes(
        self, 
        statement: MyBatisStatement, 
        sql_fragments: Dict[str, str],
        max_depth: int = 3
    ) -> str:
        """include 확장 (재귀적)"""
        sql = statement.sql
        
        for _ in range(max_depth):
            expanded = False
            for include in statement.includes:
                if include in sql_fragments:
                    pattern = rf'<include\s+refid\s*=\s*["\']({re.escape(include)})["\'][^>]*/>'
                    if re.search(pattern, sql):
                        sql = re.sub(pattern, sql_fragments[include], sql)
                        expanded = True
            
            if not expanded:
                break
        
        return sql

