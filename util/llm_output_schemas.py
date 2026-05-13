"""룰별 LLM 출력 스키마 — Pydantic v2 모델.

모든 LLM 룰의 출력 형태를 한 곳에서 선언.
`llm_output_coercer.coerce_to_schema` 가 이 스키마로 검증 + 교정.
소비자 코드는 타입이 보장된 dict 를 받으므로 isinstance 방어 불필요.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════════════
# Framework — code_analysis
# ═══════════════════════════════════════════════════════════════════════════

class TableInsightColumn(BaseModel):
    name: str = ""
    description: str = ""

class TableInsight(BaseModel):
    tableName: str = ""
    description: str = ""
    columns: List[TableInsightColumn] = []

class Dependency(BaseModel):
    targetModule: str = ""
    sourceMember: str = ""
    usage: str = ""
    relationType: str = ""

class RuleItem(BaseModel):
    rule_id: str = ""
    flow_id: str = ""
    guard_rule_id: Optional[str] = None
    branch_from: Optional[str] = None
    statement: str = ""
    coupled_domains: List[str] = []

class ExampleGiven(BaseModel):
    narrative: str = ""
    inputs: Dict[str, Any] = {}
    preconditions: Dict[str, Any] = {}

class ExampleThen(BaseModel):
    narrative: str = ""
    return_val: Any = Field(None, alias="return")
    exception: Optional[Dict[str, Any]] = None
    reads: List[Dict[str, Any]] = []
    writes: List[Dict[str, Any]] = []
    globals: Dict[str, Any] = Field(default_factory=dict)
    out_params: Dict[str, Any] = {}

class Example(BaseModel):
    rule_id: str = ""
    is_boundary: bool = False
    description: str = ""
    given: Any = {}
    when: str = ""
    then: Any = {}

class Question(BaseModel):
    text: str = ""
    reason: str = ""

class CodeAnalysisOutput(BaseModel):
    summary: str = ""
    stereotype: str = ""
    actor: str = ""
    dependencies: List[Dependency] = []
    tableInsights: List[TableInsight] = []
    rules: List[RuleItem] = []
    examples: List[Example] = []
    questions: List[Question] = []


# ═══════════════════════════════════════════════════════════════════════════
# Framework — call_extract
# ═══════════════════════════════════════════════════════════════════════════

class CallExtractItem(BaseModel):
    call_id: str = ""
    obj_name: str = ""
    function_name: str = ""

class CallExtractFunction(BaseModel):
    function_id: str = ""
    results: List[CallExtractItem] = []

class CallExtractOutput(BaseModel):
    results: List[CallExtractFunction] = []


# ═══════════════════════════════════════════════════════════════════════════
# Framework — table_extract
# ═══════════════════════════════════════════════════════════════════════════

class TableExtractTable(BaseModel):
    schema_: str = Field("", alias="schema")
    name: str = ""
    relation: str = ""
    description: str = ""

class TableExtractAlias(BaseModel):
    alias: str = ""
    physical_table: str = ""

class TableExtractColumn(BaseModel):
    table_schema: str = ""
    table_name: str = ""
    column_name: str = ""
    description: str = ""

class TableExtractFk(BaseModel):
    from_schema: str = ""
    from_table: str = ""
    from_column: str = ""
    to_schema: str = ""
    to_table: str = ""
    to_column: str = ""

class TableExtractResult(BaseModel):
    sql_key: str = ""
    tables: List[TableExtractTable] = []
    alias_map: List[TableExtractAlias] = []
    columns: List[TableExtractColumn] = []
    fks: List[TableExtractFk] = []

class TableExtractOutput(BaseModel):
    results: List[TableExtractResult] = []


# ═══════════════════════════════════════════════════════════════════════════
# Framework — module_pre_analysis
# ═══════════════════════════════════════════════════════════════════════════

class ModulePreAnalysisOutput(BaseModel):
    module_display_name: str = ""
    module_intent: str = ""


# ═══════════════════════════════════════════════════════════════════════════
# Framework — module_analysis
# ═══════════════════════════════════════════════════════════════════════════

class ModuleAnalysisOutput(BaseModel):
    summary: str = ""
    moduleStereotype: str = ""
    unified_actors: Dict[str, List[str]] = {}


# ═══════════════════════════════════════════════════════════════════════════
# Framework — variable_summary
# ═══════════════════════════════════════════════════════════════════════════

class VariableSummaryOutput(BaseModel):
    summary: str = ""


# ═══════════════════════════════════════════════════════════════════════════
# Framework — actor_synonym_merge
# 출력이 동적 key dict — 스키마는 "dict[str, list[str]]" 이라 모델 래핑 불필요.
# coercer 에서 dict 타입 + 값 list[str] 만 확인.
# ═══════════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════════
# DBMS — node_analysis
# ═══════════════════════════════════════════════════════════════════════════

class DbmsExampleThen(BaseModel):
    narrative: str = ""
    return_val: Any = Field(None, alias="return")
    out_params: Dict[str, Any] = {}
    exception: Optional[Dict[str, Any]] = None
    writes: List[Dict[str, Any]] = []
    commit: bool = False

class DbmsExample(BaseModel):
    description: str = ""
    given: Any = {}
    when: str = ""
    then: Any = {}
    is_boundary: bool = False

class DbmsRuleItem(BaseModel):
    statement: str = ""
    examples: List[DbmsExample] = []

class DbmsNodeAnalysisEntry(BaseModel):
    startLine: int = 0
    endLine: int = 0
    summary: str = ""
    actor: str = ""
    rules: List[DbmsRuleItem] = []
    questions: List[Question] = []

class DbmsNodeAnalysisOutput(BaseModel):
    analysis: List[DbmsNodeAnalysisEntry] = []


# ═══════════════════════════════════════════════════════════════════════════
# DBMS — dml_extract
# ═══════════════════════════════════════════════════════════════════════════

class DmlExtractColumn(BaseModel):
    name: str = ""
    description: str = ""

class DmlExtractTable(BaseModel):
    table: str = ""
    op: str = ""
    tableDescription: str = ""
    columns: List[DmlExtractColumn] = []

class DmlExtractFk(BaseModel):
    sourceTable: str = ""
    sourceColumns: List[str] = []
    targetTable: str = ""
    targetColumns: List[str] = []

class DmlExtractDbLink(BaseModel):
    name: str = ""
    mode: str = ""

class DmlExtractRange(BaseModel):
    startLine: int = 0
    endLine: int = 0
    tables: List[DmlExtractTable] = []
    fkRelations: List[DmlExtractFk] = []
    dbLinks: List[DmlExtractDbLink] = []

class DmlExtractOutput(BaseModel):
    ranges: List[DmlExtractRange] = []


# ═══════════════════════════════════════════════════════════════════════════
# DBMS — unit_analysis
# ═══════════════════════════════════════════════════════════════════════════

class UnitAnalysisOutput(BaseModel):
    summary: str = ""


# ═══════════════════════════════════════════════════════════════════════════
# DBMS — table_summary
# ═══════════════════════════════════════════════════════════════════════════

class TableSummaryColumn(BaseModel):
    name: str = ""
    description: str = ""

class TableSummaryOutput(BaseModel):
    tableDescription: str = ""
    columns: List[TableSummaryColumn] = []


# ═══════════════════════════════════════════════════════════════════════════
# DBMS — node_context
# ═══════════════════════════════════════════════════════════════════════════

class NodeContextOutput(BaseModel):
    parent_context: str = ""
    sibling_context: str = ""


# ═══════════════════════════════════════════════════════════════════════════
# Registry — rule_name → schema class
# ═══════════════════════════════════════════════════════════════════════════

RULE_SCHEMAS: Dict[str, type[BaseModel]] = {
    # Framework
    "code_analysis": CodeAnalysisOutput,
    "call_extract": CallExtractOutput,
    "table_extract": TableExtractOutput,
    "module_pre_analysis": ModulePreAnalysisOutput,
    "module_analysis": ModuleAnalysisOutput,
    "variable_summary": VariableSummaryOutput,
    # actor_synonym_merge → 동적 키 dict, coercer 에서 별도 처리
    # DBMS
    "node_analysis": DbmsNodeAnalysisOutput,
    "dml_extract": DmlExtractOutput,
    "unit_analysis": UnitAnalysisOutput,
    "table_summary": TableSummaryOutput,
    "node_context": NodeContextOutput,
}
