# Plan

1. Introduce a shared referenced-symbol context record and JSON serializer.
2. Build records from REFERENCES and the full reachable INIT_BY source chain,
   retaining both source names and each source record's exact declaration value.
3. Feed identical structured JSON to framework and DBMS Rule/Example prompts.
4. Strengthen only Rule/Example instructions around constant and initializer use.
5. Keep the existing typed given/when/then output while requiring exact known
   literals and explicit questions for unresolved runtime values.
6. Test record construction, multi-source retention, Unicode-safe serialization,
   identical consumer wiring, and removal of delimiter joins.
