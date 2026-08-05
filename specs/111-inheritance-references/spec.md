# Feature Specification: 상속 조상 필드/상수 매칭 (REFERENCES/INIT_BY)

**Created**: 2026-07-30 | **Status**: Implemented (동세션)

## 무엇/왜
Java `extends`/`implements`·Python `class B(A)` 의 조상 필드/상수를 맨이름 참조로 못 잡던
공백(token_matcher 문서화된 한계) 해소. 언어차는 `_PARENT_DECL` 표(데이터)로만 — 로직 한 벌.

## 계약
- 부모 해소 = 같은 파일 동명 모듈 → import 소유자 맵 **단독** 후보만 (중의는 포기 — 과대근사 금지).
- 자기 모듈 우선 섀도잉 (`_chain_hits` — 토큰마다 첫 dict 승리). 깊이 상한 3, 순환 차단.
- 선언 라인은 SourceFileCache 에서 직접 (원문 주입 순서 무관).
- **정직한 비범위**: 선언 없는 Python 인스턴스 속성(`self.x = …`)은 Variable 노드 자체가
  없어(파서 영역) 대상 밖. 선언된 필드는 종전에도 잡혔음 — 회귀 테스트로 고정.

## 검증
tests/pipeline/step2_load_ast/test_inheritance_references.py **5 passed** — Java extends /
Python bases / 섀도잉 / 선언 필드 회귀 / 중의 포기. 전량 green.
