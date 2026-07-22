"""078 단일 파일 라이브 테스트 — 같은 형식 파일이 1개뿐일 때 파일 내 held-out 승격.

실행: .venv 의 python 으로 리포 어디서든
`python specs/078-external-reference-simplification/live-tests/live_agent_singlefile.py`.
로컬 GPU LLM(.env 의 ROBO_LLM_CONFIG)을 실제 호출하므로 unit suite 에는 넣지 않는다.

시나리오 2개를 한 프로젝트에 넣는다:
- codes.tbx 1개(반복 60줄): 앞 70%만 관찰 → 미관측 뒷부분에서 검증 → 승격되어야 함.
- memo.ftxt 1개(반복 없는 자유서식): 분할 기준 미달 → 정직하게 미분석이어야 함.
skill 저장소는 temp dir 로 patch — 제품 skill 카탈로그를 오염시키지 않는다.
"""
import asyncio
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))
from dotenv import load_dotenv

load_dotenv(REPO_ROOT / ".env")

import asyncio as aio  # noqa: E402

from llm.usage.cost_budget import CostBudget  # noqa: E402
from pipeline.execution.pause_controller import PauseController  # noqa: E402
from pipeline.steps.step3_load_external.load_external_step import load_external_step  # noqa: E402
from pipeline.steps.step4_scan_project.scan_external_step import scan_external_step  # noqa: E402
from shared.files.cache import SourceFileCache  # noqa: E402

REGIONS = ["서울", "부산", "대구", "광주", "대전", "울산", "세종", "인천", "경기", "강원"]
TABLE = "CODE|LABEL|USE\n" + "".join(
    f"CD{i:03d}|{REGIONS[i % 10]}지점{i}|{'Y' if i % 7 else 'N'}\n" for i in range(60)
)
MEMO = (
    "운영팀 인수인계 메모입니다.\n\n"
    "지난 분기에는 정산 지연이 두 번 있었고 원인은 서로 달랐습니다.\n"
    "다음 담당자는 야간 배치 로그를 먼저 확인하세요.\n"
)


async def main() -> None:
    print(f"table={len(TABLE):,}자(반복 60줄, 파일 1개) / memo={len(MEMO):,}자(자유서식, 파일 1개)")
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "single-file-project"
        root.mkdir()
        (root / "codes.tbx").write_text(TABLE, encoding="utf-8", newline="\n")
        (root / "memo.ftxt").write_text(MEMO, encoding="utf-8", newline="\n")
        skills_dir = Path(temp) / "skills"
        run_dir = Path(temp) / "run"
        run_dir.mkdir()

        cfg = {"configurable": {
            "codebase": SimpleNamespace(root=root),
            "source_cache": SourceFileCache(),
            "pause": PauseController(),
            "neo_client": None,
            "stream": None,
            "run": SimpleNamespace(run_dir=run_dir),
            "cost_budget": CostBudget(),
            "llm_semaphore": aio.Semaphore(7),
        }}
        state = await load_external_step({}, cfg)
        with patch(
            "pipeline.steps.step4_scan_project.scan_external_step.EXTERNAL_SKILLS_DIR",
            skills_dir,
        ):
            result = await scan_external_step(state, cfg)

        statuses = {f.relpath: (f.status, f.reason) for f in result["project_files"]}
        chunks = result["external_chunks"]
        bad = sum(1 for c in chunks if c.text != TABLE[c.char_start:c.char_end])
        keys = sorted(key for c in chunks for key in c.keys)
        # 승격 근거가 된 미관측 구간(뒤 30%, CD042 이후)의 키가 실제로 청크화됐는지.
        tail_keys = [k for k in keys if k >= "CD042"]
        print("\n===== 결과 =====")
        print("상태:", statuses)
        print(f"청크 {len(chunks)}개 · slice 불일치 {bad} · 키 예시 {keys[:3]}...{keys[-3:] if keys else []}")
        print(f"미관측 구간 키 {len(tail_keys)}개 (기대 18)")
        table_ok = statuses.get("codes.tbx", ("", ""))[0] == "analyzed" \
            and len(chunks) == 60 and bad == 0 and len(tail_keys) == 18
        memo_ok = statuses.get("memo.ftxt") == ("unanalyzed", "no_held_out_evidence")
        print("[SINGLE-FILE PASS]" if table_ok and memo_ok else "[SINGLE-FILE FAIL]",
              f"table_ok={table_ok} memo_ok={memo_ok}")


asyncio.run(main())
