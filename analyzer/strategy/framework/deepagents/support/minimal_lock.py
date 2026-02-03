"""
Minimal Lock - 최소 동시성 락

플랜 규칙:
- 병렬 청크 처리 시 충돌 방지
- table_name 단위 lock 또는 elementId 단위 lock
- "완전 삭제"는 레이스로 그래프 상태 꼬일 확률이 높다

사용 예시:
    lock = MinimalLock()
    
    async with lock.acquire("ORDER_HEADER"):
        # TABLE 관련 작업
        await neo4j.apply_graph_mutations(mutations)
"""

import asyncio
from typing import Dict, Optional, Set
from contextlib import asynccontextmanager
import time


class MinimalLock:
    """
    최소 동시성 락
    
    - elementId 단위 lock
    - table_name 단위 lock
    - 타임아웃 지원
    """
    
    def __init__(self, default_timeout: float = 30.0):
        """
        Args:
            default_timeout: 기본 타임아웃 (초)
        """
        self._locks: Dict[str, asyncio.Lock] = {}
        self._lock_owners: Dict[str, str] = {}  # key -> owner_id
        self._lock_times: Dict[str, float] = {}  # key -> acquire_time
        self.default_timeout = default_timeout
        self._meta_lock = asyncio.Lock()  # 락 딕셔너리 보호용
    
    async def _get_or_create_lock(self, key: str) -> asyncio.Lock:
        """키에 대한 락 가져오기 (없으면 생성)"""
        async with self._meta_lock:
            if key not in self._locks:
                self._locks[key] = asyncio.Lock()
            return self._locks[key]
    
    async def acquire(
        self,
        key: str,
        owner_id: str = "",
        timeout: Optional[float] = None
    ) -> bool:
        """
        락 획득
        
        Args:
            key: 락 키 (table_name 또는 elementId)
            owner_id: 소유자 ID (디버깅용)
            timeout: 타임아웃 (초)
        
        Returns:
            True if 획득 성공, False if 타임아웃
        """
        lock = await self._get_or_create_lock(key)
        timeout = timeout or self.default_timeout
        
        try:
            # 타임아웃으로 락 획득 시도
            await asyncio.wait_for(lock.acquire(), timeout=timeout)
            
            # 소유자 정보 기록
            self._lock_owners[key] = owner_id
            self._lock_times[key] = time.time()
            
            return True
        except asyncio.TimeoutError:
            return False
    
    def release(self, key: str):
        """
        락 해제
        
        Args:
            key: 락 키
        """
        if key in self._locks:
            lock = self._locks[key]
            if lock.locked():
                lock.release()
            
            # 소유자 정보 삭제
            self._lock_owners.pop(key, None)
            self._lock_times.pop(key, None)
    
    def is_locked(self, key: str) -> bool:
        """
        락 상태 확인
        
        Args:
            key: 락 키
        
        Returns:
            True if 락이 걸려있음
        """
        if key not in self._locks:
            return False
        return self._locks[key].locked()
    
    def get_lock_info(self, key: str) -> Optional[Dict]:
        """
        락 정보 조회
        
        Args:
            key: 락 키
        
        Returns:
            {"owner": str, "acquired_at": float, "duration": float} or None
        """
        if not self.is_locked(key):
            return None
        
        acquired_at = self._lock_times.get(key, 0)
        return {
            "owner": self._lock_owners.get(key, ""),
            "acquired_at": acquired_at,
            "duration": time.time() - acquired_at if acquired_at else 0,
        }
    
    def get_all_locked_keys(self) -> Set[str]:
        """모든 락이 걸린 키 반환"""
        return {key for key, lock in self._locks.items() if lock.locked()}
    
    @asynccontextmanager
    async def lock(self, key: str, owner_id: str = "", timeout: Optional[float] = None):
        """
        컨텍스트 매니저로 락 사용
        
        Usage:
            async with lock.lock("ORDER_HEADER"):
                # 작업 수행
        """
        acquired = await self.acquire(key, owner_id, timeout)
        if not acquired:
            raise asyncio.TimeoutError(f"Failed to acquire lock for key: {key}")
        
        try:
            yield
        finally:
            self.release(key)
    
    async def acquire_multiple(
        self,
        keys: list,
        owner_id: str = "",
        timeout: Optional[float] = None
    ) -> bool:
        """
        여러 키에 대해 락 획득 (원자적)
        
        모든 락을 획득하거나, 하나라도 실패하면 모두 해제
        
        Args:
            keys: 락 키 목록
            owner_id: 소유자 ID
            timeout: 타임아웃 (초)
        
        Returns:
            True if 모든 락 획득 성공
        """
        acquired_keys = []
        
        try:
            for key in keys:
                if await self.acquire(key, owner_id, timeout):
                    acquired_keys.append(key)
                else:
                    # 하나라도 실패하면 롤백
                    for acquired in acquired_keys:
                        self.release(acquired)
                    return False
            return True
        except Exception:
            # 예외 발생 시 롤백
            for acquired in acquired_keys:
                self.release(acquired)
            raise
    
    def release_multiple(self, keys: list):
        """
        여러 키에 대해 락 해제
        
        Args:
            keys: 락 키 목록
        """
        for key in keys:
            self.release(key)
    
    @asynccontextmanager
    async def lock_multiple(self, keys: list, owner_id: str = "", timeout: Optional[float] = None):
        """
        여러 키에 대해 컨텍스트 매니저로 락 사용
        
        Usage:
            async with lock.lock_multiple(["ORDER_HEADER", "ORDER_ITEM"]):
                # 작업 수행
        """
        acquired = await self.acquire_multiple(keys, owner_id, timeout)
        if not acquired:
            raise asyncio.TimeoutError(f"Failed to acquire locks for keys: {keys}")
        
        try:
            yield
        finally:
            self.release_multiple(keys)


# 전역 락 인스턴스 (편의용)
GLOBAL_LOCK = MinimalLock()


async def acquire_table_lock(table_name: str, owner_id: str = "", timeout: float = 30.0) -> bool:
    """전역 락으로 테이블 락 획득"""
    return await GLOBAL_LOCK.acquire(f"table:{table_name.upper()}", owner_id, timeout)


def release_table_lock(table_name: str):
    """전역 락으로 테이블 락 해제"""
    GLOBAL_LOCK.release(f"table:{table_name.upper()}")


async def acquire_element_lock(element_id: str, owner_id: str = "", timeout: float = 30.0) -> bool:
    """전역 락으로 엘리먼트 락 획득"""
    return await GLOBAL_LOCK.acquire(f"element:{element_id}", owner_id, timeout)


def release_element_lock(element_id: str):
    """전역 락으로 엘리먼트 락 해제"""
    GLOBAL_LOCK.release(f"element:{element_id}")

