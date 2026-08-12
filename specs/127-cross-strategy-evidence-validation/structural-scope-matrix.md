# Deterministic structural scope and residual matrix

The boundary is proof, not convenience: only grammar- or catalog-owned facts are
deterministic. Everything else remains explicit residue or LLM interpretation.

| Axis | Declared deterministic scope | Languages/dialects | Residual / fail-closed boundary |
|---|---|---|---|
| Routine/control/statement coordinates | Parser AST ranges and labels | C, Java, Python, Oracle PL/SQL, PostgreSQL PL/pgSQL | Pro*C and COBOL are lexing-only, not semantic parser support |
| CALL | Parser call node/callsite and exact resolver | Same parser-supported languages | Dynamic dispatch, generated names, synonym/runtime lookup remain unresolved |
| Framework READ/WRITE | Declared external SQL keys and embedded SQL only where the skill/parser contract identifies them | C, Java, Python | Arbitrary ORM/reflection/string construction is not claimed exhaustive |
| DBMS READ/WRITE | DML grammar object roles: SELECT/FROM/JOIN/USING, INSERT target+source, UPDATE target+sources, DELETE target+USING, MERGE target+source, nested subqueries/CTEs | Oracle SQL/PLSQL and PostgreSQL SQL/PLpgSQL implemented contracts | Dynamic SQL text, synonyms, runtime search path and unsupported vendor grammar remain unresolved |
| Oracle DB link | `schema.object@link`/`object@link` syntax and link token are grammar facts | Oracle | Remote physical identity is not locally guessed; needs Catalog metadata |
| Identifier identity | Exact schema/name plus quote flags | Oracle, PostgreSQL | Quoted/case-sensitive identity fails closed when provider metadata cannot preserve it |
| Table sample | Catalog-owned table identity and captured sample snapshot | Resolver-supported datasource | No name-only cross-datasource merge; missing provider sample remains absent |
| Rule row coordinate/path | Parser control tree plus direct statement ownership | Parser-supported languages | Mutable-variable time versions and effects after terminating child controls are not yet fully modeled |
| Business meaning | Never deterministic merely from names | All | LLM output is measured, validated and quarantined; it is not promoted to source truth |

Measured corpus statements are kept separate:

- Framework shopmall saved run: independent gold matched ROUTINE 124, TABLE 22,
  COLUMN 153, CALLS 657, READS 76, WRITES 52, REFERENCES 1,350 and INIT_BY 7
  with zero missing/extra on those axes. This does not prove other languages or corpora.
- RWIS saved DBMS graph currently contains CALLS 5, READS 124 and WRITES 40. The
  historical answer-key file contains 5 call, 123 read and 40 write occurrences, but
  its occurrence coordinates and the graph's merged DML-edge identity are different
  contracts; the existing checker cannot compare them directly. No product verdict is
  made from that one-count difference.
- The new Oracle/PostgreSQL data-object contracts are covered by synthetic/parser/
  Analyzer/Catalog/Fabric tests. End-to-end corpus replay is intentionally absent because
  the DBMS Analyzer run ceiling is zero.
