"""호출(call site) 결정론 분류 — 프로젝트 루틴 / stdlib / 프레임워크 / external.

Parser의 active named/constructor occurrence만 이름 목록과 대조한다(LLM·에이전트 0).
inactive는 보존하고 conditional/expression/owner·wrapper 증거 부족은 명시 unresolved다.
- 프로젝트 루틴 → ``CALLS{internal}`` (callee 확정). **먼저 검사** — 우리 루틴이
  stdlib 동명에 가려지지 않게.
- stdlib 심볼 → 드롭.
- 프레임워크 루틴(skill 이 알려준 목록) → 드롭.
- **호출 wrapper**(skill 이 알려준 — 인자가 실제 호출 대상) → call occurrence는 보존하되
  literal/symbol evidence가 아직 없으면 target을 만들지 않고 explicit unresolved로 회계.
- 그 외 → ``CALLS{external}`` (분석 범위 밖 — 호출 사실만 기록).

``framework_runtime_functions`` 의 각 항목은 **글로브 패턴** (``fnmatchcase``) 으로 해석.

stdlib 목록 = 인접 ``stdlib/{c,java,python}_stdlib.json``.
"""
from __future__ import annotations

import fnmatch
import json
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from graph.schema.graph_edges import Calls
from graph.schema.graph_nodes import CodeNode
from pipeline.languages import EXT_TO_LANG, statement_labels

_STDLIB_DIR = Path(__file__).resolve().parent / "stdlib"



@dataclass
class CallClassifyResult:
    """``classify_call_sites`` 결과 — 엣지 + 드롭 집계 (조용한 실패 표면화용)."""
    edges: list[Calls] = field(default_factory=list)
    dropped_stdlib: int = 0
    dropped_framework: int = 0
    dropped_macro: int = 0
    preserved_inactive: int = 0
    unresolved_presence: int = 0
    unresolved_expression: int = 0
    unresolved_owner: int = 0
    unresolved_macro: int = 0
    wrapper_unresolved: int = 0
    unresolved: list[dict[str, str]] = field(default_factory=list)
    ledger: list[dict[str, Any]] = field(default_factory=list)

    @property
    def accounted(self) -> int:
        return (
            len(self.edges)
            + self.dropped_stdlib
            + self.dropped_framework
            + self.dropped_macro
            + self.preserved_inactive
            + self.unresolved_presence
            + self.unresolved_expression
            + self.unresolved_owner
            + self.unresolved_macro
            + self.wrapper_unresolved
        )


class CallClassificationInvariantError(RuntimeError):
    """Canonical call occurrence/accounting or target resolution invariant failed."""


@lru_cache(maxsize=None)
def _stdlib_set(lang: str) -> frozenset[str]:
    """언어별 stdlib 심볼 집합 — 1회 로딩 캐시. 목록 파일 없으면 빈 집합."""
    path = _STDLIB_DIR / f"{lang}_stdlib.json"
    if not path.is_file():
        return frozenset()
    return frozenset(json.loads(path.read_text(encoding="utf-8")))


def _lang_of(file_path: str) -> str:
    return EXT_TO_LANG.get(Path(file_path).suffix.lstrip(".").lower(), "")


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


