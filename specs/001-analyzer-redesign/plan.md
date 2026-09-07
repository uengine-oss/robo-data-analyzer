# 구현 계획

## 1. 사용되지 않는 의미 경로 제거

- Function Table Usage의 planner slot, 호출, prompt, schema, 임시 `extra`, 계측과 테스트를 함께 제거한다.
- 결정론적 Table·Column·READS·WRITES·operations와 Summary/Rule 입력 근거는 유지한다.
- Analyzer와 커밋된 관련 서비스에서 제거 이름과 숨은 소비자가 0인지 확인한다.

## 2. 실행 완료 상태 정합

- graph commit과 postcommit evidence 봉인을 명시적인 두 경계로 나눈다.
- receipt, performance artifact, manifest 실패를 각각 주입해 거짓 `complete`가 없음을 검증한다.
- `RunStopped`만 정상 중단으로 처리하고 실제 workflow 오류와의 경합을 반증한다.

## 3. 실행환경과 큰 Rule

- Rule response obligation 용량을 실행 model/runtime capability에서 읽는다.
- atomic row oversize는 실행 전에 좌표와 필요량을 드러내며 자르거나 재시도하지 않는다.
- runtime/test 의존성 정본과 exact lock을 만들고 seal에 lock·실제 환경 identity를 포함한다.

## 4. 코드와 문서 정리

- 소비자 없는 모듈, 설정, ontology key와 모순된 주석을 제거한다.
- 과거 spec 번호 대신 현재 invariant가 스스로 읽히게 고친다.
- publication, meaning plan, meaning fanout의 과대한 책임만 입력·출력 계약 기준으로 줄인다.

## 5. 검증과 마감

- 최대 Summary/Rule actual을 section별로 감사하고 안전한 추가 축소가 없으면 현 상태 유지로 닫는다.
- 자동 회귀와 exact-once 회계 뒤 최종 GPU artifact의 30~40개 봉인 표본을 사람이 직접 판정한다.
- 현재 SDD, Analyzer 공용 skill과 최종 보고를 실제 코드에 맞춘다.
- 보호 WIP를 제외한 검증된 파일만 commit하고, push는 명시 권한 범위에서만 수행한다.
