"""embed_step — 분석 끝 임베딩 → Neo4j (코드노드 = 검색 강화텍스트 + :EMBEDDED + 벡터 인덱스).

코드노드 임베딩 텍스트는 검색측과 **글자 단위 동일**한 ``search/corpus/embed_material.compose_embed_text``
로 **그래프에서** 조성한다(요약만이 아니라 종류·역할·테이블관계·검색문장 — 검색이 성립하려면
생성·검색이 같은 문장이어야 하므로 단일 진실). 파이프라인 끝이라 테이블 관계 엣지까지 완성돼 있다.
Table/Column 은 description 기반 현행 유지(검색 대상 아님 — :EMBEDDED 안 붙임).

흐름:
1. 그래프에서 코드노드 재료(``FETCH_MATERIAL``) 읽어 ``compose_embed_text`` → 벡터.
2. Table/Column description → 벡터 (현행).
3. UNWIND 배치 SET — 코드노드는 ``embedding`` + ``:EMBEDDED`` 마커, Table/Column 은 ``embedding``.
4. ``:EMBED_META`` 지문 upsert(검색이 질의 임베더와 대조) + 벡터 인덱스 IF NOT EXISTS + ONLINE 대기.
5. state 노드에 embedding 반영(artifact 정합).

on/off 토글 — ``ROBO_PIPELINE_EMBED_ENABLED`` (기본 ON). 인덱스=스키마라 매 run wipe(DETACH
DELETE=데이터만) 를 견딘다. 임베딩 실패는 raise(헌법 IV — 벡터 없는 노드는 검색에서 영구 실종).
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from typing import Optional

from langchain_core.runnables import RunnableConfig

from search.corpus.embed_material import (
    EMBED_META_LABEL,
    EMBEDDED_LABEL,
    FETCH_MATERIAL,
    VECTOR_INDEX_NAME,
    compose_embed_text,
)
from analyzer_settings import SETTINGS
from llm import embedder
from runtime_events.runtime_logger import log_info, log_warning
from runtime_events.processing_events import emit_processing_event, step_processing_scope
from graph.neo4j_client import Neo4jClient
from graph.ownership import ANALYZER_GRAPH_OWNER
from pipeline.execution.step_config import step_config


_STEP = "step7.embed"

# 진척 emit 단위(스트림 가시성용) — 임베딩 자체는 embedder 가 문자예산·동시로 배치한다.
_PROGRESS_BATCH = 500
# 인덱스 ONLINE 대기 상한(초). 검색은 분석 완료 후에만 일어나므로 준비 대기는 run 안에서 끝난다.
_AWAIT_INDEX_TIMEOUT = 300


async def embed_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    """코드노드(강화텍스트)·Table/Column(description) → 벡터 → Neo4j + 코드노드 :EMBEDDED·인덱스·지문."""
    if not SETTINGS.analysis_pipeline.embed_enabled:
        log_info(_STEP, "skip", reason="임베딩 비활성화 (ROBO_PIPELINE_EMBED_ENABLED=false)")
        return {}

    cfg = step_config(config)
    neo: Neo4jClient | None = cfg.get("neo_client")
    if neo is None:
        log_warning(_STEP, "config_missing", "neo_client 누락 — step skip")
        return {}
    stream = cfg.get("stream")

    started = time.monotonic()
    log_info(_STEP, "start",
             tables=len(state.get("tables") or []),
             columns=len(state.get("columns") or []),
             code_nodes=len(state.get("code_nodes") or []),
             backend=embedder.fingerprint())

    with step_processing_scope(_STEP, stream):
        # 코드노드 — 그래프 재료로 강화텍스트 임베딩(검색과 동일 조성).
        material = await neo.run_read_query(FETCH_MATERIAL)
        code_updates = await _embed_code_material(material, stream)

        # Table/Column — description 기반(현행), 임베더만 원격 1024d.
        table_updates, column_updates = await asyncio.to_thread(_build_table_column_embeddings, state)

    if code_updates:
        await neo.run_batch_unwind(_embedded_code_query(), code_updates)
    for label, items in (("TABLE", table_updates), ("COLUMN", column_updates)):
        if items:
            await neo.run_batch_unwind(_embedding_query(label, "id"), items)

    await _ensure_vector_index(neo, code_updates)

    updated_tables = _attach_embedding(state.get("tables", []), table_updates, "id")
    updated_columns = _attach_embedding(state.get("columns", []), column_updates, "id")
    updated_code_nodes = _attach_embedding(state.get("code_nodes", []), code_updates, "id")

    log_info(
        _STEP, "done",
        code_embedded=len(code_updates),
        code_material=len(material),
        table_embedded=len(table_updates),
        column_embedded=len(column_updates),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {
        "tables": updated_tables,
        "columns": updated_columns,
        "code_nodes": updated_code_nodes,
    }


# ── 코드노드 (그래프 재료 → 강화텍스트) ────────────────────────

async def _embed_code_material(material: list[dict], stream) -> list[dict]:
    """``FETCH_MATERIAL`` row 들을 ``compose_embed_text`` 로 조성 → 벡터. 배치마다 진척 emit.

    encode_texts 는 sync(내부 asyncio.run) 이므로 ``to_thread`` 로 격리한다. 배치 경계에서만
    루프 위에서 emit → CPU 스레드가 스트림 큐를 직접 만지지 않는다(스레드 안전)."""
    total = len(material)
    if total == 0:
        log_warning(_STEP, "no_code_material", "summary 보유 코드노드 0 — 검색 임베딩 없음")
        return []
    updates: list[dict] = []
    for start in range(0, total, _PROGRESS_BATCH):
        batch = material[start:start + _PROGRESS_BATCH]
        updates.extend(await asyncio.to_thread(_encode_code_rows, batch))
        emit_processing_event(stream, _STEP, "CodeNode", f"{len(updates)}/{total}", status="done")
    return updates


def _encode_code_rows(rows: list[dict]) -> list[dict]:
    """``[row]`` → ``[{"id", "vector"}]``. 텍스트 조성은 검색과 공용 단일 진실."""
    texts = [compose_embed_text(r) for r in rows]
    vectors = embedder.encode_texts(texts)
    return [{"id": r["id"], "vector": vec} for r, vec in zip(rows, vectors)]


def _embedded_code_query() -> str:
    """코드노드 벡터 SET + ``:EMBEDDED`` 마커. id 무라벨 매치(코드 라벨은 antlr 열린집합)."""
    return (
        "UNWIND $items AS item "
        f"MATCH (n {{id: item.id, graph_owner: '{ANALYZER_GRAPH_OWNER}'}}) "
        f"SET n.embedding = item.vector, n:{EMBEDDED_LABEL}"
    )


# ── 벡터 인덱스 + 지문 ────────────────────────────────────────

def _metadata_upsert_query() -> str:
    """임베딩 인덱스 지문도 Analyzer 소유권 경계를 따른다."""
    return (
        f"MERGE (m:{EMBED_META_LABEL} {{graph_owner: '{ANALYZER_GRAPH_OWNER}'}}) "
        "SET m.fingerprint = $fp, m.dim = $dim"
    )

async def _ensure_vector_index(neo: Neo4jClient, code_updates: list[dict]) -> None:
    """``:EMBEDDED`` 벡터 인덱스 IF NOT EXISTS + ONLINE 대기 + 지문 메타. 차원은 실벡터에서 도출.

    임베딩된 코드노드가 없으면 인덱스 생성 불가(차원 미상) → skip. 차원 불일치 옛 인덱스가
    남아 있으면 CREATE IF NOT EXISTS 는 no-op 이므로 **수동 DROP 을 명시 안내**(눈대중 금지)."""
    if not code_updates:
        log_warning(_STEP, "index_skip", "임베딩된 코드노드 0 — 벡터 인덱스 생성 생략")
        return

    dim = len(code_updates[0]["vector"])
    await _warn_on_dim_mismatch(neo, dim)
    await neo.run_query(_create_vector_index_query(dim))
    await neo.run_query(f"CALL db.awaitIndexes({_AWAIT_INDEX_TIMEOUT})")
    await neo.run_query(_metadata_upsert_query(), {"fp": embedder.fingerprint(), "dim": dim})
    log_info(_STEP, "index_ready", index=VECTOR_INDEX_NAME, dim=dim, fingerprint=embedder.fingerprint())


def _create_vector_index_query(dim: int) -> str:
    """벡터 인덱스 생성 쿼리. dim 은 실벡터 길이(int) 인라인 — 사용자 입력 아님(안전)."""
    return (
        f"CREATE VECTOR INDEX {VECTOR_INDEX_NAME} IF NOT EXISTS "
        f"FOR (n:{EMBEDDED_LABEL}) ON (n.embedding) "
        "OPTIONS {indexConfig: {"
        f"`vector.dimensions`: {int(dim)}, "
        "`vector.similarity_function`: 'cosine'}}"
    )


async def _warn_on_dim_mismatch(neo: Neo4jClient, dim: int) -> None:
    """기존 동명 벡터 인덱스의 차원이 현재와 다르면 CREATE IF NOT EXISTS 가 no-op → DROP 안내."""
    try:
        rows = await neo.run_read_query(
            "SHOW INDEXES YIELD name, options WHERE name = $name",
            {"name": VECTOR_INDEX_NAME},
        )
    except Exception as e:  # SHOW 구문 차이 등 — 사전점검은 최선수단, 검색측 지문검증이 최종 안전망
        log_warning(_STEP, "index_dim_check_failed",
                    "인덱스 차원 사전점검 실패 — 검색측 지문검증이 최종 안전망", error=str(e))
        return
    if not rows:
        return
    config = (rows[0].get("options") or {}).get("indexConfig") or {}
    existing = config.get("vector.dimensions")
    if existing is not None and int(existing) != int(dim):
        log_warning(_STEP, "index_dim_mismatch",
                    f"기존 벡터 인덱스 차원({existing}) ≠ 현재({dim}) — CREATE IF NOT EXISTS 는 no-op. "
                    f"수동 실행 필요: DROP INDEX {VECTOR_INDEX_NAME}",
                    existing=int(existing), current=int(dim))


# ── Table / Column (description 기반, 현행) ────────────────────

def _embedding_query(label: str, key: str) -> str:
    """``embedding`` property SET UNWIND 쿼리(Table/Column — 마커 없음)."""
    return (
        f"UNWIND $items AS item "
        f"MATCH (n:{label} {{{key}: item.{key}, graph_owner: '{ANALYZER_GRAPH_OWNER}'}}) "
        f"SET n.embedding = item.vector"
    )


def _build_table_column_embeddings(state: dict) -> tuple[list[dict], list[dict]]:
    """Table/Column description 수집 → 그룹별 배치 인코딩 → updates."""
    return (
        _encode_pairs(_collect_table_texts(state.get("tables", [])), "id"),
        _encode_pairs(_collect_column_texts(
            state.get("columns", []), state.get("tables", []),
            state.get("has_column_edges", []),
        ), "id"),
    )


def _collect_table_texts(tables: list) -> list[tuple[str, str]]:
    """``Table`` 들의 (fqn, ``"name: description"``) 쌍. description 빈 것은 제외."""
    out: list[tuple[str, str]] = []
    for t in tables:
        desc = (t.description or "").strip()
        if not desc:
            continue
        out.append((t.id, f"{t.name}: {desc}"))
    return out


def _collect_column_texts(columns: list, tables: list, has_column_edges: list) -> list[tuple[str, str]]:
    """``Column`` 들의 (fqn, ``"<table>.<column>: description"``) 쌍.

    테이블 prefix — 동명 컬럼(id/name 등) 벡터가 테이블 구분 없이 뭉치는 것 방지.
    """
    table_name_by_id = {t.id: t.name for t in tables}
    table_id_by_column = {e.child_id: e.parent_id for e in has_column_edges}
    out: list[tuple[str, str]] = []
    for c in columns:
        desc = (c.description or "").strip()
        if not desc:
            continue
        table_name = table_name_by_id.get(table_id_by_column.get(c.id, ""), "")
        label = f"{table_name}.{c.name}" if table_name else c.name
        out.append((c.id, f"{label}: {desc}"))
    return out


def _encode_pairs(pending: list[tuple[str, str]], key_name: str) -> list[dict]:
    """``[(키, 텍스트)]`` → ``[{key_name: 키, "vector": [...]}]``. embedder 1회 호출."""
    if not pending:
        return []
    texts = [text for _, text in pending]
    vectors = embedder.encode_texts(texts)
    return [{key_name: key, "vector": vec} for (key, _), vec in zip(pending, vectors)]


# ── state 반영 ────────────────────────────────────────────────

def _attach_embedding(nodes: list, updates: list[dict], key_attr: str) -> list:
    """``updates`` 의 vector 를 같은 키의 노드에 박은 새 인스턴스 리스트.

    임베딩 못 받은 노드는 그대로 유지 — state 키엔 reducer 가 없어(default replace) 전체
    리스트를 돌려줘야 최종 state·artifact 가 노드를 잃지 않는다.
    """
    key_to_vec = {u[key_attr]: u["vector"] for u in updates}
    return [
        replace(n, embedding=tuple(key_to_vec[getattr(n, key_attr)]))
        if getattr(n, key_attr) in key_to_vec else n
        for n in nodes
    ]
