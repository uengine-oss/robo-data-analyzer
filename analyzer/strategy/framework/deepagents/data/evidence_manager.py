"""
Evidence Manager (단순화 버전)

플랜 규칙:
- IO + dedup(hash) + merge + status 전이
- evidence.attempts: 처리 시작 시 +1
- Neo4j deepagents_attempts: review/failed 전이 시만 +1

구조:
    data/runs/{run_id}/evidence/{element_id}.json
    data/runs/{run_id}/resolved/{element_id}.json

사용 예시:
    mgr = EvidenceManager(runs_root="data/runs", run_id="abc123")
    
    # 증거 로드/생성
    evidence = mgr.load_or_create(element_id)
    
    # 증거 업데이트
    evidence["tables"].append({"name": "ORDER_HEADER", "operation": "write"})
    mgr.save(element_id, evidence)
"""

import os
import json
import hashlib
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar
from pathlib import Path
from dataclasses import dataclass, asdict, field
from functools import wraps


# =============================================================================
# 타입 변수
# =============================================================================
T = TypeVar('T')


# =============================================================================
# 예외 클래스
# =============================================================================

class EvidenceRequiredError(Exception):
    """Evidence가 필수인 작업에서 Evidence가 없을 때 발생"""
    
    def __init__(self, element_id: str, message: str = ""):
        self.element_id = element_id
        self.message = message or f"Evidence required for element_id: {element_id}"
        super().__init__(self.message)


