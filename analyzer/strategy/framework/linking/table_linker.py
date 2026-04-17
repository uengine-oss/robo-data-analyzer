"""SQL → Table/Column/FK 추출.

기존 실험용 파이프라인의 SQL→Table 추출 로직을 framework 전략용으로 통합한 모듈.
"""

from __future__ import annotations

import logging
import os
import queue
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

from config.settings import settings
from analyzer.strategy.framework.common.models import (
    ColumnRef,
    FkEdge,
    ReadsWritesEdge,
    SqlStatementChunk,
)
from util.text_utils import calculate_code_token as estimate_tokens
# LLM 배치 결과 한 건: (tables, columns, fks, alias_map, table_descriptions). 문자열 주석으로 지연 평가해 스레드 첫 import 시 NameError 방지.
SqlChunkRawResult = Tuple[
    List[Tuple[str, str, str]],
    List[ColumnRef],
    List[FkEdge],
    List[Dict[str, str]],
    Dict[Tuple[str, str], str],
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SQL 분할 상수
# ---------------------------------------------------------------------------

SQL_LLM_CHUNK_MAX = 7000
SQL_SPLIT_THRESHOLD = 8000
SQL_MAX_CHUNKS = 20

_SQL_TABLE_KEYWORDS = re.compile(
    r"\b(FROM|JOIN|INTO|UPDATE|DELETE)\b",
    re.IGNORECASE,
)


def _find_safe_break_at_keywords(text: str, start: int, ideal_end: int) -> int:
    """ideal_end 이전에서 끊기: depth 0에서 FROM/JOIN/INTO/UPDATE/DELETE가 한 번이라도 나온 뒤에만
    last_safe를 갱신. 끊기는 depth 0인 쉼표·줄바꿈 또는 다음 키워드 직전."""
    if ideal_end >= len(text):
        return len(text)
    last_safe = start
    depth = 0
    i = start
    in_single = False
    in_double = False
    seen_table_keyword = False
    while i < ideal_end and i < len(text):
        c = text[i]
        if in_single:
            if c == "\\":
                i += 2
                continue
            if c == "'":
                in_single = False
            i += 1
            continue
        if in_double:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                in_double = False
            i += 1
            continue
        if c == "'":
            in_single = True
            i += 1
            continue
        if c == '"':
            in_double = True
            i += 1
            continue
        if c == "(":
            depth += 1
            i += 1
            continue
        if c == ")":
            depth -= 1
            i += 1
            continue
        if depth == 0:
            if not seen_table_keyword and _SQL_TABLE_KEYWORDS.match(text[i:]):
                seen_table_keyword = True
            if seen_table_keyword:
                if c == "," or c == "\n":
                    last_safe = i + 1
                next_kw = _SQL_TABLE_KEYWORDS.search(text[i + 1 : ideal_end])
                if next_kw and next_kw.start() > 0:
                    candidate = i + 1 + next_kw.start()
                    if start < candidate < ideal_end:
                        last_safe = candidate
        i += 1
    if last_safe <= start:
        return ideal_end
    return min(last_safe, ideal_end)


def _split_sql_at_keyword_boundaries(sql_text: str) -> List[str]:
    """긴 SQL을 키워드 경계(FROM/JOIN/INTO/UPDATE/DELETE)에서만 분할. 각 조각은 최소 하나의 테이블 문맥 포함."""
    text = (sql_text or "").strip()
    if not text or len(text) <= SQL_SPLIT_THRESHOLD:
        return [text] if text else []
    out: List[str] = []
    start = 0
    while start < len(text):
        ideal_end = min(start + SQL_LLM_CHUNK_MAX, len(text))
        if ideal_end >= len(text):
            out.append(text[start:].strip())
            break
        break_at = _find_safe_break_at_keywords(text, start, ideal_end)
        if break_at <= start:
            break_at = ideal_end
        piece = text[start:break_at].strip()
        if piece:
            out.append(piece)
        start = break_at
        if len(out) >= SQL_MAX_CHUNKS:
            if start < len(text):
                out.append(text[start:].strip())
            break
    return out if out else [text]


def _split_sql_for_llm(sql_text: str) -> List[str]:
    """긴 SQL을 키워드 경계에서 분할. 괄호 depth 0인 FROM/JOIN/INTO/UPDATE/DELETE 근처에서만 자름."""
    text = (sql_text or "").strip()
    if not text or len(text) <= SQL_SPLIT_THRESHOLD:
        return [text] if text else []

    parts_by_semicolon = [p.strip() for p in text.split(";") if p.strip()]
    use_semicolon = len(parts_by_semicolon) <= 5 and all(len(p) >= 200 for p in parts_by_semicolon)
    if use_semicolon and parts_by_semicolon:
        parts = parts_by_semicolon
    else:
        parts = [text]

    out: List[str] = []
    for part in parts:
        if len(part) <= SQL_LLM_CHUNK_MAX:
            out.append(part)
            continue
        pieces = _split_sql_at_keyword_boundaries(part)
        out.extend(pieces)
    return out if out else [text]


def _expand_chunks_with_sql_split(chunks: List[SqlStatementChunk]) -> List[SqlStatementChunk]:
    """긴 SQL 청크를 키워드 경계에서 분할해 여러 청크로 확장. 같은 sql_key 유지, 조각 순서 유지."""
    out: List[SqlStatementChunk] = []
    for c in chunks:
        if getattr(c, "sql_type", "") == "entity_definition":
            out.append(c)
            continue
        sql_text = (getattr(c, "sql_text", "") or "").strip()
        if not sql_text or len(sql_text) <= SQL_SPLIT_THRESHOLD:
            out.append(c)
            continue
        pieces = _split_sql_at_keyword_boundaries(sql_text)
        if len(pieces) <= 1:
            out.append(c)
            continue
        sql_key = getattr(c, "sql_key", "")
        sql_type = getattr(c, "sql_type", "")
        start_line = getattr(c, "start_line", 0) or 0
        end_line = getattr(c, "end_line", 0) or 0
        file_path = getattr(c, "file_path", "")
        for piece in pieces:
            if not piece.strip():
                continue
            out.append(
                SqlStatementChunk(
                    sql_key=sql_key,
                    sql_type=sql_type,
                    start_line=start_line,
                    end_line=end_line,
                    sql_text=piece,
                    file_path=file_path,
                )
            )
    return out


def _parse_sql_extract_result(
    result: Dict[str, Any],
) -> Tuple[List[Tuple[str, str, str]], List[ColumnRef], List[FkEdge], List[Dict[str, str]], Dict[Tuple[str, str], str]]:
    """LLM sql_table_extract 단일 결과 dict → (tables, columns, fks, alias_map, table_descriptions)."""
    empty_desc: Dict[Tuple[str, str], str] = {}
    if not isinstance(result, dict):
        return [], [], [], [], empty_desc

    alias_map: List[Dict[str, str]] = []
    for row in (result.get("alias_map") or []):
        if isinstance(row, dict) and row.get("alias") and row.get("physical_table"):
            alias_map.append(
                {
                    "alias": str(row.get("alias", "")).strip(),
                    "physical_table": str(row.get("physical_table", "")).strip(),
                }
            )
    aliases_set = {ent["alias"] for ent in alias_map}
    alias_to_physical = {e["alias"]: e["physical_table"] for e in alias_map}

    tables: List[Tuple[str, str, str]] = []
    table_descriptions: Dict[Tuple[str, str], str] = {}
    for row in (result.get("tables") or []):
        if not isinstance(row, dict) or not row.get("name"):
            continue
        name = str(row.get("name") or "").strip()
        if name in aliases_set:
            continue
        schema = str(row.get("schema") or "").strip()
        rel = str(row.get("relation") or "SELECT").strip().upper()
        if rel not in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            rel = "SELECT"
        tables.append((schema, name, rel))
        desc = str(row.get("description") or "").strip()
        if desc:
            table_descriptions[(schema, name)] = desc

    columns: List[ColumnRef] = []
    for row in (result.get("columns") or []):
        if not isinstance(row, dict) or not row.get("column_name"):
            continue
        tbl = str(row.get("table_name") or "").strip()
        if tbl in alias_to_physical:
            tbl = alias_to_physical[tbl]
        col_desc = str(row.get("description") or "").strip() or None
        columns.append(
            ColumnRef(
                table_schema=str(row.get("table_schema") or "").strip(),
                table_name=tbl,
                column_name=str(row.get("column_name") or "").strip(),
                description=col_desc,
            )
        )

    fks: List[FkEdge] = []
    for row in (result.get("fks") or []):
        if isinstance(row, dict) and row.get("from_table") and row.get("to_table"):
            fks.append(
                FkEdge(
                    from_schema=str(row.get("from_schema") or "").strip(),
                    from_table=str(row.get("from_table") or "").strip(),
                    from_column=str(row.get("from_column") or "").strip(),
                    to_schema=str(row.get("to_schema") or "").strip(),
                    to_table=str(row.get("to_table") or "").strip(),
                    to_column=str(row.get("to_column") or "").strip(),
                    candidate=True,
                )
            )

    return tables, columns, fks, alias_map, table_descriptions


def _extract_tables_columns_fk_via_llm(
    sql_text: str,
    sql_key: str,
    api_key: Optional[str] = None,
) -> Tuple[List[Tuple[str, str, str]], List[ColumnRef], List[FkEdge]]:
    """LLM으로 한 조각에서 테이블·컬럼·FK 추출."""
    api_key = api_key or settings.llm.api_key or os.environ.get("LLM_API_KEY", "")
    if not (sql_text or "").strip():
        return [], [], []
    if not api_key:
        logger.error("[PHASE:2:TABLE_LLM] LLM_API_KEY 없음, sql_table_extract 스킵 | sql_key=%s", sql_key)
        return [], [], []
    text = (sql_text.strip() or "")[:SQL_LLM_CHUNK_MAX]
    try:
        from util.prompt_registry import prompts
        result = prompts.table_extract([{"sql_key": sql_key or "", "sql_text": text, "prev_summary": ""}], api_key)
        res_list = result.get("results") or []
        result_item = res_list[0] if res_list and isinstance(res_list[0], dict) else {}
    except Exception as e:
        logger.error("[PHASE:2:TABLE_LLM] LLM sql_table_extract 오류 | sql_key=%s error=%s", sql_key, e)
        return [], [], []
    tables, columns, fks, _, _ = _parse_sql_extract_result(result_item)
    return tables, columns, fks


def _extract_tables_columns_fk_batch_via_llm(
    batch: List[SqlStatementChunk],
    api_key: str,
    prev_summaries: Optional[List[str]] = None,
) -> List[SqlChunkRawResult]:
    """여러 SQL 청크를 한 번에 LLM에 보내 테이블/컬럼/FK/table_descriptions 추출."""
    if not batch or not api_key:
        return []
    from util.prompt_registry import prompts
    sql_items = []
    for i, c in enumerate(batch):
        sql_key = getattr(c, "sql_key", "")
        sql_text = (getattr(c, "sql_text", "") or "").strip()
        prev = ""
        if prev_summaries and i < len(prev_summaries):
            prev = (prev_summaries[i] or "").strip()
        sql_items.append({"sql_key": sql_key, "sql_text": sql_text, "prev_summary": prev})
    try:
        result = prompts.table_extract(sql_items, api_key)
    except Exception as e:
        logger.error("[PHASE:2:TABLE_LLM] LLM sql_table_extract 오류 | error=%s", e, exc_info=True)
        return [([], [], [], [], {}) for _ in batch]
    out: List[Tuple[List[Tuple[str, str, str]], List[ColumnRef], List[FkEdge], List[Dict[str, str]], Dict[Tuple[str, str], str]]] = []
    results_list = result.get("results") or []
    for res in results_list:
        tables, columns, fks, alias_map, table_descriptions = _parse_sql_extract_result(res)
        out.append((tables, columns, fks, alias_map, table_descriptions))
    while len(out) < len(batch):
        out.append(([], [], [], [], {}))
    return out


def _build_sql_batches(
    chunks: List[SqlStatementChunk],
    max_tokens: int,
) -> List[List[SqlStatementChunk]]:
    """청크 리스트를 max_tokens 이하로 묶어 배치 리스트 반환."""
    batches: List[List[SqlStatementChunk]] = []
    current: List[SqlStatementChunk] = []
    current_tokens = 0
    for c in chunks:
        sql_key = getattr(c, "sql_key", "")
        sql_text = (getattr(c, "sql_text", "") or "").strip()
        t = estimate_tokens(sql_key) + estimate_tokens(sql_text)
        if t > max_tokens:
            t = max_tokens
        if current and current_tokens + t > max_tokens:
            batches.append(current)
            current = []
            current_tokens = 0
        current.append(c)
        current_tokens += t
    if current:
        batches.append(current)
    return batches


def _format_prev_summary(
    prev_results: List[
        Tuple[List[Tuple[str, str, str]], List[Any], List[Any], Optional[List[Dict[str, str]]]]
    ],
) -> str:
    """이전 조각들 LLM 결과에서 물리테이블·관계·별칭→물리테이블만 요약."""
    if not prev_results:
        return ""
    parts: List[str] = []
    seen: set = set()
    alias_pairs: List[str] = []
    for item in prev_results:
        tables = item[0]
        alias_map = item[3] if len(item) >= 4 and item[3] else []
        aliases_in_this = {
            str(ent.get("alias", "")).strip()
            for ent in (alias_map or [])
            if isinstance(ent, dict) and ent.get("alias")
        }
        for schema, name, rel in tables:
            name_stripped = (name or "").strip()
            if name_stripped in aliases_in_this:
                continue
            key = (schema or "", name_stripped, rel or "")
            if key in seen:
                continue
            seen.add(key)
            parts.append(
                f"{schema}.{name_stripped}({rel})" if schema else f"{name_stripped}({rel})"
            )
        for ent in alias_map if isinstance(alias_map, list) else []:
            if isinstance(ent, dict) and ent.get("alias") and ent.get("physical_table"):
                alias_pairs.append(f"{ent.get('alias')}={ent.get('physical_table')}")
    if not parts and not alias_pairs:
        return ""
    out = "[이전 조각까지 누적된 요약(prev_summary). 아래 내용을 참고하여 이번 조각 결과에 포함·누적하세요:"
    if parts:
        out += " 물리테이블 " + ", ".join(parts[:20]) + (" ..." if len(parts) > 20 else "")
    if alias_pairs:
        out += "| 별칭→물리테이블: " + ", ".join(alias_pairs[:30])
    out += ". 이 조각에 위 테이블이 없으면 해당 테이블에 컬럼을 연결하지 마세요. 별칭은 테이블로 출력하지 마세요.]"
    return out


def _process_one_sql_batch(
    batch: List[SqlStatementChunk],
    api_key: str,
    prev_summaries: Optional[List[str]] = None,
) -> Tuple[List[ReadsWritesEdge], List[FkEdge], List[ColumnRef], Dict[Tuple[str, str], str], List[SqlChunkRawResult]]:
    """한 배치에 대해 LLM 호출 후 edges, fk_edges, column_refs, table_descriptions, per_chunk_raw 반환."""
    edges: List[ReadsWritesEdge] = []
    fk_edges: List[FkEdge] = []
    column_refs: List[ColumnRef] = []
    table_descriptions: Dict[Tuple[str, str], str] = {}
    per_chunk_raw: List[SqlChunkRawResult] = []
    if not batch or not api_key:
        return edges, fk_edges, column_refs, table_descriptions, per_chunk_raw
    per_chunk = _extract_tables_columns_fk_batch_via_llm(batch, api_key, prev_summaries)
    for chunk, (tables, cols, fks, alias_map, desc_map) in zip(batch, per_chunk):
        per_chunk_raw.append((tables, cols, fks, alias_map, desc_map))
        for (s, n), d in (desc_map or {}).items():
            if d and (s, n) not in table_descriptions:
                table_descriptions[(s, n)] = d
        sql_key = getattr(chunk, "sql_key", "")
        tables_set = {(s or "", t or "") for s, t, _ in tables}
        seen: set = set()
        for schema, table, rel in tables:
            key = (sql_key, schema, table, rel)
            if key in seen:
                continue
            seen.add(key)
            edges.append(
                ReadsWritesEdge(
                    sql_key=sql_key,
                    table_schema=schema,
                    table_name=table,
                    relation=rel,
                )
            )
        for col in cols:
            if (col.table_schema or "", col.table_name or "") in tables_set:
                column_refs.append(col)
        for fk in fks:
            fk_edges.append(fk)
    return edges, fk_edges, column_refs, table_descriptions, per_chunk_raw


def _link_sql_to_tables_per_key_pipeline(
    by_key: Dict[str, List[SqlStatementChunk]],
    results_by_key: Dict[str, List[Any]],
    api_key: str,
    workers: int,
    on_batch: Optional[Any] = None,
) -> Tuple[List[ReadsWritesEdge], List[FkEdge], List[ColumnRef], Dict[Tuple[str, str], str]]:
    """키 단위 파이프라인: sql_key별로 조각을 순차 처리, 서로 다른 키는 병렬."""
    edges: List[ReadsWritesEdge] = []
    fk_edges: List[FkEdge] = []
    column_refs: List[ColumnRef] = []
    table_descriptions: Dict[Tuple[str, str], str] = {}
    seen_edge: set = set()
    seen_col: set = set()
    lock = threading.Lock()
    total_pieces = sum(len(v) for v in by_key.values())
    work_queue: queue.Queue = queue.Queue()
    completed_count = [0]

    for k in by_key:
        work_queue.put((k, 0, by_key[k][0], ""))

    def worker() -> None:
        while True:
            try:
                item = work_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            key, piece_i, chunk, prev = item
            if key is None:
                work_queue.task_done()
                break
            try:
                e, fk, col, batch_desc, raw = _process_one_sql_batch([chunk], api_key, [prev])
            except Exception as ex:
                logger.error("[PHASE:2:TABLE_LLM] SQL 키 단위 LLM 예외: %s", ex)
                e, fk, col, batch_desc, raw = [], [], [], {}, [([], [], [], [], {})]
            with lock:
                if raw and len(raw) > 0:
                    results_by_key.setdefault(key, []).append(raw[0])
                for (s, n), d in (batch_desc or {}).items():
                    if d and (s, n) not in table_descriptions:
                        table_descriptions[(s, n)] = d
                for x in e:
                    k_edge = (x.sql_key, x.table_schema, x.table_name, x.relation)
                    if k_edge not in seen_edge:
                        seen_edge.add(k_edge)
                        edges.append(x)
                fk_edges.extend(fk)
                for c in col:
                    ckey = (c.table_schema, c.table_name, c.column_name)
                    if ckey not in seen_col:
                        seen_col.add(ckey)
                        column_refs.append(c)
                completed_count[0] += 1
                n, t = completed_count[0], total_pieces
                if n % 10 == 0 or n == t:
                    logger.info("[PHASE:2:TABLE_LLM] SQL→테이블(키 단위 파이프라인) 완료 %d/%d", n, t)
            # 배치 완료 즉시 콜백 (lock 밖에서)
            if on_batch and (e or fk or col):
                on_batch(e, fk, col, batch_desc or {})
            with lock:
                if piece_i + 1 < len(by_key[key]):
                    chunk_next = by_key[key][piece_i + 1]
                    prev_next = _format_prev_summary(results_by_key.get(key, [])[: piece_i + 1])
                    work_queue.put((key, piece_i + 1, chunk_next, prev_next))

    logger.info(
        "[PHASE:2:TABLE_LLM] SQL→테이블 키 단위 파이프라인 시작 | 키=%d 조각=%d 워커=%d",
        len(by_key),
        total_pieces,
        workers,
    )
    threads = [
        threading.Thread(target=worker, daemon=False)
        for _ in range(min(workers, total_pieces))
    ]
    for t in threads:
        t.start()
    # 종료 신호 전송 후 모든 스레드 join (busy-wait 제거)
    for _ in threads:
        work_queue.put((None, 0, None, ""))
    for t in threads:
        t.join(timeout=600)
    return edges, fk_edges, column_refs, table_descriptions


USE_SQL_PER_KEY_PIPELINE = os.environ.get("USE_SQL_PER_KEY_PIPELINE", "true").lower() == "true"


# ═══════════════════════════════════════════════════════════════════════
# Async 버전 — AsyncBatchExecutor + LLMRateLimiter 기반
# ═══════════════════════════════════════════════════════════════════════

async def link_sql_to_tables_async(
    sql_chunks: List[SqlStatementChunk],
    api_key: Optional[str] = None,
    on_batch: Optional[Any] = None,
) -> Tuple[List[ReadsWritesEdge], List[FkEdge], List[ColumnRef], Dict[Tuple[str, str], str]]:
    """SqlStatement → Table (async 버전). AsyncBatchExecutor + LLMRateLimiter 사용.

    동기 link_sql_to_tables()와 동일한 결과를 반환하되,
    LLM 호출을 asyncio.to_thread로 래핑하여 이벤트 루프를 차단하지 않고
    LLMRateLimiter로 전역 동시성을 제어합니다.
    """
    import asyncio
    from util.async_executor import get_rate_limiter

    edges: List[ReadsWritesEdge] = []
    fk_edges: List[FkEdge] = []
    column_refs: List[ColumnRef] = []
    table_descriptions: Dict[Tuple[str, str], str] = {}
    seen_edge: set = set()
    seen_col: set = set()
    _api_key = api_key or settings.llm.api_key or os.environ.get("LLM_API_KEY", "")

    chunks_to_process = [c for c in sql_chunks if (getattr(c, "sql_text", "") or "").strip()]
    if not chunks_to_process:
        return edges, fk_edges, column_refs, table_descriptions

    expanded = _expand_chunks_with_sql_split(chunks_to_process)
    by_key: Dict[str, List[SqlStatementChunk]] = {}
    for c in expanded:
        by_key.setdefault(getattr(c, "sql_key", ""), []).append(c)
    max_pieces = max(len(v) for v in by_key.values()) if by_key else 0
    results_by_key: Dict[str, List[SqlChunkRawResult]] = {k: [] for k in by_key}
    max_tokens = settings.batch.max_batch_token
    limiter = get_rate_limiter()

    def _collect_results(e, fk, col, batch_desc, raw, batch):
        """배치 결과를 공용 리스트에 수집 (thread-safe 불필요 — await 기반)."""
        for (s, n), d in (batch_desc or {}).items():
            if d and (s, n) not in table_descriptions:
                table_descriptions[(s, n)] = d
        for j, chunk in enumerate(batch):
            if j < len(raw):
                results_by_key.setdefault(getattr(chunk, "sql_key", ""), []).append(raw[j])
        for x in e:
            key_edge = (x.sql_key, x.table_schema, x.table_name, x.relation)
            if key_edge not in seen_edge:
                seen_edge.add(key_edge)
                edges.append(x)
        fk_edges.extend(fk)
        for c_item in col:
            ckey = (c_item.table_schema, c_item.table_name, c_item.column_name)
            if ckey not in seen_col:
                seen_col.add(ckey)
                column_refs.append(c_item)
        if on_batch and (e or fk or col):
            on_batch(e, fk, col, batch_desc or {})

    for round_i in range(max_pieces):
        round_data = []
        for k in sorted(by_key.keys()):
            if round_i >= len(by_key[k]):
                continue
            chunk = by_key[k][round_i]
            prev = _format_prev_summary(results_by_key.get(k, [])[:round_i])
            round_data.append((k, chunk, prev))
        if not round_data:
            continue
        round_chunks = [c for _, c, _ in round_data]
        round_prevs = [p for _, _, p in round_data]
        index_in_round = {id(c): i for i, (_, c, _) in enumerate(round_data)}
        batches = _build_sql_batches(round_chunks, max_tokens)

        logger.info("[PHASE:2:TABLE_LLM] async 라운드 %d/%d | 청크=%d 배치=%d", round_i + 1, max_pieces, len(round_chunks), len(batches))

        from util.async_executor import get_executor
        executor = get_executor()

        # 각 배치에 prev_summaries를 바인딩
        batch_with_prev = [(batch, [round_prevs[index_in_round[id(c)]] for c in batch]) for batch in batches]

        async def _process_one(item):
            batch, prev_summaries = item
            result = await asyncio.to_thread(_process_one_sql_batch, batch, _api_key, prev_summaries)
            return result, batch

        def _on_result(result):
            if result is None:
                return
            (e, fk, col, batch_desc, raw), batch = result
            _collect_results(e, fk, col, batch_desc, raw, batch)

        await executor.run_batch(
            items=batch_with_prev,
            processor=_process_one,
            on_result=_on_result,
            label="TABLE_LLM",
        )

    logger.info(
        "[PHASE:2:TABLE_LLM] async 완료 | reads_writes=%d fk=%d columns=%d",
        len(edges), len(fk_edges), len(column_refs),
    )
    return edges, fk_edges, column_refs, table_descriptions
