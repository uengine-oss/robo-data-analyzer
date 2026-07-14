# Specifications — ROBO Data Analyzer

> **Backfilled** on 2026-06-15 by reverse-engineering the existing codebase using the
> [GitHub Spec Kit](https://github.com/github/spec-kit) format. Each `spec.md` was derived
> from the actual implementation (LangGraph step functions, agents, Neo4j writers, env
> catalog) — **not** from a prior design doc. Endpoint paths, Neo4j labels, env vars, and
> pipeline behaviors are grounded in code, and several README/docstring claims were
> **corrected against the code** during backfill (see "Discrepancies" below).

## What this is

robo-data-analyzer turns legacy source code (Java/C/Python/… or Oracle PL/SQL) into a
**Neo4j knowledge graph** (13 node labels / 21 edge types) via a LangGraph pipeline
(Framework = 11 steps, DBMS = 13 steps) that is deterministic-first and uses LLMs only where
*meaning* is required. These specs document that system feature-by-feature in WHAT/WHY terms.

> **Step-count footnote**: spec 002 lists steps as sequential entries plus a parallel
> post-processing group (`summarize_module` + `enrich_table_description` + `build_components` +
> `health_metrics`). That parallel group counts as a **single step slot** in the 11/13 tally.

## How to use these specs

- **Onboarding**: read in numbered order to understand the pipeline end to end.
- **Change planning**: when modifying a feature, update its `spec.md` first — the spec is the
  contract, the code is the implementation.
- **New features**: `/speckit-specify` → `/speckit-plan` → `/speckit-tasks` → `/speckit-implement`.
  The constitution at [`.specify/memory/constitution.md`](../.specify/memory/constitution.md)
  states the 8 principles every new plan's "Constitution Check" must satisfy.

## Feature index

| # | Spec | One-liner | Pipeline phase |
|---|------|-----------|----------------|
| 001 | [Analysis Run Orchestration & NDJSON Streaming](001-analysis-run-orchestration/spec.md) | `POST /robo/analyze` + `/control`; one run = workflow + NDJSON stream; budget circuit breaker | entry/lifecycle |
| 002 | [Strategy Detection & Pipeline Assembly](002-strategy-pipeline-assembly/spec.md) | framework/dbms auto-detect; declarative Strategy → StateGraph with 0 if-branches | assembly |
| 003 | [DDL Parsing → Schema Graph](003-ddl-schema-graph/spec.md) | sqlglot multi-dialect DDL → SCHEMA/TABLE/COLUMN + FK | step1 |
| 004 | [AST Loading → Code Structure Graph](004-ast-code-structure-graph/spec.md) | antlr JSON AST → FUNCTION/MODULE/… + structural edges | step2 |
| 005 | [Codebase Scan Agent](005-scan-agent/spec.md) | Framework LLM agent → ScanResult (table patterns, wrappers, notes) | step3 |
| 006 | [Deterministic Code↔Table & Call Linking](006-deterministic-linking/spec.md) | regex grep → READS/WRITES; call classification → CALLS (LLM 0) | step4 (1-2) |
| 007 | [Residual Pattern Linking Agent](007-residual-pattern-linking/spec.md) | bounded agent rounds resolve leftover table-access cases | step4 (3) |
| 008 | [Analysis Context Building](008-analysis-context/spec.md) | per-Function AnalysisContext + large-fn one-liner + table samples | step5 |
| 009 | [Function Analysis: Bottom-up DAG + 3-split/batch + Example Mapping](009-function-analysis/spec.md) | Tarjan SCC + bottom-up rounds → summaries + RULE/EXAMPLE/QUESTION | step6 |
| 010 | [Post-Processing: Module & Table Summaries](010-postprocessing-summaries/spec.md) | aggregate member fns → MODULE/TABLE/COLUMN descriptions | step7 |
| 011 | [Component Clustering (Leiden) & Health Metrics](011-components-health/spec.md) | Leiden components + deterministic risk metrics | step7 |
| 012 | [Catalog Integration & Embedding](012-catalog-embedding/spec.md) | catalog sample rows + ETL lineage; MiniLM embeddings | step5/7 |
| 013 | [LLM Configuration, Cost Governance & Structured Agent Output](013-llm-cost-config/spec.md) | one-file LLM config, USD circuit breaker, submit-tool wrap | cross-cutting |

> **001–013 = 백필된 코어 스펙** (2026-06 시점 코드에서 역공학). 그 이후 **014–046** 은 기능별 증분
> 스펙이며 폴더로만 쌓인다 (append-only). 아래는 그중 **현행 규칙을 바꾼 것**만 추린다.

### 최근 스펙 (현행 규칙 변경)

| # | Spec | One-liner | 비고 |
|---|------|-----------|------|
| 045 | [Graph Integrity Restore](045-graph-integrity-restore/spec.md) | 헌법 위반 시정 — full-rebuild wipe 복원 · 조용한 실패 제거 · 소비자 계약 이름 정합 · artifact 이중덤프 제거 | **016 FR-004 supersede** (code_text 는 step6 아니라 **step2** 가 채운다) |
| 046 | [DRY / Single Truth](046-dry-single-truth/spec.md) | 헌법 II·III 복원 — 전략 분기 0 (DI) · 확장자·루틴라벨·비용추적·MERGE 쿼리 단일진실 · run 스코프 소스캐시 · 죽은 코드 삭제 | 동작 불변 리팩터 |

### Supersede 목록 (옛 스펙의 어느 조항이 죽었나)

| 옛 스펙 | 죽은 조항 | 대체 |
|---|---|---|
| [016](016-node-display-properties/spec.md) | **FR-004** — *"code_text 는 step6 에서 저장 (step2 소스 무파싱 불변식 보존)"* | **045 FR-019** — code_text 는 **step2** 가 파일·라인 있는 **모든 노드**에 단일 지점에서 부여. **step2 무파싱 불변식 폐기.** |

## Cross-service contracts (boundaries)

The analyzer is one service in a pipeline. Only these boundaries ripple to other services
when changed (see [constitution](../.specify/memory/constitution.md) → Cross-Service Contracts):

- **↑ antlr-code-parser** → JSON AST format read from `data/analysis/` (input contract; spec 004).
- **↓ robo-architect** → the Neo4j graph schema, 13 nodes / 21 edges (output contract; spec 003/004/009/011).
- **↔ catalog service** → HTTP `sample-context` + `/lineage` (spec 012).

## Backfill methodology

Each spec was produced by: (1) identifying a coherent unit of capability (a pipeline phase or
a cross-cutting concern) rather than splitting by file; (2) reading the real step functions,
agents, Neo4j writers, and `env_catalog.py`; (3) writing WHAT/WHY language — prioritized user
stories, observable functional requirements, real Neo4j entities, measurable
technology-agnostic success criteria, and the assumptions baked into the implementation.

### Discrepancies corrected against code (notable)

The backfill found several stale docs/comments; specs follow the **code**, not the doc:

- **003** — DDL "FK auto-inference" is aspirational; step1 emits only *explicit* DDL FKs
  (`is_candidate=False`). `schema_resolver.py` is not used by step1. `db_name` is hardcoded `""`.
- **009** — the "RadixAttention 95%+" comment is unverifiable; spec describes the deterministic
  mechanism (byte-identical prefix + warmup) instead. `batch_planner` skip detection uses
  `own_ast_type()`, not the docstring's substring claim.
- **011** — `health_report.json` `top_risky` is **high-risk only**, sorted by (fan_in, loc), so
  it can be fewer than 20 (README says a flat "top 20").
- **013** — `llm_configs/README.md` import snippet (`shared.llm.llm_config_loader`) is stale;
  the real module is `shared/llm/config_loader.py`.
- **007** — README's `_RESIDUAL_MAX_ROUNDS` shorthand is the env var `ROBO_PIPELINE_RESIDUAL_MAX_ROUNDS`.

Specs are intentionally short (~100-165 lines each) — they describe the contract, not the code.
</content>
