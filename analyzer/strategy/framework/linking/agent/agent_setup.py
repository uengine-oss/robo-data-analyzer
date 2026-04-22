"""Agent 인프라 — 초기화 + Prompt/Skill 로더 + 도구 팩토리.

Section 1: Agent environment initialization (LLM, ChunkBundle, agent factory)
Section 2: Prompt / Skill loading (prompts/{agent}/*.md, skills/{scope}/*/SKILL.md)
Section 3: Tool factory (LangChain @tool 도구 생성)
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

from config.settings import settings
from analyzer.strategy.framework.common.node_models import (
    ArtifactChunk,
    ColumnRef,
    ConfigSectionChunk,
    FkEdge,
    FunctionTableEdge,
    ReferToEdge,
    SqlStatementChunk,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Section 1: Agent environment initialization
# ═══════════════════════════════════════════════════════════════════════════


def _ensure_bundle_indexes(state: Dict[str, Any]) -> None:
    """state에 span_index / chunk_index가 없으면 ChunkBundle로부터 빌드한다."""
    if state.get("span_index") and state.get("chunk_index"):
        return

    from analyzer.strategy.framework.analysis.chunk_bundle import (
        build_bundle_span_index,
        _get_chunk_bundle_dir_from_run_dir,
    )

    run_dir = state.get("run_dir")
    chunk_dir = _get_chunk_bundle_dir_from_run_dir(run_dir)

    span_index, chunk_index = build_bundle_span_index(
        chunk_bundle_dir=chunk_dir,
        artifact_index=state.get("artifact_index"),
        sql_index=state.get("sql_index"),
    )

    state["span_index"] = span_index
    state["chunk_index"] = chunk_index
    logger.info(
        "[DEEP_AGENT] ChunkBundle 인덱스 빌드 완료 | span=%d chunk=%d",
        len(span_index), len(chunk_index),
    )


def build_common_middleware(model: Any) -> list:
    """scanning/linking 공용 미들웨어 세트.

    Progressive Disclosure 기반 설계: 시스템 프롬프트는 공통 지침만 담고,
    상황별 지침은 `read_skill` tool 결과로 로드된다.
    """
    from langchain.agents.middleware import (
        ContextEditingMiddleware,
        ClearToolUsesEdit,
        SummarizationMiddleware,
        TodoListMiddleware,
    )
    from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware

    return [
        TodoListMiddleware(),
        ContextEditingMiddleware(edits=[
            ClearToolUsesEdit(trigger=70_000, keep=3),
        ]),
        SummarizationMiddleware(model=model, trigger=("tokens", 85_000)),
        PatchToolCallsMiddleware(),
    ]


def _build_agent_factory(model: Any) -> Callable:
    """create_agent + 공용 미들웨어를 바인딩한 에이전트 팩토리."""
    from langchain.agents import create_agent

    middleware = build_common_middleware(model)

    def factory(*, tools: list, system_prompt: str, **kwargs: Any) -> Any:
        return create_agent(
            model=model,
            tools=tools,
            system_prompt=system_prompt,
            middleware=middleware,
            **kwargs,
        )

    return factory


def _init_agent_env(
    state: Dict[str, Any],
    api_key: Optional[str] = None,
    run_dir: Optional[Path] = None,
) -> Tuple[Callable, Any]:
    """Agent 실행 환경을 초기화한다.

    Returns:
        (agent_factory, model) — 에이전트 생성 팩토리와 LLM 모델.
    """
    from client.llm_client import get_llm

    if run_dir:
        state.setdefault("run_dir", str(run_dir))

    _ensure_bundle_indexes(state)

    model = get_llm(api_key=api_key) if api_key else get_llm()
    return _build_agent_factory(model), model


# ═══════════════════════════════════════════════════════════════════════════
# Section 2: Prompt / Skill loading
# ═══════════════════════════════════════════════════════════════════════════


# 프로젝트 루트 기준 rules/deep_agent 경로
_BASE = Path(__file__).resolve().parents[5] / "rules" / "deep_agent"
_PROMPTS_DIR = _BASE / "prompts"
_SKILLS_DIR = _BASE / "skills"

AgentScope = Literal["scanning", "linking"]


@dataclass(frozen=True)
class SkillInfo:
    """SKILL.md에서 로드된 Skill 정보.

    Progressive Disclosure 기반: LLM이 필요 시 `read_skill(name)` 으로 body를 로드한다.
    """
    name: str
    description: str
    body: str  # 마크다운 본문 (프론트매터 제외)


def _parse_skill_md(path: Path) -> Optional[SkillInfo]:
    """SKILL.md 파일을 파싱하여 SkillInfo로 반환한다.

    YAML 프론트매터(--- ... ---) + 마크다운 본문 구조.
    프론트매터에서 `name` · `description` 두 필드만 사용.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("[SKILL] 파일 읽기 실패 | path=%s error=%s", path, e)
        return None

    if not text.startswith("---"):
        logger.warning("[SKILL] 프론트매터 없음 | path=%s", path)
        return None

    parts = text.split("---", 2)
    if len(parts) < 3:
        logger.warning("[SKILL] 프론트매터 파싱 실패 | path=%s", path)
        return None

    frontmatter_text = parts[1].strip()
    body = parts[2].strip()

    # 간단한 YAML 파싱 (name, description 만 수집)
    fm: Dict[str, str] = {}
    current_key = ""
    current_value_lines: List[str] = []

    for line in frontmatter_text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if ":" in stripped and not stripped.startswith("-") and not stripped.startswith(" "):
            if current_key:
                fm[current_key] = " ".join(current_value_lines).strip()
            key, _, val = stripped.partition(":")
            current_key = key.strip()
            val = val.strip()
            if val == ">":
                val = ""
            current_value_lines = [val] if val else []
        else:
            current_value_lines.append(stripped)
    if current_key:
        fm[current_key] = " ".join(current_value_lines).strip()

    name = fm.get("name", path.parent.name)
    description = fm.get("description", "")

    return SkillInfo(name=name, description=description, body=body)


