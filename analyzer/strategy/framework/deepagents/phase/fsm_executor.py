"""
FSM Executor - 후보 처리 상태 머신

최우선 규칙 3개:
1. DDL 없어도 무조건 생성
2. 테이블 발견되면 FROM/WRITES 무조건 생성
3. confidence는 우선순위용이지 커밋 조건이 아님

FSM 상태:
- S0: Load Evidence
- S1: artifact_key 생성
- S2: 캐시 조회
- S3: docs 탐색 (XML/YAML만!)
- S4: Neo4j 보완 탐색
- S5: 테이블 확정 (write_conflict만 review)
- S6: Operation 확정 (FROM/WRITES 무조건 생성)
- S7: 컬럼/FK 확정 [MVP 생략]
- S8: 커밋 전 검증 (tables 없음만 failed)
- S9: Neo4j 반영 (DBMS 표준 스키마)

Review reason (제한적 사용!):
- tool_limit: tool 호출 >= 10
- write_conflict: write의 primary table 2개 이상
"""

from enum import Enum, auto
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Callable
import asyncio

from ..support.cache_layer import CacheLayer, hints_to_artifact_key, needs_namespace_lookup, CacheEntry
from ..data.evidence_manager import EvidenceManager, EvidenceData


class FSMState(Enum):
    """FSM 상태"""
    S0_LOAD_EVIDENCE = auto()
    S1_CREATE_KEY = auto()
    S2_CACHE_LOOKUP = auto()
    S3_DOCS_SEARCH = auto()
    S4_NEO4J_LOOKUP = auto()
    S5_CONFIRM_TABLE = auto()
    S6_CONFIRM_OPERATION = auto()
    S7_CONFIRM_COLUMN_FK = auto()  # MVP 생략
    S8_PRE_COMMIT_CHECK = auto()
    S9_NEO4J_COMMIT = auto()
    DONE = auto()
    SKIP = auto()
    REVIEW = auto()
    FAILED = auto()


@dataclass
class FSMContext:
    """FSM 컨텍스트 (후보 처리 중 상태 유지)"""
    # 후보 정보
    element_id: str
    external_hints: List[str]
    file_path: str
    confidence: float
    
    # 상태
    current_state: FSMState = FSMState.S0_LOAD_EVIDENCE
    artifact_key: str = ""
    needs_s4: bool = False
    
    # Evidence
    evidence: Optional[EvidenceData] = None
    
    # 캐시
    cache_hit: bool = False
    cached_data: Optional[Dict[str, Any]] = None
    
    # 결과
    tables: List[Dict[str, Any]] = None
    mutations: List[Dict[str, Any]] = None
    
    # 최종
    final_status: str = ""  # success, review, failed
    final_reason: str = ""
    
    def __post_init__(self):
        if self.tables is None:
            self.tables = []
        if self.mutations is None:
            self.mutations = []


class RuntimePolicy:
    """
    런타임 정책 (가드레일 대체)
    
    플랜 규칙:
    - MAX_SLICE_LIMIT: 30KB
    - MAX_TOOL_CALLS: 10
    - MAX_SEARCH_FAILS: 2
    - MAX_RETRIES: 2
    """
    MAX_SLICE_LIMIT = 30000  # 30KB (바이트)
    MAX_TOOL_CALLS = 10
    MAX_SEARCH_FAILS = 2
    MAX_RETRIES = 2
    
    @classmethod
    def validate_slice_limit(cls, limit: int) -> int:
        """슬라이스 limit 검증"""
        return min(limit, cls.MAX_SLICE_LIMIT)
    
    @classmethod
    def should_review(cls, evidence: EvidenceData) -> Tuple[bool, str]:
        """
        리뷰 전이 조건 검사
        
        Returns:
            (should_review, reason_code)
        """
        # 우선순위 1: tool 호출 상한
        if evidence.tool_call_count >= cls.MAX_TOOL_CALLS:
            return True, "tool_limit"
        
        # 우선순위 2: 검색 실패 상한
        if evidence.search_fail_count >= cls.MAX_SEARCH_FAILS:
            return True, "search_fail"
        
        # 우선순위 3: 재시도 상한
        if evidence.attempts >= cls.MAX_RETRIES and evidence.status == "working":
            return True, "retry_exhausted"
        
        return False, ""


