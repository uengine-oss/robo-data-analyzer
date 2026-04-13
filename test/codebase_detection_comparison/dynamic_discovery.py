"""Phase 3.5 — 동적 발견 (Dynamic Discovery).

규칙서(사전)에 없는 미지의 프레임워크를 진짜로 "발견" 하는 레이어.

흐름:
  1. 미지 prefix 발견 (네거티브 매칭)
  2. sample_files_by_prefix    - 그 prefix 쓰는 대표 파일 수집
  3. extract_code_signals      - 각 파일에서 14가지 힌트 추출
  4. summarize_signals_multi   - 여러 파일 힌트를 종합
  5. characterize_unknown      - LLM 에게 원본 증거 제출, 라이브러리 특성 추론
  6. save_learned_skill        - 발견 결과를 JSON 으로 저장해 다음 실행에 캐시
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from test.codebase_detection_comparison.code_signals import (
    CodeSignals,
    extract_signals,
    summarize_signals_multi,
)
from test.codebase_detection_comparison.file_tools import (
    DEFAULT_SKIP_DIRS,
    _iter_files,
)


def sample_files_by_prefix(
    root: str | Path,
    prefix: str,
    count: int = 5,
    extensions: list[str] | None = None,
) -> list[str]:
    """미지의 접두어 prefix 와 관련된 대표 파일 상대경로 리스트 반환.

    수집 우선순위:
      1. 파일명이 prefix 로 시작 (예: pfm* 이면 pfmcom.h)
      2. 헤더 파일 (`.h`, `.hpp`, `.hh`)
      3. 해당 prefix 를 #include/import 로 참조하는 소스 파일
    """

    root_path = Path(root).resolve()
    ext_set = {e.lower() for e in (extensions or [".c", ".h", ".ec", ".pc", ".cpp", ".hpp", ".py", ".java", ".kt", ".ts", ".js", ".go", ".rs"])}
    prefix_lower = prefix.lower()

    # 1) 파일명 매치 우선.
    name_matches: list[Path] = []
    # 2) prefix 를 실제 참조하는 소스 파일.
    usage_matches: list[Path] = []

    # 파일명 정규식: 접두어 + 알파벳 또는 '_'.
    name_pat = re.compile(rf"^{re.escape(prefix_lower)}[a-zA-Z0-9_]*")
    # 본문 참조 정규식: include / import / use 중 prefix 가 첫 단어.
    body_pat = re.compile(
        rf'(?:#include\s*[<"]|from\s+|import\s+|use\s+)[<"]?{re.escape(prefix_lower)}',
        re.IGNORECASE,
    )

    for path in _iter_files(root_path, DEFAULT_SKIP_DIRS):
        ext = path.suffix.lower()
        if ext not in ext_set:
            continue
        if name_pat.match(path.stem.lower()):
            name_matches.append(path)
            continue
        # 본문 매치는 파일 앞 50줄만 훑어봄 (빠르게).
        if len(usage_matches) < count * 3:
            try:
                with path.open("r", encoding="utf-8", errors="ignore") as f:
                    head = "".join(line for _, line in zip(range(50), f))
            except (IOError, OSError):
                continue
            if body_pat.search(head):
                usage_matches.append(path)

        if len(name_matches) >= count * 2 and len(usage_matches) >= count:
            break

    # 결과 조합: 이름 매치 먼저, 그 다음 사용처. 중복 제거.
    seen: set[Path] = set()
    ordered: list[Path] = []
    for path in name_matches + usage_matches:
        if path in seen:
            continue
        seen.add(path)
        ordered.append(path)
        if len(ordered) >= count:
            break

    return [str(p.relative_to(root_path)).replace("\\", "/") for p in ordered]


def collect_code_signals_for_prefix(
    root: str | Path,
    prefix: str,
    count: int = 5,
    extensions: list[str] | None = None,
) -> dict[str, Any]:
    """한 prefix 에 대해 샘플 파일 확보 → 14가지 힌트 추출 → 종합 요약 반환.

    DeepAgent 가 한 번의 도구 호출로 전체 증거를 받을 수 있게 묶음.
    """

    root_path = Path(root).resolve()
    sample_paths = sample_files_by_prefix(root_path, prefix, count=count, extensions=extensions)

    per_file_signals: list[CodeSignals] = []
    for rel in sample_paths:
        signals = extract_signals(root_path / rel)
        per_file_signals.append(signals)

    summary = summarize_signals_multi(per_file_signals)

    return {
        "prefix": prefix,
        "sampled_files": sample_paths,
        "per_file": [s.to_dict() for s in per_file_signals],
        "aggregate_summary": summary,
    }


def build_characterization_prompt(evidence: dict[str, Any]) -> str:
    """LLM 에게 제출할 라이브러리 특성 추론 프롬프트.

    `evidence` 는 collect_code_signals_for_prefix 반환값.
    """

    prefix = evidence["prefix"]
    summary = evidence["aggregate_summary"]
    samples = evidence.get("sampled_files", [])

    return f"""당신은 코드 고고학자입니다. 아래는 미지의 라이브러리/프레임워크 '{prefix}' 에 대한
