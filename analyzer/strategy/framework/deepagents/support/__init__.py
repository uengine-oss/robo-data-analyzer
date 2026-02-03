"""
지원 기능 모듈

지원 기능 관련 모듈:
- cache_layer: 3계층 캐시 (인메모리/파일/영속)
- minimal_lock: 동시성 제어 (table/element 단위)
- runtime_policy: 정책 및 제한 (30KB, 10회 상한)
"""

from .cache_layer import (
    CacheLayer,
    CacheEntry,
    hints_to_artifact_key,
    needs_namespace_lookup,
)

from .minimal_lock import (
    MinimalLock,
    GLOBAL_LOCK,
    acquire_table_lock,
    release_table_lock,
    acquire_element_lock,
    release_element_lock,
)

from .runtime_policy import (
    RuntimePolicy,
    PolicyLimits,
    POLICY,
    validate_slice_limit,
    should_review,
    should_apply_writes,
)

__all__ = [
    # cache_layer
    "CacheLayer",
    "CacheEntry",
    "hints_to_artifact_key",
    "needs_namespace_lookup",
    # minimal_lock
    "MinimalLock",
    "GLOBAL_LOCK",
    "acquire_table_lock",
    "release_table_lock",
    "acquire_element_lock",
    "release_element_lock",
    # runtime_policy
    "RuntimePolicy",
    "PolicyLimits",
    "POLICY",
    "validate_slice_limit",
    "should_review",
    "should_apply_writes",
]

