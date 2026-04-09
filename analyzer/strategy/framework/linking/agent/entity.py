"""DeepAgent 엔티티 식별 + Function→Table 연결."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config.settings import settings
from util.artifact_logger import log_llm_interaction
from util.prompt_executor import get_rule_loader

from analyzer.strategy.framework.common.models import FkEdge, FunctionTableEdge, ReferToEdge

from analyzer.strategy.framework.linking.agent.common import _init_deep_agent_env
from analyzer.strategy.framework.linking.agent.tools import build_tools

logger = logging.getLogger(__name__)

_ENTITY_DIR_HINTS = frozenset({"entity", "entities", "model", "models", "domain", "schema"})
_DTO_DIR_HINTS = frozenset({"dto", "vo", "request", "response", "view"})


def _classify_by_path(file_path: str) -> str:
    """파일 경로의 디렉토리명으로 entity/dto/unknown 사전 분류."""
    parts_lower = {p.lower() for p in Path(file_path).parts} if file_path else set()
    if parts_lower & _ENTITY_DIR_HINTS:
        return "entity"
    if parts_lower & _DTO_DIR_HINTS:
        return "dto"
    return "unknown"


def _build_entity_id_user_message(
    candidates: Optional[List[Dict[str, str]]] = None,
    ddl_table_metadata: Optional[Dict] = None,
) -> str:
    """시나리오별 DeepAgent user message 구성.

    - DDL 있음: 테이블 이름 + FK 목록만 전달 (관계 추적용)
    - DDL 없음: 구조체/클래스 후보 전달 (엔티티 판별 + 관계 추적)
    """
    parts: List[str] = []

    if ddl_table_metadata:
        fk_lines: List[str] = []
        for (_schema, _name), meta in ddl_table_metadata.items():
            orig = meta.get("original_name", _name)
            for fk in (meta.get("foreign_keys") or meta.get("foreignKeys") or []):
                fk_col = fk.get("column", "")
                fk_ref = fk.get("ref", "")
                if fk_col and fk_ref:
                    fk_lines.append(f"- {orig}.{fk_col} → {fk_ref}")

        if fk_lines:
            if len(fk_lines) <= 50:
                parts.append("[이미 식별된 FK — 중복 생성하지 마세요]\n" + "\n".join(fk_lines))
            else:
                parts.append(f"이미 식별된 FK {len(fk_lines)}개 존재. 중복 생성하지 마세요.")

        table_names = [meta.get("original_name", _name) for (_schema, _name), meta in ddl_table_metadata.items()]
        if len(table_names) <= 100:
            parts.append("[테이블 이름] " + ", ".join(table_names))
        else:
            parts.append(f"프로젝트에 테이블 {len(table_names)}개 존재. table_search_tool로 검색하세요.")

        parts.append(
            "위 테이블을 코드에서 사용하는 메서드를 추적하세요. 컬럼 상세는 table_columns_tool로 조회하세요.\n"
            "이미 식별된 FK 이외의 새로운 FK 관계가 코드에서 발견되면 graph_create_fk_tool로 등록하세요."
        )

    elif candidates:
        cand_lines = []
        for c in candidates:
            line = f"- {c['fqn']} (file: {c['file_path']})"
            if c.get("annotations"):
                line += f" [annotations: {c['annotations']}]"
            if c.get("comment"):
                line += f" [comment: {c['comment']}]"
            cand_lines.append(line)
        parts.append("[클래스/구조체 후보]\n" + "\n".join(cand_lines))
        parts.append(
            "\n위 후보에서 DB 엔티티를 식별하고, 사용하는 함수를 추적하세요."
        )

    return "\n".join(parts)


def _entity_system_prompt(has_ddl: bool) -> str:
    from util.prompt_registry import prompts
    return prompts.deep_agent_entity_prompt(has_ddl=has_ddl)


def run_deep_agent_entity_identification(
    module_index: Dict[str, Any],
    state: Dict[str, Any],
    api_key: Optional[str] = None,
    run_dir: Optional[Path] = None,
    ddl_table_metadata: Optional[Dict] = None,
) -> Tuple[List[FunctionTableEdge], List[FkEdge]]:
    """DeepAgent로 엔티티 식별 + Function→Table 연결."""
    has_ddl = bool(ddl_table_metadata)
    has_candidates = bool(module_index)

    if not has_ddl and not has_candidates:
        logger.info("[PHASE:2:DEEP_AGENT:ENTITY] DDL도 후보도 없음 → 즉시 종료")
        return [], []

    try:
        create_deep_agent, model = _init_deep_agent_env(state, api_key=api_key, run_dir=run_dir)
    except Exception as e:
        logger.error("[PHASE:2:DEEP_AGENT:ENTITY] deepagents 미설치 또는 LLM 클라이언트 없음: %s", e)
        return [], []

    _ENTITY_CANDIDATE_KINDS = frozenset({"CLASS", "STRUCT", "UNION"})
    candidates: List[Dict[str, str]] = []
    for fqn, info in (module_index or {}).items():
        if not isinstance(info, dict):
            continue
        if info.get("kind") not in _ENTITY_CANDIDATE_KINDS:
            continue
        name = info.get("name", "")
        fp = info.get("file_path", "")
        path_class = _classify_by_path(fp)
        if path_class == "dto":
            continue
        candidates.append({
            "fqn": fqn,
            "name": name,
            "file_path": fp,
            "annotations": (info.get("annotations") or "")[:100],
            "comment": (info.get("comment") or "")[:100],
            "path_hint": path_class,
        })

    candidates.sort(key=lambda c: (0 if c.get("path_hint") == "entity" else 1))

    if not candidates and not has_ddl:
        logger.info("[PHASE:2:DEEP_AGENT:ENTITY] 후보 없음 + DDL 없음 → 즉시 종료")
        return [], []

    scenario = "ddl_tables" if has_ddl else "candidates"
    logger.info(
        "[PHASE:2:DEEP_AGENT:ENTITY] 시작 | scenario=%s ddl_tables=%d candidates=%d",
        scenario, len(ddl_table_metadata or {}), len(candidates),
    )

    table_collector: List[FunctionTableEdge] = []
    refer_collector: List[ReferToEdge] = []
    fk_collector: List[FkEdge] = []
    collector_lock = threading.Lock()
    tools = build_tools(
        state, "", refer_collector, table_collector,
        fk_collector=fk_collector,
        tool_scope="entity",
        collector_lock=collector_lock,
    )

    system_prompt = _entity_system_prompt(has_ddl)
    agent = create_deep_agent(
        model=model, tools=tools,
        system_prompt=system_prompt,
    )

    da_cfg = settings.deep_agent
    batch_size = da_cfg.entity_batch_size
    max_entity_workers = da_cfg.entity_max_workers

    if has_ddl:
        logger.info("[PHASE:2:DEEP_AGENT:ENTITY] scenario=ddl_tables | 테이블 %d개로 코드 추적", len(ddl_table_metadata or {}))
        user_msg = _build_entity_id_user_message(ddl_table_metadata=ddl_table_metadata)
        try:
            agent.invoke({"messages": [{"role": "user", "content": user_msg}]})
            log_llm_interaction(
                prompt_name="framework/deep_agent_entity_identification",
                input_payload=user_msg,
                output_payload={"table_access": len(table_collector)},
                metadata={"scenario": "ddl_with_tables"},
            )
        except Exception as ex:
            logger.error("[PHASE:2:DEEP_AGENT:ENTITY] invoke 실패 (ddl): %s", ex)
    else:
        batches = [candidates[i:i + batch_size] for i in range(0, max(len(candidates), 1), batch_size)]
        logger.info("[PHASE:2:DEEP_AGENT:ENTITY] scenario=candidates | 후보 %d개 → %d배치 병렬 식별", len(candidates), len(batches))

        def _process_batch(batch_idx: int, batch: list) -> None:
            batch_names = [c.get("name", "?") for c in batch]
            logger.info("[PHASE:2:DEEP_AGENT:ENTITY] 배치 %d 시작 | 후보=%s", batch_idx, batch_names)
            before_count = len(table_collector)
            batch_agent = create_deep_agent(
                model=model, tools=tools,
                system_prompt=system_prompt,
            )
            user_msg = _build_entity_id_user_message(candidates=batch)
            try:
                batch_agent.invoke({"messages": [{"role": "user", "content": user_msg}]})
                log_llm_interaction(
                    prompt_name="framework/deep_agent_entity_identification",
                    input_payload=user_msg,
                    output_payload={"table_access": len(table_collector)},
                    metadata={"batch_start": batch_idx * batch_size, "batch_size": len(batch)},
                )
            except Exception as ex:
                logger.error("[PHASE:2:DEEP_AGENT:ENTITY] invoke 실패 batch=%d: %s", batch_idx, ex)
            added = len(table_collector) - before_count
            if added > 0:
                logger.info("[PHASE:2:DEEP_AGENT:ENTITY] 배치 %d 완료 | READS/WRITES +%d건", batch_idx, added)
            else:
                logger.info("[PHASE:2:DEEP_AGENT:ENTITY] 배치 %d 완료 | 엔티티 식별 안 됨 (후보: %s)", batch_idx, batch_names)

        from concurrent.futures import ThreadPoolExecutor, as_completed
        BATCH_TIMEOUT = 300  # 배치당 최대 5분
        with ThreadPoolExecutor(max_workers=min(len(batches), max_entity_workers)) as executor:
            futures = {executor.submit(_process_batch, idx, b): idx for idx, b in enumerate(batches)}
            for future in as_completed(futures):
                batch_idx = futures[future]
                try:
                    future.result(timeout=BATCH_TIMEOUT)
                except TimeoutError:
                    logger.error("[PHASE:2:DEEP_AGENT:ENTITY] 배치 %d 타임아웃 (%ds) — 스킵", batch_idx, BATCH_TIMEOUT)
                except Exception as ex:
                    logger.error("[PHASE:2:DEEP_AGENT:ENTITY] 배치 %d 스레드 예외: %s", batch_idx, ex)

    identified_tables = {e.table_name for e in table_collector if e.table_name}
    if identified_tables:
        logger.info(
            "[PHASE:2:DEEP_AGENT:ENTITY] 완료 | 식별된 엔티티=%d (%s) | READS/WRITES=%d FK=%d",
            len(identified_tables), ", ".join(sorted(identified_tables)),
            len(table_collector), len(fk_collector),
        )
    else:
        logger.info("[PHASE:2:DEEP_AGENT:ENTITY] 완료 | 엔티티 식별 안 됨 — 후보 %d개 중 엔티티 0개", len(candidates))
    return table_collector, fk_collector
