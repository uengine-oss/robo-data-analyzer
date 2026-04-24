"""OrderForge 문서 샘플 생성 — 실제 규모 + 의도적 의미 섞임.

실행:
  python test/universal_file_parser/samples/test_project/docs/_generate_samples.py

생성:
  - architecture.pdf   (20+ 페이지 분량, 의미 섞인 구조)
  - overview.pptx      (25 슬라이드)
  - metrics.xlsx       (5 시트, 시트당 여러 표)
  - operations.docx    (18+ 섹션)
"""

from pathlib import Path

OUT_DIR = Path(__file__).parent


# =============================================================================
# PDF — OrderForge 아키텍처 설계서
# =============================================================================

def make_pdf():
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4

    path = OUT_DIR / "architecture.pdf"
    c = canvas.Canvas(str(path), pagesize=A4)
    w, h = A4

    def wrap(text, x, y, width=500, fs=10, lh=13):
        c.setFont("Helvetica", fs)
        words = text.split()
        line = ""
        for word in words:
            cand = (line + " " + word).strip()
            if c.stringWidth(cand, "Helvetica", fs) < width:
                line = cand
            else:
                c.drawString(x, y, line)
                y -= lh
                line = word
                if y < 60:
                    c.showPage()
                    y = h - 60
        if line:
            c.drawString(x, y, line)
            y -= lh
        return y

    def title_page():
        c.setFont("Helvetica-Bold", 18)
        c.drawString(50, h - 60, "OrderForge — Architecture & Design Document")
        c.setFont("Helvetica", 10)
        c.drawString(50, h - 80, "Revision 3.1 / 2026-04-15 / Owner: Platform Engineering")

    sections = [
        ("1. Document Purpose and Audience",
         "This document describes the internal architecture of the OrderForge service for "
         "engineers, operators, and product managers. It is the authoritative reference for "
         "design decisions and should be updated whenever those decisions change. Parts of this "
         "document duplicate information that appears in the README and the operations runbook "
         "on purpose; the goal is that anyone picking up a single document can understand the "
         "relevant subset without jumping between sources. When information in this document "
         "conflicts with production configuration, production configuration wins and this "
         "document is the one that needs updating."),

        ("2. Context and Motivation",
         "OrderForge replaced the monolithic legacy-order-backend in late 2024. The old system "
         "coupled reservation orders, standard orders, and subscription orders inside a single "
         "transaction. A slow payment provider or a single database deadlock would block every "
         "path simultaneously. OrderForge splits these concerns so each bounded context can "
         "fail and recover independently. The migration took seven months and involved "
         "shadow-writing to both systems for the final six weeks until cutover. During that "
         "period the two systems had to agree byte-for-byte on the resulting order document, "
         "which is why the current storage model looks verbose compared to a greenfield design."),

        ("3. Order State Machine",
         "Every order is a finite state machine. Transitions are driven exclusively by domain "
         "events; there is no API that directly mutates order state. The canonical state graph "
         "is: DRAFT -> PENDING_PAYMENT -> (PAID | EXPIRED), PAID -> PICKING -> PACKED -> "
         "SHIPPED -> DELIVERED, DELIVERED -> (RETURN_REQUESTED -> RETURNED). Timeouts for each "
         "state are defined in config/state_timeouts.yaml and enforced by a background worker "
         "that runs every 30 seconds. The state machine is the single source of truth; any "
         "other subsystem reading order state must derive it from here."),

        ("4. Bounded Contexts",
         "Five bounded contexts live inside a single deployable. They share a process and a "
         "database but not a schema. Ordering owns the state machine and the order aggregate. "
         "PricingAdapter is the outbound gateway to the pricing engine. PaymentOrchestrator "
         "owns the saga that coordinates multi-method payments. FulfillmentBridge translates "
         "carrier webhooks into internal events. Returns handles the post-delivery workflow. "
         "The decision to keep them in one deployable rather than splitting into services came "
         "down to the cost of distributed transactions; everything we tried added more "
         "complexity than it removed."),

        ("5. Data Storage",
         "PostgreSQL 15 is the system of record. The schema is partitioned by created_month so "
         "that old data can be archived without a full table rewrite. Read replicas serve "
         "analytical dashboards but never drive write paths. Redis holds idempotency keys, "
         "active order locks, and short-lived caches for customer profile lookups; a Redis "
         "failure degrades performance but does not take down order creation. Kafka is the "
         "event backbone: every state transition produces a message on the orderforge.events "
         "topic and downstream services subscribe. The retention is seven days, which is long "
         "enough for any downstream consumer to recover from a multi-day outage."),

        ("6. Payment Orchestration — Part One",
         "Payments are the most complex part of the system. A single order can be paid with "
         "one card, multiple cards, a combination of points and a card, or a split across a "
         "gift certificate and a card. Each method has different idempotency guarantees from "
         "the underlying gateway. The orchestrator uses the saga pattern: it builds a plan of "
         "authorization calls, executes them in order, and if any step fails it runs the "
         "compensation plan to cancel already-authorized amounts. The plan is persisted so that "
         "recovery after a process crash can resume exactly where it left off. See section 11 "
         "for continuation of this topic."),

        ("7. Inventory Interaction",
         "OrderForge does not own inventory. It sends reservation commands to the inventory "
         "service and trusts the reply. Reservation happens before pricing because a sold-out "
         "item should fail fast without calling the pricing engine. If the pricing call later "
         "fails and we roll back the order, we also release the reservation. Reservations have "
         "a TTL on the inventory side so that a crashed OrderForge instance never leaves stock "
         "permanently locked; the TTL is set to the configured order expiration plus a thirty "
         "second safety margin."),

        ("8. Fulfillment Integration",
         "Seven carriers deliver orders today. Each carrier sends webhooks with different "
         "payload shapes, authentication schemes, and retry behaviours. The FulfillmentBridge "
         "has one adapter per carrier and a single normalized event produced for each incoming "
         "webhook. The adapters are intentionally dumb: they translate shape and hand off to "
         "the shared state transition code. This means adding a new carrier is a matter of "
         "writing a single adapter class and adding the credentials; no core code changes."),

        ("9. Returns and Exchanges",
         "A return is modelled as a child entity of the original order. The parent order stays "
         "immutable and a return_request row captures the reason, the items being returned, "
         "and the financial adjustment. Exchanges are internally a pair of linked return and "
         "new order records; the pair is identified by an exchange_pair_id so that reports can "
         "collapse the two into a single customer-facing transaction. The return policy limits "
         "returns to three separate requests per order and enforces the global 30-day window "
         "from delivery."),

        ("10. Observability",
         "We emit structured JSON logs on stdout so that the Kubernetes log pipeline can ship "
         "them to Elasticsearch without reformatting. Every log record contains trace_id, "
         "span_id, order_id (when relevant), and a stable event_code that the alerting rules "
         "pattern-match against. Metrics are exposed as Prometheus counters and histograms; "
         "the naming follows the orderforge_ prefix convention. Traces flow to Jaeger and are "
         "sampled at 5% by default with forced sampling on errors. Dashboards are in Grafana "
         "under the orderforge folder."),

        ("11. Payment Orchestration — Part Two",
         "Continuing from section 6, the implementation detail worth highlighting is the "
         "handling of partial failures. If the first card authorization succeeds for 50,000 KRW "
         "but the point deduction for the remaining 10,000 KRW fails, the orchestrator must "
         "cancel the first authorization before the customer sees a charge. The cancellation "
         "call to the gateway is itself idempotent; we keep retrying until it succeeds or until "
         "an operator escalates. In the rare case where the gateway confirms a charge we never "
         "asked for, a reconciliation job compares our expected state with the gateway's daily "
         "settlement file and flags discrepancies for human review."),

        ("12. Security Posture",
         "Customer PII is stored only where strictly required: shipping address, recipient "
         "name, recipient phone. Payment details are never stored on our side; we only keep "
         "tokens issued by the gateways. All database connections use TLS and the primary "
         "cluster sits inside a private VPC. The application runs as a non-root user in a "
         "read-only filesystem container. Secrets come from the cluster secret manager at pod "
         "start and are rotated every 90 days by an automated job. Network egress is restricted "
         "to the payment gateway IP ranges and internal service CIDRs."),

        ("13. Capacity Planning",
         "At peak traffic the service handles about 650 orders per second. The bottleneck on "
         "the critical path is the pricing engine call, which caps out around 800 requests per "
         "second due to downstream constraints. We mitigate this with a local cache of "
         "frequently applied coupons and a circuit breaker that falls back to list prices if "
         "the engine starts to degrade. Capacity is reviewed each quarter against campaign "
         "plans; the last review raised the minimum replica count from six to eight ahead of "
         "the year-end sales period."),

        ("14. Testing Strategy",
         "Unit tests cover the domain modules exhaustively and must run in under thirty "
         "seconds. Integration tests start real Postgres, Redis, and Kafka containers and run "
         "the actual HTTP handlers. Contract tests exchange stubs with pricing and inventory "
         "so that a breaking change in their API shows up in our CI rather than in production. "
         "Chaos tests run weekly against the staging cluster and inject network partitions, "
         "slow dependencies, and message loss to verify our resilience claims."),

        ("15. Deployment Topology",
         "Production runs in three regions active-active with primary data residency in Seoul. "
         "Each region has its own Postgres primary and Redis cluster, plus a shared global "
         "Kafka deployment. Cross-region reads fall back to the local cache if the global "
         "cluster is unreachable. Deployments are blue-green: the new version runs in parallel "
         "on half the replicas until the error rate over five minutes is within the historical "
         "window, then traffic migrates fully. Rollbacks are a single command but operators "
         "prefer roll-forward when the issue is not obviously catastrophic."),

        ("16. Known Limitations",
         "The service does not currently support partial shipments from a single order; all "
         "items must ship together or the order splits into multiple child orders. Multi-tenant "
         "isolation is cooperative rather than enforced; a runaway tenant can cause noisy "
         "neighbour effects. International shipping requires manual intervention for customs "
         "declarations because our templates are only translated for three carriers so far."),

        ("17. Upcoming Work",
         "The next major revision will introduce partial shipment support and a declarative "
         "fraud rule engine. The shipment work touches the state machine so it is scoped "
         "carefully; the fraud engine is additive and expected to ship first. A longer-term "
         "item is replacing the saga implementation with an outbox-based approach, which we "
         "believe will simplify recovery and reduce the amount of process memory held during "
         "payment flows."),

        ("18. Appendix A — Glossary",
         "Idempotency-Key: a caller-supplied token that allows safe retries of mutating "
         "requests; if a request with a previously-seen key arrives, the cached response is "
         "returned rather than executing the action again. Saga: a sequence of local "
         "transactions with defined compensations, used here to coordinate multi-step payment "
         "flows without a distributed transaction. Bounded Context: a cohesive subset of the "
         "domain model with its own ubiquitous language; OrderForge contains five. DLQ: dead "
         "letter queue; where events go when they cannot be processed after all retries."),
    ]

    title_page()
    y = h - 110
    for title, body in sections:
        if y < 180:
            c.showPage()
            title_page()
            y = h - 110
        c.setFont("Helvetica-Bold", 11)
        c.drawString(50, y, title)
        y -= 16
        y = wrap(body, 60, y)
        y -= 10

    c.save()
    return path


