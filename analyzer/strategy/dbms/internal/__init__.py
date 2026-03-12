"""
DBMS 전략의 내부 구현 모듈 집합.

외부에서는 analyzer.strategy.dbms.internal 에서 필요한 심벌만 import 하고,
실제 구현은 parsing/, linking/, graph/ 하위 패키지로 분산.
기존 import 경로 하위호환을 위해 re-export.
"""

# --- AST 관련 (parsing/ast_processor) ---
from analyzer.strategy.dbms.parsing.ast_processor import (  # noqa: F401
    DbmsAstProcessor,
    BaseAstProcessor,
)

# --- 모델 관련 (common/models) ---
from analyzer.strategy.dbms.common.models import (  # noqa: F401
    StatementNode,
    ProcedureInfo,
    AnalysisBatch,
    BatchPlanner,
    FileStatus,
    FileAnalysisContext,
)

# --- 상수 관련 (common/constants) ---
from analyzer.strategy.dbms.common.constants import (  # noqa: F401
    PROCEDURE_TYPES,
    NON_ANALYSIS_TYPES,
    DML_STATEMENT_TYPES,
    VARIABLE_DECLARATION_TYPES,
    TABLE_RELATIONSHIP_MAP,
    VARIABLE_ROLE_MAP,
)

# --- LLM 규칙 (linking/llm_rules) ---
from analyzer.strategy.dbms.linking.llm_rules import (  # noqa: F401
    analyze_code,
    analyze_dml_tables,
    analyze_summary_only,

    summarize_table_metadata,
    analyze_variables,
    extract_node_context,
    analyze_variable_summary,
)

# --- 파이프라인 페이즈 (pipeline/phases + linking/summarizer) ---
from analyzer.strategy.dbms.pipeline.phases import (  # noqa: F401
    run_phase_ddl,
    run_phase_ast,
    run_phase_llm,
    run_phase_vector,
    run_phase_lineage,
)
from analyzer.strategy.dbms.linking.summarizer import run_phase_post  # noqa: F401

# --- 유틸리티 (common/utils) ---
from analyzer.strategy.dbms.common.utils import (  # noqa: F401
    apply_name_case,
    resolve_default_schema,
    list_ddl_files,
    get_procedure_name_from_code,
    build_statement_name,
)

# --- AST 수집기 (parsing/ast_collector) ---
from analyzer.strategy.dbms.parsing.ast_collector import StatementCollector  # noqa: F401

# --- 리니지 (linking/lineage_analyzer) ---
from analyzer.strategy.dbms.linking.lineage_analyzer import (  # noqa: F401
    LineageInfo,
    LineageAnalyzer,
)
