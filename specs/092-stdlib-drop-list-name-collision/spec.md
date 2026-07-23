# 092 — stdlib 드롭 목록이 타입 이름이라 작동하지 않는다 (측정 완료 · 대부분 BLOCKED)

작성 2026-07-23. **아직 고치지 않았다.** `specs/093-java-value-position-calls/` 조사 중
발견해 분리했다. 안전분은 지금 적용 가능하고, 대부분(모호분)은 **원리적으로 막혀 있다**.

> **참조 규약**: 이 문서가 말하는 "값 위치 호출 결함"은 `specs/093-java-value-position-calls/`
> 다. 그 문서는 처음 `091` 로 커밋됐다가 병행 세션이 먼저 점유한 `091-response-cache` 와
> 겹쳐 **093 으로 개번**됐다(그쪽 번호는 제품 코드가 이미 참조 중이라 우선). 본문에서는
> 번호가 아니라 **경로**로 참조한다.

---

## 1. 이 spec 의 본질 — 제품이 같은 이름을 정반대로 판정하게 된다

이것이 부차 위험이 아니라 **이 문서의 주제다.**

```
spec 081  DECLARED_RESOLVERS   "insert": (0,)   → 외부 자원 조회다. 이어라.
spec 092  stdlib 드롭 목록      insert           → 표준 라이브러리다. 버려라.
```

**같은 제품이 같은 문자열 `insert` 를 놓고 정반대 결론을 내리게 된다.**

원인은 하나다. **`call_text` 에 수신자 타입이 없다.**

```java
sb.insert(0, "x");                                    // StringBuilder — 버려야 한다
SqlMapper.getSqlMapClient().insert("...createCmbsDesk", dto);  // iBATIS — 이어야 한다
```

AST 의 `FUNCTION_CALL` 노드는 `{type, name, startLine, endLine}` 뿐이고 `call_text` 에는
메서드 이름만 담긴다(`framework_reader._collect_internal`). 수신자 표현식이 없다.
따라서 이 둘은 **이름만으로 원리적으로 구별 불가능하다.** 규칙을 아무리 정교하게 짜도
같은 입력에 같은 답을 낼 수밖에 없다.

이 모순을 해소하지 않은 채 드롭 목록만 채우면, 어느 한쪽이 **반드시 틀린다.**
그래서 이 spec 은 "목록을 채운다"가 아니라 **"어디까지 이름으로 판정 가능한가"** 를 가른다.

## 2. 결함 자체

`classify_call_sites` 는 `call_text` 를 `stdlib/{lang}_stdlib.json` 집합과 대조한다
(`call_classifier.py:57-62`, `:205`). 그런데 자바만 두 쪽의 **의미 축이 다르다.**

```
java_stdlib.json (384 항목)   ArrayList · Arrays · Base64 · AbstractMap …   ← 전부 타입 이름
call_text 실제 값             append · add · put · close · setInt …         ← 전부 메서드 이름
```

**언어별로 다르다 — 이 결함은 자바 전용이다.**

| 목록 | 항목 | 성격 | 실제 호출 이름 적중 |
|---|---:|---|---|
| `c_stdlib.json` | 112 | `printf` `malloc` `strcmp` `fopen` — **함수 이름** | **8/8 정상 작동** |
| `java_stdlib.json` | 384 | `ArrayList` `AbstractMap` — **타입 이름** | **0/8 전부 실패** |
| `python_stdlib.json` | 103 | `open` `len` `print` `range` + 예외 클래스 | 6/8 대체로 작동 |

C 는 메서드가 없고 자유 함수뿐이라 "타입 이름"과 "호출 이름"이 애초에 갈리지 않는다.
자바만 구조적으로 실패한다.

## 3. 안전분 / 모호분 — 세 코퍼스 **원문 호출 라인**으로 판정

이름만 보고 분류하지 않았다. `insert`/`update` 를 잡아낸 것과 같은 방식으로 실제 라인을 읽었다.

### 3-1. 안전분 — 지금 적용 가능

