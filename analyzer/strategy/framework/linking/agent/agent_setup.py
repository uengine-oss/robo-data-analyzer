"""Agent 인프라 — 초기화 + Skill 로더 + 도구 팩토리.

Section 1: Agent environment initialization (LLM, ChunkBundle, agent factory)
Section 2: Skill loading (SKILL.md discovery, match, prompt build)
Section 3: Tool factory (LangChain @tool 도구 생성, Skill별 조합)
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

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


# ---------------------------------------------------------------------------
# ChunkBundle 인덱스 보장
# ---------------------------------------------------------------------------


def _ensure_bundle_indexes(state: Dict[str, Any]) -> None:
    """state에 span_index / chunk_index가 없으면 ChunkBundle로부터 빌드한다.

    이미 존재하면 아무것도 하지 않는다.
    빌드 결과는 state에 직접 저장하여 이후 호출에서 재사용한다.
    """
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


# ---------------------------------------------------------------------------
# DeepAgent 환경 초기화
# ---------------------------------------------------------------------------


def _build_agent_factory(model: Any) -> Callable:
    """create_agent + 최소 미들웨어를 바인딩한 에이전트 팩토리를 반환한다.

    FilesystemMiddleware 제거 → 내장 도구(write_file/edit_file/execute 등) 비활성화.
    MemoryMiddleware / AnthropicPromptCachingMiddleware 제거 (불필요).

    미들웨어 (순서: 가벼운 방어 → 무거운 작업 → 최종 상한):
      1. TodoListMiddleware: 계획 관리
      2. ContextEditingMiddleware + ClearToolUsesEdit: 오래된 tool 결과 pruning (70K trigger)
      3. SummarizationMiddleware: context overflow 안전망 (85K trigger)
      4. ToolCallLimitMiddleware: run당 총 tool 호출 상한
      5. ModelCallLimitMiddleware: LLM 호출 상한 (무한루프·비용 방어)
      6. PatchToolCallsMiddleware: tool call 보정
    """
    from langchain.agents import create_agent
    from langchain.agents.middleware import (
        ContextEditingMiddleware,
        ClearToolUsesEdit,
        ModelCallLimitMiddleware,
        SummarizationMiddleware,
        TodoListMiddleware,
        ToolCallLimitMiddleware,
    )
    from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware

    middleware = [
        TodoListMiddleware(),
        ContextEditingMiddleware(edits=[
            ClearToolUsesEdit(trigger=70_000, keep=3),
        ]),
        SummarizationMiddleware(model=model, trigger=("tokens", 85_000)),
        PatchToolCallsMiddleware(),
    ]

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

    수행 작업:
      1. run_dir을 state에 설정
      2. ChunkBundle 인덱스 보장
      3. LLM 모델 인스턴스 생성
      4. create_agent 기반 팩토리 구성 (최소 미들웨어)

    Args:
        state: pipeline state. span_index/chunk_index가 없으면 자동 빌드.
        api_key: LLM API 키 (없으면 기본값 사용).
        run_dir: 실행 디렉토리.

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
# Section 2: Skill loading
# ═══════════════════════════════════════════════════════════════════════════


# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

# Skill 디렉토리 기본 경로 (프로젝트 루트 기준)
_SKILLS_DIR = Path(__file__).resolve().parents[5] / "rules" / "deep_agent" / "skills"

# 입력 카테고리 → Skill 이름 매핑
_CATEGORY_TO_SKILL: Dict[str, str] = {
    # 엔티티 관련 (DDL 없을 때)
    "entity": "entity-analysis",
    "bean": "entity-analysis",
    "cmp_mapping": "entity-analysis",
    # SQL 관련
    "sql": "sql-tracking",
    # 설정 관련
    "config": "config-tracking",
    # DDL 테이블 추적 (별도 호출)
    "ddl_tables": "table-tracking",
}

# 카테고리 미매칭 시 기본 Skill
_DEFAULT_SKILL = "sql-tracking"


# ---------------------------------------------------------------------------
# 데이터 모델
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SkillInfo:
    """SKILL.md에서 로드된 Skill 정보."""
    name: str
    description: str
    allowed_tools: List[str]
    body: str  # 마크다운 본문 (프론트매터 제외)


# ---------------------------------------------------------------------------
# 로더
# ---------------------------------------------------------------------------


def _parse_skill_md(path: Path) -> Optional[SkillInfo]:
    """SKILL.md 파일을 파싱하여 SkillInfo로 반환한다.

    YAML 프론트매터(--- ... ---) + 마크다운 본문 구조를 파싱.
    프론트매터가 없으면 None 반환.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("[SKILL] 파일 읽기 실패 | path=%s error=%s", path, e)
        return None

    # YAML 프론트매터 파싱 (--- ... --- 구간)
    if not text.startswith("---"):
        logger.warning("[SKILL] 프론트매터 없음 | path=%s", path)
        return None

    parts = text.split("---", 2)
    if len(parts) < 3:
        logger.warning("[SKILL] 프론트매터 파싱 실패 | path=%s", path)
        return None

    frontmatter_text = parts[1].strip()
    body = parts[2].strip()

    # 간단한 YAML 파싱 (외부 의존성 없이)
    fm: Dict[str, str] = {}
    current_key = ""
    current_value_lines: List[str] = []

    for line in frontmatter_text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if ":" in stripped and not stripped.startswith("-") and not stripped.startswith(" "):
            # 이전 키 저장
            if current_key:
                fm[current_key] = " ".join(current_value_lines).strip()
            key, _, val = stripped.partition(":")
            current_key = key.strip()
            val = val.strip()
            # YAML > 표시자 제거
            if val == ">":
                val = ""
            current_value_lines = [val] if val else []
        else:
            # 멀티라인 값 이어가기
            current_value_lines.append(stripped)
    if current_key:
        fm[current_key] = " ".join(current_value_lines).strip()

    name = fm.get("name", path.parent.name)
    description = fm.get("description", "")
    allowed_tools_str = fm.get("allowed-tools", "")
    allowed_tools = [t.strip() for t in allowed_tools_str.split(",") if t.strip()]

    return SkillInfo(
        name=name,
        description=description,
        allowed_tools=allowed_tools,
        body=body,
    )


