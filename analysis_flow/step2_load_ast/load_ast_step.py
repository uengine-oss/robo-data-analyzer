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

from shared.workflow_runner.step_config import step_config

from analysis_flow.step2_load_ast.antlr_json_reader import antlr_json_path, read_antlr_json
from analysis_flow.step2_load_ast.reference_linker import link_references
from shared.logging_and_stream.logger import log_error, log_info, log_warning
from shared.neo4j.edges.belongs_to_package import upsert_belongs_to_package
from shared.neo4j.edges.has_function import upsert_has_function
from shared.neo4j.edges.has_member import upsert_has_member
from shared.neo4j.edges.has_variable import upsert_has_variable
from shared.neo4j.edges.init_by import upsert_init_by
from shared.neo4j.edges.parent_of import upsert_parent_of
from shared.neo4j.edges.references import upsert_references
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.nodes.constant import upsert_constants
from shared.neo4j.nodes.data_type import upsert_data_types
from shared.neo4j.nodes.function import upsert_functions
from shared.neo4j.nodes.module import upsert_modules
from shared.neo4j.nodes.package import upsert_packages
from shared.neo4j.nodes.variable import upsert_variables

_STEP = "step2.load_ast"


async def load_ast_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    """antlr JSON 디렉토리 → 코드 노드 + 구조/참조 엣지 + call_sites/import_refs.

    코드 노드 = Function / Module / Package / Variable / Constant / DataType.
    구조 엣지 = PARENT_OF / HAS_FUNCTION / HAS_VARIABLE / HAS_MEMBER /
               BELONGS_TO_PACKAGE (reader 가 antlr 트리에서 도출).
    참조 엣지 = REFERENCES / INIT_BY (reference_linker 결정론 식별자 매칭).
    antlr JSON 디렉토리가 없으면 ``FileNotFoundError`` — silent skip 하지 않는다.
    """
    cfg = step_config(config)
    project = cfg.get("project")
    neo: Neo4jClient | None = cfg.get("neo_client")
    if project is None:
        log_warning(_STEP, "config_missing", "project 누락 — step skip")
        return {}

    started = time.monotonic()
    ast_dir, source_root = antlr_json_path(Path(project.root))

    if not ast_dir.is_dir():
        err = FileNotFoundError(
            f"antlr JSON 디렉토리 없음: {ast_dir} — antlr-parser 를 먼저 실행하세요"
        )
        log_error(_STEP, "start", err, project=project.name)
        raise err

    log_info(_STEP, "start", project=project.name, ast_dir=str(ast_dir))

    functions, modules, packages = [], [], []
    variables, constants, data_types = [], [], []
    parent_of = []
    has_function, has_variable, has_member, belongs_to_package = [], [], [], []
    call_sites, import_refs = [], []
    parsed_files = 0
    for json_path in sorted(ast_dir.rglob("*.json")):
        if not json_path.is_file():
            continue
        result = read_antlr_json(json_path, source_root)
        functions.extend(result.functions)
        modules.extend(result.modules)
        packages.extend(result.packages)
        variables.extend(result.variables)
        constants.extend(result.constants)
        data_types.extend(result.data_types)
        parent_of.extend(result.parent_of_edges)
        has_function.extend(result.has_function_edges)
        has_variable.extend(result.has_variable_edges)
        has_member.extend(result.has_member_edges)
        belongs_to_package.extend(result.belongs_to_package_edges)
        call_sites.extend(result.call_sites)
        import_refs.extend(result.import_refs)
        parsed_files += 1

    # REFERENCES / INIT_BY — 전 파일 노드가 모인 뒤 결정론 식별자 매칭.
    # import_refs 를 넘겨 모듈 너머 참조까지 (계획 §2.8 Pass 2).
    references, init_by = link_references(functions, variables, constants, import_refs)

    if neo is not None:
        # 노드는 라벨이 달라 동시쓰기 안전 → 병렬. 엣지는 노드 선행 필요 +
        # 같은 노드 락 경합 가능 → 순차.
        await asyncio.gather(
            upsert_functions(neo, functions),
            upsert_modules(neo, modules),
            upsert_packages(neo, packages),
            upsert_variables(neo, variables),
            upsert_constants(neo, constants),
            upsert_data_types(neo, data_types),
        )
        await upsert_parent_of(neo, parent_of)
        await upsert_has_function(neo, has_function)
        await upsert_has_variable(neo, has_variable)
        await upsert_has_member(neo, has_member)
        await upsert_belongs_to_package(neo, belongs_to_package)
        await upsert_references(neo, references)
        await upsert_init_by(neo, init_by)

    log_info(
        _STEP, "done",
        parsed_files=parsed_files,
        functions=len(functions), modules=len(modules),
        packages=len(packages), variables=len(variables),
        constants=len(constants), data_types=len(data_types),
        parent_of_edges=len(parent_of),
        has_function=len(has_function), has_variable=len(has_variable),
        has_member=len(has_member), belongs_to_package=len(belongs_to_package),
        references=len(references), init_by=len(init_by),
        call_sites=len(call_sites), import_refs=len(import_refs),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {
        "functions": functions,
        "modules": modules,
        "packages": packages,
        "variables": variables,
        "constants": constants,
        "data_types": data_types,
        "parent_of_edges": parent_of,
        "has_function_edges": has_function,
        "has_variable_edges": has_variable,
        "has_member_edges": has_member,
        "belongs_to_package_edges": belongs_to_package,
        "references_edges": references,
        "init_by_edges": init_by,
        "call_sites": call_sites,
        "import_refs": import_refs,
    }