def classify_call_sites(
    call_sites: list[dict],
    code_nodes: list[CodeNode],
    framework_names: frozenset[str] = frozenset(),
    wrappers: Iterable[dict] = (),
    macro_facts: Iterable[dict] = (),
) -> CallClassifyResult:
    """call_sites 를 결정론 분류 → ``Calls`` 엣지 + 드롭 집계.

    Args:
        call_sites: ``[{call_id, caller_id, call_text, file, line}, ...]``.
        code_nodes: 같은 프로젝트 CodeNode 노드 — internal 매칭 대상.
        framework_names: 프레임워크 루틴 패턴 집합 (skill 출처) — glob 허용.
        wrappers: 호출 wrapper 명시 — ``[{wrapper, callee_from}, ...]``. SKILL 정의.
                  argument literal/symbol fact가 구현되기 전에는 target을 추측하지 않는다.
    """
    # statement 노드(GOTO 라벨 등)는 호출 대상이 아니다 — `goto PFM_CATCH;` 의 GOTO
    # 노드가 PFM_CATCH() 호출의 internal 대상으로 오해석되던 실측 결함(2026-08-05).
    stmt_labels = statement_labels()
    callables = [fn for fn in code_nodes if fn.label not in stmt_labels]
    defined_names = {(fn.name or "").strip() for fn in callables if (fn.name or "").strip()}
    by_name: dict[str, list[str]] = defaultdict(list)
    module_of: dict[str, str] = {}
    for fn in callables:
        name = (fn.name or "").strip()
        if not name:
            continue
        by_name[name].append(fn.id)
        module_of[fn.id] = fn.owner_id

    wrapper_names: set[str] = set()
    for w in wrappers:
        name = w.get("wrapper") if isinstance(w, dict) else getattr(w, "wrapper", None)
        if not name:
            continue
        wrapper_names.add(str(name))

    # Parser grammar evidence only; Analyzer never reads/reparses source for macros.
    function_macros = _function_macro_candidates(macro_facts)

    result = CallClassifyResult()
    ids = [str(site.get("call_id") or "") for site in call_sites]
    if any(not fact_id for fact_id in ids) or len(ids) != len(set(ids)):
        raise CallClassificationInvariantError(
            "call occurrence ledger requires unique nonblank canonical call_id"
        )
    for cs in call_sites:
        call_id = str(cs["call_id"])
        presence = cs.get("presence_status")
        if presence == "inactive":
            result.preserved_inactive += 1
            result.ledger.append({
                "call_id": call_id,
                "outcome": "preserved",
                "reason": "inactive_presence",
            })
            continue
        if presence in {"conditional", "unknown"}:
            result.unresolved_presence += 1
            row = {
                "call_id": call_id,
                "outcome": "unresolved",
                "reason": "insufficient_conditional_presence",
            }
            result.unresolved.append({key: row[key] for key in ("call_id", "reason")})
            result.ledger.append(row)
            continue
        if presence != "active":
            raise CallClassificationInvariantError(
                f"call {call_id} has invalid/missing presence_status={presence!r}"
            )
        callee_kind = cs.get("callee_kind")
        if callee_kind == "expression":
            result.unresolved_expression += 1
            row = {
                "call_id": call_id,
                "outcome": "unresolved",
                "reason": "insufficient_expression_target_resolution",
            }
            result.unresolved.append({key: row[key] for key in ("call_id", "reason")})
            result.ledger.append(row)
            continue
        if callee_kind not in {"named", "constructor"}:
            raise CallClassificationInvariantError(
                f"call {call_id} has invalid/missing callee_kind={callee_kind!r}"
            )
        name = (cs.get("call_text") or "").strip()
        caller = cs.get("caller_id", "")
        if not name:
            raise CallClassificationInvariantError(
                f"call {call_id} has no grammar-owned terminal name"
            )
        if not caller:
            result.unresolved_owner += 1
            row = {
                "call_id": call_id,
                "outcome": "unresolved",
                "reason": "insufficient_framework_routine_owner",
            }
            result.unresolved.append({key: row[key] for key in ("call_id", "reason")})
            result.ledger.append(row)
            continue

        # A declaration does not prove the effective macro state at this call.
        # Ordered #undef/redefinition/include events and build defines remain Parser
        # responsibilities.  Preserve that uncertainty before claiming any edge.
        macro_candidates = function_macros.get(
            (str(cs.get("source_id") or ""), name), (),
        )
        effective_candidates = [
            fact for fact in macro_candidates
            if fact["presence_status"] != "inactive"
        ]
        if effective_candidates:
            result.unresolved_macro += 1
            reason = (
                "insufficient_conditional_macro_definition"
                if any(
                    fact["presence_status"] in {"conditional", "unknown"}
                    for fact in effective_candidates
                )
                else "insufficient_macro_expansion_state"
            )
            row = {
                "call_id": call_id,
                "outcome": "unresolved",
                "reason": reason,
                "macro_fact_ids": sorted(
                    str(fact["fact_id"]) for fact in effective_candidates
                ),
            }
            result.unresolved.append({
                key: row[key] for key in ("call_id", "reason")
            })
            result.ledger.append(row)
            continue

        callee = _resolve_internal(name, caller, by_name, module_of)
        if callee is not None:
            result.edges.append(Calls(
                caller_id=caller,
                callee_id=callee,
                scope="internal",
                recursive=caller == callee,
            ))
            result.ledger.append({
                "call_id": call_id,
                "outcome": "edge",
                "scope": "internal",
                "callee_id": callee,
            })
            continue

        # Wrapper target is encoded in an argument expression.  Until Parser literal/
        # symbol evidence binds that argument, source-line regex would be a second parser.
        if name in wrapper_names:
            result.wrapper_unresolved += 1
            row = {
                "call_id": call_id,
                "outcome": "unresolved",
                "reason": "insufficient_wrapper_argument_syntax_evidence",
            }
            result.unresolved.append({key: row[key] for key in ("call_id", "reason")})
            result.ledger.append(row)
            continue

        file_path = str(cs.get("file", ""))
        if name in _stdlib_set(_lang_of(file_path)):
            result.dropped_stdlib += 1
            result.ledger.append({
                "call_id": call_id, "outcome": "excluded", "reason": "stdlib",
            })
        elif _should_drop_as_framework(name, framework_names):
            result.dropped_framework += 1
            result.ledger.append({
                "call_id": call_id, "outcome": "excluded", "reason": "framework_runtime",
            })
        else:
            result.edges.append(Calls(
                caller_id=caller, callee_id=name, scope="external",
            ))
            result.ledger.append({
                "call_id": call_id,
                "outcome": "edge",
                "scope": "external",
                "callee_id": name,
            })
    leaked = [
        edge for edge in result.edges
        if edge.scope == "external" and edge.callee_id in defined_names
    ]
    if leaked:
        details = ", ".join(
            f"caller={edge.caller_id!r} callee={edge.callee_id!r}"
            for edge in leaked[:10]
        )
        raise CallClassificationInvariantError(
            "workspace-defined functions were classified as external: " + details
        )
    if result.accounted != len(call_sites):
        raise CallClassificationInvariantError(
            f"call occurrence accounting mismatch: input={len(call_sites)}, "
            f"accounted={result.accounted}"
        )
    ledger_ids = [str(row.get("call_id") or "") for row in result.ledger]
    if ledger_ids != ids or len(ledger_ids) != len(set(ledger_ids)):
        raise CallClassificationInvariantError(
            "call occurrence result ledger must preserve every canonical ID exactly once"
        )
    return result


