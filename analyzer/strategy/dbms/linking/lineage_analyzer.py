"""데이터 리니지 분석기

ETL 코드에서 데이터 흐름(Source → Target)을 추출하여 Neo4j에 저장합니다.

주요 기능:
- INSERT/MERGE 문에서 타겟 테이블 추출
- SELECT/FROM/JOIN 절에서 소스 테이블 추출
- 데이터 흐름 관계(DATA_FLOW) 생성
- 기존 Table 노드와 연결하여 리니지 시각화

관계 타입:
- ETL_READS: ETL 프로시저가 소스 테이블에서 데이터를 읽음
- ETL_WRITES: ETL 프로시저가 타겟 테이블에 데이터를 씀
- DATA_FLOWS_TO: 소스 테이블에서 타겟 테이블로 데이터가 흐름
"""

import re
import logging
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, field

from client.neo4j_client import Neo4jClient
from util.text_utils import log_process

logger = logging.getLogger(__name__)


@dataclass
class LineageInfo:
    """데이터 리니지 정보"""
    etl_name: str  # ETL 프로시저/함수명
    source_tables: list[str] = field(default_factory=list)
    target_tables: list[str] = field(default_factory=list)
    operation_type: str = "ETL"  # ETL, INSERT, MERGE, UPDATE, DELETE
    description: str = ""
    file_name: str = ""
    is_etl: bool = False  # ETL 패턴으로 감지됨


