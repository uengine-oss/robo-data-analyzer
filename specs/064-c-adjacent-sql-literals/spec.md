# Spec: C adjacent SQL literal linking

## Problem

Framework C code legally splits one SQL string across adjacent literals. The scan-agent regex is applied to raw physical text, so a pattern requiring `SELECT ... FROM table` misses the relation when `SELECT` and `FROM` are in different adjacent literals. Live Shopmall evidence found 39 source-backed READS missing while all 52 writes were present.

## Requirements

- FR-001: The deterministic regex linker must apply configured patterns to the logical value of adjacent C/C++ string literals as well as raw source.
- FR-002: It must preserve an offset map to the original source and store the table identifier's physical source line as `evidence_line`.
- FR-003: It must not concatenate non-adjacent literals separated by executable tokens or punctuation.
- FR-004: It must not introduce project/table/function-specific patterns or data.
- FR-005: Raw and logical scans must deduplicate the same semantic match.
- FR-006: Other language behavior and existing pattern validation must remain unchanged.
- FR-007: An SQL literal with nested DML must expose every source-backed table
  relation, including reads inside an outer write statement.

## Success criteria

- A split `SELECT`/`FROM` C literal produces exactly one READS edge to the declared table with the physical table line.
- Non-adjacent literals produce no synthetic match.
- A nested `SELECT` inside an `UPDATE` produces both the outer write and inner read.
- Focused, structure, full unit, prompt hash, compile, and diff gates pass.
