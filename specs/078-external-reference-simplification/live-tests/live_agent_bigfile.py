"""078 큰 파일 라이브 테스트 — 미리보기(4KB) 밖에서 구조가 바뀌는 파일.

실행: .venv 의 python 으로 리포 어디서든
`python specs/078-external-reference-simplification/live-tests/live_agent_bigfile.py`.
로컬 GPU LLM(.env 의 ROBO_LLM_CONFIG)을 실제 호출하므로 unit suite 에는 넣지 않는다.

train: 앞 ~25KB 구조 A(REC 라인) + 뒤 구조 B(EVT 라인).
held-out: 구조 B 만. → B 규칙 없이는 held-out 게이트에서 승격 불가.
결정론 마이너가 전문을 스캔하므로 두 구조의 후보를 모두 제공해야 성공한다.
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

REC_LINES = "".join(f"REC=ITM{i:04d}; QTY={i % 97 + 1}; LOC=WH{i % 9 + 1}\n" for i in range(700))
EVT_LINES_TRAIN = "".join(f"EVT ORD{i:04d} DONE\n" for i in range(60))
TRAIN = "# 재고 스냅샷\n" + REC_LINES + "# 이벤트 로그 구간\n" + EVT_LINES_TRAIN
HELDOUT = "# 이벤트 로그 구간\n" + "".join(f"EVT PAY{i:04d} DONE\n" for i in range(40))


async def main() -> None:
    print(f"train={len(TRAIN):,}자 (구조 전환점 ~{len(REC_LINES):,}자), heldout={len(HELDOUT):,}자")
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "bigfile-project"
        root.mkdir()
        # train 배정은 크기 기준(큰 파일이 train)이므로 이름 순서와 무관하다.
        (root / "snapshot.rlog").write_text(TRAIN, encoding="utf-8", newline="\n")
        (root / "events.rlog").write_text(HELDOUT, encoding="utf-8", newline="\n")
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
        sources = {"snapshot.rlog": TRAIN, "events.rlog": HELDOUT}
        bad = sum(
            1 for c in chunks
            if c.text != sources[c.file_path][c.char_start:c.char_end]
        )
        rec = [c for c in chunks if c.text.startswith("REC=")]
        evt = [c for c in chunks if c.text.startswith("EVT ")]
        heldout_evt = [c for c in evt if c.file_path == "events.rlog"]
        print("\n===== 결과 =====")
        print("상태:", statuses)
        print(f"청크 {len(chunks)}개 · slice 불일치 {bad} · REC {len(rec)} · EVT {len(evt)} "
              f"(held-out EVT {len(heldout_evt)})")
        promoted = statuses.get("snapshot.rlog", ("", ""))[0] == "analyzed" \
            and statuses.get("events.rlog", ("", ""))[0] == "analyzed"
        both = bool(rec) and bool(heldout_evt) and bad == 0
        print("[BIGFILE PASS]" if promoted and both else "[BIGFILE FAIL]",
              f"promoted={promoted} 두구조모두={both}")


asyncio.run(main())
