"""메타데이터 보강 서비스

Text2SQL API를 통해 샘플 데이터를 조회하고,
LLM으로 테이블/컬럼 설명을 생성합니다.
FK 관계도 샘플 데이터 매칭으로 추론합니다.
"""

import asyncio
import json
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

import aiohttp
from openai import AsyncOpenAI
from rapidfuzz import fuzz

from client.neo4j_client import Neo4jClient
from config.settings import settings
from util.stream_event import emit_message, emit_phase_event
from util.text_utils import log_process

logger = logging.getLogger(__name__)


# =========================================================================
# 유사도 계산 유틸리티
# =========================================================================

def normalize_column_name(name: str) -> str:
    """컬럼명 정규화 (소문자 변환)"""
    if not name:
        return ""
    return name.lower()


def calculate_levenshtein_similarity(name1: str, name2: str) -> float:
    """Levenshtein 거리 기반 유사도 계산"""
    norm1 = name1.lower()
    norm2 = name2.lower()
    
    if not norm1 or not norm2:
        return 0.0
    
    return fuzz.ratio(norm1, norm2) / 100.0


def calculate_jaro_winkler_similarity(name1: str, name2: str) -> float:
    """Jaro-Winkler 유사도 계산"""
    norm1 = name1.lower()
    norm2 = name2.lower()
    
    if not norm1 or not norm2:
        return 0.0
    
    return fuzz.WRatio(norm1, norm2) / 100.0


def calculate_column_similarity(name1: str, name2: str) -> float:
    """컬럼명 종합 유사도 계산 (Levenshtein 50%, Jaro-Winkler 50%)"""
    levenshtein = calculate_levenshtein_similarity(name1, name2)
    jaro_winkler = calculate_jaro_winkler_similarity(name1, name2)
    
    return levenshtein * 0.5 + jaro_winkler * 0.5


def are_types_compatible(type1: str, type2: str) -> bool:
    """데이터 타입 호환성 확인"""
    if not type1 or not type2:
        return True  # 타입 정보가 없으면 통과
    
    type1_lower = type1.lower()
    type2_lower = type2.lower()
    
    if type1_lower == type2_lower:
        return True
    
    # 숫자 타입 그룹
    numeric_types = {"int", "integer", "bigint", "smallint", "numeric", "decimal", "float", "double", "real", "number"}
    if any(t in type1_lower for t in numeric_types) and any(t in type2_lower for t in numeric_types):
        return True
    
    # 문자열 타입 그룹
    string_types = {"varchar", "char", "text", "string", "nvarchar", "nchar", "varchar2"}
    if any(t in type1_lower for t in string_types) and any(t in type2_lower for t in string_types):
        return True
    
    # 날짜 타입 그룹
    date_types = {"date", "datetime", "timestamp", "time"}
    if any(t in type1_lower for t in date_types) and any(t in type2_lower for t in date_types):
        return True
    
    return False


# =========================================================================
# 메타데이터 보강 프로세서
# =========================================================================

