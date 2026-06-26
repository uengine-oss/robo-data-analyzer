# Phase 1 Data Model: 테이블 논리명

## 엔티티

### TableDescription (LLM 출력) — 신규 필드
```
db, schema_, name, description (기존)
+ logical_name: str = ""    # ★신규 — 짧은 도메인 표시명. 빈값이면 미저장
```

### TABLE 노드 — 신규 속성
```
id, db, schema, name, description, analyzed_description, table_type (기존)
+ logical_name              # ★신규 — 코드노드 logical_name과 동일 의미. 빈값 미저장(nonempty)
불변식: 논리명 ≠ 물리명, 사람이 읽을 도메인 표현. 근거 없으면 미부여.
```

### 노드 summary (기존, 문체 보강)
```
[핵심]+내용 — 업무 이벤트 중심 + 값·코드(M/Q/S/D/P 등) 보존 + 입력에 없는 내용 0(추측금지)
```

## 흐름
```
step7 table_description LLM → {logical_name, description} 산출
  → table_updates = {id(fqn), logical_name, analyzed_description}
  → table.py MERGE: logical_name SET (nonempty → 빈값 미저장)
  → frontend TableCard: logical_name 표시(물리명 병기)
code_summary 단계 → summary 업무이벤트형(값/코드 보존) — 프롬프트 예시·지침 보강분 반영
```

## 검증 규칙
- 재분석 후: 근거있는 테이블 logical_name 채움 / 빈 logical_name 저장 0 / 논리명≠물리명(샘플)
- 요약 샘플 원문대조: 값·코드 보존율 100% / 없는내용 추가 0
- 검색(별도) 재측정: 의미있는 하락 0 (회귀 모니터)
