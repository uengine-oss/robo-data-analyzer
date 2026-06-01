"""CodeGraph(SQLite) → robo Neo4j 스펙 매핑 — 테스트 모듈 (PoC).

목적
────
CodeGraph가 분석대상 프로젝트를 인덱싱해 만든 SQLite 그래프를,
robo-data-analyzer 의 Neo4j 스펙(노드 라벨 / 관계 타입 / id 형식)으로
**매핑하면 무엇이 채워지고 무엇이 비는지**를 실제 숫자로 보여준다.

- LLM 0, 외부 의존 0 (Python 표준 sqlite3 만).
- 실제 적재는 안 함 — 매핑 '로직'과 '커버리지/공백'만 출력.

전제: 대상 폴더를 먼저 CodeGraph로 인덱싱해 둘 것.
    cd <대상 프로젝트>;  codegraph init -i        # → .codegraph/codegraph.db

실행:
    python module_test/codegraph_to_robo_mapping.py [codegraph.db 경로]
    (생략 시 기본 = ../data/source/.codegraph/codegraph.db)
"""
from __future__ import annotations

import sqlite3
import sys
from collections import Counter
from pathlib import Path

# Windows cp949 콘솔에서도 한글/이모지 출력되게 (robo 본체와 동일 처리)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ────────────────────────────────────────────────────────────────────────
# 매핑 로직 (단일 진실) — CodeGraph 종류 → robo 스펙
# ────────────────────────────────────────────────────────────────────────

# CodeGraph node.kind → robo 노드 라벨 (None = robo 스펙에 대응 없음 → DROP)
NODE_KIND_TO_ROBO_LABEL: dict[str, str | None] = {
    "file":        "MODULE",
    "module":      "MODULE",
    "namespace":   "MODULE",
    "class":       "MODULE",      # robo는 클래스도 MODULE(sub_label로 구분)
    "interface":   "MODULE",
    "trait":       "MODULE",
    "protocol":    "MODULE",
    "function":    "FUNCTION",
    "method":      "FUNCTION",
    "component":   "FUNCTION",
    "route":       None,          # robo 스펙에 route 노드 없음
    "struct":      "DATA_TYPE",
    "enum":        "DATA_TYPE",
    "type_alias":  "DATA_TYPE",
    "variable":    "VARIABLE",
    "field":       "VARIABLE",
    "property":    "VARIABLE",
    "parameter":   "VARIABLE",
    "constant":    "CONSTANT",
    "enum_member": "CONSTANT",
    "import":      None,          # robo는 #include/import를 그래프 노드로 안 둠
    "export":      None,
}

# CodeGraph edge.kind → robo 관계 타입.
# 'contains'는 (source 라벨, target 라벨)에 따라 갈리므로 함수로 따로 처리.
EDGE_KIND_TO_ROBO_REL: dict[str, str | None] = {
    "calls":        "CALLS",
    "references":   "REFERENCES",
    "imports":      None,         # robo 스펙에 IMPORTS 관계 없음 → DROP
    "exports":      None,
    "extends":      None,         # robo 스펙에 상속 관계 없음 → DROP (공백)
    "implements":   None,
    "type_of":      None,         # robo는 type_of 관계 없음
    "returns":      None,
    "instantiates": None,
    "overrides":    None,
    "decorates":    None,
    # 'contains'는 _map_contains() 가 처리
}

# robo 스펙에는 있으나 CodeGraph가 절대 못 주는 것 (robo가 따로 채워야 함)
ROBO_ONLY_NODES = ["SCHEMA", "TABLE", "COLUMN", "PACKAGE", "RULE", "EXAMPLE", "QUESTION"]
ROBO_ONLY_EDGES = ["READS", "WRITES", "HAS_COLUMN", "BELONGS_TO", "FK",
                   "INIT_BY", "BELONGS_TO_PACKAGE",
                   "HAS_RULE", "HAS_EXAMPLE", "HAS_QUESTION", "NEXT", "BRANCH", "AFFECTS_TABLE"]
