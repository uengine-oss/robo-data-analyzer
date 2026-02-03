"""
Cache Layer - 3계층 캐시 시스템 + Description 메모리

플랜 규칙:
- 도구 호출 전에 캐시 먼저 조회 (재검색 금지)
- 인메모리 캐시 → 파일 캐시 → (선택) 영속 인덱스
- 캐시 hit면 docs_search/docs_slice 절대 다시 하지 않는다
- 캐시 무효화: mtime + size

Description 메모리:
- 테이블/컬럼별 description을 청크 처리 중 수집
- 청크 완료 시 LLM으로 기존+신규 통합
- 통합된 description으로 Neo4j 업데이트 (덮어쓰기)
"""

from typing import Dict, Any, List, Optional, Tuple, Callable
from dataclasses import dataclass, field
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path
import json
import os
import hashlib
import time
import re


# =============================================================================
# 유사도 기반 매칭 유틸리티 (범용, 하드코딩 없음)
# =============================================================================

def normalize_for_match(s: str) -> str:
    """
    매칭용 문자열 정규화
    - 특수문자 제거 (알파벳, 숫자만 유지)
    - 소문자 변환
    
    예: "ORDER_HEADER" → "orderheader"
        "order-service.getOrders" → "orderservicegetorders"
    """
    return re.sub(r'[^a-z0-9]', '', s.lower())


def extract_tokens(value: str) -> set:
    """
    문자열에서 의미있는 토큰 추출 (범용 규칙)
    
    규칙:
    1. CamelCase 분리: getOrdersByStatus → [get, orders, by, status]
    2. 점(.) 분리: order.service → [order, service]
    3. 언더스코어(_) 분리: ORDER_HEADER → [order, header]
    4. 하이픈(-) 분리: order-service → [order, service]
    5. 2글자 이상만 포함
    
    Args:
        value: 원본 문자열
    
    Returns:
        추출된 토큰 집합
    """
    tokens = set()
    
    if not value:
        return tokens
    
    # 원본 정규화 버전 추가
    normalized = normalize_for_match(value)
    if len(normalized) >= 2:
        tokens.add(normalized)
    
    # CamelCase 분리
    camel_parts = re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|$|\d)', value)
    for part in camel_parts:
        part_lower = part.lower()
        if len(part_lower) >= 2:
            tokens.add(part_lower)
    
    # 구분자로 분리 (., _, -, 공백)
    for delimiter in ['.', '_', '-', ' ']:
        if delimiter in value:
            for segment in value.split(delimiter):
                segment_lower = segment.lower().strip()
                if len(segment_lower) >= 2:
                    tokens.add(segment_lower)
                # 각 세그먼트에서 CamelCase도 추출
                for part in re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|$|\d)', segment):
                    part_lower = part.lower()
                    if len(part_lower) >= 2:
                        tokens.add(part_lower)
    
    return tokens


def calc_similarity(a: str, b: str) -> float:
    """
    두 문자열 유사도 계산 (0.0 ~ 1.0)
    
    방법:
    1. 정규화 후 완전 일치 체크
    2. 포함 관계 체크 (부분 매칭)
    3. SequenceMatcher 유사도
    
    Args:
        a, b: 비교할 문자열
    
    Returns:
        유사도 (0.0 ~ 1.0)
    """
    # 정규화
    na = normalize_for_match(a)
    nb = normalize_for_match(b)
    
    if not na or not nb:
        return 0.0
    
    # 완전 일치
    if na == nb:
        return 1.0
    
    # 포함 관계 (order ⊂ orderheader)
    if na in nb or nb in na:
        shorter = min(len(na), len(nb))
        longer = max(len(na), len(nb))
        # 길이 비율 기반 (짧은 것이 긴 것에 포함되면 점수)
        return (shorter / longer) * 0.85
    
    # SequenceMatcher 유사도
    return SequenceMatcher(None, na, nb).ratio()


def hints_to_artifact_key(external_hints: List[str], file_path: str = "") -> str:
    """
    힌트 묶음 → artifact_key (범용 정규화)
    
    키 생성 규칙 (우선순위 순):
    1. 힌트 타입별 키 생성 (entity, service, queryId, configKey 등)
    2. Fallback: 힌트 조합으로 키 생성
    
    Args:
        external_hints: 힌트 목록 ["entity=OrderHeader", "service=createOrder"]
        file_path: 후보 파일 경로 (보조키용)
    
    Returns:
        정규화된 artifact_key
    """
    hints_dict = {}
    for hint in external_hints:
        if "=" in hint:
            key, value = hint.split("=", 1)
            # 값 정규화 (특수문자 제거, 소문자)
            value = value.strip().replace('"', '').replace("'", "")
            # 확장자 제거
            for ext in [".xml", ".java", ".kt", ".yaml", ".yml", ".properties"]:
                value = value.replace(ext, "")
            # 경로에서 마지막 부분만
            if '/' in value:
                value = value.split('/')[-1]
            hints_dict[key.lower()] = value.lower()
    
    # 힌트 타입별 키 생성 (범용 규칙)
    # 우선순위: 가장 구체적인 힌트부터
    
    # namespace + queryId 조합
    if "namespace" in hints_dict and "queryid" in hints_dict:
        return f"query:{hints_dict['namespace']}#{hints_dict['queryid']}"
    
    # queryId만
    if "queryid" in hints_dict:
        fallback_ns = Path(file_path).stem.lower() if file_path else "unknown"
        return f"query:{fallback_ns}#{hints_dict['queryid']}"
    
    # entity
    if "entity" in hints_dict:
        return f"entity:{hints_dict['entity']}"
    
    # service
    if "service" in hints_dict:
        return f"service:{hints_dict['service']}"
    
    # configKey
    if "configkey" in hints_dict:
        return f"config:{hints_dict['configkey']}"
    
    # jpa_entity
    if "jpa_entity" in hints_dict:
        return f"entity:{hints_dict['jpa_entity']}"
    
    # symbol (범용 식별자)
    if "symbol" in hints_dict:
        return f"symbol:{hints_dict['symbol']}"
    
    # Fallback: 힌트 조합
    if external_hints:
        sorted_hints = sorted(external_hints)
        combined = "__".join(h.replace("=", "_") for h in sorted_hints).lower()
        return f"generic:{combined}"
    
    return "generic:unknown"


