"""DBMS Phase 5: 리니지 분석 — catalog 서비스에 위임."""

from __future__ import annotations

import logging
import os
from typing import Any, AsyncGenerator, List

import aiohttp
import aiofiles

from client.neo4j_client import Neo4jClient
from analyzer.strategy.base_analyzer import AnalysisStats
from util.ndjson_stream import emit_message, emit_phase_event
from util.logger import log_process

logger = logging.getLogger(__name__)


async def run_phase_lineage(
    ctx: Any,  # DbmsPhaseContext
    phase_num: int = 5,
) -> AsyncGenerator[bytes, None]:
    """데이터 리니지 분석 — catalog 서비스에 위임."""
    source_dir = ctx.orchestrator.dirs.get("source", "")

    if not source_dir or not os.path.exists(source_dir):
        yield emit_message("   ℹ️ SP 파일 없음 → 리니지 분석 건너뜀")
        return

    sql_files: List[str] = []
    for root, _, files in os.walk(source_dir):
        for f in files:
            if f.endswith(".sql"):
                sql_files.append(os.path.join(root, f))

    if not sql_files:
        yield emit_message("   ℹ️ SP 파일 없음 → 리니지 분석 건너뜀")
        return

    total_files = len(sql_files)
    log_process("PHASE:5", "START", f"리니지 분석 시작: {total_files}개 SP 파일", logging.INFO)
    yield emit_message(f"   🔍 [Phase {phase_num}] {total_files}개 SP 파일 → catalog 리니지 분석 위임...")
    yield emit_phase_event(phase_num, "리니지 분석", "in_progress", 0, {"total_files": total_files})

    catalog_url = os.environ.get("CATALOG_SERVICE_URL", "http://localhost:5503")
    dbms = getattr(ctx.orchestrator, "target", "oracle") or "oracle"
    name_case = getattr(ctx.orchestrator, "name_case", "original")

    total_etl = 0
    total_reads = 0
    total_writes = 0
    total_flows = 0
    files_with_etl = 0

    async with aiohttp.ClientSession() as session:
        for idx, sql_file in enumerate(sql_files, 1):
            file_name = os.path.basename(sql_file)
            progress = int((idx / total_files) * 100)

            try:
                async with aiofiles.open(sql_file, "r", encoding="utf-8", errors="ignore") as f:
                    sql_content = await f.read()

                resp = await session.post(
                    f"{catalog_url}/robo/lineage/analyze/",
                    json={
                        "sqlContent": sql_content,
                        "fileName": file_name,
                        "dbms": dbms,
                        "nameCaseOption": name_case,
                    },
                    timeout=aiohttp.ClientTimeout(total=120),
                )
                result = await resp.json()

                lineages = result.get("lineages", [])
                file_stats = result.get("stats", {})

                if lineages:
                    files_with_etl += 1
                    total_etl += file_stats.get("etl_nodes", 0)
                    total_reads += file_stats.get("etl_reads", 0)
                    total_writes += file_stats.get("etl_writes", 0)
                    total_flows += file_stats.get("data_flows", 0)
                    yield emit_message(f"      ✅ [{idx}/{total_files}] {file_name}: ETL {len(lineages)}개 감지")
                else:
                    yield emit_message(f"      ⏭️ [{idx}/{total_files}] {file_name}: ETL 패턴 없음")

                yield emit_phase_event(phase_num, "리니지 분석", "in_progress", progress,
                                       {"current_file": file_name, "done": idx, "total": total_files, "etl_found": total_etl})

            except Exception as e:
                error_msg = f"{file_name} 리니지 분석 실패(스킵): {e}"
                log_process("PHASE:5", "ERROR", error_msg, logging.ERROR, e)
                yield emit_message(f"      ⚠️ [{idx}/{total_files}] {file_name}: 리니지 분석 실패 → 스킵 ({str(e)[:80]})")
                continue

    if not hasattr(ctx.stats, "etl_count"):
        ctx.stats.etl_count = 0
    if not hasattr(ctx.stats, "data_flows"):
        ctx.stats.data_flows = 0
    ctx.stats.etl_count = total_etl
    ctx.stats.data_flows = total_flows

    log_process("PHASE:5", "COMPLETE",
                f"리니지 완료: ETL {total_etl}개, READS {total_reads}개, WRITES {total_writes}개, DATA_FLOWS {total_flows}개",
                logging.INFO)
    yield emit_message(
        f"   ✅ 리니지 분석 완료: ETL {total_etl}개, ETL_READS {total_reads}개, "
        f"ETL_WRITES {total_writes}개, DATA_FLOWS_TO {total_flows}개"
    )
    yield emit_phase_event(phase_num, "리니지 분석", "completed", 100,
                           {"etl_nodes": total_etl, "etl_reads": total_reads,
                            "etl_writes": total_writes, "data_flows": total_flows})