def _load_skills_from_dir(base: Path) -> Dict[str, SkillInfo]:
    """지정 디렉토리 아래의 모든 SKILL.md 를 로드."""
    skills: Dict[str, SkillInfo] = {}
    if not base.exists():
        return skills
    for skill_dir in sorted(base.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        info = _parse_skill_md(skill_md)
        if info:
            skills[info.name] = info
    return skills


_SKILL_CACHE: Dict[str, Dict[str, SkillInfo]] = {}


def load_skills_for_agent(agent: AgentScope) -> Dict[str, SkillInfo]:
    """지정 에이전트가 접근 가능한 스킬만 로드한다 (자기 전용 + shared).

    - scanning → skills/scanning/* + skills/shared/*
    - linking  → skills/linking/*  + skills/shared/*

    동일 프로세스 내 중복 로드는 모듈-레벨 캐시로 회피.
    """
    if agent in _SKILL_CACHE:
        return _SKILL_CACHE[agent]

    agent_dir = _SKILLS_DIR / agent
    shared_dir = _SKILLS_DIR / "shared"

    skills = _load_skills_from_dir(agent_dir)
    for name, info in _load_skills_from_dir(shared_dir).items():
        skills.setdefault(name, info)  # agent 전용이 shared 보다 우선

    logger.info(
        "[SKILL:%s] scope 로드 완료 | skills=%d (%s)",
        agent, len(skills), list(skills.keys()),
    )
    _SKILL_CACHE[agent] = skills
    return skills


def load_system_prompt(agent: AgentScope) -> str:
    """prompts/{agent}/system.md 본문을 반환.

    프론트매터가 있으면 제거하고 본문만 반환.
    """
    path = _PROMPTS_DIR / agent / "system.md"
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return text.strip()


def load_user_template(agent: AgentScope) -> str:
    """prompts/{agent}/user_template.md 본문을 반환."""
    path = _PROMPTS_DIR / agent / "user_template.md"
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return text.strip()


def make_skill_tools(agent: AgentScope) -> list:
    """list_skills() / read_skill(name) 두 도구를 반환한다.

    Progressive Disclosure 패턴:
      - list_skills: 스킬 메타데이터(name, description) 목록 JSON.
      - read_skill:  지정 스킬의 본문을 반환.

    지정 에이전트 스코프에 해당하는 스킬만 노출 (교차 격리).
    각 호출마다 에이전트의 스킬 선택 내역을 INFO 로그로 남겨 추적 가능.
    """
    from langchain_core.tools import tool as lc_tool

    @lc_tool
    def list_skills() -> str:
        """사용 가능한 스킬 목록(JSON) 을 반환. 각 항목 = {name, description}.

        작업 중 특정 패턴/프레임워크 지식이 필요하다고 판단되면 먼저 이 도구로 후보를 확인한 뒤
        `read_skill(name)` 으로 본문을 로드하세요.
        """
        skills = load_skills_for_agent(agent)
        entries = [
            {"name": s.name, "description": s.description}
            for s in skills.values()
        ]
        logger.info(
            "[SKILL:%s] list_skills 호출 | 후보=%s",
            agent, [s["name"] for s in entries],
        )
        return json.dumps(entries, ensure_ascii=False)

    @lc_tool
    def read_skill(name: str) -> str:
        """지정 스킬의 본문(마크다운) 을 로드한다. 이름은 list_skills 의 결과에서 고르세요."""
        skills = load_skills_for_agent(agent)
        skill = skills.get(name)
        if not skill:
            logger.warning("[SKILL:%s] read_skill 실패 | name=%s (미존재)", agent, name)
            return f"SKILL_NOT_FOUND: '{name}'"
        logger.info(
            "[SKILL:%s] read_skill 채택 | name=%s body_chars=%d",
            agent, name, len(skill.body),
        )
        return skill.body

    return [list_skills, read_skill]


# ═══════════════════════════════════════════════════════════════════════════
# Section 3: Tool factory
# ═══════════════════════════════════════════════════════════════════════════


# ---------------------------------------------------------------------------
# 3-1. 결과 수집기 (Collector)
# ---------------------------------------------------------------------------


@dataclass
class ToolResultCollector:
    """도구 호출 결과를 스레드-세이프하게 수집한다."""
    refer_to: List[ReferToEdge] = field(default_factory=list)
    table_access: List[FunctionTableEdge] = field(default_factory=list)
    fk: List[FkEdge] = field(default_factory=list)
    columns: List[ColumnRef] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def snapshot_and_clear(self) -> Dict[str, list]:
        with self.lock:
            result = {
                "refer_to": list(self.refer_to),
                "table_access": list(self.table_access),
                "fk": list(self.fk),
                "columns": list(self.columns),
            }
            self.refer_to.clear()
            self.table_access.clear()
            self.fk.clear()
            self.columns.clear()
        return result

    @property
    def total_count(self) -> int:
        return len(self.refer_to) + len(self.table_access) + len(self.fk) + len(self.columns)


# ---------------------------------------------------------------------------
# 3-2. 인덱스 조회 헬퍼 (순수 함수)
# ---------------------------------------------------------------------------


def sql_lookup(sql_key: str, sql_index: Dict[str, SqlStatementChunk]) -> Optional[Dict[str, Any]]:
    stmt = sql_index.get(sql_key)
    if not stmt:
        return None
    return {
        "sql_key": stmt.sql_key,
        "sql_type": stmt.sql_type,
        "sql_text": getattr(stmt, "sql_text", ""),
        "file_path": getattr(stmt, "file_path", ""),
        "start_line": getattr(stmt, "start_line", 0),
        "end_line": getattr(stmt, "end_line", 0),
    }


def config_lookup(config_key: str, config_index: Dict[str, ConfigSectionChunk]) -> Optional[Dict[str, Any]]:
    cfg = config_index.get(config_key)
    if not cfg:
        return None
    return {
        "config_key_path": cfg.config_key_path,
        "value": getattr(cfg, "raw_snippet", "") or getattr(cfg, "value", ""),
        "file_path": getattr(cfg, "file_path", ""),
        "start_line": getattr(cfg, "start_line", 0),
        "end_line": getattr(cfg, "end_line", 0),
    }


def build_artifact_lookup_index(artifact_index: Dict[str, ArtifactChunk]) -> Dict[str, str]:
    idx: Dict[str, str] = {}
    for cid, ac in (artifact_index or {}).items():
        cid_norm = (cid or "").strip()
        if cid_norm:
            idx.setdefault(cid_norm, cid_norm)
        hint = getattr(ac, "hint_key", None)
        hint_norm = (hint or "").strip()
        if hint_norm:
            idx.setdefault(hint_norm, cid_norm)
    return idx


# ---------------------------------------------------------------------------
# 3-3. 도구 팩토리 (메인 진입점)
# ---------------------------------------------------------------------------


def build_tools(
    state: Dict[str, Any],
    collector: ToolResultCollector,
    function_id: str = "",
    variable_id: Optional[str] = None,
    batch_startpoint_tables: Optional[set] = None,
) -> List[Any]:
    """linking 에이전트용 도구 전체 세트를 반환한다.

    모든 링킹 에이전트는 동일 도구 세트를 사용. 상황에 맞는 도구 선택은
    SKILL.md 본문의 지침에 따라 LLM 이 판단한다.
    """
    from langchain_core.tools import tool as lc_tool
    from analyzer.strategy.framework.linking.table_linker import sql_op_to_method_relation

    default_confidence = settings.deep_agent.default_confidence
    lock = collector.lock

    sql_index: Dict[str, SqlStatementChunk] = state.get("sql_index") or {}
    config_index: Dict[str, ConfigSectionChunk] = state.get("config_index") or {}
    artifact_index: Dict[str, ArtifactChunk] = state.get("artifact_index") or {}
    artifact_key_index = build_artifact_lookup_index(artifact_index)
    ddl_meta = state.get("ddl_table_metadata") or {}
    span_index = state.get("span_index") or {}
    chunk_index = state.get("chunk_index") or {}
    data_dir = state.get("data_dir") or ""

    # 기존 FK 세트 (중복 생성 방지)
    existing_fk_set: set = set()
    for (_s, _n), info in ddl_meta.items():
        for fk in (info.get("foreign_keys") or info.get("foreignKeys") or []):
            fk_col = (fk.get("column") or "").strip().upper()
            fk_ref = (fk.get("ref") or "").strip().upper()
            if fk_col and fk_ref:
                existing_fk_set.add((_n.upper(), fk_col, fk_ref))

    from analyzer.strategy.framework.analysis.chunk_bundle import (
        bundle_search as _bundle_search,
        bundle_get_chunk as _bundle_get_chunk,
    )

    def _enrich_with_modulename(result: Dict[str, Any]) -> Dict[str, Any]:
        fp = result.get("filePath", "")
        if fp:
            fname = fp.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            result["moduleName"] = fname.rsplit(".", 1)[0] if "." in fname else fname
        return result

    # ── 인덱스 조회 도구 ──

    @lc_tool
    def sql_lookup_tool(sql_key: str) -> str:
        """sql_key로 SQL 문장을 조회한다."""
        r = sql_lookup(sql_key, sql_index)
        logger.info("[TOOL] sql_lookup | key=%s found=%s", sql_key, bool(r))
        return json.dumps(r, ensure_ascii=False) if r else ""

    @lc_tool
    def config_lookup_tool(config_key: str) -> str:
        """config_key로 설정 엔트리를 조회한다."""
        r = config_lookup(config_key, config_index)
        logger.info("[TOOL] config_lookup | key=%s found=%s", config_key, bool(r))
        return json.dumps(r, ensure_ascii=False) if r else ""

    @lc_tool
    def artifact_lookup_tool(key: str) -> str:
        """chunk_id 또는 hint_key(ejb-name)로 ArtifactChunk를 조회한다."""
        cid = artifact_key_index.get((key or "").strip())
        if not cid:
            return ""
        ac = artifact_index.get(cid)
        if not ac:
            return ""
        data = {
            "chunk_id": cid,
            "chunk_type": getattr(ac, "chunk_type", ""),
            "hint_key": getattr(ac, "hint_key", ""),
            "file_path": getattr(ac, "source_path", ""),
            "text": getattr(ac, "text", "")[:2000],
        }
        logger.info("[TOOL] artifact_lookup | key=%s chunk_id=%s", key, cid)
        return json.dumps(data, ensure_ascii=False)

    # ── 코드 검색 도구 ──

    @lc_tool
    def bundle_search_tool(query: str, kind: str = "symbol") -> str:
        """ChunkBundle에서 심볼/텍스트 검색한다. kind: symbol|text."""
        results = _bundle_search(data_dir, query, kind, span_index=span_index, chunk_index=chunk_index)
        enriched = [_enrich_with_modulename(r) for r in results[:20]]
        logger.info("[TOOL] bundle_search | query=%s kind=%s results=%d", query, kind, len(enriched))
        return json.dumps(enriched, ensure_ascii=False)

    @lc_tool
    def bundle_get_chunk_tool(chunk_id: str) -> str:
        """chunk_id로 코드 조각을 조회한다."""
        r = _bundle_get_chunk(chunk_id, chunk_index=chunk_index)
        if r:
            _enrich_with_modulename(r)
        logger.info("[TOOL] bundle_get_chunk | chunk_id=%s found=%s", chunk_id, bool(r))
        return json.dumps(r, ensure_ascii=False) if r else ""

    # ── 관계 등록 도구 ──

    @lc_tool
    def graph_create_refer_to_tool(
        target_type: str, target_key: str,
        evidence: str = "", source_function_id: str = "",
    ) -> str:
        """REFER_TO/CALLS_SQL/USES_CONFIG 엣지를 생성한다. source_function_id 필수."""
        mid = source_function_id.strip() if source_function_id else function_id
        if not mid:
            return "error: source_function_id required"
        with lock:
            collector.refer_to.append(ReferToEdge(
                function_id=mid,
                target_type=target_type,
                target_id=target_key,
                evidence={"note": evidence} if evidence else {},
                confidence=default_confidence,
                variable_id=variable_id,
            ))
        logger.info("[TOOL] refer_to | %s → %s:%s", mid, target_type, target_key)
        return "ok"

    @lc_tool
    def graph_create_table_access_tool(
        table_name: str, access: str,
        evidence: str = "", source_function_id: str = "",
    ) -> str:
        """READS/WRITES 엣지를 생성한다. access: READS|WRITES. source_function_id 필수."""
        mid = source_function_id.strip() if source_function_id else function_id
        rel = access.upper() if access else "READS"
        if rel not in ("READS", "WRITES"):
            rel = "READS"
        if not mid:
            return "error: source_function_id required"
        with lock:
            collector.table_access.append(FunctionTableEdge(
                function_id=mid,
                table_schema="",
                table_name=table_name,
                relation=sql_op_to_method_relation("INSERT" if rel == "WRITES" else "SELECT"),
                variable_id=variable_id,
            ))
        logger.info("[TOOL] table_access | %s → %s %s", mid, rel, table_name)
        return "ok"

    @lc_tool
    def graph_create_column_tool(
        table_name: str, column_name: str,
        dtype: str = "", description: str = "",
    ) -> str:
        """Table 의 Column 을 등록한다. (Table)-[:HAS_COLUMN]->(Column).

        코드/구조체/DTO 정의에서 실제 관찰된 필드만 등록. 추측 금지.
        description 은 해당 함수에서 이 컬럼을 왜/어떻게 쓰는지 1~2 문장. 모르면 빈 문자열.
        dtype 도 관찰된 경우만 (C struct 필드 타입 / DTO 필드 타입). 모르면 빈 문자열.
        """
        tbl = (table_name or "").strip()
        col = (column_name or "").strip()
        if not tbl or not col:
            return "error: table_name and column_name required"
        with lock:
            collector.columns.append(ColumnRef(
                table_schema="",
                table_name=tbl,
                column_name=col,
                dtype=(dtype or "").strip() or None,
                description=(description or "").strip() or None,
            ))
        logger.info("[TOOL] column | %s.%s%s", tbl, col, f" ({dtype})" if dtype else "")
        return "ok"

    @lc_tool
    def graph_create_fk_tool(
        from_table: str, from_column: str,
        to_table: str, to_column: str,
        evidence: str = "", from_schema: str = "", to_schema: str = "",
    ) -> str:
        """FK 관계를 등록한다. from_table=FK쪽(자식), to_table=PK쪽(부모)."""
        if from_table.upper() == to_table.upper():
            return "error: self-referencing FK not allowed"
        if batch_startpoint_tables:
            if from_table.upper() not in {t.upper() for t in batch_startpoint_tables}:
                return "skipped: from_table not in this batch's startpoints"
        if ddl_meta:
            known_tables = {n.upper() for (_, n) in ddl_meta.keys()}
            for tbl in (from_table, to_table):
                if tbl.upper() not in known_tables:
                    return f"error: '{tbl}' not found in DDL table list"
        ref_key = f"{to_table}.{to_column}".upper()
        check_key = (from_table.upper(), from_column.upper(), ref_key)
        if check_key in existing_fk_set:
            return "already exists (from DDL)"
        existing_fk_set.add(check_key)
        with lock:
            collector.fk.append(FkEdge(
                from_schema=from_schema, from_table=from_table, from_column=from_column,
                to_schema=to_schema, to_table=to_table, to_column=to_column,
                candidate=True,
            ))
        logger.info("[TOOL] fk | %s.%s → %s.%s", from_table, from_column, to_table, to_column)
        return "ok"

    # ── DDL 조회 도구 ──

    @lc_tool
    def table_search_tool(keyword: str) -> str:
        """키워드로 DDL 테이블을 검색한다. 대소문자/언더스코어 무시."""
        keyword_norm = keyword.lower().replace("_", "")
        results = [
            {"schema": s, "name": n}
            for (s, n) in ddl_meta
            if keyword_norm in n.lower().replace("_", "")
        ]
        logger.info("[TOOL] table_search | keyword=%s results=%d", keyword, len(results))
        return json.dumps(results[:20], ensure_ascii=False) if results else "no matching tables found"

    @lc_tool
    def table_columns_tool(table_name: str, table_schema: str = "") -> str:
        """테이블의 컬럼 목록과 PK 정보를 조회한다."""
        for (s, n), info in ddl_meta.items():
            if n.lower() == table_name.lower() and (not table_schema or s.lower() == table_schema.lower()):
                columns = info.get("columns") or []
                pks = info.get("primary_keys") or []
                col_names = [c.get("name", "") if isinstance(c, dict) else str(c) for c in columns]
                logger.info("[TOOL] table_columns | table=%s columns=%d", table_name, len(col_names))
                return json.dumps({"schema": s, "name": n, "primary_keys": pks, "columns": col_names}, ensure_ascii=False)
        return f"table '{table_name}' not found"

    return [
        sql_lookup_tool, config_lookup_tool, artifact_lookup_tool,
        bundle_search_tool, bundle_get_chunk_tool,
        graph_create_refer_to_tool, graph_create_table_access_tool,
        graph_create_column_tool, graph_create_fk_tool,
        table_search_tool, table_columns_tool,
    ]