def needs_namespace_lookup(artifact_key: str) -> bool:
    """
    임시키 여부 판별 (추가 정보 필요한 경우)
    
    범용 규칙:
    - "unknown"이 포함되면 임시키
    - query:unknown#... 형태면 임시키
    
    Args:
        artifact_key: artifact_key
    
    Returns:
        True면 추가 정보 확정 필요 (임시키)
    """
    if not artifact_key:
        return True
    
    # "unknown"이 포함되면 임시키
    if "unknown" in artifact_key.lower():
        return True
    
    # query:unknown#... 형태 체크
    if artifact_key.startswith("query:"):
        parts = artifact_key.split(":")
        if len(parts) >= 2:
            ns_query = parts[1]
            if "#" in ns_query:
                namespace = ns_query.split("#")[0]
                if namespace == "unknown":
                    return True
    
    return False


@dataclass
class CacheEntry:
    """
    캐시 엔트리 (2단계 키 시스템 지원)
    
    구조:
    - artifact_key: 1차 키 (정규화된 의미 키)
    - tags: 2차 보조 인덱스용 태그 목록
    - is_provisional: 임시키 여부 (namespace 미확정 등)
    - promoted_from: 승격 전 임시키 (추적용)
    
    태그 형식 (유사도 매칭에 사용):
    - token:order       (비즈니스 단어)
    - token:header      (비즈니스 단어)
    - table:order_header  (테이블명)
    - column:order_id     (컬럼명)
    """
    artifact_key: str
    data: Dict[str, Any]
    mtime: float = 0
    size: int = 0
    created_at: float = field(default_factory=time.time)
    source_file: str = ""
    
    # 2단계 키 시스템 지원
    tags: List[str] = field(default_factory=list)
    is_provisional: bool = False  # 임시키 여부 (query:unknown#... 등)
    promoted_from: str = ""  # 승격 전 임시키 (추적용)


