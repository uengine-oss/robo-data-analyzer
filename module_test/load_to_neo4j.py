"""CodeGraph(SQLite) → Neo4j 적재 — robo 스펙과 '똑같이' (DB명: test).

- 노드: :MODULE {module_id}, :FUNCTION {function_id}
- 엣지: CALLS(Function→Function), HAS_FUNCTION(Module→Function)
- MERGE 쿼리·속성키·제약·wipe 전부 robo 코드 그대로 복제.
- 매핑 로직은 codegraph_to_robo_mapping.py 재사용(단일 진실).

실행 (robo venv):
    cd robo-data-analyzer
    .venv/Scripts/python module_test/load_to_neo4j.py
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
from codegraph_to_robo_mapping import (  # noqa: E402
    NODE_KIND_TO_ROBO_LABEL, EDGE_KIND_TO_ROBO_REL, _map_contains,
    file_module_id, robo_node_id,
)
from neo4j import GraphDatabase  # noqa: E402

# ── 접속정보: robo .env 에서 읽기(수정 X, 읽기만). 비밀번호 하드코딩 금지(git 노출 방지) ──
def _env(key: str, default: str = "") -> str:
    v = os.environ.get(key)
    if v:
        return v
    envf = Path(__file__).resolve().parent.parent / ".env"   # robo-data-analyzer/.env
    if envf.is_file():
        for line in envf.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and line.startswith(key + "="):
                return line.split("=", 1)[1].strip()
    return default

URI = _env("ROBO_NEO4J_URI", "bolt://127.0.0.1:7687")
USER = _env("ROBO_NEO4J_USER", "neo4j")
PWD = _env("ROBO_NEO4J_PASSWORD", "")
DB = "test"   # robo 본 DB는 neo4j, 테스트는 test (.env 의 ROBO_NEO4J_DATABASE 안 덮어씀)

# ── robo 스키마/Cypher 그대로 복제 ──
CONSTRAINTS = [
    "CREATE CONSTRAINT function_id IF NOT EXISTS FOR (f:FUNCTION) REQUIRE f.function_id IS UNIQUE",
    "CREATE CONSTRAINT module_id IF NOT EXISTS FOR (m:MODULE) REQUIRE m.module_id IS UNIQUE",
]
INDEXES = ["CREATE INDEX function_file_path IF NOT EXISTS FOR (f:FUNCTION) ON (f.file_path)"]

MERGE_MODULE = """
UNWIND $items AS item
MERGE (m:MODULE {module_id: item.module_id})
SET m.name=item.name, m.file_path=item.file_path,
    m.start_line=item.start_line, m.end_line=item.end_line,
    m.comment=item.comment, m.annotations=item.annotations
"""
MERGE_FUNCTION = """
UNWIND $items AS item
MERGE (f:FUNCTION {function_id: item.function_id})
SET f.signature=item.signature, f.file_path=item.file_path,
    f.start_line=item.start_line, f.end_line=item.end_line,
    f.module_id=item.module_id, f.package_id=item.package_id,
    f.comment=item.comment, f.return_type=item.return_type, f.params=item.params
