# Implementation Plan: DRY·단일진실 복원 (046)

**Spec**: [spec.md](./spec.md) · **Created**: 2026-07-13

## Constitution Check

| 원칙 | 관계 | 판정 |
|---|---|---|
| **II. Zero-Branch Strategy** | `analyze_code_step` 의 `== "dbms"` 제거 → `Strategy` DI | ✅ **복원** |
| **III. Single Source of Truth** | 확장자·루틴라벨·비용추적·저장쿼리·슬라이싱·공통필드를 **각각 한 곳으로** | ✅ **복원** |
| **I. Determinism-First** | LLM 추가 0 | ✅ 무영향 |
| **V. KV-Cache** | 프롬프트 **불변** — 프롬프트 파일 손대지 않는다 (byte-identical CI 유지) | ✅ 무영향 |
| **VIII. Cohesion Exception** | `COMPONENT` 합본은 **그대로 둔다** (헌법이 승인한 예외) | ✅ 존중 |

**핵심 안전장치**: FR-012(동작 불변). 리팩터 전후 그래프·산출물이 같아야 한다.

## Cross-Service Contract Impact

**contract 변경 0건.** 라벨·엣지·속성·HTTP 계약 전부 불변. 내부 구조만 바뀐다.
→ 다른 서비스(architect / catalog / frontend) **영향 없음**.

## 구현 순서 (위험 낮은 것 → 높은 것)

각 단계마다 **컴파일 + 로드 + 동작 검증**을 하고 넘어간다.

| # | 항목 | FR | 위험 |
|---|---|---|---|
| 1 | 죽은 코드 삭제 (참조 0 확인분) | FR-010 | 낮음 |
| 2 | 루틴 라벨 단일화 | FR-005 | 낮음 |
| 3 | 확장자 단일화 + 미지원 시 명시 실패 | FR-003 | 낮음 |
| 4 | LLM 예외 분류 헬퍼 (9곳 → 1곳) | FR-011 | 중간 |
| 5 | 전략 DI (`== "dbms"` 제거) | FR-001, FR-002 | 중간 |
| 6 | AST 리더 공통 필드 (5벌 → 1벌) | FR-009 | 중간 |
| 7 | Neo4j 저장 통합 (32 → 1 + 예외) | FR-004 | **높음** |
| 8 | 소스 캐시 run 스코프 + `code_text` 재사용 | FR-007, FR-008 | 중간 |
| 9 | 검색 LLM 비용추적 편입 | FR-006 | 중간 |

## 검증 전략

- **각 단계**: `py_compile` 전수 + 실제 모듈 로드 + 해당 로직 단위 실행
- **최종 (SC-001 동작 불변)**: 045+046 통합 후 실제 분석 1회 → 그래프 라벨별 노드 수·산출물을 **리팩터 전 스냅샷과 대조**
- **SC-002~SC-009**: grep 카운트 / 로그 / 사용량 기록으로 측정
