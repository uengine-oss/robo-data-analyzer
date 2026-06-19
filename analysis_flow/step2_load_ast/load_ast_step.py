"""step2_load_ast — antlr JSON AST → 코드 노드 + 구조/참조 엣지 + 호출/import 단서.

antlr-parser 가 소스를 미리 파싱해 ``data/analysis/`` (``data/source`` 구조 그대로
미러) 에 떨궈둔 AST 만 읽는다 — analyzer 는 소스를 재파싱하지 않는다. ``antlr_json_reader`` 가
언어별 해석을, ``reference_linker`` 가 REFERENCES/INIT_BY 결정론 매칭을 담당.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Optional

from langchain_core.runnables import RunnableConfig

from shared.workflow.step_utils import step_config

from analysis_flow.step2_load_ast.antlr_json_reader import ast_and_source_roots, read_antlr_json
from analysis_flow.step2_load_ast.reference_linker import link_references
from shared.log.logger import log_error, log_info, log_warning
from shared.neo4j.edges.belongs_to import upsert_belongs_to
from shared.neo4j.edges.has_member import upsert_has_member
from shared.neo4j.edges.init_by import upsert_init_by
from shared.neo4j.edges.next import upsert_statement_next
from shared.neo4j.edges.parent_of import upsert_parent_of
from shared.neo4j.edges.references import upsert_references
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.nodes.constant import upsert_constants
from shared.neo4j.nodes.data_type import upsert_data_types
from shared.neo4j.nodes.code_node import upsert_code_nodes
from shared.neo4j.nodes.module import upsert_modules
from shared.neo4j.nodes.package import upsert_packages
from shared.neo4j.nodes.variable import upsert_variables
from shared.workflow.graph_stream import emit_edges, emit_message, emit_nodes

_STEP = "step2.load_ast"


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
    if project is None:
        log_warning(_STEP, "config_missing", "project 누락 — step skip")
        return {}

    started = time.monotonic()
    ast_dir, source_root = ast_and_source_roots(Path(project.root))

    if not ast_dir.is_dir():
        err = FileNotFoundError(
            f"antlr JSON 디렉토리 없음: {ast_dir} — antlr-parser 를 먼저 실행하세요"
        )
        log_error(_STEP, "start", err, root=str(project.root))
        raise err

    log_info(_STEP, "start", root=str(project.root), ast_dir=str(ast_dir))

    code_nodes, modules, packages = [], [], []
    variables, constants, data_types = [], [], []
    parent_of = []
    has_member, belongs_to, statement_next = [], [], []
    call_sites, import_refs = [], []
    parsed_files = 0
    for json_path in sorted(ast_dir.rglob("*.json")):
        if not json_path.is_file():
            continue
        result = read_antlr_json(json_path, source_root)
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
        parsed_files += 1

    # REFERENCES / INIT_BY — 전 파일 노드가 모인 뒤 결정론 식별자 매칭.
    # import_refs 를 넘겨 모듈 너머 참조까지 (계획 §2.8 Pass 2).
    references, init_by = link_references(code_nodes, variables, constants, import_refs)

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
        await upsert_parent_of(neo, parent_of)
        await upsert_has_member(neo, has_member)
        await upsert_belongs_to(neo, belongs_to)
        await upsert_statement_next(neo, statement_next)
        await upsert_references(neo, references)
        await upsert_init_by(neo, init_by)

    # 라이브 스트림 — 코드 구조 골격을 step 완료 즉시 화면으로. NEXT(statement_next)는
    # 프론트 비표시 종류라 제외(REFERENCES/INIT_BY는 보내되 프론트 정책이 표시 결정).
    stream = cfg.get("stream")
    emit_nodes(stream, code_nodes)
    emit_nodes(stream, modules)
    emit_nodes(stream, packages)
    emit_nodes(stream, variables)
    emit_nodes(stream, constants)
    emit_nodes(stream, data_types)
    emit_edges(stream, parent_of)
    emit_edges(stream, has_member)
    emit_edges(stream, belongs_to)
    emit_edges(stream, references)
    emit_edges(stream, init_by)
    emit_message(
        stream,
        f"✅ 코드 구조 적재 완료 — 노드 {len(code_nodes)}, 소속·부모 {len(has_member) + len(parent_of)}",
    )

    log_info(
        _STEP, "done",
        parsed_files=parsed_files,
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
    }
