"""search/judge — 뭉치 후보 LLM 심판 (놀이터 engine/cluster_judge 이식, spec 007).

제거 전용(반환⊆입력, 환각 차단). 실패=None(호출부 폴백). 프로세스 내 메모리 캐시(temperature 0).
프롬프트 도메인 중립. 검색 LLM = search.llm(ROBO_SEARCH_LLM_CONFIG, 기본 Qwen/GPU).
"""
from __future__ import annotations

import os

from search import llm

# 판정 재료 통일(spec 009) — 심판이 임베딩·CE와 같은 재료(요약+search_text)를 봄. 기본 on(게이트 승자).
_SEM_UNIFIED = os.environ.get("ROBO_SEM_UNIFIED", "1") == "1"
_cache: dict = {}

_SYSTEM = """너는 레거시 코드 검색 결과의 심판이다. 사용자의 요구사항/질의와, 검색이 반환한
코드 노드 목록(이름·역할·요약)이 주어진다. **요구사항과 실제로 관련된 노드만** 골라라.

[규칙]
- 표면 단어만 겹치고 업무 의미가 다른 노드는 제외한다.
- 요구사항이 가리키는 업무 흐름의 구성원(직접 수행·검증·조회·기록·호출되는 구현)은 관련이다.
- 같은 시스템이라도 요구사항과 다른 업무 흐름의 곁가지는 제외한다.
- 확신이 없으면 포함하라 — 참고자료 용도라 아깝게 자르는 것보다 남기는 게 낫다.

[출력] 오직 단일 JSON 객체 1개: {"relevant": [관련 노드 번호들]}"""


def judge(query: str, nodes: list[dict]) -> set[int] | None:
    """nodes 중 질의 관련 인덱스 집합. LLM 실패/형식 위반 = None(호출부가 원결과 유지).

    ★ 목록 번호는 **1부터**. 0부터 매기면 LLM 이 0 을 안 내보내 리콜 1위가 매번 죽는다(실측).
    코드가 1-based 로 내보내고 받을 때 되돌린다."""
    if not nodes:
        return set()

    def _line(i: int, n: dict) -> str:
        body = (n.get("summary") or "").strip()
        if _SEM_UNIFIED and (n.get("search_text") or "").strip():
            body += " / " + n["search_text"].strip()
        return f"{i + 1}. {n['name']} [{n.get('stereotype') or n.get('label', '')}] {body}"

    lines = [_line(i, n) for i, n in enumerate(nodes)]
    prompt = (f"[요구사항]\n{query}\n\n[검색 결과 노드] (번호 1~{len(nodes)})\n"
              + "\n".join(lines) + "\n\n관련 노드 번호만 JSON으로.")
    key = hash((_SYSTEM, prompt))
    if key in _cache:
        v = _cache[key]
        return set(v) if v is not None else None
    out = llm.chat_json(prompt, system=_SYSTEM, max_tokens=2048)
    v = None
    if isinstance(out, dict) and isinstance(out.get("relevant"), list):
        v = sorted({int(i) - 1 for i in out["relevant"]
                    if str(i).lstrip("-").isdigit() and 1 <= int(i) <= len(nodes)})
    _cache[key] = v
    return set(v) if v is not None else None