# =============================================================================
# PPTX — OrderForge 프로젝트 발표
# =============================================================================

def make_pptx():
    from pptx import Presentation

    path = OUT_DIR / "overview.pptx"
    prs = Presentation()

    slides = [
        ("OrderForge Overview",
         "Order Management Microservice / Platform Engineering / 2026 Q2"),

        ("Why OrderForge",
         "We replaced the legacy monolith because a single slow payment provider would "
         "block every order path simultaneously. OrderForge isolates each concern into its "
         "own bounded context so failures can be contained. The migration took seven months "
         "and cut our peak-time error rate from 3.2% to under 0.4%."),

        ("What OrderForge owns",
         "Order creation and the state machine, payment orchestration across multiple methods, "
         "fulfillment webhook ingestion, returns and exchanges, settlement calculation, and "
         "outbound event publication. It explicitly does not own catalog data, inventory "
         "counts, notification delivery, or coupon pricing logic."),

        ("State machine at a glance",
         "DRAFT -> PENDING_PAYMENT -> PAID -> PICKING -> PACKED -> SHIPPED -> DELIVERED. "
         "Returns branch off from DELIVERED. Every transition is event-driven; there is no "
         "endpoint to force a state change except the internal operational API used during "
         "incidents."),

        ("Order creation flow",
         "Idempotency check, inventory reservation, pricing engine call, order materialization "
         "as DRAFT, immediate submit transition to PENDING_PAYMENT, hand-off to payment "
         "orchestrator, and response to the client. Entire path budgeted at 500ms p99; typical "
         "latency is around 280ms."),

        ("Payment orchestration",
         "Saga pattern with per-step compensation. Supports single card, multiple cards, "
         "card + points, card + gift certificate, and combinations of three methods. If any "
         "step fails the prior authorizations are cancelled through gateway-specific "
         "idempotent cancellation APIs."),

        ("Inventory and pricing integration",
         "Inventory reservations have TTL safety margins so a crashed process cannot leave "
         "stock permanently locked. Pricing calls have a circuit breaker with fallback to list "
         "prices when the engine degrades. Both integrations have been tested in chaos drills."),

        ("Fulfillment integration",
         "Seven carrier webhooks normalized through per-carrier adapters. Adding a new carrier "
         "is one adapter class plus credentials. We never re-implement state transition logic "
         "per carrier; adapters translate shape only."),

        ("Returns and exchanges",
         "Returns are child entities of the original order. Exchanges are modelled as paired "
         "return + new order linked by exchange_pair_id. Return policy enforces 30-day window "
         "and maximum three separate requests per order."),

        ("Storage",
         "Postgres 15 cluster for authoritative state, partitioned by created_month. Redis "
         "for idempotency keys, locks, and hot caches. Kafka for the event backbone with seven "
         "day retention. BigQuery mirror on a six-hour CDC for analytics."),

        ("Observability",
         "Structured logs, Prometheus metrics, Jaeger traces with 5% default sampling and "
         "forced error sampling. Dashboards in Grafana under the orderforge folder cover the "
         "full pipeline from order creation to settlement."),

        ("Alerts",
         "Order creation error rate over 1% for five minutes, payment success rate under 95% "
         "for ten minutes, p99 order latency over one second for five minutes, and Kafka "
         "consumer lag over ten thousand messages. All alerts link back to the runbook."),

        ("Scaling characteristics",
         "650 orders per second at peak. Eight replicas minimum in production with horizontal "
         "autoscaling up to twenty. Bottleneck is the pricing engine call at around 800 "
         "requests per second; mitigated through local caching and circuit breaker."),

        ("Security",
         "Customer PII is minimized to shipping details only. Payment details are never stored "
         "on our side. TLS everywhere, non-root containers, read-only filesystem, secrets "
         "rotated every ninety days, network egress restricted to known CIDRs."),

        ("Deployment",
         "Three region active-active. Blue-green deployments with error-rate gating before "
         "full cutover. Rollback is a single command but operators typically prefer to "
         "roll-forward when the regression is not catastrophic."),

        ("Testing",
         "Unit tests run in under thirty seconds, integration tests with real containers take "
         "about eight minutes, contract tests with downstream services run in CI, and chaos "
         "tests inject network partitions and slow dependencies weekly in staging."),

        ("Performance comparison",
         "Metric              | Legacy      | OrderForge\n"
         "Orders per second   | 220         | 650\n"
         "p99 create latency  | 1.2s        | 280ms\n"
         "Error rate at peak  | 3.2%        | 0.4%\n"
         "Deploy frequency    | weekly      | daily"),

        ("Recent wins",
         "Blackfriday handled without any sev-1 incidents for the first time in four years. "
         "Deployment frequency went from weekly to daily without regressing change-failure-rate. "
         "Payment success rate up 2.8 percentage points year over year."),

        ("Known limitations",
         "Partial shipment from a single order is not supported yet. Multi-tenant isolation is "
         "cooperative rather than enforced. International customs declarations still require "
         "manual intervention for four of the seven carriers."),

        ("What we are working on",
         "Partial shipment support for Q3. Declarative fraud rule engine for Q2. Outbox-based "
         "saga replacement on the roadmap for Q4. Continued investment in the chaos harness to "
         "catch more failure modes before production."),

        ("Feedback from stakeholders",
         "Ops likes the observability story but wants faster iteration on dashboard changes. "
         "Finance is satisfied with settlement accuracy. Customer Service requests a self-serve "
         "return correction tool which is being scoped. Product wants partial shipment to "
         "enable the marketplace launch."),

        ("Team structure",
         "Platform Engineering owns OrderForge with seven engineers, one SRE, and one product "
         "manager. The on-call rotation covers business hours in Seoul with follow-the-sun "
         "support from London for after-hours. A cross-functional forum with Ops, Finance, and "
         "Customer Service meets biweekly."),

        ("Dependencies overview",
         "Upstream: customer, catalog, inventory, pricing, coupon. Downstream: notification, "
         "settlement, analytics. External: seven shipping carriers, four payment gateways, and "
         "a fraud detection engine. Most downstream consumers are event-driven and thus "
         "tolerate short outages on our side."),

        ("Cost profile",
         "Monthly infrastructure spend is dominated by Postgres at about 45%, followed by "
         "Kafka at 20% and the application replicas at 15%. Observability stack (logs, "
         "metrics, traces) accounts for 12%. Remaining 8% is spread across Redis, object "
         "storage, and transit."),

        ("Questions or feedback",
         "Slack #orderforge for general discussion. Incident-only channel is "
         "#orderforge-pager. Design review meetings are on Tuesdays at 11am. The team "
         "backlog is in Jira under project key OF."),
    ]

    for title_text, body_text in slides:
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = title_text
        body = slide.placeholders[1].text_frame
        body.text = body_text

    prs.save(str(path))
    return path


