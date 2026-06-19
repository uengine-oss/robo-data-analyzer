"""호출(call site) 결정론 분류 — 프로젝트 루틴 / stdlib / 프레임워크 / external.

호출엔 "미해결"이 없다. call_text 를 세 목록과 대조해 분류한다 (LLM·에이전트 0):
- 프로젝트 루틴 → ``CALLS{internal}`` (callee 확정). **먼저 검사** — 우리 루틴이
  stdlib 동명에 가려지지 않게.
- stdlib 심볼 → 드롭.
- 프레임워크 루틴(skill 이 알려준 목록) → 드롭.
- **호출 wrapper**(skill 이 알려준 — 인자가 실제 호출 대상) → 라인 본문에서 인자
  추출 후 ``CALLS{internal|external}``. 인자가 code_nodes 의 module_id 와 매치되면
  그 모듈 entry 루틴으로 internal, 매치 안 되면 인자 이름 그대로 external.
- 그 외 → ``CALLS{external}`` (분석 범위 밖 — 호출 사실만 기록).

``framework_code_nodes`` 의 각 항목은 **글로브 패턴** (``fnmatchcase``) 으로 해석.

stdlib 목록 = ``shared/lang/stdlib/{c,java,python}_stdlib.json``.
"""
from __future__ import annotations

import fnmatch
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

from data_models.graph_edges import Calls
from data_models.graph_nodes import CodeNode
from shared.lang.source_extension import EXT_TO_LANG
from shared.source_file_cache import SourceFileCache

_STDLIB_DIR = Path(__file__).resolve().parent / "stdlib"

_ID_SUFFIX = re.compile(r":\d+(?:#\d+)?$")


@dataclass
class CallClassifyResult:
    """``classify_call_sites`` 결과 — 엣지 + 드롭 집계 (조용한 실패 표면화용)."""
    edges: list[Calls] = field(default_factory=list)
    dropped_stdlib: int = 0
    dropped_framework: int = 0
    dropped_incomplete: int = 0
    wrapper_unresolved: int = 0          # wrapper 매치했으나 본문에서 인자 추출 실패


@lru_cache(maxsize=None)
def _stdlib_set(lang: str) -> frozenset[str]:
    """언어별 stdlib 심볼 집합 — 1회 로딩 캐시. 목록 파일 없으면 빈 집합."""
    path = _STDLIB_DIR / f"{lang}_stdlib.json"
    if not path.is_file():
        return frozenset()
    return frozenset(json.loads(path.read_text(encoding="utf-8")))


def _lang_of(file_path: str) -> str:
    return EXT_TO_LANG.get(Path(file_path).suffix.lstrip(".").lower(), "")


def _bare_name(node_id: str) -> str:
    return _ID_SUFFIX.sub("", node_id).rsplit(".", 1)[-1]


def _should_drop_as_framework(name: str, patterns: Iterable[str]) -> bool:
    """이 호출 이름을 호출 그래프에서 드롭할 것인지 — framework 패턴 매칭.

    True 면 그래프에 박지 않음 (PFM_TRY / PRINT_* 같은 디버그·런타임 매크로).
    False 면 다음 분류 단계 (external) 로 진행.

    패턴은 glob — ``PRINT_*`` 한 줄이 시리즈 23 개 다 드롭.
    """
    for pat in patterns:
        if fnmatch.fnmatchcase(name, pat):
            return True
    return False


# ── wrapper 인자 추출 — callee_from 별 정규식 (단일 진실) ──

def _wrapper_arg_pattern(wrapper: str, callee_from: str) -> Optional[re.Pattern]:
    """``callee_from`` 종류에 따라 wrapper 호출 본문에서 인자를 추출하는 정규식.

    지원 callee_from:
    - ``first_arg_string``: 첫 인자가 큰따옴표 문자열 — 예: ``wrap("name", ...)``
    - ``first_arg_identifier``: 첫 인자가 식별자 — 예: ``wrap(name, ...)``

    미지원 callee_from → None (호출자가 wrapper_unresolved 카운트).
    """
    w = re.escape(wrapper)
    if callee_from == "first_arg_string":
        return re.compile(rf"{w}\s*\(\s*\"([^\"]+)\"")
    if callee_from == "first_arg_identifier":
        return re.compile(rf"{w}\s*\(\s*([A-Za-z_]\w*)")
    return None


def _extract_wrapper_callee(
    file_path: str, line: int, pattern: re.Pattern, file_cache: SourceFileCache,
) -> Optional[str]:
    """``file_path`` 의 ``line`` 본문에서 wrapper 정규식으로 인자 추출 — 없으면 None."""
    lines = file_cache.lines(file_path)
    if not lines or line < 1 or line > len(lines):
        return None
    m = pattern.search(lines[line - 1])
    return m.group(1).strip() if m else None


