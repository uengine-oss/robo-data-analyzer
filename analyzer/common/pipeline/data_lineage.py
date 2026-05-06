"""데이터 리니지 분석 — robo-data-catalog 서비스에 위임 (전략 공통).

DBMS / Framework 양쪽에서 호출. source 디렉토리의 .sql 파일 전수를 catalog 의
`/robo/lineage/analyze/` 에 보내 ETL/READ/WRITE/data_flow 통계 누적.

책임:
  - source 디렉토리의 .sql 파일 전수 → catalog `/robo/lineage/analyze/` POST
  - 각 파일 응답에서 ETL/READ/WRITE/data_flow 통계 누적 — `AnalysisStats` 정식 필드에 직접 갱신
  - 호출자 (`{dbms,framework}/pipeline/phase_lineage.py`) 는 본 모듈을 호출만.

정책 정렬:
  - 호출 실패는 errlog 후 다음 파일 진행 (한 파일 fatal 아님 — catalog 가
    그 파일을 처리 못 해도 다른 파일은 처리 가능). 단 실패한 파일 자체는 errlog 의무.
  - source 디렉토리/SP 파일 부재는 정상 시나리오 (전부 DDL only 분석 가능).
  - emit_phase_event 는 `in_progress` 만 yield. `started`/`completed` 는 호출자가
    감싸 emit (전략 공통 패턴).
"""

from __future__ import annotations

import logging
import os
from typing import AsyncGenerator, List

import aiohttp

from analyzer.strategy.base_analyzer import AnalysisStats
from config.settings import settings
from util.encoding_reader import read_text_with_fallback
from util.logger import log_process
from util.stream_emitter import emit_user_message, emit_phase_event

logger = logging.getLogger(__name__)


def _list_sql_files(source_dir: str) -> List[str]:
    """source 디렉토리 하위의 .sql 파일 전수 수집."""
    if not source_dir or not os.path.exists(source_dir):
        return []
    files: List[str] = []
    for root, _dirs, names in os.walk(source_dir):
        for n in names:
            if n.endswith(".sql"):
                files.append(os.path.join(root, n))
    return sorted(files)


async def _request_lineage(
    session: aiohttp.ClientSession,
    catalog_url: str,
    sql_content: str,
    file_name: str,
    dbms: str,
    name_case: str,
) -> dict:
    """catalog `/robo/lineage/analyze/` POST 요청 — 응답 dict 반환."""
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
    return await resp.json()


async def run_data_lineage(
    *,
    source_dir: str,
    dbms: str,
    name_case: str,
    catalog_url: str,
    stats: AnalysisStats,
    phase_num: int = 5,
) -> AsyncGenerator[bytes, None]:
    """source 디렉토리 .sql 파일들에 대해 catalog lineage 분석을 위임.

    Args:
        stats: 호출자 `AnalysisStats` — 본 함수가 lineage_* 필드를 직접 갱신.
            호출자(dbms_pipeline) 가 종료 시 `emit_phase_event(... "completed", ...)`
            에 stats 의 lineage_* 값을 그대로 전달.

    Yields:
        emit_user_message / emit_phase_event(in_progress) 바이트.
    """
    # Framework 의 pre_analysis 와 동일 패턴 — catalog 비활성 시 graceful skip
    if not settings.catalog.enabled:
        yield emit_user_message("   ⏭️ catalog 비활성 (CATALOG_SERVICE_URL 미설정) — 리니지 분석 skip")
        log_process("PHASE:5", "SKIP", "catalog 비활성 — 리니지 분석 skip", logging.INFO)
        return

    sql_files = _list_sql_files(source_dir)
    if not sql_files:
        yield emit_user_message("   ℹ️ SP 파일 없음 → 리니지 분석 건너뜀")
        return

    total_files = len(sql_files)
    files_with_etl = 0
    log_process("PHASE:5", "START", f"리니지 분석 시작: {total_files}개 SP 파일", logging.INFO)
    yield emit_user_message(f"   🔍 [Phase {phase_num}] {total_files}개 SP 파일 → catalog 리니지 분석 위임...")
    yield emit_phase_event(
        phase_num, "리니지 분석", "in_progress", 0,
        {"total_files": total_files},
    )

    async with aiohttp.ClientSession() as session:
        for idx, sql_file in enumerate(sql_files, 1):
            file_name = os.path.basename(sql_file)
            progress = int((idx / total_files) * 100)
            try:
                # 4단계 encoding chain (utf-8 → utf-8-sig → cp949 → euc-kr).
                # 비-utf-8 사용 시 read_text_with_fallback 가 errlog 의무 기록.
                sql_content, _enc = await read_text_with_fallback(
                    sql_file,
                    preferred_encodings=("utf-8", "utf-8-sig", "cp949", "euc-kr"),
                )
                result = await _request_lineage(
                    session, catalog_url, sql_content, file_name, dbms, name_case,
                )
                lineages = result.get("lineages") or []
                file_stats = result.get("stats") or {}
                if lineages:
                    files_with_etl += 1
                    stats.lineage_etl_count += int(file_stats.get("etl_nodes", 0))
                    stats.lineage_etl_reads += int(file_stats.get("etl_reads", 0))
                    stats.lineage_etl_writes += int(file_stats.get("etl_writes", 0))
                    stats.lineage_data_flows += int(file_stats.get("data_flows", 0))
                    yield emit_user_message(f"      ✅ [{idx}/{total_files}] {file_name}: ETL {len(lineages)}개 감지")
                else:
                    yield emit_user_message(f"      ⏭️ [{idx}/{total_files}] {file_name}: ETL 패턴 없음")
                yield emit_phase_event(
                    phase_num, "리니지 분석", "in_progress", progress,
                    {
                        "current_file": file_name, "done": idx, "total": total_files,
                        "etl_found": stats.lineage_etl_count,
                    },
                )
            except Exception as e:  # noqa: SILENT_FAIL — 한 파일 실패가 전체를 막지 않도록 다른 파일 계속 진행 (정책 정렬: docstring 명시)
                stats.lineage_files_failed += 1
                logger.error(
                    "[PHASE:5:LINEAGE] catalog 호출 실패 — 다른 파일 계속 진행 | file=%s | %s: %s",
                    file_name, type(e).__name__, e, exc_info=True,
                )
                yield emit_user_message(
                    f"      ⚠️ [{idx}/{total_files}] {file_name}: 리니지 분석 실패 → 스킵 ({str(e)[:80]})"
                )
                continue

    log_process(
        "PHASE:5", "COMPLETE",
        (
            f"리니지 완료: ETL {stats.lineage_etl_count}개, READS {stats.lineage_etl_reads}개, "
            f"WRITES {stats.lineage_etl_writes}개, DATA_FLOWS {stats.lineage_data_flows}개 "
            f"(files_with_etl={files_with_etl}/{total_files}, failed={stats.lineage_files_failed})"
        ),
        logging.INFO,
    )
    yield emit_user_message(
        f"   ✅ 리니지 분석 완료: ETL {stats.lineage_etl_count}개, ETL_READS {stats.lineage_etl_reads}개, "
        f"ETL_WRITES {stats.lineage_etl_writes}개, DATA_FLOWS_TO {stats.lineage_data_flows}개"
    )


__all__ = ["run_data_lineage"]
