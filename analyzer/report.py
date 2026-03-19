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
# 데이터 모델
# ═══════════════════════════════════════════════════════════════════════

SCOPE_LABELS = {"INTERNAL", "EXTERNAL"}
CLASS_LABELS = {"MODULE"}
EXTERNAL_TYPE_LABELS = {"EXTERNAL"}
METHOD_ROLE_LABELS = {"READMODEL", "COMMAND"}
DML_LABELS = {
    "SELECT", "INSERT", "UPDATE", "DELETE", "MERGE",
    "EXECUTE_IMMEDIATE", "FETCH", "CTE", "OPEN_CURSOR", "CREATE_TEMP_TABLE",
}
PROCEDURE_LABELS = {"PROCEDURE", "FUNCTION", "TRIGGER"}
EXCLUDED_REL_TYPES = {"NEXT", "PARENT_OF"}

STRUCTURAL_REL_TYPES = {"CONTAINS", "HAS_FUNCTION", "HAS_VARIABLE", "BELONGS_TO_PACKAGE", "CONTAINS_PACKAGE", "HAS_COLUMN"}
UML_REL_TYPES = {"EXTENDS", "IMPLEMENTS", "ASSOCIATION", "COMPOSITION", "AGGREGATION", "DEPENDENCY"}
DEP_REL_TYPES = {"CALLS", "USES", "CALL"}
DB_REL_TYPES = {"FROM", "READS", "WRITES", "REFERS_TO", "REFER_TO", "FK_TO_TABLE", "FK_TO"}
ETL_REL_TYPES = {"ETL_READS", "ETL_WRITES", "DATA_FLOWS_TO"}

# 엔티티 상세에서 구조 관계로 묶을 관계 타입 (UML + ETL)
ENTITY_STRUCT_REL_TYPES = UML_REL_TYPES | ETL_REL_TYPES
# 엔티티 상세에서 호출 관계로 묶을 관계 타입
ENTITY_CALL_REL_TYPES = {"CALLS", "USES", "CALL"}
# 엔티티 상세에서 데이터 접근으로 묶을 관계 타입
ENTITY_DATA_REL_TYPES = {"FROM", "READS", "WRITES", "REFER_TO", "REFERS_TO"}

# ═══════════════════════════════════════════════════════════════════════
# 통합 테이블 스키마 상수
# ═══════════════════════════════════════════════════════════════════════

SCHEMA_NODE_STATS = ["노드 타입", "개수"]
SCHEMA_REL_STATS = ["관계 타입", "개수"]
SCHEMA_STEREOTYPE = ["스테레오타입", "개수"]

SCHEMA_PKG_MEMBERS = ["이름", "타입", "스테레오타입", "범위"]

SCHEMA_MEMBER = ["이름", "시그니처", "반환타입", "수정자", "요약"]
SCHEMA_FIELD_VAR = ["이름", "타입", "수정자", "요약"]
SCHEMA_STRUCT_REL = ["방향", "관계", "대상", "속성"]
SCHEMA_CALL_REL = ["방향", "대상", "호출코드"]
SCHEMA_DATA_ACCESS = ["대상", "관계", "근거", "신뢰도"]

SCHEMA_EXTERNAL_DEP = ["외부 엔티티", "타입", "참조 INTERNAL"]

SCHEMA_ARTIFACT_REL = ["방향", "관계", "대상"]

SCHEMA_NONTARGET_BLOCK = ["이름", "종류", "키워드", "라인", "요약"]
SCHEMA_NONTARGET_REF = ["블록", "테이블", "역할", "근거", "신뢰도"]

SCHEMA_COLUMN = ["컬럼명", "타입", "Nullable", "PK", "설명"]
SCHEMA_FK = ["관계", "대상", "FK타입", "신뢰도", "근거"]

SCHEMA_ACCESS_MATRIX = ["테이블", "엔티티", "접근유형"]
SCHEMA_STRUCT_ALL = ["관계", "소스", "대상", "속성"]
SCHEMA_ETL_FLOW = ["관계", "소스", "대상"]


