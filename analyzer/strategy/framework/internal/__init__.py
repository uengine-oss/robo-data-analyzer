"""
Framework 전략의 내부 구현 모듈 집합.

외부에서는 analyzer.strategy.framework.internal 에서 필요한 심벌만 import 하고,
실제 구현은 parsing/, linking/, graph/ 하위 패키지로 분산.
기존 import 경로 하위호환을 위해 re-export.
"""

# --- AST 관련 (parsing/ast_walker → _ast_core) ---
from analyzer.strategy.framework._ast_core import (  # noqa: F401
    FrameworkAstProcessor,
    CLASS_TYPES,
    FIELD_TYPES,
    METHOD_TYPES,
)

# --- ChunkBundle 관련 (parsing/chunk_bundle) ---
from analyzer.strategy.framework.parsing.chunk_bundle import (  # noqa: F401
    build_chunk_bundle_from_source,
    save_chunk_bundle,
    _get_chunk_bundle_dir_from_run_dir,
    _get_chunk_bundle_dir,
    load_chunk_bundle,
    load_field_chunks_from_bundles,
    build_bundle_span_index,
    bundle_search,
    bundle_get_chunk,
)

# --- Linking 관련 (linking/orchestrator + matcher + call_graph + summarizer) ---
from analyzer.strategy.framework.linking.orchestrator import (  # noqa: F401
    run_step5,
    derive_method_table_edges,
)
from analyzer.strategy.framework.linking.summarizer import (  # noqa: F401
    run_step6,
    run_field_summary,
    run_class_summary,
)
from analyzer.strategy.framework.linking.call_graph import (  # noqa: F401
    derive_uses_from_calls,
)

# --- Cypher 관련 (graph/queries) ---
from analyzer.strategy.framework.graph.queries import (  # noqa: F401
    build_refer_to_queries,
    build_reads_writes_queries,
    build_calls_queries,
    build_uses_queries,
    build_table_queries_from_reads_writes,
    build_column_queries,
    build_has_column_queries,
    build_method_table_from_writes_queries,
    build_class_table_refer_to_queries,
    build_artifact_table_refer_to_queries,
    build_method_summary_queries,
    build_uml_dependency_queries,
    build_field_summary_queries,
    build_class_summary_queries,
    build_table_description_enrichment_query,
    build_table_description_update_queries,
)
