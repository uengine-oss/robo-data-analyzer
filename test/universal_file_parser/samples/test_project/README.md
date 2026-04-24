# OrderForge

**고처리량 이커머스 주문 관리 마이크로서비스**

[![Build](https://img.shields.io/badge/build-passing-brightgreen)] [![Coverage](https://img.shields.io/badge/coverage-87%25-yellowgreen)] [![License](https://img.shields.io/badge/license-Apache%202.0-blue)]

OrderForge 는 대규모 이커머스 플랫폼의 **주문 생애주기 전반** 을 책임지는 마이크로서비스입니다. 장바구니에서 결제까지, 배송 추적에서 반품·환불까지 — 한 서비스에서 **주문과 관련된 모든 상태 전이와 부작용** 을 관리합니다.

이 저장소는 신규 합류자부터 운영 담당자까지 모두가 OrderForge 를 이해하고 활용할 수 있도록 **아키텍처 / API / 설정 / 배포 / 장애 대응 / FAQ** 를 한 파일에 담았습니다.

---

## 새로 합류한 분은 여기부터

처음이라면 **다음 순서로** 읽으세요:

1. **[Overview](#overview)** — 이 서비스가 뭘 하는지 5분 요약
2. **[Quick Start](#quick-start)** — 로컬에서 주문 한 건 만들어 보기
3. **[Architecture](#architecture)** — 내부 동작 원리
4. **[API Reference](#api-reference)** — 주요 엔드포인트 스펙
5. **[Operations Runbook](docs/operations.docx)** — 운영 담당자용 상세 가이드

처음부터 전부 읽을 필요 없습니다. 본인 역할에 맞는 섹션만 골라 읽으세요. 예를 들어:

- **백엔드 개발자**: Architecture → API Reference → Development → Testing
- **운영 담당자**: Quick Start → Configuration → Deployment → Troubleshooting
- **QA 엔지니어**: Testing → API Reference → Known Issues
- **제품 관리자**: Overview → Business Flows → FAQ

### 자주 찾는 위치 빠른 참조

| 무엇이 궁금한가 | 어디를 보세요 |
|---|---|
| 주문 상태 전이 정의 | [Architecture → Order State Machine](#order-state-machine) |
| 결제 실패 시 재시도 정책 | [Business Flows → Payment](#payment-flow) + [Configuration → Retry](#retry-policy) |
| 프로덕션 롤백 절차 | [Deployment → Rollback](#rollback-procedure) |
| 로컬에서 개발 시작 | [Quick Start](#quick-start) + [Development → Prerequisites](#prerequisites) |
| 자주 발생하는 에러 코드 | [Troubleshooting → Error Codes](#error-codes) |
| API 요청/응답 스키마 | [API Reference](#api-reference) 각 엔드포인트 |
| 메트릭 대시보드 | [Observability → Dashboards](#dashboards) |
| 카오스 테스트 시나리오 | [Testing → Chaos](#chaos-testing) |

---

## Overview

OrderForge 는 **2024년 4분기** 에 기존 모놀리식 주문 처리 로직을 마이크로서비스로 분리하면서 시작되었습니다. 도입 이전의 `legacy-order-backend` 는 예약 주문, 일반 주문, 정기 배송을 하나의 거대한 트랜잭션으로 처리했기 때문에, 특정 결제 프로바이더의 지연이 전체 서비스를 마비시키는 상황이 반복되었습니다. OrderForge 는 각 단계를 **독립된 상태 전이** 로 모델링하고, 외부 연동을 **비동기 이벤트** 로 분리해 이 문제를 해결합니다.

### 무엇을 하는가

OrderForge 는 다음 책임 영역을 담당합니다:

- **주문 생성과 검증** — 고객의 장바구니 스냅샷을 받아 재고·가격·쿠폰·멤버십 규칙을 적용하여 **불변 주문 객체(Immutable Order)** 를 생성합니다.
- **결제 오케스트레이션** — 카드/계좌이체/간편결제/포인트 등 복수 결제수단을 조합해 결제 승인과 부분 환불을 처리합니다.
- **배송 라이프사이클** — 라벨 생성부터 배송 완료까지 각 단계 이벤트를 수신하고 주문 상태를 갱신합니다.
- **반품·교환·환불** — 사후 요청을 원주문과 연결해 상태 전이를 관리하고, 재고와 정산을 롤백합니다.
- **정산과 수수료 계산** — 판매자/플랫폼/배송사의 정산 기준을 주문 단위로 산출해 정산 서비스에 전달합니다.
- **고객 알림과 상태 공개** — 주문 상태 변경 이벤트를 발행해 알림 서비스와 고객 앱이 구독할 수 있게 합니다.

### 무엇을 하지 않는가

OrderForge 는 **의도적으로** 다음 책임은 맡지 않습니다. 각 책임은 별도 서비스에 위임됩니다:

- **상품 카탈로그 관리** — `catalog-service` 가 담당. OrderForge 는 주문 시점의 상품 스냅샷만 저장.
- **재고 실시간 차감** — `inventory-service` 가 담당. OrderForge 는 "예약/확정/해제" 명령만 내림.
- **결제 게이트웨이 직접 통신** — `payment-gateway-adapter` 가 담당. OrderForge 는 논리적 결제 명령만 사용.
- **고객 알림 직접 발송** — `notification-service` 가 담당. OrderForge 는 이벤트만 발행.
- **쿠폰/프로모션 계산** — `pricing-engine` 이 담당. OrderForge 는 계산 결과만 요청.

이런 분리 덕에 각 서비스가 독립적으로 배포되고 장애가 격리됩니다.

### 핵심 수치

| 지표 | 값 | 비고 |
|---|---|---|
| 일평균 주문 처리 | 약 82만 건 | 피크 대비 약 40% |
| 피크 시간 초당 주문 | 650 건 | 블랙프라이데이 실측 |
| p50 / p99 주문 생성 지연 | 35ms / 280ms | 결제 완료까지 별개 |
| 가용성 SLO | 99.95% | 월간 21분 다운 허용 |
| 연간 처리 거래액 | 약 4200억원 | 2025 실적 |

---

## Quick Start

로컬에서 주문 한 건 만들어 보는 가장 빠른 경로입니다.

### 1. 사전 준비

```bash
# 필요한 도구
brew install just docker postgresql@15

# 저장소 클론
git clone git@github.com:example/orderforge.git
cd orderforge
```

### 2. 로컬 스택 기동

```bash
# Postgres, Redis, Kafka, Mock 결제 게이트웨이를 한 번에
just dev-up

# 상태 확인
just dev-status
```

이 명령은 `docker-compose.yml` 을 사용해 5개의 보조 서비스를 띄웁니다. 처음에는 이미지 다운로드에 2~3분 걸립니다.

### 3. 애플리케이션 시작

```bash
# 스키마 마이그레이션
just migrate

# 서버 실행 (hot-reload)
just dev
```

기본 포트는 `8080` 입니다. 다른 포트를 쓰려면 `APP_PORT` 환경변수를 바꾸세요.

### 4. 첫 주문 생성

```bash
curl -X POST http://localhost:8080/api/v1/orders \
  -H 'Content-Type: application/json' \
  -H 'X-Customer-Id: cust_demo_001' \
  -d '{
    "items": [
      {"sku": "BOOK-0001", "quantity": 1, "unit_price": 15000}
    ],
    "shipping_address": {
      "zip": "06234",
      "address_line": "서울 강남구 테헤란로 123"
    },
    "payment_method": "card_mock"
  }'
```

성공하면 `201 Created` 와 `order_id` 를 반환합니다.

### 5. 상태 조회

```bash
curl http://localhost:8080/api/v1/orders/{order_id}
```

초기에는 `state: PENDING_PAYMENT` 로 보입니다. Mock 결제 게이트웨이가 비동기로 승인 이벤트를 보내면 `PAID` → `SHIPPED` → `DELIVERED` 순으로 자동 전이됩니다.

### 6. 정리

```bash
just dev-down
```

---

## Architecture

OrderForge 는 **육각형 아키텍처(Hexagonal Architecture)** 를 따르며, 도메인 코어와 어댑터가 명확히 분리되어 있습니다. 각 외부 시스템은 포트(인터페이스) 를 통해 추상화되어 있어, 테스트에서는 in-memory 구현으로 쉽게 대체 가능합니다.

### Order State Machine

주문 하나는 **유한 상태 기계(FSM)** 로 관리됩니다. 상태 전이는 오로지 도메인 이벤트에 의해서만 일어나며, 외부에서 상태를 직접 변경하는 API 는 존재하지 않습니다.

```
    DRAFT ──submit──> PENDING_PAYMENT ──authorize──> PAID ──pick──> PICKING
                        │                              │                │
                        │ timeout                      │ refund         │ pack
                        ▼                              ▼                ▼
                     EXPIRED                        REFUNDED         PACKED
                                                                       │
                                                                       │ ship
                                                                       ▼
                                                                    SHIPPED
                                                                       │
                                                                       │ deliver
                                                                       ▼
                                                                    DELIVERED ──return──> RETURN_REQUESTED
                                                                                                 │
                                                                                                 ▼
                                                                                            RETURNED
```

**상태 불변 조건**:
- `DRAFT` → 어떤 상태로든 갈 수 있음 (submit/cancel)
- `PENDING_PAYMENT` → `PAID` 또는 `EXPIRED` 만 가능
- `PAID` 이후 모든 상태는 **취소 불가** — 반품 플로우로만 환불 가능
- `RETURNED` 는 최종 상태 (다시 배송 안 됨)

상태별 체류 시간에 대한 타임아웃은 `config/state_timeouts.yaml` 에 정의되어 있으며, 배경 워커가 주기적으로 체크합니다.

### 컴포넌트 구성

OrderForge 는 내부적으로 **5개의 바운디드 컨텍스트** 로 나뉩니다. 각 컨텍스트는 같은 프로세스 안에서 실행되지만 도메인 모델과 데이터베이스 테이블이 분리되어 있습니다.

- **Ordering** — 주문 생성, 검증, 상태 전이를 담당하는 도메인 코어. 가장 많은 비즈니스 로직이 여기 있습니다. 핵심 클래스는 `domain/order/Order.kt`, `domain/order/OrderStateMachine.kt` 이며, 테스트는 `test/order/` 디렉토리에 집중되어 있습니다.
- **Pricing Adapter** — 가격 엔진과의 통신을 담당. 주문 생성 시 실시간 할인·쿠폰·멤버십 혜택을 조회합니다. 가격 엔진이 응답하지 않으면 **자동 후퇴(fallback)** 로 기본 가격을 사용하며, 이 경우 주문에 `priced_at_fallback=true` 플래그가 남아 정산 시 추가 검증을 받습니다.
- **Payment Orchestrator** — 결제 상태 전이를 관장. 사가(Saga) 패턴으로 카드/포인트/상품권 등 복합 결제를 조율합니다. 한 결제수단이 실패하면 이미 승인된 다른 수단을 **자동으로 취소(compensation)** 합니다.
- **Fulfillment Bridge** — 배송사 이벤트를 수신해 주문 상태에 반영. 배송사마다 이벤트 포맷이 달라 각각의 어댑터(`fulfillment/cj.py`, `fulfillment/hanjin.py` 등) 가 있습니다.
- **Returns** — 반품·교환·환불 워크플로우. 원주문과 사후 요청을 연결하고 재고 롤백, 정산 취소를 트리거합니다.

### 데이터 저장

- **운영 DB**: PostgreSQL 15 클러스터 (primary + 2 replicas). 주문/결제/배송/반품 테이블은 전부 여기에 있습니다. 파티셔닝은 `created_month` 기준 월 단위입니다.
- **이벤트 로그**: Kafka (3 브로커, 각 토픽 7일 보관). 모든 상태 전이가 이벤트로 기록되며, 다운스트림 서비스가 이를 구독합니다.
- **캐시**: Redis (master + 2 slaves). 주문 조회, 진행 중 주문 잠금, 멱등키 관리에 사용합니다. TTL 은 용도별로 다릅니다 (60초 ~ 1시간).
- **분석용 미러**: BigQuery 에 6시간마다 CDC 로 미러됩니다. 운영 DB 에는 분석 쿼리가 직접 들어오지 않도록 합니다.

### 외부 연동

| 대상 | 방향 | 프로토콜 | 빈도 |
|---|---|---|---|
| 결제 게이트웨이 (4종) | Out | HTTPS REST | 주문당 1~3회 |
| 배송사 웹훅 (7개) | In | HTTPS POST | 배송당 5~10회 |
| 재고 서비스 | Out | gRPC | 주문당 2회 (예약+확정) |
| 알림 서비스 | Out (pub/sub) | Kafka | 상태 전이당 1회 |
| 정산 서비스 | Out (pub/sub) | Kafka | 주문당 1회 (완료 시) |
| 카탈로그 서비스 | Out | HTTPS REST | 주문당 1회 (스냅샷) |
| 사기 탐지 엔진 | Out | gRPC | 고액 주문만 |

---

## Business Flows

실제로 가장 많이 일어나는 **세 가지 비즈니스 플로우** 를 자세히 설명합니다. 코드 읽기 전에 이 플로우를 머리에 그려두면 대부분의 의사결정이 이해됩니다.

### Order Placement Flow

고객이 **장바구니에서 결제 버튼을 누르는 순간** 부터 주문이 **PENDING_PAYMENT** 상태로 도달할 때까지의 흐름입니다.

1. `POST /api/v1/orders` 요청이 들어오면 **멱등키(Idempotency-Key)** 를 먼저 검사합니다. 같은 키로 이미 주문이 있으면 그 주문을 반환합니다.
2. 장바구니 스냅샷에 대해 **재고 예약** 을 재고 서비스에 요청합니다. 예약 성공 시에만 다음 단계로 진행합니다.
3. **가격 엔진** 에 최종 가격 계산을 요청합니다. 할인, 쿠폰, 포인트 사용, 배송비가 모두 여기서 결정됩니다.
4. 주문을 `DRAFT` 상태로 생성하고, 즉시 `submit` 이벤트를 발행해 `PENDING_PAYMENT` 상태로 전이합니다.
5. 결제 오케스트레이터에게 결제 요청을 비동기로 전달하고, 클라이언트에는 주문 ID 를 반환합니다.

이 플로우의 **제약 조건**:
- 전체 처리 시간이 500ms 를 넘으면 타임아웃으로 자동 롤백 (재고 해제 포함)
- 동일 고객이 **5초 안에 중복 요청** 을 보내면 두 번째 요청은 멱등 처리됨
- 장바구니가 비어 있거나 모든 상품이 품절이면 즉시 `400 Bad Request`

### Payment Flow

`PENDING_PAYMENT` 상태의 주문이 **결제 승인 또는 만료** 에 도달하는 흐름입니다. Payment Orchestrator 가 주도합니다.

주문의 결제수단 구성에 따라 세 가지 변형이 있습니다:

- **단일 결제수단**: 카드 하나만으로 결제. 게이트웨이에 승인 요청을 보내고, 성공 시 `PAID` 전이. 실패 시 재시도 정책에 따라 최대 3회 재시도.
- **포인트 + 카드 조합**: 먼저 포인트를 차감 예약하고, 부족분을 카드로 결제. 카드 승인이 실패하면 포인트 예약을 **자동 롤백** 합니다.
- **다중 카드 분할**: 두 카드로 금액을 나눠 승인. 두 번째 카드가 실패하면 첫 번째 카드 **자동 취소(승인 취소 API)**.

모든 결제는 `payment_attempt` 테이블에 기록됩니다. 재시도는 **지수 백오프(exponential backoff)** 로 간격이 늘어나며, 세 번째 시도까지 실패하면 주문은 `EXPIRED` 로 전이됩니다. 이 때 이미 예약된 재고와 차감된 포인트가 **전부 복원** 됩니다.

### Return Flow

배송 완료(`DELIVERED`) 후 고객이 반품을 요청하면 이 플로우가 시작됩니다. 반품은 `order_id` 에 대해 하위 엔티티 `return_request` 를 만드는 방식이며, 원주문은 그대로 보존됩니다.

1. `POST /api/v1/orders/{id}/returns` 로 반품 요청 접수
2. 반품 사유가 **고객 변심** 이면 배송비를 고객 부담으로, **상품 하자** 면 판매자 부담으로 기본값 설정
3. 물류사에 픽업 오더를 생성하고 반품 라벨을 발급
4. 픽업 완료 이벤트가 도착하면 `RETURN_REQUESTED` → `RETURNED` 전이
5. 정산 서비스에 **정산 취소** 이벤트 발행, 재고 서비스에 **재고 복구** 명령 발행
6. 결제 오케스트레이터가 원결제수단으로 환불 처리 (부분 환불 가능)

**주의사항**:
- 같은 주문에 대해 **반품 요청은 최대 3건** 까지 가능 (분할 반품)
- 배송 후 **30일이 지나면** 반품 요청이 자동 거부됨
- 교환은 내부적으로 "반품 + 신규 주문" 으로 처리되며, 두 주문이 `exchange_pair_id` 로 연결됨

---

## API Reference

OrderForge 의 모든 API 는 **JSON over HTTPS** 이며, 인증은 **Bearer Token** 을 사용합니다. 토큰은 인증 서비스에서 발급받아야 하며, 이 서비스에서 직접 발급하지 않습니다.

### 공통 규약

- 모든 요청은 `X-Request-Id` 헤더를 권장 (디버깅용)
- 변경성 요청은 `Idempotency-Key` 헤더 필수 (생성/환불/취소)
- 모든 타임스탬프는 **ISO 8601 UTC**
- 금액은 **정수 (원 단위)** 로만 주고받음 (소수점 없음)
- 오류 응답은 `{error: {code, message, details}}` 포맷

### POST /api/v1/orders

새 주문 생성.

**요청**:
```json
{
  "customer_id": "cust_ABC123",
  "items": [
    {"sku": "BOOK-0001", "quantity": 2, "unit_price": 15000},
    {"sku": "PEN-0042",  "quantity": 5, "unit_price":  1200}
  ],
  "shipping_address": {
    "zip": "06234",
    "address_line": "서울 강남구 테헤란로 123",
    "recipient": "홍길동",
    "phone": "010-1234-5678"
  },
  "payment_methods": [
    {"type": "card", "token": "pm_tok_XYZ"},
    {"type": "point", "amount": 5000}
  ],
  "coupon_codes": ["WELCOME10"],
  "delivery_notes": "문 앞에 놓아주세요"
}
```

**응답**:
```json
{
  "order_id": "ord_01HF8E3...",
  "state": "PENDING_PAYMENT",
  "total_amount": 41000,
  "created_at": "2026-04-20T10:15:32Z",
  "expires_at": "2026-04-20T10:45:32Z"
}
```

**오류 코드**:
- `400 BAD_REQUEST` — 스키마 오류
- `402 PAYMENT_REQUIRED` — 결제수단 유효성 실패
- `409 CONFLICT` — 재고 부족
- `422 UNPROCESSABLE_ENTITY` — 쿠폰 규칙 위반
- `429 TOO_MANY_REQUESTS` — 분당 요청 한도 초과

### GET /api/v1/orders/{order_id}

주문 상세 조회.

응답에는 주문 본체, 결제 이력, 배송 이력, 반품 이력이 모두 포함됩니다. 조회 권한은 **주문자 본인 또는 내부 서비스 토큰** 만 허용됩니다.

### GET /api/v1/orders

주문 목록 조회. 쿼리 파라미터로 필터링.

- `customer_id`: 특정 고객의 주문만
- `state`: 특정 상태의 주문만 (여러 개 가능, 쉼표 구분)
- `created_after` / `created_before`: 기간 필터
- `limit`: 기본 20, 최대 100
- `cursor`: 페이지네이션 커서 (응답의 `next_cursor` 사용)

### POST /api/v1/orders/{order_id}/cancel

주문 취소. `PENDING_PAYMENT` 상태에서만 가능. 이미 `PAID` 이면 반품 플로우로 가야 합니다.

### POST /api/v1/orders/{order_id}/returns

반품 요청. 요청 본문에 반품 항목과 사유를 명시합니다. 부분 반품 지원.

### POST /api/v1/orders/{order_id}/exchanges

교환 요청. 내부적으로 반품 + 신규 주문으로 처리되며, 두 주문은 `exchange_pair_id` 로 연결됩니다.

### 내부 전용 API (Internal)

다음 엔드포인트는 **내부 서비스 토큰** 만으로 호출 가능하며 외부에 노출되지 않습니다:

- `POST /internal/v1/orders/{id}/force-state` — 운영 인시던트 시 상태 강제 조정
- `POST /internal/v1/orders/{id}/replay-events` — 이벤트 재처리 (누락된 알림 복구)
- `GET /internal/v1/orders/{id}/audit` — 상태 변경 감사 로그 조회
- `POST /internal/v1/settlements/rebuild` — 정산 재계산 (회계 보정용)

---

## Configuration

설정은 **세 단계** 로 분리되어 있습니다. 각 단계는 명확한 책임이 있으며, 서로 덮어쓰는 관계입니다.

### Layer 1 — 기본값 (내장)

`config/defaults.yaml` 에 전 설정의 기본값이 있습니다. 이 파일은 **코드와 함께 빌드** 되므로 배포 후 변경 불가. 로컬 개발 시 모든 키의 기본값을 확인하려면 이 파일을 참조하세요.

### Layer 2 — 환경별 오버라이드 (파일)

각 환경별로 `config/app.{env}.yaml` 이 있습니다. `env` 는 `local`, `dev`, `stage`, `prod` 중 하나입니다. 여기에 있는 키는 defaults 를 덮어씁니다.

예: `config/app.prod.yaml`
```yaml
server:
  port: 8080
  worker_threads: 32

database:
  host: orderforge-primary.internal
  pool_size: 40
  connect_timeout_ms: 2000

kafka:
  brokers:
    - kafka-1.internal:9092
    - kafka-2.internal:9092
    - kafka-3.internal:9092
  producer_acks: all
```

### Layer 3 — 런타임 환경변수 (최우선)

`ORDERFORGE_` 접두사가 붙은 환경변수는 YAML 설정을 덮어씁니다. 대문자와 이중 언더스코어로 계층을 표현합니다.

예:
- `ORDERFORGE_SERVER__PORT=9000` → `server.port` 덮어씀
- `ORDERFORGE_DATABASE__PASSWORD=s3cret` → `database.password` 덮어씀

### 주요 설정 키

| 키 | 타입 | 기본값 | 설명 |
|---|---|---|---|
| `server.port` | int | 8080 | HTTP 서버 포트 |
| `server.worker_threads` | int | 16 | 동시 요청 처리 스레드 |
| `database.host` | string | localhost | Postgres 호스트 |
| `database.port` | int | 5432 | Postgres 포트 |
| `database.name` | string | orderforge | 데이터베이스명 |
| `database.user` | string | orderforge_app | 사용자명 |
| `database.password` | string | — | 필수, 환경변수에서만 |
| `database.pool_size` | int | 20 | 커넥션 풀 크기 |
| `redis.url` | string | redis://localhost:6379 | Redis 접속 URL |
| `kafka.brokers` | list | [localhost:9092] | 브로커 주소 목록 |
| `payment.timeout_ms` | int | 8000 | 결제 게이트웨이 타임아웃 |
| `payment.max_retries` | int | 3 | 결제 재시도 횟수 |
| `order.expire_minutes` | int | 30 | 미결제 주문 만료 시간 |
| `return.max_days` | int | 30 | 반품 가능 기한 |
| `feature.fraud_check` | bool | true | 사기 탐지 활성화 |
| `feature.inventory_reserve` | bool | true | 재고 선예약 활성화 |

### Retry Policy

외부 서비스 호출은 공통 재시도 정책을 따릅니다:

```yaml
retry:
  max_attempts: 3
  initial_interval_ms: 200
  backoff_multiplier: 2.0
  max_interval_ms: 5000
  retryable_errors:
    - TIMEOUT
    - CONNECTION_REFUSED
    - SERVICE_UNAVAILABLE
  non_retryable_errors:
    - BAD_REQUEST
    - UNAUTHORIZED
    - VALIDATION_FAILED
```

결제 서비스만 **별도 정책** 을 씁니다 (`payment.max_retries=3` + `payment.idempotency_window_min=10`). 결제는 중복 방지가 중요하므로 재시도 전에 멱등키 일치 여부를 반드시 확인합니다.

### Feature Flags

런타임에 토글 가능한 플래그는 `feature.*` 하위에 모입니다. 자주 변경되는 값은 YAML 이 아닌 **Feature Flag 서비스** (LaunchDarkly) 에서 관리합니다:

- `feature.fraud_check` — 사기 탐지 호출 여부
- `feature.inventory_reserve` — 재고 선예약 여부 (off 시 직접 차감)
- `feature.multi_payment` — 복합 결제수단 허용
- `feature.async_notification` — 알림을 동기 대신 비동기로 발행
- `feature.bypass_pricing_engine` — 가격 엔진 장애 시 기본가 사용

---

## Development

### Prerequisites

- **Kotlin 1.9.x** (JVM 17 타겟)
- **JDK 17** — Azul Zulu 또는 Eclipse Temurin
- **Docker** — 로컬 의존 서비스 기동용
- **just** — 작업 러너 (Makefile 대체)
- **PostgreSQL 15+** 클라이언트 (마이그레이션용 `psql`)
- **Redis CLI** (선택, 디버깅 편의)

### 빌드

```bash
# 전체 빌드 + 테스트
just build

# 테스트 없이 빌드만
just build-fast

# 네이티브 이미지 (GraalVM)
just build-native
```

### Testing

테스트는 **3단계** 로 나뉩니다:

- **Unit tests** — `domain/` 하위. 순수 도메인 로직만. mock 없이 in-memory 구현으로.
- **Integration tests** — `test/integration/`. 실제 Postgres, Redis, Kafka 를 `testcontainers` 로 띄워서 실행.
- **Contract tests** — `test/contract/`. Pact 를 이용해 다운스트림 서비스와의 계약을 검증.

```bash
just test-unit          # 빠름, ~30초
just test-integration   # 느림, ~8분 (컨테이너 기동 포함)
just test-contract      # ~2분
just test-all           # 전부
```

### Chaos Testing

프로덕션 유사 환경에서 **카오스 테스트** 를 주기적으로 돌립니다:

- **Network partition** — 결제 게이트웨이 연결을 10초 끊고 복구. 주문이 재시도로 복구되는지 확인.
- **Slow dependency** — 가격 엔진에 500ms 지연 주입. 후퇴(fallback) 가 발동하고 p99 지연이 임계치 안인지 확인.
- **Resource exhaustion** — DB 커넥션 풀을 1로 줄이고 과부하. 백프레셔가 제대로 작동하는지.
- **Data corruption** — 이벤트 스트림에 말성 메시지 삽입. DLQ 로 가는지.

---

## Deployment

### CI/CD Pipeline

1. **빌드** — `just build` 실행, 단위 테스트 포함
2. **통합 테스트** — testcontainers 로 실제 DB/Redis/Kafka 띄워서
3. **이미지 빌드** — Docker 이미지 생성, 커밋 해시로 태깅
4. **스테이징 배포** — staging 네임스페이스에 롤아웃
5. **E2E 테스트** — 실제 주문·결제·반품 시나리오
6. **프로덕션 승인** — 수동 승인 후 프로덕션 배포
7. **프로덕션 배포** — 롤링 업데이트로 무중단 배포

### Rollback Procedure

롤백이 필요한 상황이 생기면:

1. **원인 파악** — 어떤 변경이 문제인지 로그와 메트릭으로 확인
2. **영향 범위 평가** — 데이터베이스 스키마 변경이 있었는지 확인 (있으면 롤백 어려움)
3. **이전 버전으로 복구** — `just rollback PREV_TAG` 실행
4. **검증** — 헬스체크, 주요 API 동작 확인
5. **사후 분석** — 인시던트 보고서 작성

롤백이 **위험한 경우**:
- 데이터베이스 스키마 변경이 포함된 경우 (roll-forward 하는 것이 안전)
- 30분 이상 지난 배포 (데이터 호환성 위험)
- 결제 상태 마이그레이션이 있었던 경우

---

## Observability

### Dashboards

운영 중 주요 대시보드:

- **Overview**: `grafana.internal/d/orderforge/overview` — 전반적인 주문 처리 상태
- **Payment**: `grafana.internal/d/orderforge/payment` — 결제 성공률, 실패 원인별 분포
- **Fulfillment**: `grafana.internal/d/orderforge/fulfillment` — 배송 상태 추적
- **Returns**: `grafana.internal/d/orderforge/returns` — 반품 처리 현황
- **Business KPI**: `grafana.internal/d/orderforge/kpi` — 매출, 주문당 평균금액, 이탈률

### Key Metrics

핵심 모니터링 지표:

- `orderforge_order_created_total` — 주문 생성 카운터 (상태별 라벨)
- `orderforge_order_duration_seconds` — 주문 처리 시간 히스토그램
- `orderforge_payment_success_rate` — 결제 성공률 (게이트웨이별)
- `orderforge_fulfillment_delay_seconds` — 배송 지연 시간
- `orderforge_return_rate` — 반품률 (카테고리별)
- `orderforge_active_orders` — 활성 주문 수 게이지

### Alerts

다음 조건에서 알림이 발동:

- 주문 생성 에러율 > 1% (5분간)
- 결제 성공률 < 95% (10분간)
- p99 주문 생성 지연 > 1초 (5분간)
- DB 커넥션 풀 사용률 > 80% (5분간)
- Kafka consumer lag > 10,000 메시지

---

## Troubleshooting

### 자주 발생하는 에러 코드

| 코드 | 원인 | 조치 |
|---|---|---|
| `ORD_E001` | 재고 부족 | 재고 서비스 확인, 필요 시 수동 조정 |
| `ORD_E002` | 가격 엔진 타임아웃 | 가격 엔진 상태 확인, fallback 활성화 |
| `PAY_E001` | 결제 게이트웨이 연결 실패 | 게이트웨이 상태 페이지 확인 |
| `PAY_E002` | 카드 승인 거절 | 고객에게 다른 결제수단 권유 |
| `FUL_E001` | 배송사 API 인증 실패 | 토큰 갱신 |
| `RTN_E001` | 반품 기한 초과 | 고객 서비스에 예외 처리 요청 |

### 흔한 장애 시나리오

**시나리오 1: 갑자기 주문 생성 실패율 급증**
- 원인 후보: 재고 서비스 장애, DB 연결 문제, 가격 엔진 지연
- 대응: 메트릭 확인 → 가장 영향 큰 의존성부터 복구

**시나리오 2: 결제 대기 주문이 쌓임**
- 원인 후보: 결제 게이트웨이 장애, 콜백 처리 지연
- 대응: 결제 이력 조회로 실제 승인 여부 확인, 필요 시 수동 리플레이

**시나리오 3: 배송 상태가 업데이트 안 됨**
- 원인 후보: 배송사 웹훅 인증 토큰 만료, 네트워크 이슈
- 대응: 웹훅 로그 확인, 수동 상태 동기화 API 호출

---

## FAQ

**Q: 주문이 이미 결제됐는데 취소하고 싶어요.**
A: `POST /api/v1/orders/{id}/returns` 로 반품 요청을 보내세요. 결제 후에는 직접 취소가 아니라 반품 플로우로 처리됩니다.

**Q: 같은 주문을 두 번 보냈는데 중복 처리되지 않나요?**
A: `Idempotency-Key` 헤더를 같은 값으로 보내면 두 번째 요청은 첫 번째 결과를 반환합니다. 키가 다르면 다른 주문으로 처리됩니다.

**Q: 결제 게이트웨이가 다운됐을 때 주문은 어떻게 되나요?**
A: 재시도 정책에 따라 최대 3번 시도 후 `EXPIRED` 로 전이됩니다. 이 때 예약된 재고와 포인트는 자동 복원됩니다.

**Q: 복합 결제수단(카드+포인트) 중 하나가 실패하면요?**
A: 이미 승인된 부분은 자동 취소(compensation) 됩니다. 사가 패턴으로 원자성을 보장합니다.

**Q: 반품 가능 기한은 바꿀 수 있나요?**
A: `return.max_days` 설정으로 변경 가능합니다. 단, 회계 규정상 최소 14일은 보장해야 합니다.

**Q: 이벤트가 누락된 것 같은데요?**
A: `POST /internal/v1/orders/{id}/replay-events` 로 이벤트를 재발행할 수 있습니다. 이벤트는 Kafka 에 7일간 보관됩니다.

**Q: BigQuery 미러와 운영 DB 가 다른 경우?**
A: CDC 가 6시간마다 돌아서 약간의 지연이 있습니다. 최신 데이터가 필요하면 운영 DB 를 봐야 합니다. 단 분석 쿼리는 운영 DB 로 보내지 마세요.

**Q: 테스트에서 실제 결제가 되나요?**
A: 아니요. 로컬/스테이징은 Mock 결제 게이트웨이(`payment_method: card_mock`) 를 사용합니다. 프로덕션 토큰은 다른 URL 로 연결됩니다.

---

## Contributing

기여 방법:

1. 이슈를 만들어 변경 의도를 설명
2. 브랜치 이름은 `feat/...`, `fix/...`, `chore/...` 로 시작
3. 단위 테스트 커버리지 80% 이상 유지
4. PR 설명에 "왜" 와 "무엇" 을 포함
5. 리뷰 2명 승인 후 머지

코드 스타일은 `ktlint` 자동 적용. 커밋 전에 `just fmt` 실행 권장.

---

## License

Apache License 2.0. `LICENSE` 파일 참조.

---

## Changelog

### 2026-04-20 (v2.8.0)
- 복합 결제수단 지원 확장 (상품권 추가)
- Kafka consumer lag 알림 추가
- 사기 탐지 엔드포인트 타임아웃 2초 → 5초 완화

### 2026-03-15 (v2.7.2)
- 반품 가능 기한 30일로 통일 (이전: 카테고리별 상이)
- p99 주문 생성 지연 280ms 달성 (이전 310ms)

### 2026-02-01 (v2.7.0)
- 가격 엔진 후퇴(fallback) 로직 도입
- CDC 빈도 12시간 → 6시간 단축

### 2025-12-10 (v2.6.0)
- 교환 플로우 정식 지원
- BigQuery 미러 테이블 스키마 v2 마이그레이션
