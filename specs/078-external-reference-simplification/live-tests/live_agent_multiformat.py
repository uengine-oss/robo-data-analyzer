"""078 외부 Agent 범용성 라이브 테스트 — 서로 다른 계열 4형식을 실 LLM 으로.

실행: .venv 의 python 으로 리포 어디서든
`python specs/078-external-reference-simplification/live-tests/live_agent_multiformat.py`.
로컬 GPU LLM(.env 의 ROBO_LLM_CONFIG)을 실제 호출하므로 unit suite 에는 넣지 않는다.

형식마다 train/held-out 2파일. 한 프로젝트에 모두 넣어 형식 간 상호 negative 가
자동 적용된다. 각 형식에 대해: skill 승격 → 청크 slice-exact → 대표 key 링킹 확인.
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
from pipeline.steps.step5_linking.link_external.linker import link_external  # noqa: E402
from graph.schema.graph_nodes import CodeNode  # noqa: E402
from shared.files.cache import SourceFileCache  # noqa: E402

FILES = {
    # 1) 줄 레코드 (세미콜론)
    "orders.ifd": "* 인터페이스 정의\nTRX=ORD0001; NAME=주문생성; IO=IN;  LEN=120\nTRX=STK0101; NAME=재고조회; IO=OUT; LEN=200\n",
    "payments.ifd": "* 인터페이스 정의\nTRX=PAY0001; NAME=결제승인; IO=IN;  LEN=160\nTRX=PAY0002; NAME=결제취소; IO=IN;  LEN=160\n",
    # 2) 구획 블록 (BEGIN/END, 멀티라인)
    "batch_a.blkdef": (
        "-- 배치 정의\n"
        "BEGIN JOB DAILY_SETTLE\n  SCHEDULE 0300\n  TARGET settle_main\nEND JOB\n"
        "BEGIN JOB DAILY_CLEAN\n  SCHEDULE 0400\n  TARGET tmp_purge\nEND JOB\n"
    ),
    "batch_b.blkdef": (
        "-- 배치 정의\n"
        "BEGIN JOB MONTH_REPORT\n  SCHEDULE 0500\n  TARGET report_gen\n  RETRY 3\nEND JOB\n"
    ),
    # 3) 파이프 구분 표 (헤더 + 값 행)
    "codes_a.tbl": "CODE|LABEL|USE\nGRD01|일반등급|Y\nGRD02|우수등급|Y\n",
    "codes_b.tbl": "CODE|LABEL|USE\nRGN01|서울권역|Y\nRGN09|제주권역|N\n",
    # 4) 비정형 섹션 (::: 헤더)
    "menu_a.sect": ":::MAIN_MENU:::\ntitle=메인\norder=1\n\n:::SUB_MENU:::\ntitle=부가\norder=2\n",
    "menu_b.sect": ":::ADMIN_MENU:::\ntitle=관리\norder=9\n",
}

# 형식별 (링킹에 쓸 대표 key, held-out 파일)
LINK_KEYS = {
    ".ifd": "PAY0001",
    ".blkdef": "MONTH_REPORT",
    ".tbl": "RGN09",
    ".sect": "ADMIN_MENU",
}


async def main() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "multi-format-project"
        root.mkdir()
        for name, content in FILES.items():
            (root / name).write_text(content, encoding="utf-8", newline="\n")
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

        chunks = result["external_chunks"]
        statuses = {f.relpath: (f.status, f.reason) for f in result["project_files"]}
        print("\n===== 결과 요약 =====")
        print("파일 상태:", statuses)
        print("승격 skill:", [p.parent.name for p in skills_dir.glob("*/SKILL.md")])

        by_ext: dict[str, list] = {}
        bad = 0
        for chunk in chunks:
            src = FILES[chunk.file_path]
            if chunk.text != src[chunk.char_start:chunk.char_end]:
                bad += 1
                print(f"[FAIL] slice mismatch: {chunk.file_path} {chunk.char_start}:{chunk.char_end}")
            by_ext.setdefault(Path(chunk.file_path).suffix, []).append(chunk)
        print(f"청크 slice 불일치: {bad}")
        for ext, group in sorted(by_ext.items()):
            keys = sorted({key for chunk in group for key in chunk.keys})
            print(f"  {ext}: {len(group)}청크 keys={keys}")

        print("\n===== 형식별 링킹 검사 =====")
        passed = failed = 0
        for ext, key in LINK_KEYS.items():
            code = CodeNode(
                id=f"code:t:C:m{ext.strip('.')}", signature="m()", file_path="C.java",
                start_line=1, end_line=2, label="METHOD", name="m",
                code_text=f'registry.load("{key}");', owner_id="code:t:C",
            )
            outcome = link_external(
                code_nodes=[code], variables=[], constants=[],
                chunks=chunks, routine_ids={code.id},
            )
            edges = [e for e in outcome.external_edges if e.matched_key == key]
            ok = len(edges) == 1
            passed += ok
            failed += (not ok)
            print(f"  {ext} key={key}: {'PASS' if ok else 'FAIL'} "
                  f"(REFER_TO {len(edges)}건)")
        print(f"\n형식 4종 중 링킹 성공 {passed} / 실패 {failed}")
        print("[MULTI-FORMAT TEST DONE]")


asyncio.run(main())
