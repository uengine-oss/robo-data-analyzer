"""격리 실험용 미니 프롬프트 — 분석 파이프라인의 system 비슷한 8K 토큰 패턴.

본체 yaml (`prompts/framework/_common.yaml` 등) 과 격리. 실험 결과가 본체와 무관해야
원인 영역 정확히 좁힘 (분석 본체 변경의 회귀가 아니라 LLM·SGLang 자체 동작 검증).

토큰 크기 조절:
- LARGE_INPUT  ≈ 8000 토큰 (현재 hang 재현 크기)
- SMALL_INPUT  ≈ 1000 토큰 (시나리오 E 입력 크기 영향 검증)
"""
from __future__ import annotations


SYSTEM_PROMPT = """\
당신은 비즈니스 코드(함수) 의 의미를 추출하는 분석 에이전트입니다.
출력은 항상 Pydantic 스키마로 강제되며 자유 텍스트 응답은 불가합니다.

## 1. 입력 명세
- function_id / signature / function body — 분석 대상
- codebase_notes — 프로젝트 안내 (op 매핑 등)
- reads / writes — 결정론 단계 결과 (추가·제거 X)

## 2. 분석 원칙
1. 코드 토큰 그대로 박지 마라 — 의미로 풀어쓰기
2. 추측 금지 — 본문 코드에 직접 있는 것만
3. R/W 추론 금지 — 주어진 목록만 사용
4. 모든 종료 경로 빠짐 없이 반영 (silent pass 분기 포함)
5. 모호 단어 박멸 ("적절히 / 필요시 / 상황에 따라" 등 금지)

## 3. 응답 형식 강제
response_format Pydantic schema 강제 — 자유 텍스트 응답 불가.
이 분할이 출력하지 않는 필드는 응답에 포함하지 않습니다.
"""


# 함수 본문 mock — 분석 패턴 재현용 (도메인 무관 generic).
def _mock_function_body(repeat: int) -> str:
    """함수 본문 mock 텍스트 — repeat 횟수로 입력 토큰 크기 조절."""
    block = """
    // 결제 승인 검증
    if (request.amount > LIMIT_MAX) {
        return Result.reject("OVER_LIMIT");
    }

    // 외부 PG 호출
    PgResponse response = pgClient.charge(request.cardNumber, request.amount);
    if (response == null) {
        log.error("PG_TIMEOUT");
        return Result.reject("TIMEOUT");
    }

    // 결과 기록
    AuthHistory history = AuthHistory.builder()
        .cardNumber(maskCard(request.cardNumber))
        .amount(request.amount)
        .responseCode(response.code)
        .approvedAt(LocalDateTime.now())
        .build();
    authHistoryRepository.save(history);

    // 분기 처리
    if ("APPROVED".equals(response.code)) {
        return Result.success(history.getId());
    } else if ("DECLINED".equals(response.code)) {
        notifyDeclined(request.userId, response.message);
        return Result.reject("DECLINED");
    } else {
        // 알 수 없는 응답 코드 - silent pass (마스터에 없는 코드는 정상 케이스)
        return Result.unknown();
    }
"""
    return block * repeat


# 약 1000 토큰 (작은 입력 — 시나리오 E)
SMALL_INPUT = f"""\
function_id: com.example.payment.PaymentService.processOrder
signature: processOrder(PaymentRequest request) -> Result

codebase_notes:
- Spring Boot 결제 도메인. PG 모듈 wrapper 사용.
- 응답 코드: 'APPROVED' = 성공 / 'DECLINED' = 거절 / 'TIMEOUT' = 시간초과

reads: ["db.payment.AUTH_HISTORY", "db.payment.CARD_MASTER"]
writes: ["db.payment.AUTH_HISTORY"]

function body:
```
{_mock_function_body(2)}
```
"""


# 약 8000 토큰 (현재 hang 재현 크기 — block 약 1000자 / 토큰당 4자 가정)
LARGE_INPUT = f"""\
function_id: com.example.payment.PaymentService.processOrder
signature: processOrder(PaymentRequest request) -> Result

codebase_notes:
- Spring Boot 결제 도메인. PG 모듈 wrapper 사용.
- 응답 코드: 'APPROVED' = 성공 / 'DECLINED' = 거절 / 'TIMEOUT' = 시간초과
- 인증 이력 = audit-write (INSERT only, 갱신·삭제 X)
- 카드사 코드 매핑 = lookup (단순 SELECT 후 분기)

reads: ["db.payment.AUTH_HISTORY", "db.payment.CARD_MASTER"]
writes: ["db.payment.AUTH_HISTORY"]

referenced_symbols:
- [상수] LIMIT_MAX (int) = 500000
- [상수] AUTH_TIMEOUT_MS (int) = 3000

callee_summaries:
- com.example.payment.PgClient.charge: 외부 PG 모듈에 결제 승인 요청 송신 후 응답 회수
- com.example.payment.CardMasker.maskCard: 카드번호 16자리 중 가운데 6자리 마스킹
- com.example.payment.AuthHistoryRepository.save: 인증 이력 단건 INSERT

function body:
```
{_mock_function_body(32)}
```
"""
