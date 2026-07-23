"""LLM 응답 콘텐츠 해시 캐시 — 같은 입력이면 서버를 다시 부르지 않는다.

두 가지를 동시에 해결한다.

1. **재현성**: 서버가 배치 크기에 따라 같은 입력에 다른 출력을 낸다(사건 G — 실행마다
   규칙 수가 ±3% 흔들림). 온도·top_k 로는 못 막고 서버 설정은 우리 권한 밖이다. 캐시는
   그 문제를 **클라이언트에서** 끝낸다 — 한 번 나온 답이 그 입력의 답이 된다.
2. **재실행 비용**: 코퍼스가 그대로면 LLM 콜이 0에 수렴한다. 코드 일부만 바뀌면 그 함수의
   프롬프트만 달라지므로 그 부분만 다시 부른다.

키는 **모델에 실제로 보낸 것 전부**다(프롬프트 본문·모델·max_tokens·응답 스키마). 하나라도
바뀌면 다른 키라서 **별도 무효화 로직이 필요 없다** — 프롬프트가 곧 키다.

저장 위치는 코퍼스 옆(``ROBO_DATA_DIR/.llm-cache``)이다. 프롬프트가 코퍼스 내용을 품고
있어 코퍼스와 수명을 같이하는 것이 맞고, 저장소를 오염시키지 않는다.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

from shared.config.settings import SETTINGS
from shared.events.runtime_logger import log_warning

_COMPONENT = "llm.response_cache"


def cache_root() -> Optional[Path]:
    """캐시 폴더. 비활성이면 None."""
    if not SETTINGS.analysis_llm.response_cache:
        return None
    return SETTINGS.analysis_data.dir / ".llm-cache"


def make_key(
    *,
    rule_name: str,
    system_prompt: str,
    user_content: str,
    model: str,
    max_tokens: Any,
    response_model: Optional[type[BaseModel]],
) -> str:
    """모델에 보낸 것 전부를 담은 해시 — 하나라도 다르면 다른 키가 된다."""
    payload = json.dumps(
        {
            "rule": rule_name,
            "system": system_prompt,
            "user": user_content,
            "model": model,
            "max_tokens": max_tokens,
            "schema": response_model.__name__ if response_model else None,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _path(key: str) -> Optional[Path]:
    root = cache_root()
    return None if root is None else root / key[:2] / f"{key}.json"


def load(key: str, response_model: Optional[type[BaseModel]]) -> Any:
    """적중이면 산출물, 아니면 None. 캐시 문제는 미적중으로 강등해 분석을 막지 않는다."""
    path = _path(key)
    if path is None or not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))["value"]
    except Exception as error:  # noqa: BLE001 — 캐시는 보조물
        log_warning(_COMPONENT, "cache_read_failed",
                    "캐시 항목을 읽지 못해 다시 호출한다",
                    key=key, error=f"{type(error).__name__}: {error}")
        return None
    if response_model is None:
        return value
    try:
        return response_model.model_validate(value)
    except Exception as error:  # noqa: BLE001 — 스키마가 바뀌었으면 미적중 처리
        log_warning(_COMPONENT, "cache_schema_mismatch",
                    "캐시 항목이 현재 스키마와 맞지 않아 다시 호출한다",
                    key=key, schema=response_model.__name__,
                    error=f"{type(error).__name__}: {error}")
        return None


def store(key: str, value: Any) -> None:
    """산출물 적재. 실패는 경고만 — 캐시 때문에 분석이 죽으면 안 된다."""
    path = _path(key)
    if path is None:
        return
    body = value.model_dump() if isinstance(value, BaseModel) else value
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"value": body}, ensure_ascii=False), encoding="utf-8")
    except Exception as error:  # noqa: BLE001
        log_warning(_COMPONENT, "cache_write_failed",
                    "캐시 적재 실패 — 분석 결과에는 영향 없음",
                    key=key, error=f"{type(error).__name__}: {error}")
