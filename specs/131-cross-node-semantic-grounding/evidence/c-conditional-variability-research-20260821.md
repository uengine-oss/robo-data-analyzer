# C conditional-variability 처리 근거 — 2026-08-21

## 결론

현재 Parser의 “첫 branch 유지” 전처리는 제거 대상이다. 그러나 directive를 지우고 양쪽 token을 한 C
parser에 그대로 넣는 것도 일반해가 아니다. C preprocessor 조건은 선언·문장·표현식·macro invocation의
중간을 가를 수 있으므로 서로 배타적인 token을 연결하면 source 어느 configuration에도 없는 AST를 만들 수
있다.

채택 경계는 다음 두 모드와 공통 fallback이다.

1. `compile_commands.json` 또는 동등한 검증된 compile action이 있으면 그 action별 macro/include/working
   directory를 concrete configuration 증거로 사용한다. 같은 파일에 여러 command가 있으면 서로 다른
   configuration으로 보존하고 합치지 않는다.
2. configuration이 없는데 모든 variant의 구문 분석이 필요하면 configuration-preserving preprocessor/parser
   provider가 presence condition을 붙인 token/AST를 생산해야 한다. ANTLR listener는 provider가 만든 concrete
   또는 presence-conditioned parse tree를 소비할 수 있지만 첫 branch를 임의 선택하지 않는다.
3. 그런 provider가 아직 검증되지 않은 환경에서는 exact conditional region과 condition source를 evidence
   ledger에 전부 보존하고, 영향을 받는 의미 owner를 명시적 `degraded/insufficient`로 만든다. `#if 0/1`처럼
   완전히 결정 가능한 상수도 동일 evaluator 계약을 통과시켜 active/inactive로 표시한다. unknown branch를
   버리거나 active로 가장하지 않는다.

이 fallback은 “분석 성공처럼 보이게 하는 제한”이 아니라 missing build context를 정직하게 표면화하면서
source/evidence closure를 보존하는 계약이다. variability-aware provider가 추가되면 동일 IR의
`conditionProvenance`와 completeness를 채우므로 Analyzer를 다시 언어별로 만들 필요가 없다.

## 1차 근거

- SuperC, *Parsing All of C by Taming the Preprocessor* (PLDI 2012): conditional token stream에서 parser state를
  fork/merge하고 presence condition을 보존해야 실제 C variability를 다룰 수 있음을 설계·Linux 실험으로
  보였다. 특히 function-like macro invocation과 조건부 token의 상호작용 때문에 단순 branch 열거/결합이
  충분하지 않다. https://www.paulgazzillo.com/papers/pldi12.pdf
- TypeChef, *TypeChef: Toward Type Checking #ifdef Variability in C* 및 후속 variability-aware parsing:
  partial preprocessor, variability-aware lexer/parser, feature model을 결합해 모든 branch를 조건부 AST로
  보존한다. https://www.cs.cmu.edu/~ckaestne/pdf/oopsla11_typechef.pdf
- TypeChef 공식 저장소는 frontend jar/Maven 사용법을 제공하지만 README 자체가 classpath 구성이 어렵다고
  명시한다. 현재 Robo의 Java 17/ANTLR/Windows corpus에서 build·license·성능·AST mapping을 검증하지 않았으므로
  이름만 보고 production dependency로 채택하지 않는다. https://github.com/ckaestne/TypeChef
- LLVM Clang JSON Compilation Database 정식 규격은 translation unit마다 working directory, file, 실제 compile
  argv를 제공하며 같은 file에 여러 command object가 있을 수 있음을 명시한다. concrete configuration 입력의
  표준 경계로 채택한다. https://clang.llvm.org/docs/JSONCompilationDatabase.html
- Clang `PPCallbacks`는 `If/Ifdef/Ifndef/Else/Endif`, condition value와 skipped source range를 제공한다.
  concrete compile action의 전처리 provenance 교차검증 후보로 사용 가능하다.
  https://clang.llvm.org/doxygen/classclang_1_1PPCallbacks.html
- 현재 Robo `antlr-grammars/CParser.g4`는 ISO C preprocessing-file 계열에 “No ANTLR4 rule”이라고 명시하고,
  `CLexer.g4`의 `Directive`는 hidden channel이다. 따라서 현 grammar만으로 build condition을 평가했다고
  주장할 수 없다.

## 채택하지 않은 shortcut

- `#if 0`과 `#if 1`만 문자열 비교하고 나머지는 첫 branch 선택: actual 일부만 고치며 unknown을 계속 위조한다.
- 모든 directive line만 지우고 양 branch 동시 파싱: mutually exclusive token을 연결해 source에 없는 AST를
  만들 수 있다.
- 모든 macro 조합 전수 enumeration: configuration 수가 지수적으로 증가하고 include/function-macro 상호작용을
  닫지 못한다. 숫자 cap을 두면 다시 숨은 손실이다.
- TypeChef/SuperC를 즉시 runtime 의존성으로 추가: 현재 build/maintenance/license/corpus mapping 검증이 없으므로
  연구 결과를 맹신하는 행동이다. provider spike와 actual 비교를 별도 reversible slice로 해야 한다.

## 구현·actual 검증 결과

- 구현 책임 슬라이스: Parser `d8dca383f681a6111e1788d6506fa052beb6dd1e`.
- Parser frontend가 lexer `Directive` token의 exact range로 17개 conditional region을 보존했다.
  16개 constant expression은 단일 보수적 evaluator 계약으로 active/inactive를 판정했고, 외부 build
  macro가 필요한 `#ifdef PFM_DBG` 1개는 unknown으로 남겼다.
- 기존 첫-branch 전처리뿐 아니라 parse 실패 시 alternate branch 하나를 골라
  `RECOVERED_VALIDATED`로 가장하던 `c.alternate-preprocessor-branches.v1`도 red로 오판을 재현한 뒤
  제거했다. unknown function은 `PARTIAL`과 `insufficient_missing_build_configuration`으로 남는다.
- actual call ledger는 total 12,113 = active 12,080 + inactive 33 + conditional/unknown 0이다. 기존
  12,112 active view가 포함하던 `#if 0` call 33개를 inactive evidence로 보존하고, 기존에 삭제했던 active
  else call 1개를 복구했다. unknown region에는 call이 없지만 region fact와 unresolved scope ID는 남는다.
- APAS 23파일 deterministic replay와 conditional-region 내부 call 81개 전수 직접 대조가 일치했다.

## 남은 검증

- compile database가 실제 제공되는 corpus에서 action별 configuration ID와 concrete AST를 검증하지 않았다.
- configuration-preserving provider spike는 TypeChef와 Clang concrete action을 동일 IR로 변환한 뒤
  정확도·시간·메모리·유지보수성을 비교한다. 한쪽을 선채택하지 않는다.
- 현 Parser의 C source type-name 수집과 include/define 추출에는 과거 언어별 heuristic이 남아 있다.
  이번 conditional/call IR의 구문 owner는 ANTLR context/directive token이지만, 그 heuristic을 범용 계약으로
  오인하지 않는다. 별도 red와 grammar/build-context 대체가 없이는 확대하거나 Analyzer로 옮기지 않는다.