| 이름 | 실측 원문 (발췌) | 판정 근거 |
|---|---|---|
| `append` | `sb.append("=== 여신 신청 목록 ===\n")` · `sbCust.append(AgtScpEnc…)` · `if (sb.length()>0) sb.append(',')` | 세 코퍼스 66 건 전부 StringBuilder/StringBuffer |
| `close` | `try { rs.close(); }` · `ps.close()` · `conn.close()` | 전부 JDBC 자원 해제 |
| `write` | `response.getWriter().write(content)` | `Writer.write` |
| `addAll` | `orderPayments.addAll(pref.getRelated(…))` · `this.orderBy.addAll(fields)` | `Collection.addAll` |
| `removeIf` | `table.removeIf(g -> g.matchesFields(…))` | `Collection.removeIf` (자바 8+ 전용 시그니처) |
| `println` | `System.out.println("[" + LocalDateTime.now()…)` | `PrintStream.println` |
| `printStackTrace` | `t.printStackTrace(System.out)` | `Throwable.printStackTrace` |

**한계 명시**: "안전"의 근거는 **우리 세 코퍼스에서 반례가 0** 이라는 것이지 보편 보장이 아니다.
사내 프레임워크가 `append` 라는 이름의 조회 API 를 두면 그때 깨진다. 새 코퍼스마다 §3 을
같은 방식으로 재확인해야 한다.

### 3-2. 모호분 — **BLOCKED**. 수신자 타입 없이는 판별 불가

| 이름 | 반례가 된 실측 원문 | 왜 막혔나 |
|---|---|---|
| `insert` 27 · `update` 20 · `delete` 4 (ukesa) | `SqlMapper.getSqlMapClient().insert("ReferenceInformationDC_IDCReferenceInformation.createCmbsDesk", cmbsDeskDTO)` | **iBATIS DAO 호출.** 동시에 081 카탈로그 이름이고 JDK 에도 `StringBuilder.insert`·`MessageDigest.update`·`File.delete` 로 존재 |
| `add` 113 (ukesa) | `ContinueBusinessExcption.add(new BusinessException("B4200023","UKEE0088",cuzMsg))` | 수신자가 JDK 컬렉션이 아니라 **프레임워크 예외 누적기**로 보인다. 같은 코퍼스의 `dtos.add(dto)`·`exprs.add(…)` 는 진짜 `List.add` — **한 이름에 둘이 섞여 있다** |
| `put` 38 (ukesa) | `jobInputParam.put("charSharp","#")` · `jobInputParam.put("baseDate", getJobContext().getJobYMS())` | 배치 파라미터 객체. `Map` 인지 프레임워크 컨텍스트인지 이름으로 불명 |

**`add` 하나만 봐도 같은 이름 안에 진짜 JDK 와 프레임워크 API 가 섞여 있다.**
이름 기반 드롭은 이 둘을 나눌 수 없으므로, 넣으면 프레임워크 호출이 조용히 사라지고
빼면 잡음이 남는다. **어느 쪽도 정답이 아니다 → BLOCKED.**

### 3-3. 판단 유보분 — 안전분에 넣지 않았다

`setInt` 2 · `setDate` 2 · `setBoolean` 2 · `setTime` 2 (loan, 계 8 건).

```java
ps.setInt(3, this.creditScore);      ps.setDate(6, this.ratingDate);
ps.setBoolean(10, this.isValid);     cal.setTime(loanStartDate);
```

이 코퍼스에서는 전부 JDBC `PreparedStatement` 와 `Calendar` 다. 그러나 `setDate`/`setInt` 는
**DTO 세터와 이름 모양이 같다.** 프레임워크 DTO 의 `setDate` 가 external 로 잡히면 잘못 드롭된다.
반례를 실제로 찾지는 못했으나 **위험 성격이 모호분과 같으므로 안전분에 넣지 않았다.**
넣으면 loan 이 60 → 68 건이 된다. FP 0 우선 원칙에 따라 보류.

## 4. 실측 (2026-07-23)

### 4-1. 계측 설계

