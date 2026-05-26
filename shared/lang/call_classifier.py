"""호출(call site) 결정론 분류 — 프로젝트 함수 / stdlib / 프레임워크 / external.

호출엔 "미해결"이 없다. call_text 를 세 목록과 대조해 분류한다 (LLM·에이전트 0):
- 프로젝트 함수 → ``CALLS{internal}`` (callee 확정). **먼저 검사** — 우리 함수가
  stdlib 동명에 가려지지 않게.
- stdlib 심볼 → 드롭.
- 프레임워크 함수(skill 이 알려준 목록) → 드롭.
- 그 외 → ``CALLS{external}`` (분석 범위 밖 — 호출 사실만 기록).

stdlib 목록 = ``shared/lang/stdlib/{c,java,python}_stdlib.json``.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from data_models.graph_edges import Calls
from data_models.graph_nodes import Function
from shared.lang.source_ext import EXT_TO_LANG

_STDLIB_DIR = Path(__file__).resolve().parent / "stdlib"

# function_id 끝의 _unique_id 충돌 접미 (``:line`` / ``:line#n``).
_ID_SUFFIX = re.compile(r":\d+(?:#\d+)?$")


@dataclass
class CallClassifyResult:
    """``classify_call_sites`` 결과 — 엣지 + 드롭 집계 (조용한 실패 표면화용)."""
    edges: list[Calls] = field(default_factory=list)
    dropped_stdlib: int = 0
    dropped_framework: int = 0
    dropped_incomplete: int = 0          # call_text / caller 누락 — AST 불완전


@lru_cache(maxsize=None)
def _stdlib_set(lang: str) -> frozenset[str]:
    """언어별 stdlib 심볼 집합 — 1회 로딩 캐시. 목록 파일 없으면 빈 집합."""
    path = _STDLIB_DIR / f"{lang}_stdlib.json"
    if not path.is_file():
        return frozenset()
    return frozenset(json.loads(path.read_text(encoding="utf-8")))


def _lang_of(file_path: str) -> str:
    """파일 경로 확장자 → 언어 (미지원 확장자는 빈 문자열)."""
    return EXT_TO_LANG.get(Path(file_path).suffix.lstrip(".").lower(), "")


def _bare_name(function_id: str) -> str:
    """function_id → 함수 이름 (``_unique_id`` 충돌 접미 제거 후 마지막 세그먼트)."""
    return _ID_SUFFIX.sub("", function_id).rsplit(".", 1)[-1]


def classify_call_sites(
    call_sites: list[dict],
    functions: list[Function],
    framework_names: frozenset[str] = frozenset(),
) -> CallClassifyResult:
    """call_sites 를 결정론 분류 → ``Calls`` 엣지 + 드롭 집계.

    Args:
        call_sites: ``[{call_id, caller_function_id, call_text, file, line}, ...]``.
        functions: 같은 프로젝트 Function 노드 — internal 매칭 대상.
        framework_names: 프레임워크 함수 이름 집합 (skill 출처). 비면 framework 드롭 없음.

    Returns:
        ``CallClassifyResult`` — ``edges`` 는 internal/external 확정분.
    """
    by_name: dict[str, list[str]] = defaultdict(list)
    module_of: dict[str, str] = {}
    for fn in functions:
        by_name[_bare_name(fn.function_id)].append(fn.function_id)
        module_of[fn.function_id] = fn.module_id

    result = CallClassifyResult()
    for cs in call_sites:
        name = (cs.get("call_text") or "").strip()
        caller = cs.get("caller_function_id", "")
        if not name or not caller:
            result.dropped_incomplete += 1
            continue

        callee = _resolve_internal(name, caller, by_name, module_of)
        if callee is not None:
            result.edges.append(Calls(
                caller_function_id=caller, callee_function_id=callee, scope="internal",
            ))
        elif name in _stdlib_set(_lang_of(cs.get("file", ""))):
            result.dropped_stdlib += 1
        elif name in framework_names:
            result.dropped_framework += 1
        else:
            result.edges.append(Calls(
                caller_function_id=caller, callee_function_id=name, scope="external",
            ))
    return result


def _resolve_internal(
    name: str,
    caller: str,
    by_name: dict[str, list[str]],
    module_of: dict[str, str],
) -> str | None:
    """call_text → 프로젝트 함수 function_id. 프로젝트 함수 아니면 None.

    동명 함수 다수면 호출한 함수와 같은 모듈 후보 우선 (C ``static`` 은 파일 스코프).
    그래도 못 가르면 정렬상 첫 후보 — 우리 함수인 건 확실하므로 internal 유지.
    """
    matches = by_name.get(name)
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    same_module = [m for m in matches if module_of.get(m) == module_of.get(caller)]
    return sorted(same_module)[0] if same_module else sorted(matches)[0]