def load_all_skills(skills_dir: Optional[Path] = None) -> Dict[str, SkillInfo]:
    """skills 디렉토리에서 모든 SKILL.md를 로드하여 {name: SkillInfo} 딕셔너리로 반환한다."""
    base = skills_dir or _SKILLS_DIR
    skills: Dict[str, SkillInfo] = {}

    if not base.exists():
        logger.warning("[SKILL] skills 디렉토리 없음 | path=%s", base)
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
            logger.debug("[SKILL] 로드 완료 | name=%s tools=%s", info.name, info.allowed_tools)

    logger.info("[SKILL] 전체 로드 완료 | skills=%d (%s)", len(skills), list(skills.keys()))
    return skills


# ---------------------------------------------------------------------------
# Skill 선택
# ---------------------------------------------------------------------------


def select_skill_for_category(category: str, skills: Dict[str, SkillInfo]) -> Optional[SkillInfo]:
    """입력 카테고리에 맞는 Skill을 선택한다.

    Args:
        category: UnresolvedLink의 카테고리 (entity, sql, config 등).
        skills: load_all_skills()로 로드된 Skill 딕셔너리.

    Returns:
        매칭된 SkillInfo. 매칭 실패 시 기본 Skill 반환. Skill 자체가 없으면 None.
    """
    skill_name = _CATEGORY_TO_SKILL.get(category, _DEFAULT_SKILL)
    skill = skills.get(skill_name)

    if not skill:
        logger.warning("[SKILL] 카테고리 '%s' → Skill '%s' 없음", category, skill_name)
        # 폴백: 아무 Skill이라도 반환
        if skills:
            return next(iter(skills.values()))
        return None

    return skill


def build_system_prompt_with_skill(skill: SkillInfo) -> str:
    """Skill 본문을 시스템 프롬프트로 변환한다.

    공통 절대 규칙 + Skill 본문을 결합하여 최종 시스템 프롬프트를 구성한다.
    """
    common_rules = (
        "당신은 소스코드 분석 전문가 에이전트입니다.\n\n"
        "[절대 규칙]\n"
        "- 오직 제공된 도구만 사용하십시오.\n"
        "- 확신이 없으면 관계를 생성하지 마십시오.\n"
        "- 하나를 찾았다고 멈추지 말고 모든 사용처를 빠짐없이 찾으십시오.\n"
        "- 검색 결과가 0건이면 검색어를 바꿔 최소 2~3회 재시도하십시오.\n"
        "- source_function_id는 반드시 bundle_search에서 발견한 실제 함수의 chunk_id를 사용하십시오.\n\n"
    )
    return common_rules + skill.body