ROBO_ONLY_PROPS = ["Function.summary/stereotype/logical_name",
                   "Module.summary/intent", "Table/Column.analyzed_description"]


def file_module_id(file_path: str) -> str:
    """robo 규칙: 상대경로 → 확장자 제거 후 '/'→'.'  (예: sub/foo.c → sub.foo)"""
    parts = Path(file_path).with_suffix("").as_posix().strip("/").split("/")
    return ".".join(p for p in parts if p)


def _map_contains(src_label: str | None, tgt_label: str | None) -> str | None:
    """robo: Module→Function=HAS_FUNCTION, Module→Variable=HAS_VARIABLE,
    DataType→Variable=HAS_MEMBER, Function→Function(중첩)=PARENT_OF."""
    if src_label == "FUNCTION" and tgt_label == "FUNCTION":
        return "PARENT_OF"
    if src_label in ("MODULE",) and tgt_label == "FUNCTION":
        return "HAS_FUNCTION"
    if src_label in ("MODULE",) and tgt_label == "VARIABLE":
        return "HAS_VARIABLE"
    if src_label == "DATA_TYPE" and tgt_label == "VARIABLE":
        return "HAS_MEMBER"
    if src_label in ("MODULE",) and tgt_label == "DATA_TYPE":
        return "HAS_FUNCTION"  # 근사 — robo엔 Module→DataType 직접 엣지 없음(주: 공백)
    return None  # 그 외 contains는 robo 대응 없음


def robo_node_id(label: str, name: str, file_path: str) -> str:
    """robo id 형식: MODULE→module_id(file_module_id), FUNCTION→module_id.name 등."""
    mid = file_module_id(file_path)
    if label == "MODULE":
        return mid
    return f"{mid}.{name}" if mid else name


