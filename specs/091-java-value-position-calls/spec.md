# 091 — 자바 값 위치 호출이 AST 에 안 잡힌다 (미착수 · 착수 조건 있음)

작성: 2026-07-23. **아직 고치지 않았다.** spec 081 조사 중 발견해 파급만 계측하고 기록만 남긴다.
착수 조건은 §7 을 볼 것.

## 1. 증상

자바 소스에서 **반환값을 쓰는 호출**이 AST 에 `FUNCTION_CALL` 노드로 만들어지지 않는다.
문장으로 쓴 호출(`foo.bar(x);`)만 잡히고, 대입·return·인자·캐스팅에 들어간 호출은 통째로 없다.

```java
context.lookup(jndiName);                          // 잡힘
Object h = (Object) context.lookup(jndiName);      // 안 잡힘
return context.lookup(jndiName);                   // 안 잡힘
System.out.println(context.lookup(jndiName));      // println 만 잡히고 lookup 은 안 잡힘
```

**C · Python 은 정상**이다(§3-2). 자바 전용 결함이다.

## 2. 원인 — 문법 차원에서 확정

`antlr-grammars/Java20Parser.g4` 는 grammars-v4 판본이며 좌재귀를 풀기 위해
`primaryNoNewArray` 를 재작성하면서 **호출 대안을 규칙 안에 직접 인라인**했다.
그 결과 `methodInvocation` 규칙을 참조하는 곳이 **`statementExpression` 하나뿐**이다.

```
Java20Parser.g4:967    statementExpression : … | methodInvocation | classInstanceCreationExpression ;
Java20Parser.g4:1377   methodInvocation : methodName '(' … | expressionName '.' identifier '(' … ;

Java20Parser.g4:1267   primaryNoNewArray                                    ← 값 위치는 전부 이쪽
        : …
        | methodName '(' argumentList? ')' pNNA?
        | typeName '.' typeArguments? identifier '(' argumentList? ')' pNNA?
        | expressionName '.' typeArguments? identifier '(' argumentList? ')' pNNA?
        | …
Java20Parser.g4:1298   pNNA : … | '.' typeArguments? identifier '(' argumentList? ')' pNNA? ;
```

그런데 `JavaAstListener` 는 `enterMethodInvocation` 만 훅한다.

- `src/main/java/legacymodernizer/parser/antlr/java/JavaAstListener.java:304` — `enterMethodInvocation`
- 같은 파일 `:326` — `enterClassInstanceCreationExpression` (`new X()`)

`classInstanceCreationExpression` 도 `statementExpression` 에서만 참조되므로 **`new X()` 도
값 위치에서는 똑같이 누락**된다. 즉 결함 범위는 메서드 호출 + 객체 생성 둘 다다.

**수정 지점**: `JavaAstListener` 에 `enterPrimaryNoNewArray` / `enterPNNA` 훅을 더해 호출
대안을 방출한다. 판별 규칙은 §3-3 의 계측기가 쓴 것과 같다.

## 3. 재현법

### 3-1. 프로브 (최소 재현)

```java
package com.probe;
import javax.naming.InitialContext;

public class Probe {
    private InitialContext context;
    public void   stmtPosition(String s)         { System.out.println("hello"); }          // 10행
    public Object valuePositionCast(String s)    throws Exception { Object h = (Object) context.lookup(s); return h; }  // 16행
    public Object valuePositionReturn(String s)  throws Exception { return context.lookup(s); }                          // 21행
    public Object valuePositionPlain(String s)   throws Exception { Object h = context.lookup(s); return h; }            // 25행
    public void   stmtPositionQualified(String s) throws Exception { context.lookup(s); }                                // 29행
    public void   nestedArgument(String s)       throws Exception { System.out.println(context.lookup(s)); }             // 33행
}
```

격리 실행 (`project/data` 를 건드리지 않는다):

```bash
cd D:/work/robo/project/antlr-code-parser
SERVER_PORT=8401 java "-Dparser.data.root=<work>" -jar target/parser-0.0.1-SNAPSHOT.jar
# <work>/source/probe/Probe.java 를 둔 뒤 업로드 모드(빈 body)로 파싱
curl -s -X POST http://127.0.0.1:8401/antlr/parsing -H "Content-Type: application/json" -d '{}'
# <work>/analysis/probe/Probe.json 의 FUNCTION_CALL 노드를 센다
```

