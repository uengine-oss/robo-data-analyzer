"""메타데이터 보강 서비스

Text2SQL API를 통해 샘플 데이터를 조회하고,
LLM으로 테이블/컬럼 설명을 생성합니다.
FK 관계도 샘플 데이터 매칭으로 추론합니다.
"""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
from openai import AsyncOpenAI
from rapidfuzz import fuzz

from client.neo4j_client import Neo4jClient
from config.settings import settings
from util.text_utils import log_process

logger = logging.getLogger(__name__)

# 상수
DESCRIPTION_SOURCE = "sample_data_inference"
TEXT2SQL_ENDPOINT = "/text2sql/direct-sql"
SAMPLE_DATA_LIMIT = 10  # LLM 프롬프트용 샘플 데이터 최대 행 수
TIMEOUT_CONNECT = 5  # 연결 타임아웃 (초)
TIMEOUT_REQUEST = 30  # 요청 타임아웃 (초)

# 데이터 타입 그룹
NUMERIC_TYPES = {"int", "integer", "bigint", "smallint", "serial", "bigserial", "smallserial", "numeric", "decimal", "float", "double", "real", "number"}
STRING_TYPES = {"varchar", "char", "text", "string", "nvarchar", "nchar", "varchar2"}
DATE_TYPES = {"date", "datetime", "timestamp", "time"}


# =========================================================================
# 유사도 계산 유틸리티
# =========================================================================

def calculate_column_similarity(name1: str, name2: str) -> float:
    """컬럼명 종합 유사도 계산 (Levenshtein 50%, Jaro-Winkler 50%)"""
    if not name1 or not name2:
        return 0.0
    
    norm1 = name1.lower()
    norm2 = name2.lower()
    
    levenshtein = fuzz.ratio(norm1, norm2) / 100.0
    jaro_winkler = fuzz.WRatio(norm1, norm2) / 100.0
    
    return levenshtein * 0.5 + jaro_winkler * 0.5


def are_types_compatible(type1: str, type2: str) -> bool:
    """데이터 타입 호환성 확인"""
    if not type1 or not type2:
        return True  # 타입 정보가 없으면 통과
    
    type1_lower = type1.lower()
    type2_lower = type2.lower()
    
    if type1_lower == type2_lower:
        return True
    
    # 타입 그룹별 호환성 확인
    for type_group in [NUMERIC_TYPES, STRING_TYPES, DATE_TYPES]:
        if any(t in type1_lower for t in type_group) and any(t in type2_lower for t in type_group):
            return True
    
    return False


