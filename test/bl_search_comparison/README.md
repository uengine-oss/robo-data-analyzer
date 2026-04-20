# 비즈니스 규칙(Rule) 검색, 어떻게 해야 정확할까?

> 발표 대본처럼 읽으시면 됩니다. 하나의 질문을 끝까지 따라가면서 모든 기법을 설명합니다.
> 실제 실행 로그: `c:/tmp/bl_search_result.log`
>
> ℹ️ Example Mapping 전환 이후, 검색 단위는 **Rule(규칙 한 문장) + 하위 Example(Given/When/Then 구체 케이스)** 입니다.
> 같은 Rule 은 여러 FUNCTION 에서 재사용될 수 있습니다 (rule_hash 로 노드 공유).

---

## 1. 우리가 풀려는 문제

저희는 코드를 분석해서 Neo4j 에 **"비즈니스 규칙(Rule)"** 을 저장합니다. 각 Rule 은 아래 구조입니다.

| 구성 | 뜻 | 예시 |
|------|------|------|
| **Rule.statement** | 규칙 한 문장 | "잔액이 출금액보다 작으면 InsufficientFundsException" |
| **Example.given** | 초기 상태 | "account.balance=100, amount=200" |
| **Example.when** | 실행 동작 | "출금 요청" |
| **Example.then** | 기대 결과 | "InsufficientFundsException, balance 변경 없음" |
| **Example.is_boundary** | 경계값 여부 | true (예: balance=amount 일 때 허용) |
| **HAS_RULE 관계 속성** | flow_id / guard_rule_id / branch_from / coupled_domains | Rule 과 FUNCTION 을 잇는 경로의 메타 |

Rule 하나당 **최소 2개 이상의 Example** 이 있고, 경계값 케이스가 1개 이상 포함됩니다. 같은 문장의 Rule 은 `rule_hash` 로 함수 간 공유됩니다.

**오늘 풀어볼 문제는 이겁니다.**

> 사용자가 평범한 말로 질문했을 때, 수많은 Rule 중에서 **정확히 맞는 것만** 어떻게 찾아줄까?

---

## 2. 하나의 질문을 끝까지 따라가 봅시다

> ### 💬 "SKB 은행에서 결과코드 'A002'일 때 오류 메시지는?"

두 가지 방법을 차례로 보여드립니다.

---

## 3. 먼저, 가장 흔한 방법 — **벡터 검색**

### 벡터 검색이 뭐냐면요

각 Rule 을 **statement + 대표 Example 을 결합한 텍스트** 로 임베딩(숫자 1,536 개짜리 좌표) 하여 `rule_embedding_index` 에 저장합니다. 질문도 같은 방식으로 임베딩한 뒤 **가까운 Rule 10 개**를 뽑아옵니다. 빠르지만 한계가 있습니다.

### 그런데 이 질문에 돌려봤더니…

정답 Rule (A002 → 오류메시지 조립) 이 **top-10 에 없을 수 있습니다.** 벡터는 단어 유사도 중심이라:

- **'A002' 같은 코드값을 놓칩니다** — "A002" 가 임베딩에서 특별한 의미를 갖지 않음
- **"B이면" vs "B가 아니면"** 조건의 반대를 구분 못 함
- 단어가 겹치면 **내용이 달라도** 상위로 올림

---

## 4. 그럼 어떻게 해결했냐 — **DeepAgent 시맨틱 검색**

똑똑한 검색 전문가 한 명을 붙여뒀다고 생각하시면 됩니다. 이 전문가는 **LLM** 이고, 5 개의 도구를 씁니다.

| 도구 | 하는 일 |
|------|---------|
| `rule_vector_search(query, top_k)` | 의미로 Rule 검색 (위 벡터 검색) |
| `rule_keyword_search(keyword)` | Rule statement / Example 텍스트에서 **정확한 문자열** 검색 — 'A002' 같은 코드값 |
| `get_rule_detail(rule_hash)` | Rule 의 statement + **모든 Example(Given/When/Then)** + 연결 FUNCTION 전문 |
| `get_function_context(function_id)` | 함수 맥락 (summary, 테이블, 같은 함수의 다른 Rule 목록) |
| `select_relevant_rules(results_json)` | 최종 답 제출 |

그리고 전문가에게 **5 단계 수사 매뉴얼**을 줬습니다 (`bl_semantic_search.yaml` 의 system_prompt).

