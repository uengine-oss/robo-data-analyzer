"""Neo4j export JSON → 마크다운 분석 보고서 생성기.

Neo4j 그래프의 모든 노드·관계·속성을 누락 없이 읽어서,
LLM 없이 순수 Python으로 마크다운 분석보고서를 생성한다.

설계 원칙:
- 단일 고정 템플릿: 전략(Framework/DBMS) 무관, 데이터 유무 무관
- 파싱 가능: 헤딩 계층·테이블 컬럼이 100% 결정적(deterministic)
- 중복 제거: 같은 정보는 한 곳에만
- 가독성: 사람이 읽기 편한 구조
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════
# 라벨 상수 — Neo4j 노드 라벨 분류
# ═══════════════════════════════════════════════════════════════════════

# ── 범위(Scope) ──
SCOPE_LABELS = {"INTERNAL", "EXTERNAL"}

# ── 1차 라벨: 역할 기반 (Framework) ──
MODULE_LABELS = {"MODULE"}
PRIMARY_LABELS = {"MODULE", "VARIABLE", "CONSTANT"}
# ── 1차 라벨: DBMS 고유 (역할 매핑 없이 직접 사용) ──
DBMS_MODULE_LABELS = {"PROCEDURE", "FUNCTION", "TRIGGER"}

# ── DML 라벨 ──
DML_LABELS = {
    "SELECT", "INSERT", "UPDATE", "DELETE", "MERGE",
    "EXECUTE_IMMEDIATE", "FETCH", "CTE", "OPEN_CURSOR", "CREATE_TEMP_TABLE",
}

# ── 서브레이블: ontology_roles.yaml AST 타입 ──
SUB_LABELS = {
    "CLASS", "INTERFACE", "ENUM", "FILE", "STRUCT", "UNION",       # module
    "METHOD", "CONSTRUCTOR",                                        # function
    "FIELD", "MEMBER", "GLOBAL_VARIABLE",                           # variable
    "DEFINE",                                                       # constant
}

# ── 스테레오타입 라벨 (common + oop) ──
STEREOTYPE_LABELS = {
    "Init", "Query", "Command", "Handler", "Validation", "BusinessLogic",
    "Endpoint", "Getter", "Setter", "Lifecycle", "Scheduled", "Transactional",
}

# ── 통계 제외용 보조 라벨 ──
_SKIP_COUNT_LABELS = SCOPE_LABELS | {"EXTERNAL"}

# ═══════════════════════════════════════════════════════════════════════
# 관계 타입 상수 — 용도별 그룹
# ═══════════════════════════════════════════════════════════════════════

EXCLUDED_REL_TYPES = {"NEXT", "PARENT_OF"}

STRUCTURAL_REL_TYPES = {"CONTAINS", "HAS_FUNCTION", "HAS_VARIABLE", "BELONGS_TO_PACKAGE", "CONTAINS_PACKAGE", "HAS_COLUMN", "HAS_GWT"}
UML_REL_TYPES = {"EXTENDS", "IMPLEMENTS", "ASSOCIATION", "COMPOSITION", "AGGREGATION", "DEPENDENCY"}
ETL_REL_TYPES = {"ETL_READS", "ETL_WRITES", "DATA_FLOWS_TO"}

ENTITY_STRUCT_REL_TYPES = UML_REL_TYPES | ETL_REL_TYPES       # 모듈 상세: 구조 관계
ENTITY_CALL_REL_TYPES = {"CALLS", "USES", "CALL"}              # 모듈 상세: 호출 관계
ENTITY_DATA_REL_TYPES = {"FROM", "READS", "WRITES", "REFER_TO", "REFERS_TO"}  # 모듈 상세: 데이터 접근

# ═══════════════════════════════════════════════════════════════════════
# 마크다운 테이블 스키마 상수 (헤더 컬럼 정의)
# ═══════════════════════════════════════════════════════════════════════

# -- 개요 섹션 --
SCHEMA_NODE_STATS = ["노드 타입", "개수"]
SCHEMA_REL_STATS = ["관계 타입", "개수"]
SCHEMA_STEREOTYPE = ["스테레오타입", "개수"]

# -- 패키지 섹션 --
SCHEMA_PKG_MEMBERS = ["이름", "타입", "스테레오타입", "범위"]

# -- 모듈 상세 섹션 --
SCHEMA_FUNCTION = ["이름", "시그니처", "반환타입", "수정자", "요약"]
SCHEMA_VARIABLE = ["이름", "타입", "수정자", "요약"]
SCHEMA_STRUCT_REL = ["방향", "관계", "대상", "속성"]
SCHEMA_CALL_REL = ["방향", "대상", "호출코드"]
SCHEMA_DATA_ACCESS = ["대상", "관계", "근거", "신뢰도"]

# -- 외부 의존성 섹션 --
SCHEMA_EXTERNAL_DEP = ["외부 엔티티", "타입", "참조 INTERNAL"]

# -- Artifact 섹션 --
SCHEMA_ARTIFACT_REL = ["방향", "관계", "대상"]

# -- 설정 파일(NONTARGET) 섹션 --
SCHEMA_NONTARGET_BLOCK = ["이름", "종류", "키워드", "라인", "요약"]
SCHEMA_NONTARGET_REF = ["블록", "테이블", "역할", "근거", "신뢰도"]

# -- DB 스키마 섹션 --
SCHEMA_COLUMN = ["컬럼명", "타입", "Nullable", "PK", "설명"]
SCHEMA_FK = ["관계", "대상", "FK타입", "신뢰도", "근거"]

# -- 횡단 분석 섹션 --
SCHEMA_ACCESS_MATRIX = ["테이블", "모듈", "접근유형"]
SCHEMA_STRUCT_ALL = ["관계", "소스", "대상", "속성"]
SCHEMA_ETL_FLOW = ["관계", "소스", "대상"]


# ═══════════════════════════════════════════════════════════════════════
# 데이터 모델 — GraphNode, GraphRelationship, GraphData
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class GraphNode:
    node_id: str
    labels: List[str]
    properties: Dict[str, Any]

    @property
    def primary_label(self) -> str:
        """1차 레이블 반환.

        Framework: 역할 라벨(MODULE/VARIABLE/CONSTANT) 우선
        DBMS: 고유 라벨(PROCEDURE/FUNCTION/TRIGGER) 그대로
        """
        # 1) Framework 역할 라벨
        for lbl in self.labels:
            if lbl in PRIMARY_LABELS:
                return lbl
        # 2) DBMS 고유 라벨
        for lbl in self.labels:
            if lbl in DBMS_MODULE_LABELS:
                return lbl
        # 3) 폴백 (Table, Column, DML 등)
        for lbl in self.labels:
            if lbl in _SKIP_COUNT_LABELS:
                continue
            return lbl
        return self.labels[0] if self.labels else "UNKNOWN"

    @property
    def sub_label(self) -> str:
        """서브레이블 반환 (STRUCT, METHOD, MEMBER, DEFINE 등)."""
        for lbl in self.labels:
            if lbl in SUB_LABELS:
                return lbl
        return ""

    @property
    def stereotype_label(self) -> str:
        """스테레오타입 라벨 반환 (Init, Query, Command 등)."""
        for lbl in self.labels:
            if lbl in STEREOTYPE_LABELS:
                return lbl
        return ""

    @property
    def is_internal(self) -> bool:
        return "EXTERNAL" not in self.labels

    @property
    def is_external(self) -> bool:
        return "EXTERNAL" in self.labels

    @property
    def scope_label(self) -> str:
        return "EXTERNAL" if self.is_external else "INTERNAL"

    @property
    def function_role(self) -> str:
        """CQRS 역할 반환 — 스테레오타입 라벨에서 유도."""
        _READMODEL_STEREOTYPES = {"Query", "Getter"}
        for lbl in self.labels:
            if lbl in _READMODEL_STEREOTYPES:
                return "READMODEL"
            if lbl in STEREOTYPE_LABELS:
                return "COMMAND"
        return ""

    def prop(self, key: str, default: Any = "") -> Any:
        return self.properties.get(key, default)

    @property
    def display_name(self) -> str:
        for k in ("module_name", "name", "procedure_name", "fqn", "id"):
            v = self.properties.get(k)
            if v:
                return str(v)
        fname = self.properties.get("file_name") or self.properties.get("file_path")
        if fname:
            import os
            return os.path.basename(str(fname))
        return self.node_id


@dataclass
class GraphRelationship:
    rel_id: str
    rel_type: str
    properties: Dict[str, Any]
    start_node_id: str
    end_node_id: str

    def prop(self, key: str, default: Any = "") -> Any:
        return self.properties.get(key, default)


@dataclass
class GraphData:
    nodes: Dict[str, GraphNode] = field(default_factory=dict)
    relationships: List[GraphRelationship] = field(default_factory=list)
    _outgoing: Dict[str, List[GraphRelationship]] = field(default_factory=lambda: defaultdict(list))
    _incoming: Dict[str, List[GraphRelationship]] = field(default_factory=lambda: defaultdict(list))
    _label_counts: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    _sub_label_counts: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    _stereotype_counts: Dict[str, int] = field(default_factory=lambda: defaultdict(int))

    modules: List[GraphNode] = field(default_factory=list)
    variables: List[GraphNode] = field(default_factory=list)
    packages: List[GraphNode] = field(default_factory=list)
    tables: List[GraphNode] = field(default_factory=list)
    columns: List[GraphNode] = field(default_factory=list)
    dml_nodes: List[GraphNode] = field(default_factory=list)
    nontarget_files: List[GraphNode] = field(default_factory=list)
    nontarget_blocks: List[GraphNode] = field(default_factory=list)
    schemas: List[GraphNode] = field(default_factory=list)
    dblinks: List[GraphNode] = field(default_factory=list)
    artifact_chunks: List[GraphNode] = field(default_factory=list)

    def get_node(self, node_id: str) -> Optional[GraphNode]:
        return self.nodes.get(node_id)

    def get_children(self, parent_id: str, rel_type: str) -> List[GraphNode]:
        result = []
        for rel in self._outgoing.get(parent_id, []):
            if rel.rel_type == rel_type:
                n = self.nodes.get(rel.end_node_id)
                if n:
                    result.append(n)
        return result

    def get_parents(self, child_id: str, rel_type: str) -> List[GraphNode]:
        result = []
        for rel in self._incoming.get(child_id, []):
            if rel.rel_type == rel_type:
                n = self.nodes.get(rel.start_node_id)
                if n:
                    result.append(n)
        return result

    def get_outgoing_rels(self, node_id: str, rel_types: Optional[Set[str]] = None) -> List[GraphRelationship]:
        rels = self._outgoing.get(node_id, [])
        if rel_types:
            return [r for r in rels if r.rel_type in rel_types]
        return list(rels)

    def get_incoming_rels(self, node_id: str, rel_types: Optional[Set[str]] = None) -> List[GraphRelationship]:
        rels = self._incoming.get(node_id, [])
        if rel_types:
            return [r for r in rels if r.rel_type in rel_types]
        return list(rels)

    def node_display(self, node_id: str) -> str:
        n = self.nodes.get(node_id)
        return n.display_name if n else node_id

    def rel_type_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = defaultdict(int)
        for rel in self.relationships:
            if rel.rel_type not in EXCLUDED_REL_TYPES:
                counts[rel.rel_type] += 1
        return dict(sorted(counts.items(), key=lambda x: -x[1]))


# ═══════════════════════════════════════════════════════════════════════
# JSON 파싱
# ═══════════════════════════════════════════════════════════════════════

def _load_graph(json_path: str) -> GraphData:
    path = Path(json_path)
    raw = path.read_text(encoding="utf-8").lstrip("\ufeff")
    data = json.loads(raw)

    graph = GraphData()

    if isinstance(data, dict):
        _parse_nodes(data.get("Nodes", []), graph)
        _parse_relationships(data.get("Relationships", []), graph)
    elif isinstance(data, list):
        _parse_neo4j_export(data, graph)

    _index_nodes(graph)
    return graph


_NODE_ID_KEYS = ("identity", "elementId", "Node ID", "node_id", "id")
_NODE_META_KEYS = frozenset(("identity", "elementId", "Node ID", "node_id", "id",
                              "labels", "Labels", "properties", "Properties"))
_REL_META_KEYS = frozenset(("identity", "elementId", "Relationship ID", "id",
                             "type", "Type", "rel_type",
                             "start", "end", "Start Node ID", "End Node ID",
                             "startNodeElementId", "endNodeElementId",
                             "start_node_id", "end_node_id",
                             "properties", "Properties"))


def _parse_nodes(nodes_list: list, graph: GraphData) -> None:
    for item in nodes_list:
        node_id = ""
        for k in _NODE_ID_KEYS:
            v = item.get(k)
            if v:
                node_id = str(v)
                break
        if not node_id:
            continue

        labels = item.get("labels") or item.get("Labels") or []
        if isinstance(labels, str):
            labels = [labels]

        props = item.get("properties") or item.get("Properties") or {}
        if not props:
            props = {k: v for k, v in item.items() if k not in _NODE_META_KEYS}

        node = GraphNode(node_id=node_id, labels=labels, properties=props)
        graph.nodes[node_id] = node

        # 1차 레이블 카운트 (MODULE/FUNCTION/VARIABLE/CONSTANT 또는 DBMS 레이블)
        graph._label_counts[node.primary_label] += 1
        if node.is_external:
            graph._label_counts["EXTERNAL"] += 1

        # 서브레이블 카운트 (STRUCT, METHOD, MEMBER 등)
        sl = node.sub_label
        if sl:
            graph._sub_label_counts[sl] += 1

        # 스테레오타입 카운트 (Init, Query, Command 등)
        st = node.stereotype_label
        if st:
            graph._stereotype_counts[st] += 1


def _parse_relationships(rels_list: list, graph: GraphData) -> None:
    for item in rels_list:
        rel_id = str(
            item.get("identity")
            or item.get("elementId")
            or item.get("Relationship ID")
            or item.get("id", "")
        )
        rel_type = item.get("type") or item.get("Type") or item.get("rel_type") or ""

        start_id = item.get("start") or item.get("Start Node ID") or item.get("startNodeElementId") or item.get("start_node_id") or ""
        end_id = item.get("end") or item.get("End Node ID") or item.get("endNodeElementId") or item.get("end_node_id") or ""

        if isinstance(start_id, dict):
            start_id = start_id.get("identity") or start_id.get("elementId") or ""
        if isinstance(end_id, dict):
            end_id = end_id.get("identity") or end_id.get("elementId") or ""

        start_id = str(start_id)
        end_id = str(end_id)

        props = item.get("properties") or item.get("Properties") or {}
        if not props:
            props = {k: v for k, v in item.items() if k not in _REL_META_KEYS}

        rel = GraphRelationship(
            rel_id=rel_id, rel_type=rel_type, properties=props,
            start_node_id=start_id, end_node_id=end_id,
        )
        graph.relationships.append(rel)
        graph._outgoing[start_id].append(rel)
        graph._incoming[end_id].append(rel)


def _parse_neo4j_export(records: list, graph: GraphData) -> None:
    seen_nodes: set = set()
    seen_rels: set = set()
    for record in records:
        for _key, val in record.items():
            if not isinstance(val, dict):
                continue
            if "labels" in val and val.get("identity") or val.get("elementId"):
                nid = str(val.get("identity", val.get("elementId", "")))
                if nid not in seen_nodes:
                    seen_nodes.add(nid)
                    _parse_nodes([val], graph)
            elif "type" in val and ("start" in val or "startNodeElementId" in val):
                rid = str(val.get("identity", val.get("elementId", "")))
                if rid not in seen_rels:
                    seen_rels.add(rid)
                    _parse_relationships([val], graph)


def _index_nodes(graph: GraphData) -> None:
    # Framework 자식 함수 노드 ID 수집 (HAS_FUNCTION 관계의 타겟)
    child_function_ids = {
        r.end_node_id
        for rels in graph._incoming.values()
        for r in rels
        if r.rel_type == "HAS_FUNCTION"
    }

    for node in graph.nodes.values():
        pl = node.primary_label
        if pl in MODULE_LABELS:
            # Framework: MODULE (역할 기반)
            graph.modules.append(node)
        elif pl in DBMS_MODULE_LABELS:
            # DBMS: PROCEDURE/FUNCTION/TRIGGER (고유 라벨) → 최상위 모듈
            # Framework 자식 함수는 제외 (HAS_FUNCTION으로 접근)
            if node.node_id not in child_function_ids:
                graph.modules.append(node)
        elif pl in ("VARIABLE", "CONSTANT"):
            graph.variables.append(node)
        elif pl == "PACKAGE":
            graph.packages.append(node)
        elif pl == "Table":
            graph.tables.append(node)
        elif pl == "Column":
            graph.columns.append(node)
        elif pl in DML_LABELS:
            graph.dml_nodes.append(node)
        elif pl == "NONTARGET_FILE":
            graph.nontarget_files.append(node)
        elif pl == "NONTARGET_BLOCK":
            graph.nontarget_blocks.append(node)
        elif pl == "Schema":
            graph.schemas.append(node)
        elif pl == "DBLink":
            graph.dblinks.append(node)
        elif pl == "ARTIFACT_CHUNK":
            graph.artifact_chunks.append(node)


# ═══════════════════════════════════════════════════════════════════════
# 마크다운 유틸리티
# ═══════════════════════════════════════════════════════════════════════

def _md_table(headers: List[str], rows: List[List[str]]) -> str:
    """마크다운 테이블 생성. 빈 rows면 헤더 + 빈 행 출력."""
    lines = []
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    if not rows:
        lines.append("| " + " | ".join(["-"] * len(headers)) + " |")
    else:
        for row in rows:
            escaped = [str(c).replace("|", "\\|").replace("\n", " ") if c else "-" for c in row]
            lines.append("| " + " | ".join(escaped) + " |")
    return "\n".join(lines)


def _prop_table(pairs: List[Tuple[str, str]]) -> str:
    """key-value 속성 테이블. 값 없으면 '-' 표시, 모든 행 항상 출력."""
    rows = []
    for display_name, val in pairs:
        rows.append([display_name, str(val) if val else "-"])
    return _md_table(["항목", "값"], rows)


def _blockquote(text: str) -> str:
    if not text:
        return "> 없음"
    lines = text.strip().split("\n")
    return "\n".join(f"> {line}" for line in lines)


def _code_block(code: str, lang: str = "") -> str:
    if not code:
        return f"```{lang}\n없음\n```"
    return f"```{lang}\n{code.strip()}\n```"


def _safe(val: Any) -> str:
    if val is None:
        return ""
    return str(val)


def _truncate(text: str, max_len: int = 100) -> str:
    """긴 텍스트 잘라내기."""
    if not text:
        return "-"
    text = str(text).replace("\n", " ").strip()
    if len(text) > max_len:
        return text[:max_len] + "..."
    return text


def _format_rel_props(props: Dict[str, Any]) -> str:
    if not props:
        return "-"
    skip_keys = {"created_at"}
    parts = []
    for k, v in props.items():
        if k in skip_keys:
            continue
        parts.append(f"{k}={v}")
    return ", ".join(parts) if parts else "-"


def _infer_stereotype(graph: GraphData, mod: GraphNode) -> str:
    """모듈의 스테레오타입 추론 (라벨 → 속성 → 자식 함수 역할 순)."""
    # 1) 라벨에서 직접 추출
    st = mod.stereotype_label
    if st:
        return st
    # 2) 속성에서 추출
    st = mod.prop("stereotype", "")
    if st:
        return st
    # 3) 자식 함수의 역할에서 추론
    func_roles = set()
    for func in graph.get_children(mod.node_id, "HAS_FUNCTION"):
        r = func.function_role
        if r:
            func_roles.add(r)
    return "/".join(sorted(func_roles)) if func_roles else ""


def _get_module_name(mod: GraphNode) -> str:
    """모듈의 표시 이름."""
    for k in ("module_name", "procedure_name", "name"):
        v = mod.prop(k)
        if v:
            return str(v)
    return mod.display_name


# ═══════════════════════════════════════════════════════════════════════
# 섹션 렌더러 — 항상 고정 구조 출력
# ═══════════════════════════════════════════════════════════════════════

def _render_overview(graph: GraphData) -> str:
    """## 1. 시스템 개요"""
    parts = ["## 1. 시스템 개요\n"]

    # 1.1 노드 통계 (1차 레이블 기준)
    parts.append("### 1.1. 노드 통계\n")
    node_rows = []
    for label, count in sorted(graph._label_counts.items(), key=lambda x: -x[1]):
        node_rows.append([label, str(count)])
    parts.append(_md_table(SCHEMA_NODE_STATS, node_rows))

    # 1.2 서브레이블 분포 (AST 타입 상세)
    parts.append("\n### 1.2. 서브레이블 분포\n")
    sub_rows = []
    for label, count in sorted(graph._sub_label_counts.items(), key=lambda x: -x[1]):
        sub_rows.append([label, str(count)])
    parts.append(_md_table(SCHEMA_NODE_STATS, sub_rows))

    # 1.3 관계 통계
    parts.append("\n### 1.3. 관계 통계\n")
    rel_counts = graph.rel_type_counts()
    rel_rows = [[rt, str(cnt)] for rt, cnt in rel_counts.items()]
    parts.append(_md_table(SCHEMA_REL_STATS, rel_rows))

    # 1.4 스테레오타입 분포
    parts.append("\n### 1.4. 스테레오타입 분포\n")
    stereo_counts: Dict[str, int] = defaultdict(int)
    if graph._stereotype_counts:
        # Neo4j 라벨 기반 스테레오타입 카운트 우선 사용
        for st, cnt in graph._stereotype_counts.items():
            stereo_counts[st] += cnt
    else:
        # 라벨에 스테레오타입이 없으면 속성에서 추론
        for mod in graph.modules:
            st = _infer_stereotype(graph, mod) or "미분류"
            stereo_counts[st] += 1
    stereo_rows = [[s, str(c)] for s, c in sorted(stereo_counts.items(), key=lambda x: -x[1])]
    parts.append(_md_table(SCHEMA_STEREOTYPE, stereo_rows))

    return "\n".join(parts)