# ═══════════════════════════════════════════════════════════════════════════
# Section 3: Tool factory
# ═══════════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════════
# 3-1. 결과 수집기 (Collector)
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class ToolResultCollector:
    """도구 호출 결과를 스레드-세이프하게 수집한다.

    각 도구는 이 collector에 결과를 추가하고, phase_linking에서
    배치 단위로 꺼내어 Neo4j에 반영한다.
    """
    refer_to: List[ReferToEdge] = field(default_factory=list)
    table_access: List[FunctionTableEdge] = field(default_factory=list)
    fk: List[FkEdge] = field(default_factory=list)
    columns: List[ColumnRef] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def snapshot_and_clear(self) -> Dict[str, list]:
        """현재까지 수집된 결과를 반환하고 내부 리스트를 비운다.

        Neo4j 즉시 반영 시 호출: 결과를 꺼내고 → Neo4j에 쓰고 → 다음 배치.
        """
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
        """수집된 전체 엣지 수."""
        return len(self.refer_to) + len(self.table_access) + len(self.fk) + len(self.columns)


# ═══════════════════════════════════════════════════════════════════════════
# 3-2. 인덱스 조회 헬퍼 (순수 함수)
# ═══════════════════════════════════════════════════════════════════════════


def sql_lookup(sql_key: str, sql_index: Dict[str, SqlStatementChunk]) -> Optional[Dict[str, Any]]:
    """sql_key로 SQL 문장을 조회한다."""
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
    """config_key로 설정 엔트리를 조회한다."""
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
    """hint_key 또는 chunk_id로 ArtifactChunk를 찾기 위한 역인덱스를 생성한다."""
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


# ═══════════════════════════════════════════════════════════════════════════
# 3-3. 도구 팩토리 (메인 진입점)
# ═══════════════════════════════════════════════════════════════════════════


def build_tools(
    state: Dict[str, Any],
    collector: ToolResultCollector,
    function_id: str = "",
    variable_id: Optional[str] = None,
    skill_name: str = "",
    batch_startpoint_tables: Optional[set] = None,
) -> List[Any]:
    """Skill에 맞는 LangChain 호환 도구 목록을 생성한다.

    Args:
        state: pipeline state (인덱스, DDL 메타 등 포함).
        collector: 도구 결과 수집기 (스레드-세이프).
        function_id: 기본 소스 함수 ID (미매칭 키 추적 시 사용).
        variable_id: 기본 소스 변수 ID (변수 기반 참조 시 사용).
        skill_name: 활성화할 Skill 이름. 이에 따라 노출할 도구가 결정됨.
            - "entity-analysis": 코드 검색 + table_access + fk
            - "table-tracking":  코드 검색 + table_access + fk + DDL 조회
            - "sql-tracking":    SQL 조회 + 코드 검색 + refer_to + table_access + fk
            - "config-tracking": Config 조회 + 코드 검색 + refer_to
            - "" (빈 문자열): 모든 도구 (폴백)

    Returns:
        LangChain @tool 데코레이터가 적용된 도구 함수 리스트.
    """
    from langchain_core.tools import tool as lc_tool
    from analyzer.strategy.framework.linking.table_linker import sql_op_to_method_relation

    default_confidence = settings.deep_agent.default_confidence
    lock = collector.lock

    # ── state에서 인덱스 참조 ──
    sql_index: Dict[str, SqlStatementChunk] = state.get("sql_index") or {}
    config_index: Dict[str, ConfigSectionChunk] = state.get("config_index") or {}
    artifact_index: Dict[str, ArtifactChunk] = state.get("artifact_index") or {}
    artifact_key_index = build_artifact_lookup_index(artifact_index)
    ddl_meta = state.get("ddl_table_metadata") or {}
    span_index = state.get("span_index") or {}
    chunk_index = state.get("chunk_index") or {}
    data_dir = state.get("data_dir") or ""

    # ── 기존 FK 세트 (중복 생성 방지) ──
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

    # ── 유틸: filePath → moduleName 추출 ──
    def _enrich_with_modulename(result: Dict[str, Any]) -> Dict[str, Any]:
        fp = result.get("filePath", "")
        if fp:
            fname = fp.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            result["moduleName"] = fname.rsplit(".", 1)[0] if "." in fname else fname
        return result

    # ─────────────────────────────────────────────────────────────
    # 도구 정의 (각 도구는 @lc_tool 데코레이터 적용)
    # ─────────────────────────────────────────────────────────────

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
        # 자기 참조 방지
        if from_table.upper() == to_table.upper():
            return "error: self-referencing FK not allowed"
        # 배치별 방향 필터: from_table이 이 배치의 startpoint여야 함
        if batch_startpoint_tables:
            if from_table.upper() not in {t.upper() for t in batch_startpoint_tables}:
                return "skipped: from_table not in this batch's startpoints"
        # DDL 테이블 목록 기반 검증
        if ddl_meta:
            known_tables = {n.upper() for (_, n) in ddl_meta.keys()}
            for tbl in (from_table, to_table):
                if tbl.upper() not in known_tables:
                    return f"error: '{tbl}' not found in DDL table list"
        # 중복 검사
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

    # ─────────────────────────────────────────────────────────────
    # Skill별 도구 조합
    # ─────────────────────────────────────────────────────────────

    return _assemble_tools_for_skill(
        skill_name=skill_name,
        has_ddl=bool(ddl_meta),
        tools_map={
            # 인덱스 조회
            "sql_lookup": sql_lookup_tool,
            "config_lookup": config_lookup_tool,
            "artifact_lookup": artifact_lookup_tool,
            # 코드 검색
            "bundle_search": bundle_search_tool,
            "bundle_get_chunk": bundle_get_chunk_tool,
            # 관계 등록
            "refer_to": graph_create_refer_to_tool,
            "table_access": graph_create_table_access_tool,
            "column": graph_create_column_tool,
            "fk": graph_create_fk_tool,
            # DDL 조회
            "table_search": table_search_tool,
            "table_columns": table_columns_tool,
        },
    )


