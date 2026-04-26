"""Phase 1 준비 단계 공개 API.

파일시스템 스캔 + 결정론 메타 수집 + scanning 에이전트 자연어 요약을 한 번에
수행해 CodebaseProfile (catalog + plan + info + summary + resources) 로 반환한다.

흐름:
  1. scan_project()          — 파일 카탈로그 + ProcessingPlan (결정론)
  2. collect()               — 확장자/매니페스트/imports/대표 파일 뼈대/함수 인덱스
  3. _run_profile_agent()    — 프레임워크/DB 접근 패턴 자연어 요약 (LLM 에이전트)
  4. CodebaseProfile 로 묶어 반환
"""

from __future__ import annotations

import logging
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Tuple

from util.llm_call_safety import strip_control_tokens
from analyzer.strategy.framework.common.models.node_models import FileCatalogEntry, ProcessingPlan
from analyzer.strategy.framework.scanning.agent.scanning_agent import build_scanning_agent
from analyzer.strategy.framework.scanning.codebase_scan_collector import (
    CodebaseInfo,
    collect,
    scan_project,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# Phase 1 준비 단계 산출물 데이터 모델
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class CodebaseProfile:
    """Phase 1 준비 단계의 최종 산출물.

    scanner 결과 (catalog + plan) + 결정론 수집 메타 (info) +
    Deep Agent 가 관찰한 3블록 (프레임워크/데이터접근/반복 이디엄) 자연어 요약.
    """

    catalog: List[FileCatalogEntry]
    plan: ProcessingPlan
    info: CodebaseInfo
    summary: str
    resources: dict[str, list[str]]
    # Block 별 자연어 섹션 — 파싱 성공 시 {"framework", "data_access", "idioms"}.
    # 파싱 실패 시 빈 dict 으로 두고 summary 전체만 쓰는 폴백 허용.
    summary_sections: dict[str, str] = field(default_factory=dict)
    tool_calls: int = 0
    elapsed_sec: float = 0.0


def profile(
    root: Path,
    lang: str,
    scan_roots: Optional[List[Tuple[str, str]]] = None,
    api_key: str = "",
) -> CodebaseProfile:
    """프로젝트 루트 1 개 → CodebaseProfile 1 개."""
    started = time.time()
    root_path = Path(root).resolve()
    logger.info("[PHASE:1:PROFILE] 시작 | root=%s lang=%s", root_path, lang)

    catalog, plan = scan_project(scan_roots=scan_roots, lang=lang) if scan_roots else scan_project(lang=lang, project_root=str(root_path))
    logger.info(
        "[PHASE:1:PROFILE] 스캔 완료 | catalog=%d source_ast=%d sql=%d entity=%d config=%d",
        len(catalog), len(plan.source_ast_jobs), len(plan.sql_jobs),
        len(plan.entity_jobs), len(plan.config_jobs),
    )

    info = collect(str(root_path))

    resources = {
        "SQL": [_strip_prefix(p) for p in plan.sql_jobs],
        "ENTITY": [_strip_prefix(p) for p in plan.entity_jobs],
        "CONFIG": [_strip_prefix(p) for p in plan.config_jobs],
        "DOCS": [_strip_prefix(p) for p in plan.docs_jobs],
        "OTHER": [],
    }

    summary, summary_sections, tool_calls = _run_profile_agent(
        root=root_path,
        info=info,
        resources=resources,
        api_key=api_key,
    )

    elapsed = time.time() - started
    logger.info(
        "[PHASE:1:PROFILE] 완료 | tool_calls=%d elapsed=%.1fs sections=%s summary_preview=%s",
        tool_calls, elapsed,
        ",".join(sorted(summary_sections.keys())) or "(폴백)",
        (summary or "")[:160].replace("\n", " "),
    )

    return CodebaseProfile(
        catalog=catalog,
        plan=plan,
        info=info,
        summary=summary,
        summary_sections=summary_sections,
        resources=resources,
        tool_calls=tool_calls,
        elapsed_sec=elapsed,
    )


def _strip_prefix(path: str) -> str:
    """scanner 가 붙인 'source/' 경로 접두부 제거."""
    p = path.replace("\\", "/")
    if p.startswith("source/"):
        return p[len("source/"):]
    return p


# ═══════════════════════════════════════════════════════════════════════
# Profile 에이전트 실행 (드라이버) — 입력 렌더링 / 출력 파싱
# ═══════════════════════════════════════════════════════════════════════


def _run_profile_agent(
    root: Path,
    info: CodebaseInfo,
    resources: dict[str, list[str]],
    api_key: str = "",
    _retry: int = 0,
) -> tuple[str, dict[str, str], int]:
    """scanning 에이전트 호출 → (final_text, summary_sections, tool_calls).

    summary_sections 키: 'framework' / 'data_access' (2블록 KO/EN) +
    Block 1/2/3 하위호환. 파싱 실패 시 빈 dict.
    Harmony 누수/섹션 0 건 시 최대 1 회 재시도.
    """
    agent, user_template = build_scanning_agent(root=root, api_key=api_key)
    user_msg = _render_prompt(root, info, resources, user_template)

    logger.info("[PHASE:1:PROFILE] Agent 시작 (retry=%d)", _retry)
    result = agent.invoke({"messages": [{"role": "user", "content": user_msg}]})

    raw_text = _extract_final_text(result)
    final_text = strip_control_tokens(raw_text).strip()
    sections = _split_sections(final_text)
    tool_calls = _count_tool_calls(result)
    had_leak = final_text != (raw_text or "").strip()
    logger.info(
        "[PHASE:1:PROFILE] Agent 완료 | tool_calls=%d sections=%d harmony_leak=%s",
        tool_calls, len(sections), had_leak,
    )

    # 재시도 조건: 섹션 0 건이고 아직 재시도 안 했을 때 (Harmony 누수·빈 응답 등).
    if _retry == 0 and len(sections) == 0:
        logger.warning(
            "[PHASE:1:PROFILE] 섹션 파싱 실패 (harmony_leak=%s) — 1 회 재시도",
            had_leak,
        )
        return _run_profile_agent(root, info, resources, api_key=api_key, _retry=1)

    return final_text, sections, tool_calls


# ─────────────────────────────────────────────────────────────────────
# Block 3 (반복 네이밍/이디엄) 근거용 언어 중립 시그널 수집 헬퍼
# 모두 정규식 기반 — 특정 언어/프레임워크에 의존하지 않음.
# ─────────────────────────────────────────────────────────────────────

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# 카멜케이스·스네이크케이스 모두 분리.
_TOKEN_SPLIT_RE = re.compile(r"[_\s]+|(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
# 함수 정의 라인 휴리스틱: 들여쓰기 허용, `(` 가 있고 `;` 로 끝나지 않음.
_FUNC_DEF_RE = re.compile(r"^\s*(?:[A-Za-z_][\w\s*&:<>,]*?\s+)?([A-Za-z_]\w*)\s*\([^;]*$")
# 호출 토큰: IDENT 뒤에 `(` 가 붙은 것.
_CALL_RE = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
# C/Java/Python 공통 키워드·빌트인 (호출처럼 보이는 제어 흐름 제거용).
_STOP_KEYWORDS = frozenset({
    "if", "for", "while", "switch", "do", "return", "sizeof", "case", "else",
    "def", "class", "lambda", "await", "yield", "new", "try", "catch", "throw",
    "static", "long", "int", "char", "void", "short", "double", "float", "bool",
    "struct", "union", "enum", "typedef", "const", "unsigned", "signed",
})


def _extract_func_name(signature: str) -> str:
    """시그니처 문자열에서 함수명만 추출 (언어 중립: `(` 앞 마지막 IDENT)."""
    head = signature.split("(", 1)[0]
    toks = _IDENT_RE.findall(head)
    return toks[-1] if toks else ""


def _compute_name_ngrams(function_index: list[tuple[str, list[str]]]) -> str:
    """함수 시그니처에서 함수명만 뽑아 토큰 1-gram / 2-gram 빈도 집계."""
    names: list[str] = []
    for _path, sigs in function_index:
        for sig in sigs:
            name = _extract_func_name(sig)
            if name:
                names.append(name)
    if not names:
        return "(표본 부족)"
    unigrams: Counter[str] = Counter()
    bigrams: Counter[tuple[str, str]] = Counter()
    for name in names:
        tokens = [t for t in _TOKEN_SPLIT_RE.split(name) if t]
        tokens_lc = [t.lower() for t in tokens]
        unigrams.update(tokens_lc)
        for i in range(len(tokens_lc) - 1):
            bigrams[(tokens_lc[i], tokens_lc[i + 1])] += 1
    lines = [f"(함수 {len(names)}개 기준)", "[1-gram 상위]"]
    for tok, n in unigrams.most_common(15):
        if n < 2:
            break
        lines.append(f"  {tok}: {n}")
    lines.append("[2-gram 상위]")
    had_bi = False
    for (a, b), n in bigrams.most_common(15):
        if n < 2:
            break
        lines.append(f"  {a}_{b}: {n}")
        had_bi = True
    if not had_bi:
        lines.append("  (빈출 없음)")
    return "\n".join(lines)


def _read_representative_source(root: Path, representative_file: str) -> list[str] | None:
    if not representative_file:
        return None
    candidate = root / representative_file
    if not candidate.exists() or not candidate.is_file():
        return None
    try:
        src = candidate.read_text(encoding="utf-8", errors="replace")
    except OSError as e:  # noqa: SILENT_FAIL — 대표 파일 읽기 실패는 프로파일링 보조 입력 부재로 처리 (다른 파일로 진행)
        logger.error(
            "[PROFILER] 대표 소스 읽기 실패 | path=%s | %s: %s",
            candidate, type(e).__name__, e,
        )
        return None
    return src.splitlines()


def _is_func_definition(lines: list[str], i: int) -> bool:
    """정의인지 호출인지 언어 중립 휴리스틱으로 판별."""
    cur = lines[i]
    if '{' in cur:
        return True
    if cur.rstrip().endswith(':'):
        return True
    j = i + 1
    while j < len(lines) and j < i + 3:
        stripped = lines[j].lstrip()
        if not stripped:
            j += 1
            continue
        return stripped.startswith('{')
    return False


def _compute_call_sequences(lines: list[str]) -> str:
    """대표 파일 각 함수의 첫 3개 호출 심볼 시퀀스 추출."""
    results: list[str] = []
    i = 0
    while i < len(lines) and len(results) < 20:
        m = _FUNC_DEF_RE.match(lines[i])
        if m:
            name = m.group(1)
            stripped = lines[i].lstrip()
            first_word = stripped.split('(', 1)[0].split()[-1] if stripped else ""
            if (
                name
                and name not in _STOP_KEYWORDS
                and first_word not in _STOP_KEYWORDS
                and _is_func_definition(lines, i)
            ):
                body = "\n".join(lines[i + 1:i + 120])
                calls: list[str] = []
                for cm in _CALL_RE.finditer(body):
                    sym = cm.group(1)
                    if sym != name and sym not in _STOP_KEYWORDS:
                        calls.append(sym)
                        if len(calls) >= 3:
                            break
                if calls:
                    results.append(f"  {name}: {' → '.join(calls)}")
        i += 1
    return "\n".join(results) if results else "(표본 부족)"


def _compute_function_length_stats(lines: list[str]) -> str:
    """괄호 매칭 기반 함수 본문 길이 분포 (언어 중립 휴리스틱)."""
    lengths: list[int] = []
    i = 0
    while i < len(lines):
        if _FUNC_DEF_RE.match(lines[i]):
            start = i
            depth = 0
            started = False
            end = i
            for j in range(i, min(i + 500, len(lines))):
                for ch in lines[j]:
                    if ch == '{':
                        depth += 1
                        started = True
                    elif ch == '}':
                        depth -= 1
                        if started and depth == 0:
                            end = j
                            break
                if started and depth == 0:
                    break
            if started:
                lengths.append(end - start + 1)
                i = end
        i += 1
    if not lengths:
        return "(표본 부족)"
    lengths.sort()
    n = len(lengths)
    avg = sum(lengths) // n
    med = lengths[n // 2]
    return f"함수 {n}개 / 평균 {avg} 라인 / 중앙 {med} / 최대 {lengths[-1]}"


def _compute_import_cooccurrence(import_frequency: dict[str, int]) -> str:
    """파일별 import 원본이 없으면 전체 빈도 상위만 표시."""
    if not import_frequency:
        return "(표본 부족)"
    items = sorted(import_frequency.items(), key=lambda kv: -kv[1])[:10]
    return ", ".join(f"{k}({v})" for k, v in items)


def _render_prompt(root: Path, info: CodebaseInfo, resources: dict[str, list[str]], user_template: str) -> str:
    lines: list[str] = []
    for cat in ("SQL", "ENTITY", "CONFIG", "DOCS"):
        paths = resources.get(cat) or []
        if paths:
            preview = ", ".join(paths[:8])
            more = f" (+ {len(paths) - 8} more)" if len(paths) > 8 else ""
            lines.append(f"- {cat}: {preview}{more}")
    resources_summary = "\n".join(lines) if lines else "(없음)"

    if info.function_index:
        idx_lines: list[str] = []
        for path, sigs in info.function_index:
            idx_lines.append(f"- {path}")
            for sig in sigs:
                idx_lines.append(f"    · {sig}")
        function_index_block = "\n".join(idx_lines)
    else:
        function_index_block = "(없음)"

    # Block 3 근거 시그널
    ngrams_block = _compute_name_ngrams(info.function_index)
    rep_lines = _read_representative_source(root, info.representative_file)
    if rep_lines is not None:
        call_seq_block = _compute_call_sequences(rep_lines)
        length_stats_block = _compute_function_length_stats(rep_lines)
    else:
        call_seq_block = "(대표 파일 접근 불가)"
        length_stats_block = "(대표 파일 접근 불가)"
    cooccurrence_block = _compute_import_cooccurrence(info.import_frequency)

    return user_template.format(
        extensions=info.extensions or "(비어있음)",
        manifest_name=info.manifest_name or "(없음)",
        manifest_content=info.manifest_content or "(없음)",
        config_name=info.config_name or "(없음)",
        config_content=info.config_content or "(없음)",
        import_frequency=info.import_frequency or "(비어있음)",
        import_cooccurrence=cooccurrence_block,
        sample_file_count=info.sample_file_count,
        representative_file=info.representative_file or "(없음)",
        representative_skeleton=info.representative_skeleton or "(없음)",
        function_index=function_index_block,
        function_name_ngrams=ngrams_block,
        call_sequences=call_seq_block,
        function_length_stats=length_stats_block,
        resources_summary=resources_summary,
    )


# ─────────────────────────────────────────────────────────────────────
# 결과 파싱 헬퍼
# ─────────────────────────────────────────────────────────────────────


def _extract_final_text(result: dict[str, Any]) -> str:
    for msg in reversed(result.get("messages", [])):
        if getattr(msg, "type", "") != "ai":
            continue
        if getattr(msg, "tool_calls", None):
            continue
        content = getattr(msg, "content", "")
        if isinstance(content, list):
            parts = [b.get("text", "") for b in content if isinstance(b, dict)]
            content = "\n".join(p for p in parts if p)
        if content and content.strip():
            return content.strip()
    return ""


def _count_tool_calls(result: dict[str, Any]) -> int:
    return sum(
        len(getattr(msg, "tool_calls", None) or [])
        for msg in result.get("messages", [])
    )


# 2블록 포맷: '## 프레임워크 패턴' / '## DB 접근 패턴' (번호 없음).
# 기존 'Block 1/2/3' 포맷 호환 유지.
_SECTION_HEADER_RE = re.compile(
    r"""^\s*
        (?:\#{1,3}\s*|\*\*\s*)?
        (?:
            (?:block\s*)?([123])[\.\:\-\s]       # Block 1/2/3 (legacy)
          | (프레임워크\s*패턴|framework\s*pattern)    # framework pattern (KO/EN)
          | (DB\s*접근\s*패턴|DB\s*access\s*pattern)   # DB access pattern (KO/EN)
        )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _split_sections(text: str) -> dict[str, str]:
    """LLM 텍스트에서 섹션 분리. 실패 시 빈 dict.

    키: 'framework' / 'data_access' / 'idioms'.
    """
    if not text:
        return {}
    num_key = {"1": "framework", "2": "data_access", "3": "idioms"}
    lines = text.splitlines()
    positions: list[tuple[int, str]] = []
    for i, ln in enumerate(lines):
        m = _SECTION_HEADER_RE.match(ln)
        if not m:
            continue
        if m.group(1):
            key = num_key.get(m.group(1))
        elif m.group(2):
            key = "framework"
        elif m.group(3):
            key = "data_access"
        else:
            key = None
        if key:
            positions.append((i, key))
    if not positions:
        return {}
    sections: dict[str, str] = {}
    for idx, (start, key) in enumerate(positions):
        end = positions[idx + 1][0] if idx + 1 < len(positions) else len(lines)
        body = "\n".join(lines[start:end]).strip()
        if body:
            sections[key] = body
    return sections
