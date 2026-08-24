# C-080 IR 2.0 sealed inputs — 2026-08-23

이 원장은 제품 구현 전에 입력·설정·계약·정답지를 한 번 봉인한다. 아래 SHA-256이 달라지면 같은
입력으로 취급하지 않고 변경 원인과 재봉인 이유를 먼저 남긴다. secret 값은 기록하지 않는다.

## Git anchors

- Analyzer `main`/`origin/main`: `ffce245be4cfc6824853413b2e8c5aff58cca1ee`
- Parser `main`/`origin/main`: `5dd64106540674a4aebfdb37ad897a375950d335`
- 사용자 제공 Analyzer 기준 `bfec4e2bc04e8fa7cbbcc2bb194d95019d8248b6`은 현재 HEAD의 조상이며,
  사이 변경은 spec 131 문서뿐이다.

## Configuration and contracts

| SHA-256 | Path / meaning |
|---|---|
| `b6b9ad971521bc46edf9cdf449b4bb5c397389fdc37a8480f7cc9c823ec6c35d` | `robo-workspace/.env` (값 비공개) |
| `938c38a85b58b8fcbdbaadc2a1579570816e26cee7afab6904f5390e52ac0218` | root `CLAUDE.md` |
| `e04cc436760c9c2ad55a87ee9137fd6847885f880dde62992de7ce6a7c1ebb20` | `.claude/skills/robo-work/SKILL.md` |
| `429a2e5342b3921ab024604575a37102cebbb5e5d28308d275bfaf7016f3b972` | Analyzer constitution |
| `ddc99e30ff228fc1ea5f5ff319f3dcd5614ca1aa0563a4e40318d176284f987e` | `ABSOLUTE_RULES.md` |
| `78071198063efcbb3a906853a617776e8459a753999dea1c3c0a05a4441ece94` | 구현 전 `CURRENT_STATUS.md` |
| `0b0e55c5128d7f6e1bf989ff92f6d1e989cd966bc4c0c7a2d97ebdd0ea489cc0` | `history/handovers/04-검증방법.md` |
| `8131dcef3871c06ab71e1af3ff8a6a6c6c3ac3cee5868f89eae27ab46dc23c63` | 삭제된 계약 경로를 교정한 `README.md` |
| `630f37dc0ed678fc8ad094e8d59e99d0f3300df5d34b6cd906fb5eeb7530d77c` | 구조 component를 닫은 `contracts/common-evidence-ir-v1.md` |

## Parser source and answer keys

| SHA-256 | Path / meaning |
|---|---|
| `656ef9594f763bb7ca119cc248cff3e36b747ca29d33e16505eb27077f69e049` | generated `CParser.java` grammar contexts |
| `79d77f08de7c34a38b2b6aae42bfada624392ebdbd74cadc7d9e8873730d06cf` | generated `CLexer.java` vocabulary |
| `b613dc4034590b31df92f52241eece754dba43ead60640fe2d635ae7ab5f04e6` | 구현 전 `CAstListener.java` |
| `54bb880f352e60a9937b2da2676ae94cd80ddb9657c1e9ab79bb2045159919b8` | 구현 전 `CallableEvidenceExtraction.java` |
| `a92c5f724811e493366d20262a7d7887c95bff38274c3edece6f6bd580d1b435` | 구현 전 `EvidenceIrSealer.java` |
| `7beebce764dc83aba4d1c4033d7b17c16ecc3865bb220f8cadce1eaa80782e58` | 구현 전 `CLanguageModule.java` |
| `ae045af9e9c2ba6c3cdc8b198c6f671c062d2e5ecdc3f5583e726b3d28caddcc` | IR 1.3 역사 정답지 `CCallBindingEvidenceContractTest.java` |
| `c6d5b1c4275c5a1ef2667a7add9b9031b4c1517a0d57cd25fa1a85f3f4606f61` | IR 2.0 formal red와 source vectors |

IR 1.3 역사 기준은 20/20 green이다. IR 2.0 formal red는 구현 전 4/4 fail이며 첫 좌표는 다음과 같다.

- identity: `CStructuralEvidenceIr2ContractTest.java:58`, actual `1.3.0`
- lexical scope: `CStructuralEvidenceIr2ContractTest.java:85`, `scopePath` 부재
- completeness: `CStructuralEvidenceIr2ContractTest.java:149`, `call_binding` row 존재
- typed syntax: `CStructuralEvidenceIr2ContractTest.java:108`, schema 부재

## Analyzer WIP and answer keys

| SHA-256 | Path / meaning |
|---|---|
| `773188104028b9f660379f631d38e2b37c135e11dab67376caf11b7b070218c1` | 보존 대상 modified `pipeline/steps/step2_load_ast/evidence_ir.py` |
| `3155ce124e9b45f299a180da9e380a6f022f06cc337313b394a4e23b9af5f8d1` | evidence decoder 회귀 정답지 |
| `7e795ebf6bfc2976d7fd1ed53440a813c27c310ad1aa87fd899f814e8a7f0dff` | 기존 IR 1.3 binding formal red |

구현 전 decoder 회귀는 50 fail/7 pass이며 첫 좌표는 `evidence_ir.py:1704`, `_KINDS` 미정의다.
기존 binding formal red는 9/9 error이며 첫 좌표는 `test_call_binding_classifier.py:171`, 현재 classifier가
`callable_facts` 인자를 받지 않는다는 오류다. 둘 다 Parser-owned binding을 소비하는 방향으로 green하지 않는다.

## Incremental actual slice

