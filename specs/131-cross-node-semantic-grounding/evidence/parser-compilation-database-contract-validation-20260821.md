# Parser compilation database 계약 검증 — 2026-08-21

**Commit**: Parser `a5310608c2060e31e728bc8d8bcc9a2b95b6645e`

## 1. 책임 범위

이 슬라이스는 C-064의 첫 실행 계약이다. compiler나 shell을 실행하지 않고 JSON compilation database
command object를 exact 보존·분류한다. 실제 preprocessing trace 생산, expanded-token ANTLR 배선, Analyzer v2
consumer는 이 커밋의 완료 주장에 포함하지 않는다.

생산 모델은 다음을 분리한다.

- 원문 `directory/file/arguments/command/output`와 resolve된 `Path`
- shell parser 없이 provider에 전달 가능한 `ARGUMENT_VECTOR`
- 원문은 보존하지만 authoritative trace 입력으로는 승인하지 않는 `SHELL_COMMAND_ONLY`
- 필수 field/source 결손인 `UNRESOLVED`
- snapshot `EXACT|PARTIAL|UNRESOLVED`
- `population = emitted + explicitlyUnresolved`

command object ID는 전체 JSON object를 key-sorted canonical tree로 만든 뒤 SHA-256 domain hash를 사용한다.
array 순서와 argument 순서는 보존하고 object field 순서는 제거한다. 동일 command object가 여러 번 나타나면
base ID + duplicate ordinal로 서로 다른 exact-once ID를 부여한다. malformed/unknown field도 canonical tree에
남으므로 ID 입력에서 조용히 소실되지 않는다.

## 2. formal red와 구현 중 반증

최초 `CompilationDatabaseContractTest`는 생산 타입이 전혀 없어 test compilation에서
`CompilationDatabaseSnapshot`, `CompilationDatabaseLoader`, status/capability/model symbol 부재로 실패했다.

첫 구현 후에는 shell-only command object가 `PARTIAL`이어야 한다는 계약에 대해 실제 결과가
`UNRESOLVED`여서 1/4 실패했다. command object는 exact 보존됐지만 trace eligibility만 결손인 경우를 전체 DB
부재와 합치면 안 되므로 status를 교정했다.

cached diff 자기 감사에서 추가로 다음 두 손실을 발견해 커밋 전에 red를 보강했다.

- malformed JSON parse 실패 시 이미 읽은 raw bytes hash가 사라짐
- optional 표준 field의 잘못된 JSON type이 `null`로 조용히 축약됨

read와 syntax failure를 분리해 malformed raw SHA-256을 보존하고, wrong type은 explicit reason과 command
population으로 회계한다.

## 3. 자동 회귀

- 집중: `mvnw.cmd -q -Dtest=CompilationDatabaseContractTest test` — 7/7 pass
- Parser 전체: `mvnw.cmd -q test`
  - suites 49
  - tests 177
  - failures 0
  - errors 0
  - skipped 10
- `git diff --cached --check` — pass

## 4. 결정론 전수 회계

봉인 fixture에서 다음 population을 직접 고정했다.

- 동일 TU, `-DFEATURE=1|0`: population 2 = emitted 2 + unresolved 0, ID 2 distinct
- shell-only: population 1 = emitted 0 + unresolved 1, reason
  `insufficient_argument_vector`
- absent/malformed database: 각각 population 1 = emitted 0 + unresolved 1
- exact + missing source + non-object: population 3 = emitted 1 + unresolved 2
- identical duplicate command: population 2, canonical occurrence ID 2 distinct
- wrong optional field type: population 1 = emitted 0 + unresolved 1

서로 다른 database entry 순서와 서로 다른 JSON object field 순서에서 정렬된 command ID 원장은 동일했다.
argument 배열은 byte 의미 순서를 유지한다. shell command를 split하거나 실행하는 경로는 0이다.

## 5. 봉인 직접 판정과 actual 경계

주 에이전트가 fixture JSON, resolve된 source path, argv 순서, status/reason, duplicate ordinal을 직접 대조했다.
7개 계약 case가 의도한 의미와 일치했다.

actual APAS에는 compilation database가 0이므로 현 단계 생산 계약에 넣으면
`UNRESOLVED / population 1 / emitted 0 / explicitlyUnresolved 1 /
insufficient_compilation_database`다. 더 중요한 실제 결손은 header 0, include 450/450 unresolved다. 이
슬라이스가 존재한다는 이유로 APAS expansion이나 call 의미가 검증됐다고 주장하지 않는다.

다음 책임 슬라이스는 database discovery와 source/configuration 매칭, provider trace capability/closure
계약이다. 실제 trace가 authoritative일 때만 expanded tokens를 ANTLR에 전달한다. 결손 상태에서는 v1
raw-source call을 권위 fallback으로 사용하지 않는 Analyzer/Parser 배선을 별도 red로 고정한다.
