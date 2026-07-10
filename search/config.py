"""search/config — cluster 검색 토글·임계 (놀이터 engine/config 이식, 실측 튜닝값).

dense 채널 구현(index|scan)·fetch_k 는 ``ENV.search`` 가 단일 진실 — 여기 두면 이중화된다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from shared.config.env_catalog import ENV


@dataclass
class Config:
    # ── L2 다중채널 리콜 ──
    use_dense: bool = True
    use_bm25: bool = True
    use_structural: bool = True
    fetch_k: int = ENV.search.fetch_k     # 채널별 후보 수 (실측: 100)
    rrf_k: int = 60                        # RRF 상수

    # ── 요구사항→관련코드 뭉치 (spec 005, CE 게이트) ──
    cluster_top_n: int = 12               # 곁가지 컷: 상위 N만 핵심
    cluster_hub_fanin: int = 20           # 허브 컷: 호출 팬인 이상이면 인프라 유틸로 제외
    cluster_expand_hops: int = 1          # 관계확장 홉
    cluster_ce_min: float = 0.20          # core 게이트(CE 활성): CE 확률 미만은 곁가지
    cluster_cos_min: float = 0.50         # 확장·호출표시 게이트 + core 합집합 게이트의 코사인 축
    cluster_ce_quorum: int = 2            # CE 모드 정족수(준활성 함정 방지)
    # CE 채점 대상 = RRF 상위 N만(CPU 비용 절반↓). 나머지 pool 은 코사인 축으로만 게이트 통과
    # 가능(합집합 게이트의 cos≥cos_min). 30 전부 채점으로 되돌리려면 env 로 30.
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