# =============================================================================
# XLSX — 운영 지표 매트릭스 (5 시트, 의미 섞인 구조)
# =============================================================================

def make_xlsx():
    from openpyxl import Workbook

    path = OUT_DIR / "metrics.xlsx"
    wb = Workbook()

    # 시트 1: SLO and Capacity (한 시트에 SLO 표 + 용량 계획 표 혼재)
    ws1 = wb.active
    ws1.title = "slo_and_capacity"
    ws1.cell(row=1, column=1, value="OrderForge SLO and Capacity")
    ws1.cell(row=2, column=1, value="Last updated: 2026-04-15")
    # Block A: SLO
    ws1.cell(row=4, column=1, value="Block A: Service Level Objectives")
    headers_a = ["SLO", "Target", "Window", "Current", "Status"]
    for c, v in enumerate(headers_a, 1):
        ws1.cell(row=5, column=c, value=v)
    slo_rows = [
        ["Availability",    "99.95%", "30d", "99.97%", "OK"],
        ["p99 order create", "< 500ms", "1h", "280ms", "OK"],
        ["Payment success", "> 95%",  "1h", "97.2%",  "OK"],
        ["Fulfillment lag", "< 60s",  "5m", "42s",    "OK"],
        ["Return approval", "< 24h",  "30d", "8h",    "OK"],
    ]
    for r, row in enumerate(slo_rows, 6):
        for c, v in enumerate(row, 1):
            ws1.cell(row=r, column=c, value=v)
    # Block B: Capacity (같은 시트 아래에 별도 표)
    ws1.cell(row=13, column=1, value="Block B: Capacity Plan (Q2 2026)")
    headers_b = ["Resource", "Current", "Planned", "Rationale"]
    for c, v in enumerate(headers_b, 1):
        ws1.cell(row=14, column=c, value=v)
    cap_rows = [
        ["App replicas (prod)", 8, 12, "Year-end sale ramp"],
        ["DB primary size",     "db.r6.2xlarge", "db.r6.4xlarge", "Connection pool pressure"],
        ["Redis cluster",       3, 3, "No change needed"],
        ["Kafka brokers",       3, 5, "Consumer lag during peak"],
        ["Pricing engine RPS",  800, 1200, "Blocking on pricing team Q3 upgrade"],
    ]
    for r, row in enumerate(cap_rows, 15):
        for c, v in enumerate(row, 1):
            ws1.cell(row=r, column=c, value=v)

    # 시트 2: Business KPI by month (단일 표, 월별)
    ws2 = wb.create_sheet("business_kpi")
    ws2.cell(row=1, column=1, value="Monthly Business KPIs 2026")
    headers_k = ["Month", "Orders", "GMV (KRW)", "Avg Basket", "Return Rate", "Top SKU"]
    for c, v in enumerate(headers_k, 1):
        ws2.cell(row=3, column=c, value=v)
    kpi_rows = [
        ["Jan", 24_300_000, 32_800_000_000, 135000, "3.1%", "BOOK-0001"],
        ["Feb", 22_100_000, 29_500_000_000, 133500, "3.4%", "BOOK-0001"],
        ["Mar", 26_800_000, 36_200_000_000, 135100, "3.0%", "PEN-0042"],
        ["Apr (MTD)", 14_500_000, 19_800_000_000, 136500, "2.8%", "BOOK-0001"],
    ]
    for r, row in enumerate(kpi_rows, 4):
        for c, v in enumerate(row, 1):
            ws2.cell(row=r, column=c, value=v)

    # 시트 3: Incident log and postmortem actions (한 시트에 2개 표)
    ws3 = wb.create_sheet("incidents")
    ws3.cell(row=1, column=1, value="Incident Log and Postmortem Actions")
    ws3.cell(row=3, column=1, value="Recent Incidents")
    hdr3a = ["ID", "Date", "Duration", "Severity", "Summary"]
    for c, v in enumerate(hdr3a, 1):
        ws3.cell(row=4, column=c, value=v)
    inc_rows = [
        ["INC-2026-04-07", "2026-04-07", "23m", "SEV-2", "Pricing engine timeout cascade"],
        ["INC-2026-03-22", "2026-03-22", "8m",  "SEV-3", "Kafka broker replaced during update"],
        ["INC-2026-02-15", "2026-02-15", "1h 12m", "SEV-1", "Payment gateway X outage affected 12% traffic"],
        ["INC-2026-02-01", "2026-02-01", "41m", "SEV-2", "Redis failover caused idempotency misses"],
    ]
    for r, row in enumerate(inc_rows, 5):
        for c, v in enumerate(row, 1):
            ws3.cell(row=r, column=c, value=v)
    # 두 번째 표 — 후속 액션
    ws3.cell(row=11, column=1, value="Postmortem Action Items")
    hdr3b = ["Incident", "Action", "Owner", "Due", "Status"]
    for c, v in enumerate(hdr3b, 1):
        ws3.cell(row=12, column=c, value=v)
    action_rows = [
        ["INC-2026-04-07", "Add circuit breaker to pricing adapter",  "jdoe",   "2026-05-01", "in progress"],
        ["INC-2026-04-07", "Reduce pricing timeout 8s -> 3s",        "jdoe",   "2026-04-25", "done"],
        ["INC-2026-03-22", "Stagger broker updates",                 "msmith", "2026-04-15", "done"],
        ["INC-2026-02-15", "Add gateway X failover to gateway Y",    "klee",   "2026-05-15", "in progress"],
        ["INC-2026-02-01", "Move idempotency store to Postgres",     "jdoe",   "2026-06-01", "not started"],
    ]
    for r, row in enumerate(action_rows, 13):
        for c, v in enumerate(row, 1):
            ws3.cell(row=r, column=c, value=v)

    # 시트 4: Feature flags (단순 카테고리 분류)
    ws4 = wb.create_sheet("feature_flags")
    hdr4 = ["Flag", "Default", "Prod", "Stage", "Category", "Owner"]
    for c, v in enumerate(hdr4, 1):
        ws4.cell(row=1, column=c, value=v)
    flag_rows = [
        ["fraud_check",        True,  True,  True,  "security", "jdoe"],
        ["inventory_reserve",  True,  True,  True,  "ordering", "msmith"],
        ["multi_payment",      True,  True,  True,  "payment",  "klee"],
        ["async_notification", False, True,  True,  "ordering", "msmith"],
        ["bypass_pricing",     False, False, True,  "resilience", "jdoe"],
        ["partial_shipment",   False, False, False, "fulfillment", "msmith"],
        ["fraud_rules_v2",     False, False, True,  "security", "klee"],
        ["new_return_ui",      False, False, True,  "returns", "msmith"],
    ]
    for r, row in enumerate(flag_rows, 2):
        for c, v in enumerate(row, 1):
            ws4.cell(row=r, column=c, value=v)

    # 시트 5: Carrier integration matrix (한 시트에 통합 상태 + 최근 이슈 혼재)
    ws5 = wb.create_sheet("carriers")
    ws5.cell(row=1, column=1, value="Carrier Integration Matrix")
    ws5.cell(row=3, column=1, value="Carrier Support")
    hdr5a = ["Carrier", "API Version", "Webhook", "Tracking", "International", "Since"]
    for c, v in enumerate(hdr5a, 1):
        ws5.cell(row=4, column=c, value=v)
    carrier_rows = [
        ["CJ Logistics",     "v3", "Yes", "Yes",   "No",   "2024-10"],
        ["Hanjin",           "v2", "Yes", "Yes",   "No",   "2024-11"],
        ["Lotte",            "v2", "Yes", "Partial", "No", "2024-12"],
        ["FedEx",            "v4", "Yes", "Yes",   "Yes",  "2025-03"],
        ["DHL",              "v3", "Yes", "Yes",   "Yes",  "2025-03"],
        ["USPS",             "v2", "Polling", "Yes", "Yes", "2025-06"],
        ["Korea Post",       "v1", "No",  "Manual", "No",  "2025-08"],
    ]
    for r, row in enumerate(carrier_rows, 5):
        for c, v in enumerate(row, 1):
            ws5.cell(row=r, column=c, value=v)
    # Block B: recent carrier issues (같은 시트)
    ws5.cell(row=14, column=1, value="Recent Carrier Issues")
    hdr5b = ["Carrier", "Issue", "Impact", "Status"]
    for c, v in enumerate(hdr5b, 1):
        ws5.cell(row=15, column=c, value=v)
    issue_rows = [
        ["Korea Post", "No webhook support, polling unreliable",    "5% orders delayed", "workaround in place"],
        ["USPS",       "Webhook signature validation failing",      "3% orders delayed", "fix in flight"],
        ["CJ Logistics","Occasional duplicate delivery events",     "0.1% reconciliation", "acceptable"],
    ]
    for r, row in enumerate(issue_rows, 16):
        for c, v in enumerate(row, 1):
            ws5.cell(row=r, column=c, value=v)

    wb.save(str(path))
    return path


