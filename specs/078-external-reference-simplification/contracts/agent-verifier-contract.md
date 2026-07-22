# 078 Agent(unknown text) — 제안·검증 계약

## 역할 분리

- **Agent(LLM)**: unknown text 파일에 대해 (a) 청크 경계 span 목록 또는 (b) regex/
  state-machine skill을 제안한다. 청크 텍스트·key·관계를 직접 생성하지 않는다.
- **Deterministic verifier(코드)**: 제안을 원문에 적용해 다음을 전부 검사한다.
  1. slice-exact: 각 span에 대해 `slice == decoded_source[start:end]`
  2. no-overlap: span 상호 무중첩·정렬 가능
  3. bounds: 파일 범위 내, 빈 span 금지
  4. **held-out**: 같은 형식으로 분류된 파일 중 제안에 사용하지 않은 파일에서
     같은 skill이 불변식을 유지 (held-out 0건이면 승격 불가 — 그 파일들은
     `unanalyzed` 유지)
  5. negative: 다른 형식 decoy 샘플에서 매치 폭주(파일 전체 삼킴·0폭 매치 등) 없음
- 전부 통과 시에만 span을 청크로 확정. 하나라도 실패하면 청크 0개 +
  `unanalyzed`/사유 (fail-closed). LLM 후보 간 합의는 후보 선별 신호일 뿐 승인
  근거가 아니다.

## 기존 자산 재사용

- `agents/external.py`·`step4_scan_project/external_skills.py`의 안전장치(unsafe regex
  차단·timeout·크기 제한·skill 저장/재검증)는 유지하되, 산출·검증 계약을 위 정의로
  교체한다. `negative_samples=[]` 호출 금지 — 실제 decoy를 전달한다.
- 저장된 skill도 현재 파일 집합에서 위 verifier를 다시 통과해야 사용된다.