def _render_packages(graph: GraphData) -> str:
    """## 2. 패키지 구조"""
    parts = ["## 2. 패키지 구조\n"]

    if not graph.packages:
        parts.append("패키지 없음.\n")
        return "\n".join(parts)

    # 패키지 트리 구성
    pkg_by_fqn: Dict[str, GraphNode] = {}
    pkg_children: Dict[str, List[str]] = defaultdict(list)
    root_pkgs: List[str] = []

    for pkg in graph.packages:
        fqn = pkg.prop("fqn", pkg.prop("name", ""))
        pkg_by_fqn[fqn] = pkg

    for pkg in graph.packages:
        fqn = pkg.prop("fqn", pkg.prop("name", ""))
        parent_rels = graph.get_outgoing_rels(pkg.node_id, {"BELONGS_TO_PACKAGE"})
        if parent_rels:
            parent_node = graph.get_node(parent_rels[0].end_node_id)
            if parent_node:
                parent_fqn = parent_node.prop("fqn", parent_node.prop("name", ""))
                pkg_children[parent_fqn].append(fqn)
            else:
                root_pkgs.append(fqn)
        else:
            root_pkgs.append(fqn)

    # 패키지별 멤버 수집 (모듈만 — artifact/nontarget은 전용 섹션에서)
    pkg_members: Dict[str, List[GraphNode]] = defaultdict(list)
    for mod in graph.modules:
        pkg_name = mod.prop("package_name") or mod.prop("schema", "")
        if pkg_name:
            pkg_members[pkg_name].append(mod)

    def _has_members(fqn: str) -> bool:
        if pkg_members.get(fqn):
            return True
        for child_fqn in pkg_children.get(fqn, []):
            if _has_members(child_fqn):
                return True
        return False

    def _render_pkg_tree(fqn: str) -> None:
        if not _has_members(fqn):
            return

        parts.append(f"### {fqn}\n")

        members = pkg_members.get(fqn, [])
        rows = []
        for m in sorted(members, key=lambda x: x.display_name):
            stereo = _infer_stereotype(graph, m)
            type_display = f"{m.primary_label}:{m.sub_label}" if m.sub_label else m.primary_label
            rows.append([
                m.display_name,
                type_display,
                stereo or "-",
                m.scope_label,
            ])
        parts.append(_md_table(SCHEMA_PKG_MEMBERS, rows))
        parts.append("")

        for child_fqn in sorted(pkg_children.get(fqn, [])):
            _render_pkg_tree(child_fqn)

    for root_fqn in sorted(root_pkgs):
        _render_pkg_tree(root_fqn)

    return "\n".join(parts)