# =========================================================================
# 메타데이터 보강 서비스
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
        datasource_name: str = "",
    ):
        """초기화
        
        Args:
            client: Neo4j 클라이언트
            openai_client: OpenAI 클라이언트
            text2sql_base_url: Text2SQL API 기본 URL
            datasource_name: 데이터 소스 이름
        """
        self.client = client
        self.openai_client = openai_client
        self.text2sql_base_url = (text2sql_base_url or "").rstrip("/")
        self.datasource_name = datasource_name or ""
        
        # 설정값
        self.sample_size = settings.metadata_enrichment.fk_sample_size
        self.similarity_threshold = settings.metadata_enrichment.fk_similarity_threshold
        self.match_ratio_threshold = settings.metadata_enrichment.fk_match_ratio_threshold
        self.fk_concurrency = settings.metadata_enrichment.fk_concurrency

    # =======================================================================
    # Text2SQL API 관련
    # =======================================================================

    async def check_text2sql_available(self, session: aiohttp.ClientSession) -> bool:
        """Text2SQL 서버 사용 가능 여부 확인"""
        if not self.text2sql_base_url:
            return False
        
        url = f"{self.text2sql_base_url}{TEXT2SQL_ENDPOINT}"
        payload = {"sql": "SELECT 1 AS test LIMIT 1"}
        if self.datasource_name:
            payload["datasource"] = self.datasource_name
        
        try:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=TIMEOUT_CONNECT)) as resp:
                if resp.status in [200, 400, 422]:
                    log_process("METADATA", "SERVER_OK", f"Text2SQL 서버 확인: {url}", logging.INFO)
                    return True
        except (aiohttp.ClientConnectorError, aiohttp.ServerTimeoutError, asyncio.TimeoutError) as e:
            log_process("METADATA", "SERVER_CHECK", f"엔드포인트 {url} 연결 실패: {e}", logging.DEBUG)
        except Exception as e:
            log_process("METADATA", "SERVER_CHECK", f"엔드포인트 {url} 확인 중 오류: {e}", logging.DEBUG)
        
        return False

    async def fetch_sample_data(self, session: aiohttp.ClientSession, sql: str, max_retries: int = 3) -> Optional[List[Dict[str, Any]]]:
        """Text2SQL Direct SQL API로 샘플 데이터 조회 (QueuePool 오류 시 재시도)"""
        url = f"{self.text2sql_base_url}{TEXT2SQL_ENDPOINT}"
        payload = {"sql": sql}
        if self.datasource_name:
            payload["datasource"] = self.datasource_name
        
        for attempt in range(max_retries):
            try:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=TIMEOUT_REQUEST)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        # 서버가 200이지만 에러를 반환하는 경우 (execution_error 등)
                        if isinstance(data, dict) and "error" in data:
                            error_msg = str(data.get("error", ""))
                            # QueuePool 오류인 경우 재시도
                            if "QueuePool" in error_msg or "connection timed out" in error_msg:
                                if attempt < max_retries - 1:
                                    wait_time = (attempt + 1) * 2  # 2초, 4초, 6초...
                                    log_process(
                                        "METADATA", 
                                        "RETRY", 
                                        f"QueuePool 오류 발생, {wait_time}초 후 재시도 ({attempt + 1}/{max_retries}): {error_msg[:100]}", 
                                        logging.WARNING
                                    )
                                    await asyncio.sleep(wait_time)
                                    continue
                                else:
                                    log_process("METADATA", "RETRY_FAILED", f"재시도 {max_retries}회 모두 실패: {error_msg[:150]}", logging.ERROR)
                                    return None
                            else:
                                # 다른 종류의 오류는 재시도하지 않음
                                log_process("METADATA", "SQL_ERROR", f"SQL 실행 오류: {error_msg[:150]}", logging.DEBUG)
                                return None
                        
                        return self._parse_sample_data(data)
                    else:
                        body = await resp.text()
                        log_process("METADATA", "SAMPLE_FAIL", f"API 응답 오류: status={resp.status}, body={body[:150]}", logging.DEBUG)
            except asyncio.TimeoutError:
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 2
                    log_process("METADATA", "RETRY", f"타임아웃 발생, {wait_time}초 후 재시도 ({attempt + 1}/{max_retries})", logging.WARNING)
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    log_process("METADATA", "TIMEOUT", f"SQL 요청 타임아웃 ({TIMEOUT_REQUEST}초, {max_retries}회 재시도 실패)", logging.DEBUG)
            except Exception as e:
                error_msg = str(e)
                if ("QueuePool" in error_msg or "connection timed out" in error_msg) and attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 2
                    log_process("METADATA", "RETRY", f"연결 오류 발생, {wait_time}초 후 재시도 ({attempt + 1}/{max_retries}): {error_msg[:100]}", logging.WARNING)
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    log_process("METADATA", "API_ERROR", f"API 호출 실패: {error_msg[:150]}", logging.DEBUG)
        
        return None

    def _parse_sample_data(self, data: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
        """샘플 데이터 파싱"""
        rows = data.get("rows", [])
        columns = data.get("columns", [])
        
        if rows and columns:
            return [dict(zip(columns, row)) for row in rows]
        elif rows:
            return rows
        else:
            return data.get("results", data.get("data", [])) or None

    # =======================================================================
    # 설명 생성 및 업데이트
    # =======================================================================

    async def generate_descriptions_from_sample(
        self,
        table_name: str,
        schema_name: str,
        sample_data: List[Dict[str, Any]],
        columns_info: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """샘플 데이터를 기반으로 LLM이 테이블/컬럼 설명 생성"""
        prompt = self._build_description_prompt(table_name, schema_name, sample_data, columns_info)
        
        try:
            response = await self.openai_client.chat.completions.create(
                model=settings.llm.model or "gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_completion_tokens=1000,
                response_format={"type": "json_object"},
            )
            
            result = json.loads(response.choices[0].message.content)
            log_process(
                "METADATA",
                "LLM_OK",
                f"설명 생성 완료: {table_name} (테이블: {bool(result.get('table_description'))}, "
                f"컬럼: {len(result.get('column_descriptions', {}))}개)",
                logging.INFO,
            )
            return result
            
        except Exception as e:
            log_process("METADATA", "LLM_ERROR", f"LLM 설명 생성 실패: {e}", logging.WARNING)
            return None

    def _build_description_prompt(
        self, table_name: str, schema_name: str, sample_data: List[Dict[str, Any]], columns_info: List[Dict[str, Any]]
    ) -> str:
        """설명 생성 프롬프트 생성"""
        sample_rows = sample_data[:SAMPLE_DATA_LIMIT]
        sample_str = "\n".join([str(row) for row in sample_rows])
        
        columns_str = "\n".join([
            f"- {col.get('column_name', col.get('name', ''))}: {col.get('data_type', col.get('dtype', 'unknown'))}"
            for col in columns_info
        ])
        
        return f"""다음은 테이블 "{schema_name}"."{table_name}"의 정보입니다.

## 컬럼 정보:
{columns_str}

## 샘플 데이터 (최대 {SAMPLE_DATA_LIMIT}행):
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

    async def update_descriptions_in_neo4j(
        self, table_name: str, schema_name: str, descriptions: Dict[str, Any]
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
            await self._update_table_description(table_name, schema_name, table_desc)
            table_updated = 1
        
        # 컬럼 description 업데이트
        column_descs = descriptions.get("column_descriptions", {})
        if column_descs:
            columns_updated = await self._update_column_descriptions(table_name, schema_name, column_descs)
        
        return table_updated, columns_updated

    async def _update_table_description(self, table_name: str, schema_name: str, description: str) -> None:
        """테이블 description 업데이트 (description이 없는 경우만)"""
        query = """
        MATCH (t:Table {name: $table_name, schema: $schema_name})
        WHERE t.description IS NULL 
           OR t.description = '' 
           OR t.description = 'N/A'
        SET t.description = $description, 
            t.description_source = $source
        """
        await self.client.execute_with_params(
            query, {"table_name": table_name, "schema_name": schema_name, "description": description, "source": DESCRIPTION_SOURCE}
        )

    async def _update_column_descriptions(self, table_name: str, schema_name: str, column_descs: Dict[str, str]) -> int:
        """컬럼 description 업데이트"""
        query = """
        MATCH (t:Table {name: $table_name, schema: $schema_name})
          -[:HAS_COLUMN]->(c:Column {name: $col_name})
        WHERE c.description IS NULL 
           OR c.description = '' 
           OR c.description = 'N/A'
        SET c.description = $description, 
            c.description_source = $source
        """
        
        updated = 0
        for col_name, col_desc in column_descs.items():
            await self.client.execute_with_params(
                query,
                {
                    "table_name": table_name,
                    "schema_name": schema_name,
                    "col_name": col_name,
                    "description": col_desc,
                    "source": DESCRIPTION_SOURCE,
                },
            )
            updated += 1
        
        return updated

    # =======================================================================
    # FK 관계 추론
    # =======================================================================

    async def find_fk_candidates(
        self, source_tables: List[Dict[str, Any]], all_tables: List[Dict[str, Any]], session: Optional[aiohttp.ClientSession] = None
    ) -> List[Dict[str, Any]]:
        """FK 관계 후보 쌍 추출
        
        1. 컬럼명 유사도 기반 후보 추출
        2. 유사도 기반 후보가 없으면 PK 값 기반 후보 추출 (fallback)
        """
        existing_fk_set = await self._get_existing_fk_set()
        candidate_keys = set()  # 중복 체크용
        candidate_info_map = {}  # 기본키 정보 저장용: {(from_table, to_table, from_col, to_col): {to_is_pk, from_is_pk}}
        candidates = []
        
        # 상세 통계 수집용
        source_table_stats: Dict[str, Dict[str, int]] = {}  # {table_key: {total_candidates, exact_matches, ...}}
        column_pair_stats: Dict[str, int] = {}  # {source_column_key: candidate_count}
        target_table_stats: Dict[str, int] = {}  # {target_table_key: candidate_count}
        similarity_distribution: Dict[str, int] = {}  # {similarity_range: count}
        
        # 1. 유사도 기반 후보 추출
        similarity_candidates = []
        exact_matches = 0  # 완전 일치 개수
        high_similarity = 0  # 0.95 이상 유사도 개수
        
        log_process(
            "FK_INFERENCE",
            "CANDIDATE_START",
            f"후보 선정 시작: 소스 {len(source_tables)}개 × 타겟 {len(all_tables)}개 = 최대 {len(source_tables) * len(all_tables)}개 테이블 쌍",
            logging.INFO,
        )
        
        for source_table in source_tables:
            source_key = f"{source_table.get('schema_name', '')}.{source_table.get('table_name', '')}"
            source_table_stats[source_key] = {
                "total_candidates": 0,
                "exact_matches": 0,
                "high_similarity": 0,
                "columns_checked": len(source_table.get("columns", [])),
            }
            
            for target_table in all_tables:
                if self._is_same_table(source_table, target_table):
                    continue
                
                target_key = f"{target_table.get('schema_name', '')}.{target_table.get('table_name', '')}"
                
                for candidate in self._compare_table_columns(source_table, target_table, existing_fk_set, candidate_keys, candidate_info_map):
                    similarity_candidates.append(candidate)
                    
                    # 통계 수집
                    similarity = candidate.get("similarity", 0)
                    if similarity == 1.0:
                        exact_matches += 1
                        source_table_stats[source_key]["exact_matches"] += 1
                    elif similarity >= 0.95:
                        high_similarity += 1
                        source_table_stats[source_key]["high_similarity"] += 1
                    
                    source_table_stats[source_key]["total_candidates"] += 1
                    target_table_stats[target_key] = target_table_stats.get(target_key, 0) + 1
                    
                    # 소스 컬럼별 통계
                    source_col_key = f"{source_key}.{candidate.get('from_column', '')}"
                    column_pair_stats[source_col_key] = column_pair_stats.get(source_col_key, 0) + 1
                    
                    # 유사도 분포
                    if similarity == 1.0:
                        similarity_distribution["1.0 (완전일치)"] = similarity_distribution.get("1.0 (완전일치)", 0) + 1
                    elif similarity >= 0.99:
                        similarity_distribution["0.99-0.999"] = similarity_distribution.get("0.99-0.999", 0) + 1
                    elif similarity >= 0.98:
                        similarity_distribution["0.98-0.989"] = similarity_distribution.get("0.98-0.989", 0) + 1
                    elif similarity >= 0.95:
                        similarity_distribution["0.95-0.979"] = similarity_distribution.get("0.95-0.979", 0) + 1
        
        candidates.extend(similarity_candidates)
        
        # 상세 통계 로그 출력
        if similarity_candidates:
            log_process(
                "FK_INFERENCE",
                "CANDIDATE_STATS",
                f"후보 선정 통계: 완전 일치 {exact_matches}개, 고유사도(≥0.95) {high_similarity}개, 총 {len(similarity_candidates)}개",
                logging.INFO,
            )
            
            # 소스 테이블별 상위 10개 출력
            top_sources = sorted(source_table_stats.items(), key=lambda x: x[1]["total_candidates"], reverse=True)[:10]
            if top_sources:
                log_process(
                    "FK_INFERENCE",
                    "CANDIDATE_SOURCE_TOP",
                    f"소스 테이블별 후보 수 (상위 10개): " + 
                    ", ".join([f"{k}: {v['total_candidates']}개" for k, v in top_sources]),
                    logging.INFO,
                )
            
            # 소스 컬럼별 상위 10개 출력
            top_columns = sorted(column_pair_stats.items(), key=lambda x: x[1], reverse=True)[:10]
            if top_columns:
                log_process(
                    "FK_INFERENCE",
                    "CANDIDATE_COLUMN_TOP",
                    f"소스 컬럼별 후보 수 (상위 10개): " + 
                    ", ".join([f"{k}: {v}개" for k, v in top_columns]),
                    logging.INFO,
                )
            
            # 타겟 테이블별 상위 10개 출력
            top_targets = sorted(target_table_stats.items(), key=lambda x: x[1], reverse=True)[:10]
            if top_targets:
                log_process(
                    "FK_INFERENCE",
                    "CANDIDATE_TARGET_TOP",
                    f"타겟 테이블별 후보 수 (상위 10개): " + 
                    ", ".join([f"{k}: {v}개" for k, v in top_targets]),
                    logging.INFO,
                )
            
            # 유사도 분포 출력
            if similarity_distribution:
                log_process(
                    "FK_INFERENCE",
                    "CANDIDATE_SIMILARITY_DIST",
                    f"유사도 분포: " + ", ".join([f"{k}: {v}개" for k, v in sorted(similarity_distribution.items(), reverse=True)]),
                    logging.INFO,
                )
        
        # 유사도 기반 후보가 없을 때 로그 출력
        if not similarity_candidates:
            log_process(
                "FK_INFERENCE",
                "NO_SIMILARITY_CANDIDATES",
                f"유사도 기반 후보 없음 (소스 테이블: {len(source_tables)}개, 타겟 테이블: {len(all_tables)}개)",
                logging.INFO,
            )
        
        # 2. 유사도 기반 후보가 없으면 PK 값 기반 후보 추출 (fallback)
        pk_based_count = 0
        if not similarity_candidates and session:
            log_process("FK_INFERENCE", "FALLBACK", "유사도 기반 후보 없음, PK 값 기반 후보 탐색 시작", logging.INFO)
            pk_based_candidates = await self._find_fk_candidates_by_pk_values(
                session, source_tables, all_tables, existing_fk_set, candidate_keys, candidate_info_map
            )
            pk_based_count = len(pk_based_candidates)
            candidates.extend(pk_based_candidates)
            
            if not pk_based_candidates:
                log_process(
                    "FK_INFERENCE",
                    "NO_PK_CANDIDATES",
                    "PK 값 기반 후보도 없음",
                    logging.INFO,
                )
        
        # 우선순위 점수 순으로 정렬
        candidates.sort(key=lambda x: x["priority_score"], reverse=True)
        similarity_count = len(similarity_candidates)
        log_process(
            "FK_INFERENCE",
            "CANDIDATES",
            f"후보 쌍 발견: {len(candidates)}개 (유사도: {similarity_count}개, PK기반: {pk_based_count}개)",
            logging.INFO,
        )
        
        # 후보 수가 너무 많으면 경고
        if len(candidates) > 5000:
            log_process(
                "FK_INFERENCE",
                "CANDIDATE_WARNING",
                f"⚠️ 후보 수가 매우 많습니다 ({len(candidates)}개). 검증에 시간이 오래 걸릴 수 있습니다.",
                logging.WARNING,
            )
        
        return candidates

    async def _get_existing_fk_set(self) -> set:
        """기존 FK 관계 집합 조회"""
        query = """
        MATCH (t1:Table)-[r:FK_TO_TABLE]->(t2:Table)
        RETURN t1.schema + '.' + t1.name AS from_table,
               t2.schema + '.' + t2.name AS to_table,
               r.sourceColumn AS from_column,
               r.targetColumn AS to_column
        """
        results = await self.client.execute_queries([query])
        existing_fks = results[0] if results else []
        
        return {
            (fk.get("from_table", ""), fk.get("to_table", ""), fk.get("from_column", ""), fk.get("to_column", ""))
            for fk in existing_fks
        }

    def _is_same_table(self, table1: Dict[str, Any], table2: Dict[str, Any]) -> bool:
        """같은 테이블인지 확인"""
        return (
            table1.get("schema_name", "") == table2.get("schema_name", "")
            and table1.get("table_name", "") == table2.get("table_name", "")
        )

    def _compare_table_columns(
        self,
        source_table: Dict[str, Any],
        target_table: Dict[str, Any],
        existing_fk_set: set,
        candidate_keys: set,
        candidate_info_map: Dict,
    ) -> List[Dict[str, Any]]:
        """테이블 컬럼 비교하여 FK 후보 추출"""
        candidates = []
        source_table_name = source_table.get("table_name", "")
        source_table_schema = source_table.get("schema_name", "")
        source_table_columns = source_table.get("columns", [])
        
        target_table_name = target_table.get("table_name", "")
        target_table_schema = target_table.get("schema_name", "")
        target_table_columns = target_table.get("columns", [])
        
        for source_col in source_table_columns:
            for target_col in target_table_columns:
                candidate = self._create_fk_candidate(
                    source_table_schema,
                    source_table_name,
                    source_col,
                    target_table_schema,
                    target_table_name,
                    target_col,
                    existing_fk_set,
                    candidate_keys,
                    candidate_info_map,
                )
                if candidate:
                    candidates.append(candidate)
        
        return candidates

    def _create_fk_candidate(
        self,
        source_schema: str,
        source_table: str,
        source_col: Dict[str, Any],
        target_schema: str,
        target_table: str,
        target_col: Dict[str, Any],
        existing_fk_set: set,
        candidate_keys: set,
        candidate_info_map: Dict,
    ) -> Optional[Dict[str, Any]]:
        """FK 후보 생성
        
        전략: 이름이 완전히 비슷한 것만 후보로 선정
        - 완전 일치 (대소문자 무시) → 최우선
        - 약간의 변형만 허용 (언더스코어 추가/제거, 약간의 철자 차이)
        """
        source_col_name = source_col.get("column_name", "")
        source_col_type = source_col.get("data_type", "")
        source_is_pk = source_col.get("is_primary_key", False)
        
        target_col_name = target_col.get("column_name", "")
        target_col_type = target_col.get("data_type", "")
        target_is_pk = target_col.get("is_primary_key", False)
        
        # 1. 완전 일치 체크 (대소문자 무시) - 최우선
        if source_col_name.lower() == target_col_name.lower():
            similarity = 1.0
        else:
            # 2. 유사도 계산 (완전 일치가 아닌 경우만)
            similarity = calculate_column_similarity(source_col_name, target_col_name)
            
            # 3. 엄격한 임계값 적용 (완전히 비슷한 것만)
            # - 0.95 이상: 거의 동일 (예: TAGSN vs TAG_SN)
            # - 0.9 이상: 매우 유사 (예: USER_ID vs USERID)
            # - 0.8 이상: 유사하지만 너무 관대함 (제외)
            if similarity < 0.95:
                return None
        
        # 타입 호환성 확인
        if not are_types_compatible(source_col_type, target_col_type):
            return None
        
        # 역방향 후보 체크 (기본키 우선순위 고려)
        from_table_key = f"{source_schema}.{source_table}"
        to_table_key = f"{target_schema}.{target_table}"
        candidate_key = (from_table_key, to_table_key, source_col_name, target_col_name)
        reverse_key = (to_table_key, from_table_key, target_col_name, source_col_name)
        
        # 기존 FK 체크
        if candidate_key in existing_fk_set:
            return None
        
        # 역방향 후보가 이미 있는지 확인
        if reverse_key in candidate_info_map:
            existing_info = candidate_info_map[reverse_key]
            existing_to_is_pk = existing_info.get("to_is_pk", False)
            existing_from_is_pk = existing_info.get("from_is_pk", False)
            current_to_is_pk = target_is_pk
            current_from_is_pk = source_is_pk
            
            # 기본키 우선순위 규칙:
            # 1. 타겟이 기본키인 쪽을 우선 선택
            # 2. 양쪽 모두 기본키가 없으면, 한쪽이 이미 있으면 연결하지 않음
            if current_to_is_pk and not existing_to_is_pk:
                # 현재 후보가 더 우선순위가 높음 (타겟이 PK) - 기존 것을 제거하고 현재 것 추가
                candidate_keys.discard(reverse_key)
                del candidate_info_map[reverse_key]
                candidate_keys.add(candidate_key)
                candidate_info_map[candidate_key] = {
                    "to_is_pk": target_is_pk,
                    "from_is_pk": source_is_pk,
                }
            elif not current_to_is_pk and not existing_to_is_pk:
                # 양쪽 모두 기본키가 없으면, 한쪽이 이미 있으면 연결하지 않음
                return None
            else:
                # 기존 것이 더 우선순위가 높거나 같으면 현재 것은 스킵
                return None
        elif candidate_key in candidate_keys:
            # 이미 같은 방향이 있으면 스킵 (중복)
            return None
        else:
            # 새로 추가
            candidate_keys.add(candidate_key)
            candidate_info_map[candidate_key] = {
                "to_is_pk": target_is_pk,
                "from_is_pk": source_is_pk,
            }
        
        # 우선순위 점수 계산
        priority_score = similarity
        if target_is_pk:
            priority_score += 0.5
        if source_is_pk:
            priority_score -= 0.1
        
        return {
            "from_table": source_table,
            "from_schema": source_schema,
            "from_column": source_col_name,
            "from_type": source_col_type,
            "from_is_pk": source_is_pk,
            "to_table": target_table,
            "to_schema": target_schema,
            "to_column": target_col_name,
            "to_type": target_col_type,
            "to_is_pk": target_is_pk,
            "similarity": similarity,
            "priority_score": priority_score,
        }

    async def verify_fk_relationship(
        self, session: aiohttp.ClientSession, candidate: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """FK 관계 검증 (샘플 데이터 매칭)
        
        Returns:
            성공 시: FK 정보 딕셔너리
            실패 시: {"success": False, "reason": "실패 원인"}
        """
        from_table = candidate["from_table"]
        from_schema = candidate["from_schema"]
        from_column = candidate["from_column"]
        to_table = candidate["to_table"]
        to_schema = candidate["to_schema"]
        to_column = candidate["to_column"]
        
        full_from_table = f'"{from_schema}"."{from_table}"'
        full_to_table = f'"{to_schema}"."{to_table}"'
        
        try:
            # 소스 샘플 데이터 조회
            sample_values = await self._get_sample_values(session, full_from_table, from_column)
            if not sample_values:
                return {"success": False, "reason": f"소스 테이블에서 샘플 데이터 없음: {full_from_table}.{from_column}"}
            
            # 타겟에서 매칭 확인
            match_ratio = await self._calculate_match_ratio(session, full_to_table, to_column, sample_values)
            if match_ratio is None:
                return {"success": False, "reason": f"타겟 테이블에서 매칭 데이터 없음"}
            
            # 매칭 비율 검증
            if match_ratio >= self.match_ratio_threshold:
                return {
                    **candidate,
                    "match_ratio": match_ratio,
                    "matched_count": int(match_ratio * len(sample_values)),
                    "total_samples": len(sample_values),
                }
            else:
                return {
                    "success": False,
                    "reason": f"매칭 비율 부족: {match_ratio:.1%} (임계값: {self.match_ratio_threshold:.1%})",
                }
                
        except Exception as e:
            return {"success": False, "reason": f"예외 발생: {str(e)[:200]}"}

    async def _get_sample_values(self, session: aiohttp.ClientSession, full_table: str, column: str) -> List[Any]:
        """소스 테이블에서 샘플 값 추출"""
        sample_sql = f'SELECT DISTINCT "{column}" FROM {full_table} WHERE "{column}" IS NOT NULL LIMIT {self.sample_size}'
        sample_data = await self.fetch_sample_data(session, sample_sql)
        
        if not sample_data:
            return []
        
        return [row.get(column) for row in sample_data if row.get(column) is not None]

    async def _calculate_match_ratio(
        self, session: aiohttp.ClientSession, full_table: str, column: str, sample_values: List[Any]
    ) -> Optional[float]:
        """매칭 비율 계산"""
        if not sample_values:
            return None
        
        # 값 이스케이프
        escaped_values = self._escape_values(sample_values[:self.sample_size])
        if not escaped_values:
            return None
        
        # 타겟에서 매칭 확인
        values_str = ", ".join(escaped_values)
        check_sql = f'SELECT DISTINCT "{column}" FROM {full_table} WHERE "{column}" IN ({values_str})'
        matched_data = await self.fetch_sample_data(session, check_sql)
        
        if not matched_data:
            return None
        
        matched_values = [row.get(column) for row in matched_data if row.get(column) is not None]
        
        # 매칭 비율 계산
        source_value_set = set(sample_values)
        matched_value_set = set(matched_values)
        matched_count = len(source_value_set & matched_value_set)
        
        log_process(
            "FK_INFERENCE",
            "VERIFY",
            f"매칭 검증: {full_table}.{column} (매칭: {matched_count}/{len(sample_values)} = {matched_count/len(sample_values):.2%})",
            logging.INFO,
        )
        
        return matched_count / len(sample_values) if sample_values else None

    def _escape_values(self, values: List[Any]) -> List[str]:
        """SQL 값 이스케이프"""
        escaped = []
        for v in values:
            if v is None:
                continue
            if isinstance(v, (int, float)):
                escaped.append(str(v))
            else:
                # SQL 문자열 이스케이핑: 작은따옴표를 두 개로 변환
                escaped_value = str(v).replace("'", "''")
                escaped.append(f"'{escaped_value}'")
        return escaped

    def _get_candidate_key(self, candidate: Dict[str, Any]) -> str:
        """후보 키 생성"""
        return (
            f"{candidate['from_schema']}.{candidate['from_table']}.{candidate['from_column']} -> "
            f"{candidate['to_schema']}.{candidate['to_table']}.{candidate['to_column']}"
        )

    async def verify_fk_relationships_batch(
        self, 
        session: aiohttp.ClientSession, 
        candidates: List[Dict[str, Any]]
    ) -> Dict[str, Dict[str, Any]]:
        """FK 후보를 소스 컬럼별로 그룹화 → 소스 샘플 1회 조회 → 개별 타겟 검증
        
        전략:
          1. 소스 컬럼별 그룹화 (같은 소스는 샘플 1회만 조회)
          2. 각 타겟 후보마다 개별 SQL로 검증 (UNION ALL 사용 안함)
          3. 세마포어로 동시 SQL 쿼리 수 제한 (커넥션 풀 보호)
        
        Returns:
            {candidate_key: result_dict} 형태의 결과
        """
        if not candidates:
            return {}
        
        # 동시 SQL 쿼리 수 제한 (QueuePool 보호)
        semaphore = asyncio.Semaphore(self.fk_concurrency)
        
        # 1. 후보를 소스 컬럼별로 그룹화
        candidates_by_source: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}
        for candidate in candidates:
            source_key = (
                candidate["from_schema"], 
                candidate["from_table"], 
                candidate["from_column"]
            )
            if source_key not in candidates_by_source:
                candidates_by_source[source_key] = []
            candidates_by_source[source_key].append(candidate)
        
        log_process(
            "FK_INFERENCE",
            "VERIFY_START",
            f"검증 시작: 후보 {len(candidates)}개 → {len(candidates_by_source)}개 소스 컬럼 그룹",
            logging.INFO,
        )
        
        results: Dict[str, Dict[str, Any]] = {}
        processed_sources = 0
        
        # 2. 각 소스 컬럼별로 처리
        for (from_schema, from_table, from_column), source_candidates in candidates_by_source.items():
            if not source_candidates:
                continue
            
            processed_sources += 1
            full_from_table = f'"{from_schema}"."{from_table}"'
            
            # 2-1. 소스 샘플 값 한 번만 조회
            sample_values = await self._get_sample_values(session, full_from_table, from_column)
            
            if not sample_values:
                for candidate in source_candidates:
                    key = self._get_candidate_key(candidate)
                    results[key] = {
                        "success": False,
                        "reason": "소스 데이터 없음"
                    }
                continue
            
            # 이스케이프
            escaped_values = self._escape_values(sample_values)
            if not escaped_values:
                for candidate in source_candidates:
                    key = self._get_candidate_key(candidate)
                    results[key] = {"success": False, "reason": "샘플 값 이스케이프 실패"}
                continue
            
            values_str = ", ".join(escaped_values)
            total_samples = len(sample_values)
            
            # 2-2. 각 타겟 후보를 개별 검증 (세마포어로 동시성 제한)
            async def _verify_single_target(candidate: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
                """개별 타겟 검증"""
                key = self._get_candidate_key(candidate)
                to_schema = candidate["to_schema"]
                to_table = candidate["to_table"]
                to_column = candidate["to_column"]
                full_to_table = f'"{to_schema}"."{to_table}"'
                
                check_sql = (
                    f'SELECT COUNT(DISTINCT "{to_column}") as matched_count '
                    f'FROM {full_to_table} '
                    f'WHERE "{to_column}" IN ({values_str})'
                )
                
                async with semaphore:
                    try:
                        check_result = await self.fetch_sample_data(session, check_sql)
                    except Exception as e:
                        error_msg = str(e)
                        # QueuePool 오류인 경우 재시도 가능하도록 표시
                        if "QueuePool" in error_msg or "connection timed out" in error_msg:
                            return key, {"success": False, "reason": f"연결 풀 오류 (재시도 필요): {error_msg[:150]}"}
                        return key, {"success": False, "reason": f"SQL 실행 오류: {error_msg[:150]}"}
                
                # 결과 분석
                if check_result is None:
                    return key, {"success": False, "reason": f"SQL 실행 실패 (테이블/컬럼 불일치 가능): {full_to_table}.\"{to_column}\""}
                
                if not check_result:
                    return key, {"success": False, "reason": "매칭 데이터 없음 (빈 결과)"}
                
                matched_count = check_result[0].get("matched_count", 0) if check_result else 0
                match_ratio = matched_count / total_samples if total_samples > 0 else 0
                
                if matched_count == 0:
                    return key, {"success": False, "reason": "매칭 데이터 없음 (0건)"}
                
                if match_ratio >= self.match_ratio_threshold:
                    return key, {
                        **candidate,
                        "match_ratio": match_ratio,
                        "matched_count": matched_count,
                        "total_samples": total_samples,
                    }
                else:
                    return key, {
                        "success": False,
                        "reason": f"매칭 비율 부족: {matched_count}/{total_samples} ({match_ratio:.0%} < {self.match_ratio_threshold:.0%})"
                    }
            
            # 타겟들을 병렬로 검증 (세마포어가 동시성 제한)
            tasks = [_verify_single_target(c) for c in source_candidates]
            task_results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for result in task_results:
                if isinstance(result, Exception):
                    log_process("FK_INFERENCE", "TASK_ERROR", f"비동기 태스크 예외: {result}", logging.ERROR)
                    continue
                key, result_dict = result
                results[key] = result_dict
            
            # 진행 로그 (50개 소스 컬럼마다)
            if processed_sources % 50 == 0:
                log_process(
                    "FK_INFERENCE",
                    "VERIFY_PROGRESS",
                    f"검증 진행: {processed_sources}/{len(candidates_by_source)}개 소스 컬럼 완료",
                    logging.INFO,
                )
        
        return results

    async def save_fk_relationship(self, fk_info: Dict[str, Any]) -> None:
        """Neo4j에 FK 관계 저장 (FK_TO_TABLE + FK_TO_COLUMN)
        
        FK_TO_TABLE이 생성되면 반드시 FK_TO_COLUMN도 생성되어야 합니다.
        Column 노드가 없으면 생성합니다.
        """
        from_key = f"{fk_info['from_schema']}.{fk_info['from_table']}.{fk_info['from_column']}"
        to_key = f"{fk_info['to_schema']}.{fk_info['to_table']}.{fk_info['to_column']}"
        
        # FK_TO_TABLE 저장
        await self._save_fk_table_relationship(fk_info)
        
        # FK_TO_COLUMN 저장 (Column 노드가 없으면 생성)
        try:
            await self._save_fk_column_relationship(fk_info)
        except Exception as e:
            # FK_TO_COLUMN 생성 실패 시 에러 로그
            log_process(
                "FK_INFERENCE",
                "COLUMN_REL_ERROR",
                f"FK_TO_COLUMN 생성 실패 (FK_TO_TABLE은 생성됨): {from_key} → {to_key} - {e}",
                logging.ERROR,
            )
            # Column 노드가 없을 수 있으므로 생성 시도
            log_process(
                "FK_INFERENCE",
                "CREATING_COLUMNS",
                f"Column 노드 생성 시도: {from_key} → {to_key}",
                logging.INFO,
            )
            await self._ensure_column_nodes_exist(fk_info)
            log_process(
                "FK_INFERENCE",
                "COLUMNS_CREATED",
                f"Column 노드 생성 완료: {from_key} → {to_key}",
                logging.INFO,
            )
            # 다시 시도
            try:
                await self._save_fk_column_relationship(fk_info)
                log_process(
                    "FK_INFERENCE",
                    "COLUMN_SAVED_RETRY",
                    f"FK_TO_COLUMN 저장 재시도 성공: {from_key} → {to_key}",
                    logging.INFO,
                )
            except Exception as e2:
                log_process(
                    "FK_INFERENCE",
                    "COLUMN_REL_FATAL",
                    f"FK_TO_COLUMN 저장 재시도 실패: {from_key} → {to_key} - {e2}",
                    logging.ERROR,
                )
                raise  # 최종 실패 시 예외 전파
        
        log_process(
            "FK_INFERENCE",
            "SAVED",
            f"FK 관계 저장 완료 (FK_TO_TABLE + FK_TO_COLUMN): {from_key} → {to_key}",
            logging.INFO,
        )

    async def _save_fk_table_relationship(self, fk_info: Dict[str, Any]) -> None:
        """FK_TO_TABLE 관계 저장"""
        query = """
        MATCH (t1:Table {name: $from_table, schema: $from_schema})
        MATCH (t2:Table {name: $to_table, schema: $to_schema})
        MERGE (t1)-[r:FK_TO_TABLE {
            sourceColumn: $from_column,
            targetColumn: $to_column
        }]->(t2)
        ON CREATE SET 
            r.type = 'many_to_one',
            r.source = $source,
            r.similarity = $similarity,
            r.match_ratio = $match_ratio,
            r.matched_count = $matched_count,
            r.total_samples = $total_samples
        ON MATCH SET
            r.source = CASE 
                WHEN r.source = 'ddl' THEN 'ddl'
                ELSE $source
            END
        RETURN r
        """
        
        await self.client.execute_with_params(query, self._build_fk_params(fk_info))

    async def _save_fk_column_relationship(self, fk_info: Dict[str, Any]) -> None:
        """FK_TO_COLUMN 관계 저장 (Column 노드가 없으면 생성)"""
        from_col_fqn = f"{fk_info['from_schema']}.{fk_info['from_table']}.{fk_info['from_column']}"
        to_col_fqn = f"{fk_info['to_schema']}.{fk_info['to_table']}.{fk_info['to_column']}"
        
        # Column 노드가 없으면 생성하고, FK_TO_COLUMN 관계도 생성
        query = """
        // 소스 컬럼 노드 생성 또는 조회
        MERGE (c1:Column {fqn: $from_col_fqn})
        ON CREATE SET 
            c1.name = $from_column,
            c1.dtype = $from_type,
            c1.description = '',
            c1.description_source = ''
        
        // 타겟 컬럼 노드 생성 또는 조회
        MERGE (c2:Column {fqn: $to_col_fqn})
        ON CREATE SET 
            c2.name = $to_column,
            c2.dtype = $to_type,
            c2.description = '',
            c2.description_source = ''
        
        // 테이블-컬럼 관계 확인 및 생성
        WITH c1, c2
        MATCH (t1:Table {name: $from_table, schema: $from_schema})
        MATCH (t2:Table {name: $to_table, schema: $to_schema})
        
        // 소스 테이블-컬럼 관계
        MERGE (t1)-[:HAS_COLUMN]->(c1)
        
        // 타겟 테이블-컬럼 관계
        MERGE (t2)-[:HAS_COLUMN]->(c2)
        
        // FK_TO_COLUMN 관계 생성
        WITH c1, c2
        MERGE (c1)-[r:FK_TO_COLUMN]->(c2)
        ON CREATE SET 
            r.type = 'many_to_one',
            r.source = $source,
            r.similarity = $similarity,
            r.match_ratio = $match_ratio,
            r.matched_count = $matched_count,
            r.total_samples = $total_samples
        ON MATCH SET
            r.source = CASE 
                WHEN r.source = 'ddl' THEN 'ddl'
                ELSE $source
            END
        RETURN r
        """
        
        params = self._build_fk_params(fk_info)
        params.update({
            "from_col_fqn": from_col_fqn, 
            "to_col_fqn": to_col_fqn,
            "from_column": fk_info['from_column'],
            "to_column": fk_info['to_column'],
            "from_type": fk_info.get('from_type', ''),
            "to_type": fk_info.get('to_type', ''),
        })
        await self.client.execute_with_params(query, params)
    
    async def _ensure_column_nodes_exist(self, fk_info: Dict[str, Any]) -> None:
        """Column 노드가 존재하는지 확인하고 없으면 생성"""
        from_col_fqn = f"{fk_info['from_schema']}.{fk_info['from_table']}.{fk_info['from_column']}"
        to_col_fqn = f"{fk_info['to_schema']}.{fk_info['to_table']}.{fk_info['to_column']}"
        
        query = """
        // 소스 컬럼 노드 생성
        MERGE (c1:Column {fqn: $from_col_fqn})
        ON CREATE SET 
            c1.name = $from_column,
            c1.dtype = $from_type,
            c1.description = '',
            c1.description_source = ''
        
        // 타겟 컬럼 노드 생성
        MERGE (c2:Column {fqn: $to_col_fqn})
        ON CREATE SET 
            c2.name = $to_column,
            c2.dtype = $to_type,
            c2.description = '',
            c2.description_source = ''
        
        // 테이블-컬럼 관계 생성
        WITH c1, c2
        MATCH (t1:Table {name: $from_table, schema: $from_schema})
        MATCH (t2:Table {name: $to_table, schema: $to_schema})
        MERGE (t1)-[:HAS_COLUMN]->(c1)
        MERGE (t2)-[:HAS_COLUMN]->(c2)
        RETURN c1, c2
        """
        
        params = {
            "from_col_fqn": from_col_fqn,
            "to_col_fqn": to_col_fqn,
            "from_column": fk_info['from_column'],
            "to_column": fk_info['to_column'],
            "from_type": fk_info.get('from_type', ''),
            "to_type": fk_info.get('to_type', ''),
            "from_table": fk_info['from_table'],
            "from_schema": fk_info['from_schema'],
            "to_table": fk_info['to_table'],
            "to_schema": fk_info['to_schema'],
        }
        await self.client.execute_with_params(query, params)

    async def _find_fk_candidates_by_pk_values(
        self,
        session: aiohttp.ClientSession,
        source_tables: List[Dict[str, Any]],
        all_tables: List[Dict[str, Any]],
        existing_fk_set: set,
        candidate_keys: set,
        candidate_info_map: Dict,
    ) -> List[Dict[str, Any]]:
        """PK 값 기반 FK 후보 추출 (유사도 기반 후보가 없을 때 fallback)
        
        소스 테이블의 PK 컬럼 값들을 조회하고,
        타겟 테이블의 모든 컬럼에서 해당 값들이 존재하는지 IN 절로 확인합니다.
        """
        candidates = []
        
        for source_table in source_tables:
            source_table_name = source_table.get("table_name", "")
            source_table_schema = source_table.get("schema_name", "")
            source_table_columns = source_table.get("columns", [])
            
            # 소스 테이블의 PK 컬럼 찾기
            pk_columns = [col for col in source_table_columns if col.get("is_primary_key", False)]
            
            if not pk_columns:
                continue
            
            # 각 PK 컬럼에 대해
            for pk_col in pk_columns:
                pk_col_name = pk_col.get("column_name", "")
                pk_col_type = pk_col.get("data_type", "")
                
                # PK 값들 조회
                full_table = f'"{source_table_schema}"."{source_table_name}"'
                sample_sql = f'SELECT DISTINCT "{pk_col_name}" FROM {full_table} WHERE "{pk_col_name}" IS NOT NULL LIMIT {self.sample_size}'
                sample_data = await self.fetch_sample_data(session, sample_sql)
                
                if not sample_data:
                    continue
                
                pk_values = [row.get(pk_col_name) for row in sample_data if row.get(pk_col_name) is not None]
                if not pk_values:
                    continue
                
                # 타겟 테이블의 모든 컬럼과 비교
                for target_table in all_tables:
                    if self._is_same_table(source_table, target_table):
                        continue
                    
                    target_table_name = target_table.get("table_name", "")
                    target_table_schema = target_table.get("schema_name", "")
                    target_table_columns = target_table.get("columns", [])
                    
                    for target_col in target_table_columns:
                        target_col_name = target_col.get("column_name", "")
                        target_col_type = target_col.get("data_type", "")
                        target_is_pk = target_col.get("is_primary_key", False)
                        
                        # 타입 호환성 확인
                        if not are_types_compatible(pk_col_type, target_col_type):
                            continue
                        
                        # 역방향 후보 체크 (기본키 우선순위 고려)
                        from_table_key = f"{source_table_schema}.{source_table_name}"
                        to_table_key = f"{target_table_schema}.{target_table_name}"
                        candidate_key = (from_table_key, to_table_key, pk_col_name, target_col_name)
                        reverse_key = (to_table_key, from_table_key, target_col_name, pk_col_name)
                        
                        # 기존 FK 체크
                        if candidate_key in existing_fk_set:
                            continue
                        
                        # 역방향 후보가 이미 있는지 확인
                        if reverse_key in candidate_info_map:
                            existing_info = candidate_info_map[reverse_key]
                            existing_to_is_pk = existing_info.get("to_is_pk", False)
                            existing_from_is_pk = existing_info.get("from_is_pk", False)
                            current_to_is_pk = target_is_pk
                            current_from_is_pk = True  # PK 값 기반이므로 항상 True
                            
                            # 기본키 우선순위 규칙:
                            # 1. 타겟이 기본키인 쪽을 우선 선택
                            # 2. 양쪽 모두 기본키가 없으면, 한쪽이 이미 있으면 연결하지 않음
                            if current_to_is_pk and not existing_to_is_pk:
                                # 현재 후보가 더 우선순위가 높음 (타겟이 PK) - 기존 것을 제거하고 현재 것 추가
                                candidate_keys.discard(reverse_key)
                                del candidate_info_map[reverse_key]
                            elif not current_to_is_pk and not existing_to_is_pk:
                                # 양쪽 모두 기본키가 없으면, 한쪽이 이미 있으면 연결하지 않음
                                continue
                            else:
                                # 기존 것이 더 우선순위가 높거나 같으면 현재 것은 스킵
                                continue
                        elif candidate_key in candidate_keys:
                            # 이미 같은 방향이 있으면 스킵 (중복)
                            continue
                        
                        # 타겟 컬럼에서 PK 값들이 존재하는지 확인 (IN 절)
                        match_ratio = await self._check_pk_values_in_target(
                            session, target_table_schema, target_table_name, target_col_name, pk_values
                        )
                        
                        # 매칭 비율이 임계값 이상이면 후보로 추가
                        if match_ratio and match_ratio >= self.match_ratio_threshold:
                            candidate_keys.add(candidate_key)
                            candidate_info_map[candidate_key] = {
                                "to_is_pk": target_is_pk,
                                "from_is_pk": True,  # PK 값 기반이므로 항상 True
                            }
                            
                            # 우선순위 점수: PK 기반이므로 낮은 점수 (유사도는 0)
                            priority_score = 0.3  # PK 기반 후보는 낮은 우선순위
                            if target_is_pk:
                                priority_score += 0.2  # 타겟도 PK면 추가 점수
                            
                            log_process(
                                "FK_INFERENCE",
                                "PK_CANDIDATE",
                                f"PK 값 기반 후보: {from_table_key}.{pk_col_name} → {to_table_key}.{target_col_name} "
                                f"(매칭: {match_ratio:.1%})",
                                logging.INFO,
                            )
                            
                            candidates.append({
                                "from_table": source_table_name,
                                "from_schema": source_table_schema,
                                "from_column": pk_col_name,
                                "from_type": pk_col_type,
                                "from_is_pk": True,
                                "to_table": target_table_name,
                                "to_schema": target_table_schema,
                                "to_column": target_col_name,
                                "to_type": target_col_type,
                                "to_is_pk": target_is_pk,
                                "similarity": 0.0,  # 유사도 없음
                                "priority_score": priority_score,
                            })
        
        return candidates

    async def _check_pk_values_in_target(
        self, session: aiohttp.ClientSession, target_schema: str, target_table: str, target_column: str, pk_values: List[Any]
    ) -> Optional[float]:
        """타겟 컬럼에서 PK 값들이 존재하는지 확인 (IN 절)"""
        if not pk_values:
            return None
        
        # 값 이스케이프
        escaped_values = self._escape_values(pk_values[:self.sample_size])
        if not escaped_values:
            return None
        
        # IN 절로 확인
        full_table = f'"{target_schema}"."{target_table}"'
        values_str = ", ".join(escaped_values)
        check_sql = f'SELECT DISTINCT "{target_column}" FROM {full_table} WHERE "{target_column}" IN ({values_str})'
        
        matched_data = await self.fetch_sample_data(session, check_sql)
        if not matched_data:
            return None
        
        matched_values = [row.get(target_column) for row in matched_data if row.get(target_column) is not None]
        
        # 매칭 비율 계산
        source_value_set = set(pk_values)
        matched_value_set = set(matched_values)
        matched_count = len(source_value_set & matched_value_set)
        
        if not pk_values:
            return None
        
        return matched_count / len(pk_values)

    def _build_fk_params(self, fk_info: Dict[str, Any]) -> Dict[str, Any]:
        """FK 저장용 파라미터 생성"""
        return {
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
            "source": DESCRIPTION_SOURCE,
        }
