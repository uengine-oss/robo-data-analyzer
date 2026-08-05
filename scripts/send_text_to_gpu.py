"""GPU(SGLang) 서버에 그냥 텍스트를 보내는 최소 스크립트.

스키마·가드·재전송 장치 없음. 텍스트 in → 텍스트 out.
endpoint / model / sampling / extra_body 는 ``llm/configs/<ROBO_LLM_CONFIG>.yaml``,
API 키는 ``.env`` 의 ``ROBO_LLM_API_KEY`` — 하드코딩 없음.

사용법 3가지:

1. **딸각 (대화형)** — 인자 없이 실행하면 채팅 루프. ``D:\\work\\robo\\gpu-chat.cmd``
   더블클릭이 이 모드다. 텍스트 입력 → 답 출력 반복. ``/new`` 대화 초기화,
   ``/q`` 또는 빈 줄에서 Enter 두 번 → 종료.
2. 인자 1건: ``python scripts\\send_text_to_gpu.py "보낼 텍스트"``
3. 파이프: ``type question.txt | python scripts\\send_text_to_gpu.py``

한 번 실행(2·3번)은 응답 본문만 stdout — 파이프 사용 가능. 토큰 사용량은 stderr.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import httpx

from shared.config.settings import SETTINGS
from llm.configs.loader import load_llm_config


def _build_body(cfg, messages: list[dict], max_tokens: int, stream: bool) -> dict:
    body = {
        "model": cfg.model,
        "messages": messages,
        "temperature": cfg.sampling.temperature,
        "top_p": cfg.sampling.top_p,
        "max_tokens": max_tokens,
        # yaml 의 extra_body (enable_thinking=false / top_k 등) 그대로 병합
        **(cfg.extra_body or {}),
    }
    if stream:
        body["stream"] = True
    return body


def _stream_chat(cfg, url: str, headers: dict, messages: list[dict], max_tokens: int) -> str:
    """스트리밍 호출 — 토큰을 즉시 출력하고 전체 응답 텍스트를 반환."""
    chunks: list[str] = []
    with httpx.stream(
        "POST", url, json=_build_body(cfg, messages, max_tokens, stream=True),
        headers=headers, timeout=cfg.timeout_seconds, verify=cfg.verify_ssl,
    ) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line.startswith("data: "):
                continue
            data = line[len("data: "):]
            if data.strip() == "[DONE]":
                break
            choices = json.loads(data).get("choices") or []
            if choices:
                piece = choices[0].get("delta", {}).get("content") or ""
                if piece:
                    chunks.append(piece)
                    print(piece, end="", flush=True)
    print()
    return "".join(chunks)


def _chat_loop(cfg, url: str, headers: dict, system: str | None, max_tokens: int) -> None:
    """대화형 모드 — 텍스트 넣고 Enter, 답 보고 또 입력. 이전 대화를 기억한다."""
    print("─" * 60)
    print(f" GPU 채팅 — {cfg.model} @ {cfg.api_base}")
    print(" 텍스트 입력 후 Enter. /new = 대화 초기화, /q 또는 빈 입력 = 종료")
    print("─" * 60)

    def fresh() -> list[dict]:
        return [{"role": "system", "content": system}] if system else []

    messages = fresh()
    while True:
        try:
            # 파이프/리다이렉트 입력에 섞이는 BOM 도 제거 — /q 판정이 깨지지 않게
            text = input("\n나> ").strip().lstrip("﻿").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n종료")
            return
        if not text or text.lower() in ("/q", "/quit", "exit"):
            print("종료")
            return
        if text.lower() == "/new":
            messages = fresh()
            print("(대화 초기화)")
            continue
        messages.append({"role": "user", "content": text})
        print(f"\n{cfg.model}> ", end="", flush=True)
        try:
            answer = _stream_chat(cfg, url, headers, messages, max_tokens)
        except (httpx.HTTPError, json.JSONDecodeError) as e:
            # 한 번의 실패로 창이 닫히지 않게 — 메시지 보여주고 계속
            print(f"\n[호출 실패: {type(e).__name__}: {e}]")
            messages.pop()
            continue
        messages.append({"role": "assistant", "content": answer})


def main() -> None:
    parser = argparse.ArgumentParser(description="GPU 서버에 텍스트 전송 (인자 없으면 대화형)")
    parser.add_argument("text", nargs="*", help="보낼 텍스트 (없으면 대화형 또는 stdin)")
    parser.add_argument("--system", default=None, help="system 메시지 (선택)")
    parser.add_argument("--max-tokens", type=int, default=2048)
    parser.add_argument("--stream", action="store_true", help="한 번 실행에서도 토큰 즉시 출력")
    parser.add_argument("--raw", action="store_true", help="응답 JSON 전체 출력 (non-stream)")
    parser.add_argument("--chat", action="store_true", help="대화형 모드 강제 진입")
    args = parser.parse_args()

    cfg = load_llm_config(SETTINGS.analysis_llm.config)
    if not SETTINGS.analysis_llm.api_key:
        raise SystemExit("ROBO_LLM_API_KEY 가 .env 에 설정되지 않음")
    url = (cfg.api_base or "").rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {SETTINGS.analysis_llm.api_key}"}

    text = " ".join(args.text).strip()

    # 인자도 파이프도 없으면 대화형 — "딸각" 실행의 기본 경로
    if args.chat or (not text and sys.stdin.isatty()):
        _chat_loop(cfg, url, headers, args.system, args.max_tokens)
        return

    if not text and not sys.stdin.isatty():
        text = sys.stdin.read().strip()
    if not text:
        raise SystemExit("보낼 텍스트가 없음 — 인자 또는 stdin 으로 전달")

    messages = []
    if args.system:
        messages.append({"role": "system", "content": args.system})
    messages.append({"role": "user", "content": text})

    print(f"[{cfg.model} @ {url}]", file=sys.stderr)

    if args.stream:
        _stream_chat(cfg, url, headers, messages, args.max_tokens)
        return

    resp = httpx.post(
        url, json=_build_body(cfg, messages, args.max_tokens, stream=False),
        headers=headers, timeout=cfg.timeout_seconds, verify=cfg.verify_ssl,
    )
    resp.raise_for_status()
    data = resp.json()
    if args.raw:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    print(data["choices"][0]["message"]["content"])
    usage = data.get("usage") or {}
    print(
        f"[input {usage.get('prompt_tokens')} tok / output {usage.get('completion_tokens')} tok]",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
