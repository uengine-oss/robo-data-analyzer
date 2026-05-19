"""extract_dml_metadata_step — DBMS only. PL/SQL DML range 메타 LLM 추출.

흐름:
1. ``parse_source_step`` 가 생성한 nested DML Function (``parent_id`` 있는 range) 들을 수집.
2. ``ROBO_PIPELINE_CALL_LLM_BATCH_SIZE`` 단위 배치 → ``dml_extract.yaml`` LLM 호출.
3. ``DmlExtractOutput`` 의 tables/fk_relations/db_links → ``ReadsWrites`` + ``Fk`` 엣지.

위치: step6 와 step7 사이에 실행 — DBMS strategy 만 step 리스트에 포함.

**Plan §6.3 와의 관계**:
Plan §6.3 ``prompts/dbms`` 에 ``dml_extract.yaml`` 명시. 이 yaml 을 소비하는 step 이
필요해서 이 파일이 추가됨. ``analysis_pipelines.py`` 의 ``DBMS_PIPELINE.steps`` 에 명시 포함.
Plan §3.5 표의 "단순 LLM 호출 (DBMS) — dml_extract" 흐름을 구현.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

from langchain_core.runnables import RunnableConfig

from data_models.graph_edges import Fk, ReadsWrites
from data_models.graph_nodes import Function
from shared.config.env_catalog import ENV
from shared.llm.cost_budget import BudgetExceededError
from shared.llm.llm_output_models import DmlExtractOutput
from shared.llm.middleware.call_limit import ModelCallLimitExceeded
from shared.llm.yaml_llm_runner import run_yaml_rule
from shared.logging_and_stream.logger import log_error
from shared.neo4j.edges.fk import upsert_fk
from shared.neo4j.edges.reads_writes import upsert_reads_writes
from shared.neo4j.neo4j_client import Neo4jClient


async def extract_dml_metadata_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    """DML range 들 → LLM 으로 tables/FK/DBLink 메타 추출.

    DBMS strategy only. Framework 는 step 리스트에 포함 안 됨.
    """
    cfg = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    strategy = cfg.get("strategy")
    cost_budget = cfg.get("cost_budget")
    neo: Neo4jClient | None = cfg.get("neo_client")
    if strategy is None:
        return {}

    functions = state.get("functions", [])
    # parent_id 있는 함수 = nested DML range
    ranges = [f for f in functions if f.parent_id is not None]
    if not ranges:
        return {}

    # parent (procedure) 별 그룹화 → 같은 procedure 의 range 들을 1 batch
    by_parent: dict[str, list[Function]] = {}
    for r in ranges:
        by_parent.setdefault(r.parent_id, []).append(r)

    prompts_dir = Path(strategy.prompts_dir)
    semaphore = asyncio.Semaphore(ENV.pipeline.max_concurrency)

    all_rw: list[ReadsWrites] = []
    all_fk: list[Fk] = []

    async def _extract_one(parent_id: str, parent_ranges: list[Function]):
        ranges_json = [
            {
                "range_id": r.function_id,
                "signature": r.signature,
                "body": _read_body(r),
            }
            for r in parent_ranges
        ]
        async with semaphore:
            try:
                result: DmlExtractOutput = await run_yaml_rule(
                    prompts_dir=prompts_dir,
                    rule_name="dml_extract",
                    user_values={
                        "procedure_id": parent_id,
                        "ranges_json": json.dumps(ranges_json, ensure_ascii=False, indent=2),
                    },
                    response_model=DmlExtractOutput,
                    budget=cost_budget,
                )
            except (BudgetExceededError, ModelCallLimitExceeded):
                raise            # 회로 차단기 즉시 전파
            except Exception as e:
                log_error(
                    "analysis_flow.step7_post_processing.extract_dml_metadata_step",
                    "dml_extract",
                    e,
                    procedure_id=parent_id,
                )
                return [], []

        rw_edges: list[ReadsWrites] = []
        fk_edges: list[Fk] = []
        for range_out in result.ranges:
            for tref in range_out.tables:
                relation = (
                    "WRITES"
                    if tref.relation.upper() in ("WRITE", "WRITES", "INSERT", "UPDATE", "DELETE")
                    else "READS"
                )
                rw_edges.append(ReadsWrites(
                    function_id=range_out.range_id,
                    table_fqn=tref.table,
                    relation=relation,
                ))
            for fk in range_out.fk_relations:
                if not isinstance(fk, dict):
                    continue
                from_t = fk.get("from_table")
                to_t = fk.get("to_table")
                from_c = fk.get("from_column")
                to_c = fk.get("to_column")
                if from_t and to_t and from_c and to_c:
                    fk_edges.append(Fk(
                        from_table_fqn=from_t,
                        to_table_fqn=to_t,
                        from_column=from_c,
                        to_column=to_c,
                        is_candidate=True,
                    ))
        return rw_edges, fk_edges

    results = await asyncio.gather(*[
        _extract_one(pid, rngs) for pid, rngs in by_parent.items()
    ])
    for rw_edges, fk_edges in results:
        all_rw.extend(rw_edges)
        all_fk.extend(fk_edges)

    if neo is not None:
        if all_rw:
            await upsert_reads_writes(neo, all_rw)
        if all_fk:
            await upsert_fk(neo, all_fk)

    return {
        "reads_writes_edges": all_rw,
        "fk_edges": all_fk,
    }


def _read_body(fn: Function) -> str:
    """range 본문 라인 범위 추출."""
    try:
        text = Path(fn.file_path).read_text(encoding="utf-8", errors="ignore")
    except (OSError, UnicodeDecodeError):
        return ""
    lines = text.splitlines()
    return "\n".join(lines[max(fn.start_line - 1, 0) : fn.end_line])
