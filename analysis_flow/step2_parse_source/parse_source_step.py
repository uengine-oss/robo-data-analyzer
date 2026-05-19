"""step2_parse_source — 소스 파일 → Function/Module 노드.

parser 구현체는 ``strategy.parser`` 가 주입 (DI). 분기 0개:
- Framework: ``TreeSitterParser``
- DBMS: ``PLSQLParser``

parser 인터페이스: ``async def parse(path: Path) -> ParseResult`` —
``ParseResult.functions / modules / parent_of_edges / uses_edges`` 반환.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from langchain_core.runnables import RunnableConfig

from shared.logging_and_stream.logger import log_info, log_warning
from shared.neo4j.edges.parent_of import upsert_parent_of
from shared.neo4j.edges.uses import upsert_uses
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.nodes.function import upsert_functions
from shared.neo4j.nodes.module import upsert_modules

_STEP = "step2.parse_source"


async def parse_source_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    """소스 디렉토리 → Function/Module 노드 + 엣지.

    ``strategy.parser`` 가 파일별로 ``parse(path)`` 호출 → 결과 집계.
    """
    cfg = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    strategy = cfg.get("strategy")
    project = cfg.get("project")
    neo: Neo4jClient | None = cfg.get("neo_client")
    if strategy is None or project is None:
        log_warning(_STEP, "config_missing", "strategy/project 누락 — step skip")
        return {}

    parser = strategy.parser
    started = time.monotonic()
    log_info(_STEP, "start", project=project.name, parser=type(parser).__name__, source_root=str(project.root))

    functions, modules, parent_of, uses = [], [], [], []
    parsed_files = 0

    extensions = getattr(parser, "supported_extensions", ("*",))
    for ext in extensions:
        glob = f"**/*.{ext}" if ext != "*" else "**/*"
        for src in sorted(Path(project.root).rglob(glob)):
            if not src.is_file():
                continue
            result = await parser.parse(src)
            functions.extend(result.functions)
            modules.extend(result.modules)
            parent_of.extend(getattr(result, "parent_of_edges", []) or [])
            uses.extend(getattr(result, "uses_edges", []) or [])
            parsed_files += 1

    if neo is not None:
        await upsert_functions(neo, functions)
        await upsert_modules(neo, modules)
        await upsert_parent_of(neo, parent_of)
        await upsert_uses(neo, uses)

    log_info(
        _STEP, "done",
        parsed_files=parsed_files,
        functions=len(functions), modules=len(modules),
        parent_of_edges=len(parent_of), uses_edges=len(uses),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )

    return {
        "functions": functions,
        "modules": modules,
        "parent_of_edges": parent_of,
        "uses_edges": uses,
    }