class MetadataEnrichmentService:
    """메타데이터 보강 서비스
    
    description이 없는 테이블에 대해 샘플 데이터를 기반으로 설명을 생성합니다.
    """

    def __init__(
        self,
        client: Neo4jClient,
        openai_client: AsyncOpenAI,
        text2sql_base_url: str,
    ):
        """초기화
        
        Args:
            client: Neo4j 클라이언트
            openai_client: OpenAI 클라이언트
            text2sql_base_url: Text2SQL API 기본 URL
        """
        self.client = client
        self.openai_client = openai_client
        self.text2sql_base_url = text2sql_base_url.rstrip("/") if text2sql_base_url else ""
        
        # 설정
        self.sample_size = settings.metadata_enrichment.fk_sample_size
        self.similarity_threshold = settings.metadata_enrichment.fk_similarity_threshold
        self.match_ratio_threshold = settings.metadata_enrichment.fk_match_ratio_threshold

    async def check_text2sql_available(
        self,
        session: aiohttp.ClientSession,
    ) -> bool:
        """Text2SQL 서버 사용 가능 여부 확인"""
        if not self.text2sql_base_url:
            return False
            
        endpoints = [
            f"{self.text2sql_base_url}/text2sql/direct-sql",
            f"{self.text2sql_base_url}/api/v1/direct-sql",
        ]
        
        test_payload = {"sql": "SELECT 1 AS test LIMIT 1"}
        
        for url in endpoints:
            try:
                async with session.post(
                    url,
                    json=test_payload,
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status in [200, 400, 422]:
                        log_process("METADATA", "SERVER_OK", f"Text2SQL 서버 확인: {url}", logging.INFO)
                        return True
            except (aiohttp.ClientConnectorError, aiohttp.ServerTimeoutError, asyncio.TimeoutError) as e:
                log_process("METADATA", "SERVER_CHECK", f"엔드포인트 {url} 연결 실패: {e}", logging.DEBUG)
                continue
            except Exception as e:
                log_process("METADATA", "SERVER_CHECK", f"엔드포인트 {url} 확인 중 오류: {e}", logging.DEBUG)
                continue
        
        return False

    async def fetch_sample_data(
        self,
        session: aiohttp.ClientSession,
        sql: str,
    ) -> Optional[List[Dict[str, Any]]]:
        """Text2SQL Direct SQL API로 샘플 데이터 조회"""
        endpoints = [
            f"{self.text2sql_base_url}/text2sql/direct-sql",
            f"{self.text2sql_base_url}/api/v1/direct-sql",
        ]
        
        payload = {"sql": sql}
        
        for url in endpoints:
            try:
                async with session.post(
                    url, json=payload, timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        rows = data.get("rows", [])
                        columns = data.get("columns", [])
                        
                        if rows and columns:
                            return [dict(zip(columns, row)) for row in rows]
                        elif rows:
                            return rows
                        else:
                            sample_data = data.get("results", data.get("data", []))
                            if sample_data:
                                return sample_data
                    else:
                        body = await resp.text()
                        log_process("METADATA", "SAMPLE_FAIL", 
                            f"샘플 데이터 조회 실패: status={resp.status}, body={body[:200]}", 
                            logging.WARNING)
            except Exception as e:
                log_process("METADATA", "API_ERROR", f"엔드포인트 {url} 호출 실패: {e}", logging.WARNING)
                continue
        
        return None

    async def generate_descriptions_from_sample(
        self,
        table_name: str,
        schema_name: str,
        sample_data: List[Dict[str, Any]],
        columns_info: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """샘플 데이터를 기반으로 LLM이 테이블/컬럼 설명 생성"""
        # 샘플 데이터를 문자열로 변환 (최대 10행)
        sample_rows = sample_data[:10] if len(sample_data) > 10 else sample_data
        sample_str = "\n".join([str(row) for row in sample_rows])
        
        # 컬럼 정보 정리
        columns_str = "\n".join([
            f"- {col.get('column_name', col.get('name', ''))}: "
            f"{col.get('data_type', col.get('dtype', 'unknown'))}"
            for col in columns_info
        ])
        
        prompt = f"""다음은 테이블 "{schema_name}"."{table_name}"의 정보입니다.

## 컬럼 정보:
{columns_str}

## 샘플 데이터 (최대 10행):
{sample_str}

위 정보를 분석하여 다음을 JSON 형식으로 응답하세요:

1. "table_description": 테이블이 어떤 데이터를 저장하는지 한국어로 설명 (1-2문장)
2. "column_descriptions": 각 컬럼에 대한 설명을 담은 객체 (컬럼명: 설명)

예시:
{{
  "table_description": "고객의 주문 정보를 저장하는 테이블입니다.",
  "column_descriptions": {{
    "order_id": "주문 고유 식별자",
    "customer_name": "고객 이름",
    "order_date": "주문 일시"
  }}
}}

JSON만 응답하세요."""

        try:
            response = await self.openai_client.chat.completions.create(
                model=settings.llm.model or "gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=1000,
                response_format={"type": "json_object"},
            )
            
            result = json.loads(response.choices[0].message.content)
            log_process("METADATA", "LLM_OK", 
                f"설명 생성 완료: {table_name} (테이블: {bool(result.get('table_description'))}, "
                f"컬럼: {len(result.get('column_descriptions', {}))}개)", 
                logging.INFO)
            return result
            
        except Exception as e:
            log_process("METADATA", "LLM_ERROR", f"LLM 설명 생성 실패: {e}", logging.WARNING)
            return None

    async def update_descriptions_in_neo4j(
        self,
        table_name: str,
        schema_name: str,
        descriptions: Dict[str, Any],
    ) -> Tuple[int, int]:
        """Neo4j에 생성된 description 업데이트
        
        Returns:
            (테이블 업데이트 수, 컬럼 업데이트 수)
        """
        table_updated = 0
        columns_updated = 0
        
        # 테이블 description 업데이트
        table_desc = descriptions.get("table_description", "")
        if table_desc:
            update_table_query = """
            MATCH (t:Table {name: $table_name, schema: $schema_name})
            SET t.description = $description, 
                t.description_source = 'sample_data_inference'
            """
            await self.client.execute_with_params(
                update_table_query,
                {
                    "table_name": table_name,
                    "schema_name": schema_name,
                    "description": table_desc,
                },
            )
            table_updated = 1
        
        # 컬럼 description 업데이트
        column_descs = descriptions.get("column_descriptions", {})
        if column_descs:
            for col_name, col_desc in column_descs.items():
                update_col_query = """
                MATCH (t:Table {name: $table_name, schema: $schema_name})
                  -[:HAS_COLUMN]->(c:Column {name: $col_name})
                WHERE c.description IS NULL 
                   OR c.description = '' 
                   OR c.description = 'N/A'
                SET c.description = $description, 
                    c.description_source = 'sample_data_inference'
                """
                await self.client.execute_with_params(
                    update_col_query,
                    {
                        "table_name": table_name,
                        "schema_name": schema_name,
                        "col_name": col_name,
                        "description": col_desc,
                    },
                )
                columns_updated += 1
        
        return table_updated, columns_updated

    async def find_fk_candidates(
        self,
        tables: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """FK 관계 후보 쌍 추출 (컬럼명 유사도 기반)"""
        candidates = []
        
        # 이미 존재하는 FK 관계 조회 (중복 방지)
        existing_fk_query = """
        MATCH (t1:Table)-[r:FK_TO_TABLE]->(t2:Table)
        RETURN t1.schema + '.' + t1.name AS from_table,
               t2.schema + '.' + t2.name AS to_table,
               r.sourceColumn AS from_column,
               r.targetColumn AS to_column
        """
        existing_fks_result = await self.client.execute_queries([existing_fk_query])
        existing_fks = existing_fks_result[0] if existing_fks_result else []
        existing_fk_set = {
            (
                fk.get("from_table", ""),
                fk.get("to_table", ""),
                fk.get("from_column", ""),
                fk.get("to_column", ""),
            )
            for fk in existing_fks
        }
        
        log_process("FK_INFERENCE", "EXISTING", f"기존 FK 관계 수: {len(existing_fk_set)}", logging.INFO)
        
        # 모든 테이블 쌍 생성
        for i, table1 in enumerate(tables):
            for j, table2 in enumerate(tables):
                if i >= j:  # 중복 방지
                    continue
                
                table1_name = table1.get("table_name", "")
                table1_schema = table1.get("schema_name", "")
                table1_columns = table1.get("columns", [])
                
                table2_name = table2.get("table_name", "")
                table2_schema = table2.get("schema_name", "")
                table2_columns = table2.get("columns", [])
                
                # 각 컬럼 쌍 비교
                for col1 in table1_columns:
                    for col2 in table2_columns:
                        col1_name = col1.get("column_name", "")
                        col1_type = col1.get("data_type", "")
                        
                        col2_name = col2.get("column_name", "")
                        col2_type = col2.get("data_type", "")
                        
                        # 컬럼명 유사도 계산
                        similarity = calculate_column_similarity(col1_name, col2_name)
                        
                        if similarity < self.similarity_threshold:
                            continue
                        
                        # 데이터 타입 호환성 확인
                        if not are_types_compatible(col1_type, col2_type):
                            continue
                        
                        # 이미 존재하는 FK 관계인지 확인
                        from_table_key = f"{table1_schema}.{table1_name}"
                        to_table_key = f"{table2_schema}.{table2_name}"
                        
                        if (from_table_key, to_table_key, col1_name, col2_name) in existing_fk_set:
                            continue
                        
                        candidates.append({
                            "from_table": table1_name,
                            "from_schema": table1_schema,
                            "from_column": col1_name,
                            "from_type": col1_type,
                            "to_table": table2_name,
                            "to_schema": table2_schema,
                            "to_column": col2_name,
                            "to_type": col2_type,
                            "similarity": similarity,
                        })
        
        # 유사도 순으로 정렬
        candidates.sort(key=lambda x: x["similarity"], reverse=True)
        
        log_process("FK_INFERENCE", "CANDIDATES", f"후보 쌍 발견: {len(candidates)}개", logging.INFO)
        return candidates

    async def verify_fk_relationship(
        self,
        session: aiohttp.ClientSession,
        candidate: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """FK 관계 검증 (샘플 데이터 매칭)"""
        from_table = candidate["from_table"]
        from_schema = candidate["from_schema"]
        from_column = candidate["from_column"]
        to_table = candidate["to_table"]
        to_schema = candidate["to_schema"]
        to_column = candidate["to_column"]
        
        full_from_table = f'"{from_schema}"."{from_table}"'
        full_to_table = f'"{to_schema}"."{to_table}"'
        
        try:
            # 1. 소스 테이블에서 샘플 데이터 조회
            sample_sql = f'SELECT DISTINCT "{from_column}" FROM {full_from_table} WHERE "{from_column}" IS NOT NULL LIMIT {self.sample_size}'
            
            sample_data = await self.fetch_sample_data(session, sample_sql)
            
            if not sample_data or len(sample_data) == 0:
                return None
            
            # 샘플 값 추출
            sample_values = [
                row.get(from_column) for row in sample_data if row.get(from_column) is not None
            ]
            
            if not sample_values:
                return None
            
            # 2. 타겟 테이블에서 해당 값들이 존재하는지 확인
            escaped_values = []
            for v in sample_values[:self.sample_size]:
                if v is None:
                    continue
                if isinstance(v, (int, float)):
                    escaped_values.append(str(v))
                else:
                    escaped = str(v).replace("'", "''")
                    escaped_values.append(f"'{escaped}'")
            
            if not escaped_values:
                return None
            
            values_str = ", ".join(escaped_values)
            check_sql = f'SELECT DISTINCT "{to_column}" FROM {full_to_table} WHERE "{to_column}" IN ({values_str})'
            
            matched_data = await self.fetch_sample_data(session, check_sql)
            
            if not matched_data:
                return None
            
            # 매칭된 값 추출
            matched_values = [
                row.get(to_column) for row in matched_data if row.get(to_column) is not None
            ]
            
            # 3. 매칭 비율 계산
            source_value_set = set(sample_values)
            matched_value_set = set(matched_values)
            matched_unique_count = len(source_value_set & matched_value_set)
            total_samples = len(sample_values)
            
            if total_samples == 0:
                return None
            
            match_ratio = matched_unique_count / total_samples
            
            log_process("FK_INFERENCE", "VERIFY", 
                f"매칭 검증: {full_from_table}.{from_column} → {full_to_table}.{to_column} "
                f"(매칭: {matched_unique_count}/{total_samples} = {match_ratio:.2%})", 
                logging.INFO)
            
            # 4. 매칭 비율이 임계값 이상이면 FK 확정
            if match_ratio >= self.match_ratio_threshold:
                return {
                    **candidate,
                    "match_ratio": match_ratio,
                    "matched_count": matched_unique_count,
                    "total_samples": total_samples,
                }
            
            return None
            
        except Exception as e:
            log_process("FK_INFERENCE", "VERIFY_ERROR", 
                f"FK 검증 실패: {full_from_table}.{from_column} → {full_to_table}.{to_column} - {e}", 
                logging.WARNING)
            return None

    async def save_fk_relationship(
        self,
        fk_info: Dict[str, Any],
    ) -> None:
        """Neo4j에 FK 관계 저장"""
        fk_query = """
        MATCH (t1:Table {name: $from_table, schema: $from_schema})
        MATCH (t2:Table {name: $to_table, schema: $to_schema})
        MERGE (t1)-[r:FK_TO_TABLE {
            sourceColumn: $from_column,
            targetColumn: $to_column
        }]->(t2)
        ON CREATE SET 
            r.type = 'many_to_one',
            r.source = 'sample_data_inference',
            r.similarity = $similarity,
            r.match_ratio = $match_ratio,
            r.matched_count = $matched_count,
            r.total_samples = $total_samples
        ON MATCH SET
            r.source = CASE 
                WHEN r.source = 'ddl' THEN 'ddl'
                ELSE 'sample_data_inference'
            END
        RETURN r
        """
        
        await self.client.execute_with_params(
            fk_query,
            {
                "from_table": fk_info["from_table"],
                "from_schema": fk_info["from_schema"],
                "from_column": fk_info["from_column"],
                "to_table": fk_info["to_table"],
                "to_schema": fk_info["to_schema"],
                "to_column": fk_info["to_column"],
                "similarity": fk_info.get("similarity", 0.0),
                "match_ratio": fk_info.get("match_ratio", 0.0),
                "matched_count": fk_info.get("matched_count", 0),
                "total_samples": fk_info.get("total_samples", 0),
            },
        )
        
        log_process("FK_INFERENCE", "SAVED", 
            f"FK 관계 저장: {fk_info['from_schema']}.{fk_info['from_table']}.{fk_info['from_column']} → "
            f"{fk_info['to_schema']}.{fk_info['to_table']}.{fk_info['to_column']}", 
            logging.INFO)