관찰 증거입니다. 사전 지식 없이 이 증거만으로 다음을 추론하세요.

[관찰 증거]

- 접두어: {prefix}
- 샘플 파일: {samples}
- 파일명 컨벤션: {summary.get('filename_patterns', [])}
- 헤더 주석 샘플 (파일별 맨 위 블록):
{json.dumps(summary.get('header_comments_sample', []), ensure_ascii=False, indent=2)}
- 공통 #include/import: {json.dumps(summary.get('includes_top', []), ensure_ascii=False)}
- 반복되는 typedef/구조체: {summary.get('typedefs_top', [])}
- 함수 네이밍 컨벤션 클러스터: {summary.get('naming_convention_clusters', [])}
- 자주 호출되는 매크로 (UPPER_SNAKE 형식): {json.dumps(summary.get('macros_top', []), ensure_ascii=False)}
- 자주 호출되는 API 함수 (소문자 식별자): {json.dumps(summary.get('api_calls_top', []), ensure_ascii=False)}
- 에러 처리 키워드/상수: {summary.get('error_keywords', [])}
- 유틸리티 함수 접두어 클러스터: {summary.get('utility_prefixes_top', [])}
- 업무 도메인 용어 (변수명 _cd/_id/_amt 등): {json.dumps(summary.get('domain_terms_top', []), ensure_ascii=False)}
- 자연어 감지: {summary.get('natural_languages_detected', [])}

[추론 지시]

다음 항목을 JSON 형식으로 추론하라. 확신이 낮으면 null 또는 "unknown" 으로 표기하고
'confidence' 에 0~1 실수로 확신도를 기재하라. 근거가 된 증거 항목을 'evidence_used'
배열에 명시하라 (예: ["header_comment", "macros", "naming_convention"]).

{{
  "library_role": "<이 라이브러리의 역할 추정. 예: 'DB 접근 프레임워크', 'TP 모니터', '웹 프레임워크'>",
  "architecture_clues": {{
    "layers": {{"<레이어코드>": "<역할>"}} ,
    "pattern": "<아키텍처 패턴 추정. 예: 'EMB 3-tier', 'MVC', 'Hexagonal'>"
  }},
  "db_access_style": "<DB 접근 방식. 예: 'DBIO 코드 생성', 'Embedded SQL', 'ORM', 'raw driver'. 없으면 null>",
  "error_handling_style": "<예외처리 방식. 예: 'setjmp/longjmp goto', 'try/catch', 'Result 타입'>",
  "likely_region": "<추정 지역/사용자층. 예: 'Korea financial sector'. 없으면 null>",
  "likely_vendor": "<추정 벤더. 예: 'TmaxSoft'. 없으면 null>",
  "canonical_name_guess": "<추정 공식 이름. 확신 낮으면 null>",
  "confidence": <0.0~1.0>,
  "evidence_used": ["<사용한 증거 카테고리 목록>"],
  "reasoning": "<2~4문장으로 왜 이 결론인지 서술>"
}}

