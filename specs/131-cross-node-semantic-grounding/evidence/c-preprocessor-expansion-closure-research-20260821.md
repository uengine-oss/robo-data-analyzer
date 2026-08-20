# C 전처리 확장 폐쇄 조사 — 2026-08-21

**Scope**: C-064 — 선언 근거를 구성별 실제 호출 의미 근거로 승격

## 1. 반증된 가정

원문에서 `f()`가 ANTLR call syntax로 보였다는 사실은 C translation unit의 실행 호출을 증명하지 않는다.
`WRAP(f())`의 parameter는 replacement-list에 따라 0회, 1회, 여러 번 방출되거나 stringize/token-paste의
재료가 될 수 있다. 반대로 macro body나 token paste가 원문에 없던 call syntax를 생성할 수도 있다.
따라서 macro 이름 목록으로 raw-source call을 제외하는 방식도, raw-source call을 그대로 보존하는 방식도
모두 구성별 전처리 결과 없이 definitive 의미 판정을 할 수 없다.

현재 evidence v1의 `(sourceId, decodedTextSha256, kind, source range, ordinal)` fact ID도 충분하지 않다.
동일 header range가 서로 다른 translation unit/configuration에서 다른 의미를 가질 수 있고, 같은 header가
한 translation unit에 여러 번 포함되거나 macro argument가 여러 번 확장되면 하나의 source range에서 여러
semantic occurrence가 생긴다.

## 2. 현재 APAS에서 확인한 실제 입력 결손

`D:/work/robo/project/data/source`를 전수 회계한 결과는 다음과 같다.

- 전체 23파일 = C source 23 + header 0
- `compile_commands.json` 0
- include 450회, 고유 include target 284개
- 입력 안에서 resolve되는 include 0, 결손 include 450/450
- host PATH, WSL에 Clang/GCC/MSVC 없음; Docker CLI도 없음
- Parser `pom.xml`/Docker runtime은 Java 17 + ANTLR이고 compiler frontend 의존성·설정 계약이 없음

따라서 현재 APAS만으로 `PFM_DBG` 76회를 포함한 구성별 expansion을 실제 빌드와 동일하다고 증명할 수
없다. 이 상태에서 임의 predefined macro, include stub, guessed flag 또는 자체 부분 preprocessor를 넣는 것은
근본 해결이 아니라 새로운 거짓 의미 owner다.

## 3. 1차 근거와 한계

- Clang JSON Compilation Database 정본은 command object 하나가 translation unit의 한 컴파일 방법이며,
  같은 file에 여러 configuration command가 존재할 수 있다고 명시한다. shell 역해석 손실을 피하기 위해
  `arguments`가 `command`보다 우선이다.
  https://clang.llvm.org/docs/JSONCompilationDatabase.html
- Clang `PPCallbacks`는 `MacroDefined`, `MacroUndefined`, invocation range/arguments가 있는
  `MacroExpands`, include/file change와 skipped-range 관련 callback을 제공한다.
  https://clang.llvm.org/doxygen/classclang_1_1PPCallbacks.html
- Clang `SourceManager`는 token의 spelling location과 expansion location, include chain별 `FileID`를
  구분한다.
  https://clang.llvm.org/doxygen/classclang_1_1SourceManager.html
- Clang `TokenBuffer`는 parser가 받는 expanded token과 source의 spelled token mapping을 제공한다.
  그러나 공식 문서 자체에 macro argument mapping과 include expansion의 FIXME가 남아 있다. 그러므로
  `TokenBuffer` 하나를 완전한 provenance로 신용하지 않고 `PPCallbacks`와 `SourceManager` event를 함께
  전수 회계해야 한다.
  https://clang.llvm.org/doxygen/classclang_1_1syntax_1_1TokenBuffer.html
  https://clang.llvm.org/doxygen/structclang_1_1syntax_1_1TokenBuffer_1_1Expansion.html
- GCC 공식 문서는 standalone preprocessor text가 whitespace를 바꾸고 line marker로 file/line만
  전달하며, macro 결과 token은 outermost macro 사용 line으로 보고된다고 설명한다. 즉 `gcc -E` text만으로
  nested expansion exact source closure를 증명할 수 없다.
  https://gcc.gnu.org/onlinedocs/gcc-14.1.0/cpp/Preprocessor-Output.html
  https://gcc.gnu.org/onlinedocs/gcc-6.5.0/cpp/Line-Control.html
- MSVC `/P`도 macro-expanded `.i`와 `#line`을 만들지만 nested macro provenance 계약은 제공하지 않는다.
  따라서 expanded syntax 입력으로는 쓸 수 있어도 exact evidence closure를 자동 승인할 수 없다.
  https://learn.microsoft.com/en-us/cpp/build/reference/p-preprocess-to-a-file?view=msvc-170

위 자료는 구현 후보의 capability를 판정하는 근거일 뿐이다. 특정 frontend가 다른 compiler의 dialect,
builtin, target ABI, predefined macro를 동일하게 재현한다고 가정하지 않는다.

## 4. 채택 계약