def _render_module_detail(graph: GraphData, mod: GraphNode) -> str:
    """단일 모듈 상세 — MODULE/PROCEDURE/FUNCTION/TRIGGER 모두 동일 포맷.

    하위 섹션: 기본 정보 → 요약 → 함수 → 변수 → 구조 관계 → 호출 관계 → 데이터 접근 → 요약 코드
    """
    name = _get_module_name(mod)
    parts = [f"### {name}\n"]

    # ── 기본 정보 (범용 8행) ──
    type_display = f"{mod.primary_label}:{mod.sub_label}" if mod.sub_label else mod.primary_label
    info_pairs = [
        ("이름", name),
        ("타입", type_display),
        ("FQN", _safe(mod.prop("fqn"))),
        ("패키지", _safe(mod.prop("package_name") or mod.prop("schema"))),
        ("스테레오타입", _safe(_infer_stereotype(graph, mod))),
        ("파일", _safe(mod.prop("file_name"))),
        ("라인", f"{_safe(mod.prop('start_line'))}-{_safe(mod.prop('end_line'))}"),
        ("토큰", _safe(mod.prop("token"))),
    ]
    parts.append("#### 기본 정보\n")
    parts.append(_prop_table(info_pairs))

    # ── 요약 ──
    parts.append("\n#### 요약\n")
    parts.append(_blockquote(mod.prop("summary")))

    # ── 함수 (FUNCTION + DML 통합) ──
    func_rows = []
    for func in sorted(graph.get_children(mod.node_id, "HAS_FUNCTION"),
                       key=lambda x: x.prop("start_line", 0) or 0):
        func_rows.append([
            _safe(func.prop("name")),
            _safe(func.prop("signature")),
            _safe(func.prop("return_type")),
            _safe(func.prop("modifiers")),
            _safe(func.prop("summary")),
        ])
    for d in sorted(graph.get_children(mod.node_id, "CONTAINS"),
                    key=lambda x: x.prop("start_line", 0) or 0):
        if d.primary_label in DML_LABELS:
            func_rows.append([
                d.primary_label,
                _truncate(d.prop("node_code")),
                "-",
                f"Line {_safe(d.prop('start_line'))}",
                "-",
            ])
    parts.append("\n#### 함수\n")
    parts.append(_md_table(SCHEMA_FUNCTION, func_rows))

    # ── 변수 (HAS_VARIABLE + DECLARES 통합) ──
    var_rows = []
    # HAS_VARIABLE: Framework 분석 결과
    for v in sorted(graph.get_children(mod.node_id, "HAS_VARIABLE"),
                    key=lambda x: x.prop("start_line", 0) or 0):
        var_rows.append([
            _safe(v.prop("name")),
            _safe(v.prop("variable_type")),
            _safe(v.prop("modifiers")),
            _safe(v.prop("summary")),
        ])
    # DECLARES: DBMS 분석 결과
    for v in graph.get_children(mod.node_id, "DECLARES"):
        var_rows.append([
            _safe(v.prop("name")),
            _safe(v.prop("role")),
            "-",
            _safe(v.prop("summary")),
        ])
    parts.append("\n#### 변수\n")
    parts.append(_md_table(SCHEMA_VARIABLE, var_rows))

    # ── 구조 관계 (UML + ETL 통합) ──
    struct_rels = []
    for r in graph.get_outgoing_rels(mod.node_id, ENTITY_STRUCT_REL_TYPES):
        struct_rels.append([
            "→", r.rel_type, graph.node_display(r.end_node_id),
            _format_rel_props(r.properties),
        ])
    for r in graph.get_incoming_rels(mod.node_id, ENTITY_STRUCT_REL_TYPES):
        struct_rels.append([
            "←", r.rel_type, graph.node_display(r.start_node_id),
            _format_rel_props(r.properties),
        ])
    parts.append("\n#### 구조 관계\n")
    parts.append(_md_table(SCHEMA_STRUCT_REL, struct_rels))

    # ── 호출 관계 (CALLS + CALL + USES 통합) ──
    call_rels = []
    for r in graph.get_outgoing_rels(mod.node_id, ENTITY_CALL_REL_TYPES):
        call_rels.append(["→", graph.node_display(r.end_node_id), _safe(r.prop("call_code"))])
    # 자식 함수 레벨 호출 수집
    for func in graph.get_children(mod.node_id, "HAS_FUNCTION"):
        for r in graph.get_outgoing_rels(func.node_id, ENTITY_CALL_REL_TYPES):
            call_rels.append(["→", graph.node_display(r.end_node_id), _safe(r.prop("call_code"))])
    for r in graph.get_incoming_rels(mod.node_id, ENTITY_CALL_REL_TYPES):
        call_rels.append(["←", graph.node_display(r.start_node_id), _safe(r.prop("call_code"))])
    parts.append("\n#### 호출 관계\n")
    parts.append(_md_table(SCHEMA_CALL_REL, call_rels))

    # ── 데이터 접근 (FROM/READS/WRITES/REFER_TO 통합) ──
    # 모듈 직접 → 자식 함수 → 자식 DML → 자식 변수 순으로 수집
    data_access = []
    for r in graph.get_outgoing_rels(mod.node_id, ENTITY_DATA_REL_TYPES):
        data_access.append([
            graph.node_display(r.end_node_id), r.rel_type,
            _safe(r.prop("evidence")), _safe(r.prop("confidence")),
        ])
    for func in graph.get_children(mod.node_id, "HAS_FUNCTION"):
        for r in graph.get_outgoing_rels(func.node_id, ENTITY_DATA_REL_TYPES):
            data_access.append([
                graph.node_display(r.end_node_id), r.rel_type,
                _safe(r.prop("evidence")), _safe(r.prop("confidence")),
            ])
    for d in graph.get_children(mod.node_id, "CONTAINS"):
        if d.primary_label in DML_LABELS:
            for r in graph.get_outgoing_rels(d.node_id, ENTITY_DATA_REL_TYPES):
                data_access.append([
                    graph.node_display(r.end_node_id), r.rel_type,
                    _safe(r.prop("evidence")), _safe(r.prop("confidence")),
                ])
    for v in graph.get_children(mod.node_id, "HAS_VARIABLE"):
        for r in graph.get_outgoing_rels(v.node_id, ENTITY_DATA_REL_TYPES):
            data_access.append([
                graph.node_display(r.end_node_id), r.rel_type,
                _safe(r.prop("evidence")), _safe(r.prop("confidence")),
            ])
    parts.append("\n#### 데이터 접근\n")
    parts.append(_md_table(SCHEMA_DATA_ACCESS, data_access))

    # ── 요약 코드 ──
    parts.append("\n#### 요약 코드\n")
    parts.append(_code_block(mod.prop("summarized_code")))

    return "\n".join(parts)