실측 결과 — 호출 7건 중 3건만 방출:

```
방출  println(10행) · lookup(29행) · println(33행)     ← 전부 문장 위치
누락  lookup(16행) · lookup(21행) · lookup(25행) · lookup(33행)
```

### 3-2. 다른 언어 대조 (같은 프로브 구조)

| 언어 | 값 위치 호출 | 결과 |
|---|---|---|
| C | `FILE *fp = fopen(path,"r");` · `printf("%d", atoi(path));` | **전부 방출** (4/4) |
| Python | `fp = open(path,"r")` · `print(open(path).read())` | **전부 방출** |
| Java | 위 프로브 | **값 위치 전부 누락** |

`CAstListener` 는 `postfixExpression` 기반, `PythonAstListener` 는 자체 호출 규칙 기반이라
값 위치를 탄다. 자바만 문장 위치 규칙에 걸려 있다.

### 3-3. 코퍼스 계측기 (참값 세기)

정규식 추정을 쓰면 안 된다(spec 081 조사에서 정규식 하네스가 `throws` 절과 `.lookup(` 을
놓쳐 오판한 전례가 있다). **제품과 같은 문법 클래스로 파스트리를 걸어야 한다.**

계측기는 `Java20Lexer`/`Java20Parser`(진입 규칙 `start_()`)로 파싱한 뒤
`Java20ParserBaseListener` 를 상속해 다음을 센다.

```
EMITTED  : MethodInvocationContext                      ← 제품이 지금 잡는 것
MISSING  : PrimaryNoNewArrayContext / PNNAContext 중
           "직접 자식에 '(' 터미널이 있고 그 앞 자식이
            IdentifierContext 또는 MethodNameContext" 인 것
           단 '(' 가 자식 인덱스 0 이면 제외 — 그건 '(' expression ')' (괄호식)이다
유일화   : '(' 토큰의 tokenIndex (중첩 호출 이중계수 차단)
```

컴파일·실행:

```powershell
$AR='C:\Users\roede\.m2\repository\org\antlr\antlr4-runtime\4.13.2\antlr4-runtime-4.13.2.jar'
javac -encoding UTF-8 -cp "D:\work\robo\project\antlr-code-parser\target\classes;$AR" -d <out> CallCensus.java
java  -cp "<out>;D:\work\robo\project\antlr-code-parser\target\classes;$AR" CallCensus <코퍼스루트>
```

**계측기 신뢰도 검증 (필수 절차)**: 계측기의 EMITTED 를 제품이 실제로 만든 `call_sites` 와
대조한다. 2026-07-23 실측에서 loan 440/440 · ofbiz 491/491 · ukesa 1492/1506 으로 일치했다.
ukesa 의 차 14 건은 제품이 별도 훅(`enterClassInstanceCreationExpression`)으로 잡는
문장 위치 `new X()` 이고 계측기는 그것을 세지 않는다.

## 4. 계측 수치 (2026-07-23, 실물 코퍼스)

### 4-1. 호출 지점 자체

| 코퍼스 | 지금 방출 | 누락 | 참값 | 배수 |
|---|---:|---:|---:|---|
| loan-ejb-app (자바 EJB) | 440 | 927 | 1,367 | **3.11×** |
| ofbiz-order-standalone (자바 ERP) | 495 | 1,437 | 1,932 | **3.90×** |
| ukesa (자바 + iBATIS) | 1,492 | 2,629 | 4,121 | **2.76×** |

**이 수치는 하한이다.** 계측기가 값 위치 `new X()` 를 세지 않으므로 실제 증가폭은 더 크다.

loan-ejb-app 에서 누락되는 이름의 성격 (원문 등장 횟수 → 방출 0):
`lookup` 4 · `getLocalHome` 18 · `getRemoteHome` 12 · `getDataSource` 1 ·
`findByPrimaryKey` 30 · `create` 38 · `getConnection` 9 · `prepareStatement` 7 ·
`executeQuery` 4 · `next` 20 — **반환값을 쓰는 호출은 하나도 안 잡힌다.**
반대로 방출된 상위는 `append` 56 · `setRollbackOnly` 44 · `setStatus` 26 처럼 전부 void 다.