def classify_call_sites(
    call_sites: list[dict],
    code_nodes: list[CodeNode],
    framework_names: frozenset[str] = frozenset(),
    wrappers: Iterable[dict] = (),
) -> CallClassifyResult:
    """call_sites 를 결정론 분류 → ``Calls`` 엣지 + 드롭 집계.

    Args:
        call_sites: ``[{call_id, caller_id, call_text, file, line}, ...]``.
        code_nodes: 같은 프로젝트 CodeNode 노드 — internal 매칭 대상.
        framework_names: 프레임워크 루틴 패턴 집합 (skill 출처) — glob 허용.
        wrappers: 호출 wrapper 명시 — ``[{wrapper, callee_from}, ...]``. SKILL 정의.
                  wrapper 호출은 라인 본문에서 인자 추출 후 분류 — 모듈 entry 루틴과
                  매치되면 internal, 아니면 external.
    """
    by_name: dict[str, list[str]] = defaultdict(list)
    module_of: dict[str, str] = {}
    for fn in code_nodes:
        by_name[_bare_name(fn.id)].append(fn.id)
        module_of[fn.id] = fn.owner_id

    # wrapper 인자 → 진짜 호출 대상 분류용: 모듈 id → entry 루틴 id 매핑.
    # entry = id 가 "<owner_id>.<owner_id>" 형태인 루틴 (모듈 엔트리 표준).
    # 같은 모듈의 entry 가 없으면 그 모듈 루틴 중 정렬상 첫 후보 (단순화).
    entry_by_module: dict[str, str] = {}
    other_by_module: dict[str, list[str]] = defaultdict(list)
    for fn in code_nodes:
        if not fn.owner_id:
            continue
        if fn.id == f"{fn.owner_id}.{fn.owner_id}":
            entry_by_module[fn.owner_id] = fn.id
        else:
            other_by_module[fn.owner_id].append(fn.id)

    def _wrapper_callee_to_internal(callee_name: str) -> Optional[str]:
        """wrapper 인자(이름) → internal 루틴 id 또는 None (external)."""
        if callee_name in entry_by_module:
            return entry_by_module[callee_name]
        siblings = other_by_module.get(callee_name)
        if siblings:
            return sorted(siblings)[0]
        return None

    wrapper_patterns: dict[str, re.Pattern] = {}
    for w in wrappers:
        name = w.get("wrapper") if isinstance(w, dict) else getattr(w, "wrapper", None)
        callee_from = w.get("callee_from") if isinstance(w, dict) else getattr(w, "callee_from", "first_arg_string")
        if not name:
            continue
        pat = _wrapper_arg_pattern(name, callee_from or "first_arg_string")
        if pat is not None:
            wrapper_patterns[name] = pat

    file_cache = SourceFileCache() if wrapper_patterns else None

    result = CallClassifyResult()
    for cs in call_sites:
        name = (cs.get("call_text") or "").strip()
        caller = cs.get("caller_id", "")
        if not name or not caller:
            result.dropped_incomplete += 1
            continue

        callee = _resolve_internal(name, caller, by_name, module_of)
        if callee is not None:
            result.edges.append(Calls(
                caller_id=caller, callee_id=callee, scope="internal",
            ))
            continue

        # wrapper 매치 — 라인 본문에서 인자 추출 후 internal/external 분류.
        if name in wrapper_patterns and file_cache is not None:
            file_path = cs.get("file", "")
            line = int(cs.get("line", 0) or 0)
            extracted = _extract_wrapper_callee(
                file_path, line, wrapper_patterns[name], file_cache,
            )
            if not extracted:
                result.wrapper_unresolved += 1
                continue
            internal_id = _wrapper_callee_to_internal(extracted)
            if internal_id is not None:
                result.edges.append(Calls(
                    caller_id=caller, callee_id=internal_id, scope="internal",
                ))
            else:
                result.edges.append(Calls(
                    caller_id=caller, callee_id=extracted, scope="external",
                ))
            continue

        if name in _stdlib_set(_lang_of(cs.get("file", ""))):
            result.dropped_stdlib += 1
        elif _should_drop_as_framework(name, framework_names):
            result.dropped_framework += 1
        else:
            result.edges.append(Calls(
                caller_id=caller, callee_id=name, scope="external",
            ))
    return result


def _resolve_internal(
    name: str,
    caller: str,
    by_name: dict[str, list[str]],
    module_of: dict[str, str],
) -> str | None:
    """call_text → 프로젝트 루틴 code_id. 프로젝트 루틴 아니면 None.

    동명 루틴 다수면 호출한 루틴과 같은 모듈 후보 우선 (C ``static`` 은 파일 스코프).
    """
    matches = by_name.get(name)
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    same_module = [m for m in matches if module_of.get(m) == module_of.get(caller)]
    return sorted(same_module)[0] if same_module else sorted(matches)[0]
