"""진입점: data 폴더 경로 인자, LangGraph 파이프라인 실행 후 출력."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import warnings
from pathlib import Path

# requests/urllib3/chardet 버전 경고 억제 (터미널 난잡함 방지)
warnings.filterwarnings("ignore", message=".*doesn't match a supported version.*")

from exp.config import get_data_dir
from exp.graph import build_in_memory_graph
from exp.pipeline import run as pipeline_run
from exp.neo4j import ExpNeo4jWriter

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description="PRD 실험용 파이프라인: data 폴더 분석 (LangGraph)")
    parser.add_argument(
        "--data-dir",
        default=None,
        help="(무시됨) 실제 분석 경로(settings.path.data_dir)로 고정됨",
    )
    parser.add_argument(
        "--skip-llm",
        action="store_true",
        help="Step6 LLM 비활성화 (규칙만)",
    )
    parser.add_argument(
        "--skip-deepagent",
        action="store_true",
        help="Step5 후 unresolved 시 deepagent 루프 비활성화. DeepAgent 사용 시 .env에 LLM_API_KEY(및 LLM_API_BASE, LLM_MODEL) 필요.",
    )
    parser.add_argument(
        "--output",
        choices=("json", "file", "console", "neo4j"),
        default="neo4j",
        help="출력: neo4j(배치 반영, 기본값), json(한줄), file(저장), console(pretty)",
    )
    parser.add_argument(
        "--output-file",
        default="exp_output.json",
        help="--output file 일 때 저장 경로",
    )
    parser.add_argument(
        "--no-pathlib",
        action="store_true",
        help="pathlib 대신 os.walk 사용",
    )
    parser.add_argument(
        "--neo4j-batch-size",
        type=int,
        default=None,
        help="Neo4j 배치 크기 (기본: .env NEO4J_QUERY_BATCH_SIZE)",
    )
    parser.add_argument(
        "--debug-calls",
        action="store_true",
        help="CALLS 파이프라인 디버깅: calls_edges.jsonl, unresolved_calls.jsonl 출력",
    )
    parser.add_argument(
        "--output-calls-dir",
        default=None,
        help="--debug-calls 시 jsonl 저장 디렉터리 (기본: data_dir)",
    )
    parser.add_argument(
        "--output-run-dir",
        default=None,
        help="로그·산출물·청크번들 저장 폴더. 미지정 시 exp/exp_run 사용 (인자 없이 실행해도 여기에 모두 기록).",
    )
    parser.add_argument(
        "--no-artifacts",
        action="store_true",
        help="산출물 덤프 생략. 로그만 파일에 기록(기본 run 디렉터리 또는 --output-run-dir 사용).",
    )
    args = parser.parse_args()

    data_dir = get_data_dir()
    if not Path(data_dir).is_dir():
        logger.error("data 디렉터리가 없습니다: %s", data_dir)
        return 1

    # 인자 없이 실행해도 로그·산출물·청크번들 모두 run_dir에 기록 (기본: exp/exp_run)
    _exp_dir = Path(__file__).resolve().parent
    run_dir = Path(args.output_run_dir).resolve() if getattr(args, "output_run_dir", None) else _exp_dir / "exp_run"
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "exp_run.txt"

    args.run_dir = str(run_dir)

    # 콘솔: 짧은 태그 포맷. 파일: 풀 name 유지(디버깅용)
    root = logging.getLogger()
    for h in root.handlers[:]:
        root.removeHandler(h)
    from exp.log_config import ExpConsoleFormatter, ShortNameFilter
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(ExpConsoleFormatter())
    console_handler.addFilter(ShortNameFilter())
    root.addHandler(console_handler)
    file_handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(levelname)s [%(name)s] %(message)s"))
    root.addHandler(file_handler)

    logger.info("[EXP:RUN] run_dir=%s  로그=%s (모든 산출물·로그 여기 기록)", run_dir, log_path.name)

    logger.info("[EXP:RUN] data_dir 검사 통과  data_dir=%s", data_dir)
    # 인자 없이 실행해도 output=neo4j(기본값)이면 Step 단위로 그때그때 Neo4j에 반영됨.
    # 각 flush마다 asyncio.run()으로 새 루프 사용 → writer는 매번 연결·종료하므로 run.py에서 미리 연결하지 않음.
    neo4j_writer = None
    if args.output == "neo4j":
        batch_size = args.neo4j_batch_size or int(os.environ.get("NEO4J_QUERY_BATCH_SIZE", "30"))
        neo4j_writer = ExpNeo4jWriter(batch_size=batch_size)
        args.neo4j_writer = neo4j_writer
        args.neo4j_batch_size = batch_size
        logger.info("[EXP:RUN] Neo4j writer 준비  batch_size=%d", batch_size)
    state = pipeline_run(data_dir, args)
    logger.info("[EXP:RUN] 파이프라인 완료  state_keys=%s", list(state.keys()))

    # 기본값: 산출물 덤프 실행. --no-artifacts 시에만 생략.
    if not getattr(args, "no_artifacts", False):
        from exp.artifact_dump import dump_artifacts
        dump_artifacts(dict(state), run_dir)

    graph = build_in_memory_graph(
        state["method_facts_list"],
        state["sql_chunks"],
        state["config_chunks"],
        state["refer_to_edges"],
        state["reads_writes"],
        state["fk_edges"],
        state["summary_by_method"],
        calls_edges=state.get("calls_edges_method") or [],
        uses_edges=state.get("uses_edges") or [],
    )

    if args.debug_calls:
        out_calls_dir = Path(args.output_calls_dir or args.run_dir or data_dir)
        out_calls_dir.mkdir(parents=True, exist_ok=True)
        edges_path = out_calls_dir / "calls_edges.jsonl"
        un_path = out_calls_dir / "unresolved_calls.jsonl"
        with open(edges_path, "w", encoding="utf-8") as f:
            for e in state.get("calls_edges_method") or []:
                f.write(json.dumps({"caller_method_id": e.caller_method_id, "callee_method_id": e.callee_method_id, "call_code": (getattr(e, "call_code", None) or "")[:200]}, ensure_ascii=False) + "\n")
        with open(un_path, "w", encoding="utf-8") as f:
            for u in state.get("unresolved_calls") or []:
                f.write(json.dumps(u, ensure_ascii=False) + "\n")
        logger.info("[EXP:DEBUG] CALLS 산출물  %s  %s", edges_path, un_path)

    if args.output == "neo4j":
        # Neo4j 반영은 Step2/Step3/Step5/DeepAgent/Step6에서 그때그때 완료. 파이프라인 끝 일괄 반영 없음.
        logger.info("[EXP:RUN] Neo4j 출력 | Step별 즉시 반영 완료 (추가 일괄 반영 없음)")
    elif args.output == "console":
        logger.info("[EXP:RUN] 출력  output=%s", args.output)
        print(json.dumps(graph.to_dict(), indent=2, ensure_ascii=False))
    elif args.output == "file":
        logger.info("[EXP:RUN] 출력  output=%s", args.output)
        out_path = Path(args.output_file)
        out_path.write_text(json.dumps(graph.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("저장: %s", out_path)
    else:
        logger.info("[EXP:RUN] 출력  output=%s", args.output)
        print(json.dumps(graph.to_dict(), ensure_ascii=False))

    return 0


if __name__ == "__main__":
    sys.exit(main())
