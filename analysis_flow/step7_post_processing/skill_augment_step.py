"""skill_augment_step — 의미 추출을 **외부 CLI 에이전트 + 스킬**에 맡긴다.

`enrich_table_description_step` 의 대안이다. 둘 중 하나만 돈다 (``ROBO_ANALYZE_ENGINE``).

## 왜 대안을 두는가

기존 경로는 함수마다 YAML 프롬프트로 LLM 을 부른다. 함수 52개면 규칙 수만큼 곱해지고,
로컬 모델에서는 호출당 40~90초라 **몇 시간**이 걸린다(2026-08-05 실측). 사용자가
「분석 시작」을 누르고 그만큼 기다리면 매뉴얼의 실습 시나리오가 성립하지 않는다.

스킬 경로는 에이전트에게 작업 폴더와 지시문 하나를 주고, **무엇을 읽을지는 에이전트가 정하게**
한다. 호출 수를 우리가 정하지 않는다.

## 두 엔진을 동시에 두는 이유

spec 007 AD-8 — 새 경로가 옛 경로보다 나은지는 **나란히 돌려 봐야** 안다. 기본값은 옛 경로
(`controller`)로 두고, 환경변수 한 줄로 바꾼다. 되돌리기가 배포가 아니라 설정이어야 한다.

## 쓰기 주체는 그대로 분석기다

에이전트는 JSON 파일 하나만 남기고, Neo4j 에 쓰는 것은 이 단계다. 헌법 원칙 III 의
"writer 는 하나" 를 지키고, 에이전트가 그래프 스키마를 알 필요도 없어진다.
"""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Optional

from langchain_core.runnables import RunnableConfig

from shared.agent.cliagent_runner import AgentUnavailable, run as run_agent
from shared.config.env_catalog import ENV
from shared.logging_and_stream.logger import log_error, log_info, log_warning
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.nodes.column import update_analyzed_descriptions as update_column_analyzed
from shared.neo4j.nodes.table import update_analyzed_descriptions as update_table_analyzed
from shared.workflow_runner.step_config import step_config

_STEP = "step7.skill_augment"

#: 에이전트가 읽을 것들. AST 는 참고용이라 없어도 진행한다.
_INPUTS = ("ddl", "procedures", "analysis")

# 프로시저 조인에서 유추한 관계.
#
# ## 왜 기존 `FK_TO_TABLE` 로 쓰는가
#
# 처음에는 `(:Column)-[:INFERRED_REFERENCES]->(:Column)` 이라는 새 타입을 만들었다.
# "추론과 단언을 섞지 않는다"는 의도는 맞았지만 **아무도 읽지 않았다** — 카탈로그 API 는
# `(:Table)-[r]->(:Table)` 을 읽고 타입 화이트리스트가 `FK_TO_TABLE` 외 4종뿐이라
# 화면에 하나도 나오지 않았다(2026-08-05 실측).
#
# 기록되지 않은 구별은 구별이 아니다. 기존 형태로 쓰고 **`inferred` 속성으로 구별**한다.
# `source='procedure'` 가 화면에서 「코드 분석」(점선·하늘색)으로 그려지게 한다.
_UPSERT_INFERRED_REL = """
UNWIND $items AS item
MATCH (ft:Table)-[:HAS_COLUMN]->(fc:Column)
  WHERE NOT ft:TABLE AND ft.datasource = item.datasource
    AND toLower(ft.name) = toLower(item.from_table)
    AND toLower(fc.name) = toLower(item.from_column)
MATCH (tt:Table)-[:HAS_COLUMN]->(tc:Column)
  WHERE NOT tt:TABLE AND tt.datasource = item.datasource
    AND toLower(tt.name) = toLower(item.to_table)
    AND toLower(tc.name) = toLower(item.to_column)
MERGE (ft)-[r:FK_TO_TABLE {sourceColumn: fc.name, targetColumn: tc.name}]->(tt)
SET r.source = 'procedure',
    r.type = 'many_to_one',
    r.inferred = true,
    r.confidence = item.confidence,
    r.evidence = item.evidence
RETURN count(r) AS created
"""
# `description` 을 채우지 않는다. 캔버스가 그 필드를 **카디널리티 표기(`[1:N]` 등) 감지**에
# 쓰고 엣지 라벨로도 그리므로, 설명 문장을 넣으면 컬럼쌍 라벨 위에 겹쳐 글자가 깨진다
# (2026-08-05 실측: "코드 분석에서 추c010 → c010!된 외래키 아님").
# 추론임은 선 모양(`source='procedure'` → 점선)이 이미 말해 주고, 상세는 `evidence` 에 있다.


def _stage_inputs(run_dir: Path, project_root: Path, ddl_dir: Path | None) -> Path:
    """에이전트가 볼 작업 폴더를 만든다.

    원본 디렉터리를 그대로 주지 않고 사본을 만드는 이유: 에이전트는 파일을 쓸 수 있고,
    다음 단계가 읽을 입력을 건드리면 안 된다. 사본이면 최악의 경우도 이 run 안에서 끝난다.
    """
    work = run_dir / "augment"
    work.mkdir(parents=True, exist_ok=True)
    if ddl_dir and ddl_dir.exists():
        shutil.copytree(ddl_dir, work / "ddl", dirs_exist_ok=True)
    for name in ("procedures", "analysis"):
        src = project_root.parent / name if name == "analysis" else project_root / name
        if src.exists():
            shutil.copytree(src, work / name, dirs_exist_ok=True)
    return work