def _render_external(graph: GraphData, external_modules: List[GraphNode]) -> str:
    """## 4. 외부 의존성 (EXTERNAL) 내용."""
    rows = []
    for mod in sorted(external_modules, key=lambda m: m.display_name):
        referrers = set()
        for rel in graph.get_incoming_rels(mod.node_id):
            if rel.rel_type in EXCLUDED_REL_TYPES:
                continue
            src = graph.get_node(rel.start_node_id)
            if src and src.is_internal:
                referrers.add(src.display_name)
        rows.append([
            mod.display_name,
            mod.primary_label,
            ", ".join(sorted(referrers)) if referrers else "-",
        ])
    return _md_table(SCHEMA_EXTERNAL_DEP, rows)


def _render_artifact_chunks(graph: GraphData) -> str:
    """## 5. Artifact 청크"""
    parts = ["## 5. Artifact 청크\n"]

    if not graph.artifact_chunks:
        parts.append("Artifact 청크 없음.\n")
        return "\n".join(parts)

    for chunk in sorted(graph.artifact_chunks, key=lambda c: c.display_name):
        parts.append(f"### {chunk.display_name}\n")

        info_pairs = [
            ("이름", chunk.display_name),
            ("파일", _safe(chunk.prop("file_name", chunk.prop("file_path", "")))),
            ("종류", _safe(chunk.prop("kind", chunk.prop("type", "")))),
            ("요약", _safe(chunk.prop("summary"))),
        ]
        parts.append("#### 기본 정보\n")
        parts.append(_prop_table(info_pairs))

        rel_rows = []
        for r in graph.get_outgoing_rels(chunk.node_id):
            if r.rel_type not in EXCLUDED_REL_TYPES:
                rel_rows.append(["→", r.rel_type, graph.node_display(r.end_node_id)])
        for r in graph.get_incoming_rels(chunk.node_id):
            if r.rel_type not in EXCLUDED_REL_TYPES:
                rel_rows.append(["←", r.rel_type, graph.node_display(r.start_node_id)])
        parts.append("\n#### 관계\n")
        parts.append(_md_table(SCHEMA_ARTIFACT_REL, rel_rows))
        parts.append("")

    return "\n".join(parts)