class LineageAnalyzer:
    """ETL 코드에서 데이터 리니지를 분석하는 클래스"""

    # SQL 키워드 패턴
    _PROC_PATTERN = re.compile(
        r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:PROCEDURE|FUNCTION)\s+(\w+)",
        re.IGNORECASE
    )

    _INSERT_PATTERN = re.compile(
        r"INSERT\s+INTO\s+(\w+(?:\.\w+)?)",
        re.IGNORECASE
    )

    _MERGE_PATTERN = re.compile(
        r"MERGE\s+INTO\s+(\w+(?:\.\w+)?)",
        re.IGNORECASE
    )

    _UPDATE_PATTERN = re.compile(
        r"UPDATE\s+(\w+(?:\.\w+)?)\s+SET",
        re.IGNORECASE
    )

    _DELETE_PATTERN = re.compile(
        r"DELETE\s+FROM\s+(\w+(?:\.\w+)?)",
        re.IGNORECASE
    )

    _FROM_PATTERN = re.compile(
        r"FROM\s+(\w+(?:\.\w+)?)",
        re.IGNORECASE
    )

    _JOIN_PATTERN = re.compile(
        r"(?:LEFT\s+|RIGHT\s+|INNER\s+|OUTER\s+|CROSS\s+)?JOIN\s+(\w+(?:\.\w+)?)",
        re.IGNORECASE
    )

    _USING_PATTERN = re.compile(
        r"USING\s*\(\s*SELECT.*?FROM\s+(\w+(?:\.\w+)?)",
        re.IGNORECASE | re.DOTALL
    )

    # 제외할 시스템 테이블/함수
    _EXCLUDED_TABLES = {
        "dual", "sysdate", "systimestamp", "user", "rownum",
        "all_tables", "user_tables", "dba_tables",
        "information_schema", "pg_catalog",
    }

    def __init__(
        self,
        dbms: str = "oracle",
    ):
        self.dbms = dbms.lower()

    def analyze_sql_content(self, sql_content: str, file_name: str = "") -> list[LineageInfo]:
        """SQL 내용을 분석하여 리니지 정보를 추출합니다.

        Args:
            sql_content: SQL 소스 코드 문자열
            file_name: 파일명 (로깅용)

        Returns:
            LineageInfo 리스트
        """
        lineage_list: list[LineageInfo] = []

        # 프로시저/함수 단위로 분석
        procedures = self._split_procedures(sql_content)

        for proc_name, proc_body in procedures:
            lineage = self._analyze_procedure(proc_name, proc_body)
            if lineage.source_tables or lineage.target_tables:
                lineage_list.append(lineage)
                log_process(
                    "LINEAGE", "ANALYZE",
                    f"{proc_name}: {len(lineage.source_tables)} sources → {len(lineage.target_tables)} targets"
                )

        # 프로시저가 없으면 파일 전체를 하나의 단위로 분석
        if not procedures:
            lineage = self._analyze_procedure(file_name or "UNKNOWN", sql_content)
            if lineage.source_tables or lineage.target_tables:
                lineage_list.append(lineage)

        return lineage_list

    def _split_procedures(self, sql_content: str) -> list[tuple[str, str]]:
        """SQL 내용을 프로시저/함수 단위로 분할합니다."""
        result = []

        # CREATE PROCEDURE/FUNCTION 찾기
        pattern = re.compile(
            r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:PROCEDURE|FUNCTION)\s+(\w+)\s*"
            r"(?:\([^)]*\))?\s*(?:AS|IS)?\s*"
            r"(.*?)(?=CREATE\s+(?:OR\s+REPLACE\s+)?(?:PROCEDURE|FUNCTION)|$)",
            re.IGNORECASE | re.DOTALL
        )

        for match in pattern.finditer(sql_content):
            proc_name = match.group(1)
            proc_body = match.group(2)
            result.append((proc_name, proc_body))

        return result

    def _analyze_procedure(self, proc_name: str, proc_body: str) -> LineageInfo:
        """단일 프로시저의 리니지를 분석합니다."""
        lineage = LineageInfo(etl_name=proc_name)

        # 타겟 테이블 추출 (INSERT, MERGE, UPDATE, DELETE)
        targets = set()

        for match in self._INSERT_PATTERN.finditer(proc_body):
            table = self._normalize_table_name(match.group(1))
            if table and table.lower() not in self._EXCLUDED_TABLES:
                targets.add(table)
                lineage.operation_type = "INSERT"

        for match in self._MERGE_PATTERN.finditer(proc_body):
            table = self._normalize_table_name(match.group(1))
            if table and table.lower() not in self._EXCLUDED_TABLES:
                targets.add(table)
                lineage.operation_type = "MERGE"

        for match in self._UPDATE_PATTERN.finditer(proc_body):
            table = self._normalize_table_name(match.group(1))
            if table and table.lower() not in self._EXCLUDED_TABLES:
                targets.add(table)
                if not lineage.operation_type or lineage.operation_type == "ETL":
                    lineage.operation_type = "UPDATE"

        for match in self._DELETE_PATTERN.finditer(proc_body):
            table = self._normalize_table_name(match.group(1))
            if table and table.lower() not in self._EXCLUDED_TABLES:
                targets.add(table)
                if not lineage.operation_type or lineage.operation_type == "ETL":
                    lineage.operation_type = "DELETE"

        # 소스 테이블 추출 (FROM, JOIN, USING)
        sources = set()

        for match in self._FROM_PATTERN.finditer(proc_body):
            table = self._normalize_table_name(match.group(1))
            if table and table.lower() not in self._EXCLUDED_TABLES:
                # 타겟 테이블은 소스에서 제외 (자기 자신 참조 제외)
                if table.upper() not in {t.upper() for t in targets}:
                    sources.add(table)

        for match in self._JOIN_PATTERN.finditer(proc_body):
            table = self._normalize_table_name(match.group(1))
            if table and table.lower() not in self._EXCLUDED_TABLES:
                if table.upper() not in {t.upper() for t in targets}:
                    sources.add(table)

        for match in self._USING_PATTERN.finditer(proc_body):
            table = self._normalize_table_name(match.group(1))
            if table and table.lower() not in self._EXCLUDED_TABLES:
                if table.upper() not in {t.upper() for t in targets}:
                    sources.add(table)

        lineage.source_tables = sorted(sources)
        lineage.target_tables = sorted(targets)

        # ETL 패턴 감지: 소스 테이블에서 데이터를 읽어 타겟 테이블에 쓰는 패턴
        # - INSERT INTO ... SELECT FROM ...
        # - MERGE INTO ... USING ...
        # - UPDATE ... SET ... (FROM 절이 있는 경우)
        if lineage.source_tables and lineage.target_tables:
            lineage.is_etl = True
            lineage.operation_type = "ETL"
            logger.debug(
                f"ETL 패턴 감지: {proc_name} | "
                f"sources={lineage.source_tables} → targets={lineage.target_tables}"
            )
        elif len(targets) > 1:
            # 여러 테이블에 쓰는 경우도 ETL로 간주
            lineage.is_etl = True
            lineage.operation_type = "ETL"

        return lineage

    def _normalize_table_name(self, table: str) -> Optional[str]:
        """테이블명을 정규화합니다."""
        if not table:
            return None

        # 스키마.테이블 형식 유지
        parts = table.strip().split(".")
        normalized = ".".join(p.strip().upper() for p in parts if p.strip())

        return normalized if normalized else None

    async def save_lineage_to_neo4j(
        self,
        client: Neo4jClient,
        lineage_list: list[LineageInfo],
        file_name: str = "",
        name_case: str = "original",  # uppercase, lowercase, original
    ) -> dict:
        """리니지 정보를 Neo4j에 저장합니다.

        기존 PROCEDURE/FUNCTION 노드와 Table 노드를 연결합니다.
        별도의 DataSource 노드 대신 기존 노드를 활용합니다.

        Args:
            client: Neo4j 클라이언트
            lineage_list: LineageInfo 리스트
            file_name: 원본 파일명
            name_case: 이름 대소문자 처리 (uppercase, lowercase, original)

        Returns:
            저장 결과 (노드/관계 수)
        """
        queries = []
        stats = {
            "etl_nodes": 0,
            "etl_reads": 0,
            "etl_writes": 0,
            "data_flows": 0,
            "matched_tables": 0,
        }

        for lineage in lineage_list:
            if not lineage.is_etl:
                continue  # ETL 패턴이 아니면 스킵

            proc_name = self._apply_name_case(lineage.etl_name, name_case)

            # 기존 PROCEDURE/FUNCTION 노드에 ETL 플래그 설정
            queries.append({
                "query": """
                    MATCH (__cy_proc__)
                    WHERE (__cy_proc__:PROCEDURE OR __cy_proc__:FUNCTION)
                      AND toLower(__cy_proc__.procedure_name) = toLower($proc_name)
                    SET __cy_proc__.is_etl = true,
                        __cy_proc__.etl_operation = $operation_type,
                        __cy_proc__.etl_source_count = $source_count,
                        __cy_proc__.etl_target_count = $target_count
                    RETURN __cy_proc__
                """,
                "parameters": {
                    "proc_name": proc_name,
                    "operation_type": lineage.operation_type,
                    "source_count": len(lineage.source_tables),
                    "target_count": len(lineage.target_tables),
                }
            })
            stats["etl_nodes"] += 1

            # 소스 테이블 → ETL 관계 (ETL_READS)
            for source in lineage.source_tables:
                source_table = self._parse_table_name(source, name_case)

                # 테이블명만으로 매칭 (스키마는 선택적)
                queries.append({
                    "query": """
                        MATCH (__cy_proc__)
                        WHERE (__cy_proc__:PROCEDURE OR __cy_proc__:FUNCTION)
                          AND toLower(__cy_proc__.procedure_name) = toLower($proc_name)
                        MATCH (__cy_t__:Table)
                        WHERE toLower(__cy_t__.name) = toLower($table_name)
                        MERGE (__cy_proc__)-[__cy_r__:ETL_READS]->(__cy_t__)
                        SET __cy_r__.operation = $operation_type,
                            __cy_r__.file_name = $file_name
                        RETURN __cy_proc__, __cy_r__, __cy_t__
                    """,
                    "parameters": {
                        "proc_name": proc_name,
                        "table_name": source_table["name"],
                        "operation_type": lineage.operation_type,
                        "file_name": file_name,
                    }
                })
                stats["etl_reads"] += 1

            # ETL → 타겟 테이블 관계 (ETL_WRITES)
            for target in lineage.target_tables:
                target_table = self._parse_table_name(target, name_case)

                queries.append({
                    "query": """
                        MATCH (__cy_proc__)
                        WHERE (__cy_proc__:PROCEDURE OR __cy_proc__:FUNCTION)
                          AND toLower(__cy_proc__.procedure_name) = toLower($proc_name)
                        MATCH (__cy_t__:Table)
                        WHERE toLower(__cy_t__.name) = toLower($table_name)
                        MERGE (__cy_proc__)-[__cy_r__:ETL_WRITES]->(__cy_t__)
                        SET __cy_r__.operation = $operation_type,
                            __cy_r__.file_name = $file_name
                        RETURN __cy_proc__, __cy_r__, __cy_t__
                    """,
                    "parameters": {
                        "proc_name": proc_name,
                        "table_name": target_table["name"],
                        "operation_type": lineage.operation_type,
                        "file_name": file_name,
                    }
                })
                stats["etl_writes"] += 1

            # 소스 테이블 → 타겟 테이블 직접 연결 (DATA_FLOWS_TO)
            for source in lineage.source_tables:
                for target in lineage.target_tables:
                    source_table = self._parse_table_name(source, name_case)
                    target_table = self._parse_table_name(target, name_case)

                    queries.append({
                        "query": """
                            MATCH (__cy_src__:Table)
                            WHERE toLower(__cy_src__.name) = toLower($src_name)
                            MATCH (__cy_tgt__:Table)
                            WHERE toLower(__cy_tgt__.name) = toLower($tgt_name)
                            MERGE (__cy_src__)-[__cy_r__:DATA_FLOWS_TO]->(__cy_tgt__)
                            SET __cy_r__.via_etl = $etl_name,
                                __cy_r__.operation = $operation_type,
                                __cy_r__.file_name = $file_name
                            RETURN __cy_src__, __cy_r__, __cy_tgt__
                        """,
                        "parameters": {
                            "src_name": source_table["name"],
                            "tgt_name": target_table["name"],
                            "etl_name": lineage.etl_name,
                            "operation_type": lineage.operation_type,
                            "file_name": file_name,
                        }
                    })
                    stats["data_flows"] += 1

        if queries:
            await client.execute_queries(queries)
            log_process(
                "LINEAGE", "SAVE",
                f"Neo4j 저장 완료: ETL {stats['etl_nodes']}개, "
                f"ETL_READS {stats['etl_reads']}개, "
                f"ETL_WRITES {stats['etl_writes']}개, "
                f"DATA_FLOWS_TO {stats['data_flows']}개"
            )

        return stats

    def _apply_name_case(self, name: str, case: str) -> str:
        """이름 대소문자 변환"""
        if case == "uppercase":
            return name.upper()
        elif case == "lowercase":
            return name.lower()
        return name

    def _parse_table_name(self, table_ref: str, name_case: str) -> dict:
        """테이블 참조를 스키마와 테이블명으로 분리"""
        parts = table_ref.strip().split(".")
        if len(parts) >= 2:
            schema = self._apply_name_case(parts[-2], name_case)
            name = self._apply_name_case(parts[-1], name_case)
        else:
            schema = ""
            name = self._apply_name_case(parts[0], name_case)
        return {"schema": schema, "name": name}


async def analyze_lineage_from_sql(
    sql_content: str,
    file_name: str = "",
    dbms: str = "oracle",
) -> tuple[list[LineageInfo], dict]:
    """SQL 내용에서 리니지를 분석하고 Neo4j에 저장합니다.

    Args:
        sql_content: SQL 소스 코드
        file_name: 파일명
        dbms: DBMS 타입

    Returns:
        (LineageInfo 리스트, 저장 통계)
    """
    analyzer = LineageAnalyzer(
        dbms=dbms,
    )

    lineage_list = analyzer.analyze_sql_content(sql_content, file_name)

    if lineage_list:
        client = Neo4jClient()
        try:
            stats = await analyzer.save_lineage_to_neo4j(client, lineage_list, file_name)
        finally:
            await client.close()
    else:
        stats = {"etl_nodes": 0, "data_sources": 0, "data_flows": 0}

    return lineage_list, stats


