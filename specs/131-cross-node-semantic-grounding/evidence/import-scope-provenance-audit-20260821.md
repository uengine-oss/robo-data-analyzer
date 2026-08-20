# C-066 import scope/provenance audit

## 판정

현재 `framework_reader -> import_scopes -> token_matcher -> REFERENCES -> Neo4j -> API/MCP` 경로는
Parser canonical import fact를 끝까지 소비하지 않는다. 이는 표시용 preview가 아니라 semantic edge를
누락하거나 근거 없는 edge를 만들 수 있는 실제 의미 결함이다.

- `framework_reader._bind_import_evidence()`까지는 canonical import ID, directive/target exact range,
  grammar rule, target kind, conditional presence, completeness status가 보존된다.
- `import_scopes._imports_by_file()`가 이를 `{file: [name]}`으로 축약하면서 위 필드를 모두 버린다.
- 이후 C는 `^"..."`, Python은 `^from\s+...` 정규식, Java는 `static`/`.`/`.*` 문자열 분해로
  legacy `IMPORT.name`을 다시 해석한다. Analyzer가 언어명을 직접 분기하지 않는다는 주석과 달리
  언어별 syntax가 ontology profile과 host regex/string operation으로 이동했을 뿐이다.
- `evidence_status=unsupported|not_applicable`, `presence_status=inactive|conditional|unknown`도 scope
  입력에서 구분되지 않는다. 따라서 quoted inactive C include 또는 canonical evidence가 없는
  Java/Python legacy import가 semantic target 후보를 승인할 수 있다.
- `References`와 Neo4j `REFERENCES`는 `(source_id,target_id,evidence_line)`만 저장한다. import fact ID,
  source/target range, presence, resolution outcome이 사라진다. node-detail/API/MCP는
  `{node}::symbol::{target}::{line}`을 새 evidence ID처럼 합성하므로 Parser occurrence로 역추적할 수 없다.

## 유입 commit

- `6956d02c` (2026-05-26): 최초 `reference_linker.py`가 import를 `{name,file,line}` 문자열로 축약하고
  C/Java/Python별 문자열 해석을 도입했다.
- `bc699ee2` (2026-08-05): 로직을 `import_scopes.py`와 `ontology_roles.yaml.reference_imports`로
  이동했다. C `local_path_pattern`, Python `from_pattern`, Java separator/static/wildcard 계약은 이
  commit에서 들어왔고 syntax owner는 바뀌지 않았다.
- Parser Java listener도 `replaceFirst/replaceAll`로 import text를 정리하며, Python listener는
  statement 원문을 legacy name으로 넣는다. 두 언어의 canonical import completeness는 현재
  `unsupported`다.

## actual APAS 영향

`_runs/framework/analyzer-directive-import-consumer-20260821-r1.json`의 sealed accounting:

- source 23, import fact 450, unique fact ID 450
- target kind `angle` 450, `quoted` 0
- presence `active` 449, `inactive` 1
- 입력 source/header 구성: C source 23, header 0
- C profile은 quoted target만 받으므로 450/450이 Pass 2 scope에서 제외된다.
- 현재 입력에는 imported header target 자체가 없어 cross-header edge의 actual 차이는 0이다. 이는
  결함 부재 증명이 아니라 target population 0에 따른 비발동이다.
- inactive 1건은 `zapamcom10110.c`의 `<zngmmcap20020.h>`이며 `#if 0` 아래다. angle 형식이라 현재는
  우연히 제외되지만 같은 presence의 quoted include는 구분 없이 활성 scope가 된다.

## 분류

- canonical import ID/range/presence/completeness drop: 실제 의미 손실
- angle include 450/450 배제: 실제 검색/관계 누락 가능성; actual target population 0 때문에 현재 edge 영향 0
- inactive/degraded import 승인 가능성: 근거 없는 semantic edge 가능성
- `REFERENCES.evidence_line`만 공개: source-to-public provenance 손실
- import 대상 수 제한이나 실행 분할 문제가 아니다. 숫자 증설로 해결할 수 없다.

## 공식 규격과 설계 경계

- C11 6.10.2는 angle header 검색 위치와 quoted 검색 방식을 implementation-defined로 둔다.
  따라서 basename match는 build/toolchain resolution을 대체할 수 없다.
  <https://www.open-std.org/jtc1/sc22/wg14/www/docs/n1570.pdf>
- Java JLS 21 7.5는 single type, type-on-demand, single static, static-on-demand를 서로 다른 import
  의미로 정의하고 scope를 해당 compilation unit으로 한정한다.
  <https://docs.oracle.com/javase/specs/jls/se21/html/jls-7.html#jls-7.5>
- Python reference 7.11은 각 module clause를 개별 수행하고 alias, from-member, wildcard, relative level의
  binding 의미를 구분한다.
  <https://docs.python.org/3/reference/simple_stmts.html#the-import-statement>

이 자료는 설계를 대신하는 권위가 아니라 현재 grammar와 반례를 교차 검증하는 1차 근거로만 사용한다.

## 근본 계약과 red

Parser grammar/listener가 import/include occurrence별 exact range와 다음 구조를 language-neutral evidence
IR에 발행한다: module/path components, imported member, alias, wildcard, local/system/relative kind. Analyzer는
source text를 regex/split으로 재파싱하지 않고 구조화 fact와 project/build resolution evidence만 소비한다.
target resolution은 Analyzer 소유를 유지하되 unknown/ambiguous/missing build context에서 임의 후보를
선택하지 않고 occurrence outcome ledger에 unresolved로 남긴다.

구현 전에 다음 red를 고정한다.

1. inactive/conditional/unknown import는 active semantic scope를 승인하지 않는다.
2. `unsupported|not_applicable|partial` producer를 legacy name으로 보충하지 않는다.
3. C quoted/angle/computed를 Analyzer regex 없이 구분하며 build resolution이 없으면 guessed basename edge를
   만들지 않는다.
4. Java 네 import variant와 Python direct/from/relative/alias/wildcard/multi-clause가 grammar-owned 구조로
   exact accounting된다.
5. import에 의존해 파생된 reference occurrence는 canonical import fact ID와 resolution outcome을 보존하며,
   aggregate `REFERENCES`는 occurrence ledger를 대체하지 않는다.
6. duplicate/missing/out-of-order fact와 graph/API/MCP population/hash 불일치는 fail closed한다.

## 미검증

- Java classpath/module path와 Python `sys.path`/import hook을 포함한 authoritative target resolution provider는
  현재 입력 계약에 없다. grammar-owned syntax fact만으로 이를 resolved라고 주장하지 않는다.
- APAS에는 header target population이 없으므로 실제 cross-file positive edge는 별도 sealed fixture와 향후
  build trace가 있는 actual corpus에서 검증해야 한다.