def _render_nontarget(graph: GraphData) -> str:
    """## 6. 설정 파일 (NONTARGET)"""
    parts = ["## 6. 설정 파일 (NONTARGET)\n"]

    if not graph.nontarget_files and not graph.nontarget_blocks:
        parts.append("설정 파일 없음.\n")
        return "\n".join(parts)

    for ntf in sorted(graph.nontarget_files, key=lambda f: f.prop("file_path", "")):
        fname = ntf.prop("file_name", ntf.prop("file_path", ntf.display_name))
        parts.append(f"### {fname}\n")

        info_pairs = [
            ("파일 경로", _safe(ntf.prop("file_path"))),
            ("확장자", _safe(ntf.prop("ext"))),
            ("디렉토리", _safe(ntf.prop("directory"))),
        ]
        parts.append("#### 파일 정보\n")
        parts.append(_prop_table(info_pairs))

        blocks = graph.get_children(ntf.node_id, "CONTAINS")
        blocks = [b for b in blocks if b.primary_label == "NONTARGET_BLOCK"]
        block_rows = []
        for blk in blocks:
            block_rows.append([
                _safe(blk.prop("name")),
                _safe(blk.prop("kind")),
                _safe(blk.prop("keywords")),
                f"{_safe(blk.prop('start_line'))}-{_safe(blk.prop('end_line'))}",
                _safe(blk.prop("summary")),
            ])
        parts.append("\n#### 블록 목록\n")
        parts.append(_md_table(SCHEMA_NONTARGET_BLOCK, block_rows))

        ref_rows = []
        for blk in blocks:
            blk_name = blk.prop("name", blk.display_name)
            for r in graph.get_outgoing_rels(blk.node_id, {"REFERS_TO"}):
                ref_rows.append([
                    blk_name,
                    graph.node_display(r.end_node_id),
                    _safe(r.prop("role")),
                    _safe(r.prop("evidence")),
                    _safe(r.prop("confidence")),
                ])
        parts.append("\n#### 테이블 참조\n")
        parts.append(_md_table(SCHEMA_NONTARGET_REF, ref_rows))
        parts.append("")

    return "\n".join(parts)