class CacheLayer:
    """
    3계층 캐시 시스템 + 2단계 키 시스템
    
    계층 구조:
    1. 인메모리 캐시 (가장 빠름, 프로세스 재시작 시 소멸)
    2. 파일 캐시 (중단/재개 가능)
    3. (선택) 영속 인덱스 (SQLite/LMDB)
    
    2단계 키 시스템:
    - 1차 키: artifact_key (정규화된 의미 키) → exact lookup
    - 2차 키: tag_index (보조 인덱스) → fuzzy lookup
    
    조회 흐름:
    1. get() → 정확 키 조회 (exact)
    2. fuzzy_get() → 태그 기반 유사 조회 + 스코어링
    3. get_with_fallback() → exact → fuzzy 순차 시도
    """
    
    # 싱글턴 인스턴스 저장소 (경로별)
    _instances: Dict[str, "CacheLayer"] = {}
    
    @classmethod
    def get_instance(cls, cache_root: str) -> "CacheLayer":
        """
        싱글턴 패턴: 동일 경로면 같은 인스턴스 반환
        
        이렇게 하면 main_agent.py와 runtime_resolution_phase.py가
        같은 메모리 캐시를 공유하게 됩니다.
        
        Args:
            cache_root: 캐시 파일 루트 경로
        
        Returns:
            CacheLayer 인스턴스
        """
        normalized_path = str(Path(cache_root).resolve())
        
        if normalized_path not in cls._instances:
            cls._instances[normalized_path] = cls(cache_root)
        
        return cls._instances[normalized_path]
    
    @classmethod
    def clear_instances(cls):
        """모든 싱글턴 인스턴스 제거 (테스트용)"""
        cls._instances.clear()
    
    def __init__(self, cache_root: str):
        """
        Args:
            cache_root: 캐시 파일 루트 경로
            
        Note: 직접 생성보다 CacheLayer.get_instance(path)를 사용하세요.
        """
        self.cache_root = Path(cache_root)
        self.cache_root.mkdir(parents=True, exist_ok=True)
        
        # 인메모리 캐시 (1차 키 → CacheEntry)
        self._memory_cache: Dict[str, CacheEntry] = {}
        
        # 보조 인덱스 (2차 키: 태그 → artifact_key 집합)
        # 예: tag_index["table:order_header"] = {"mybatis:ordermapper#createorder", "mybatis:ordermapper#updateorder"}
        self._tag_index: Dict[str, set] = defaultdict(set)
        
        # 파일 캐시 디렉토리
        self.extract_cache_dir = self.cache_root / "extract"
        self.slice_cache_dir = self.cache_root / "slice"
        self.search_cache_dir = self.cache_root / "search"
        self.tag_index_file = self.cache_root / "tag_index.json"
        
        for d in [self.extract_cache_dir, self.slice_cache_dir, self.search_cache_dir]:
            d.mkdir(parents=True, exist_ok=True)
        
        # 파일에서 태그 인덱스 로드
        self._load_tag_index()
        
        # 파일 캐시에서 메모리 캐시로 프리로드 (Memory Hits 활성화!)
        self._preload_memory_cache()
    
    def _load_tag_index(self):
        """파일에서 태그 인덱스 로드"""
        if not self.tag_index_file.exists():
            return
        
        try:
            with open(self.tag_index_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            for tag, keys in data.items():
                self._tag_index[tag] = set(keys)
        except Exception:
            pass
    
    def _preload_memory_cache(self, max_entries: int = 500):
        """
        파일 캐시에서 메모리 캐시로 프리로드
        
        이 메서드는 __init__에서 자동 호출됩니다.
        메모리 캐시가 프리로드되면 첫 번째 조회부터 memory hit이 발생합니다.
        
        Args:
            max_entries: 최대 로드할 엔트리 수 (메모리 제한)
        """
        import logging
        logger = logging.getLogger("deepagents.cache")
        
        if not self.extract_cache_dir.exists():
            return
        
        loaded_count = 0
        try:
            # 최근 수정된 파일부터 로드 (가장 관련성 높은 캐시)
            cache_files = sorted(
                self.extract_cache_dir.glob("*.json"),
                key=lambda f: f.stat().st_mtime,
                reverse=True
            )[:max_entries]
            
            for cache_file in cache_files:
                try:
                    with open(cache_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    
                    artifact_key = data.get("artifact_key", "")
                    if not artifact_key:
                        continue
                    
                    entry = CacheEntry(
                        artifact_key=artifact_key,
                        data=data.get("data", {}),
                        mtime=data.get("mtime", 0),
                        size=data.get("size", 0),
                        created_at=data.get("created_at", 0),
                        source_file=data.get("source_file", ""),
                        tags=data.get("tags", []),
                        is_provisional=data.get("is_provisional", False),
                        promoted_from=data.get("promoted_from", "")
                    )
                    
                    self._memory_cache[artifact_key] = entry
                    loaded_count += 1
                    
                except Exception:
                    continue
            
            if loaded_count > 0:
                logger.info(f"🔄 CACHE PRELOAD: {loaded_count} entries loaded into memory")
        except Exception as e:
            logger.warning(f"⚠️ CACHE PRELOAD 실패: {str(e)}")
    
    def _save_tag_index(self):
        """태그 인덱스를 파일에 저장"""
        try:
            # set을 list로 변환
            data = {tag: list(keys) for tag, keys in self._tag_index.items()}
            
            with open(self.tag_index_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    
    def _generate_tags(self, artifact_key: str, data: Dict[str, Any], hints: List[str] = None) -> List[str]:
        """
        캐시 데이터에서 검색용 태그 생성 (범용 토큰 추출)
        
        태그 종류:
        - token:{word}: 의미있는 단어 토큰 (유사도 매칭용)
        - table:{name}: 테이블명
        - column:{name}: 컬럼명
        
        Args:
            artifact_key: 정규화된 artifact_key
            data: 캐시 데이터 (tables, columns 등)
            hints: 원본 힌트 리스트 (선택)
        
        Returns:
            태그 리스트
        """
        tags = set()
        
        # 1. artifact_key에서 토큰 추출 (범용)
        # entity:orderheader → ["entity", "order", "header", "orderheader"]
        skip_tokens = {"generic", "unknown", "query", "entity", "service", "config", "symbol"}
        for token in extract_tokens(artifact_key):
            if token not in skip_tokens:
                tags.add(f"token:{token}")
        
        # 2. 힌트에서 토큰 추출 (범용 - 모든 힌트 타입에 동일 처리)
        if hints:
            for hint in hints:
                if "=" in hint:
                    _, value = hint.split("=", 1)
                    # 값에서 토큰 추출
                    for token in extract_tokens(value):
                        tags.add(f"token:{token}")
        
        # 3. 테이블명 태그 + 토큰
        for table in data.get("tables", []):
            if isinstance(table, dict):
                table_name = table.get("name", "").strip()
                if table_name:
                    # 원본 테이블명 (소문자)
                    tags.add(f"table:{table_name.lower()}")
                    # 테이블명에서 토큰 추출
                    for token in extract_tokens(table_name):
                        tags.add(f"token:{token}")
        
        # 4. 컬럼명 태그
        for col in data.get("columns", []):
            if isinstance(col, dict):
                col_name = col.get("name", "").lower().strip()
                if col_name:
                    tags.add(f"column:{col_name}")
        
        # 테이블 내부 컬럼도 처리
        for table in data.get("tables", []):
            if isinstance(table, dict):
                for col in table.get("columns", []):
                    if isinstance(col, dict):
                        col_name = col.get("name", "").lower().strip()
                        if col_name:
                            tags.add(f"column:{col_name}")
                    elif isinstance(col, str):
                        tags.add(f"column:{col.lower()}")
        
        return list(tags)
    
    def _update_tag_index(self, artifact_key: str, tags: List[str], old_tags: List[str] = None):
        """
        태그 인덱스 갱신
        
        Args:
            artifact_key: artifact_key
            tags: 새 태그 리스트
            old_tags: 기존 태그 리스트 (제거용)
        """
        # 기존 태그에서 제거
        if old_tags:
            for tag in old_tags:
                if tag in self._tag_index:
                    self._tag_index[tag].discard(artifact_key)
                    # 빈 집합이면 키 삭제
                    if not self._tag_index[tag]:
                        del self._tag_index[tag]
        
        # 새 태그 추가
        for tag in tags:
            self._tag_index[tag].add(artifact_key)
        
        # 파일에 저장
        self._save_tag_index()
    
    def _key_to_filename(self, key: str) -> str:
        """키를 파일명으로 변환 (안전한 문자만)"""
        # 해시로 변환
        hash_val = hashlib.md5(key.encode()).hexdigest()[:16]
        # 키의 앞부분 + 해시
        safe_prefix = "".join(c if c.isalnum() or c in "_-" else "_" for c in key[:30])
        return f"{safe_prefix}_{hash_val}.json"
    
    def is_cache_valid(self, cached_entry: CacheEntry, file_path: str) -> bool:
        """
        캐시 무효화 검증 (mtime + size)
        
        Args:
            cached_entry: 캐시 엔트리
            file_path: 원본 파일 경로
        
        Returns:
            True면 캐시 유효
        """
        if not file_path:
            return True  # 파일 경로 없으면 유효로 간주
        
        try:
            stat = os.stat(file_path)
            return (
                cached_entry.mtime == stat.st_mtime and
                cached_entry.size == stat.st_size
            )
        except FileNotFoundError:
            return False
        except Exception:
            return False
    
    # =========================================================================
    # 인메모리 캐시
    # =========================================================================
    
    def memory_get(self, artifact_key: str) -> Optional[CacheEntry]:
        """인메모리 캐시 조회"""
        return self._memory_cache.get(artifact_key)
    
    def memory_set(self, artifact_key: str, entry: CacheEntry):
        """인메모리 캐시 저장"""
        self._memory_cache[artifact_key] = entry
    
    def memory_has(self, artifact_key: str) -> bool:
        """인메모리 캐시 존재 여부"""
        return artifact_key in self._memory_cache
    
    # =========================================================================
    # 파일 캐시 (extract)
    # =========================================================================
    
    def file_get_extract(self, artifact_key: str) -> Optional[CacheEntry]:
        """파일 캐시에서 추출 결과 조회"""
        filename = self._key_to_filename(artifact_key)
        filepath = self.extract_cache_dir / filename
        
        if not filepath.exists():
            return None
        
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            return CacheEntry(
                artifact_key=artifact_key,
                data=data.get("data", {}),
                mtime=data.get("mtime", 0),
                size=data.get("size", 0),
                created_at=data.get("created_at", 0),
                source_file=data.get("source_file", ""),
                tags=data.get("tags", []),
                is_provisional=data.get("is_provisional", False),
                promoted_from=data.get("promoted_from", "")
            )
        except Exception:
            return None
    
    def file_set_extract(self, artifact_key: str, entry: CacheEntry):
        """파일 캐시에 추출 결과 저장"""
        filename = self._key_to_filename(artifact_key)
        filepath = self.extract_cache_dir / filename
        
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump({
                    "artifact_key": artifact_key,
                    "data": entry.data,
                    "mtime": entry.mtime,
                    "size": entry.size,
                    "created_at": entry.created_at,
                    "source_file": entry.source_file,
                    "tags": entry.tags,
                    "is_provisional": entry.is_provisional,
                    "promoted_from": entry.promoted_from
                }, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    
    # =========================================================================
    # 파일 캐시 (slice)
    # =========================================================================
    
    def file_get_slice(self, file_path: str, byte_offset: int, limit: int) -> Optional[Dict[str, Any]]:
        """파일 캐시에서 슬라이스 결과 조회"""
        key = f"{file_path}:{byte_offset}:{limit}"
        filename = self._key_to_filename(key)
        filepath = self.slice_cache_dir / filename
        
        if not filepath.exists():
            return None
        
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return None
    
    def file_set_slice(self, file_path: str, byte_offset: int, limit: int, data: Dict[str, Any]):
        """파일 캐시에 슬라이스 결과 저장"""
        key = f"{file_path}:{byte_offset}:{limit}"
        filename = self._key_to_filename(key)
        filepath = self.slice_cache_dir / filename
        
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    
    # =========================================================================
    # 파일 캐시 (search)
    # =========================================================================
    
    def file_get_search(self, keyword: str, hint_type: str, scope: str) -> Optional[List[Dict[str, Any]]]:
        """파일 캐시에서 검색 결과 조회"""
        key = f"search:{keyword}:{hint_type}:{scope}"
        filename = self._key_to_filename(key)
        filepath = self.search_cache_dir / filename
        
        if not filepath.exists():
            return None
        
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return None
    
    def file_set_search(self, keyword: str, hint_type: str, scope: str, results: List[Dict[str, Any]]):
        """파일 캐시에 검색 결과 저장"""
        key = f"search:{keyword}:{hint_type}:{scope}"
        filename = self._key_to_filename(key)
        filepath = self.search_cache_dir / filename
        
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    
    # =========================================================================
    # 통합 캐시 조회 (3계층)
    # =========================================================================
    
    def get(
        self,
        artifact_key: str,
        source_file: str = ""
    ) -> Tuple[Optional[CacheEntry], str]:
        """
        3계층 캐시 조회
        
        Args:
            artifact_key: artifact_key
            source_file: 원본 파일 경로 (무효화 검증용)
        
        Returns:
            (CacheEntry or None, hit_source: "memory" | "file" | "miss")
        """
        # 1. 인메모리 캐시
        entry = self.memory_get(artifact_key)
        if entry:
            if self.is_cache_valid(entry, source_file):
                return entry, "memory"
            # 무효화된 캐시 제거
            del self._memory_cache[artifact_key]
        
        # 2. 파일 캐시
        entry = self.file_get_extract(artifact_key)
        if entry:
            if self.is_cache_valid(entry, source_file):
                # 인메모리 캐시에도 저장 (프로모션)
                self.memory_set(artifact_key, entry)
                return entry, "file"
        
        return None, "miss"
    
    def _calculate_cache_score(self, data: Dict[str, Any]) -> float:
        """
        캐시 점수 계산 (confidence + richness)
        
        플랜 규칙:
        - artifact_confidence = max(table.confidence) (tables가 없으면 0.5)
        - richness = tables 개수 * 1 + columns 개수 * 0.2 + fks 개수 * 0.5
        - score = confidence * 100 + richness
        
        Args:
            data: 캐시 데이터
        
        Returns:
            점수 (높을수록 좋음)
        """
        # artifact confidence 계산
        tables = data.get("tables", [])
        if tables:
            confidences = [t.get("confidence", 0.5) for t in tables if isinstance(t, dict)]
            confidence = max(confidences) if confidences else 0.5
        else:
            confidence = data.get("confidence", 0.5)
        
        # richness 계산
        richness = (
            len(tables) * 1 +
            len(data.get("columns", [])) * 0.2 +
            len(data.get("fks", [])) * 0.5
        )
        
        return confidence * 100 + richness
    
    def set(
        self,
        artifact_key: str,
        data: Dict[str, Any],
        source_file: str = "",
        hints: List[str] = None
    ):
        """
        캐시 저장 (인메모리 + 파일 + 태그 인덱스) - confidence+richness 기반 교체
        
        플랜 규칙:
        - 기존 캐시와 점수 비교 후 더 좋은 것만 저장
        - data에 confidence, artifact_meta, tables 3개 필드 포함 확인
        - 태그 생성 및 tag_index 갱신
        - 임시키(unknown) 여부 자동 판별
        
        Args:
            artifact_key: artifact_key
            data: 저장할 데이터 (confidence, artifact_meta, tables 포함)
            source_file: 원본 파일 경로 (mtime/size 저장용)
            hints: 원본 힌트 리스트 (태그 생성용)
        """
        # artifact confidence 계산 및 설정 (없으면 max(table.confidence))
        if "confidence" not in data:
            tables = data.get("tables", [])
            if tables:
                confidences = [t.get("confidence", 0.5) for t in tables if isinstance(t, dict)]
                data["confidence"] = max(confidences) if confidences else 0.5
            else:
                data["confidence"] = 0.5
        
        # artifact_meta 기본값 설정
        if "artifact_meta" not in data:
            data["artifact_meta"] = {}
        
        # tables 기본값 설정
        if "tables" not in data:
            data["tables"] = []
        
        # 기존 캐시 조회 (인메모리 또는 파일)
        old_entry = self.memory_get(artifact_key)
        if old_entry is None:
            old_entry = self.file_get_extract(artifact_key)
        
        # 점수 비교: 기존이 더 좋으면 저장하지 않음
        if old_entry is not None:
            old_score = self._calculate_cache_score(old_entry.data)
            new_score = self._calculate_cache_score(data)
            
            if old_score >= new_score:
                # 기존 캐시가 더 좋거나 같음 → 유지 (덮어쓰기 안 함)
                return
        
        # 새 캐시가 더 좋음 → 저장
        mtime = 0
        size = 0
        
        if source_file:
            try:
                stat = os.stat(source_file)
                mtime = stat.st_mtime
                size = stat.st_size
            except Exception:
                pass
        
        # 태그 생성
        tags = self._generate_tags(artifact_key, data, hints)
        
        # 임시키 여부 판별 (mybatis:unknown#... 형태)
        is_provisional = needs_namespace_lookup(artifact_key)
        
        entry = CacheEntry(
            artifact_key=artifact_key,
            data=data,
            mtime=mtime,
            size=size,
            source_file=source_file,
            tags=tags,
            is_provisional=is_provisional
        )
        
        # 태그 인덱스 갱신
        old_tags = old_entry.tags if old_entry else None
        self._update_tag_index(artifact_key, tags, old_tags)
        
        # 인메모리 캐시
        self.memory_set(artifact_key, entry)
        
        # 파일 캐시
        self.file_set_extract(artifact_key, entry)
    
    # =========================================================================
    # 유사도 기반 Fuzzy Lookup (다중 후보 반환)
    # =========================================================================
    
    def fuzzy_get(
        self,
        hints: List[str],
        missing_bindings: List[str] = None,
        search_tokens: List[str] = None,
        min_similarity: float = 0.5,
        max_candidates: int = 5
    ) -> Tuple[Optional[CacheEntry], float, str]:
        """
        유사도 기반 퍼지 조회 (단일 최고 후보 반환 - 하위 호환)
        
        다중 후보가 필요하면 fuzzy_get_candidates() 사용
        
        Args:
            hints: 외부 힌트 리스트
            missing_bindings: 미해결 바인딩 리스트
            search_tokens: 검색용 토큰 (analysis에서 추출된 비즈니스 단어)
            min_similarity: 최소 유사도 (0.0~1.0)
            max_candidates: 최대 후보 수
        
        Returns:
            (CacheEntry or None, score, match_reason)
        """
        candidates = self.fuzzy_get_candidates(
            hints=hints,
            missing_bindings=missing_bindings,
            search_tokens=search_tokens,
            min_similarity=min_similarity,
            max_candidates=max_candidates
        )
        
        if not candidates:
            return None, 0, "no_candidates"
        
        # 최고 점수 후보 반환
        best = candidates[0]
        return best["entry"], best["score"], best["reason"]
    
    def fuzzy_get_candidates(
        self,
        hints: List[str],
        missing_bindings: List[str] = None,
        search_tokens: List[str] = None,
        min_similarity: float = 0.5,
        max_candidates: int = 5
    ) -> List[Dict[str, Any]]:
        """
        유사도 기반 퍼지 조회 - 다중 후보 반환
        
        동작 방식:
        1. hints, missing_bindings, search_tokens에서 검색 토큰 추출
        2. 모든 캐시 엔트리와 토큰 유사도 비교
        3. 유사도가 min_similarity 이상인 후보들 반환
        
        유사도 점수:
        - 토큰 일치: 1.0
        - 포함 관계 (order ⊂ orderheader): 0.7~0.85
        - 문자열 유사도: SequenceMatcher ratio
        
        Args:
            hints: 외부 힌트 리스트
            missing_bindings: 미해결 바인딩 리스트
            search_tokens: 검색용 토큰 (비즈니스 단어)
            min_similarity: 최소 유사도
            max_candidates: 최대 후보 수
        
        Returns:
            후보 리스트 [{"entry": CacheEntry, "score": float, "reason": str, "matches": [...]}]
            점수 내림차순 정렬
        """
        # 1. 검색 토큰 추출 (범용)
        search_words = set()
        
        # hints에서 토큰 추출
        for hint in (hints or []):
            if "=" in hint:
                _, value = hint.split("=", 1)
                search_words.update(extract_tokens(value))
        
        # missing_bindings에서 토큰 추출
        for binding in (missing_bindings or []):
            search_words.update(extract_tokens(binding))
        
        # search_tokens 추가 (analysis에서 추출된 비즈니스 단어)
        for token in (search_tokens or []):
            if len(token) >= 2:
                search_words.add(token.lower())
        
        if not search_words:
            return []
        
        # 2. 모든 캐시 엔트리와 유사도 비교
        candidates = []
        
        # 메모리 캐시 + 파일 캐시 모두 검색
        all_entries = {}
        
        # 메모리 캐시
        for key, entry in self._memory_cache.items():
            all_entries[key] = entry
        
        # 파일 캐시에서 추가 로드 (tag_index에 있는 키들)
        for tag_keys in self._tag_index.values():
            for key in tag_keys:
                if key not in all_entries:
                    entry = self.file_get_extract(key)
                    if entry:
                        all_entries[key] = entry
        
        for artifact_key, entry in all_entries.items():
            # 캐시 엔트리의 토큰 추출
            cache_tokens = set()
            for tag in entry.tags:
                if tag.startswith("token:"):
                    cache_tokens.add(tag[6:])  # "token:" 제거
                elif tag.startswith("table:"):
                    # 테이블명도 토큰으로 사용
                    cache_tokens.update(extract_tokens(tag[6:]))
            
            if not cache_tokens:
                continue
            
            # 각 검색 단어 vs 캐시 토큰 유사도 계산
            match_details = []
            total_similarity = 0.0
            match_count = 0
            
            for search_word in search_words:
                best_sim = 0.0
                best_match = ""
                
                for cache_token in cache_tokens:
                    sim = calc_similarity(search_word, cache_token)
                    if sim > best_sim:
                        best_sim = sim
                        best_match = cache_token
                
                if best_sim >= min_similarity:
                    total_similarity += best_sim
                    match_count += 1
                    match_details.append(f"{search_word}~{best_match}({best_sim:.2f})")
            
            if match_count == 0:
                continue
            
            # 점수 계산
            # - 평균 유사도 × 60
            # - 매칭된 단어 수 × 15
            # - confidence × 10
            # - 테이블 수 × 5
            avg_sim = total_similarity / match_count
            score = avg_sim * 60
            score += match_count * 15
            score += entry.data.get("confidence", 0.5) * 10
            score += len(entry.data.get("tables", [])) * 5
            
            # 임시키 감점
            if entry.is_provisional:
                score *= 0.8
            
            candidates.append({
                "entry": entry,
                "artifact_key": artifact_key,
                "score": score,
                "avg_similarity": avg_sim,
                "match_count": match_count,
                "reason": f"sim={avg_sim:.2f},matches={match_details[:3]}",
                "matches": match_details
            })
        
        # 3. 점수 내림차순 정렬 및 상위 N개 반환
        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates[:max_candidates]
    
    def get_with_fallback(
        self,
        hints: List[str],
        missing_bindings: List[str] = None,
        search_tokens: List[str] = None,
        source_file: str = ""
    ) -> Tuple[Optional[CacheEntry], str, float]:
        """
        2단계 조회: exact → fuzzy (유사도 기반) 순차 시도
        
        LLM 도구에서 호출하는 메인 메서드.
        hints를 받아서 정규화된 키로 exact lookup,
        없으면 유사도 기반 fuzzy lookup 시도.
        
        Args:
            hints: 외부 힌트 리스트
            missing_bindings: 미해결 바인딩 리스트
            search_tokens: 검색용 토큰 (비즈니스 단어)
            source_file: 원본 파일 경로 (캐시 무효화 검증용)
        
        Returns:
            (CacheEntry or None, match_type: "exact"|"fuzzy"|"miss", score)
        """
        import logging
        logger = logging.getLogger("deepagents.cache")
        
        # 힌트에서 "목적 타입" 추론
        purpose = self._infer_purpose_from_hints(hints)
        
        # 1. 정확 키 생성 및 exact lookup
        exact_key = hints_to_artifact_key(hints)
        entry, hit_source = self.get(exact_key, source_file)
        
        if entry:
            # 🚨 목적에 맞는 데이터가 있는지 검증
            is_valid, reason = self._is_valid_for_purpose(entry, purpose)
            if is_valid:
                return entry, f"exact:{hit_source}", 100.0
            # 목적에 맞지 않으면 로그 남기고 fuzzy로 진행
            logger.info(f"❌ CACHE_GET MISS (목적 불일치): {exact_key} → {reason}")
        
        # 2. 유사도 기반 Fuzzy lookup
        entry, score, reason = self.fuzzy_get(
            hints=hints,
            missing_bindings=missing_bindings,
            search_tokens=search_tokens
        )
        
        if entry:
            # 🚨 목적에 맞는 데이터가 있는지 검증
            is_valid, validation_reason = self._is_valid_for_purpose(entry, purpose)
            if is_valid:
                return entry, f"fuzzy:{reason}", score
            # 목적에 맞지 않으면 로그 남기고 miss
            logger.info(f"❌ CACHE_GET MISS (목적 불일치): {entry.key} → {validation_reason}")
            return None, "miss", 0
        
        return None, "miss", 0
    
    def _infer_purpose_from_hints(self, hints: List[str]) -> str:
        """
        힌트에서 "찾으려는 목적" 추론 (범용적)
        
        Returns:
            "table" - 테이블/엔티티/SQL 정보 찾기
            "config" - 설정값 찾기
            "service" - 서비스 정보 찾기
            "any" - 어떤 정보든 OK
        """
        for hint in (hints or []):
            hint_lower = hint.lower()
            
            # 테이블/엔티티/SQL 목적
            if any(k in hint_lower for k in ["entity=", "table=", "queryid=", "namespace=", "db_kind=", "sql"]):
                return "table"
            
            # 설정값 목적
            if any(k in hint_lower for k in ["configkey=", "config=", ".yaml", ".yml", ".properties"]):
                return "config"
            
            # 서비스 목적
            if "service=" in hint_lower and "orderservice" not in hint_lower:
                # service=createOrderHeader 같은 경우 (orderService.xxx는 제외)
                return "service"
        
        # 기본: 어떤 정보든 OK
        return "any"
    
    def _is_valid_for_purpose(self, entry: CacheEntry, purpose: str) -> Tuple[bool, str]:
        """
        목적에 맞는 데이터가 있는지 검증
        
        Args:
            entry: 캐시 엔트리
            purpose: "table", "config", "service", "any"
        
        Returns:
            (is_valid, reason) 튜플
            - is_valid: True면 유효, False면 무효
            - reason: 무효한 경우 이유 설명
        """
        data = entry.data
        
        if purpose == "table":
            # 테이블 목적: tables가 있어야 유효
            tables = data.get("tables", [])
            if len(tables) > 0:
                return True, ""
            return False, f"테이블 목적인데 tables=[] (비어있음)"
        
        elif purpose == "config":
            # 설정값 목적: config 정보가 있어야 유효
            config_info = data.get("config", {})
            artifact_meta = data.get("artifact_meta", {})
            has_config = bool(config_info.get("key") or config_info.get("value") or artifact_meta.get("key"))
            if has_config:
                return True, ""
            return False, f"설정값 목적인데 config 정보 없음 (config={config_info}, artifact_meta={artifact_meta})"
        
        elif purpose == "service":
            # 서비스 목적: services가 있거나 tables가 있어야 유효
            services = data.get("services", [])
            tables = data.get("tables", [])
            if len(services) > 0 or len(tables) > 0:
                return True, ""
            return False, f"서비스 목적인데 services=[] 및 tables=[] (둘 다 비어있음)"
        
        else:  # "any"
            # 범용: 어떤 유의미한 정보든 있으면 유효
            is_valid = self._is_valid_cache_entry(entry)
            if is_valid:
                return True, ""
            return False, "범용 목적인데 유의미한 데이터 없음"
    
    def _is_valid_cache_entry(self, entry: CacheEntry) -> bool:
        """
        캐시가 유효한지 검증 (범용적 - 프레임워크 무관)
        
        검증 기준:
        1. tables가 있으면 유효 (테이블 정보 보유)
        2. config 필드가 있으면 유효 (설정값 정보 보유)
        3. services 필드가 있으면 유효 (서비스 정보 보유)
        4. artifact_meta에 유의미한 정보가 있으면 유효
        5. 위 모두 없으면 무효 (빈 캐시)
        
        핵심: 어떤 형태로든 유의미한 정보가 있으면 유효!
        """
        data = entry.data
        
        # 1. tables가 있으면 유효
        tables = data.get("tables", [])
        if tables and len(tables) > 0:
            return True
        
        # 2. config 정보가 있으면 유효
        config_info = data.get("config", {})
        if config_info and (config_info.get("key") or config_info.get("value")):
            return True
        
        # 3. services 정보가 있으면 유효
        services = data.get("services", [])
        if services and len(services) > 0:
            return True
        
        # 4. artifact_meta에 유의미한 정보가 있으면 유효
        artifact_meta = data.get("artifact_meta", {})
        if artifact_meta:
            # file, key, name 등 유의미한 필드가 있으면 유효
            meaningful_keys = ["file", "key", "name", "value", "path", "source"]
            if any(artifact_meta.get(k) for k in meaningful_keys):
                return True
        
        # 5. 위 모두 없으면 무효 (빈 캐시)
        return False
    
    def get_candidates(
        self,
        hints: List[str],
        missing_bindings: List[str] = None,
        search_tokens: List[str] = None,
        source_file: str = "",
        max_candidates: int = 5
    ) -> List[Dict[str, Any]]:
        """
        다중 캐시 후보 반환 (유사도 기반)
        
        단일 후보가 아닌, 유사도가 높은 여러 후보를 반환합니다.
        하나의 검색 결과가 여러 테이블과 관련될 수 있을 때 유용합니다.
        
        Args:
            hints: 외부 힌트 리스트
            missing_bindings: 미해결 바인딩 리스트
            search_tokens: 검색용 토큰
            source_file: 원본 파일 경로
            max_candidates: 최대 후보 수
        
        Returns:
            후보 리스트 (점수 내림차순)
        """
        candidates = []
        
        # 1. 정확 키 체크 (있으면 최우선)
        exact_key = hints_to_artifact_key(hints)
        entry, hit_source = self.get(exact_key, source_file)
        
        if entry:
            candidates.append({
                "entry": entry,
                "artifact_key": exact_key,
                "score": 100.0,
                "match_type": f"exact:{hit_source}",
                "reason": "exact_match"
            })
        
        # 2. 유사도 기반 후보 추가
        fuzzy_candidates = self.fuzzy_get_candidates(
            hints=hints,
            missing_bindings=missing_bindings,
            search_tokens=search_tokens,
            max_candidates=max_candidates
        )
        
        # exact 후보와 중복 제거 후 추가
        for fc in fuzzy_candidates:
            if fc["artifact_key"] != exact_key:
                fc["match_type"] = "fuzzy"
                candidates.append(fc)
        
        # 상위 N개만
        return candidates[:max_candidates]
    
    def promote_key(self, provisional_key: str, confirmed_key: str) -> bool:
        """
        임시키를 확정키로 승격
        
        추가 정보가 확정되었을 때 사용:
        query:unknown#createorder → query:ordermapper#createorder
        entity:unknown → entity:orderheader
        
        동작:
        1. 임시키 캐시 엔트리 로드
        2. 확정키로 새 엔트리 생성
        3. 태그 인덱스 갱신
        4. 임시키 엔트리 삭제 (선택)
        
        Args:
            provisional_key: 임시키 (예: query:unknown#createorder)
            confirmed_key: 확정키 (예: query:ordermapper#createorder)
        
        Returns:
            성공 여부
        """
        # 임시키 엔트리 로드
        entry = self.memory_get(provisional_key)
        if entry is None:
            entry = self.file_get_extract(provisional_key)
        
        if entry is None:
            return False
        
        # 확정키로 새 엔트리 생성
        new_tags = self._generate_tags(confirmed_key, entry.data)
        
        new_entry = CacheEntry(
            artifact_key=confirmed_key,
            data=entry.data,
            mtime=entry.mtime,
            size=entry.size,
            source_file=entry.source_file,
            tags=new_tags,
            is_provisional=False,
            promoted_from=provisional_key
        )
        
        # 태그 인덱스에서 임시키 제거, 확정키 추가
        for tag in entry.tags:
            if tag in self._tag_index:
                self._tag_index[tag].discard(provisional_key)
        
        for tag in new_tags:
            self._tag_index[tag].add(confirmed_key)
        
        self._save_tag_index()
        
        # 인메모리 캐시
        self.memory_set(confirmed_key, new_entry)
        
        # 파일 캐시
        self.file_set_extract(confirmed_key, new_entry)
        
        # 임시키 삭제
        if provisional_key in self._memory_cache:
            del self._memory_cache[provisional_key]
        
        # 파일 캐시에서도 삭제
        provisional_file = self.extract_cache_dir / self._key_to_filename(provisional_key)
        if provisional_file.exists():
            try:
                provisional_file.unlink()
            except Exception:
                pass
        
        return True
    
    def get_provisional_entries(self) -> List[CacheEntry]:
        """임시키 엔트리 목록 조회 (승격 대상)"""
        provisionals = []
        
        for entry in self._memory_cache.values():
            if entry.is_provisional:
                provisionals.append(entry)
        
        return provisionals
    
    def clear_memory(self):
        """인메모리 캐시 전체 삭제"""
        self._memory_cache.clear()
        self._tag_index.clear()
    
    def clear_all(self):
        """모든 캐시 삭제"""
        self.clear_memory()
        
        # 파일 캐시 삭제
        import shutil
        for d in [self.extract_cache_dir, self.slice_cache_dir, self.search_cache_dir]:
            if d.exists():
                shutil.rmtree(d)
                d.mkdir(parents=True, exist_ok=True)
        
        # 태그 인덱스 파일 삭제
        if self.tag_index_file.exists():
            try:
                self.tag_index_file.unlink()
            except Exception:
                pass


# =============================================================================
# [DELETED] DescriptionMemory - 미사용 dead code 삭제됨
# description 업데이트는 neo4j_tools.py의 enrich_table_schema 내 ' | ' 연결로 처리
# =============================================================================

