"""step 산출물 ``logs/analysis.run/latest/artifacts/`` 적재.

파일 정책 (Plan §6.3 logs 구조):

- ``ast.json``                — step1_parse_ddl + step2_load_ast 결과
                                (Schema/Table/Column/Function/Module/Package/Variable
                                /Constant/DataType + 모든 구조·참조 엣지 + raw call_sites
                                / import_refs)
- ``linking.json``            — step4_linking 결과 (R/W / Calls / unresolved / scan)
- ``analysis_contexts.json``  — step5_build_context 결과 (AnalysisContext per id)
- ``analysis.json``           — step6_analyze_function + step7_post_processing 결과
                                (Function summary / Module summary)
- ``graph_extras.json``       — ★ 위 4 분류에 매핑 안 된 state 키 자동 dump
                                (새 키 추가 시 누락 사각지대 차단 — 단일 진실)

영역 규칙 — 새 state 키 추가 시
─────────────────────────────────
``_CATEGORY`` 매핑에 키 → artifact 명을 추가한다. 안 추가하면 자동으로
``graph_extras.json`` 에 dump 되므로 검증 사각지대는 0. 단 의미상 어느 분류인지
명시는 영역 규칙으로 강제 (의도 명확성).

워크플로우 종료 후 ``dump_state_artifacts(run, final_state)`` 1회 호출.
"""
from __future__ import annotations

import dataclasses
import json
from dataclasses import is_dataclass
from pathlib import Path
from typing import Any

from data_models.analysis_run import AnalysisRun
from shared.log.logger import log_warning
from shared.workflow.state import WorkflowState


_LOG = "shared.workflow.artifact_writer"