### 4-2. `calls_edges` (제품 `classify_call_sites` 를 그대로 통과시킨 결과)

| 코퍼스 | raw | internal | 유일쌍 (Neo4j MERGE 저장분) |
|---|---|---|---|
| loan-ejb-app | 440 → 1,366 (**×3.10**) | 283 → 726 (×2.57) | 338 → 1,007 (**×2.98**) |
| ofbiz | 491 → 1,905 (**×3.88**) | 455 → 1,589 (×3.49) | 242 → 1,191 (**×4.92**) |
| ukesa | 1,506 → 4,094 (**×2.72**) | 193 → 378 (×1.96) | 711 → 2,135 (**×3.00**) |

stdlib · framework 드롭은 전후 모두 0 건이다 — **누락분이 드롭으로 흡수되지 않고 거의 전부
엣지가 된다.**

#### 드롭이 0 인 진짜 이유 — 걸러지는 게 없어서가 아니라 **매칭이 구조적으로 불가능**해서다

`classify_call_sites` 는 `call_text` 를 `stdlib/{lang}_stdlib.json` 집합과 대조한다
(`call_classifier.py:57-62`). 그런데 두 쪽의 **의미 축이 다르다.**

```
java_stdlib.json (384 항목)   ArrayList · Arrays · Base64 · AtomicInteger · AbstractMap …   ← 전부 타입 이름
call_text 실제 값             append · add · put · get · size · setStatus …                  ← 전부 메서드 이름
```

타입 이름 목록에 메서드 이름이 있을 수 없으므로 `sb.append()` · `list.add()` · `map.put()` 은
**한 건도 드롭되지 않고 전부 `CALLS{external}` 엣지가 된다.** 이건 091 이 만드는 문제가 아니라
**이미 존재하는 결함**이고, 091 은 그 오염을 약 3 배로 키운다.

→ 091 착수 전에 이 드롭 목록을 먼저 손봐야 한다(§8-2). 안 그러면 `equals` · `toString` ·
`length` 까지 호출 그래프에 박힌다.

### 4-3. 소비자 결과 변화

`calls_edges` 소비처: `build_context_step` · `framework_per_node` · `batch_analyze` ·
`build_components_step` · `health_metrics_step`.

| | loan | ofbiz | ukesa |
|---|---|---|---|
| 컴포넌트 군집 수 | 16 → 15 | 10 → 9 | 15 → 20 |
| 군집 소속 노드 | 173 → 295 | 118 → 279 | 100 → 177 |
| **동반집합이 달라진 노드** | 139/173 (**80%**) | 118/118 (**100%**) | 84/100 (**84%**) |
| 컴포넌트 naming LLM 호출 | 16 → 15 | 10 → 9 | 15 → 20 |
| risk 라벨 바뀐 노드 | 16/1107 (1%) | 40/885 (5%) | 21/1477 (1%) |
| `in_cycle` | 0 → 1 | 5 → 19 | 14 → 33 |
| caller+callee 컨텍스트 항목 | 611 → 1,682 (×2.75) | 460 → 2,173 (**×4.72**) | 871 → 2,440 (×2.80) |

컴포넌트는 **개수는 거의 그대로인데 구성이 거의 전부 바뀐다.** 화면에 나가는 분해가 달라지는
변경이므로 별도 검증·별도 보고가 필요하다.

### 4-4. step7 스케줄링 DAG

`framework_per_node.py` 는 `calls_edges` 로 의존 DAG 를 만들어 발사 순서를 정한다.
SCC 축약 후 웨이브 분해 결과:

| 코퍼스 | 직렬 웨이브 수 | 최대 동시 발사 수 | 대기 노드 |
|---|---|---|---|
| loan-ejb-app | 3 → 4 | 724 → 680 | 60 → 104 |
| **ofbiz** | **6 → 12 (2배)** | **207 → 76 (−63%)** | 109 → 240 |
| ukesa | 3 → 4 | 393 → 375 | 46 → 64 |

여기에 프롬프트 입력도 커진다 — `ctx.callees` 가 `three_split_llm_fanout._format_callee_summaries`
에서 callee 요약 목록으로 매 프롬프트에 렌더링되는데 그 항목이 ×2.75~×4.72 가 된다.

### 4-5. 안 변하는 것

