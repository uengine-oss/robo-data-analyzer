"""방법 A (Naive): 파일 몇 개 샘플링 → 단일 LLM 호출.

의도된 한계:
- 전략 없음. 전체 파일 중 상위 10개만 샘플링.
- 매니페스트(requirements.txt 등) 를 적극적으로 찾지 않음.
- LLM 이 ProFrame 같은 미학습 프레임워크는 거의 알아보지 못함.
"""

from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from client.llm_client import get_llm
from config.settings import settings
from test.codebase_detection_comparison.file_tools import (
    DEFAULT_SKIP_DIRS,
    _iter_files,
    read_file,
)


_PROMPT_TEMPLATE = """너는 코드베이스 분석가다. 아래 파일 목록과 앞부분만 보고
이 프로젝트의 기술 스택을 추정하라.

응답 형식(JSON):
{{
  "primary_language": "<주 언어 하나. 예: Python>",
  "frameworks": ["<프레임워크 이름>", ...],
  "orms": ["<ORM 이름>", ...],
  "databases": ["<DB 엔진 이름>", ...],
  "proprietary_frameworks": ["<내부/독점 프레임워크 이름>", ...]
}}

근거 없으면 빈 배열로. JSON 외의 텍스트는 절대 출력하지 마라.

프로젝트 루트: {root}
전체 파일 수: {total_files}
샘플된 파일 수: {sampled}

--- 파일 내용 샘플 ---
{bundle}
"""


@dataclass
class NaiveResult:
    primary_language: str = ""
    frameworks: list[str] = field(default_factory=list)
    orms: list[str] = field(default_factory=list)
    databases: list[str] = field(default_factory=list)
    proprietary_frameworks: list[str] = field(default_factory=list)
    raw_answer: str = ""
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    elapsed_sec: float = 0.0
    sampled_files: list[str] = field(default_factory=list)
    error: str | None = None


def _sample_files(root: Path, sample_count: int, seed: int | None) -> list[Path]:
    """루트 아래 모든 소스 파일 중 무작위 샘플링."""

    all_files: list[Path] = []
    for p in _iter_files(root, DEFAULT_SKIP_DIRS):
        # 너무 큰 바이너리 배제.
        try:
            if p.stat().st_size > 500_000:
                continue
        except OSError:
            continue
        all_files.append(p)

    if not all_files:
        return []

    rng = random.Random(seed) if seed is not None else random.Random()
    if len(all_files) <= sample_count:
        return all_files
    return rng.sample(all_files, sample_count)


def _build_bundle(root: Path, files: list[Path], max_chars: int = 30000) -> tuple[str, int]:
    """샘플된 파일들의 앞 200줄을 합쳐 bundle 문자열 생성. 총 max_chars 컷."""

    parts: list[str] = []
    total = 0
    for path in files:
        try:
            content = read_file(path, limit=200)
        except (IOError, OSError):
            continue
        rel = str(path.relative_to(root)).replace("\\", "/")
        header = f"\n# FILE: {rel}\n"
        chunk = header + content
        if total + len(chunk) > max_chars:
            remaining = max_chars - total
            if remaining > len(header) + 100:
                parts.append(header + content[: remaining - len(header)])
            break
        parts.append(chunk)
        total += len(chunk)
    return "".join(parts), total


def _extract_json(text: str) -> dict:
    """LLM 응답에서 첫 JSON 객체 추출 (코드펜스 등에 감싸져 있어도 처리)."""

    # 코드펜스 제거.
    cleaned = re.sub(r"```(?:json)?\s*", "", text)
    cleaned = cleaned.replace("```", "")
    # 첫 { 와 마지막 } 사이를 파싱 시도.
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}
    candidate = cleaned[start : end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return {}


def _sum_usage(response) -> tuple[int, int, int]:
    """LangChain AIMessage 의 usage_metadata 에서 토큰 수 추출."""

    usage = getattr(response, "usage_metadata", None) or {}
    prompt = int(usage.get("input_tokens", 0))
    completion = int(usage.get("output_tokens", 0))
    total = int(usage.get("total_tokens", prompt + completion))
    return prompt, completion, total


def naive_detect(
    root: str | Path,
    sample_count: int = 10,
    api_key: str = "",
    seed: int | None = 42,
    logger=None,
) -> NaiveResult:
    """방법 A 실행. 동기 함수 (단일 LLM 호출만)."""

    root_path = Path(root).resolve()
    start = time.perf_counter()
    api_key = api_key or settings.llm.api_key

    if logger:
        logger.log_phase("NAIVE", f"방법 A 시작 (sample_count={sample_count})")

    files = _sample_files(root_path, sample_count, seed)
    bundle, _bundle_bytes = _build_bundle(root_path, files)

    if logger:
        sampled = [str(f.relative_to(root_path)).replace("\\", "/") for f in files]
        logger.log("NAIVE", f"샘플된 파일 {len(files)}개", sampled)

    # 전체 파일 수 추정 (진행 상황 공유용).
    total_files = sum(1 for _ in _iter_files(root_path, DEFAULT_SKIP_DIRS))
    if logger:
        logger.log("NAIVE", f"전체 파일 수: {total_files}, bundle 바이트: {_bundle_bytes}")

    prompt = _PROMPT_TEMPLATE.format(
        root=str(root_path),
        total_files=total_files,
        sampled=len(files),
        bundle=bundle,
    )

    if logger:
        logger.log("NAIVE", "LLM 단일 호출 시작...")

    try:
        llm = get_llm(api_key=api_key)
        response = llm.invoke([
            {"role": "system", "content": "당신은 숙련된 코드베이스 분석가입니다. 반드시 JSON만 출력합니다."},
            {"role": "user", "content": prompt},
        ])
    except Exception as exc:  # noqa: BLE001
        if logger:
            logger.log("NAIVE", f"LLM 호출 실패: {exc}")
        return NaiveResult(
            raw_answer="",
            elapsed_sec=time.perf_counter() - start,
            error=f"LLM call failed: {exc}",
            sampled_files=[str(f.relative_to(root_path)).replace("\\", "/") for f in files],
        )

    raw = getattr(response, "content", "") or ""
    if isinstance(raw, list):
        raw = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in raw)

    parsed = _extract_json(raw)
    prompt_tok, completion_tok, total_tok = _sum_usage(response)

    if logger:
        logger.log("NAIVE", f"LLM 응답 수신 (tokens={total_tok})")
        logger.log("NAIVE", "raw JSON 파싱 결과", parsed)

    return NaiveResult(
        primary_language=str(parsed.get("primary_language", "")),
        frameworks=list(parsed.get("frameworks", []) or []),
        orms=list(parsed.get("orms", []) or []),
        databases=list(parsed.get("databases", []) or []),
        proprietary_frameworks=list(parsed.get("proprietary_frameworks", []) or []),
        raw_answer=raw,
        total_tokens=total_tok,
        prompt_tokens=prompt_tok,
        completion_tokens=completion_tok,
        elapsed_sec=time.perf_counter() - start,
        sampled_files=[str(f.relative_to(root_path)).replace("\\", "/") for f in files],
    )
