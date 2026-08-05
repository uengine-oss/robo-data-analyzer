"""LLM 응답 콘텐츠 해시 캐시 — 같은 입력이면 서버를 다시 부르지 않는다.

두 가지를 동시에 해결한다.

1. **재현성**: 서버가 배치 크기에 따라 같은 입력에 다른 출력을 낸다(사건 G — 실행마다
   규칙 수가 ±3% 흔들림). 온도·top_k 로는 못 막고 서버 설정은 우리 권한 밖이다. 캐시는
   그 문제를 **클라이언트에서** 끝낸다 — 한 번 나온 답이 그 입력의 답이 된다.
2. **재실행 비용**: 코퍼스가 그대로면 LLM 콜이 0에 수렴한다. 코드 일부만 바뀌면 그 함수의
   프롬프트만 달라지므로 그 부분만 다시 부른다.

키는 **모델에 실제로 보낸 것 전부**다(프롬프트 본문·모델·max_tokens·구조화 채널·응답 스키마). 하나라도
바뀌면 다른 키라서 **별도 무효화 로직이 필요 없다** — 프롬프트가 곧 키다.

저장 위치는 코퍼스 옆(``ROBO_DATA_DIR/.llm-cache``)이다. 프롬프트가 코퍼스 내용을 품고
있어 코퍼스와 수명을 같이하는 것이 맞고, 저장소를 오염시키지 않는다.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

from shared.config.settings import SETTINGS
from shared.events.runtime_logger import log_info, log_warning

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
    structured_output_method: str,
    response_model: Optional[type[BaseModel]],
) -> str:
    """모델에 보낸 것 전부를 담은 해시 — 하나라도 다르면 다른 키가 된다."""
    schema = (
        response_model.model_json_schema()
        if response_model is not None
        else None
    )
    payload = json.dumps(
        {
            "rule": rule_name,
            "system": system_prompt,
            "user": user_content,
            "model": model,
            "max_tokens": max_tokens,
            "structured_output_method": structured_output_method,
            "schema": schema,
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
        # 마지막 사용 시각 갱신 — sweep() 이 이걸로 "안 쓰는 것"을 가린다. 실패해도
        # 무해하므로(정리 우선순위만 부정확해진다) 조회를 막지 않는다.
        try:
            os.utime(path, None)
        except OSError as error:
            log_warning(
                _COMPONENT,
                "cache_touch_failed",
                "캐시 적중 파일의 최근 사용 시각 갱신 실패 — 값은 그대로 반환",
                key=key,
                error=f"{type(error).__name__}: {error}",
            )
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


def sweep() -> None:
    """용량 상한을 넘으면 오래 안 쓴 항목부터 지운다(LRU).

    키가 프롬프트 전체라서 프롬프트·코드를 고치면 옛 항목은 **다시는 안 읽히는 채로**
    남는다. 상한이 없으면 코퍼스 옆에서 무한히 쌓인다. 지워도 손실은 없다 — 필요하면
    다시 만든다(비용만 든다). 그래서 정리 실패도 경고로 끝낸다.

    최근성은 ``load()`` 가 갱신하는 mtime 이다. 목표치를 상한의 80% 로 잡아, 매 run 마다
    경계에서 조금씩 지우는 낭비를 막는다.
    """
    root = cache_root()
    limit_mb = SETTINGS.analysis_llm.response_cache_max_mb
    if root is None or limit_mb <= 0 or not root.is_dir():
        return
    limit_bytes = limit_mb * 1024 * 1024
    try:
        entries = [(p, p.stat()) for p in root.rglob("*.json")]
    except OSError as error:
        log_warning(_COMPONENT, "cache_sweep_failed",
                    "캐시 용량 점검 실패 — 정리를 건너뛴다",
                    error=f"{type(error).__name__}: {error}")
        return

    total = sum(stat.st_size for _, stat in entries)
    if total <= limit_bytes:
        return

    target = int(limit_bytes * 0.8)
    entries.sort(key=lambda item: item[1].st_mtime)  # 오래 안 쓴 것부터
    removed = removed_bytes = 0
    failed = 0
    first_error = None
    for path, stat in entries:
        if total <= target:
            break
        try:
            path.unlink()
        except OSError as error:
            failed += 1
            if first_error is None:
                first_error = f"{type(error).__name__}: {error}"
            continue  # 다음 항목은 독립적으로 정리할 수 있다.
        total -= stat.st_size
        removed += 1
        removed_bytes += stat.st_size

    # 조용한 삭제 금지 — 무엇을 얼마나 지웠는지 남긴다.
    log_info(_COMPONENT, "cache_swept",
             deleted=removed,
             deleted_mb=f"{removed_bytes / 1024 / 1024:.1f}",
             remaining_mb=f"{total / 1024 / 1024:.1f}",
             limit_mb=limit_mb,
             failed=failed,
             first_error=first_error)
    if failed:
        log_warning(
            _COMPONENT,
            "cache_sweep_partial_failure",
            "일부 캐시 항목 삭제 실패 — 남은 용량과 첫 오류를 기록한다",
            failed=failed,
            remaining_mb=f"{total / 1024 / 1024:.1f}",
            first_error=first_error,
        )


def store(key: str, value: Any) -> None:
    """산출물 적재. 실패는 경고만 — 캐시 때문에 분석이 죽으면 안 된다."""
    path = _path(key)
    if path is None:
        return
    body = value.model_dump() if isinstance(value, BaseModel) else value
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # 같은 키를 여러 콜이 동시에 쓸 수 있다(3분할·폴백 조각이 동시 실행).
        # 임시 파일에 쓰고 rename 하면 반쪽 파일이 구조적으로 남을 수 없다 — 다음 run 이
        # 손상 항목을 읽고 미적중 처리하는 낭비를 원천 차단한다.
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"value": body}, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except Exception as error:  # noqa: BLE001
        log_warning(_COMPONENT, "cache_write_failed",
                    "캐시 적재 실패 — 분석 결과에는 영향 없음",
                    key=key, error=f"{type(error).__name__}: {error}")