JSON 외의 텍스트는 출력하지 마세요.
"""


def characterize_via_llm(evidence: dict[str, Any], api_key: str = "") -> dict[str, Any]:
    """LLM 에게 증거 제출 → 라이브러리 특성 JSON 반환.

    동기 호출. 도구 함수에서 부르기 적합.
    실패 시 error 필드 포함 dict 반환.
    """

    from client.llm_client import get_llm
    from config.settings import settings

    api_key = api_key or settings.llm.api_key
    prompt = build_characterization_prompt(evidence)

    try:
        llm = get_llm(api_key=api_key)
        response = llm.invoke(
            [
                {
                    "role": "system",
                    "content": "당신은 레거시 코드 고고학자입니다. 반드시 JSON 만 출력합니다.",
                },
                {"role": "user", "content": prompt},
            ]
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": f"LLM call failed: {exc}"}

    raw = getattr(response, "content", "") or ""
    if isinstance(raw, list):
        raw = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in raw
        )

    cleaned = re.sub(r"```(?:json)?\s*", "", raw).replace("```", "")
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1:
        return {"error": "JSON not found in response", "raw": raw[:500]}
    try:
        parsed = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as err:
        return {"error": f"JSON parse failed: {err}", "raw": raw[:500]}

    usage = getattr(response, "usage_metadata", None) or {}
    parsed["_tokens"] = {
        "input": usage.get("input_tokens", 0),
        "output": usage.get("output_tokens", 0),
        "total": usage.get("total_tokens", 0),
    }
    return parsed


def save_learned_skill(
    prefix: str,
    characterization: dict[str, Any],
    evidence: dict[str, Any],
    learned_dir: str | Path,
) -> str:
    """LLM 이 추론한 특성을 새 Skill JSON 으로 저장.

    경로: learned_dir/<prefix>_<date>.json
    다음 실행 때 skill_loader 가 자동으로 로드하도록 기존 스키마 준수.
    """

    learned_dir = Path(learned_dir)
    learned_dir.mkdir(parents=True, exist_ok=True)

    date = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{prefix}_{date}.json"
    out_path = learned_dir / filename

    # 네임은 LLM 이 추측한 것 우선, 없으면 prefix 기반.
    canonical = characterization.get("canonical_name_guess") or f"Unknown-{prefix}"
    summary = evidence.get("aggregate_summary", {})

    # framework_rule 스키마에 맞는 최소 구조 (None 값은 제거).
    related: dict[str, Any] = {}
    if characterization.get("likely_vendor"):
        related["vendor"] = characterization["likely_vendor"]
    if characterization.get("likely_region"):
        related["region"] = characterization["likely_region"]

    metadata: dict[str, Any] = {
        "added_on": datetime.now().strftime("%Y-%m-%d"),
        "added_by": "dynamic_discovery",
        "evidence_used": characterization.get("evidence_used", []),
        "samples": evidence.get("sampled_files", [])[:5],
    }
    for key, src in (
        ("llm_confidence", characterization.get("confidence")),
        ("llm_reasoning", characterization.get("reasoning")),
        ("library_role", characterization.get("library_role")),
        ("architecture_clues", characterization.get("architecture_clues")),
        ("db_access_style", characterization.get("db_access_style")),
    ):
        if src is not None:
            metadata[key] = src

    # 매크로 패턴은 안전한 정규식만 (특수문자 escape).
    safe_patterns = []
    for m in summary.get("macros_top", [])[:5]:
        name = m.get("name", "")
        if name and name.replace("_", "").isalnum():
            safe_patterns.append(
                {
                    "regex": re.escape(name),
                    "weight": 0.7,
                    "description": f"발견된 매크로 (count={m['count']})",
                }
            )

    rule = {
        "$schema": "../../schemas/framework_rule.schema.json",
        "name": canonical,
        "category": "framework",
        "subtype": "learned-dynamic",
        "applicable_languages": evidence.get("applicable_languages", ["C"]),
        "evidence": {
            "imports": [
                {"prefix": prefix, "weight": 0.9, "description": "동적 발견된 접두어"}
            ],
            "patterns": safe_patterns,
        },
        "confidence_weights": {
            "import_match": 0.85,
            "pattern_match": 0.7,
        },
        "related": related,
        "metadata": metadata,
    }

    with out_path.open("w", encoding="utf-8") as f:
        json.dump(rule, f, ensure_ascii=False, indent=2)

    return str(out_path)