class FSMExecutor:
    """
    FSM 실행기
    
    후보 1개를 S0~S9 상태를 거쳐 처리
    """
    
    def __init__(
        self,
        evidence_manager: EvidenceManager,
        cache_layer: CacheLayer,
        docs_tools: Any,  # DocsTools
        neo4j_tools: Any,  # Neo4jTools
    ):
        self.evidence_mgr = evidence_manager
        self.cache = cache_layer
        self.docs_tools = docs_tools
        self.neo4j_tools = neo4j_tools
    
    async def process_candidate(self, candidate: Dict[str, Any]) -> FSMContext:
        """
        후보 1개 처리 (FSM 실행)
        
        Args:
            candidate: {
                "element_id": str,
                "external_hints": List[str],
                "file_path": str,
                "confidence": float,
                ...
            }
        
        Returns:
            FSMContext (최종 상태 포함)
        """
        ctx = FSMContext(
            element_id=candidate.get("element_id", ""),
            external_hints=candidate.get("external_hints", []),
            file_path=candidate.get("file_path", ""),
            confidence=candidate.get("confidence", 0.5),
        )
        
        # 상태 머신 루프
        while ctx.current_state not in (FSMState.DONE, FSMState.SKIP, FSMState.REVIEW, FSMState.FAILED):
            ctx = await self._execute_state(ctx)
        
        return ctx
    
    async def _execute_state(self, ctx: FSMContext) -> FSMContext:
        """상태별 실행"""
        state_handlers = {
            FSMState.S0_LOAD_EVIDENCE: self._s0_load_evidence,
            FSMState.S1_CREATE_KEY: self._s1_create_key,
            FSMState.S2_CACHE_LOOKUP: self._s2_cache_lookup,
            FSMState.S3_DOCS_SEARCH: self._s3_docs_search,
            FSMState.S4_NEO4J_LOOKUP: self._s4_neo4j_lookup,
            FSMState.S5_CONFIRM_TABLE: self._s5_confirm_table,
            FSMState.S6_CONFIRM_OPERATION: self._s6_confirm_operation,
            FSMState.S7_CONFIRM_COLUMN_FK: self._s7_confirm_column_fk,
            FSMState.S8_PRE_COMMIT_CHECK: self._s8_pre_commit_check,
            FSMState.S9_NEO4J_COMMIT: self._s9_neo4j_commit,
        }
        
        handler = state_handlers.get(ctx.current_state)
        if handler:
            return await handler(ctx)
        
        # 알 수 없는 상태 → FAILED
        ctx.current_state = FSMState.FAILED
        ctx.final_status = "failed"
        ctx.final_reason = "unknown_state"
        return ctx
    
    # =========================================================================
    # S0: Load Evidence
    # =========================================================================
    
    async def _s0_load_evidence(self, ctx: FSMContext) -> FSMContext:
        """
        S0: Evidence 로드 (attempts +1)
        
        - status=success → SKIP
        - status=review (24h 미경과) → SKIP
        """
        evidence = self.evidence_mgr.load_or_create(
            ctx.element_id,
            artifact_key="",
            hints=ctx.external_hints
        )
        ctx.evidence = evidence
        
        # 이미 성공 → SKIP
        if evidence.status == "success":
            ctx.current_state = FSMState.SKIP
            ctx.final_status = "skip"
            ctx.final_reason = "already_success"
            return ctx
        
        # 리뷰 상태 (24h 미경과) → SKIP
        # TODO: 24h 경과 체크 로직 추가
        if evidence.status == "review":
            ctx.current_state = FSMState.SKIP
            ctx.final_status = "skip"
            ctx.final_reason = "pending_review"
            return ctx
        
        ctx.current_state = FSMState.S1_CREATE_KEY
        return ctx
    
    # =========================================================================
    # S1: Create artifact_key
    # =========================================================================
    
    async def _s1_create_key(self, ctx: FSMContext) -> FSMContext:
        """
        S1: artifact_key 생성 + needs_s4 판별
        """
        ctx.artifact_key = hints_to_artifact_key(ctx.external_hints, ctx.file_path)
        ctx.needs_s4 = needs_namespace_lookup(ctx.artifact_key)
        
        # Evidence에도 저장
        if ctx.evidence:
            ctx.evidence.artifact_key = ctx.artifact_key
            self.evidence_mgr.save(ctx.element_id, ctx.evidence)
        
        ctx.current_state = FSMState.S2_CACHE_LOOKUP
        return ctx
    
    # =========================================================================
    # S2: Cache Lookup
    # =========================================================================
    
    async def _s2_cache_lookup(self, ctx: FSMContext) -> FSMContext:
        """
        S2: 캐시 조회
        
        - HIT + valid + !needs_s4 → S5
        - HIT + valid + needs_s4 → S4
        - MISS/invalid → S3
        """
        entry, hit_source = self.cache.get(ctx.artifact_key)
        
        if entry and hit_source != "miss":
            ctx.cache_hit = True
            ctx.cached_data = entry.data
            
            # 캐시된 테이블 정보 로드
            if "tables" in entry.data:
                ctx.tables = entry.data["tables"]
            
            # needs_s4면 S4로 (namespace 확정 필요)
            if ctx.needs_s4:
                ctx.current_state = FSMState.S4_NEO4J_LOOKUP
            else:
                ctx.current_state = FSMState.S5_CONFIRM_TABLE
        else:
            ctx.cache_hit = False
            ctx.current_state = FSMState.S3_DOCS_SEARCH
        
        return ctx
    
    # =========================================================================
    # S3: Docs Search
    # =========================================================================
    
    async def _s3_docs_search(self, ctx: FSMContext) -> FSMContext:
        """
        S3: docs 탐색
        
        - tool_call_count++
        - 실패 시 search_fail_count++
        - 상한 초과 시 review
        - entity_name 추출 후 artifact_meta에 저장
        """
        if not ctx.evidence:
            ctx.current_state = FSMState.FAILED
            ctx.final_status = "failed"
            ctx.final_reason = "no_evidence"
            return ctx
        
        # tool_call_count 증가
        self.evidence_mgr.increment_tool_call(ctx.evidence)
        
        # 정책 검사
        should_review, reason = RuntimePolicy.should_review(ctx.evidence)
        if should_review:
            ctx.current_state = FSMState.REVIEW
            ctx.final_status = "review"
            ctx.final_reason = reason
            self.evidence_mgr.set_status_review(ctx.evidence, reason)
            return ctx
        
        # entity_name 추출 → artifact_meta에 저장 (디버깅/추적용)
        entity_name = self._get_entity_name_from_hints(ctx.external_hints)
        if entity_name is not None:
            ctx.evidence.artifact_meta["entity_name"] = entity_name
        
        # 힌트에서 검색 키워드 추출
        hint_type = self._get_hint_type(ctx.external_hints)
        keyword = self._get_search_keyword(ctx.external_hints)
        
        if not keyword:
            self.evidence_mgr.increment_search_fail(ctx.evidence)
            
            # 정책 재검사
            should_review, reason = RuntimePolicy.should_review(ctx.evidence)
            if should_review:
                ctx.current_state = FSMState.REVIEW
                ctx.final_status = "review"
                ctx.final_reason = reason
                self.evidence_mgr.set_status_review(ctx.evidence, reason)
                return ctx
            
            # needs_s4면 Neo4j로
            if ctx.needs_s4:
                ctx.current_state = FSMState.S4_NEO4J_LOOKUP
            else:
                ctx.current_state = FSMState.S5_CONFIRM_TABLE
            return ctx
        
        # docs_search 실행
        try:
            results = self.docs_tools.docs_search(
                keyword=keyword,
                hint_type=hint_type,
                max_results=10
            )
            
            if not results:
                self.evidence_mgr.increment_search_fail(ctx.evidence)
            else:
                # 결과 저장 (dedup)
                for r in results:
                    self.evidence_mgr.add_search_result(ctx.evidence, {
                        "file_path": r.file_path,
                        "byte_offset": r.byte_offset,
                        "line_number": r.line_number,
                        "text": r.text
                    })
                
                # 첫 번째 결과로 slice
                first_result = results[0]
                slice_result = self.docs_tools.docs_slice(
                    file_path=first_result.file_path,
                    byte_offset=max(0, first_result.byte_offset - 2000),
                    limit=RuntimePolicy.MAX_SLICE_LIMIT
                )
                
                if slice_result.get("content"):
                    self.evidence_mgr.add_slice_result(ctx.evidence, slice_result)
                    
                    # 테이블 추출 시도
                    tables = self._extract_tables_from_content(
                        slice_result["content"],
                        hint_type,
                        ctx.external_hints
                    )
                    
                    for table in tables:
                        self.evidence_mgr.merge_table(
                            ctx.evidence,
                            table["name"],
                            table.get("operation", "read"),
                            table.get("confidence", 0.5),
                            table.get("source_type", hint_type),
                            table.get("is_primary", True)
                        )
                        ctx.tables.append(table)
            
            self.evidence_mgr.save(ctx.element_id, ctx.evidence)
            
        except Exception as e:
            self.evidence_mgr.increment_search_fail(ctx.evidence)
            self.evidence_mgr.save(ctx.element_id, ctx.evidence)
        
        # 정책 재검사
        should_review, reason = RuntimePolicy.should_review(ctx.evidence)
        if should_review:
            ctx.current_state = FSMState.REVIEW
            ctx.final_status = "review"
            ctx.final_reason = reason
            self.evidence_mgr.set_status_review(ctx.evidence, reason)
            return ctx
        
        # needs_s4면 S4, 아니면 S5
        if ctx.needs_s4:
            ctx.current_state = FSMState.S4_NEO4J_LOOKUP
        else:
            ctx.current_state = FSMState.S5_CONFIRM_TABLE
        
        return ctx
    
    def _get_hint_type(self, hints: List[str]) -> str:
        """힌트에서 타입 추출"""
        for hint in hints:
            hint_lower = hint.lower()
            if "namespace=" in hint_lower or "queryid=" in hint_lower:
                return "mybatis"
            if "entity=" in hint_lower:
                return "ofbiz_entity"
            if "service=" in hint_lower:
                return "ofbiz_service"
            if "configkey=" in hint_lower:
                return "config"
            if "jpa_entity=" in hint_lower:
                return "jpa"
        return ""
    
    def _get_search_keyword(self, hints: List[str]) -> str:
        """힌트에서 검색 키워드 추출"""
        for hint in hints:
            if "=" in hint:
                key, value = hint.split("=", 1)
                value = value.strip().replace('"', '').replace("'", "")
                
                # 우선순위: entity > queryId > namespace > service
                key_lower = key.lower()
                if key_lower in ("entity", "queryid", "namespace", "service", "jpa_entity"):
                    return value
        return ""
    
    def _get_entity_name_from_hints(self, hints: List[str]) -> Optional[str]:
        """
        힌트에서 객체명/식별자 추출 (디버깅/추적용 메타데이터)
        
        플랜 규칙:
        - OFBiz: entity=OrderHeader → "OrderHeader" (객체명)
        - OFBiz: service=createOrder → "createOrder" (서비스명)
        - JPA: jpa_entity=Product → "Product" (객체명)
        - MyBatis: namespace=OrderMapper + queryId=createOrder → "OrderMapper.createOrder" (완전 식별자)
        - MyBatis: queryId=createOrder만 있으면 → "unknown.createOrder" (namespace 없음)
        
        Args:
            hints: external_hints 리스트
        
        Returns:
            entity_name 또는 None (optional - 없어도 시스템 정상 동작)
        """
        hints_dict = {}
        for hint in hints:
            if "=" in hint:
                key, value = hint.split("=", 1)
                value = value.strip().replace('"', '').replace("'", "")
                hints_dict[key.lower()] = value
        
        # OFBiz entity (객체명)
        if "entity" in hints_dict:
            return hints_dict["entity"]
        
        # OFBiz service (서비스명)
        if "service" in hints_dict:
            return hints_dict["service"]
        
        # JPA entity (객체명)
        if "jpa_entity" in hints_dict:
            return hints_dict["jpa_entity"]
        
        # MyBatis: namespace + queryId → "namespace.queryId"
        if "namespace" in hints_dict and "queryid" in hints_dict:
            return f"{hints_dict['namespace']}.{hints_dict['queryid']}"
        
        # MyBatis: queryId만 → "unknown.queryId"
        if "queryid" in hints_dict:
            return f"unknown.{hints_dict['queryid']}"
        
        return None
    
    def _extract_tables_from_content(
        self,
        content: str,
        hint_type: str,
        hints: List[str]
    ) -> List[Dict[str, Any]]:
        """
        컨텐츠에서 테이블 추출 (간단한 패턴 매칭)
        
        TODO: 파서 도구와 통합
        """
        import re
        
        tables = []
        content_upper = content.upper()
        
        # MyBatis: FROM, JOIN, INSERT INTO, UPDATE, DELETE FROM
        if hint_type == "mybatis":
            # FROM/JOIN
            from_pattern = r'(?:FROM|JOIN)\s+([A-Z_][A-Z0-9_]*)'
            for match in re.finditer(from_pattern, content_upper):
                tables.append({
                    "name": match.group(1),
                    "operation": "read",
                    "confidence": 0.7,
                    "source_type": "sql_mapper",
                    "is_primary": match.group(0).startswith("FROM")
                })
            
            # INSERT/UPDATE/DELETE
            write_pattern = r'(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+([A-Z_][A-Z0-9_]*)'
            for match in re.finditer(write_pattern, content_upper):
                tables.append({
                    "name": match.group(1),
                    "operation": "write",
                    "confidence": 0.8,
                    "source_type": "sql_mapper",
                    "is_primary": True
                })
        
        # OFBiz entity: table-name 속성
        elif hint_type == "ofbiz_entity":
            table_pattern = r'TABLE-NAME="([^"]+)"'
            for match in re.finditer(table_pattern, content_upper):
                tables.append({
                    "name": match.group(1),
                    "operation": "read",
                    "confidence": 0.9,
                    "source_type": "entity_xml",
                    "is_primary": True
                })
        
        # 중복 제거
        seen = set()
        unique_tables = []
        for t in tables:
            key = (t["name"], t["operation"])
            if key not in seen:
                seen.add(key)
                unique_tables.append(t)
        
        return unique_tables
    
    # =========================================================================
    # S4: Neo4j Lookup (queryId-only면 강제)
    # =========================================================================
    
    async def _s4_neo4j_lookup(self, ctx: FSMContext) -> FSMContext:
        """
        S4: Neo4j 보완 탐색
        
        - namespace 후보 찾기
        - artifact_key 확정
        """
        if not ctx.evidence:
            ctx.current_state = FSMState.FAILED
            ctx.final_status = "failed"
            ctx.final_reason = "no_evidence"
            return ctx
        
        # tool_call_count 증가
        self.evidence_mgr.increment_tool_call(ctx.evidence)
        
        # 정책 검사
        should_review, reason = RuntimePolicy.should_review(ctx.evidence)
        if should_review:
            ctx.current_state = FSMState.REVIEW
            ctx.final_status = "review"
            ctx.final_reason = reason
            self.evidence_mgr.set_status_review(ctx.evidence, reason)
            return ctx
        
        # queryId 추출
        query_id = ""
        for hint in ctx.external_hints:
            if "queryid=" in hint.lower():
                query_id = hint.split("=", 1)[1].strip()
                break
        
        if query_id and self.neo4j_tools:
            try:
                # 같은 queryId를 가진 노드 검색
                result = await self.neo4j_tools.search_nodes_by_keyword(
                    keyword=query_id,
                    labels=["METHOD_CALL", "ANNOTATION"],
                    limit=10
                )
                
                if result.records:
                    # 첫 번째 결과에서 namespace 추출 시도
                    for record in result.records:
                        node_code = record.get("n", {}).get("node_code", "")
                        # namespace 패턴 찾기
                        import re
                        ns_match = re.search(r'namespace["\s:=]+([A-Za-z0-9_.]+)', node_code, re.IGNORECASE)
                        if ns_match:
                            namespace = ns_match.group(1).lower()
                            # artifact_key 업데이트
                            ctx.artifact_key = f"mybatis:{namespace}#{query_id.lower()}"
                            ctx.needs_s4 = False
                            
                            if ctx.evidence:
                                ctx.evidence.artifact_key = ctx.artifact_key
                                self.evidence_mgr.save(ctx.element_id, ctx.evidence)
                            break
                
            except Exception:
                pass
        
        self.evidence_mgr.save(ctx.element_id, ctx.evidence)
        ctx.current_state = FSMState.S5_CONFIRM_TABLE
        return ctx
    
    # =========================================================================
    # S5: Confirm Table
    # =========================================================================
    
    async def _s5_confirm_table(self, ctx: FSMContext) -> FSMContext:
        """
        S5: 테이블 확정
        
        - write_conflict → review
        - table_mismatch → review
        """
        if not ctx.evidence:
            ctx.current_state = FSMState.FAILED
            ctx.final_status = "failed"
            ctx.final_reason = "no_evidence"
            return ctx
        
        # 캐시된 데이터에서 테이블 로드
        if ctx.cache_hit and ctx.cached_data and "tables" in ctx.cached_data:
            for t in ctx.cached_data["tables"]:
                self.evidence_mgr.merge_table(
                    ctx.evidence,
                    t.get("name", ""),
                    t.get("operation", "read"),
                    t.get("confidence", 0.5),
                    t.get("source_type", ""),
                    t.get("is_primary", True)
                )
        
        # write 충돌 검사
        if self.evidence_mgr.is_write_conflict(ctx.evidence):
            ctx.current_state = FSMState.REVIEW
            ctx.final_status = "review"
            ctx.final_reason = "write_conflict"
            self.evidence_mgr.set_status_review(ctx.evidence, "write_conflict")
            return ctx
        
        # entity vs SQL 불일치 검사
        if self.evidence_mgr.is_table_mismatch(ctx.evidence):
            ctx.current_state = FSMState.REVIEW
            ctx.final_status = "review"
            ctx.final_reason = "table_mismatch"
            self.evidence_mgr.set_status_review(ctx.evidence, "table_mismatch")
            return ctx
        
        ctx.current_state = FSMState.S6_CONFIRM_OPERATION
        return ctx
    
    # =========================================================================
    # S6: Confirm Operation
    # =========================================================================
    
    async def _s6_confirm_operation(self, ctx: FSMContext) -> FSMContext:
        """
        S6: Operation 확정
        
        🚨 새 정책:
        - 테이블 발견되면 무조건 FROM/WRITES 생성
        - confidence는 우선순위용이지 커밋 조건이 아님
        - 불확실하면 FROM으로 (안전하게)
        """
        if not ctx.evidence:
            ctx.current_state = FSMState.FAILED
            ctx.final_status = "failed"
            ctx.final_reason = "no_evidence"
            return ctx
        
        # 각 테이블에 대해 mutation 생성 (무조건!)
        for table in ctx.evidence.tables:
            table_name = table.get("name", "")
            operation = table.get("operation", "read")
            
            # 🚨 confidence 체크 제거: operation만으로 판단
            # - write/INSERT/UPDATE/DELETE → WRITES
            # - read/SELECT/불확실 → FROM
            
            # mutation 생성
            if operation == "write":
                ctx.mutations.append({
                    "type": "MERGE_WRITES",
                    "from_element_id": ctx.element_id,
                    "to_table": table_name
                })
            else:
                ctx.mutations.append({
                    "type": "MERGE_FROM",
                    "from_element_id": ctx.element_id,
                    "to_table": table_name
                })
            
            # TABLE 노드 생성 mutation (DDL 없어도 무조건 생성!)
            ctx.mutations.append({
                "type": "MERGE_TABLE",
                "table": table_name
            })
        
        ctx.current_state = FSMState.S7_CONFIRM_COLUMN_FK
        return ctx
    
    # =========================================================================
    # S7: Confirm Column/FK [MVP 생략]
    # =========================================================================
    
    async def _s7_confirm_column_fk(self, ctx: FSMContext) -> FSMContext:
        """
        S7: 컬럼/FK 확정 [MVP 생략]
        """
        # MVP에서는 테이블만 확정
        ctx.current_state = FSMState.S8_PRE_COMMIT_CHECK
        return ctx
    
    # =========================================================================
    # S8: Pre-Commit Check
    # =========================================================================
    
    async def _s8_pre_commit_check(self, ctx: FSMContext) -> FSMContext:
        """
        S8: 커밋 전 검증
        
        🚨 새 정책:
        - 테이블 발견되면 무조건 커밋 (review 남용 금지)
        - confidence < 0.7 → review 아님! (성공 처리)
        - tables 없음만 failed
        """
        if not ctx.evidence:
            ctx.current_state = FSMState.FAILED
            ctx.final_status = "failed"
            ctx.final_reason = "no_evidence"
            return ctx
        
        # 테이블 없음 → failed (이것만 유일한 실패 조건)
        if not ctx.evidence.tables:
            ctx.current_state = FSMState.FAILED
            ctx.final_status = "failed"
            ctx.final_reason = "no_tables"
            self.evidence_mgr.set_status_failed(ctx.evidence, "no_tables")
            return ctx
        
        # 🚨 confidence 체크 제거: 테이블 있으면 무조건 커밋!
        # retry 상한은 도구 호출 제한용이지 커밋 차단 조건이 아님
        
        ctx.current_state = FSMState.S9_NEO4J_COMMIT
        return ctx
    
    # =========================================================================
    # S9: Neo4j Commit
    # =========================================================================
    
    async def _s9_neo4j_commit(self, ctx: FSMContext) -> FSMContext:
        """
        S9: Neo4j 반영 (트랜잭션 배치)
        
        - mutations 실행
        - 캐시 저장
        - status=success
        """
        if not ctx.evidence:
            ctx.current_state = FSMState.FAILED
            ctx.final_status = "failed"
            ctx.final_reason = "no_evidence"
            return ctx
        
        # mutations 실행
        if ctx.mutations and self.neo4j_tools:
            try:
                await self._apply_mutations(ctx.mutations)
            except Exception as e:
                ctx.current_state = FSMState.FAILED
                ctx.final_status = "failed"
                ctx.final_reason = f"commit_error: {str(e)}"
                self.evidence_mgr.set_status_failed(ctx.evidence, f"commit_error: {str(e)}")
                return ctx
        
        # 캐시 저장 (플랜: confidence, artifact_meta, tables 3개 필수 포함)
        # artifact 전체 confidence = max(table.confidence)
        if ctx.evidence.tables:
            artifact_confidence = max(
                t.get("confidence", 0.5) for t in ctx.evidence.tables
            )
        else:
            artifact_confidence = 0.5
        
        self.cache.set(
            ctx.artifact_key,
            {
                "confidence": artifact_confidence,  # artifact 전체 confidence
                "artifact_meta": ctx.evidence.artifact_meta,  # entity_name 포함
                "tables": [t for t in ctx.evidence.tables],  # 테이블 정보 배열
                "columns": [c for c in ctx.evidence.columns],
                "fks": [f for f in ctx.evidence.fks],
                "element_id": ctx.element_id,
                "mutations": ctx.mutations
            }
        )
        
        # 성공
        ctx.current_state = FSMState.DONE
        ctx.final_status = "success"
        self.evidence_mgr.set_status_success(ctx.evidence)
        
        # resolved 저장 (artifact_meta 포함)
        self.evidence_mgr.save_resolved(ctx.element_id, {
            "tables": ctx.evidence.tables,
            "artifact_meta": ctx.evidence.artifact_meta,
            "mutations": ctx.mutations,
            "artifact_key": ctx.artifact_key
        })
        
        return ctx
    
    async def _apply_mutations(self, mutations: List[Dict[str, Any]]):
        """
        mutations 실행 (트랜잭션 배치)
        
        플랜 규칙:
        - idempotent MERGE 유지
        - status 프로퍼티 미사용
        """
        if not self.neo4j_tools:
            return
        
        # neo4j_tools에 apply_graph_mutations 메서드가 있으면 사용
        if hasattr(self.neo4j_tools, 'apply_graph_mutations'):
            await self.neo4j_tools.apply_graph_mutations(mutations)
        else:
            # 개별 실행 (fallback)
            for mut in mutations:
                if mut["type"] == "MERGE_TABLE":
                    # TABLE 노드 생성은 check_table_exists + create_table_schema 조합
                    pass
                elif mut["type"] == "MERGE_FROM":
                    # from 관계 생성
                    pass
                elif mut["type"] == "MERGE_WRITES":
                    # writes 관계 생성
                    pass