# ═══════════════════════════════════════════════════════════════════════════
# 3-4. Skill별 도구 조합 로직
# ═══════════════════════════════════════════════════════════════════════════

# Skill별 필요한 도구 키 목록
_SKILL_TOOL_SETS: Dict[str, List[str]] = {
    "entity-analysis": [
        "bundle_search", "bundle_get_chunk",
        "table_access", "column", "fk",
        # DDL 도구는 has_ddl 시 자동 추가
    ],
    "table-tracking": [
        "bundle_search", "bundle_get_chunk",
        "table_access", "column", "fk",
        "table_search", "table_columns",
    ],
    "sql-tracking": [
        "sql_lookup", "artifact_lookup",
        "bundle_search", "bundle_get_chunk",
        "refer_to", "table_access", "column", "fk",
        # DDL 도구는 has_ddl 시 자동 추가
    ],
    "config-tracking": [
        "config_lookup",
        "bundle_search", "bundle_get_chunk",
        "refer_to",
    ],
}

# DDL 조회 도구 키
_DDL_TOOL_KEYS = ["table_search", "table_columns"]


def _assemble_tools_for_skill(
    skill_name: str,
    has_ddl: bool,
    tools_map: Dict[str, Any],
) -> List[Any]:
    """Skill 이름에 맞는 도구 목록을 조합한다.

    Args:
        skill_name: 활성화된 Skill 이름.
        has_ddl: DDL 메타데이터 존재 여부.
        tools_map: 도구 키 → @tool 함수 매핑.

    Returns:
        해당 Skill에 필요한 도구 함수 리스트.
    """
    # Skill 이름으로 필요 도구 키 목록 조회
    tool_keys = _SKILL_TOOL_SETS.get(skill_name)

    if tool_keys is None:
        # 미등록 Skill 또는 빈 문자열 → 전체 도구 (폴백)
        tool_keys = list(tools_map.keys())

    # DDL 도구 자동 추가 (DDL 있고 + 아직 포함 안 된 경우)
    if has_ddl:
        for key in _DDL_TOOL_KEYS:
            if key not in tool_keys:
                tool_keys.append(key)

    # 키 → 도구 함수 변환 (존재하는 것만)
    return [tools_map[k] for k in tool_keys if k in tools_map]