def _function_macro_candidates(
    macro_facts: Iterable[dict],
) -> dict[tuple[str, str], tuple[dict, ...]]:
    """Index validated grammar declarations without interpreting source text."""
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    seen: set[str] = set()
    for fact in macro_facts:
        if not isinstance(fact, dict):
            raise CallClassificationInvariantError("macro fact object required")
        fact_id = str(fact.get("fact_id") or "")
        source_id = str(fact.get("source_id") or "")
        name = str(fact.get("terminal_name") or "")
        kind = fact.get("macro_kind")
        presence = fact.get("presence_status")
        if not fact_id or fact_id in seen:
            raise CallClassificationInvariantError(
                "macro evidence requires unique nonblank canonical fact_id"
            )
        if not source_id or not name or kind not in {"object", "function"}:
            raise CallClassificationInvariantError(
                f"macro {fact_id} has invalid canonical identity/kind"
            )
        if presence not in {"active", "inactive", "conditional", "unknown"}:
            raise CallClassificationInvariantError(
                f"macro {fact_id} has invalid presence_status={presence!r}"
            )
        seen.add(fact_id)
        if kind == "function":
            grouped[(source_id, name)].append(fact)
    return {
        key: tuple(sorted(items, key=lambda item: str(item["fact_id"])))
        for key, items in grouped.items()
    }


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