- **`one_liner` 사전압축 LLM 호출 수 불변** — 대상 선정이 `token_count`(본문 크기) 기준이라
  호출 그래프와 무관하다.
- **spec 081 외부참조 링킹 연결 건수 불변 (이득 0)** — 링커는 `call_sites` 를 안 쓰고 본문을
  직접 읽으며, `FUNCTION_CALL` 노드도 `Calls` 엣지도 **인자 정보를 담지 않기 때문에**
  고쳐도 링킹이 쓸 수가 없다(§6).
- C · Python 무관.

## 5. 계측 방법의 한계 (측정 안 한 것)

- **실제 step7 경과시간 증가는 안 쟀다.** 재려면 LLM 을 태운 전 파이프라인을 전후 2회 돌려야
  하고 GPU 가 병행 세션과 겹친다. §4-4 는 스케줄 **구조** 변화이지 초 단위 실측이 아니다.
- 값 위치 `new X()` 누락분은 계측에 미포함 — 실제 증가폭은 §4 수치보다 크다.
- Neo4j 에는 아무것도 쓰지 않았다. 그래프 저장 후 역검증 미수행.
- 자바 코퍼스 3개만 쟀다. 다른 고객사 코드에서 배수가 같다는 보장은 없다.

## 6. 왜 spec 081 에서 뺐나

1. **081 에 이득 0** — 링킹 연결이 1건도 늘지 않는다(§4-5). `Calls` 엣지는
   `(caller_id, callee_id, scope, callee_*)` 뿐이고 `FUNCTION_CALL` 노드는
   `{type, name, startLine, endLine}` 뿐이라 **키 인자 표현식이 어디에도 없다.**
   래퍼 승격 판정("키 인자가 파라미터와 같은 심볼인가 / 재대입됐는가")은 본문을 읽어야만
   가능하다. 결함 A 를 고쳐도 이 결론은 그대로다.
2. **병행 세션 작업을 깨뜨린다** — spec 082~090 이 전부 step7 성능 최적화이고 그 기준선이
   현재의 (구멍 난) DAG 위에서 측정됐다. ofbiz 기준 직렬 깊이 2배 · 동시 발사 −63% 면
   그 기준선이 통째로 무효가 된다.
3. **화면 산출물이 바뀐다** — 컴포넌트 동반집합 80~100% 변경은 별도 검증·별도 보고 대상이다.

## 7. 착수 조건 (지키지 않으면 착수 금지)

- [ ] **병행 세션의 step7 성능 작업(spec 082~090 계열)이 일단락될 것.** 진행 중에 착수하면
      그쪽 기준선이 측정 도중 바뀐다. 착수 전 `git log` 로 step7 perf 커밋이 멈췄는지 확인한다.
- [ ] 착수 시 파서 저장소(`antlr-code-parser`)와 analyzer 를 **같은 슬라이스로** 다룰 것.
      골든 바이트 계약(`src/test/resources/recovery/golden/java.json`)이 걸리므로 끄지 말고
      필드 단위 전수 대조 후 갱신한다(spec 081 의 `initValue` 수정이 선례).
- [ ] 회귀 기준선을 **먼저** 확보할 것 — 수정 전 전 파이프라인 1회 실행으로
      결정론 산출물(코드본문·요약·READS·WRITES·CALLS·REFERENCES) 수치를 고정한다.
- [ ] 컴포넌트 분해 변경은 **화면·보고 대상**이므로 사용자 승인 후 반영한다.
- [ ] **stdlib 드롭 목록을 먼저 고칠 것**(§4-2 · §8-2). 지금은 타입 이름 목록이라 `put` ·
      `append` 가 한 건도 안 걸러진다. 이 상태로 091 을 얹으면 오염이 3 배가 된다.
      선행 수정 후 "091 없이도 엣지가 줄어드는지"를 먼저 실측한다.

## 8. 설계 결정 (착수 시 반드시 지킬 것)

### 8-1. 새 노드 타입은 기존 `FUNCTION_CALL` **하나로 통일**한다

값 위치 호출에 `EXPR_CALL` 같은 새 타입을 주고 싶은 유혹이 있으나 **주지 않는다.**

