# 핵심 트러블슈팅 — Deep Agent Hang 이슈

> **2026-04-15** · 다른 사람도 같은 함정에 빠지지 않도록 사실 기반으로 정리

---

## 증상

특정 프로젝트(예: ukesa)에서 분석을 시도하면 **무한 대기**.
- 다른 5 개 프로젝트는 평균 15~25 초에 정상 종료
- 한 프로젝트만 5 분, 10 분 지나도 끝나지 않음
- 프로세스는 살아있고 (Python 프로세스 메모리 점유), 에러 로그도 없음
- LLM POST 호출 1~3 회 후 갑자기 새 호출이 안 일어남

---

## 진단 절차

### 1단계 — 컴포넌트별로 분리 측정

| 호출 단위 | 결과 |
|---|---|
| `collect()` 단독 | **0.17 초** ✓ 정상 |
| LLM 직접 호출 (3KB 입력) | **7.7 초** ✓ 정상 응답 |
| LLM 직접 호출 (전체 6.8KB 입력) | **11.7 초** ✓ 정상 응답 |
| Deep Agent 통과 (같은 입력) | **90 초+** ✗ Hang |

→ **LLM·API 자체는 멀쩡, Deep Agent 거치는 순간 hang.**

### 2단계 — LLM 응답 형식 관찰

LLM 직접 호출 시 응답 본문 (ukesa 케이스):

```
content=[{
  'type': 'text',
  'text': 'classify_file("kee-config/.../Batch_Ett051.sqlmap.xml", "SQL")\n
           classify_file("kee-config/.../ReferenceInformationDC_IDCReferenceInformation.sqlmap.xml", "SQL")'
}]
```

**LLM 이 `classify_file(...)` 을 평범한 텍스트 본문에 적어서 반환.**
즉 도구 호출 (tool_call) API 응답이 아니라 자연어 텍스트.

### 3단계 — Deep Agent 의 행동

Deep Agent 는 LLM 응답에서 **`tool_call` 타입의 출력만** 도구 실행으로 인식.
LLM 이 텍스트로 도구 호출을 "묘사" 하면:
- Deep Agent 는 도구 실행 안 함
- 다음 LLM 호출도 트리거 안 함
- 그냥 **무한 대기** 상태

---

## 근본 원인

**OpenAI Responses API (gpt-5.4 + reasoning_effort=none) 가 일부 입력에서 도구 호출 형식 대신 자연어 텍스트로 응답하는 경우 발생.**

### 발생 조건 (관찰)

- 사내·비공개 프레임워크 이름 (LLM 학습 지식이 약한 영역)
- 한국어가 다수 포함된 컬럼명·주석
- 비교적 큰 (수백 KB) 매퍼 XML 의 첫 몇 줄 미리보기
- 위 조건들 중 둘 이상 겹치면 트리거 확률 상승

다른 5 개 프로젝트 (Spring/EJB/OFBiz/ProFrame/Python) 에서는 발생 안 함 — LLM 학습 데이터가 풍부해 정상적으로 tool_call 응답.

---

## 해결책 (검증됨)

### A. SYSTEM_PROMPT 에 응답 형식 강제

[`agent.py`](agent.py) 의 `SYSTEM_PROMPT` 첫 머리에:

```
**중요 — 응답 형식**:
모든 행동은 반드시 tool_call (도구 호출 API) 로만 한다.
도구 이름·인자를 본문 텍스트로 적지 말 것 (예: 텍스트로 classify_file(...) 작성 금지).
설명·서술도 도구 호출의 인자 안에서만. 도구 외 텍스트 응답 금지.
```

### B. 입력 크기 절약 (보조)

[`collector.py`](collector.py) 의 `_first_meaningful_lines` 기본 라인 수를 8 → 4 로 축소.
큰 매퍼 파일 미리보기가 작아져 LLM 의 응답 모드 트리거 확률 감소.

---

## 검증 결과

| 변수 | 수정 전 | 수정 후 |
|---|---|---|
| ukesa | **무한 hang (∞)** | **29.5 초** ✓ |
| 다른 5 개 | 15~25 초 정상 | 15~25 초 (영향 없음) |

수정 후 모든 프로젝트가 안정적으로 종료. ukesa 는 sqlmap.xml 2 개를 모두 `SQL` 카테고리로 정확 분류.

---

## 만약 다시 비슷한 증상이 보이면

1. **로그 확인**: LLM POST 호출 횟수 vs 시간 — 갑자기 멈췄으면 본 이슈 의심
2. **프로세스 살아있는데 로그 없음** = 응답 대기 중 = 본 이슈 가능성 큼
3. **수동 진단**: `client/llm_client.py` 의 `get_llm()` 으로 직접 호출해 LLM 응답을 살펴봄
   - 응답에 `'type': 'text'` 안에 도구 함수명이 적혀있으면 본 이슈 확정
   - 정상이면 `tool_call` 타입 출력이 들어있어야 함
4. SYSTEM_PROMPT 강화 (위 A) + 입력 크기 점검 (위 B)

---

## 교훈

- **LLM 의 응답 형식이 항상 일관되지 않는다** — 같은 도구 세팅이라도 입력에 따라 텍스트로 답할 수 있다
- **Deep Agent 류 프레임워크는 tool_call 만 인식** — 텍스트 응답이면 hang
- **명시적 프롬프트 강제** 가 가장 안전한 방어선
- **컴포넌트별 분리 진단** (collect / direct LLM / agent 통과) 이 원인 추적의 정도(正道)

---

## 관련 코드

- [agent.py](agent.py) — SYSTEM_PROMPT 의 "응답 형식" 블록
- [collector.py](collector.py) — `_first_meaningful_lines(n=4)`
- [analysis_context.txt](prompts/analysis_context.txt) — 사용자 메시지 템플릿