def _render_db_schema(graph: GraphData) -> str:
    """## 7. 데이터베이스 스키마"""
    parts = ["## 7. 데이터베이스 스키마\n"]

    if not graph.tables:
        parts.append("데이터베이스 테이블 없음.\n")
        return "\n".join(parts)

    for table in sorted(graph.tables, key=lambda t: t.prop("name", "")):
        tname = table.prop("name", table.display_name)
        parts.append(f"### {tname}\n")

        info_pairs = [
            ("이름", _safe(table.prop("name"))),
            ("스키마", _safe(table.prop("schema"))),
            ("설명", _safe(table.prop("description"))),
            ("엔티티명", _safe(table.prop("entity_name"))),
            ("매핑 타입", _safe(table.prop("mapping_type"))),
            ("테이블 타입", _safe(table.prop("table_type"))),
            ("DB", _safe(table.prop("db"))),
        ]
        parts.append("#### 테이블 정보\n")
        parts.append(_prop_table(info_pairs))

        cols = graph.get_children(table.node_id, "HAS_COLUMN")
        col_rows = []
        for col in cols:
            col_rows.append([
                _safe(col.prop("name")),
                _safe(col.prop("dtype")),
                _safe(col.prop("nullable")),
                _safe(col.prop("pk_constraint")),
                _safe(col.prop("description")),
            ])
        parts.append("\n#### 컬럼\n")
        parts.append(_md_table(SCHEMA_COLUMN, col_rows))

        fk_rels = graph.get_outgoing_rels(table.node_id, {"FK_TO_TABLE", "FK_TO"})
        for col in cols:
            fk_rels.extend(graph.get_outgoing_rels(col.node_id, {"FK_TO", "FK_TO_TABLE"}))
        fk_rows = []
        for fk in fk_rels:
            fk_rows.append([
                fk.rel_type,
                graph.node_display(fk.end_node_id),
                _safe(fk.prop("fk_type")),
                _safe(fk.prop("confidence")),
                _safe(fk.prop("evidence")),
            ])
        parts.append("\n#### FK 관계\n")
        parts.append(_md_table(SCHEMA_FK, fk_rows))
        parts.append("")

    return "\n".join(parts)


