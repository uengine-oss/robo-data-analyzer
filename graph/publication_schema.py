"""Closed names for Analyzer control records and published graph products."""
from __future__ import annotations


ANALYSIS_SCOPE_LABEL = "AnalysisScope"
ANALYSIS_GENERATION_LABEL = "AnalysisGeneration"
SEARCH_SEGMENT_VECTOR_INDEX_NAME = "search_segment_embedding"

PRODUCT_NODE_TYPES = frozenset({
    "Package",
    "Module",
    "File",
    "CodeNode",
    "Table",
    "Column",
    "Rule",
    "Example",
    "ExternalChunk",
    "Component",
    "GlossaryTerm",
    "SearchSegment",
    "AnalysisHealth",
})

PRODUCT_RELATIONSHIP_TYPES = frozenset({
    "PARENT_OF",
    "NEXT",
    "HAS_COLUMN",
    "FK",
    "CALLS",
    "READS",
    "WRITES",
    "REFER_TO",
    "HAS_RULE",
    "HAS_EXAMPLE",
    "MEMBER_OF_COMPONENT",
    "HAS_SEARCH_SEGMENT",
})

PRODUCT_STAGE_NAMES = frozenset({
    "code_structure",
    "data_schema_structure",
    "external_chunks",
    "call_links",
    "data_access_links",
    "function_meanings",
    "table_meanings",
    "rule_meanings",
    "examples",
    "module_summary_rollups",
    "components",
    "glossary",
    "search_segments",
    "analysis_health",
})
