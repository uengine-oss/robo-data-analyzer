"""LLM 출력 스키마 교정기 — RuleLoader.execute() 직후 단 한 곳에서 호출.

흔한 LLM 흔들림을 자동 교정한 뒤 Pydantic 검증.
교정해도 안 되면 ValidationError 를 던져 기존 retry 루프가 잡도록.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, get_args, get_origin

from pydantic import BaseModel, ValidationError

from util.llm_output_schemas import RULE_SCHEMAS

logger = logging.getLogger(__name__)


def coerce_to_schema(rule_name: str, raw: Any) -> Dict[str, Any]:
    """LLM raw dict 를 스키마에 맞게 교정 후 반환.

    - 스키마 미등록 룰 → 그대로 통과 (점진적 적용).
    - 교정 불가 → ValidationError 전파 → retry 루프가 LLM 에 피드백.
    """
    if not isinstance(raw, dict):
        return raw

    schema_cls = RULE_SCHEMAS.get(rule_name)
    if schema_cls is None:
        return raw

    normalized = _normalize_dict(raw, schema_cls)
    model = schema_cls.model_validate(normalized)
    return model.model_dump(by_alias=True)


# ═══════════════════════════════════════════════════════════════════════════
# 재귀 교정 — 모델 필드 타입에 맞게 흔들림 흡수
# ═══════════════════════════════════════════════════════════════════════════

def _normalize_dict(data: dict, model_cls: type[BaseModel]) -> dict:
    """data 의 각 필드를 model_cls 의 필드 타입에 맞게 교정."""
    result: dict = {}
    fields = model_cls.model_fields

    for key, field_info in fields.items():
        alias = field_info.alias or key
        raw_val = data.get(alias, data.get(key))
        if raw_val is None:
            continue
        result[alias] = _coerce_value(raw_val, field_info.annotation, rule_field=alias)

    for key, val in data.items():
        if key not in result and key not in fields:
            result[key] = val

    return result


def _coerce_value(val: Any, annotation: Any, *, rule_field: str = "") -> Any:
    """annotation 에 맞게 val 을 교정."""
    origin = get_origin(annotation)
    args = get_args(annotation)

    # Optional[X] → X 로 벗기기
    if origin is type(None):
        return val
    if _is_optional(annotation):
        inner = _unwrap_optional(annotation)
        if val is None:
            return None
        return _coerce_value(val, inner, rule_field=rule_field)

    # List[X]
    if origin is list:
        item_type = args[0] if args else Any
        return _coerce_list(val, item_type, rule_field=rule_field)

    # Dict[str, X]
    if origin is dict:
        return val if isinstance(val, dict) else {}

    # Pydantic 서브모델
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if isinstance(val, dict):
            return _normalize_dict(val, annotation)
        if isinstance(val, str):
            pk = _primary_str_field(annotation)
            if pk:
                logger.warning(
                    "[COERCE] str→object 승격 | field=%s val=%.80s",
                    rule_field, val,
                )
                return {pk: val}
        return val

    # str
    if annotation is str:
        return str(val) if not isinstance(val, str) else val

    # int
    if annotation is int:
        if isinstance(val, (int, float)):
            return int(val)
        return val

    # bool
    if annotation is bool:
        return bool(val)

    return val


def _coerce_list(val: Any, item_type: Any, *, rule_field: str = "") -> List:
    """리스트 교정: 단일 값→[값], 리스트 내 항목별 교정, 깨진 항목 드롭."""
    if not isinstance(val, list):
        val = [val]

    coerced: list = []
    for i, item in enumerate(val):
        try:
            coerced.append(_coerce_value(item, item_type, rule_field=f"{rule_field}[{i}]"))
        except Exception:
            logger.warning(
                "[COERCE] 항목 드롭 | field=%s[%d] type=%s",
                rule_field, i, type(item).__name__,
            )
    return coerced


# ═══════════════════════════════════════════════════════════════════════════
# 유틸
# ═══════════════════════════════════════════════════════════════════════════

def _is_optional(annotation: Any) -> bool:
    """Union[X, None] 패턴 감지."""
    origin = get_origin(annotation)
    if origin is not None:
        import types
        if isinstance(annotation, types.UnionType) or str(origin) == "typing.Union":
            return type(None) in get_args(annotation)
    return False


def _unwrap_optional(annotation: Any) -> Any:
    """Optional[X] 에서 X 추출."""
    for arg in get_args(annotation):
        if arg is not type(None):
            return arg
    return Any


def _primary_str_field(model_cls: type[BaseModel]) -> str | None:
    """모델의 '대표 문자열 필드' — str 타입인 첫 번째 필드의 alias/이름 반환.

    LLM 이 객체 대신 문자열 하나를 뱉었을 때 {primary_field: 문자열} 로 승격하는 용도.
    """
    for key, info in model_cls.model_fields.items():
        if info.annotation is str:
            return info.alias or key
    return None