def _render_dblinks(graph: GraphData) -> str:
    """## 8. DB Link"""
    parts = ["## 8. DB Link\n"]

    if not graph.dblinks:
        parts.append(_md_table(["DB Link 이름"], []))
        return "\n".join(parts)

    rows = []
    for dbl in graph.dblinks:
        rows.append([_safe(dbl.prop("name", dbl.display_name))])
    parts.append(_md_table(["DB Link 이름"], rows))
    return "\n".join(parts)


def _render_cross_cutting(graph: GraphData) -> str:
    """## 9. 횡단 분석"""
    parts = ["## 9. 횡단 분석\n"]

    # 9.1 테이블 접근 매트릭스
    parts.append("### 9.1. 테이블 접근 매트릭스\n")
    access_data: Dict[str, Dict[str, Set[str]]] = defaultdict(lambda: defaultdict(set))
    for rel in graph.relationships:
        if rel.rel_type in ("FROM", "READS", "WRITES", "REFER_TO", "REFERS_TO"):
            src = graph.get_node(rel.start_node_id)
            tgt = graph.get_node(rel.end_node_id)
            if src and tgt and tgt.primary_label == "Table":
                src_name = src.display_name
                # 함수 노드면 부모 모듈 이름 사용
                if src.primary_label == "FUNCTION":
                    parents = graph.get_parents(src.node_id, "HAS_FUNCTION")
                    if parents:
                        src_name = parents[0].display_name
                elif src.primary_label in DML_LABELS:
                    parents = graph.get_parents(src.node_id, "CONTAINS")
                    if parents:
                        src_name = parents[0].display_name
                tgt_name = tgt.display_name
                op = "READ" if rel.rel_type in ("FROM", "READS") else (
                    "WRITE" if rel.rel_type == "WRITES" else "REFER"
                )
                access_data[tgt_name][src_name].add(op)
    access_rows = []
    for tbl_name in sorted(access_data.keys()):
        for mod_name, ops in sorted(access_data[tbl_name].items()):
            access_rows.append([tbl_name, mod_name, ", ".join(sorted(ops))])
    parts.append(_md_table(SCHEMA_ACCESS_MATRIX, access_rows))

    # 9.2 호출 체인 (진입점)
    parts.append("\n### 9.2. 호출 체인\n")
    call_rels = [r for r in graph.relationships if r.rel_type in ENTITY_CALL_REL_TYPES]
    callers = {r.start_node_id for r in call_rels}
    callees = {r.end_node_id for r in call_rels}
    entry_point_ids = callers - callees
    ep_rows = []
    for ep_id in sorted(entry_point_ids):
        node = graph.get_node(ep_id)
        if not node:
            continue
        name = node.display_name
        ntype = node.primary_label
        # 함수 노드면 부모 모듈 이름 사용
        if ntype == "FUNCTION":
            parents = graph.get_parents(node.node_id, "HAS_FUNCTION")
            if parents:
                name = parents[0].display_name
                ntype = parents[0].primary_label
        ep_rows.append([name, ntype])
    # 중복 제거
    seen: set = set()
    unique_ep_rows = []
    for row in ep_rows:
        if row[0] not in seen:
            seen.add(row[0])
            unique_ep_rows.append(row)
    parts.append(_md_table(["진입점", "타입"], unique_ep_rows))

    # 9.3 구조 관계 전체 목록
    parts.append("\n### 9.3. 구조 관계 전체 목록\n")
    struct_rows = []
    for r in graph.relationships:
        if r.rel_type in ENTITY_STRUCT_REL_TYPES:
            struct_rows.append([
                r.rel_type,
                graph.node_display(r.start_node_id),
                graph.node_display(r.end_node_id),
                _format_rel_props(r.properties),
            ])
    parts.append(_md_table(SCHEMA_STRUCT_ALL, struct_rows))

    # 9.4 ETL 데이터 흐름
    parts.append("\n### 9.4. ETL 데이터 흐름\n")
    etl_rows = []
    for r in graph.relationships:
        if r.rel_type in ETL_REL_TYPES:
            etl_rows.append([
                r.rel_type,
                graph.node_display(r.start_node_id),
                graph.node_display(r.end_node_id),
            ])
    parts.append(_md_table(SCHEMA_ETL_FLOW, etl_rows))

    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════
