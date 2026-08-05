"""Persist the authoritative human-provided project glossary.

Analyzer does not infer, merge, cap, or rewrite business terms.  The optional
``analysis/glossary.yaml`` input is validated once before graph mutation and
stored verbatim in the established search-facing ``GLOSSARY.terms_json`` shape.
No file means no glossary node and no LLM call.
"""
from __future__ import annotations

import json
from typing import Optional

from langchain_core.runnables import RunnableConfig

from graph.neo4j_client import Neo4jClient
from graph.ownership import ANALYZER_GRAPH_OWNER
from pipeline.execution.step_config import step_config
from pipeline.models.human_glossary import HumanGlossary
from shared.events.runtime_logger import log_info


_STEP = "step8.build_glossary"

_UPSERT_QUERY = f"""
UNWIND $items AS item
MERGE (g:GLOSSARY {{id: "glossary", graph_owner: '{ANALYZER_GRAPH_OWNER}'}})
SET g:ANALYZER_OWNED,
    g.terms_json = item.terms_json,
    g.n_terms = item.n_terms,
    g.source_kind = item.source_kind,
    g.source_sha256 = item.source_sha256,
    g.schema_version = item.schema_version
"""


async def build_glossary_step(
    state: dict, config: Optional[RunnableConfig] = None,
) -> dict:
    """Write supplied human terms exactly once; otherwise finish explicitly empty."""
    del state
    cfg = step_config(config)
    neo: Neo4jClient | None = cfg.get("neo_client")
    glossary = cfg.get("human_glossary")
    if neo is None:
        raise ValueError("build_glossary_step requires neo_client")
    if not isinstance(glossary, HumanGlossary):
        raise ValueError("build_glossary_step requires validated human_glossary")
    if not glossary.supplied:
        log_info(_STEP, "skip", reason="human glossary not supplied", terms=0)
        return {}

    terms = [term.graph_dict() for term in glossary.terms]
    payload = json.dumps(
        {"terms": terms, "actions": []},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    await neo.run_batch_unwind(_UPSERT_QUERY, [{
        "terms_json": payload,
        "n_terms": len(terms),
        "source_kind": "human",
        "source_sha256": glossary.sha256,
        "schema_version": glossary.schema_version,
    }])
    log_info(
        _STEP,
        "done",
        source_kind="human",
        terms=len(terms),
        source_sha256=glossary.sha256,
    )
    return {}
