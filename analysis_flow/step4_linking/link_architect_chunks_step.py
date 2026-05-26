"""step4_linking — 아키텍트 청크 링킹 (Framework 전용).

※ 보류 — 이 스텝은 현재 FRAMEWORK_PIPELINE 에서 비활성이다 (계획 §2.8 Issue I).
  XML/구조화파일 링킹 방식이 거칠어 재설계 전까지 미사용. 코드·ArchitectChunk
  노드·REFER_TO 인프라는 휴면 보존 — 되살리려면 analysis_pipelines 에 import·step
  복원. **데드코드 아님 — 삭제 금지.**

구조화 파일(XML mapper 등)을 라이브러리로 파싱 → 키마다 (키, 값) 후보.
각 키를 프로젝트 소스에서 검색 — 어느 함수 코드에 그 키 문자열이 있으면
→ ArchitectChunk 노드 + REFER_TO(함수→청크).

**코드가 실제로 참조하는 청크만 노드화** — 안 쓰이는 키는 버린다.
종류(sql/config) 분기 0개. LLM 0회 — 순수 결정론. 값 본문의 의미 분석은 step6 3분할.
"""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Optional

from langchain_core.runnables import RunnableConfig

from shared.workflow_runner.step_config import step_config

from analysis_flow.step4_linking.stage1_regex_grep.regex_table_linker import FunctionLocator
from data_models.graph_edges import ReferTo
from data_models.graph_nodes import ArchitectChunk, Function
from shared.logging_and_stream.logger import log_info, log_warning
from shared.neo4j.edges.refer_to import upsert_refer_to
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.nodes.architect_chunk import upsert_architect_chunks

_STEP = "step4.link_architect_chunks"

# 키-값 파서 대상 — 구조화 파일 확장자.
_STRUCTURED_EXTS: frozenset[str] = frozenset({".xml"})


async def link_architect_chunks_step(
    state: dict, config: Optional[RunnableConfig] = None,
) -> dict:
    """구조화 파일 → ArchitectChunk + REFER_TO (코드가 참조하는 키만)."""
    cfg = step_config(config)
    project = cfg.get("project")
    neo: Neo4jClient | None = cfg.get("neo_client")
    if project is None:
        log_warning(_STEP, "config_missing", "project 누락 — step skip")
        return {}

    functions = state.get("functions", [])
    started = time.monotonic()
    log_info(_STEP, "start", project=project.name, functions=len(functions))
    chunks, refers = _link(Path(project.root), functions)

    if neo is not None:
        if chunks:
            await upsert_architect_chunks(neo, chunks)
        if refers:
            await upsert_refer_to(neo, refers)

    log_info(
        _STEP, "done",
        architect_chunks=len(chunks), refer_to_edges=len(refers),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {"architect_chunks": chunks, "refer_to_edges": refers}


def _link(
    project_root: Path, functions: list[Function],
) -> tuple[list[ArchitectChunk], list[ReferTo]]:
    """구조화 파일 파싱 → 키 검색 → 매칭된 청크만 (ArchitectChunk, ReferTo)."""
    # 1. 구조화 파일 파싱 → {chunk_key: (body, 정의 파일)}
    candidates: dict[str, tuple[str, str]] = {}
    for f in sorted(project_root.rglob("*")):
        if f.is_file() and f.suffix.lower() in _STRUCTURED_EXTS:
            candidates.update(_parse_xml(f))
    if not candidates:
        return [], []

    # 2. 각 키를 소스에서 검색 — 단어경계 + 긴 키 우선 (부분매칭 방지).
    locator = FunctionLocator(functions)
    ordered = sorted(candidates, key=len, reverse=True)
    key_regex = re.compile(r"\b(?:" + "|".join(re.escape(k) for k in ordered) + r")\b")
    # chunk_key → {function_id: (evidence_file, evidence_line)} — 함수당 첫 등장.
    hits: dict[str, dict[str, tuple[str, int]]] = defaultdict(dict)
    for src in sorted(project_root.rglob("*")):
        if not src.is_file() or src.suffix.lower() in _STRUCTURED_EXTS:
            continue   # 구조화 파일 자체(키 정의처)는 검색 대상 X
        try:
            text = src.read_text(encoding="utf-8", errors="ignore")
        except (OSError, UnicodeDecodeError):
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            for m in key_regex.finditer(line):
                key = m.group(0)
                fid = locator.locate(str(src), line_no)
                if fid and fid not in hits[key]:
                    hits[key][fid] = (str(src), line_no)

    # 3. 매칭된 키만 노드화 + REFER_TO (안 쓰이는 키는 버림).
    chunks: list[ArchitectChunk] = []
    refers: list[ReferTo] = []
    for key, fn_hits in hits.items():
        body, def_file = candidates[key]
        chunks.append(ArchitectChunk(chunk_key=key, body=body, file_path=def_file))
        for fid, (ev_file, ev_line) in fn_hits.items():
            refers.append(ReferTo(
                function_id=fid,
                target_type="architect_chunk",
                target_key=key,
                evidence_file=ev_file,
                evidence_line=ev_line,
            ))
    return chunks, refers


def _parse_xml(xml_path: Path) -> dict[str, tuple[str, str]]:
    """XML 파일 → {chunk_key: (value_text, file_path)}.

    ``id`` 속성이 있는 모든 엘리먼트 = 청크. 루트에 ``namespace`` 가 있으면 키 prefix.
    태그 종류(select/insert/sql 등)는 안 따진다.
    """
    try:
        root = ET.parse(xml_path).getroot()
    except (ET.ParseError, OSError) as e:
        log_warning(
            _STEP, "parse_xml", "XML 파싱 실패 — 건너뜀",
            file=str(xml_path), error=f"{type(e).__name__}: {e}",
        )
        return {}
    namespace = (root.get("namespace") or "").strip()
    out: dict[str, tuple[str, str]] = {}
    for elem in root.iter():
        eid = (elem.get("id") or "").strip()
        if not eid:
            continue
        key = f"{namespace}.{eid}" if namespace else eid
        body = " ".join("".join(elem.itertext()).split())
        out[key] = (body, str(xml_path))
    return out