### 4.1 책임 경계

1. build intake가 translation unit마다 exact argv, working directory, main file, compiler identity를 받는다.
   동일 file의 여러 command/configuration은 합치지 않는다.
2. language-specific preprocessing trace provider가 include resolution/instance, define/undef lifetime,
   conditional decision, macro invocation/argument/expanded tokens, spelling·expansion provenance를 생산한다.
3. ANTLR C parser는 provider의 **expanded token stream**을 AST로 만든다. ANTLR grammar/listener는 계속
   syntax와 call boundary의 소유자이고 compiler frontend는 preprocessing/token provenance만 소유한다.
4. Analyzer는 source 문자열이나 macro name으로 재판정하지 않고 Parser의 configured semantic occurrence와
   provenance closure만 소비한다.

### 4.2 신뢰 등급과 fail-closed

- `authoritative`: 실제 compile action의 compiler implementation/target/mode와 일치하고, 필요한 event와
  expanded-token provenance가 완전하며 모든 consumed input이 content hash로 닫힌 trace.
- `compatible_replay`: 다른 frontend, text-only preprocessor output, 또는 capability 일부가 빠진 trace.
  AST 후보와 진단에는 쓸 수 있으나 definitive call edge/exclusion의 권위 입력은 아니다.
- `unresolved`: compile action, compiler, header/generated input, event, mapping 중 하나라도 없거나 hash가
  맞지 않는 상태. raw-source 결과로 fallback하지 않는다.

provider 종류는 확장 가능해야 한다. Clang callback provider를 첫 구현 후보로 둘 수 있으나 `clang == 모든 C
compiler`로 하드코딩하지 않는다. 지원하지 못하는 toolchain/configuration도 typed unresolved로 정확히
처리하는 것이 범용 계약이다.

### 4.3 canonical identity와 exact-once

v2 identity는 적어도 다음 축을 포함한다.

- `compileActionId`: main source content, compiler artifact/identity, target/mode, preprocessing-affecting exact
  arguments와 working-directory/path mapping의 canonical digest
- `configurationId`: 동일 file의 configuration을 분리하는 stable ID
- `includeInstanceId`: parent include instance + directive occurrence + resolved input content의 chain ID
- `expansionInstanceId`: parent expansion instance + invocation occurrence + selected definition + argument binding ID
- `semanticOccurrenceId`: compile action + expanded token half-open range + grammar kind + ordinal

source range는 provenance link일 뿐 semantic occurrence key가 아니다. macro argument가 두 번 방출되면 expanded
ANTLR call 두 개와 서로 다른 occurrence ID가 생기고, 방출되지 않거나 stringize되면 call은 0개다. header의
동일 spelling range가 여러 번 포함돼도 include-instance별로 분리한다. merge는
`semanticOccurrenceId` exact-once이고 configuration 간 합집합을 임의로 단일 사실로 축약하지 않는다.

### 4.4 completeness

각 compile action은 다음 population을 닫아야 한다.

- build commands selected/emitted/unresolved
- source inputs discovered/hashed/missing/generated-unavailable
- includes requested/resolved/skipped/unresolved
- directives define/undef/conditional emitted/unresolved
- macro invocations and argument bindings emitted/unresolved
- expanded tokens emitted/unmapped
- ANTLR semantic occurrences emitted/unresolved
- provenance links emitted/unresolved

`population = emitted + explicitlyUnresolved`가 모든 축에서 맞고 duplicate가 0일 때만 completeness 후보가 된다.
자동 회귀만으로 authoritative를 승인하지 않고 결정론 replay와 봉인 macro 반례 직접 판정을 별도로 수행한다.

## 5. red/구현 순서와 rollback 경계

먼저 Parser에 다음 formal red를 고정한다.

1. 같은 source, 서로 다른 `-D` configuration이 서로 다른 call population과 ID를 만든다.
2. macro가 argument call을 drop/once/duplicate/stringize하면 expanded ANTLR call이 각각 0/1/2/0이다.
3. nested macro와 token paste가 만든 call의 expansion chain이 exact source evidence로 닫힌다.
4. 같은 header의 다중 include/configuration이 occurrence를 충돌 없이 분리한다.
5. compile command/header/compiler/capability 결손은 raw-source fallback 없이 typed unresolved다.
6. text-only GCC/MSVC-style output은 authoritative로 승격되지 않는다.

계약/trace provider/expanded-token ANTLR adapter/Analyzer v2 consumer를 독립 책임 슬라이스로 커밋한다. 기존 v1
raw-source parser를 곧바로 삭제하지 않고 v2가 authoritative일 때만 semantic producer로 선택하며, v2 결손
상태에서는 v1을 의미 fallback으로 쓰지 않고 explicit unresolved를 낸다. 각 슬라이스는 독립 revert 가능해야
한다.

현재 APAS actual authoritative 검증은 build command와 284개 header closure가 공급되기 전까지 미검증이다.
이는 숫자 제한이나 작업 회피가 아니라 현재 source inventory가 직접 증명하는 입력 결손이다.