@dataclass
class GraphNode:
    node_id: str
    labels: List[str]
    properties: Dict[str, Any]

    @property
    def primary_label(self) -> str:
        for lbl in self.labels:
            if lbl in SCOPE_LABELS or lbl in METHOD_ROLE_LABELS:
                continue
            if lbl == "EXTERNAL":
                continue
            return lbl
        return self.labels[0] if self.labels else "UNKNOWN"

    @property
    def is_internal(self) -> bool:
        return "EXTERNAL" not in self.labels

    @property
    def is_external(self) -> bool:
        return "EXTERNAL" in self.labels

    @property
    def scope_label(self) -> str:
        if self.is_external:
            return "EXTERNAL"
        return "INTERNAL"

    @property
    def method_role(self) -> str:
        for lbl in self.labels:
            if lbl in METHOD_ROLE_LABELS:
                return lbl
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

    classes: List[GraphNode] = field(default_factory=list)
    methods: List[GraphNode] = field(default_factory=list)
    fields: List[GraphNode] = field(default_factory=list)
    packages: List[GraphNode] = field(default_factory=list)
    tables: List[GraphNode] = field(default_factory=list)
    columns: List[GraphNode] = field(default_factory=list)
    procedures: List[GraphNode] = field(default_factory=list)
    dml_nodes: List[GraphNode] = field(default_factory=list)
    variables: List[GraphNode] = field(default_factory=list)
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
        counted_primary = False
        for lbl in labels:
            if lbl in SCOPE_LABELS:
                continue
            if lbl in METHOD_ROLE_LABELS:
                continue
            if lbl == "EXTERNAL":
                graph._label_counts["EXTERNAL"] += 1
                continue
            graph._label_counts[lbl] += 1
            counted_primary = True
        if not counted_primary and labels:
            graph._label_counts[labels[0]] += 1


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
    for node in graph.nodes.values():
        pl = node.primary_label
        if pl in CLASS_LABELS:
            graph.classes.append(node)
        elif pl == "FUNCTION":
            graph.methods.append(node)
        elif pl == "VARIABLE":
            graph.fields.append(node)
            graph.variables.append(node)
        elif pl == "PACKAGE":
            graph.packages.append(node)
        elif pl == "Table":
            graph.tables.append(node)
        elif pl == "Column":
            graph.columns.append(node)
        elif pl in PROCEDURE_LABELS:
            graph.procedures.append(node)
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


def _infer_stereotype(graph: GraphData, cls: GraphNode) -> str:
    stereo = cls.prop("stereotype", "")
    if stereo:
        return stereo
    method_roles = set()
    for method in graph.get_children(cls.node_id, "HAS_FUNCTION"):
        r = method.method_role
        if r:
            method_roles.add(r)
    return "/".join(sorted(method_roles)) if method_roles else ""


def _get_entity_name(entity: GraphNode) -> str:
    """엔티티의 표시 이름."""
    for k in ("module_name", "procedure_name", "name"):
        v = entity.prop(k)
        if v:
            return str(v)
    return entity.display_name


# ═══════════════════════════════════════════════════════════════════════
# 섹션 렌더러 — 항상 고정 구조 출력
# ═══════════════════════════════════════════════════════════════════════

