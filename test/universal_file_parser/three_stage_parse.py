"""✅ 좋은 방법 — 라이브러리 결정론 + (미지 포맷만) DeepAgent

흐름:
  L1 캐시 → 포맷 식별 → 라이브러리 파싱 → (미지 포맷이면) L2 캐시 → DeepAgent 코드생성
  → 검증 → 성공 시 L1/L2 저장

결과는 아무것도 걸러내지 않고 **모든 키·값·태그 그대로** downstream 에 전달.
의미 판단 (어느 게 anchor 인가) 은 downstream 이 코드 컨텍스트와 함께 수행.
"""

import time

from .cache import l1_get, l1_put, l2_get, l2_put
from .detect import detect
from .flatten import flatten
from .stage1_library import library_parse, LIBRARY_HANDLERS
from .stage2_codegen import execute_parser, generate_parser_with_llm
from .validate import validate


def three_stage_parse(path: str) -> dict:
    """라이브러리 우선, 실패 시 DeepAgent 로 폴백하는 파싱.

    Returns:
        {
          "result": dict | list | None,   # 라이브러리/DeepAgent 파싱 결과 (있는 그대로)
          "flat": dict[str, str],         # 평탄화 보조 (downstream 편의)
          "elapsed_sec": float,
          "tokens": int,                  # DeepAgent 호출 시에만 발생
          "strategy": "l1_cache_hit" | "library_<fmt>" | "l2_cache_hit" | "llm_codegen" | "failed",
          "format": str,
          "validation": {"ok": bool, "reason": str},
        }
    """
    start = time.time()

    with open(path, 'rb') as f:
        data = f.read()

    # ─── L1 캐시 (같은 파일 재실행) ───────────────────
    cached = l1_get(data)
    if cached is not None:
        return _pack(
            result=cached, fmt='cached',
            strategy='l1_cache_hit', tokens=0, start=start,
            validation={"ok": True, "reason": "cached"},
        )

    # ─── 포맷 식별 ───────────────────────────────────
    fmt, encoding = detect(path, data)

    parsed = None
    tokens_used = 0
    parser_code = None
    strategy = "failed"

    # ─── 라이브러리 파싱 (익숙한 포맷) ────────────────
    if fmt in LIBRARY_HANDLERS:
        parsed = library_parse(fmt, data, encoding)
        if parsed is not None:
            strategy = f"library_{fmt}"

    # ─── DeepAgent (미지 포맷 또는 라이브러리 실패) ───
    serialize_fn = None
    is_deepagent_path = False
    if parsed is None:
        # L2 캐시 — 같은 구조 다른 파일에 대해 캐시된 파서 재사용
        cached_code = l2_get(data)
        if cached_code:
            try:
                parsed, serialize_fn = execute_parser(
                    cached_code, data, return_serialize=True
                )
                if parsed:
                    strategy = "l2_cache_hit"
                    parser_code = cached_code
                    is_deepagent_path = True
            except Exception:
                parsed = None
                serialize_fn = None

        # 진짜 LLM 호출 (DeepAgent)
        if parsed is None:
            parser_code, tokens_used = generate_parser_with_llm(path, data, fmt_hint=fmt)
            if parser_code:
                try:
                    parsed, serialize_fn = execute_parser(
                        parser_code, data, return_serialize=True
                    )
                    strategy = "llm_codegen"
                    is_deepagent_path = True
                except Exception:
                    parsed = None
                    serialize_fn = None

    # ─── 검증 ────────────────────────────────────────
    # 라이브러리 경로: 완화 기준 (coverage 70%)
    # DeepAgent 경로: 엄격 기준 (coverage 85% + serialize round-trip)
    if parsed is not None:
        ok, reason = validate(
            parsed, data, fmt,
            serialize_fn=serialize_fn if is_deepagent_path else None,
            strict=is_deepagent_path,
        )
    else:
        ok, reason = False, "no_parser_succeeded"

    # ─── 성공 시 캐시 저장 ───────────────────────────
    if ok:
        l1_put(data, parsed)
        if parser_code and strategy == "llm_codegen":
            l2_put(data, parser_code, f"generated for {fmt}")

    return _pack(
        result=parsed,
        fmt=fmt,
        strategy=strategy,
        tokens=tokens_used,
        start=start,
        validation={"ok": ok, "reason": reason},
    )


def _pack(result, fmt, strategy, tokens, start, validation) -> dict:
    flat = flatten(result) if result else {}
    return {
        "result": result,
        "flat": flat,
        "elapsed_sec": round(time.time() - start, 3),
        "tokens": tokens,
        "strategy": strategy,
        "format": fmt,
        "validation": validation,
    }