- 노드 `type` 은 규약상 **Neo4j 라벨**이다. 타입을 쪼개면 지금 `FUNCTION_CALL` 만 보는
  모든 소비처가 둘 다 다루도록 고쳐야 하고, **하나를 빠뜨리면 지금과 똑같은 구멍이 다시 생긴다.**
- "문장 위치인가 값 위치인가"는 소비처 누구도 묻지 않는 구분이다. 구분이 필요 없는 것을
  라벨로 승격시키지 않는다.

### 8-2. **노드 방출과 엣지 생성은 별개 판정이다** — 091 의 핵심 제약

두 소비자가 요구하는 것이 정반대다.

| 소비자 | 원하는 것 | `put` / `append` 를 |
|---|---|---|
| spec 081 외부참조 링킹 | 해석기 호출부의 **위치** (거기서 인자를 본문에서 뜬다) | **애초에 안 본다** — `DECLARED_RESOLVERS` 화이트리스트 |
| 호출 그래프 `link_calls` | 함수→함수 **관계** | **걷어내야 한다** — 지금 못 걷어냄(§4-2) |

따라서 **091 은 AST 노드만 방출하고, 그 노드가 `Calls` 엣지가 될지는 `link_calls` 가 자기
기준으로 판단**한다. 파서 쪽에서 "이건 stdlib 이니 노드로 안 만들겠다" 같은 판단을 하지 않는다
— 파서는 언어 사실만 기록하고, 정책은 소비처에 둔다.

**선결 과제**: `java_stdlib.json` 을 타입 이름 목록에서 **드롭 대상 호출 이름 목록**으로
바꾸거나, 매칭을 `Owner.member` 축으로 옮긴다. 어느 쪽이든 091 착수 **전**에 하고, 그 자체로
지금의 오염(`append` 56 건 등)이 줄어드는지 먼저 실측한다.

### 8-3. 이중 계수 · 중첩 · `new X()`

- **이중 계수**: `cache.put(k, v);` 는 `statementExpression → methodInvocation` 이면서 동시에
  `primaryNoNewArray` 로도 걸릴 수 있다. §3-3 계측기가 쓴 `'(' 토큰 tokenIndex 유일화`를
  리스너에도 그대로 적용한다.
- **중첩**: `f(g(h(x)))` 를 평탄하게 3 개로 낼지 트리로 낼지 정해야 한다. 현재
  `FUNCTION_CALL.children` 은 **항상 비어 있다** — 기존 산출물과 맞추려면 평탄이 안전하다.
- **`new X()`**: `classInstanceCreationExpression` 도 `statementExpression` 에서만 참조되므로
  값 위치에서 똑같이 누락된다(§2). 같은 슬라이스에서 함께 고친다.

### 8-4. 091 이 spec 081 에 주는 실제 이득 — 연결 건수가 아니라 **중복 제거**

081 링커는 지금 `resolver_catalog.call_sites()` 라는 **자체 정규식**으로 호출부를 찾는다.
제품이 이미 만드는 `state["call_sites"]` 를 안 쓰는 이유는 **거기에 해석기 호출이 0 건**이기
때문이다 — `lookup` · `getLocalHome` · `getResourceAsStream` 이 전부 값 위치라 누락돼 있다(§4-1).

091 이 착수되면 `call_sites` 에 그것들이 들어온다. 그러면 081 은 **호출부 위치를 제품 산출물에서
받고 인자만 본문에서 뜨는** 구조로 바꿔 정규식을 지울 수 있다(DRY). 다만 노드에도 엣지에도
**인자 표현식은 여전히 없으므로** 본문 슬라이싱 자체는 남는다. 연결 건수 이득은 §4-5 대로 0 이다.

## 9. 검증 상태

- [x] 문법 차원 원인 확정 — `Java20Parser.g4` 원문 대조
- [x] 최소 재현 (프로브 7건 중 3건만 방출)
- [x] C · Python 대조로 자바 전용임을 확인
- [x] 실물 3개 코퍼스 계측, 계측기를 제품 실측과 대조 검증
- [x] 파급 계측 — `calls_edges` · 컴포넌트 · 건강지표 · step7 DAG (제품 함수 그대로 호출)
- [ ] **수정 미착수** — 코드 한 줄도 안 고쳤다
- [ ] step7 실제 경과시간 영향 미측정
- [ ] Neo4j 저장 후 역검증 미수행
