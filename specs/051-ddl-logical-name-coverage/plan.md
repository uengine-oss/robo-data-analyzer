# Plan

1. Keep the generic linker on raw source and model Shopmall compile-time adjacent
   string boundaries in that project's verified skill regexes.
   Treat a matched Skill contract as immutable scan output and reject entity prefix
   matches before DDL resolution.
2. Build one DDL column allowlist from HAS_COLUMN relationships and use it for
   both prompt rendering and deterministic result validation.
3. Serialize exact table ids and their descriptions/columns/samples as separate
   JSON fields through one Framework/DBMS serializer.
4. Require exact table-id equality at the R/W boundary and report every rejected
   variant and omitted expected table without inferred canonicalization.
5. Require exact column-name equality against the table-specific DDL allowlist
   and report code/DDL mismatches.
6. Preserve logical-name-only outputs through step 7 aggregation, Neo4j update,
   and artifact state without a DDL-comment fallback.
7. Cover rejected identifier variants, multiple tables, exact/invalid columns,
   omissions, raw-source skill patterns, and aggregation regressions with tests.