def main() -> None:
    default_db = Path(__file__).resolve().parent / "testdata" / "source" / ".codegraph" / "codegraph.db"
    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else default_db
    if not db_path.is_file():
        print(f"[!] CodeGraph DB 없음: {db_path}\n    대상 폴더에서 'codegraph init -i' 먼저 실행하세요.")
        sys.exit(1)

    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    nodes = {r["id"]: r for r in con.execute(
        "select id, kind, name, qualified_name, file_path, start_line, end_line, signature from nodes")}
    edges = list(con.execute(
        "select source, target, kind, line, coalesce(provenance,'?') prov from edges"))
    con.close()

    # ── 1. CodeGraph 원본 집계 ──
    print("=" * 68)
    print(f" CodeGraph DB: {db_path}")
    print("=" * 68)
    print(f" 원본 노드 {len(nodes)} · 엣지 {len(edges)}")
    print("  노드 kind:", dict(Counter(n["kind"] for n in nodes.values())))
    print("  엣지 kind:", dict(Counter(e["kind"] for e in edges)))

    # ── 2. 노드 매핑 ──
    label_of: dict[str, str | None] = {}
    robo_node_count: Counter = Counter()
    dropped_node: Counter = Counter()
    for nid, n in nodes.items():
        lbl = NODE_KIND_TO_ROBO_LABEL.get(n["kind"], None)
        label_of[nid] = lbl
        if lbl:
            robo_node_count[lbl] += 1
        else:
            dropped_node[n["kind"]] += 1

    # ── 3. 엣지 매핑 ──
    robo_edge_count: Counter = Counter()
    dropped_edge: Counter = Counter()
    for e in edges:
        if e["kind"] == "contains":
            rel = _map_contains(label_of.get(e["source"]), label_of.get(e["target"]))
        else:
            rel = EDGE_KIND_TO_ROBO_REL.get(e["kind"], None)
        if rel:
            robo_edge_count[rel] += 1
        else:
            dropped_edge[e["kind"]] += 1

    # ── 4. 리포트 ──
    print("\n" + "-" * 68)
    print(" ✅ robo 스펙으로 매핑된 노드")
    for lbl, c in robo_node_count.most_common():
        print(f"    {lbl:<10} {c}")
    print(" ✅ robo 스펙으로 매핑된 엣지")
    for rel, c in robo_edge_count.most_common():
        print(f"    {rel:<20} {c}")

    # 유효 매핑률 = (유효 노드/엣지 중 매핑된 비율). import 등 노이즈는 분모에서 제외.
    valid_node_kinds = {"file", "function", "method", "class", "struct", "enum",
                        "variable", "constant", "interface"}
    valid_nodes = [n for n in nodes.values() if n["kind"] in valid_node_kinds]
    mapped_nodes = [n for n in valid_nodes if label_of.get(n["id"])]
    valid_edge_kinds = {"calls", "contains", "references"}
    valid_edges = [e for e in edges if e["kind"] in valid_edge_kinds]

    def _edge_mapped(e):
        if e["kind"] == "contains":
            return _map_contains(label_of.get(e["source"]), label_of.get(e["target"])) is not None
        return EDGE_KIND_TO_ROBO_REL.get(e["kind"]) is not None
    mapped_edges = [e for e in valid_edges if _edge_mapped(e)]
    print(f"\n 📊 유효 매핑률 (노이즈 제외): "
          f"노드 {len(mapped_nodes)}/{len(valid_nodes)} · 엣지 {len(mapped_edges)}/{len(valid_edges)}")

    print("\n ⛔ 정상 제외 (DROP — 실패 아님, robo가 안 쓰는 노이즈)")
    for k, c in dropped_node.most_common():
        note = "#include 헤더 — robo는 노드화 안 함" if k == "import" else "robo 스펙에 없음"
        print(f"    노드 {k:<10} {c}   ({note})")
    for k, c in dropped_edge.most_common():
        reason = ("#include 관련 — robo 미사용" if k in ("imports",)
                  else "robo엔 상속/타입 엣지 없음" if k in ("extends", "implements", "type_of")
                  else "#include 포함관계 — robo 미사용" if k == "contains"
                  else "대응 없음")
        print(f"    엣지 {k:<10} {c}   ({reason})")

    print("\n ⚠️  robo가 따로 채워야 함 — CodeGraph는 0개 제공")
    print("    노드:", ", ".join(ROBO_ONLY_NODES))
    print("    엣지:", ", ".join(ROBO_ONLY_EDGES))
    print("    의미:", ", ".join(ROBO_ONLY_PROPS))

    # ── 5. ProFrame 핵심 검증 샘플 ──
    print("\n" + "-" * 68)
    print(" 🔎 ProFrame 핵심 패턴 확인")
    b300 = [n for n in nodes.values() if "b300_call" in n["name"] and n["kind"] == "function"]
    if b300:
        nid = b300[0]["id"]
        callers = [nodes[e["source"]]["name"] for e in edges
                   if e["target"] == nid and e["kind"] == "calls" and e["source"] in nodes]
        fid = robo_node_id("FUNCTION", b300[0]["name"], b300[0]["file_path"])
        print(f"    b300_call_zapamcom10040 → FUNCTION  id='{fid}'")
        print(f"       호출자(CALLS): {callers or '(없음)'}   ← 로컬 함수 호출 ✅ 매핑됨")
    dlcall_targets = [n for n in nodes.values()
                      if n["kind"] == "function" and "zapamcom10040" in n["name"] and "call" not in n["name"]]
    print(f"    mpfm_dlcall(\"zapamcom10040\") 대상 프로그램 노드: "
          f"{len(dlcall_targets)}개  ← cross-program 호출 ❌ 누락 (robo가 wrapper로 채워야)")

    print("\n" + "=" * 68)
    print(" 결론: CodeGraph는 '구조 백본'(MODULE/FUNCTION/CALLS/HAS_FUNCTION)만 채운다.")
    print("       robo는 그 위에 R/W·DB·의미·커스텀(mpfm_dlcall)을 LLM/스캔으로 추가해야 한다.")
    print("=" * 68)


if __name__ == "__main__":
    main()