# =============================================================================
# DOCX — 운영 가이드 (18 섹션, 의미 섞임)
# =============================================================================

def make_docx():
    from docx import Document

    path = OUT_DIR / "operations.docx"
    doc = Document()

    doc.add_heading("OrderForge Operations Runbook", 0)
    doc.add_paragraph(
        "Audience: on-call engineers, SRE, and platform operators. This runbook gives you "
        "enough context to diagnose and mitigate the most common incidents without paging "
        "deeper expertise. For design rationale see the architecture document; for API "
        "specifics see the README."
    )

    sections = [
        ("1. On-Call Primer",
         "The on-call rotation is one week long. A primary engineer carries the pager and a "
         "secondary is available for escalation. At the start of each rotation the primary "
         "should run through the weekly checklist in the #orderforge-ops channel and confirm "
         "that all dashboards, alerts, and access credentials work. If anything is broken, "
         "fix it now rather than waiting for the next incident."),

        ("2. First Response Protocol",
         "When paged, acknowledge within two minutes. Join the incident channel and state the "
         "known symptom verbatim from the alert. Open the Overview dashboard in Grafana while "
         "the rest of the investigation kicks off. If impact is customer-visible and broad, "
         "start a status page update immediately; update it at least every thirty minutes until "
         "resolution."),

        ("3. Deployment and Releases",
         "Regular deployments happen through the CI/CD pipeline once unit, integration, and "
         "contract tests pass. Blue-green rollouts gate full traffic cutover on the error rate "
         "remaining within a five-minute historical window. Hotfix deployments bypass the staging "
         "wait but still go through full automated testing. Never deploy directly to "
         "production from a laptop; always through the pipeline."),

        ("4. Configuration Management",
         "Runtime configuration is split across three layers: built-in defaults, environment "
         "specific YAML, and runtime environment variables. The env vars win. Secrets come from "
         "the cluster secret manager and are never checked into git. Changes to app.prod.yaml "
         "require a rolling restart of the deployment but do not require a rebuild. Feature "
         "flag changes are instant and do not require a restart."),

        ("5. Rollback Procedure",
         "If a deployment introduces a regression, the standard rollback path is one command: "
         "just rollback PREV_TAG. This re-applies the previous image tag to the deployment. "
         "Rollbacks propagate across all replicas in under two minutes. If the deployment "
         "included a database migration, rollback is usually unsafe and roll-forward is "
         "preferred. Consult the deployment history before deciding."),

        ("6. Database Operations",
         "The Postgres primary should be treated as precious. Never run ad-hoc queries against "
         "it; use a read replica. Long-running queries on any replica will get killed after "
         "five minutes by the query governor, and this is intentional. Schema changes go "
         "through the migration system which applies them online with lock timeout safety. "
         "If a migration is taking longer than ten minutes, abort it and escalate."),

        ("7. Redis Incidents",
         "Redis failure does not take down order creation; the service falls back to reading "
         "idempotency keys from Postgres with a performance penalty. If Redis is unhealthy for "
         "more than thirty minutes, file an incident with the platform team; we should not "
         "operate in degraded mode indefinitely. The failover runbook for Redis lives with "
         "the platform team, not here."),

        ("8. Kafka Incidents",
         "Producer failures are generally benign because the service buffers in memory briefly "
         "and retries. Consumer lag growth is more concerning because it delays downstream "
         "notifications. If consumer lag exceeds ten thousand messages, start investigating "
         "whether a specific consumer is slow or whether the broker itself is under pressure. "
         "Increasing consumer parallelism is usually the first remediation."),

        ("9. Payment Gateway Issues",
         "Each payment gateway has a separate circuit breaker. When one trips, traffic to that "
         "gateway pauses and the orchestrator tries to route to a fallback gateway where "
         "possible. Some transaction types cannot be rerouted and will fail fast; this is "
         "preferable to hanging. Watch the per-gateway success rate dashboard; the breaker "
         "should recover within fifteen minutes of the underlying issue resolving."),

        ("10. Fulfillment Webhooks",
         "If delivery status is stale, check the webhook ingestion metrics for the specific "
         "carrier first. Authentication failures are the most common cause and usually mean "
         "the rotating token needs refreshing. For carriers without proper webhook support we "
         "poll their status API; polling failures show up as tracking_lag_seconds in the "
         "dashboard."),

        ("11. Returns and Exchanges Support",
         "Customer Service occasionally needs to override standard return behaviour: waive the "
         "thirty day window, allow a fourth return request, or process a refund to a different "
         "payment method than the original. These are supported through the internal admin API "
         "with two-person approval. Always record the justification in the audit log; finance "
         "audits this monthly."),

        ("12. Data Corruption Handling",
         "If you suspect data corruption (mismatched totals, impossible state transitions), "
         "stop further automated processing on the affected orders immediately. The quarantine "
         "flag on the order table prevents workers from acting on it. Then engage the senior "
         "engineer on-call and the data reliability team. Recovery paths depend on the nature "
         "of the corruption and are not prescribed here."),

        ("13. Monitoring and Dashboards",
         "The Overview dashboard is the default starting point. It shows current order volume, "
         "error rates, and latency for the critical path. Drill-down dashboards exist for "
         "payments, fulfillment, and returns. Business KPI dashboards (GMV, basket size, "
         "conversion) are owned by the product analytics team; link, do not duplicate."),

        ("14. Alerting",
         "All alerts link back to a specific section of this runbook. If an alert fires that "
         "does not have a runbook entry, write one as part of closing out the incident. We "
         "prefer a small number of high-quality alerts over many noisy ones. The review "
         "session happens monthly; alerts with false positive rate over twenty percent are "
         "either tuned or removed."),

        ("15. Chaos Exercises",
         "The chaos harness runs every Friday at 14:00 Seoul time in staging. It injects a "
         "random failure mode from a predefined list: network partition, slow dependency, "
         "message loss, resource exhaustion. The on-call engineer for the following week is "
         "expected to watch and document any unexpected behaviour. This is explicitly not "
         "a production exercise; we do not inject chaos into production."),

        ("16. Communication During Incidents",
         "For SEV-1 incidents, communications follow a strict schedule: initial notification "
         "within ten minutes, status update every thirty minutes, resolution notice within "
         "an hour of recovery. Use the standardized templates in the runbook appendix. For "
         "lower severities the cadence is relaxed but still predictable. The incident "
         "commander, not the investigator, sends updates."),

        ("17. Postmortem Process",
         "Every incident at SEV-2 or higher gets a blameless postmortem within five business "
         "days. The template covers timeline, root cause, contributing factors, remediation, "
         "and follow-up actions. Follow-ups are tracked in the incident log with owners and "
         "due dates. We review progress on open actions at the monthly ops review."),

        ("18. Contacts and Escalation",
         "Primary on-call: paged through Opsgenie. Secondary on-call: backup for primary. "
         "Escalation: engineering manager at one hour of active SEV-1. Business stakeholder: "
         "VP Engineering at ninety minutes of active SEV-1 with broad customer impact. "
         "External communication including regulatory notifications: PR and Legal, engaged "
         "by the VP Engineering not by individual engineers."),
    ]

    for title, body in sections:
        doc.add_heading(title, 1)
        doc.add_paragraph(body)

    doc.save(str(path))
    return path


if __name__ == "__main__":
    print("OrderForge 샘플 문서 생성 중...")
    for fn in [make_pdf, make_pptx, make_xlsx, make_docx]:
        p = fn()
        print(f"  {p.name}  ({p.stat().st_size:>7,} bytes)")
    print("완료")