def _artifacts_dir(run: AnalysisRun) -> Path:
    d = run.run_dir / "artifacts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def dump_artifact(run: AnalysisRun, name: str, data: Any) -> None:
    """``artifacts/<name>.json`` 에 dataclass / dict 직렬화."""
    serialized = _serialize(data)
    path = _artifacts_dir(run) / f"{name}.json"
    path.write_text(
        json.dumps(serialized, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _serialize(obj: Any) -> Any:
    """재귀 직렬화 — dataclass / Pydantic / dict / list / Path."""
    if is_dataclass(obj):
        return _dataclass_to_dict(obj)
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_serialize(x) for x in obj]
    if isinstance(obj, Path):
        return str(obj)
    return obj


def _dataclass_to_dict(obj: Any) -> dict:
    """dataclass → dict. 모든 필드 + 화이트리스트 ``@property`` 를 ``_serialize`` 재귀.

    ``metadata={"vector": True}`` 표시된 필드는 길이만 (``{"dim": N}``) — JSON 폭증 회피.
    실제 벡터는 Neo4j property 에 박혀 있고, artifact 검증은 dim 비교로 충분.

    ``@property`` 정책 — ``fqn`` 같은 derived 식별자는 artifact 검증/디버깅의 핵심이라
    화이트리스트 ``_DUMP_PROPERTIES`` 로 명시 dump. 그 외 derived property 는 dump 제외
    (artifact 노이즈 차단).
    """
    result: dict[str, Any] = {}
    for f in dataclasses.fields(obj):
        val = getattr(obj, f.name)
        if _is_vector_field(f, val):
            result[f.name] = {"dim": len(val)}
        else:
            result[f.name] = _serialize(val)
    for prop_name in _DUMP_PROPERTIES:
        prop = getattr(type(obj), prop_name, None)
        if isinstance(prop, property):
            try:
                result[prop_name] = _serialize(getattr(obj, prop_name))
            except Exception:  # noqa: BLE001 — property 계산 실패는 dump 만 누락
                pass
    return result


# artifact 에 dump 할 ``@property`` 화이트리스트 — derived 식별자 (그 외 property 는 노이즈라 제외).
_DUMP_PROPERTIES: frozenset[str] = frozenset({"fqn"})


def _is_vector_field(field_def: dataclasses.Field, val: Any) -> bool:
    """dataclass field 의 ``metadata={"vector": True}`` 표시 — 단일 진실.

    새 vector 필드는 dataclass 정의에 metadata 만 박으면 자동 정합 — 여기 if X.
    """
    return field_def.metadata.get("vector") is True and isinstance(val, (list, tuple))


# ── state 키 → artifact 명 매핑 (단일 진실) ──────────────────
# 새 state 키 추가 시 여기에 명시. 명시 안 하면 graph_extras.json 으로 떨어져
# 검증 사각지대는 0 (단 명시는 의도 명확성을 위해 영역 규칙).

_CATEGORY: dict[str, str] = {
    # ast — DDL + 소스 파싱 결과 (구조 + 참조 + raw 단서)
    "schemas":                  "ast",
    "tables":                   "ast",
    "columns":                  "ast",
    "functions":                "ast",   # 함수 전체 (summary 채워진 것은 analysis.json 에도 별도 추출)
    "modules":                  "ast",
    "packages":                 "ast",
    "variables":                "ast",
    "constants":                "ast",
    "data_types":               "ast",
    "has_column_edges":         "ast",
    "belongs_to_edges":         "ast",
    "fk_edges":                 "ast",
    "parent_of_edges":          "ast",
    "has_function_edges":       "ast",
    "has_variable_edges":       "ast",
    "has_member_edges":         "ast",
    "belongs_to_package_edges": "ast",
    "references_edges":         "ast",
    "init_by_edges":            "ast",
    "call_sites":               "ast",   # step2 raw — step4 가 Calls 엣지로 분류
    "import_refs":              "ast",

    # linking — step4 결과
    "reads_writes_edges":       "linking",
    "calls_edges":              "linking",
    "unresolved_cases":         "linking",
    "scan_result":              "linking",

    # contexts — step5 결과 (특별 dict 직렬화)
    "analysis_contexts":        "analysis_contexts",

    # Example Mapping — step6 결과 (Rule/Example/Question + 6 엣지)
    "rules":                    "analysis",
    "examples":                 "analysis",
    "questions":                "analysis",
    "has_rule_edges":           "analysis",
    "has_example_edges":        "analysis",
    "has_question_edges":       "analysis",
    "next_edges":               "analysis",
    "branch_edges":             "analysis",
    "affects_table_edges":      "analysis",

    # 컴포넌트 — step7 build_components 결과 (군집 부품 + 멤버/부품간 엣지)
    "components":               "analysis",
    "component_member_edges":   "analysis",
    "component_calls_edges":    "analysis",

    # run — 실행 메타 (artifact dump 대상 아님)
    "run":                      "_skip",
}


def dump_state_artifacts(run: AnalysisRun, state: dict) -> None:
    """워크플로우 종료 후 state 의 모든 키를 카테고리별로 분류 dump.

    WorkflowState.__annotations__ 를 iterate — 새 키 추가가 자동 검증 대상이 됨.
    매핑 안 된 키는 ``graph_extras.json`` 으로 떨어지고 ``log_warning`` 으로 표면화 (silent
    누락 차단). 카테고리 매핑 (``_CATEGORY``) 에 추가하라는 안내.
    """
    # 카테고리별로 모은다 — list 키는 list 채로, 단일 dict 키는 그대로.
    buckets: dict[str, dict[str, Any]] = {}
    extras: dict[str, Any] = {}

    # state 의 키만 보면 옛 키 누적 사고 가능 — WorkflowState.__annotations__ 가 단일 진실.
    all_keys = set(WorkflowState.__annotations__) | set(state)

    for key in sorted(all_keys):
        if key not in state:
            continue
        category = _CATEGORY.get(key)
        if category == "_skip":
            continue
        value = state[key]
        if category is None:
            # 매핑 누락 — silent 떨어트리지 않고 graph_extras.json 으로 + 경고.
            extras[key] = value
            log_warning(
                _LOG, "unmapped_state_key",
                f"state 키 '{key}' 가 _CATEGORY 매핑에 없음 — graph_extras.json 으로 dump. "
                "artifact_writer._CATEGORY 에 카테고리 명시 필요 (영역 규칙).",
                key=key,
            )
            continue
        buckets.setdefault(category, {})[key] = value

    # analysis.json 은 LLM 분석 결과 관점 — Function summary 채워진 것 + Module 종합 +
    # Example Mapping 9 종을 한 파일로. 자동 카테고리 dump 에서는 분리 처리 (덮어쓰기 회피).
    analysis_payload = buckets.pop("analysis", {})

    for category, payload in buckets.items():
        if category == "analysis_contexts":
            contexts = payload.get("analysis_contexts", {}) or {}
            dump_artifact(run, "analysis_contexts", {
                fid: _serialize(ctx) for fid, ctx in contexts.items()
            })
            continue
        dump_artifact(run, category, payload)

    if extras:
        dump_artifact(run, "graph_extras", extras)

    # analysis.json — Function summary 채워진 것 + Module 종합 + Example Mapping 9 종.
    functions_with_summary = [
        f for f in state.get("functions", []) if getattr(f, "summary", None)
    ]
    analysis_payload.setdefault("functions", functions_with_summary)
    analysis_payload.setdefault("modules", state.get("modules", []))
    dump_artifact(run, "analysis", analysis_payload)
