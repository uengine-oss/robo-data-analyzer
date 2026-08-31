# Import binding environment v1

**Status**: Current
**Owner**: Analyzer
**Scope**: Parser import binding entry → 선택 source/module 범위의 결정적 종료 상태

## 1. 책임 경계

Parser는 import statement, binding entry, source range, presence와 문법 소유 사실을 생산한다. Analyzer는
그 사실을 다시 파싱하지 않고, 현재 실행에서 봉인된 선택 source와 그 source에서 투영된 module, 언어별
검색 환경만 결합한다. LLM은 import 결속에 참여하지 않는다.

환경 미제공은 선택 source가 0개인 완전한 환경과 다르다. 전자는
`insufficient_import_binding_environment` unresolved이고, 후자는 입력 source가 환경에 없으므로 계약 오류
또는 명시 unresolved이다.

## 2. 환경 wire

```yaml
import_binding_environment:
  selected_sources:
    - source_id: <canonical selected source-relative path>
      language: <Parser language>
  module_ids_by_source:
    <source_id>: [<Analyzer module stable ID>]
  c_include_roots: [<ordered selected-source-relative directory>]
```

- source language는 Parser envelope 값을 사용한다. language 필드가 없던 legacy envelope만 ontology의 canonical
  source-extension registry로 보충하며 임의 이름 추측은 하지 않는다.
- source path와 include root는 `/` 구분의 상대 경로이며 absolute, `..`, NUL, backslash를 허용하지 않는다.
- `c_include_roots`는 compiler/build 자료에서 명시적으로 봉인된 값만 받는다. 현재 실행에 값이 없으면 빈
  배열이지 암묵적 project root가 아니다.
- 선택 source와 C file module projection은 `code_file_id(source_id)` exact identity로 결합한다. 표시·원문
  로딩용 `Module.file_path`, basename 또는 임의 suffix 검색 결과는 internal 권한이 아니다.

## 3. C adapter

active·complete·structured `source_file` entry만 C 경로 결속 후보이다.

1. quoted include는 현재 source의 디렉터리 + target을 먼저 검사한다.
2. quoted와 angle include는 봉인 순서의 `c_include_roots` + target을 검사한다.
3. 위 exact candidate가 선택 source이고 그 C file module이 하나면 `internal`이다.
4. exact candidate는 없지만 선택 source 중 target 전체 component suffix를 가진 source가 있으면 search
   환경이 불충분하므로 `unresolved`이다. 이 후보를 basename 결속으로 승격하지 않는다.
5. 선택 source closure 전체에 target suffix 후보가 없으면 현재 업로드 범위 밖임이 증명되므로
   `external`이다.
6. 잘못된/계산된 target, 불완전 evidence, 모호하거나 누락된 module projection은 `unresolved`이다.
7. inactive import는 `excluded`; conditional/unknown presence는 `unresolved`이다.

지원하지 않는 언어는 이름 유사도로 추측하지 않고 언어 adapter 부재를 이유로 unresolved로 닫는다.

## 4. occurrence ledger

각 Parser binding entry는 source order로 정확히 한 행을 만든다.

```yaml
outcome: internal | external | excluded | unresolved
reason: <internal이면 빈 값, 나머지는 명시 reason>
internal_module_ids: [<internal일 때만 nonempty unique IDs>]
```

- internal만 `internal_module_ids`를 가질 수 있다.
- external/excluded/unresolved는 reason이 필수이고 module ID를 가질 수 없다.
- legacy·partial·non-active evidence는 internal이 될 수 없다.
- occurrence/fact/module ID, hash, path/range 같은 검증 메타데이터는 내부 ledger에만 둔다. LLM input/output과
  제품 graph node에는 보내지 않는다.

## 5. 합격 기준

- 같은 입력·환경은 byte-stable ledger와 summary hash를 만든다.
- 환경이 없으면 기존 replay가 unresolved를 유지한다.
- local exact, configured include-root exact, suffix-only, no-candidate, invalid/computed, inactive, duplicate를
  각각 internal/internal/unresolved/external/unresolved/excluded/failure로 검증한다.
- 봉인된 SKT 2-file 선택 closure에서는 25 binding entry가 external 25, unresolved 0으로 닫혀야 한다.