"제대로 채운 목록"을 지어내지 않았다. 제품의 `java_stdlib.json` 에 **이미 적힌 384 개 타입**을
JDK 리플렉션으로 그 타입들의 public 메서드 이름으로 옮겼다(335/384 해소 · 서로 다른 메서드
이름 **2,107 개**). 즉 제품의 의도를 바꾸지 않고 "타입 목록"을 "메서드 목록"으로 옮겨 적었다.

제품의 `classify_call_sites` · `_cluster` · `_compute_metrics` 를 **그대로 호출**했고,
`call_classifier._stdlib_set` 만 계측 스크립트에서 치환했다.
**Neo4j 쓰기 0 · LLM 0 · 정규식 추정 0 · 제품 코드 무수정.**

계측기 재현 규칙은 §7. 스크립트는 저장소에 남기지 않는다.

### 4-2. 전량 투입했을 때 (참고치 — **채택 불가**)

| 코퍼스 | 현재 calls_edges | stdlib 후보 | 전체 대비 | external 대비 | 전량 드롭 시 |
|---|---:|---:|---:|---:|---|
| loan-ejb-app | 440 (int 283 / ext 157) | 91 | **20.7%** | 58.0% | 440 → 349 (−20.7%) |
| ofbiz-order | 491 (int 455 / ext 36) | 35 | **7.1%** | 97.2% | 491 → 456 (−7.1%) |
| ukesa | 1,506 (int 193 / ext 1,313) | 208 | **13.8%** | 15.8% | 1,506 → 1,298 (−13.8%) |

**비중이 20.7% / 7.1% / 13.8% 로 최대 3 배 차이난다. 단일 코퍼스로 일반화하면 안 된다.**
external 대비로 보면 더 벌어진다(58% / 97% / 16%).

이름별 전량 (세 코퍼스 모두 30 종 미만이라 전부 기재):

```
loan-ejb-app   append 56/10 · add 20/13 · close 3/1 · put 3/3 · setInt 2/2
               setDate 2/2 · setBoolean 2/2 · setTime 2/1 · write 1/1        (9 종)
ofbiz-order    add 24/14 · append 4/2 · addAll 3/3 · removeIf 2/2
               println 1/1 · printStackTrace 1/1                             (6 종)
ukesa          add 113/35 · put 38/6 · insert 27/27 · update 20/20
               append 6/2 · delete 4/4                                       (6 종)
```
(형식: 건수 / 서로 다른 호출 루틴 수)

### 4-3. 안전분만 적용했을 때 — **실제 채택 가능한 수치**

| 코퍼스 | 안전 | 모호 | 유보 | calls_edges | 감소율 |
|---|---:|---:|---:|---|---:|
| loan-ejb-app | **60** | 23 | 8 | 440 → 380 | **13.6%** |
| ofbiz-order | **11** | 24 | 0 | 491 → 480 | **2.2%** |
| ukesa | **6** | 202 | 0 | 1,506 → 1,500 | **0.4%** |

**ukesa 는 실효 이득이 사실상 없다.** 후보 208 건 중 202 건(97%)이 모호분이라 손댈 수 없고,
남는 것은 `append` 6 건뿐이라 1,506 건 중 0.4% 만 줄어든다. 포장하지 않는다 —
**이 코퍼스에서 안전분 수정은 의미가 없다.**

셋의 감소율이 13.6% / 2.2% / 0.4% 로 **30 배 이상 벌어진다.** 코퍼스 성격에 따라
효과가 전혀 다르므로 "평균 몇 %" 같은 표현을 쓰지 않는다.

### 4-4. 소비자 영향 — 안전분·전량 모두 **다운스트림 불변**

`specs/093-java-value-position-calls/` §4-3 · §4-4 와 같은 형식:

