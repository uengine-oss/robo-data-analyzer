"""검색 동작 토글과 실측 임계값 설정.

dense 채널 구현(index|scan)·fetch_k 는 ``SETTINGS.semantic_search`` 가 단일 진실 — 여기 두면 이중화된다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from shared.config.settings import SETTINGS


@dataclass
class Config:
    # ── L2 다중채널 리콜 ──
    use_dense: bool = True
    use_bm25: bool = True
    use_structural: bool = True
    fetch_k: int = SETTINGS.semantic_search.fetch_k     # 채널별 후보 수 (실측: 100)
    rrf_k: int = 60                        # RRF 상수

    # ── 요구사항→관련코드 뭉치 (spec 005, CE 게이트) ──
    cluster_top_n: int = 12               # 곁가지 컷: 상위 N만 핵심
    # 후보 합집합 뒤 전역 앵커를 한 번 선별한다. 과거 변형별 합집합 경로는 제거했다.
    cluster_anchor_top_n: int = int(os.environ.get("ROBO_SEARCH_ANCHOR_TOP", "3"))
    cluster_support_root_top_n: int = int(os.environ.get("ROBO_SEARCH_SUPPORT_ROOT_TOP", "12"))
    cluster_evidence_top_n: int = int(os.environ.get("ROBO_SEARCH_EVIDENCE_TOP", "15"))
    # spec 057: 앵커 점수절벽 게이트 — top1 과의 격차가 gap 초과면 앵커 제외(상한 top_n·하한 1).
    # 동반 입장 정책 — all_candidates(구 동작·롤백) | anchors_only | gated_direct |
    # graph_gated(기본): 비앵커는 [score ≥ top1−direct_gap] OR [앵커와 CALLS 거리 ≤ hop_gate]
    # 일 때만 입장(이중 신호 — 점수만으로는 신호 .676 < 노이즈 .765 역전이 실측됨).
    # 값은 검색 회귀 테스트가 고정한다. 새 비교 근거 없이 임의 조정하지 않는다.
    # 채택값 = 2026-07-16 골드 전수 스윕 P5 h3/r2 (research.md 표): recall 29/30(기준선 동률,
    # 유일 미스 release_stock 은 P0 도 미스) · 검증질의 노이즈 14→6 · 평균크기 18.2→15.9.
    cluster_anchor_gap: float = float(os.environ.get("ROBO_SEARCH_ANCHOR_GAP", "0.12"))
    cluster_support_policy: str = os.environ.get("ROBO_SEARCH_SUPPORT_POLICY", "graph_gated")
    cluster_direct_gap: float = float(os.environ.get("ROBO_SEARCH_DIRECT_GAP", "0.12"))
    cluster_hop_gate: int = int(os.environ.get("ROBO_SEARCH_HOP_GATE", "3"))
    # 순위 구제(graph_gated 보조): 의미순위 상위 N 은 직행 근거로 무조건 포함(확장 뿌리 자격
    # 은 없음). 점수 역전으로 절벽 밖에 있는 상위권 정답(실측: shipping_calc_fee 0.669 <
    # 노이즈 0.765) 보존용.
    cluster_rank_floor: int = int(os.environ.get("ROBO_SEARCH_RANK_FLOOR", "2"))
    cluster_hub_fanin: int = 20           # 허브 컷: 호출 팬인 이상이면 인프라 유틸로 제외
    cluster_expand_hops: int = 1          # 관계확장 홉
    cluster_ce_min: float = 0.20          # core 게이트(CE 활성): CE 확률 미만은 곁가지
    cluster_cos_min: float = 0.50         # 확장·호출표시 게이트 + core 합집합 게이트의 코사인 축
    cluster_ce_quorum: int = 2            # CE 모드 정족수(준활성 함정 방지)
    # CE 채점 대상 = RRF 상위 N만(CPU 비용 절반↓). 나머지 pool 은 코사인 축으로만 게이트 통과
    # 가능(합집합 게이트의 cos≥cos_min). 30 전부 채점으로 되돌리려면 환경변수로 30.
    cluster_ce_top: int = int(os.environ.get("ROBO_SEARCH_CE_TOP", "15"))
    cluster_type_weight: float = 0.15     # 타입/stereotype 매칭 랭킹 가점 (게이트 불개입)

    # spec 006 용어집 번역 게이트 — off | novel_abbrev. 승자 = novel_abbrev(게이트 실측: skt_now 구제).
    cluster_rewrite_mode: str = os.environ.get("ROBO_SEARCH_REWRITE", "novel_abbrev")
    # ── CE (cluster core 게이트 재료) ──
    ce_model: str = "dragonkue/bge-reranker-v2-m3-ko"


DEFAULT = Config()