> 1. 질문을 분석하고 HyDE 가설을 세워라
> 2. 여러 방식으로 넓게 훑어라 (vector + HyDE + keyword 병렬)
> 3. 유력 후보의 Example 전문을 다시 읽어라 (get_rule_detail)
> 4. 부족하면 다른 각도로 재시도해라 (Step-back / Decompose)
> 5. 확실한 것만 제출해라

LLM 은 매뉴얼을 따라가면서, **어떤 도구를 언제 쓸지는 스스로 결정**합니다.

---

## 5. 🔍 전문가의 수사 과정

### 🧠 1단계. 질문 분석 + HyDE 가설

```
┌ 핵심 동작 : 오류 메시지 조립
├ 대상     : SKB 은행 / 결과코드별 메시지
├ 조건     : 결과코드 = 'A002'   ← 키워드 검색 필수
└ HyDE 가설 : "결과코드가 'A002'이면 해당 코드에 매핑된 오류 메시지를 조립한다"
```

### 🔎 2단계. 다중 전략 검색 (병렬)

```
[1] rule_vector_search(원본 질의) → b000 계열 후보 반환 (정답 없음)
[2] rule_vector_search(HyDE 가설) → 역시 b000 위주      ← 🎯 HyDE
[3] rule_keyword_search("A002") → 🎉 정답 Rule 발견    ← 🎯 키워드
```

### 🎯 핵심 기법 1 — **HyDE (Hypothetical Document Embeddings)**

질문은 짧습니다. 그런데 정답 Rule.statement 는 구체 서술형입니다. 이 격차 때문에 벡터 검색이 놓칠 수 있습니다. LLM 이 **"정답 Rule 문장은 이럴 것이다"** 가설을 생성하고 그걸로 다시 벡터 검색합니다.

### 🎯 핵심 기법 2 — **키워드 검색 (벡터의 사각지대 보완)**

'A002' 같은 코드값은 벡터가 놓치기 쉽습니다. 키워드 검색은 Rule.statement 또는 Example.given/when/then 에서 **통째 문자열 매칭** 으로 이를 보완합니다.

### 📖 3단계. 유력 후보 Example 전문 읽기

```
[4] get_rule_detail(정답_rule_hash)
    → statement: "결과코드 'A002' 이면 오류메시지 'X' 조립"
    → Example: [
        {given: "rltm_auth_rslt_cd='A002'", when: "메시지 조립", then: "오류메시지='...'"},
        {given: "rltm_auth_rslt_cd='0000'", when: "메시지 조립", then: "성공 메시지"},
      ]
[5] get_rule_detail(b000_후보) → 다른 Rule 검증
...
```

각 후보에 대해 전문가가 **3 요소 매칭** 을 엄격히 적용:

```
후보 A : 동작(메시지 조립) ✅  대상(SKB 결과코드) ✅  조건(A002) ✅  → EXACT
후보 B : 동작 ✅              대상 ✅                조건 ❌         → 제외
```

### 🎯 핵심 기법 3 — **후보 검증 (미리보기 금지)**

미리보기(80자)만 보고 "비슷해 보인다" 로 판단하면 노이즈가 들어옵니다. get_rule_detail 로 **모든 Example 전문** 을 읽고 Rule statement + Example 3 요소 매칭을 해야 정확합니다.

### 🌐 4단계. 함수 맥락 확장

```
[6] get_function_context(정답_function_id)
    → summary: "SKB 실시간 인증 결과별 오류메시지 조립 전담"   ✅
    → tables: [...]
    → rules:  [R1(...), R2(A002 오류), R3(...)]  ← 같은 함수의 다른 Rule 도 파악
```

### 🎯 핵심 기법 4 — **함수 맥락 확장**

Rule 하나만 보면 같은 단어가 다른 도메인에서 쓰여 속을 수 있습니다. **Rule 이 속한 함수의 summary, 테이블 접근, 같은 함수의 다른 Rule** 까지 보면 도메인이 명확해집니다. Neo4j 그래프 DB 이기에 가능한 단계.

### ✅ 5단계. 최종 제출

```
[7] select_relevant_rules([
  { rule_hash: "…", function_id: "…", local_id: "R2", confidence: "EXACT",
    reason: "Rule.statement 가 'A002' 조건과 메시지 조립을 모두 포함, Example.given 일치" }
])
```