| | loan | ofbiz | ukesa |
|---|---|---|---|
| 컴포넌트 군집 수 | 16 → 16 | 10 → 10 | 15 → 15 |
| **동반집합 달라진 노드** | **0/173 (0%)** | **0/118 (0%)** | **0/100 (0%)** |
| risk 라벨 바뀐 노드 | **0**/1107 | **0**/885 | **0**/1477 |
| step7 직렬 웨이브 | 3 → 3 | 6 → 6 | 3 → 3 |
| step7 최대 동시발사 | 724 → 724 | 207 → 207 | 393 → 393 |
| caller+callee 항목 (안전분) | 611 → 599 (−2.0%) | 460 → 451 (−2.0%) | 871 → 869 (−0.2%) |
| caller+callee 항목 (전량) | 611 → 576 (−5.7%) | 460 → 437 (−5.0%) | 871 → 777 (−10.8%) |

**이유**: 드롭되는 것이 **전부 `external` 엣지**인데, `_cluster`(Leiden) · `_compute_metrics` ·
step7 DAG 는 모두 `routine_call_pairs` 로 **internal 만** 본다. 그래서 군집·지표·스케줄이
한 건도 안 바뀐다. 유일하게 닿는 소비자는 `build_context_step` 의 callers/callees 집계이며,
프롬프트에 `- {이름}: (외부 또는 미해결)` 로 찍히던 잡음이 줄어드는 효과다.

**→ `specs/093-java-value-position-calls/` 와 파급이 반대다.** 093 은 internal 을 2~3.5 배로
늘려 군집·지표·DAG 를 전부 바꾸고, 092 는 external 만 줄여 다운스트림이 불변이다.
두 결함을 "같은 파급"으로 묶어 판단하면 안 된다.

## 5. 모호분의 해법 — 수신자 타입. 그래서 093 슬라이스에 합류한다

`sb.insert()` 와 `sqlMapClient.insert()` 를 가르려면 **호출의 수신자 표현식이 필요하다.**
그것은 AST 노드에 필드를 더하는 일이므로 **파서 변경**이고,
`specs/093-java-value-position-calls/` 와 **같은 슬라이스**다.

```
지금       FUNCTION_CALL {type, name, startLine, endLine}
필요       + 수신자 표현식 (예: "sb" / "SqlMapper.getSqlMapClient()")
그러면     매칭 축을 "이름" 에서 "수신자.이름" 으로 옮길 수 있다
```

수신자 표현식만으로 타입이 확정되는 것은 아니다(`sb` 가 무엇인지 별도 해석 필요).
그러나 최소한 **`getSqlMapClient()` 를 거친 호출과 지역 `sb` 호출을 구분**할 수는 있고,
081 이 이미 쓰는 `symbol_values`/`REFERENCES` 로 수신자 심볼을 따라갈 여지가 생긴다.

**따라서 이 spec 의 실행 순서는:**

1. **안전분 적용** — 지금 가능. 파서 변경 없음. analyzer 데이터 파일 1 개만 수정.
   단 ukesa 처럼 이득이 0.4% 인 코퍼스가 있음을 알고 한다.
2. **모호분은 BLOCKED 유지** — 093 슬라이스에서 수신자 표현식이 실린 뒤 재개.

`specs/093-java-value-position-calls/` §8-5 에서 이 문서를 역참조한다.
**두 spec 은 여기서 다시 만난다.**

## 6. 착수 조건

- [ ] 착수 시점에 병행 세션이 어떤 코퍼스로 성능 기준선을 측정 중인지 다시 확인할 것.
      자바 코퍼스면 조율 후 착수, C·파이썬 코퍼스면 그대로 착수한다.
      (2026-07-23 확인 시점에는 C 46 개 · `REFER_TO 0` 이었고 093·092 는 자바 전용이라
       무간섭이었다. 이 확인은 그때의 스냅샷이며 착수 시점에 유효하다는 보장이 아니다 —
       반드시 다시 확인한다.)
- [ ] 안전분에 이름을 추가할 때마다 **세 코퍼스 원문 호출 라인을 다시 읽을 것**(§3 방식).
      이름 모양만 보고 안전하다고 분류하지 않는다.
- [ ] 실측 FP 가 1 건이라도 나오면 그 이름을 즉시 안전분에서 뺀다(FP 0 > 재현율).
- [ ] 모호분은 `specs/093-java-value-position-calls/` 의 수신자 표현식 작업 전에는 손대지 않는다.