def _fqn(db: str, schema: str, table: str) -> str:
    return f"{db}.{schema}.{table}"


async def skill_augment_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    cfg = step_config(config)
    neo: Neo4jClient | None = cfg.get("neo_client")
    run = cfg.get("run")
    project = cfg.get("project")
    if neo is None or run is None or project is None:
        log_warning(_STEP, "config_missing", "neo_client/run/project 누락 — step skip")
        return {}

    started = time.monotonic()
    try:
        work = _stage_inputs(Path(run.run_dir), Path(project.root), getattr(project, "ddl_dir", None))
    except Exception as exc:  # 입력 준비 실패는 진단 가능해야 한다
        log_error(_STEP, "stage_failed", exc)
        return {}

    try:
        result = run_agent(work, preferred=os.environ.get("EXTERNAL_AGENT_ID") or None)
    except AgentUnavailable as exc:
        # 조용히 옛 경로로 돌아가지 않는다 — 왜 느린지, 왜 결과가 다른지 알 수 없게 된다.
        log_error(_STEP, "agent_unavailable", exc)
        return {}
    except Exception as exc:
        log_error(_STEP, "agent_failed", exc)
        return {}

    tables = result.get("tables") or []
    relations = result.get("relations") or []

    db = getattr(project, "name", "") or ""
    schema = db  # DDL 파싱이 schema=db 로 넣는다(nodes/table.py 의 item.db)

    # ── 테이블/컬럼 설명 ──
    table_updates, column_updates = [], []
    for t in tables:
        name = str(t.get("name") or "").strip()
        desc = str(t.get("description") or "").strip()
        if not name or not desc:
            continue  # 근거 없이 채우지 않는다 (SKILL.md 규율)
        fqn = _fqn(db, schema, name)
        # 카드 제목 자리는 `logical_name` 이고 설명 자리는 따로다 — 화면이 둘을 다르게 쓴다
        # (`TableNode.vue` 의 `cardTitle`). 스킬이 짧은 도메인명을 주면 그것을 쓰고,
        # 없으면 설명의 괄호 앞부분을 잘라 쓴다. 긴 문장이 제목에 들어가면 카드가 깨진다.
        logical = str(t.get("logical_name") or "").strip() or desc.split("(")[0].strip()
        table_updates.append({
            "fqn": fqn,
            "analyzed_description": desc,
            "logical_name": logical,
        })
        for c in t.get("columns") or []:
            cname = str(c.get("name") or "").strip()
            cdesc = str(c.get("description") or "").strip()
            if cname and cdesc:
                column_updates.append({
                    "fqn": f"{fqn}.{cname}",
                    "analyzed_description": cdesc,
                })

    if table_updates:
        await update_table_analyzed(neo, table_updates)
    if column_updates:
        await update_column_analyzed(neo, column_updates)

    # ── 추론된 관계 ──
    rel_items = []
    for r in relations:
        ft, fc = str(r.get("from_table") or ""), str(r.get("from_column") or "")
        tt, tc = str(r.get("to_table") or ""), str(r.get("to_column") or "")
        if not all((ft, fc, tt, tc)):
            continue
        # `_UPSERT_INFERRED_REL` 은 카탈로그(`:Table`)를 데이터소스·이름으로 찾는다 — fqn 이 아니다.
        # 처음엔 fqn 을 넘겨 쿼리의 item.from_table 등이 모두 null 이 되어 한 건도 안 맞았다.
        rel_items.append({
            "datasource": db,
            "from_table": ft, "from_column": fc,
            "to_table": tt, "to_column": tc,
            "evidence": str(r.get("evidence") or ""),
            "confidence": str(r.get("confidence") or "low"),
        })
    created = 0
    if rel_items:
        rows = await neo.run_read_query(_UPSERT_INFERRED_REL, {"items": rel_items})
        created = int(rows[0]["created"]) if rows else 0

    log_info(
        _STEP,
        "done",
        agent=result.get("_agent"),
        tables=len(table_updates),
        columns=len(column_updates),
        relations=created,
        elapsed_sec=f"{time.monotonic() - started:.1f}",
    )
    # 0건은 실패와 구별해서 남긴다 — 근거가 없어 비는 것도 정상이기 때문이다.
    if not table_updates and not column_updates:
        log_info(_STEP, "empty", hint="에이전트가 근거를 찾지 못했습니다 — 프로시저가 테이블을 참조하는지 확인하세요")

    return {}


async def describe_tables_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    """테이블·컬럼 의미 복원 — ``ROBO_ANALYZE_ENGINE`` 으로 두 엔진 중 하나를 고른다.

    - ``controller`` (기본): 함수별 table_insights 를 YAML 프롬프트로 종합 (`enrich_table_description_step`).
    - ``cliagents`` / ``skill``: CLI 에이전트가 ``skills/metadata-augment`` 로 작업 폴더를 통째로 읽는다.

    파이프라인 정의는 모듈 상수라 import 시점에 고정된다. 고르는 일을 실행 시점으로 미뤄야
    분석기를 다시 띄우지 않고 환경변수 한 줄로 되돌릴 수 있다.
    """
    engine = (os.environ.get("ROBO_ANALYZE_ENGINE") or "controller").strip().lower()
    if engine in ("cliagents", "skill"):
        log_info(_STEP, "engine", engine=engine)
        return await skill_augment_step(state, config)
    from analysis_flow.step7_post_processing.enrich_table_description_step import (
        enrich_table_description_step,
    )
    return await enrich_table_description_step(state, config)