"""
MERGE_CALLS = """
UNWIND $items AS item
MATCH (caller:FUNCTION {function_id: item.caller_function_id})
MATCH (callee:FUNCTION {function_id: item.callee_function_id})
MERGE (caller)-[r:CALLS]->(callee) SET r.scope=item.scope
"""
MERGE_HAS_FUNCTION = """
UNWIND $items AS item
MATCH (m:MODULE {module_id: item.module_id})
MATCH (f:FUNCTION {function_id: item.function_id})
MERGE (m)-[:HAS_FUNCTION]->(f)
"""


def build_from_codegraph(db_path: Path):
    con = sqlite3.connect(str(db_path)); con.row_factory = sqlite3.Row
    nodes = {r["id"]: r for r in con.execute(
        "select id,kind,name,file_path,start_line,end_line,signature,docstring from nodes")}
    edges = list(con.execute("select source,target,kind from edges")); con.close()

    label_of, fid_of = {}, {}
    modules, functions = [], []
    for nid, n in nodes.items():
        lbl = NODE_KIND_TO_ROBO_LABEL.get(n["kind"]); label_of[nid] = lbl
        if lbl == "MODULE":
            mid = file_module_id(n["file_path"])
            modules.append({"module_id": mid, "name": n["name"], "file_path": n["file_path"],
                            "start_line": 0, "end_line": 0,
                            "comment": n["docstring"] or "", "annotations": ""})
        elif lbl == "FUNCTION":
            fid = robo_node_id("FUNCTION", n["name"], n["file_path"]); fid_of[nid] = fid
            functions.append({"function_id": fid, "signature": n["signature"] or n["name"],
                              "file_path": n["file_path"], "start_line": n["start_line"],
                              "end_line": n["end_line"], "module_id": file_module_id(n["file_path"]),
                              "package_id": "", "comment": n["docstring"] or "",
                              "return_type": "", "params": ""})

    calls, has_fn = [], []
    for e in edges:
        if e["kind"] == "calls" and e["source"] in fid_of and e["target"] in fid_of:
            calls.append({"caller_function_id": fid_of[e["source"]],
                          "callee_function_id": fid_of[e["target"]], "scope": "internal"})
        elif e["kind"] == "contains" and _map_contains(label_of.get(e["source"]), label_of.get(e["target"])) == "HAS_FUNCTION":
            has_fn.append({"module_id": file_module_id(nodes[e["source"]]["file_path"]),
                           "function_id": fid_of.get(e["target"])})
    has_fn = [h for h in has_fn if h["function_id"]]
    return modules, functions, calls, has_fn


def main():
    default_db = Path(__file__).resolve().parent / "testdata" / "source" / ".codegraph" / "codegraph.db"
    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else default_db
    modules, functions, calls, has_fn = build_from_codegraph(db_path)
    print(f"매핑: MODULE {len(modules)} · FUNCTION {len(functions)} · CALLS {len(calls)} · HAS_FUNCTION {len(has_fn)}")

    driver = GraphDatabase.driver(URI, auth=(USER, PWD))
    # 1) DB 'test' 생성 (Neo4j Desktop/Enterprise) — 이미 있으면 통과
    try:
        with driver.session(database="system") as s:
            s.run("CREATE DATABASE test IF NOT EXISTS WAIT")
        print("DB 'test' 준비 완료")
    except Exception as ex:
        print(f"[!] DB 생성 스킵({type(ex).__name__}) — 기존 'test' 사용 시도")

    with driver.session(database=DB) as s:
        for stmt in CONSTRAINTS + INDEXES:
            s.run(stmt)
        s.run("MATCH (n) DETACH DELETE n")          # robo wipe_graph
        s.run(MERGE_MODULE, items=modules)
        s.run(MERGE_FUNCTION, items=functions)
        s.run(MERGE_CALLS, items=calls)
        s.run(MERGE_HAS_FUNCTION, items=has_fn)
        # 검증
        cnt = lambda q: s.run(q).single()[0]
        print("\n[Neo4j 'test' 적재 확인]")
        print("  :MODULE   =", cnt("MATCH (m:MODULE) RETURN count(m)"))
        print("  :FUNCTION =", cnt("MATCH (f:FUNCTION) RETURN count(f)"))
        print("  CALLS     =", cnt("MATCH ()-[r:CALLS]->() RETURN count(r)"))
        print("  HAS_FUNCTION =", cnt("MATCH ()-[r:HAS_FUNCTION]->() RETURN count(r)"))
        sample = s.run("MATCH (a:FUNCTION)-[:CALLS]->(b:FUNCTION) "
                       "WHERE b.function_id CONTAINS 'b300_call' RETURN a.function_id, b.function_id LIMIT 3").values()
        print("  샘플 CALLS:", sample)
    driver.close()
    print("\n완료 — Neo4j Browser에서 :use test 후  MATCH (n) RETURN n  로 확인")


if __name__ == "__main__":
    main()
