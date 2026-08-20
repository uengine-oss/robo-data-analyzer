"""step2_load_ast — antlr JSON AST → 코드 노드 + 구조/참조 엣지 + 호출/import 단서.

antlr-parser 가 소스를 미리 파싱해 ``data/analysis/`` (``data/source`` 구조 그대로
미러) 에 떨궈둔 AST 만 읽는다 — analyzer 는 소스를 재파싱하지 않는다. ``antlr_json_reader`` 가
언어별 해석을, ``reference_linker`` 가 REFERENCES/INIT_BY 결정론 매칭을 담당.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from pathlib import Path
from typing import Optional, TypeVar

from langchain_core.runnables import RunnableConfig

from pipeline.execution.step_config import step_config

from pipeline.steps.step2_load_ast.dispatch import ast_and_source_roots, read_antlr_json
from pipeline.steps.step2_load_ast.source_ast_closure import (
    SourceAstClosure,
    ast_json_paths,
    verify_source_ast_closure,
)
from pipeline.steps.step2_load_ast.token_matcher import link_references
from shared.events.runtime_logger import log_error, log_info, log_warning
from pipeline.steps.step2_load_ast.node_identity_registry import NodeIdentityCollision, canonicalize_node_lists
from shared.files.cache import SourceFileCache
from graph.edges.belongs_to import upsert_belongs_to
from graph.edges.has_member import upsert_has_member
from graph.edges.init_by import upsert_init_by
from graph.edges.next import upsert_statement_next
from graph.edges.parent_of import upsert_parent_of
from graph.edges.references import upsert_references
from graph.neo4j_client import Neo4jClient
from graph.nodes.constant import upsert_constants
from graph.nodes.data_type import upsert_data_types
from graph.nodes.code_node import upsert_code_nodes
from graph.nodes.module import upsert_modules
from graph.nodes.package import upsert_packages
from graph.nodes.variable import upsert_variables
from pipeline.execution.graph_delta_stream import emit_edges, emit_message, emit_nodes
from pipeline.execution.pause_controller import NULL_PAUSE, PauseController

_STEP = "step2.load_ast"

_N = TypeVar("_N")


def _with_code_text(nodes: list[_N], cache: SourceFileCache) -> list[_N]:
    """라인 범위가 있는 노드에 원문 슬라이스를 붙인다 (frozen dataclass → replace).

    규칙 하나: **파일·라인이 있으면 그 원문을 갖는다.** 노드 종류로 분기하지 않는다.
    ``end_line`` 0 = 파일 끝까지 (SourceFileCache.slice 규약).
    """
    result: list[_N] = []
    for node in nodes:
        if not node.file_path or getattr(node, "raw_code_text", ""):
            result.append(node)
            continue
        excerpt = cache.excerpt(node.file_path, node.start_line, node.end_line)
        result.append(
            replace(
                node,
                raw_code_text=excerpt.raw_text,
                code_text=excerpt.numbered_text,
            )
        )
    return result


def _assert_unique_ids(*node_lists: list) -> None:
    """**id 전역 유일성 가드** (spec 047 FR-005) — 중복이면 즉시 raise.

    왜 필요한가: 이 불변식이 깨지면 무라벨 ``MATCH (n {id: …})`` 가 **두 노드를 모두** 잡아
    엣지·라벨이 엉뚱한 곳에 박힌다 (실제로 터졌다 — 테이블에 코드 HAS_MEMBER 144개,
    구조체가 DB 컬럼을 멤버로 가짐). 그래프가 조용히 거짓말을 하므로 **조용히 넘기면 안 된다**.

    이제 id 는 네임스페이스(``code:``/``db:``) + 위치(파일경로·컨테이너경로)로 만들어져
    구조적으로 겹칠 수 없다. 이 가드는 **그 설계가 깨졌을 때 즉시 알려주는 센서**다.
    """
    seen: dict[str, str] = {}
    dups: list[str] = []
    for nodes in node_lists:
        for n in nodes:
            nid = n.id
            label = getattr(n, "label", type(n).__name__)
            if nid in seen:
                dups.append(f"{nid!r} ({seen[nid]} ↔ {label})")
            else:
                seen[nid] = label
    if dups:
        err = ValueError(
            f"노드 id 중복 {len(dups)}건 — 서로 다른 노드가 같은 식별자를 갖는다 "
            f"(spec 047 불변식 위반): {dups[:5]}"
        )
        log_error(_STEP, "duplicate_node_id", err, count=len(dups))
        raise err


def _code_text_missing(*node_lists: list) -> int:
    """**불변식 감시** (spec 045 FR-005): file_path 가 있는데 원문이 빈 노드 수.

    0 이 아니면 소스를 못 읽었다는 뜻 — ``SourceFileCache`` 가 남긴 ``source_read_failed``
    경고와 짝을 이룬다. file_path 가 **없는** 노드(외부 stub 등)는 "원래 본문이 없는 것"이라
    카운트에서 제외한다 — 못 읽은 것과 섞지 않는다.
    """
    return sum(
        1
        for nodes in node_lists
        for n in nodes
        if n.file_path and not n.code_text
    )


async def load_ast_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    """antlr JSON 디렉토리 → 코드 노드 + 구조/참조 엣지 + call_sites/import_refs.

    코드 노드 = CodeNode / Module / Package / Variable / Constant / DataType.
    구조 엣지 = PARENT_OF(중첩) / HAS_MEMBER(그릇→루틴·데이터) / BELONGS_TO(그릇→폴더) /
               NEXT(DBMS 구문 실행 흐름) — reader 가 antlr 트리에서 도출.
    참조 엣지 = REFERENCES / INIT_BY (reference_linker 결정론 식별자 매칭).
    antlr JSON 디렉토리가 없으면 ``FileNotFoundError`` — silent skip 하지 않는다.
    """
    cfg = step_config(config)
    project = cfg.get("codebase")
    neo: Neo4jClient | None = cfg.get("neo_client")
    pause: PauseController = cfg.get("pause") or NULL_PAUSE
    if project is None:
        log_warning(_STEP, "config_missing", "project 누락 — step skip")
        return {}

    started = time.monotonic()
    ast_dir, source_root = ast_and_source_roots(
        Path(project.root), getattr(project, "corpus_root", None),
    )

    if not ast_dir.is_dir():
        err = FileNotFoundError(
            f"antlr JSON 디렉토리 없음: {ast_dir} — antlr-parser 를 먼저 실행하세요"
        )
        log_error(_STEP, "start", err, root=str(project.root))
        raise err

    source_cache = cfg.get("source_cache") or SourceFileCache()
    closure: SourceAstClosure | None = cfg.get("source_ast_closure")
    if closure is None:
        closure = verify_source_ast_closure(
            project_root=Path(project.root),
            ast_root=ast_dir,
            source_root=source_root,
            source_cache=source_cache,
        )

    log_info(_STEP, "start", root=str(project.root), ast_dir=str(ast_dir))

    code_nodes, modules, packages = [], [], []
    variables, constants, data_types = [], [], []
    parent_of = []
    has_member, belongs_to, statement_next = [], [], []
    call_sites, import_refs = [], []
    evidence_sources, evidence_completeness = [], []
    parsed_files = 0
    await pause.gate()
    # 파일 순차 유지 — 제어 계약(정지 시 다음 파일을 읽지 않음)이 파일 단위 gate 를
    # 요구한다. 이 단계의 시간 지배는 파싱이 아니라 Neo4j 업서트다.
    for artifact in closure.artifacts:
        result = read_antlr_json(
            artifact.ast_path,
            source_root,
            expected_sha256=artifact.ast_sha256,
            source_language=artifact.language,
            source_cache=source_cache,
        )
        code_nodes.extend(result.code_nodes)
        modules.extend(result.modules)
        packages.extend(result.packages)
        variables.extend(result.variables)
        constants.extend(result.constants)
        data_types.extend(result.data_types)
        parent_of.extend(result.parent_of_edges)
        has_member.extend(result.has_member_edges)
        belongs_to.extend(result.belongs_to_edges)
        statement_next.extend(result.statement_next_edges)
        call_sites.extend(result.call_sites)
        import_refs.extend(result.import_refs)
        evidence_sources.extend(result.evidence_sources)
        evidence_completeness.extend(result.evidence_completeness)
        parsed_files += 1
        await pause.gate()

    await pause.gate()
    call_fact_ids = [site.get("call_id") for site in call_sites]
    if len(call_fact_ids) != len(set(call_fact_ids)):
        raise ValueError("common evidence call fact IDs are not globally exact-once")
    source_ids = [item.get("source_id") for item in evidence_sources]
    if len(source_ids) != parsed_files or len(source_ids) != len(set(source_ids)):
        raise ValueError(
            "common evidence source envelopes are not exactly one per parsed file: "
            f"parsed={parsed_files}, envelopes={len(source_ids)}, "
            f"unique={len(set(source_ids))}"
        )

    await pause.gate()
    try:
        canonical, collapsed_node_observations = canonicalize_node_lists(
            code_nodes, modules, packages, variables, constants, data_types,
        )
        (
            code_nodes, modules, packages,
            variables, constants, data_types,
        ) = canonical
    except NodeIdentityCollision as err:
        log_error(_STEP, "node_identity_collision", err)
        raise

    if collapsed_node_observations:
        log_info(
            _STEP,
            "node_observations_canonicalized",
            count=collapsed_node_observations,
        )

    await pause.gate()
    # run 스코프 공유 캐시 (executor 주입) — 없으면 단독 생성(CLI/테스트 경로). FR-008.
    # ★ reference_linker 와 **같은 인스턴스**를 쓴다 — 종전엔 둘이 각자 캐시를 만들어
    #   step2 안에서만 같은 파일을 두 번 읽었다.
    # REFERENCES / INIT_BY — 전 파일 노드가 모인 뒤 결정론 식별자 매칭.
    # import_refs 를 넘겨 모듈 너머 참조까지 (계획 §2.8 Pass 2).
    references, init_by = await link_references(
        code_nodes, variables, constants, import_refs, source_cache,
        checkpoint=pause.gate,
        modules=modules,        # 상속 조상 매칭 (spec 111 — Java extends / Python bases)
    )

    await pause.gate()
    # 원문 코드 — **파일·라인이 있는 모든 노드**에 붙이는 단일 지점. 노드 종류로 분기하지 않는다.
    # (루틴뿐 아니라 선언 노드 DEFINE/STRUCT/GLOBAL_VARIABLE/MEMBER 도 자기 원문을 갖는다 —
    #  노드 패널이 "이 코드에서 도출됐다"를 보여주는 근거.)
    code_nodes = _with_code_text(code_nodes, source_cache)
    modules = _with_code_text(modules, source_cache)
    variables = _with_code_text(variables, source_cache)
    constants = _with_code_text(constants, source_cache)
    data_types = _with_code_text(data_types, source_cache)

    # ★ id 전역 유일성 가드 (spec 047) — 깨지면 그래프가 조용히 거짓말을 하므로 즉시 실패.
    #   (DB 노드는 step1 이 만들지만 네임스페이스가 달라(`db:`) 코드 노드와 겹칠 수 없다.)
    _assert_unique_ids(code_nodes, modules, packages, variables, constants, data_types)

    if neo is not None:
        # 노드는 라벨이 달라 동시쓰기 안전 → 병렬. 엣지는 노드 선행 필요 +
        # 같은 노드 락 경합 가능 → 순차.
        await asyncio.gather(
            upsert_code_nodes(neo, code_nodes),
            upsert_modules(neo, modules),
            upsert_packages(neo, packages),
            upsert_variables(neo, variables),
            upsert_constants(neo, constants),
            upsert_data_types(neo, data_types),
        )
        await pause.gate()
        await upsert_parent_of(neo, parent_of)
        await pause.gate()
        await upsert_has_member(neo, has_member)
        await pause.gate()
        await upsert_belongs_to(neo, belongs_to)
        await pause.gate()
        await upsert_statement_next(neo, statement_next)
        await pause.gate()
        await upsert_references(neo, references)
        await pause.gate()
        await upsert_init_by(neo, init_by)
        await pause.gate()

    # 라이브 스트림 — 저장 그래프와 같은 골격을 발신한다. NEXT를 포함한 기본 숨김 여부는
    # 생산 단계에서 누락하지 않고 프론트의 단일 가시성 정책이 결정한다.
    stream = cfg.get("stream")
    await emit_nodes(stream, code_nodes)
    await pause.gate()
    await emit_nodes(stream, modules)
    await pause.gate()
    await emit_nodes(stream, packages)
    await pause.gate()
    await emit_nodes(stream, variables)
    await pause.gate()
    await emit_nodes(stream, constants)
    await pause.gate()
    await emit_nodes(stream, data_types)
    await pause.gate()
    await emit_edges(stream, parent_of)
    await pause.gate()
    await emit_edges(stream, has_member)
    await pause.gate()
    await emit_edges(stream, belongs_to)
    await pause.gate()
    await emit_edges(stream, statement_next)
    await pause.gate()
    await emit_edges(stream, references)
    await pause.gate()
    await emit_edges(stream, init_by)
    await pause.gate()
    emit_message(
        stream,
        f"✅ 코드 구조 적재 완료 — 노드 {len(code_nodes)}, 소속·부모 {len(has_member) + len(parent_of)}",
    )

    # 불변식 감시 — file_path 가 있는데 원문이 빈 노드가 있으면 소스를 못 읽은 것 (FR-005).
    code_text_missing = _code_text_missing(
        code_nodes, modules, variables, constants, data_types
    )
    if code_text_missing:
        log_warning(
            _STEP, "code_text_missing",
            f"파일·라인이 있는데 원문을 못 얻은 노드 {code_text_missing}개 — "
            "소스 read 실패(source_read_failed 경고 참조). 노드 패널·검색 재료가 빈다.",
            count=code_text_missing,
        )

    log_info(
        _STEP, "done",
        parsed_files=parsed_files,
        collapsed_node_observations=collapsed_node_observations,
        code_text_missing=code_text_missing,
        code_nodes=len(code_nodes), modules=len(modules),
        packages=len(packages), variables=len(variables),
        constants=len(constants), data_types=len(data_types),
        parent_of_edges=len(parent_of),
        has_member=len(has_member), belongs_to=len(belongs_to),
        statement_next=len(statement_next),
        references=len(references), init_by=len(init_by),
        call_sites=len(call_sites), import_refs=len(import_refs),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {
        "code_nodes": code_nodes,
        "modules": modules,
        "packages": packages,
        "variables": variables,
        "constants": constants,
        "data_types": data_types,
        "parent_of_edges": parent_of,
        "has_member_edges": has_member,
        "belongs_to_edges": belongs_to,
        "statement_next_edges": statement_next,
        "references_edges": references,
        "init_by_edges": init_by,
        "call_sites": call_sites,
        "import_refs": import_refs,
        "evidence_sources": evidence_sources,
        "evidence_completeness": evidence_completeness,
    }
