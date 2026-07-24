# 099 — rules 콜 실패 시 조각 복구 (데이터 무손실 가드레일)

작성: 2026-07-24. spec 054(품질 이상 시 조각 폴백)의 **실패 경로**를 통합한다.

## 1. 무엇이 문제였나 — 실측

`code_rules_examples` 콜이 길이 초과 폭주로 실패하면(`LengthFinishReasonError` /
`finish_reason=length`), 그 노드의 **rules 를 통째로 잃는다.** 콜드런 실측:

- 683.6초 run: 이런 실패 **5건**. 각 노드의 rules·examples 가 빈 값으로 남았다.
- 650.9초 run: 0건. 스트래글러 유무가 최악/최고를 가른다.

현재 코드(`three_split_llm_fanout`):
```
rules_out = _take(done, "rules", code_id)   # 실패면 None (로그만, rules 손실)
if rules_out is not None:                    # ← 실패(None)면 이 블록을 건너뜀
    if quality.is_anomaly:
        rules_out = run_rules_fallback(...)  # 복구는 "성공했으나 이상"일 때만
```

즉 spec 054 의 조각 복구는 **"파싱은 됐으나 품질 이상"** 경로에만 걸려 있고, **길이 초과로
콜 자체가 실패한 경로에는 복구가 없다.** 이건 "조용한 실패 금지·무손실"(헌법 IV) 가드레일이
뚫린 상태다 — 목표의 "데이터를 잘라 속도를 얻지 마라" 제약을 **현재 코드가 위반**하는 셈이다.

**성능이 아니라 데이터 문제다.** 폭주 콜은 실패 *전에* 이미 시간을 쓰므로 복구는 시간을
줄이지 않는다(오히려 조각 콜만큼 미세 증가). 이 spec 은 **데이터 무결성** 을 고친다.

### 왜 조기 차단(시간 이득)은 못 하나 — 04 §6 실측

폭주를 정상 대형 출력과 **일반 신호로** 구별할 수 없다: 성공 콜 431개 출력토큰
p50=378·p99=2,552·max=3,890, 규칙 65~100% 고유(발산 아님). 스트래글러는 4,704~8,715.
정상 출력이 p50 의 10배까지 뻗어 분포가 겹친다 — 어떤 데이터 유도 임계도 정상 대형 콜을
자르거나 폭주를 놓친다. 고정 토큰 임계는 코퍼스 튜닝(spec 095 원복 사유). **조기 차단 불가,
사후 복구만 가능.**

## 2. 근본해결 — 두 실패 모드를 하나로 통합

"성공했으나 이상"과 "콜 실패"는 둘 다 **"전체 분석이 신뢰할 rules 를 못 냈다"** 는 같은
사건이다. 특수 분기를 더하지 않고 **같은 조각 메커니즘**으로 처리한다:

```
rules_out = _take(...)                       # 실패면 None
rules_call_failed = isinstance(done["rules"], BaseException)
quality = check_rule_quality(rules_out, ...) if rules_out is not None else None
if (quality and quality.is_anomaly) or rules_call_failed:   # 이상 OR 실패
    outcome = run_rules_fallback(rules_out=rules_out, report=quality, ...)  # None 허용
    rules_out = outcome.rules_out
```

`run_rules_fallback` 이 `rules_out=None`(복구할 최초 결과 없음)을 받으면:
- **조각이 있으면**: 조각 분석 → 병합으로 rules 를 **새로 복구**(길이 초과는 조각마다 작아
  폭주하지 않는다).
- **조각이 없거나(평면 함수) 전부 실패**: 수렴할 최초 결과가 없으므로 **빈 rules + 미복구
  플래그**(`rules_quality_anomaly`)로 정직하게 표면화. 크래시·조용한 손실 금지.

정상(성공·품질 정상) 경로는 폴백을 **안 탄다** — 성능·캐시 불변(spec 054 FR-016 유지).

## 3. 산출물 영향

- **결정론 6종**(4,484·439·70·15·651·7,209): 전부 그래프 구조 — rules 복구와 무관. **불변.**
- **RULE/EXAMPLE 수**: 종전엔 스트래글러 노드의 rules 가 **0**이었다(손실). 복구되면 그 노드의
  rules 가 채워져 **RULE/EXAMPLE 수가 늘 수 있다** — 이건 손실을 되돌린 것이지 데이터를
  더한 게 아니다. 스트래글러 없는 run(650.9)에서는 발동 자체가 없어 완전 동일.
- `partial` 마크: rules 를 복구했으면(내용 있음) partial 아님. 미복구면 partial 유지 + 플래그.

## 4. 검증

- [x] 단위 13/13: 복구 성공(조각→병합), 평면 함수 미복구 플래그(크래시 없음), 조각 전부 실패
      미복구, 기존 이상 경로 4종 회귀. 전수 **404 OK**(skip 5).
- [x] **콜드 검증 (실전 복구 확인됨)**: 콜드런에서 `code:apascrtu0010t02.c:c002_get_rcv_chnl_org`
      의 rules 콜이 실제로 실패했다(`RepetitionLoopAborted` period=382, spec 080 이 3,848자에서
      차단). 새 경로가 발동: `detected:call_failed` → 조각 분석 → 병합 → **rule 1개 복구**.
      **종전 코드였다면 이 노드 rules = 0(손실).** 결정론 6종 **정확 일치**
      (4,484·439·70·15·651·7,209), 완주 416/416, 미복구 플래그 노드 0.
- **관측**: 이 run 은 737.3초로 표본 중 높은 편이었다 — 폭주 발생 + 복구(조각 콜 ~50초)가
      겹친 run 이라 예상된 결과다(§5: 복구는 데이터를 되찾되 시간을 미세 증가시킨다). 폭주가
      없는 run 은 발동 자체가 없어 650초대다.

## 5. 범위 밖

시간 단축은 이 spec 이 아니다(04 §6 — 조기 차단 불가로 실측). 시간은 서버 권한(MTP, 04 §1)
필요.
