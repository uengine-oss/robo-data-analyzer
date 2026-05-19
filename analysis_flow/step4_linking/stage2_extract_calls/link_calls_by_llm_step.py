"""stage2_extract_calls — Framework Call LLM 배치.

흐름:
1. 함수 본문에서 call site 수집 — tree-sitter AST query (정확) + 정규식 보강.
2. ``CallLLMBatcher`` 가 batch_size 단위 LLM 호출 → 분류 + callee.
3. ``Calls`` 엣지 Neo4j 업서트.
4. ``virtual_dispatch`` / 분류 실패 case → ``unresolved_cases`` (stage3 가 처리).
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Optional

from langchain_core.runnables import RunnableConfig

from analysis_flow.step4_linking.stage2_extract_calls.call_llm_batcher import CallLLMBatcher
from shared.logging_and_stream.logger import log_info, log_warning
from shared.neo4j.edges.calls import upsert_calls
from shared.neo4j.neo4j_client import Neo4jClient

_STEP = "step4.link_calls_by_llm"


# tree-sitter call_expression query (언어별)
_CALL_QUERIES: dict[str, str] = {
    "java":       "(method_invocation) @call",
    "kotlin":     "(call_expression) @call",
    "python":     "(call function: [(identifier) (attribute)]) @call",
    "go":         "(call_expression function: [(identifier) (selector_expression)]) @call",
    "c":          "(call_expression function: [(identifier) (field_expression)]) @call",
    "cpp":        "(call_expression function: [(identifier) (field_expression)]) @call",
    "typescript": "(call_expression function: [(identifier) (member_expression)]) @call",
    "javascript": "(call_expression function: [(identifier) (member_expression)]) @call",
    "ruby":       "(call) @call",
    "rust":       "(call_expression) @call",
}

# 확장자 → 언어 (tree_sitter_parser._EXT_TO_LANG 과 동기화)
_EXT_TO_LANG: dict[str, str] = {
    "java": "java", "kt": "kotlin", "py": "python", "go": "go",
    "c": "c", "h": "c", "cpp": "cpp", "hpp": "cpp", "cc": "cpp",
    "ts": "typescript", "tsx": "typescript", "js": "javascript", "jsx": "javascript",
    "rb": "ruby", "rs": "rust",
}

# 정규식 fallback — tree-sitter 미지원 언어 또는 import 실패 시
_FALLBACK_CALL_RE = re.compile(r"\b([A-Za-z_][\w.$#]*?)\s*\(", re.ASCII)
_NOT_CALLS = {
    "if", "else", "elif", "while", "for", "switch", "case", "return", "throw",
    "catch", "finally", "do", "synchronized", "new",
    "def", "class", "lambda", "yield", "await", "async", "with", "raise",
    "print", "println", "fun", "func",
}

# 폭주 차단 한도
_MAX_CALL_SITES = 10000


async def link_calls_by_llm_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    cfg = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    project = cfg.get("project")
    strategy = cfg.get("strategy")
    cost_budget = cfg.get("cost_budget")
    neo: Neo4jClient | None = cfg.get("neo_client")
    if project is None or strategy is None:
        log_warning(_STEP, "config_missing", "project/strategy 누락 — step skip")
        return {}

    profile_result = state.get("profile_result") or {}
    codebase_notes = profile_result.get("codebase_notes", "")
    functions = state.get("functions", [])
    started = time.monotonic()

    log_info(_STEP, "collecting_call_sites", functions=len(functions))
    call_sites = _collect_call_sites_ast(functions)
    if not call_sites:
        log_info(_STEP, "done", call_sites=0, calls_edges=0, note="no call sites")
        return {"calls_edges": []}

    log_info(_STEP, "start", call_sites=len(call_sites), max_limit=_MAX_CALL_SITES)
    batcher = CallLLMBatcher()
    edges, unresolved = await batcher.link(
        project=project,
        call_sites=call_sites,
        codebase_notes=codebase_notes,
        prompts_dir=Path(strategy.prompts_dir),
        cost_budget=cost_budget,
    )

    if neo is not None and edges:
        await upsert_calls(neo, edges)

    log_info(
        _STEP, "done",
        call_sites=len(call_sites),
        calls_edges=len(edges), unresolved_cases=len(unresolved),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {
        "calls_edges": edges,
        "unresolved_cases": unresolved,
    }


# ── call site 수집 ────────────────────────────────────────


def _collect_call_sites_ast(functions: list) -> list[dict]:
    """tree-sitter AST 로 call site 추출 (정확). 실패 시 정규식 fallback."""
    try:
        from tree_sitter_languages import get_language, get_parser
    except ImportError:
        return _collect_call_sites_regex(functions)

    # 파일별로 1회 파싱 (캐싱)
    file_trees: dict[str, Any] = {}
    file_text: dict[str, bytes] = {}

    sites: list[dict] = []
    seen: set[str] = set()

    for fn in functions:
        if len(sites) >= _MAX_CALL_SITES:
            break
        ext = Path(fn.file_path).suffix.lstrip(".").lower()
        lang = _EXT_TO_LANG.get(ext)
        if lang is None or lang not in _CALL_QUERIES:
            sites.extend(_collect_for_function_regex(fn, seen, _MAX_CALL_SITES - len(sites)))
            continue

        if fn.file_path not in file_trees:
            try:
                parser = get_parser(lang)
                source = Path(fn.file_path).read_bytes()
                file_trees[fn.file_path] = (lang, parser.parse(source))
                file_text[fn.file_path] = source
            except Exception:
                file_trees[fn.file_path] = None

        cached = file_trees.get(fn.file_path)
        if cached is None:
            sites.extend(_collect_for_function_regex(fn, seen, _MAX_CALL_SITES - len(sites)))
            continue

        lang_name, tree = cached
        source = file_text[fn.file_path]
        try:
            language = get_language(lang_name)
            query = language.query(_CALL_QUERIES[lang_name])
            captures = query.captures(tree.root_node)
        except Exception:
            sites.extend(_collect_for_function_regex(fn, seen, _MAX_CALL_SITES - len(sites)))
            continue

        for node, _ in captures:
            line = node.start_point[0] + 1
            if line < fn.start_line or line > fn.end_line:
                continue
            call_text = source[node.start_byte : node.end_byte].decode("utf-8", errors="ignore")
            call_id = f"{fn.function_id}:{line}:{node.start_byte}"
            if call_id in seen:
                continue
            seen.add(call_id)
            sites.append({
                "call_id": call_id,
                "caller_function_id": fn.function_id,
                "call_text": call_text[:200],
                "file": fn.file_path,
                "line": line,
            })
            if len(sites) >= _MAX_CALL_SITES:
                return sites
    return sites


def _collect_call_sites_regex(functions: list) -> list[dict]:
    """tree-sitter 전체 fallback — 모든 함수 정규식."""
    sites: list[dict] = []
    seen: set[str] = set()
    for fn in functions:
        sites.extend(_collect_for_function_regex(fn, seen, _MAX_CALL_SITES - len(sites)))
        if len(sites) >= _MAX_CALL_SITES:
            break
    return sites


def _collect_for_function_regex(fn, seen: set[str], remaining: int) -> list[dict]:
    if remaining <= 0:
        return []
    try:
        text = Path(fn.file_path).read_text(encoding="utf-8", errors="ignore")
    except (OSError, UnicodeDecodeError):
        return []

    lines = text.splitlines()
    body_lines = lines[max(fn.start_line - 1, 0) : fn.end_line]
    out: list[dict] = []
    for line_idx, line in enumerate(body_lines, start=fn.start_line):
        for m in _FALLBACK_CALL_RE.finditer(line):
            name = m.group(1)
            if name in _NOT_CALLS:
                continue
            call_id = f"{fn.function_id}:{line_idx}:{m.start()}"
            if call_id in seen:
                continue
            seen.add(call_id)
            out.append({
                "call_id": call_id,
                "caller_function_id": fn.function_id,
                "call_text": m.group(0),
                "file": fn.file_path,
                "line": line_idx,
            })
            if len(out) >= remaining:
                return out
    return out
