"""외부 CLI 에이전트(Claude Code / Codex)를 헤드리스로 한 번 돌린다.

## 왜 이것이 필요한가

분석기의 의미 추출은 지금 YAML 프롬프트로 함수마다 LLM 을 부른다. 함수가 52개면 규칙 수만큼
호출이 곱해지고, 로컬 모델(실측: 호출당 40~90초)에서는 **몇 시간**이 걸린다. 사용자는
「분석 시작」을 누르고 그만큼 기다려야 한다.

에이전트에게 **작업 폴더와 지시문 하나**를 주면, 스스로 파일을 골라 읽고 한 번에 끝낸다.
읽을 것을 고르는 판단이 프롬프트 밖으로 나가고, 호출 수를 우리가 정하지 않게 된다.

## 왜 MCP 가 아니라 파일인가

spec 007 AD-6 이 정한 대로다 — cliagents 경로에서는 **MCP 도구 정의가 캐시 프리픽스에 얹혀
캐시를 오염시킨다.** 에이전트는 자기 파일 도구로 작업 폴더를 읽고, 결과를 JSON 파일 하나로
남긴다. 그래프에 쓰는 일은 분석기가 한다 — **쓰기 주체는 여전히 하나**다(헌법 원칙 III).

## 지시문은 SKILL.md 하나가 원천이다

여기서 지시문 문장을 조립하지 않는다. 넘기는 것은 **값**(작업 폴더 경로)뿐이다.
헌법의 *"지시문과 값을 섞지 않는다"* 규칙 그대로다.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent.parent / "skills" / "metadata-augment"
OUTPUT_NAME = "augment.json"

#: 에이전트 한 번의 상한. 넘으면 죽인다 — 무한히 기다리면 사용자에게는 "멈춘 앱"이다.
TURN_TIMEOUT_SECONDS = 1800


@dataclass(frozen=True)
class Runner:
    """CLI 에이전트 하나를 헤드리스로 부르는 방법."""

    name: str
    executable: str
    #: 고정 argv. **프롬프트는 여기 넣지 않는다** — 표준입력으로 보낸다.
    argv: tuple[str, ...]


#: 지원하는 에이전트. `EXTERNAL_AGENT_ID` 로 고르고, 없으면 PATH 에서 먼저 찾는 것을 쓴다.
#:
#: 두 에이전트 모두 **비대화형 단발 실행**이어야 한다. 대화형으로 뜨면 서버 프로세스가
#: 입력을 기다리며 영원히 멈춘다.
#:
#: ## 프롬프트를 argv 가 아니라 표준입력으로 보내는 이유
#:
#: 지시문은 SKILL.md 본문이고, 그 파일은 YAML front matter 때문에 **`---` 로 시작한다.**
#: argv 로 넘기면 CLI 파서가 그걸 옵션으로 읽는다 — 실측(2026-08-05):
#: `error: unknown option '---`, exit=1. 프롬프트 내용에 따라 깨지는 방식이라 더 나쁘다.
#: 표준입력에는 그런 해석이 없다.
RUNNERS: dict[str, Runner] = {
    # `-p` 가 헤드리스 단발.
    #
    # `--allowedTools` 가 **반드시 필요하다.** `--permission-mode acceptEdits` 만 주면
    # 에이전트가 파일을 쓰지 않고 결과를 표준출력에 뱉고 끝난다(2026-08-05 실측: exit=1,
    # augment.json 없음, JSON 은 stdout 에). 도구를 허용 목록으로 명시해야 실제로 쓴다.
    #
    # 허용 범위를 **읽기·쓰기·검색으로 한정**한다. Bash 를 주지 않는 이유는 이 스킬이
    # 셸을 필요로 하지 않기 때문이다 — 필요 없는 능력을 주지 않는 것이 기본이고,
    # 작업 폴더가 사본이라 최악의 경우도 이 run 안에서 끝난다.
    "claude-code": Runner(
        name="claude-code",
        executable="claude",
        argv=(
            "-p",
            "--allowedTools", "Read", "Write", "Glob", "Grep",
            "--permission-mode", "acceptEdits",
        ),
    ),
    # `-` 가 "프롬프트를 표준입력에서 읽어라" 다.
    "codex": Runner(
        name="codex",
        executable="codex",
        argv=("exec", "--skip-git-repo-check", "-"),
    ),
}


class AgentUnavailable(RuntimeError):
    """쓸 수 있는 CLI 에이전트가 없다."""


def pick_runner(preferred: str | None = None) -> Runner:
    """쓸 에이전트를 고른다. 없으면 **조용히 폴백하지 않고** 던진다.

    폴백하지 않는 이유: 사용자가 `cliagents` 를 골랐는데 말없이 옛 경로로 돌아가면,
    느린 이유도 결과가 다른 이유도 알 수 없게 된다.
    """
    order = [preferred] if preferred else []
    order += [k for k in RUNNERS if k != preferred]
    tried = []
    for key in order:
        runner = RUNNERS.get(key or "")
        if runner is None:
            continue
        tried.append(runner.executable)
        if shutil.which(runner.executable):
            return runner
    names = " 또는 ".join(dict.fromkeys(tried)) or "claude / codex"
    # 컨테이너 안이면 원인이 "설치를 안 했다" 가 아니라 **구조**다. 그 사실을 말해 주지
    # 않으면 사용자가 호스트에 CLI 를 깔아 놓고도 같은 오류를 계속 보게 된다.
    # (2026-08-09 분석기가 호스트 프로세스에서 컨테이너로 옮겨 오면서 생긴 경계다.)
    if Path("/run/.containerenv").exists() or Path("/.dockerenv").exists():
        raise AgentUnavailable(
            f"CLI 에이전트({names})는 컨테이너 안에서 쓸 수 없습니다 — 호스트에 설치된 "
            "명령이라 분석기 컨테이너의 PATH 에 없습니다.\n"
            "ROBO_ANALYZE_ENGINE 을 기본값(controller)으로 두거나, 스킬 기반 분석이 필요하면 "
            "분석기를 호스트 프로세스로 실행하세요."
        )
    raise AgentUnavailable(
        f"CLI 에이전트를 찾지 못했습니다 — PATH 에 {names} 가 있어야 합니다."
    )


def _prompt(workdir: Path) -> str:
    """에이전트에게 줄 지시문 = 스킬 본문 + 작업 폴더.

    스킬 본문을 그대로 싣는 이유: Claude Code 는 `~/.claude/skills` 를, Codex 는 또 다른
    곳을 본다. 설치 위치를 맞추는 대신 본문을 넘기면 **어느 에이전트든 같은 지시문**을 받는다
    (spec 004 의 "같은 프롬프트 파일" 규칙).
    """
    body = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    return (
        f"{body}\n\n---\n\n"
        f"작업 폴더: {workdir}\n"
        f"이 폴더 안에서만 읽고 쓴다. 결과는 {workdir / OUTPUT_NAME} 하나로 남긴다.\n"
    )


def run(workdir: Path, *, preferred: str | None = None, timeout: int = TURN_TIMEOUT_SECONDS) -> dict:
    """에이전트를 한 번 돌리고 ``augment.json`` 을 읽어 돌려준다.

    예외를 삼키지 않는다. 이 단계가 실패하면 증강 결과가 비는데, 그걸 조용히 넘기면
    "증강했는데 아무것도 안 나온다"로만 보인다 — 실제로 그 증상을 한 번 겪었다.
    """
    runner = pick_runner(preferred)
    out_path = workdir / OUTPUT_NAME
    # 이전 실행의 결과가 남아 있으면 이번 실패를 성공으로 오독한다.
    out_path.unlink(missing_ok=True)

    argv = [runner.executable, *runner.argv]
    env = dict(os.environ)
    # 편집기가 자기 자신을 Electron 으로 띄우는 것을 막는다(스튜디오 셸과 같은 이유).
    env.pop("ELECTRON_RUN_AS_NODE", None)

    proc = subprocess.run(
        argv,
        cwd=str(workdir),
        env=env,
        input=_prompt(workdir),  # argv 가 아니라 표준입력 — RUNNERS 주석 참고
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    if not out_path.exists():
        tail = (proc.stderr or proc.stdout or "").strip()[-800:]
        raise RuntimeError(
            f"{runner.name} 가 {OUTPUT_NAME} 을 만들지 않았습니다 "
            f"(exit={proc.returncode}).\n{tail}"
        )

    try:
        data = json.loads(out_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{OUTPUT_NAME} 이 JSON 이 아닙니다: {exc}") from exc

    if not isinstance(data, dict):
        raise RuntimeError(f"{OUTPUT_NAME} 의 최상위는 객체여야 합니다 (받은 것: {type(data).__name__})")

    data.setdefault("tables", [])
    data.setdefault("relations", [])
    data["_agent"] = runner.name
    data["_stdout_tail"] = (proc.stdout or "")[-2000:]
    return data