## 7. 재현 규칙 (계측기는 저장소에 남기지 않는다)

`specs/093-java-value-position-calls/` §3-3 의 `CallCensus` 와 같은 방식이다.
정규식 추정을 쓰지 않고, 제품이 실제로 만드는 산출물과 제품 함수만 쓴다.

**7-1. 드롭 목록 참값 만들기 (`JdkMethodNames`)**
```
입력   pipeline/steps/step5_linking/link_calls/stdlib/java_stdlib.json 의 384 개 타입 simple name
해소   java.lang · java.util · java.io · java.math · java.text · java.net · java.nio(.file/.charset)
       java.time(.format) · java.util.concurrent(.atomic/.locks) · java.util.function · java.util.stream
       java.util.regex · java.util.zip · java.lang.reflect(.annotation/.ref) · java.security
       java.sql · javax.sql · javax.naming  순으로 Class.forName 시도
수집   Class.getMethods() 의 이름 (상속분 포함 — 호출부에서는 구분되지 않으므로)
출력   RESOLVED<TAB>simple<TAB>FQN  /  UNRESOLVED<TAB>simple  /  METHOD<TAB>이름
검증   335/384 해소 · 서로 다른 메서드 이름 2,107 개 (2026-07-23 기준)
컴파일 javac -encoding UTF-8 -d <out> JdkMethodNames.java   (JDK 만 있으면 됨)
```

**7-2. 파급 계측**
```
step2 산출  read_antlr_json + canonicalize_node_lists 를 직접 호출해 code_nodes · call_sites 확보
            (LangGraph step 을 태우지 않는다 — Neo4j·LLM 무관하게 순수 함수만)
전/후 비교  classify_call_sites(call_sites, code_nodes) 를 두 번 호출.
            두 번째는 call_classifier._stdlib_set 를 (기존집합 | 추가이름) 반환으로 치환.
            치환은 반드시 try/finally 로 원복한다.
소비자      _cluster(code_nodes, edges) · _compute_metrics(code_nodes, edges, [])
            step7 DAG 는 find_call_cycles 로 SCC 축약 후 웨이브 분해
            (caller 는 callee 를 기다린다 — routine 라벨 노드로 한정)
컨텍스트    build_context_step 과 같은 집계: callees[caller].add(callee) · callers[callee].add(caller)
```

**7-3. 이름 검증 (§3 의 근거)**
```
call_sites 의 file/line 으로 실제 원문 라인을 읽어 이름별로 나열한다.
AST 의 file 은 계측용 사본 기준 절대경로이므로 '/source/' 뒤 꼬리를 원본 코퍼스 루트에 붙인다.
서로 다른 라인 종류를 전부 훑고, 수신자가 JDK 가 아닌 사례가 하나라도 있으면 모호분으로 내린다.
```

## 8. 검증 상태

- [x] 결함 원인 확정 — 목록은 타입 이름, 대조 값은 메서드 이름 (`call_classifier.py:57-62`)
- [x] 언어별 대조 — C 8/8 정상 · 자바 0/8 실패 · 파이썬 6/8 (자바 전용 결함)
- [x] 세 코퍼스 비중 실측 — 20.7% / 7.1% / 13.8% (배수 상이)
- [x] 이름별 전량 집계 (세 코퍼스 모두 30 종 미만이라 전수)
- [x] **원문 호출 라인으로 안전분/모호분 판정** — `insert`/`update`/`delete` 가 iBATIS 임을 확인
- [x] 안전분만 적용 시 실측 — 13.6% / 2.2% / **0.4%**
- [x] 소비자 영향 실측 — 컴포넌트·건강지표·step7 DAG **전부 0 변화**
- [ ] **수정 미착수** — 코드·데이터 한 줄도 안 고쳤다
- [ ] 안전분 7 종의 반례를 세 코퍼스 밖에서 확인하지 않았다 — 새 코퍼스마다 재확인 필요
- [ ] 판단 유보분(`setInt`/`setDate`/`setBoolean`/`setTime`)의 DTO 세터 충돌 반례 미확인
- [ ] Neo4j 저장 후 역검증 미수행