def require_evidence(func: Callable[..., T]) -> Callable[..., T]:
    """
    Evidence 필수 데코레이터
    
    함수의 첫 번째 인자가 EvidenceData인 경우, None이면 EvidenceRequiredError 발생
    
    사용 예시:
        @require_evidence
        def process(evidence: EvidenceData):
            ...
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        # 첫 번째 인자가 EvidenceData인지 확인
        evidence = None
        if args:
            evidence = args[0] if isinstance(args[0], EvidenceData) else None
        if evidence is None and 'evidence' in kwargs:
            evidence = kwargs['evidence']
        
        if evidence is None:
            raise EvidenceRequiredError("unknown", "Evidence is required but not provided")
        
        return func(*args, **kwargs)
    
    return wrapper


@dataclass
class EvidenceData:
    """증거 데이터 구조 (단순화)"""
    element_id: str
    artifact_key: str = ""
    
    # 상태
    status: str = "working"  # working, success, failed, review
    status_reason: str = ""
    
    # 카운터
    attempts: int = 0
    tool_call_count: int = 0
    search_fail_count: int = 0
    
    # 추출 결과
    tables: List[Dict[str, Any]] = field(default_factory=list)
    columns: List[Dict[str, Any]] = field(default_factory=list)
    fks: List[Dict[str, Any]] = field(default_factory=list)
    
    # 검색/슬라이스 결과 (dedup용)
    search_results: List[Dict[str, Any]] = field(default_factory=list)
    slice_results: List[Dict[str, Any]] = field(default_factory=list)
    
    # 메타
    run_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    
    # 소스 정보
    source_files: List[str] = field(default_factory=list)
    hints: List[str] = field(default_factory=list)
    
    # artifact 레벨 메타데이터 (entity_name 등)
    # 플랜: 테이블별이 아닌 artifact 레벨로 저장
    artifact_meta: Dict[str, Any] = field(default_factory=dict)
    
    # 변경 이력 (최근 3개만 유지, 디버깅/추적용)
    # 플랜: before 스냅샷만 저장, after는 current tables로 확인
    history: List[Dict[str, Any]] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EvidenceData":
        # 필드명 매핑 (호환성)
        field_names = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in field_names}
        
        # 리스트 필드 기본값
        for list_field in ["tables", "columns", "fks", "search_results", "slice_results", "source_files", "hints", "history"]:
            if list_field not in filtered:
                filtered[list_field] = []
        
        # dict 필드 기본값
        if "artifact_meta" not in filtered:
            filtered["artifact_meta"] = {}
        
        return cls(**filtered)


class EvidenceManager:
    """
    증거 파일 관리 (단순화)
    
    핵심 기능:
    - IO: JSON 파일 읽기/쓰기
    - dedup: 중복 search/slice 결과 제거 (hash 기반)
    - merge: 추출 데이터 병합 (confidence 비교)
    - status 전이: working → success/failed/review
    """
    
    def __init__(self, runs_root: str, run_id: str = ""):
        """
        Args:
            runs_root: 작업 디렉토리 루트 (예: data/runs)
            run_id: 현재 실행 ID
        """
        self.runs_root = Path(runs_root)
        self.run_id = run_id
        self.base_path = self.runs_root / run_id / "evidence" if run_id else self.runs_root / "evidence"
        
        # 디렉토리 생성
        self.base_path.mkdir(parents=True, exist_ok=True)
        
        # resolved 디렉토리
        self.resolved_path = self.runs_root / run_id / "resolved" if run_id else self.runs_root / "resolved"
        self.resolved_path.mkdir(parents=True, exist_ok=True)
    
    def _sanitize_id(self, element_id: str) -> str:
        """element_id를 파일 시스템 안전한 문자열로 변환"""
        # 콜론, 특수문자 처리
        safe_id = element_id.replace(':', '_').replace('/', '_').replace('\\', '_')
        for char in ['*', '?', '"', '<', '>', '|']:
            safe_id = safe_id.replace(char, '_')
        return safe_id
    
    def _get_evidence_path(self, element_id: str) -> Path:
        """증거 파일 경로"""
        safe_id = self._sanitize_id(element_id)
        return self.base_path / f"{safe_id}.json"
    
    def _get_resolved_path(self, element_id: str) -> Path:
        """최종 결과 파일 경로"""
        safe_id = self._sanitize_id(element_id)
        return self.resolved_path / f"{safe_id}.json"
    
    def _hash_content(self, content: str) -> str:
        """내용 해시 (dedup용)"""
        return hashlib.sha256(content.encode()).hexdigest()[:16]
    
    # =========================================================================
    # IO
    # =========================================================================
    
    def load(self, element_id: str) -> Optional[EvidenceData]:
        """
        증거 파일 로드
        
        Args:
            element_id: 노드 element_id
        
        Returns:
            EvidenceData or None
        """
        path = self._get_evidence_path(element_id)
        
        if not path.exists():
            return None
        
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return EvidenceData.from_dict(data)
        except (json.JSONDecodeError, Exception):
            return None
    
    def save(self, element_id: str, evidence: EvidenceData):
        """
        증거 파일 저장
        
        Args:
            element_id: 노드 element_id
            evidence: EvidenceData
        """
        path = self._get_evidence_path(element_id)
        
        # 업데이트 시간
        evidence.updated_at = datetime.now().isoformat()
        
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(evidence.to_dict(), f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    
    def load_or_create(self, element_id: str, artifact_key: str = "", hints: List[str] = None) -> EvidenceData:
        """
        증거 로드 또는 생성 (플랜: S0에서 사용)
        
        - 처리 시작 시 attempts += 1
        
        Args:
            element_id: 노드 element_id
            artifact_key: artifact_key
            hints: external_hints
        
        Returns:
            EvidenceData
        """
        evidence = self.load(element_id)
        
        if evidence is None:
            evidence = EvidenceData(
                element_id=element_id,
                artifact_key=artifact_key,
                hints=hints or [],
                run_id=self.run_id,
                created_at=datetime.now().isoformat(),
                status="working"
            )
        
        # 처리 시작 시 attempts +1 (플랜 규칙)
        evidence.attempts += 1
        evidence.run_id = self.run_id
        
        self.save(element_id, evidence)
        return evidence
    
    def exists(self, element_id: str) -> bool:
        """증거 파일 존재 여부"""
        return self._get_evidence_path(element_id).exists()
    
    def get_evidence_path(self, element_id: str) -> str:
        """증거 파일 경로 반환 (Neo4j 저장용)"""
        return str(self._get_evidence_path(element_id))
    
    # =========================================================================
    # Dedup (중복 제거)
    # =========================================================================
    
    def add_search_result(self, evidence: EvidenceData, result: Dict[str, Any]) -> bool:
        """
        검색 결과 추가 (중복 제거)
        
        Args:
            evidence: EvidenceData
            result: 검색 결과 {"file_path": ..., "text": ..., ...}
        
        Returns:
            True if 새로 추가됨, False if 중복
        """
        # 해시 계산 (file_path + text)
        content = f"{result.get('file_path', '')}:{result.get('text', '')}"
        content_hash = self._hash_content(content)
        
        # 중복 체크
        for existing in evidence.search_results:
            if existing.get("_hash") == content_hash:
                return False
        
        # 추가
        result["_hash"] = content_hash
        result["_added_at"] = datetime.now().isoformat()
        evidence.search_results.append(result)
        return True
    
    def add_slice_result(self, evidence: EvidenceData, result: Dict[str, Any]) -> bool:
        """
        슬라이스 결과 추가 (중복 제거)
        
        Args:
            evidence: EvidenceData
            result: 슬라이스 결과 {"file_path": ..., "byte_offset": ..., "content": ...}
        
        Returns:
            True if 새로 추가됨, False if 중복
        """
        # 해시 계산 (file_path + offset + content 앞 100자)
        content = f"{result.get('file_path', '')}:{result.get('byte_offset', 0)}:{result.get('content', '')[:100]}"
        content_hash = self._hash_content(content)
        
        # 중복 체크
        for existing in evidence.slice_results:
            if existing.get("_hash") == content_hash:
                return False
        
        # 추가 (content는 너무 크면 제외)
        slice_meta = {
            "_hash": content_hash,
            "_added_at": datetime.now().isoformat(),
            "file_path": result.get("file_path", ""),
            "byte_offset": result.get("byte_offset", 0),
            "bytes_read": result.get("bytes_read", 0),
            "mtime": result.get("mtime", 0),
            "size": result.get("size", 0),
        }
        evidence.slice_results.append(slice_meta)
        return True
    
    # =========================================================================
    # Merge (병합)
    # =========================================================================
    
    def merge_table(
        self,
        evidence: EvidenceData,
        table_name: str,
        operation: str = "read",
        confidence: float = 0.5,
        source_type: str = "",
        is_primary: bool = True
    ) -> bool:
        """
        테이블 정보 병합 (confidence 비교 + history 기록)
        
        플랜 규칙:
        - confidence 업데이트 시 기존 값을 history에 before 스냅샷으로 기록
        - history는 최근 3개만 유지
        
        Args:
            evidence: EvidenceData
            table_name: 테이블명
            operation: read/write
            confidence: 신뢰도
            source_type: 출처 타입 (entity_xml, sql_mapper, etc.)
            is_primary: primary 테이블 여부
        
        Returns:
            True if 새로 추가되거나 업데이트됨
        """
        table_name_upper = table_name.upper()
        
        # 기존 테이블 찾기
        for existing in evidence.tables:
            if existing.get("name", "").upper() == table_name_upper:
                old_confidence = existing.get("confidence", 0)
                
                # confidence 비교 → 더 높으면 업데이트
                if confidence > old_confidence:
                    # history에 이전 값 기록 (before 스냅샷만)
                    history_entry = {
                        "table": table_name_upper,
                        "before": existing.copy(),
                        "at": datetime.now().isoformat(),
                        "reason": "higher_confidence"
                    }
                    evidence.history.append(history_entry)
                    
                    # 최근 3개만 유지
                    if len(evidence.history) > 3:
                        evidence.history.pop(0)
                    
                    # 업데이트
                    existing["confidence"] = confidence
                    existing["operation"] = operation
                    existing["source_type"] = source_type
                    existing["is_primary"] = is_primary
                    return True
                return False
        
        # 새로 추가
        evidence.tables.append({
            "name": table_name_upper,
            "operation": operation,
            "confidence": confidence,
            "source_type": source_type,
            "is_primary": is_primary,
            "_added_at": datetime.now().isoformat()
        })
        return True
    
    def merge_column(
        self,
        evidence: EvidenceData,
        table_name: str,
        column_name: str,
        data_type: str = "",
        nullable: bool = True,
        pk: bool = False
    ) -> bool:
        """
        컬럼 정보 병합
        
        Args:
            evidence: EvidenceData
            table_name: 테이블명
            column_name: 컬럼명
            data_type: 데이터 타입
            nullable: NULL 허용
            pk: PK 여부
        
        Returns:
            True if 새로 추가됨
        """
        table_name_upper = table_name.upper()
        column_name_upper = column_name.upper()
        
        # 중복 체크
        for existing in evidence.columns:
            if (existing.get("table_name", "").upper() == table_name_upper and
                existing.get("name", "").upper() == column_name_upper):
                return False
        
        evidence.columns.append({
            "table_name": table_name_upper,
            "name": column_name_upper,
            "data_type": data_type,
            "nullable": nullable,
            "pk": pk,
            "_added_at": datetime.now().isoformat()
        })
        return True
    
    def merge_fk(
        self,
        evidence: EvidenceData,
        from_table: str,
        from_column: str,
        to_table: str,
        to_column: str
    ) -> bool:
        """
        FK 정보 병합
        
        Args:
            evidence: EvidenceData
            from_table: 소스 테이블
            from_column: FK 컬럼
            to_table: 타겟 테이블
            to_column: PK 컬럼
        
        Returns:
            True if 새로 추가됨
        """
        # 중복 체크
        for existing in evidence.fks:
            if (existing.get("from_table", "").upper() == from_table.upper() and
                existing.get("from_column", "").upper() == from_column.upper() and
                existing.get("to_table", "").upper() == to_table.upper()):
                return False
        
        evidence.fks.append({
            "from_table": from_table.upper(),
            "from_column": from_column.upper(),
            "to_table": to_table.upper(),
            "to_column": to_column.upper(),
            "_added_at": datetime.now().isoformat()
        })
        return True
    
    # =========================================================================
    # Status 전이
    # =========================================================================
    
    def set_status_success(self, evidence: EvidenceData):
        """
        성공 상태로 전이
        - deepagents_attempts 증가 안 함 (플랜 규칙)
        """
        evidence.status = "success"
        evidence.status_reason = ""
        self.save(evidence.element_id, evidence)
    
    def set_status_review(self, evidence: EvidenceData, reason: str):
        """
        리뷰 상태로 전이
        - Neo4j deepagents_attempts는 별도로 +1 (neo4j_tools에서)
        """
        evidence.status = "review"
        evidence.status_reason = reason
        self.save(evidence.element_id, evidence)
    
    def set_status_failed(self, evidence: EvidenceData, reason: str):
        """
        실패 상태로 전이
        - Neo4j deepagents_attempts는 별도로 +1 (neo4j_tools에서)
        """
        evidence.status = "failed"
        evidence.status_reason = reason
        self.save(evidence.element_id, evidence)
    
    def increment_tool_call(self, evidence: EvidenceData):
        """도구 호출 카운트 증가"""
        evidence.tool_call_count += 1
    
    def increment_search_fail(self, evidence: EvidenceData):
        """검색 실패 카운트 증가"""
        evidence.search_fail_count += 1
    
    # =========================================================================
    # 충돌 검사
    # =========================================================================
    
    def is_write_conflict(self, evidence: EvidenceData) -> bool:
        """
        write 충돌 검사 (플랜 규칙)
        
        - 조인은 OK (테이블 여러 개 정상)
        - 모순 = write 대상(primary table)이 2개 이상
        
        Returns:
            True if 충돌
        """
        write_primary_tables = [
            t for t in evidence.tables
            if t.get("operation") == "write" and t.get("is_primary", True)
        ]
        return len(write_primary_tables) >= 2
    
    def is_table_mismatch(self, evidence: EvidenceData) -> bool:
        """
        entity vs SQL 테이블 불일치 검사
        
        Returns:
            True if 불일치
        """
        entity_tables = {
            t["name"] for t in evidence.tables
            if t.get("source_type") == "entity_xml"
        }
        sql_tables = {
            t["name"] for t in evidence.tables
            if t.get("source_type") in ("sql_mapper", "mybatis")
        }
        
        if entity_tables and sql_tables:
            # 교집합이 없으면 불일치
            return len(entity_tables.intersection(sql_tables)) == 0
        
        return False
    
    # =========================================================================
    # Resolved (최종 결과)
    # =========================================================================
    
    def save_resolved(self, element_id: str, data: Dict[str, Any]):
        """최종 결과 저장"""
        path = self._get_resolved_path(element_id)
        
        data["_resolved_at"] = datetime.now().isoformat()
        data["_run_id"] = self.run_id
        
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    
    def load_resolved(self, element_id: str) -> Optional[Dict[str, Any]]:
        """최종 결과 로드"""
        path = self._get_resolved_path(element_id)
        
        if not path.exists():
            return None
        
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return None


# =============================================================================
# LangChain 도구 (호환성 유지)
# =============================================================================

def create_evidence_tool(evidence_manager: EvidenceManager):
    """서브에이전트용 도구 생성 (호환성 유지)"""
    
    def create_evidence(
        patch_id: str,
        source_file: str,
        line_start: int,
        line_end: int,
        matched_content: str,
        extraction_method: str,
        confidence: float,
        relationship_type: Optional[str] = None,
        from_node: Optional[str] = None,
        to_artifact: Optional[str] = None,
        model_name: Optional[str] = None,
        prompt_summary: Optional[str] = None,
    ) -> str:
        """증거 파일 생성 (레거시 호환)"""
        # element_id = patch_id로 사용
        evidence = evidence_manager.load_or_create(patch_id)
        
        # 테이블 정보 추출 시도
        if to_artifact and ":" in to_artifact:
            parts = to_artifact.split(":")
            if len(parts) >= 2:
                table_name = parts[-1]
                # relationship_type이 None인 경우 기본값 "read" 사용
                operation = "read"
                if relationship_type:
                    operation = "write" if "write" in relationship_type.lower() else "read"
                evidence_manager.merge_table(evidence, table_name, operation, confidence)
        
        # source_file 추가
        if source_file and source_file not in evidence.source_files:
            evidence.source_files.append(source_file)
        
        evidence_manager.save(patch_id, evidence)
        
        return f"Evidence created for {patch_id}"
    
    def verify_evidence(patch_id: str) -> str:
        """증거 존재 및 무결성 확인 (레거시 호환)"""
        evidence = evidence_manager.load(patch_id)
        
        if evidence and evidence.tables:
            return f"Evidence verified: {len(evidence.tables)} table(s) found for {patch_id}"
        else:
            return f"Evidence verification failed for {patch_id}"
    
    return [create_evidence, verify_evidence]
