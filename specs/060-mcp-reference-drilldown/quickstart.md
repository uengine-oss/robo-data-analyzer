# Quickstart / Verification

1. Run Analyzer unit and contract tests.
2. Start Analyzer through `robo-workspace` and list MCP tools; require `cluster_retrieve,node_detail`.
3. Call smoke query against `database=neo4j`; compare list IDs/rules/examples/evidence to the pre-change baseline and assert forbidden fields zero.
4. Call `node_detail` for an anchor FUNCTION, a self-recursive FUNCTION, and a TABLE; compare source lines/code, relations, rules, examples and columns to direct Neo4j rows.
5. Call empty/unknown IDs and a deliberately mocked duplicate/incomplete source; assert explicit error codes.
6. Run gold gate (neo4j 29/30 minimum), search UI Playwright, console/network checks.
7. Re-read diff and grep `detail=|tiered|_tiered_clusters` active code references to zero.
