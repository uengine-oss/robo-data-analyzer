"""검색 동작 토글과 실측 임계값 설정.

dense 채널 구현(index|scan)·fetch_k 는 ``SETTINGS.semantic_search`` 가 단일 진실 — 여기 두면 이중화된다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from analyzer_settings import SETTINGS


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
    # spec 053: legacy=변형별 core+확장 후 합집합 / global_anchor=후보 합집합 후 단일 선별.
    # spec 053 A/B: shopmall full gold 30/30, average result size 18.8 -> 16.5.
    # legacy remains available as an explicit rollback mode.
    cluster_selection_mode: str = os.environ.get("ROBO_SEARCH_SELECTION", "global_anchor")
    cluster_anchor_top_n: int = int(os.environ.get("ROBO_SEARCH_ANCHOR_TOP", "3"))
    cluster_support_root_top_n: int = int(os.environ.get("ROBO_SEARCH_SUPPORT_ROOT_TOP", "12"))
    cluster_evidence_top_n: int = int(os.environ.get("ROBO_SEARCH_EVIDENCE_TOP", "15"))
    # spec 057: 앵커 점수절벽 게이트 — top1 과의 격차가 gap 초과면 앵커 제외(상한 top_n·하한 1).
    # 동반 입장 정책 — all_candidates(구 동작·롤백) | anchors_only | gated_direct |
    # graph_gated(기본): 비앵커는 [score ≥ top1−direct_gap] OR [앵커와 CALLS 거리 ≤ hop_gate]
    # 일 때만 입장(이중 신호 — 점수만으로는 신호 .676 < 노이즈 .765 역전이 실측됨).
    # 값은 골드 A/B 채택(specs/057 research.md 스윕 표 — 대조표 없는 임의 조정 금지).
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
    # spec 007 LLM 심판 — off | p15. ★기본 off (2026-07-11 게이트 실측):
    # 심판 p15 승자는 gpt 기준 — Qwen(GPU 통일)으로 돌리면 과잉삭제로 리콜 −2~−5(shopmall 30→25~28).
    # off = shopmall 30/30 유지, 합산 51/53(심판 on 46~49). 소음은 표시단(core/related 접기)에서 처리.
    # gpt 심판 복구 또는 Qwen 심판 완화 프롬프트는 TODO(랫홀 방지·별도 재측정).
    cluster_judge_mode: str = os.environ.get("ROBO_SEARCH_JUDGE", "off")
    cluster_judge_min: int = 12
    cluster_judge_top: int = 15

    # ── CE (cluster core 게이트 재료) ──
    ce_model: str = "dragonkue/bge-reranker-v2-m3-ko"


DEFAULT = Config()