def _render_overview(graph: GraphData) -> str:
    """## 1. 시스템 개요"""
    parts = ["## 1. 시스템 개요\n"]

    # 1.1 노드 통계
    parts.append("### 1.1. 노드 통계\n")
    node_rows = []
    for label, count in sorted(graph._label_counts.items(), key=lambda x: -x[1]):
        if label in SCOPE_LABELS or label in METHOD_ROLE_LABELS:
            continue
        node_rows.append([label, str(count)])
    parts.append(_md_table(SCHEMA_NODE_STATS, node_rows))

    # 1.2 관계 통계
    parts.append("\n### 1.2. 관계 통계\n")
    rel_counts = graph.rel_type_counts()
    rel_rows = [[rt, str(cnt)] for rt, cnt in rel_counts.items()]
    parts.append(_md_table(SCHEMA_REL_STATS, rel_rows))

    # 1.3 스테레오타입 분포
    parts.append("\n### 1.3. 스테레오타입 분포\n")
    stereo_counts: Dict[str, int] = defaultdict(int)
    for cls in graph.classes:
        st = _infer_stereotype(graph, cls) or "미분류"
        stereo_counts[st] += 1
    for proc in graph.procedures:
        st = proc.prop("stereotype", "") or "미분류"
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

    # 패키지별 멤버 수집 (CLASS만 — artifact/nontarget은 전용 섹션에서)
    pkg_members: Dict[str, List[GraphNode]] = defaultdict(list)
    for cls in graph.classes:
        pkg_name = cls.prop("package_name", "")
        if pkg_name:
            pkg_members[pkg_name].append(cls)
    # DBMS: 프로시저도 스키마(패키지)에 매핑
    for proc in graph.procedures:
        schema = proc.prop("schema", proc.prop("package_name", ""))
        if schema:
            pkg_members[schema].append(proc)

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
            stereo = _infer_stereotype(graph, m) if m.primary_label in CLASS_LABELS else m.prop("stereotype", "")
            rows.append([
                m.display_name,
                m.primary_label,
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


def _render_entity_detail(graph: GraphData, entity: GraphNode) -> str:
    """단일 엔티티 상세 — MODULE/PROCEDURE/FUNCTION/TRIGGER 모두 동일 포맷."""
    name = _get_entity_name(entity)
    parts = [f"### {name}\n"]

    # ── 기본 정보 (범용 8행) ──
    info_pairs = [
        ("이름", name),
        ("타입", entity.primary_label),
        ("FQN", _safe(entity.prop("fqn"))),
        ("패키지", _safe(entity.prop("package_name") or entity.prop("schema"))),
        ("스테레오타입", _safe(_infer_stereotype(graph, entity) if entity.primary_label in CLASS_LABELS else entity.prop("stereotype", ""))),
        ("파일", _safe(entity.prop("file_name"))),
        ("라인", f"{_safe(entity.prop('start_line'))}-{_safe(entity.prop('end_line'))}"),
        ("토큰", _safe(entity.prop("token"))),
    ]
    parts.append("#### 기본 정보\n")
    parts.append(_prop_table(info_pairs))

    # ── 요약 ──
    parts.append("\n#### 요약\n")
    parts.append(_blockquote(entity.prop("summary")))

    # ── 메서드 (METHOD + DML 통합) ──
    members = []
    for m in sorted(graph.get_children(entity.node_id, "HAS_FUNCTION"),
                    key=lambda x: x.prop("start_line", 0) or 0):
        members.append([
            _safe(m.prop("name")),
            _safe(m.prop("signature")),
            _safe(m.prop("return_type")),
            _safe(m.prop("modifiers")),
            _safe(m.prop("summary")),
        ])
    for d in sorted(graph.get_children(entity.node_id, "CONTAINS"),
                    key=lambda x: x.prop("start_line", 0) or 0):
        if d.primary_label in DML_LABELS:
            members.append([
                d.primary_label,
                _truncate(d.prop("node_code")),
                "-",
                f"Line {_safe(d.prop('start_line'))}",
                "-",
            ])
    parts.append("\n#### 메서드\n")
    parts.append(_md_table(SCHEMA_MEMBER, members))

    # ── 필드 (FIELD + Variable 통합) ──
    fields = []
    for f in sorted(graph.get_children(entity.node_id, "HAS_VARIABLE"),
                    key=lambda x: x.prop("start_line", 0) or 0):
        fields.append([
            _safe(f.prop("name")),
            _safe(f.prop("variable_type")),
            _safe(f.prop("modifiers")),
            _safe(f.prop("summary")),
        ])
    for v in graph.get_children(entity.node_id, "DECLARES"):
        fields.append([
            _safe(v.prop("name")),
            _safe(v.prop("role")),
            "-",
            _safe(v.prop("summary")),
        ])
    parts.append("\n#### 필드\n")
    parts.append(_md_table(SCHEMA_FIELD_VAR, fields))

    # ── 구조 관계 (UML + ETL 통합) ──
    struct_rels = []
    for r in graph.get_outgoing_rels(entity.node_id, ENTITY_STRUCT_REL_TYPES):
        struct_rels.append([
            "→", r.rel_type, graph.node_display(r.end_node_id),
            _format_rel_props(r.properties),
        ])
    for r in graph.get_incoming_rels(entity.node_id, ENTITY_STRUCT_REL_TYPES):
        struct_rels.append([
            "←", r.rel_type, graph.node_display(r.start_node_id),
            _format_rel_props(r.properties),
        ])
    parts.append("\n#### 구조 관계\n")
    parts.append(_md_table(SCHEMA_STRUCT_REL, struct_rels))

    # ── 호출 관계 (CALLS + CALL + USES 통합) ──
    call_rels = []
    for r in graph.get_outgoing_rels(entity.node_id, ENTITY_CALL_REL_TYPES):
        call_rels.append(["→", graph.node_display(r.end_node_id), _safe(r.prop("call_code"))])
    # 메서드 레벨 호출 수집 (CLASS인 경우)
    for m in graph.get_children(entity.node_id, "HAS_FUNCTION"):
        for r in graph.get_outgoing_rels(m.node_id, ENTITY_CALL_REL_TYPES):
            call_rels.append(["→", graph.node_display(r.end_node_id), _safe(r.prop("call_code"))])
    for r in graph.get_incoming_rels(entity.node_id, ENTITY_CALL_REL_TYPES):
        call_rels.append(["←", graph.node_display(r.start_node_id), _safe(r.prop("call_code"))])
    parts.append("\n#### 호출 관계\n")
    parts.append(_md_table(SCHEMA_CALL_REL, call_rels))

    # ── 데이터 접근 (FROM/READS/WRITES/REFER_TO 통합) ──
    data_access = []
    for r in graph.get_outgoing_rels(entity.node_id, ENTITY_DATA_REL_TYPES):
        data_access.append([
            graph.node_display(r.end_node_id), r.rel_type,
            _safe(r.prop("evidence")), _safe(r.prop("confidence")),
        ])
    # 메서드/DML 자식의 테이블 접근도 수집
    for m in graph.get_children(entity.node_id, "HAS_FUNCTION"):
        for r in graph.get_outgoing_rels(m.node_id, ENTITY_DATA_REL_TYPES):
            data_access.append([
                graph.node_display(r.end_node_id), r.rel_type,
                _safe(r.prop("evidence")), _safe(r.prop("confidence")),
            ])
    for d in graph.get_children(entity.node_id, "CONTAINS"):
        if d.primary_label in DML_LABELS:
            for r in graph.get_outgoing_rels(d.node_id, ENTITY_DATA_REL_TYPES):
                data_access.append([
                    graph.node_display(r.end_node_id), r.rel_type,
                    _safe(r.prop("evidence")), _safe(r.prop("confidence")),
                ])
    # 필드 레벨 접근도 수집
    for f in graph.get_children(entity.node_id, "HAS_VARIABLE"):
        for r in graph.get_outgoing_rels(f.node_id, ENTITY_DATA_REL_TYPES):
            data_access.append([
                graph.node_display(r.end_node_id), r.rel_type,
                _safe(r.prop("evidence")), _safe(r.prop("confidence")),
            ])
    parts.append("\n#### 데이터 접근\n")
    parts.append(_md_table(SCHEMA_DATA_ACCESS, data_access))

    # ── 요약 코드 ──
    parts.append("\n#### 요약 코드\n")
    parts.append(_code_block(entity.prop("summarized_code")))

    return "\n".join(parts)


def _render_external(graph: GraphData, external_entities: List[GraphNode]) -> str:
    """## 4. 외부 의존성 (EXTERNAL) 내용."""
    rows = []
    for ent in sorted(external_entities, key=lambda c: c.display_name):
        referrers = set()
        for rel in graph.get_incoming_rels(ent.node_id):
            if rel.rel_type in EXCLUDED_REL_TYPES:
                continue
            src = graph.get_node(rel.start_node_id)
            if src and src.is_internal:
                referrers.add(src.display_name)
        rows.append([
            ent.display_name,
            ent.primary_label,
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

        # 기본 정보
        info_pairs = [
            ("이름", chunk.display_name),
            ("파일", _safe(chunk.prop("file_name", chunk.prop("file_path", "")))),
            ("종류", _safe(chunk.prop("kind", chunk.prop("type", "")))),
            ("요약", _safe(chunk.prop("summary"))),
        ]
        parts.append("#### 기본 정보\n")
        parts.append(_prop_table(info_pairs))

        # 관계
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

        # 파일 정보
        info_pairs = [
            ("파일 경로", _safe(ntf.prop("file_path"))),
            ("확장자", _safe(ntf.prop("ext"))),
            ("디렉토리", _safe(ntf.prop("directory"))),
        ]
        parts.append("#### 파일 정보\n")
        parts.append(_prop_table(info_pairs))

        # 블록 목록
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

        # 테이블 참조 (모든 블록의 REFERS_TO 통합)
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

        # 테이블 정보
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

        # 컬럼
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

        # FK 관계
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
        for entity_name, ops in sorted(access_data[tbl_name].items()):
            access_rows.append([tbl_name, entity_name, ", ".join(sorted(ops))])
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
        if ntype == "FUNCTION":
            parents = graph.get_parents(node.node_id, "HAS_FUNCTION")
            if parents:
                name = parents[0].display_name
                ntype = parents[0].primary_label
        ep_rows.append([name, ntype])
    # 중복 제거
    seen = set()
    unique_ep_rows = []
    for row in ep_rows:
        key = row[0]
        if key not in seen:
            seen.add(key)
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
    # INTERNAL 엔티티 = MODULE + PROCEDURE + FUNCTION + TRIGGER 중 INTERNAL
    internal_entities = [n for n in graph.classes + graph.procedures if n.is_internal]
    # EXTERNAL 엔티티
    external_entities = [n for n in graph.classes + graph.procedures if n.is_external]

    parts = ["# 레거시 시스템 분석 보고서\n"]

    # 1. 시스템 개요
    parts.append(_render_overview(graph))

    # 2. 패키지 구조
    parts.append(_render_packages(graph))

    # 3. 엔티티 상세 (INTERNAL)
    entity_parts = ["## 3. 엔티티 상세 (INTERNAL)\n"]
    if not internal_entities:
        entity_parts.append("INTERNAL 엔티티 없음.\n")
    else:
        for entity in sorted(internal_entities, key=lambda n: n.prop("fqn", "") or n.display_name):
            entity_parts.append(_render_entity_detail(graph, entity))
            entity_parts.append("")
    parts.append("\n".join(entity_parts))

    # 4. 외부 의존성 (EXTERNAL)
    ext_parts = ["## 4. 외부 의존성 (EXTERNAL)\n"]
    ext_parts.append(_render_external(graph, external_entities))
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


def _generate_report(graph: GraphData, strategy: str = "auto") -> str:  # noqa: ARG001
    """GraphData → 마크다운 보고서. strategy는 호환성 유지용 (무시됨)."""
    logger.info(
        "[REPORT] 보고서 생성 | 노드=%d, 관계=%d",
        len(graph.nodes), len(graph.relationships),
    )
    return _build_report(graph)


def generate_analysis_report_from_graph(graph_data: Dict[str, Any], strategy: str = "auto") -> str:
    """Neo4j 조회 결과 dict → 마크다운 분석 보고서.

    Args:
        graph_data: {"Nodes": [...], "Relationships": [...]} 형태의 dict
        strategy: 호환성 유지용 파라미터 (무시됨, 항상 단일 템플릿)

    Returns:
        마크다운 보고서 문자열
    """
    graph = _load_graph_from_dict(graph_data)
    return _generate_report(graph, strategy)


def generate_analysis_report(json_path: str, strategy: str = "auto") -> str:
    """Neo4j export JSON 파일 → 마크다운 분석 보고서.

    Args:
        json_path: Neo4j export JSON 파일 경로
        strategy: 호환성 유지용 파라미터 (무시됨, 항상 단일 템플릿)

    Returns:
        마크다운 보고서 문자열
    """
    graph = _load_graph(json_path)
    return _generate_report(graph, strategy)