# 보고서 빌더 — 단일 함수
# ═══════════════════════════════════════════════════════════════════════

def _build_report(graph: GraphData) -> str:
    """단일 통합 보고서 생성. 전략 무관, 데이터 유무 무관."""
    internal_modules = [n for n in graph.modules if n.is_internal]
    external_modules = [n for n in graph.modules if n.is_external]

    parts = ["# 레거시 시스템 분석 보고서\n"]

    # 1. 시스템 개요
    parts.append(_render_overview(graph))

    # 2. 패키지 구조
    parts.append(_render_packages(graph))

    # 3. 모듈 상세 (INTERNAL)
    mod_parts = ["## 3. 모듈 상세 (INTERNAL)\n"]
    if not internal_modules:
        mod_parts.append("INTERNAL 모듈 없음.\n")
    else:
        for mod in sorted(internal_modules, key=lambda n: n.prop("fqn", "") or n.display_name):
            mod_parts.append(_render_module_detail(graph, mod))
            mod_parts.append("")
    parts.append("\n".join(mod_parts))

    # 4. 외부 의존성 (EXTERNAL)
    ext_parts = ["## 4. 외부 의존성 (EXTERNAL)\n"]
    ext_parts.append(_render_external(graph, external_modules))
    parts.append("\n".join(ext_parts))

    # 5. Artifact 청크
    parts.append(_render_artifact_chunks(graph))

    # 6. 설정 파일 (NONTARGET)
    parts.append(_render_nontarget(graph))

    # 7. 데이터베이스 스키마
    parts.append(_render_db_schema(graph))

    # 8. DB Link
    parts.append(_render_dblinks(graph))

    # 9. 횡단 분석
    parts.append(_render_cross_cutting(graph))

    return "\n\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════
# 공개 API
# ═══════════════════════════════════════════════════════════════════════

def _load_graph_from_dict(data: Dict[str, Any]) -> GraphData:
    """{"Nodes": [...], "Relationships": [...]} dict를 GraphData로 파싱."""
    graph = GraphData()
    _parse_nodes(data.get("Nodes", []), graph)
    _parse_relationships(data.get("Relationships", []), graph)
    _index_nodes(graph)
    return graph


def _generate_report(graph: GraphData) -> str:
    """GraphData → 마크다운 보고서."""
    logger.info(
        "[REPORT] 보고서 생성 | 노드=%d, 관계=%d",
        len(graph.nodes), len(graph.relationships),
    )
    return _build_report(graph)


def generate_analysis_report_from_graph(graph_data: Dict[str, Any]) -> str:
    """Neo4j 조회 결과 dict → 마크다운 분석 보고서.

    Args:
        graph_data: {"Nodes": [...], "Relationships": [...]} 형태의 dict
    """
    graph = _load_graph_from_dict(graph_data)
    return _generate_report(graph)


def generate_analysis_report(json_path: str) -> str:
    """JSON 파일 경로 → 마크다운 분석 보고서."""
    graph = _load_graph(json_path)
    return _generate_report(graph)
