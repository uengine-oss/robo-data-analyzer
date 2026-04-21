"""Profile Agent — 프레임워크/DB 접근 패턴 요약 생성.

도구 3 개 (grep / read_file / read_function) 만 사용.
리소스 파일 분류는 scanner 가 이미 끝냈으므로 여기서는 자연어 요약만 담당한다.

create_agent + 최소 미들웨어 사용 (FilesystemMiddleware 제거 → 내장 도구 비활성화).
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from pathlib import Path
from typing import Any

import yaml
from langchain.agents import create_agent
from langchain.agents.middleware import (
    ContextEditingMiddleware,
    ClearToolUsesEdit,
    ModelCallLimitMiddleware,
    SummarizationMiddleware,
    TodoListMiddleware,
    ToolCallLimitMiddleware,
)
from langchain_core.tools import tool

from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware

from client.llm_client import get_llm
from config.settings import settings

from analyzer.strategy.framework.common.analysis_constants import SKIP_DIRS
from analyzer.strategy.framework.scanning.profile_models import CodebaseInfo
from analyzer.strategy.framework.scanning.tree_sitter_tools import extract_function_body

logger = logging.getLogger(__name__)

# 프롬프트는 프로젝트 최상위 rules/ 아래에서 로드 (다른 Deep Agent 프롬프트들과 같은 위치)
_RULES_PATH = (
    Path(__file__).resolve().parents[5]
    / "rules" / "deep_agent" / "profile.yaml"
)


def _load_rules() -> tuple[str, str]:
    data = yaml.safe_load(_RULES_PATH.read_text(encoding="utf-8"))
    return data["system_prompt"], data["user_template"]


SYSTEM_PROMPT, _USER_TEMPLATE = _load_rules()


def _build_tools(root: Path):

    @tool
    def grep(pattern: str, path_glob: str = "") -> str:
        """코드베이스 전체에서 정규식 매치. 반환: `file:line  라인` (최대 80 줄)."""
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            return f"INVALID_REGEX: {exc}"
        results: list[str] = []
        # 상대 패턴만 허용. 선행 '/' 나 프로젝트 루트 경로 제거.
        glob_pat = (path_glob or "*").lstrip("/\\")
        try:
            candidates = root.rglob(glob_pat)
        except (NotImplementedError, ValueError) as exc:
            return f"INVALID_GLOB: '{path_glob}' ({exc})"
        for p in candidates:
            if not p.is_file():
                continue
            if any(part in SKIP_DIRS for part in p.parts):
                continue
            try:
                with p.open("r", encoding="utf-8", errors="replace") as fh:
                    for i, line in enumerate(fh, 1):
                        if regex.search(line):
                            try:
                                rel = p.relative_to(root).as_posix()
                            except ValueError:
                                rel = str(p)
                            results.append(f"{rel}:{i}  {line.rstrip()[:200]}")
                            if len(results) >= 80:
                                break
            except OSError:
                continue
            if len(results) >= 80:
                break
        return "\n".join(results) if results else "(매치 없음)"

    @tool
    def read_file(path: str, max_lines: int = 200) -> str:
        """파일 앞쪽 max_lines 줄을 반환. 상대 경로는 프로젝트 루트 기준."""
        raw = (path or "").strip().replace("\\", "/")
        if len(raw) >= 3 and raw.startswith("/") and raw[2] == ":":
            raw = raw[1:]
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = root / raw
        if not candidate.exists() or not candidate.is_file():
            return f"FILE_NOT_FOUND: '{path}'"
        try:
            with candidate.open("r", encoding="utf-8", errors="replace") as fh:
                lines = [next(fh, None) for _ in range(max_lines)]
        except OSError as exc:
            return f"READ_ERROR: {exc}"
        text = "".join(ln for ln in lines if ln is not None)
        return text or "(빈 파일)"

    @tool
    def read_function(file_path: str, function_name: str) -> str:
        """소스 파일에서 함수 1 개 본문 (tree-sitter). 큰 함수는 앞부분만."""
        raw = (file_path or "").strip().replace("\\", "/")
        if len(raw) >= 3 and raw.startswith("/") and raw[2] == ":":
            raw = raw[1:]
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = root / raw
        if not candidate.exists():
            return f"FILE_NOT_FOUND: '{file_path}'"
        body, n_tokens, truncated = extract_function_body(
            candidate, function_name, max_tokens=2000,
        )
        if not body:
            return f"FUNCTION_NOT_FOUND: '{function_name}' in '{candidate}'"
        suffix = f"\n[잘림: 첫 ~{n_tokens} tokens]" if truncated else ""
        return body + suffix

    @tool
    def glob_search(pattern: str, path: str = "") -> str:
        """glob 패턴으로 파일 검색. 예: '**/*.xml', 'src/**/*.java'. 상대 경로는 프로젝트 루트 기준."""
        base = root / path.strip().replace("\\", "/").lstrip("/") if path.strip() else root
        if not base.exists() or not base.is_dir():
            return f"DIR_NOT_FOUND: '{path}'"
        glob_pat = pattern.strip().lstrip("/\\")
        results: list[str] = []
        try:
            for p in base.rglob(glob_pat):
                if not p.is_file():
                    continue
                if any(part in SKIP_DIRS for part in p.parts):
                    continue
                try:
                    results.append(p.relative_to(root).as_posix())
                except ValueError:
                    results.append(str(p))
                if len(results) >= 200:
                    break
        except (NotImplementedError, ValueError) as exc:
            return f"INVALID_GLOB: '{pattern}' ({exc})"
        return "\n".join(results) if results else "(매치 없음)"

    @tool
    def ls(path: str = "") -> str:
        """디렉토리 내용을 반환. 상대 경로는 프로젝트 루트 기준. 파일/디렉토리 구분 표시."""
        raw = (path or "").strip().replace("\\", "/").lstrip("/")
        target = root / raw if raw else root
        if not target.exists() or not target.is_dir():
            return f"DIR_NOT_FOUND: '{path}'"
        entries: list[str] = []
        try:
            for item in sorted(target.iterdir()):
                if item.name.startswith("."):
                    continue
                if item.name in SKIP_DIRS:
                    continue
                rel = item.relative_to(root).as_posix()
                prefix = "d " if item.is_dir() else "f "
                entries.append(f"{prefix}{rel}")
        except OSError as exc:
            return f"READ_ERROR: {exc}"
        return "\n".join(entries[:200]) if entries else "(빈 디렉토리)"

    return [grep, read_file, read_function, glob_search, ls]


# ─────────────────────────────────────────────────────────────────────
# Block 3 (반복 네이밍/이디엄) 근거용 언어 중립 시그널 수집 헬퍼
# 모두 정규식 기반 — 특정 언어/프레임워크에 의존하지 않음.
# ─────────────────────────────────────────────────────────────────────

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# 카멜케이스·스네이크케이스 모두 분리: "processOrder" / "process_order" / "APIToken" 등.
_TOKEN_SPLIT_RE = re.compile(r"[_\s]+|(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")

# 함수 정의 라인 휴리스틱: 들여쓰기 허용, `(` 가 있고 `;` 로 끝나지 않음 (선언만은 제외).
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
        if n < 2:  # 노이즈 차단 — 2회 미만은 이디엄 아님
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
    except OSError:
        return None
    return src.splitlines()


def _is_func_definition(lines: list[str], i: int) -> bool:
    """정의인지 호출인지 언어 중립 휴리스틱으로 판별.

    정의로 간주 조건:
      - 같은 줄 또는 다음 줄에 본문 시작 `{` 존재 (C/C++/Java/JS/Go/Rust/C#)
      - 또는 줄 끝이 `:` (Python/Ruby)
    """
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
    """파일별 import 원본이 없으면 전체 빈도 상위만 표시. 진짜 조합 분석은 후속."""
    if not import_frequency:
        return "(표본 부족)"
    items = sorted(import_frequency.items(), key=lambda kv: -kv[1])[:10]
    return ", ".join(f"{k}({v})" for k, v in items)


# ─────────────────────────────────────────────────────────────────────


def _render_prompt(root: Path, info: CodebaseInfo, resources: dict[str, list[str]]) -> str:
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

    return _USER_TEMPLATE.format(
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
            (?:block\s*)?([123])[\.\:\-\s]   # Block 1/2/3 (구 포맷)
          | (프레임워크\s*패턴)               # 프레임워크 패턴
          | (DB\s*접근\s*패턴)                # DB 접근 패턴
        )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _split_sections(text: str) -> dict[str, str]:
    """LLM 텍스트에서 섹션 분리. 실패 시 빈 dict (호출부가 전체 summary 폴백).

    키: 'framework' / 'data_access' / 'idioms'.
    2블록 포맷('프레임워크 패턴'/'DB 접근 패턴') 과 구 3블록 Block 1/2/3 모두 처리.
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


def run_profile_agent(
    root: Path,
    info: CodebaseInfo,
    resources: dict[str, list[str]],
    api_key: str = "",
) -> tuple[str, dict[str, str], int]:
    """Profile Agent 실행. (final_text, summary_sections, tool_calls_count) 반환.

    summary_sections 는 Block 1/2/3 파싱 결과. 파싱 실패 시 빈 dict (폴백).
    """
    llm = get_llm(api_key=api_key or settings.llm.api_key, model=settings.llm.model)
    tools = _build_tools(root)

    middleware = [
        TodoListMiddleware(),
        ContextEditingMiddleware(edits=[
            ClearToolUsesEdit(trigger=70_000, keep=3),
        ]),
        SummarizationMiddleware(model=llm, trigger=("tokens", 85_000)),
        # ToolCallLimitMiddleware(run_limit=50),  # 제한 해제
        # ModelCallLimitMiddleware(run_limit=25, thread_limit=200),  # 제한 해제
        PatchToolCallsMiddleware(),
    ]
    agent = create_agent(
        model=llm,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        middleware=middleware,
    )

    user_msg = _render_prompt(root, info, resources)
    logger.info("[PHASE:1:PROFILE] Agent 시작")
    result = agent.invoke({"messages": [{"role": "user", "content": user_msg}]})

    final_text = _extract_final_text(result)
    sections = _split_sections(final_text)
    tool_calls = _count_tool_calls(result)
    logger.info(
        "[PHASE:1:PROFILE] Agent 완료 | tool_calls=%d sections=%d",
        tool_calls, len(sections),
    )
    return final_text, sections, tool_calls