| SHA-256 | Path / meaning |
|---|---|
| `5d57a30a68796932fa95496c3a7cfa297b0cbb365b78728b31c6f6887ff1cfec` | `분석대상모음/framework/c/shopmall/source/order/cart.c` (30,709 bytes) |
| `502fcf68fee2be3356ca5f3cec137c669918553763b964f8b6e73a24ad6a0049` | actual-slice direct structural ledger test |
| `0038b99844f6fb778666aebf7ab5926e6a673ba00a464b6069bc2a6f536a484f` | 원인 좌표의 구현 전 `CParserBase.java` |
| `abd69a10670870ee241a115556a23639e1953d91aeac7269f603c24d66d023e5` | partial-upload 최소 재현 formal red |
| `a4888e03c201d8562af4dcfbc4ed7cda12fd97ff02dedcd1ef6a168099ccae4b` | body 내부 unknown local type 최소 재현 formal red |

이 actual slice는 Strategy A APAS 경로 밖의 단일 C 파일이다. 위 두 hash를 기록한 뒤 한 번만
source→ANTLR→IR 구조 회계에 사용한다.

첫 실제 실패는 `cart.c:492:0`, token `money_t`다. actual parser 값은 recoveries 2, diagnostics 1,
calls 202, conditional scope 0, decode exact이다. 같은 문법 형태만 남긴 최소 재현으로 원인을 분리한다.
첫 수정 뒤 같은 좌표가 남은 이유는 external-declaration probe가 body 전체를 읽으면서 내부
`money_t total;`의 block-item 구조 probe를 비활성화했기 때문이다.

## Closed producer and consumer evidence

- Parser commit/push: `da453950f70d6b38831d33a0c06fda230122f2ec`.
- Parser full regression: 211 tests, failures 0, errors 0, skipped 11. The sealed Strategy A
  actual corpus test remained skipped.
- Synthetic recovery counterexample: missing `)` had token range `-1..-1`; Parser now emits no
  false exact component and records callable `population=1/emitted=0/explicitlyUnresolved=1`,
  reason `insufficient_parser_recovery`.
- Analyzer WIP cause: the IR 1.3 attempt renamed `_KINDS` but left main dispatch at
  `evidence_ir.py:1704`, omitted `version` arguments, and never dispatched callable facts. The
  larger ownership defect was trusting Parser final semantic payload. IR 2.0 retains strict
  range/table/completeness validation and moves binding/type/linkage into Analyzer.
- Analyzer focused regressions before the final full run:
  - evidence decoder: 61 passed;
  - formal 0/1/N resolution: 8 passed;
  - step2/step5: 260 passed, 19 subtests passed;
  - graph/API/search/execution consumers: 181 passed, 22 subtests passed.

## Canonical actual Analyzer replay

- The first direct external-file parse produced an intentionally rejected unscoped artifact:
  SHA-256 `4cf84dea936e9c1db023687f7195e119cc4d19d5675e4368657593099e730d16`,
  `sourceId=unscoped/5d57.../cart.c`. No IDs were rewritten and Analyzer did not consume it.
- The official `ParserWorkspace.intakeFromPath` route produced canonical `sourceId=cart.c`:
  artifact SHA-256 `0bc50e52c0b24c317e06865f4b41b79312edf505b998266d28666658bdfcecc3`,
  292,783 bytes, diagnostics 0, calls 202, callables 9.
- Analyzer actual values: CodeNodes 178, calls 202, callables 9, internal 4, external 198,
  edges 4, accounted 202/202. Two in-memory deterministic ledgers were byte-identical; SHA-256
  `dacb544433f607a6300b23c662d526b6af2789d282f027eb828da92c397cb182`.
- First internal coordinate `cart.c:15479:27` is `count_cart_items(member_id)`. The source has a
  prior `static` declaration at line 38 and compatible same-TU `static` definition at line 111.
- First external coordinate `cart.c:2383:44` is the `memset` call; no uploaded callable definition exists, so it remains external evidence
  and creates no external stub/CALLS edge.

## External-state gate and exclusions

- Neo4j 인증과 `SHOW DATABASES` 성공: `neo4j`, `test`, `framework`, `dbms`, `system`만 존재하며 모두 online.
- 임시 DB를 만들지 않았다.
- `scripts/recompute_strategy_a_notation.py`는 hash를 계산하거나 읽거나 실행하지 않았고 stage 대상이 아니다.
- Strategy A 312/312 입력·결과는 봉인 상태를 유지하며 재실행·재분석하지 않는다.
- Architect 및 Catalog/Fabric 사용자 WIP는 읽거나 실행하거나 수정하거나 stage하지 않는다.

## 2026-08-24 final Analyzer slice verification

- Strategy A runner test를 제외한 worktree 전체 `unittest` 1,144건을 한 번 실행했다. C-080
  producer/decoder/adapter/core/graph/API consumer 실패는 0건이었다. 유일한 실패 18건은 봉인 실험용
  prompt 9개를 production prompt 폴더에 둔 배치 오류로, 각 prompt가 안정성 계약 2건을 위반한
  동일 원인이었다.
- 실험 prompt를 내용 hash가 보존된 run별 `llm-prompts/*.jsonl`과 별개인 spec 실험 경로로 격리하고
  evaluator가 그 경로를 사용하게 했다. production prompt 안정성 집중 red는 4/4 통과했다.
- unstaged Strategy B/성능/scan WIP가 C-080 통과를 가리는지 배제하기 위해 staged index만 별도
  임시 checkout으로 내보냈다. step2/step5/formal ledger/graph/API/run lifecycle 65건은 실패 0,
  skip 1로 통과했다. 임시 checkout은 검증 뒤 확인된 repository 하위 경로에서 제거했다.
- staged diff whitespace 검사는 오류 0이다. staged 파일에는
  `scripts/recompute_strategy_a_notation.py`, Strategy B prompt/evaluator, scan/성능 WIP,
  `CURRENT_STATUS.md`의 진행 중 변경이 포함되지 않는다.