---

## 6. 지금까지 쓴 기법 한눈에

| 단계 | 기법 | 하는 일 |
|---|---|---|
| 1 | **HyDE** | 질문을 정답 Rule statement 모양으로 바꿔 벡터 검색 |
| 2 | **키워드 검색** | 벡터가 놓치는 코드값('A002')을 통째 매칭 |
| 2 | **다중 전략** | vector + HyDE + keyword 를 같은 턴에 병렬 실행 |
| 3 | **후보 검증** | 미리보기 금지, Example 전문 읽고 3 요소 매칭 |
| 4 | **함수 맥락 확장** | Rule 이 속한 함수 summary / 테이블 / 동료 Rule 까지 확인 |
| 전체 | **에이전트 자율** | LLM 이 도구 호출을 스스로 설계 |
| 전체 | **Step-back (조건부)** | 결과 부족 시만 질문 일반화 |

---

## 7. Example Mapping 이 주는 추가 이점

Rule/Example/Question 분리 구조가 검색 정확도에 기여하는 방식:

1. **Rule 정규화**: 같은 규칙이 여러 함수에 재사용돼도 Rule 노드 1 개만 저장 → 검색 시 중복 제거 자동화, `rule_hash` 로 동일 Rule 확정
2. **Example 경계값**: Rule 당 Example 이 최소 2 개(경계값 포함) 있어 벡터 임베딩 텍스트가 풍부 → 단순 GWT 대비 검색 정확도 ↑
3. **Question 노출**: 코드상 모호한 지점은 Question 에 명시 → "해당 규칙 확정되지 않은 케이스" 로 사용자에게 노출 가능
4. **Flow 관계**: `NEXT / BRANCH` 로 Rule 간 흐름 구조가 그래프에 명시 → "이 Rule 은 어느 순서에 속하나" 질문에도 답 가능
5. **coupled_domains**: 타 도메인과 엮인 Rule 을 쉽게 식별 → "재고 도메인에 쓰기하는 결제 함수 찾기" 식 크로스 도메인 쿼리 가능

---

## 8. 결론

> **벡터만으로는 부족하다. LLM 이 가설을 세우고, 키워드로 사각지대를 메우고, Example 전문을 읽고, 함수 맥락까지 확인해야 비로소 "정확히 맞는 Rule" 이 나온다.**

### 권장 구조

```
사용자 질문
    ↓
DeepAgent (5 개 도구 + 5 단계 매뉴얼)
  1. 분석 & HyDE 가설
  2. 다중 전략 검색 (rule_vector + rule_keyword + HyDE)
  3. 후보 검증 (get_rule_detail + Example 3 요소 매칭)
  4. (필요 시) 재시도 (Step-back / Decompose)
  5. 확정 답안만 select_relevant_rules 로 제출
    ↓
정답 Rule 1~4 개
```

---

## 부록 A. 재현 방법

```bash
cd robo-data-analyzer
python -m test.bl_search_comparison.vectorize_bl
python -m test.bl_search_comparison.run_comparison > c:/tmp/bl_search_result.log 2>&1
```

### 파일 구조

```
test/bl_search_comparison/
├── vectorize_bl.py          # Rule 노드 임베딩 + rule_embedding_index 생성
├── vector_search.py         # 방법 A: 순수 벡터 Rule 검색
├── semantic_search.py       # 방법 B: DeepAgent (5 개 도구 정의)
├── bl_semantic_search.yaml  # 시스템 프롬프트 (5 단계 매뉴얼)
└── run_comparison.py        # 양쪽 나란히 실행
```

## 부록 B. Neo4j 확인 쿼리

```cypher
// Rule 노드 현황
MATCH (r:Rule) RETURN count(r) AS total_rules

// Example 경계값 비율
MATCH (e:Example)
RETURN count(e) AS total, sum(CASE WHEN e.is_boundary THEN 1 ELSE 0 END) AS boundary

// 커플링된 Rule
MATCH (f:FUNCTION)-[rel:HAS_RULE]->(r:Rule)
WHERE size(rel.coupled_domains) > 0
RETURN f.fqn, r.statement, rel.coupled_domains LIMIT 20

// 미해결 질문
MATCH (f:FUNCTION)-[:HAS_QUESTION]->(q:Question) RETURN f.fqn, q.text LIMIT 20
```
