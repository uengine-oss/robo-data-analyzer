#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Neo4j 그래프 분석 및 레포트 생성 프로그램
Neo4j에서 추출한 그래프 데이터(Nodes + Relationships)를 분석하여
모든 노드 타입과 관계를 누락 없이 상세 HTML 레포트로 생성합니다.

지원 노드 타입:
- Framework AST: CLASS, INTERFACE, ENUM (INTERNAL/EXTERNAL 레이블), METHOD, FIELD, PACKAGE
- User Story: UserStory, AcceptanceCriteria
- Nontarget: NONTARGET_FILE, NONTARGET_BLOCK
- Database: Table, Column
- DBMS: PROCEDURE, FUNCTION, SELECT, INSERT, UPDATE, DELETE, MERGE,
        EXECUTE_IMMEDIATE, FETCH, CTE, OPEN_CURSOR, CREATE_TEMP_TABLE, Variable

지원 관계 타입:
- 구조적: CONTAINS, HAS_METHOD, HAS_FIELD, BELONGS_TO_PACKAGE, CONTAINS_PACKAGE, HAS_COLUMN
- UML: ASSOCIATION, COMPOSITION, AGGREGATION, DEPENDENCY, EXTENDS, IMPLEMENTS
- 의존성: CALLS, USES, CALL
- User Story: HAS_USER_STORY, HAS_AC
- DB: REFERS_TO, FROM, WRITES, FK_TO_TABLE, FK_TO
"""

import json
from dataclasses import dataclass, field
from typing import List, Dict, Set, Optional, Any, Tuple
from collections import defaultdict


# ============================================================================
# 상수 정의
# ============================================================================

CLASS_LABELS = {"CLASS", "INTERFACE", "ENUM"}
SCOPE_LABELS = {"INTERNAL", "EXTERNAL"}
DML_LABELS = {"SELECT", "INSERT", "UPDATE", "DELETE", "MERGE",
              "EXECUTE_IMMEDIATE", "FETCH", "CTE", "OPEN_CURSOR", "CREATE_TEMP_TABLE"}

UML_REL_TYPES = {"ASSOCIATION", "COMPOSITION", "AGGREGATION", "DEPENDENCY", "EXTENDS", "IMPLEMENTS"}
STRUCTURAL_REL_TYPES = {"CONTAINS", "HAS_METHOD", "HAS_FIELD",
                        "BELONGS_TO_PACKAGE", "CONTAINS_PACKAGE", "HAS_COLUMN"}
DEPENDENCY_REL_TYPES = {"CALLS", "USES", "CALL"}
USER_STORY_REL_TYPES = {"HAS_USER_STORY", "HAS_AC"}
DB_REL_TYPES = {"REFERS_TO", "FROM", "WRITES", "FK_TO_TABLE", "FK_TO"}


# ============================================================================
# 유틸리티
# ============================================================================

def html_escape(text: str) -> str:
    """HTML 특수 문자 이스케이프"""
    if not text:
        return ""
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#x27;"))


def _prop(props: Dict[str, Any], key: str, default: Any = "") -> Any:
    """properties에서 안전하게 값 추출"""
    val = props.get(key, default)
    return val if val is not None else default


# ============================================================================
# 데이터 클래스
# ============================================================================

@dataclass
class GraphNode:
    """범용 그래프 노드"""
    node_id: str
    labels: List[str]
    properties: Dict[str, Any]

    @property
    def primary_label(self) -> str:
        """INTERNAL/EXTERNAL 등 보조 레이블 제외한 주요 레이블"""
        for lbl in self.labels:
            if lbl not in SCOPE_LABELS:
                return lbl
        return self.labels[0] if self.labels else "UNKNOWN"

    @property
    def is_internal(self) -> bool:
        return "INTERNAL" in self.labels

    @property
    def is_external(self) -> bool:
        return "EXTERNAL" in self.labels

    @property
    def scope_label(self) -> str:
        if self.is_internal:
            return "INTERNAL"
        if self.is_external:
            return "EXTERNAL"
        return ""

    @property
    def stereotype(self) -> str:
        """스테레오타입: stereotype 속성 또는 레이블에서 추출"""
        st = self.properties.get("stereotype", "")
        if st:
            return st
        # labels에서 CLASS/INTERFACE/ENUM/METHOD/INTERNAL/EXTERNAL 제외한 나머지
        extras = [lbl for lbl in self.labels
                  if lbl not in CLASS_LABELS and lbl not in SCOPE_LABELS
                  and lbl not in {"METHOD", "FIELD", "PACKAGE", "UserStory",
                                  "AcceptanceCriteria", "NONTARGET_FILE",
                                  "NONTARGET_BLOCK", "Table", "Column",
                                  "PROCEDURE", "FUNCTION", "Variable"}
                  and lbl not in DML_LABELS]
        return extras[0] if extras else ""

    def prop(self, key: str, default: Any = "") -> Any:
        return _prop(self.properties, key, default)


@dataclass
class GraphRelationship:
    """범용 그래프 관계"""
    rel_id: str
    rel_type: str
    properties: Dict[str, Any]
    start_node_id: str
    end_node_id: str

    def prop(self, key: str, default: Any = "") -> Any:
        return _prop(self.properties, key, default)


# ============================================================================
# GraphAnalyzer 메인 클래스
# ============================================================================

class GraphAnalyzer:
    """Neo4j 그래프 분석기"""
    
    def __init__(self, json_path: str):
        self.json_path = json_path
        self.nodes: Dict[str, GraphNode] = {}
        self.relationships: List[GraphRelationship] = []

        # 노드 분류
        self.classes: List[GraphNode] = []
        self.methods: List[GraphNode] = []
        self.fields: List[GraphNode] = []
        self.packages: List[GraphNode] = []
        self.user_stories: List[GraphNode] = []
        self.acceptance_criteria: List[GraphNode] = []
        self.nontarget_files: List[GraphNode] = []
        self.nontarget_blocks: List[GraphNode] = []
        self.tables: List[GraphNode] = []
        self.columns: List[GraphNode] = []
        self.procedures: List[GraphNode] = []
        self.dml_nodes: List[GraphNode] = []
        self.variables: List[GraphNode] = []
        self.other_nodes: List[GraphNode] = []

        # 관계 분류
        self.structural_rels: List[GraphRelationship] = []
        self.uml_rels: List[GraphRelationship] = []
        self.dependency_rels: List[GraphRelationship] = []
        self.user_story_rels: List[GraphRelationship] = []
        self.db_rels: List[GraphRelationship] = []
        self.other_rels: List[GraphRelationship] = []

        # 인덱스: node_id → 연결된 관계
        self._outgoing: Dict[str, List[GraphRelationship]] = defaultdict(list)
        self._incoming: Dict[str, List[GraphRelationship]] = defaultdict(list)
        
        # 레이블별 카운트 (이미지와 일치하도록)
        self._label_counts: Dict[str, int] = defaultdict(int)

    # ------------------------------------------------------------------
    # 데이터 로드
    # ------------------------------------------------------------------
        
    def load_data(self):
        """그래프 JSON 데이터 로드"""
        with open(self.json_path, 'rb') as f:
            raw_content = f.read()
        
        content = raw_content.decode('utf-8', errors='ignore')
        if content.startswith('\ufeff'):
            content = content[1:]
        
        data = json.loads(content, strict=False)
        
        # 디버깅: 데이터 타입과 구조 확인
        print(f"[DEBUG] JSON 타입: {type(data)}")
        if isinstance(data, dict):
            print(f"[DEBUG] 딕셔너리 키: {list(data.keys())[:10]}")
        elif isinstance(data, list):
            print(f"[DEBUG] 리스트 길이: {len(data)}")
            if len(data) > 0:
                print(f"[DEBUG] 첫 항목 타입: {type(data[0])}")
                if isinstance(data[0], dict):
                    print(f"[DEBUG] 첫 항목 키: {list(data[0].keys())[:20]}")
        
        # 형식 1: {"Nodes": [...], "Relationships": [...]} 딕셔너리
        if isinstance(data, dict):
            raw_nodes = data.get("Nodes", [])
            raw_rels = data.get("Relationships", [])
            print(f"[DEBUG] 딕셔너리 형식: 노드 {len(raw_nodes)}개, 관계 {len(raw_rels)}개")
        # 형식 2: [{"n": {...}, "r": {...}, "m": {...}}, ...] Neo4j export 형식
        elif isinstance(data, list):
            raw_nodes = []
            raw_rels = []
            for item in data:
                if isinstance(item, dict):
                    # Neo4j export 형식: {"n": {...}, "r": {...}, "m": {...}}
                    if "n" in item:
                        raw_nodes.append(item["n"])
                    if "r" in item:
                        raw_rels.append(item["r"])
                    # 기존 형식 지원: "Node ID"가 있으면 노드
                    elif "Node ID" in item or "node_id" in item or "id" in item:
                        raw_nodes.append(item)
                    # 기존 형식 지원: "Relationship ID" 또는 "Type"이 있으면 관계
                    elif "Relationship ID" in item or "Type" in item or "rel_type" in item:
                        raw_rels.append(item)
                    # 기존 형식 지원: 레이블로 판단 (노드에만 레이블이 있음)
                    elif "Labels" in item or "labels" in item or "label" in item:
                        raw_nodes.append(item)
            print(f"[DEBUG] 리스트 형식: 추출된 노드 항목 {len(raw_nodes)}개, 관계 {len(raw_rels)}개 (중복 제거 후 고유 노드 수는 파싱 후 확인)")
        else:
            raw_nodes = []
            raw_rels = []
            print(f"[DEBUG] 알 수 없는 형식: {type(data)}")

        # 노드 파싱
        for raw in raw_nodes:
            # Neo4j export 형식: identity 또는 elementId 사용
            node_id = str(raw.get("identity") or raw.get("elementId") or raw.get("Node ID") or raw.get("node_id") or raw.get("id") or "")
            # Neo4j export 형식: labels는 리스트
            labels = raw.get("labels") or raw.get("Labels") or raw.get("label", [])
            if isinstance(labels, str):
                labels = [labels]
            # Neo4j export 형식: properties는 딕셔너리
            properties = raw.get("properties") or raw.get("Properties") or {}
            # properties가 없고 raw 자체가 properties인 경우
            if not properties and "Node ID" not in raw and "node_id" not in raw and "identity" not in raw and "elementId" not in raw:
                properties = {k: v for k, v in raw.items() if k not in ["Labels", "labels", "label", "identity", "elementId"]}
                # labels는 별도로 추출
                if "Labels" in raw:
                    labels = raw["Labels"]
                elif "labels" in raw:
                    labels = raw["labels"]
                elif "label" in raw:
                    labels = raw["label"]
                    if isinstance(labels, str):
                        labels = [labels]
            
            node = GraphNode(
                node_id=node_id,
                labels=labels if isinstance(labels, list) else [labels] if labels else [],
                properties=properties,
            )
            if node.node_id:
                self.nodes[node.node_id] = node

        # 관계 파싱
        for raw in raw_rels:
            # Neo4j export 형식: identity 또는 elementId 사용
            rel_id = str(raw.get("identity") or raw.get("elementId") or raw.get("Relationship ID") or raw.get("relationship_id") or raw.get("id") or "")
            # Neo4j export 형식: type 사용
            rel_type = raw.get("type") or raw.get("Type") or raw.get("rel_type") or ""
            # Neo4j export 형식: properties는 딕셔너리
            properties = raw.get("properties") or raw.get("Properties") or {}
            # Neo4j export 형식: start와 end는 노드 객체 또는 ID (정수 또는 문자열)
            start_node = raw.get("start")
            end_node = raw.get("end")
            if isinstance(start_node, dict):
                start_node_id = str(start_node.get("identity") or start_node.get("elementId") or "")
            elif start_node is not None:
                # start가 정수 ID인 경우
                start_node_id = str(start_node)
            else:
                start_node_id = str(raw.get("startNodeElementId") or raw.get("Start Node ID") or raw.get("start_node_id") or raw.get("start_id") or "")
            if isinstance(end_node, dict):
                end_node_id = str(end_node.get("identity") or end_node.get("elementId") or "")
            elif end_node is not None:
                # end가 정수 ID인 경우
                end_node_id = str(end_node)
            else:
                end_node_id = str(raw.get("endNodeElementId") or raw.get("End Node ID") or raw.get("end_node_id") or raw.get("end_id") or "")
            
            # properties가 없고 raw 자체가 properties인 경우
            if not properties and "Relationship ID" not in raw and "relationship_id" not in raw and "identity" not in raw and "elementId" not in raw:
                skip_keys = {"Type", "type", "rel_type", "Start Node ID", "start_node_id", "start_id",
                            "End Node ID", "end_node_id", "end_id", "Relationship ID", "relationship_id", "id",
                            "identity", "elementId", "start", "end", "startNodeElementId", "endNodeElementId"}
                properties = {k: v for k, v in raw.items() if k not in skip_keys}
            
            rel = GraphRelationship(
                rel_id=rel_id,
                rel_type=rel_type,
                properties=properties,
                start_node_id=start_node_id,
                end_node_id=end_node_id,
            )
            if rel.rel_type and rel.start_node_id and rel.end_node_id:
                self.relationships.append(rel)
                self._outgoing[rel.start_node_id].append(rel)
                self._incoming[rel.end_node_id].append(rel)

    # ------------------------------------------------------------------
    # 노드/관계 분류
    # ------------------------------------------------------------------

    def analyze(self):
        """노드와 관계를 타입별로 분류"""
        # 레이블별 카운트 초기화
        self._label_counts.clear()
        
        for node in self.nodes.values():
            # 레이블별 카운트
            for label in node.labels:
                self._label_counts[label] += 1
            self._categorize_node(node)

        for rel in self.relationships:
            self._categorize_relationship(rel)

        # 정렬
        self.classes.sort(key=lambda n: (n.prop("package_name"), n.prop("class_name")))
        self.methods.sort(key=lambda n: (n.prop("class_name"), n.prop("start_line", 0)))
        self.fields.sort(key=lambda n: (n.prop("class_name"), n.prop("start_line", 0)))
        self.packages.sort(key=lambda n: n.prop("fqn"))
        self.tables.sort(key=lambda n: (n.prop("schema"), n.prop("name")))
        self.columns.sort(key=lambda n: n.prop("fqn"))
        self.procedures.sort(key=lambda n: n.prop("procedure_name", n.prop("name")))

    def _categorize_node(self, node: GraphNode):
        labels_set = set(node.labels)
        if labels_set & CLASS_LABELS:
            self.classes.append(node)
        elif "METHOD" in labels_set:
            self.methods.append(node)
        elif "FIELD" in labels_set:
            self.fields.append(node)
        elif "PACKAGE" in labels_set:
            self.packages.append(node)
        elif "UserStory" in labels_set:
            self.user_stories.append(node)
        elif "AcceptanceCriteria" in labels_set:
            self.acceptance_criteria.append(node)
        elif "NONTARGET_FILE" in labels_set:
            self.nontarget_files.append(node)
        elif "NONTARGET_BLOCK" in labels_set:
            self.nontarget_blocks.append(node)
        elif "Table" in labels_set:
            self.tables.append(node)
        elif "Column" in labels_set:
            self.columns.append(node)
        elif "PROCEDURE" in labels_set or "FUNCTION" in labels_set:
            self.procedures.append(node)
        elif labels_set & DML_LABELS:
            self.dml_nodes.append(node)
        elif "Variable" in labels_set:
            self.variables.append(node)
        else:
            self.other_nodes.append(node)

    def _categorize_relationship(self, rel: GraphRelationship):
        rt = rel.rel_type
        if rt in UML_REL_TYPES:
            self.uml_rels.append(rel)
        elif rt in STRUCTURAL_REL_TYPES:
            self.structural_rels.append(rel)
        elif rt in DEPENDENCY_REL_TYPES:
            self.dependency_rels.append(rel)
        elif rt in USER_STORY_REL_TYPES:
            self.user_story_rels.append(rel)
        elif rt in DB_REL_TYPES:
            self.db_rels.append(rel)
        else:
            self.other_rels.append(rel)

    # ------------------------------------------------------------------
    # 헬퍼
    # ------------------------------------------------------------------

    def _get_node(self, node_id: str) -> Optional[GraphNode]:
        return self.nodes.get(node_id)

    def _node_display_name(self, node_id: str) -> str:
        node = self._get_node(node_id)
        if not node:
            return node_id
        p = node.properties
        return (p.get("class_name") or p.get("name") or p.get("procedure_name")
                or p.get("file_name") or p.get("fqn") or p.get("id") or node_id)

    def _get_children(self, parent_id: str, rel_type: str) -> List[GraphNode]:
        """parent_id에서 rel_type으로 나가는 자식 노드 목록"""
        result = []
        for rel in self._outgoing.get(parent_id, []):
            if rel.rel_type == rel_type:
                child = self._get_node(rel.end_node_id)
                if child:
                    result.append(child)
        return result

    def _get_parents(self, child_id: str, rel_type: str) -> List[GraphNode]:
        """child_id로 들어오는 rel_type 부모 노드 목록"""
        result = []
        for rel in self._incoming.get(child_id, []):
            if rel.rel_type == rel_type:
                parent = self._get_node(rel.start_node_id)
                if parent:
                    result.append(parent)
        return result

    def _get_outgoing_rels(self, node_id: str, rel_types: Set[str] = None) -> List[GraphRelationship]:
        rels = self._outgoing.get(node_id, [])
        if rel_types:
            return [r for r in rels if r.rel_type in rel_types]
        return rels

    def _get_incoming_rels(self, node_id: str, rel_types: Set[str] = None) -> List[GraphRelationship]:
        rels = self._incoming.get(node_id, [])
        if rel_types:
            return [r for r in rels if r.rel_type in rel_types]
        return rels

    # ------------------------------------------------------------------
    # 레포트 생성
    # ------------------------------------------------------------------

    def generate_report(self, output_path: str = 'graph_analysis_report.html'):
        """HTML 레포트 생성"""
        html_content = self._generate_html()
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        print(f"[OK] 레포트 생성 완료: {output_path}")

    # ------------------------------------------------------------------
    # HTML 전체 생성
    # ------------------------------------------------------------------

    def _generate_html(self) -> str:
        parts: List[str] = []
        parts.append(self._html_head())
        parts.append(self._html_header())
        parts.append(self._html_summary())
        parts.append('<div class="content">')
        parts.append(self._html_toc())
        parts.append(self._html_packages_section())
        parts.append(self._html_classes_section())
        parts.append(self._html_nontarget_section())
        parts.append(self._html_tables_section())
        parts.append(self._html_procedures_section())
        parts.append(self._html_user_stories_section())
        parts.append(self._html_uml_relationships_section())
        parts.append(self._html_all_relationships_section())
        parts.append('</div>')  # .content
        parts.append(self._html_footer())
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # HTML head + CSS
    # ------------------------------------------------------------------

    def _html_head(self) -> str:
        return """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Neo4j 그래프 분석 레포트</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',Tahoma,Geneva,Verdana,sans-serif;line-height:1.6;color:#333;background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);padding:20px}
.container{max-width:1500px;margin:0 auto;background:#fff;border-radius:15px;box-shadow:0 10px 40px rgba(0,0,0,.3);overflow:hidden}
.header{background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);color:#fff;padding:40px;text-align:center}
.header h1{font-size:2.5em;margin-bottom:10px;text-shadow:2px 2px 4px rgba(0,0,0,.3)}
.header .subtitle{font-size:1.2em;opacity:.9}
.summary{padding:30px 40px;background:#f8f9fa;border-bottom:3px solid #667eea}
.summary h2{color:#667eea;margin-bottom:20px;font-size:1.8em}
.stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:15px;margin-top:20px}
.stat-card{background:#fff;padding:18px;border-radius:10px;box-shadow:0 2px 10px rgba(0,0,0,.1);text-align:center;border-left:4px solid #667eea}
.stat-card .number{font-size:2.2em;font-weight:700;color:#667eea}
.stat-card .label{color:#666;margin-top:5px;font-size:.9em}
.stat-card.class-card{border-left-color:#6f42c1}
.stat-card.class-card .number{color:#6f42c1}
.stat-card.method-card{border-left-color:#28a745}
.stat-card.method-card .number{color:#28a745}
.stat-card.table-card{border-left-color:#17a2b8}
.stat-card.table-card .number{color:#17a2b8}
.stat-card.rel-card{border-left-color:#fd7e14}
.stat-card.rel-card .number{color:#fd7e14}
.content{padding:40px}
.section-group{margin-bottom:50px}
.section-group > h2{color:#667eea;font-size:1.8em;margin-bottom:25px;padding-bottom:12px;border-bottom:3px solid #667eea}
.card{margin-bottom:30px;border:2px solid #e0e0e0;border-radius:10px;overflow:hidden;transition:transform .2s}
.card:hover{transform:translateY(-3px);box-shadow:0 5px 20px rgba(0,0,0,.1)}
.card-header{padding:18px 25px;color:#fff;cursor:pointer}
.card-header h3{font-size:1.3em;margin-bottom:3px}
.card-header .meta{font-size:.85em;opacity:.9;margin-top:6px}
.card-body{padding:25px}
.bg-class{background:linear-gradient(135deg,#6f42c1,#764ba2)}
.bg-interface{background:linear-gradient(135deg,#0d6efd,#0a58ca)}
.bg-enum{background:linear-gradient(135deg,#0dcaf0,#0aa2c0)}
.bg-method{background:linear-gradient(135deg,#198754,#157347)}
.bg-package{background:linear-gradient(135deg,#6c757d,#495057)}
.bg-table{background:linear-gradient(135deg,#17a2b8,#138496)}
.bg-nontarget{background:linear-gradient(135deg,#20c997,#1aa179)}
.bg-procedure{background:linear-gradient(135deg,#dc3545,#b02a37)}
.bg-userstory{background:linear-gradient(135deg,#ffc107,#e0a800);color:#333!important}
.bg-userstory h3,.bg-userstory .meta{color:#333!important}
.bg-uml{background:linear-gradient(135deg,#e83e8c,#c2185b)}
.sub-section{margin-bottom:22px}
.sub-section h4{color:#667eea;font-size:1.15em;margin-bottom:12px;padding-bottom:8px;border-bottom:2px solid #e0e0e0}
.tbl{width:100%;border-collapse:collapse;margin-top:10px;background:#fff;box-shadow:0 1px 6px rgba(0,0,0,.05);border-radius:8px;overflow:hidden}
.tbl thead{background:linear-gradient(135deg,#667eea,#764ba2);color:#fff}
.tbl th{padding:12px 14px;text-align:left;font-weight:600;font-size:.9em}
.tbl td{padding:10px 14px;border-bottom:1px solid #e9ecef;font-size:.88em}
.tbl tbody tr:hover{background:#f8f9fa}
.badge{display:inline-block;padding:3px 10px;border-radius:20px;font-size:.8em;font-weight:600;margin-right:4px;margin-bottom:4px}
.badge-internal{background:#d4edda;color:#155724}
.badge-external{background:#f8d7da;color:#721c24}
.badge-stereotype{background:#e2e3f1;color:#383d6e}
.badge-class{background:#e8daf5;color:#6f42c1}
.badge-interface{background:#cfe2ff;color:#084298}
.badge-enum{background:#cff4fc;color:#055160}
.badge-method{background:#d1e7dd;color:#0f5132}
.badge-field{background:#fff3cd;color:#664d03}
.badge-package{background:#e2e3e5;color:#41464b}
.badge-table{background:#cff4fc;color:#055160}
.badge-column{background:#d3d3e8;color:#3d3d6b}
.badge-userstory{background:#fff3cd;color:#664d03}
.badge-ac{background:#fde5c8;color:#7a4100}
.badge-nontarget{background:#d2f4ea;color:#0a7c5a}
.badge-procedure{background:#f5c6cb;color:#721c24}
.badge-variable{background:#e2e3e5;color:#41464b}
.badge-select{background:#17a2b8;color:#fff}
.badge-insert{background:#28a745;color:#fff}
.badge-update{background:#ffc107;color:#333}
.badge-delete{background:#dc3545;color:#fff}
.badge-merge{background:#6f42c1;color:#fff}
.badge-extends{background:#0d6efd;color:#fff}
.badge-implements{background:#6610f2;color:#fff}
.badge-association{background:#198754;color:#fff}
.badge-composition{background:#0dcaf0;color:#333}
.badge-aggregation{background:#20c997;color:#333}
.badge-dependency{background:#fd7e14;color:#fff}
.badge-calls{background:#e83e8c;color:#fff}
.badge-uses{background:#6f42c1;color:#fff}
.badge-contains{background:#6c757d;color:#fff}
.badge-has_method{background:#198754;color:#fff}
.badge-has_field{background:#fd7e14;color:#fff}
.badge-belongs_to_package{background:#495057;color:#fff}
.badge-has_user_story{background:#ffc107;color:#333}
.badge-has_ac{background:#e0a800;color:#333}
.badge-refers_to{background:#795548;color:#fff}
.badge-from{background:#2196f3;color:#fff}
.badge-writes{background:#f44336;color:#fff}
.badge-fk_to_table{background:#607d8b;color:#fff}
.badge-fk_to{background:#78909c;color:#fff}
.badge-has_column{background:#78909c;color:#fff}
.badge-call{background:#e91e63;color:#fff}
.badge-contains_package{background:#9e9e9e;color:#fff}
.summary-text{background:#f8f9fa;padding:18px;border-radius:8px;border-left:4px solid #28a745;line-height:1.8;font-size:0.95em;text-align:justify;word-spacing:0.1em;letter-spacing:0.01em}
.summary-text p{margin-bottom:12px}
.summary-text p:last-child{margin-bottom:0}
.code-block{background:#282c34;color:#abb2bf;padding:18px;border-radius:8px;overflow-x:auto;font-family:'Courier New',monospace;font-size:.88em;line-height:1.5;margin-top:10px;white-space:pre-wrap;word-break:break-all}
.list{list-style:none;padding-left:0}
.list li{padding:8px 12px;margin-bottom:5px;background:#f8f9fa;border-radius:5px;border-left:3px solid #667eea}
.toc{background:#f8f9fa;padding:20px 25px;border-radius:8px;margin-bottom:30px}
.toc h3{color:#667eea;margin-bottom:15px}
.toc ul{list-style:none}
.toc li{padding:5px 0}
.toc a{color:#667eea;text-decoration:none}
.toc a:hover{color:#764ba2;text-decoration:underline}
.toc .sub{padding-left:20px;font-size:.9em}
.ac-card{background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:15px;margin-bottom:12px}
.ac-card h5{color:#92400e;margin-bottom:8px;font-size:1em}
.ac-row{display:grid;grid-template-columns:80px 1fr;gap:4px 10px;font-size:.9em}
.ac-label{font-weight:600;color:#666}
.rel-arrow{color:#999;margin:0 6px}
.footer{background:#282c34;color:#fff;text-align:center;padding:20px;margin-top:40px}
@media print{body{background:#fff;padding:0}.container{box-shadow:none}.card{page-break-inside:avoid}}
</style>
</head>
<body>
<div class="container">"""

    # ------------------------------------------------------------------
    # HTML header
    # ------------------------------------------------------------------

    def _html_header(self) -> str:
        return """
<div class="header">
    <h1>📊 Neo4j 그래프 분석 레포트</h1>
    <div class="subtitle">코드 그래프 데이터 기반 상세 분석 결과</div>
    <div style="margin-top:20px;font-size:0.9em;opacity:0.9;text-align:left;max-width:800px;margin-left:auto;margin-right:auto;padding:15px;background:rgba(255,255,255,0.1);border-radius:8px">
        <strong>📋 문서 구성</strong><br>
        이 레포트는 Neo4j에서 추출한 그래프 데이터를 분석하여 생성되었습니다.<br>
        • <strong>전체 요약</strong>: 노드와 관계의 전체 통계 및 레이블별 카운트<br>
        • <strong>패키지 계층 구조</strong>: 패키지 간 포함 관계 및 소속 클래스<br>
        • <strong>클래스/인터페이스/열거형</strong>: 각 클래스의 상세 정보, 메서드, 필드, UML 관계<br>
        • <strong>Query 파일/블록</strong>: NONTARGET_FILE과 NONTARGET_BLOCK 정보<br>
        • <strong>테이블/컬럼</strong>: 데이터베이스 스키마 정보<br>
        • <strong>프로시저/함수</strong>: DBMS 프로시저 및 함수 정보<br>
        • <strong>User Story</strong>: 사용자 스토리 및 수용 기준<br>
        • <strong>UML 관계</strong>: 클래스 간 상속, 구현, 의존성 관계<br>
        • <strong>전체 관계 요약</strong>: 모든 관계 타입별 통계
    </div>
</div>"""

    # ------------------------------------------------------------------
    # HTML summary
    # ------------------------------------------------------------------

    def _html_summary(self) -> str:
        total_nodes = len(self.nodes)
        total_rels = len(self.relationships)
        
        # 레이블별 카운트 (이미지와 일치하도록)
        method_count = self._label_counts.get("METHOD", 0)
        class_count = self._label_counts.get("CLASS", 0)
        interface_count = self._label_counts.get("INTERFACE", 0)
        enum_count = self._label_counts.get("ENUM", 0)
        column_count = self._label_counts.get("Column", 0)
        acceptance_criteria_count = self._label_counts.get("AcceptanceCriteria", 0)
        field_count = self._label_counts.get("FIELD", 0)
        package_count = self._label_counts.get("PACKAGE", 0)
        table_count = self._label_counts.get("Table", 0)
        user_story_count = self._label_counts.get("UserStory", 0)
        nontarget_block_count = self._label_counts.get("NONTARGET_BLOCK", 0)
        procedure_count = self._label_counts.get("PROCEDURE", 0) + self._label_counts.get("FUNCTION", 0)

        return f"""
<div class="summary">
    <h2>📈 전체 요약</h2>
    <div class="stats-grid">
        <div class="stat-card">
            <div class="number">{total_nodes}</div>
            <div class="label">총 노드 수 (고유)</div>
        </div>
        <div class="stat-card rel-card">
            <div class="number">{total_rels}</div>
            <div class="label">총 관계 수</div>
        </div>
        <div class="stat-card class-card">
            <div class="number">{class_count + interface_count + enum_count}</div>
            <div class="label">CLASS / INTERFACE / ENUM</div>
            <div style="font-size:0.75em;color:#999;margin-top:4px">CLASS: {class_count}, INTERFACE: {interface_count}, ENUM: {enum_count}</div>
        </div>
        <div class="stat-card method-card">
            <div class="number">{method_count}</div>
            <div class="label">METHOD</div>
        </div>
        <div class="stat-card">
            <div class="number">{field_count}</div>
            <div class="label">FIELD</div>
        </div>
        <div class="stat-card">
            <div class="number">{package_count}</div>
            <div class="label">PACKAGE</div>
        </div>
        <div class="stat-card table-card">
            <div class="number">{table_count}</div>
            <div class="label">Table</div>
        </div>
        <div class="stat-card">
            <div class="number">{column_count}</div>
            <div class="label">Column</div>
        </div>
        <div class="stat-card">
            <div class="number">{user_story_count}</div>
            <div class="label">UserStory</div>
        </div>
        <div class="stat-card">
            <div class="number">{acceptance_criteria_count}</div>
            <div class="label">AcceptanceCriteria</div>
        </div>
        <div class="stat-card">
            <div class="number">{nontarget_block_count}</div>
            <div class="label">Query 블록</div>
        </div>
        <div class="stat-card">
            <div class="number">{procedure_count}</div>
            <div class="label">PROCEDURE / FUNCTION</div>
        </div>
        <div class="stat-card rel-card">
            <div class="number">{len(self.uml_rels)}</div>
            <div class="label">UML 관계</div>
        </div>
    </div>
</div>"""

    # ------------------------------------------------------------------
    # Table of Contents
    # ------------------------------------------------------------------

    def _html_toc(self) -> str:
        h = ['<div class="toc"><h3>📑 목차</h3><ul>']
        if self.packages:
            h.append('<li><a href="#sec-packages">1. 패키지 계층 구조</a></li>')
        if self.classes:
            h.append('<li><a href="#sec-classes">2. 클래스 / 인터페이스 / 열거형</a></li>')
            h.append('<ul class="sub">')
            for i, cls in enumerate(self.classes, 1):
                name = cls.prop("class_name", cls.prop("name"))
                h.append(f'<li><a href="#cls-{i}">{html_escape(name)}</a></li>')
            h.append('</ul>')
        if self.nontarget_files or self.nontarget_blocks:
            h.append('<li><a href="#sec-nontarget">3. Query 파일 / 블록</a></li>')
        if self.tables:
            h.append('<li><a href="#sec-tables">4. 테이블 / 컬럼</a></li>')
        if self.procedures:
            h.append('<li><a href="#sec-procedures">5. 프로시저 / 함수</a></li>')
        if self.user_stories:
            h.append('<li><a href="#sec-userstories">6. User Story / Acceptance Criteria</a></li>')
        if self.uml_rels:
            h.append('<li><a href="#sec-uml">7. UML 관계</a></li>')
        h.append('<li><a href="#sec-allrels">8. 전체 관계 요약</a></li>')
        h.append('</ul></div>')
        return "\n".join(h)

    # ------------------------------------------------------------------
    # Packages
    # ------------------------------------------------------------------

    def _html_packages_section(self) -> str:
        if not self.packages:
            return ""
        h = ['<div class="section-group" id="sec-packages">',
             '<h2>📦 1. 패키지 계층 구조</h2>']

        # 패키지 계층 트리 구성
        pkg_map: Dict[str, GraphNode] = {}
        for p in self.packages:
            pkg_map[p.prop("fqn")] = p

        h.append('<table class="tbl"><thead><tr>')
        h.append('<th>FQN</th><th>이름</th><th>하위 패키지</th><th>소속 클래스</th></tr></thead><tbody>')

        for pkg in self.packages:
            fqn = pkg.prop("fqn")
            name = pkg.prop("name")
            children = self._get_children(pkg.node_id, "CONTAINS_PACKAGE")
            child_names = ", ".join(c.prop("name") for c in children) or "-"
            # 이 패키지에 속하는 클래스
            classes_in_pkg = [c for c in self.classes if c.prop("package_name") == fqn]
            cls_names = ", ".join(c.prop("class_name") for c in classes_in_pkg) or "-"

            h.append(f'<tr><td><strong>{html_escape(fqn)}</strong></td>'
                     f'<td>{html_escape(name)}</td>'
                     f'<td>{html_escape(child_names)}</td>'
                     f'<td>{html_escape(cls_names)}</td></tr>')

        h.append('</tbody></table></div>')
        return "\n".join(h)

    # ------------------------------------------------------------------
    # Classes / Interfaces / Enums
    # ------------------------------------------------------------------

    def _html_classes_section(self) -> str:
        if not self.classes:
            return ""
        h = ['<div class="section-group" id="sec-classes">',
             '<h2>🏗️ 2. 클래스 / 인터페이스 / 열거형</h2>']
        for i, cls in enumerate(self.classes, 1):
            h.append(self._html_class_card(cls, i))
        h.append('</div>')
        return "\n".join(h)

    def _html_class_card(self, cls: GraphNode, index: int) -> str:
        p = cls.properties
        label = cls.primary_label
        class_name = cls.prop("class_name", cls.prop("name"))
        pkg = cls.prop("package_name")
        fqn = cls.prop("fqn")
        summary = cls.prop("summary")
        stereotype = cls.stereotype
        scope = cls.scope_label
        start_line = cls.prop("start_line", 0)
        end_line = cls.prop("end_line", 0)
        file_name = cls.prop("file_name")
        directory = cls.prop("directory")
        extends_type = cls.prop("extends_type")
        implements_types = cls.prop("implements_types")
        annotations = cls.prop("annotations")
        comment = cls.prop("comment")
        modifiers = cls.prop("modifiers")
        token = cls.prop("token", 0)

        bg = "bg-class"
        if label == "INTERFACE":
            bg = "bg-interface"
        elif label == "ENUM":
            bg = "bg-enum"

        # 배지: 기본 CLASS/INTERFACE/ENUM 대신 스테레오타입 중심으로 표시
        badges = ""
        if stereotype:
            badges += f'<span class="badge badge-stereotype">«{html_escape(stereotype)}»</span>'
        if scope:
            badges += f' <span class="badge badge-{scope.lower()}">{scope}</span>'

        h = [f'<div class="card" id="cls-{index}">',
             f'<div class="card-header {bg}">',
             f'<h3>{index}. {html_escape(class_name)}</h3>',
             f'<div class="meta">{badges}</div>',
             f'<div class="meta">📁 {html_escape(file_name)} | 📂 {html_escape(directory)} | '
             f'📏 {start_line}-{end_line} | 🔢 {token} tokens</div>',
             '</div>', '<div class="card-body">']

        # 기본 정보
        h.append('<div class="sub-section"><h4>📋 기본 정보</h4>')
        h.append('<table class="tbl"><tbody>')
        if pkg:
            h.append(f'<tr><td style="width:150px"><strong>패키지</strong></td><td>{html_escape(pkg)}</td></tr>')
        if fqn:
            h.append(f'<tr><td><strong>FQN</strong></td><td>{html_escape(fqn)}</td></tr>')
        if modifiers:
            h.append(f'<tr><td><strong>수정자</strong></td><td>{html_escape(modifiers)}</td></tr>')
        if annotations:
            h.append(f'<tr><td><strong>어노테이션</strong></td><td>{html_escape(annotations)}</td></tr>')
        if extends_type:
            h.append(f'<tr><td><strong>상속</strong></td><td><span class="badge badge-extends">EXTENDS</span> {html_escape(extends_type)}</td></tr>')
        if implements_types:
            h.append(f'<tr><td><strong>구현</strong></td><td><span class="badge badge-implements">IMPLEMENTS</span> {html_escape(implements_types)}</td></tr>')
        if comment:
            h.append(f'<tr><td><strong>주석</strong></td><td>{html_escape(comment[:300])}</td></tr>')
        h.append('</tbody></table></div>')

        # 요약
        if summary:
            h.append('<div class="sub-section"><h4>📝 요약</h4>')
            # 요약 텍스트를 문장 단위로 나누어 더 읽기 쉽게 표시
            summary_lines = summary.split('. ')
            formatted_summary = '.<br>'.join([html_escape(line.strip()) for line in summary_lines if line.strip()])
            if not formatted_summary.endswith('.'):
                formatted_summary += '.'
            h.append(f'<div class="summary-text">{formatted_summary}</div></div>')

        # 메서드
        methods = self._get_children(cls.node_id, "HAS_METHOD")
        if methods:
            methods.sort(key=lambda m: m.prop("start_line", 0))
            h.append('<div class="sub-section"><h4>🔧 메서드</h4>')
            h.append('<table class="tbl"><thead><tr><th>이름</th><th>시그니처</th>'
                     '<th>반환 타입</th><th>스테레오타입</th><th>요약</th></tr></thead><tbody>')
            for m in methods:
                m_name = m.prop("name")
                m_sig = m.prop("signature")
                m_ret = m.prop("return_type")
                m_st = m.stereotype
                m_sum = m.prop("summary")
                st_badge = f'<span class="badge badge-stereotype">«{html_escape(m_st)}»</span>' if m_st else '-'
                sum_text = html_escape(m_sum[:120]) + ('...' if len(m_sum) > 120 else '') if m_sum else '-'
                h.append(f'<tr><td><strong>{html_escape(m_name)}</strong></td>'
                         f'<td style="font-size:.82em">{html_escape(m_sig[:100]) if m_sig else "-"}</td>'
                         f'<td>{html_escape(m_ret) if m_ret else "-"}</td>'
                         f'<td>{st_badge}</td>'
                         f'<td style="font-size:.85em">{sum_text}</td></tr>')
            h.append('</tbody></table></div>')

        # 필드
        fields = self._get_children(cls.node_id, "HAS_FIELD")
        if fields:
            fields.sort(key=lambda f: f.prop("start_line", 0))
            h.append('<div class="sub-section"><h4>📌 필드</h4>')
            h.append('<table class="tbl"><thead><tr><th>이름</th><th>타입</th>'
                     '<th>수정자</th><th>어노테이션</th></tr></thead><tbody>')
            for fd in fields:
                h.append(f'<tr><td><strong>{html_escape(fd.prop("name"))}</strong></td>'
                         f'<td>{html_escape(fd.prop("field_type"))}</td>'
                         f'<td>{html_escape(fd.prop("modifiers"))}</td>'
                         f'<td>{html_escape(fd.prop("annotations"))}</td></tr>')
            h.append('</tbody></table></div>')

        # UML 관계
        uml_out = [r for r in self._get_outgoing_rels(cls.node_id, UML_REL_TYPES)]
        uml_in = [r for r in self._get_incoming_rels(cls.node_id, UML_REL_TYPES)]
        if uml_out or uml_in:
            h.append('<div class="sub-section"><h4>🔗 UML 관계</h4>')
            h.append('<table class="tbl"><thead><tr><th>방향</th><th>관계 타입</th>'
                     '<th>대상</th><th>속성</th></tr></thead><tbody>')
            for r in uml_out:
                target = self._get_node(r.end_node_id)
                t_name = self._node_display_name(r.end_node_id)
                t_scope = target.scope_label if target else ""
                scope_b = f' <span class="badge badge-{t_scope.lower()}">{t_scope}</span>' if t_scope else ''
                attrs = self._rel_attrs_str(r)
                badge_cls = f'badge-{r.rel_type.lower()}'
                h.append(f'<tr><td>→ 나가는</td>'
                         f'<td><span class="badge {badge_cls}">{r.rel_type}</span></td>'
                         f'<td>{html_escape(t_name)}{scope_b}</td>'
                         f'<td style="font-size:.85em">{html_escape(attrs)}</td></tr>')
            for r in uml_in:
                source = self._get_node(r.start_node_id)
                s_name = self._node_display_name(r.start_node_id)
                s_scope = source.scope_label if source else ""
                scope_b = f' <span class="badge badge-{s_scope.lower()}">{s_scope}</span>' if s_scope else ''
                attrs = self._rel_attrs_str(r)
                badge_cls = f'badge-{r.rel_type.lower()}'
                h.append(f'<tr><td>← 들어오는</td>'
                         f'<td><span class="badge {badge_cls}">{r.rel_type}</span></td>'
                         f'<td>{html_escape(s_name)}{scope_b}</td>'
                         f'<td style="font-size:.85em">{html_escape(attrs)}</td></tr>')
            h.append('</tbody></table></div>')

        # 의존성 관계 (CALLS, USES)
        dep_out = self._get_outgoing_rels(cls.node_id, DEPENDENCY_REL_TYPES)
        dep_in = self._get_incoming_rels(cls.node_id, DEPENDENCY_REL_TYPES)
        if dep_out or dep_in:
            h.append('<div class="sub-section"><h4>🔀 의존성 관계</h4>')
            h.append('<table class="tbl"><thead><tr><th>방향</th><th>관계</th>'
                     '<th>대상</th></tr></thead><tbody>')
            for r in dep_out:
                t_name = self._node_display_name(r.end_node_id)
                badge_cls = f'badge-{r.rel_type.lower()}'
                h.append(f'<tr><td>→</td>'
                         f'<td><span class="badge {badge_cls}">{r.rel_type}</span></td>'
                         f'<td>{html_escape(t_name)}</td></tr>')
            for r in dep_in:
                s_name = self._node_display_name(r.start_node_id)
                badge_cls = f'badge-{r.rel_type.lower()}'
                h.append(f'<tr><td>←</td>'
                         f'<td><span class="badge {badge_cls}">{r.rel_type}</span></td>'
                         f'<td>{html_escape(s_name)}</td></tr>')
            h.append('</tbody></table></div>')

        # User Story
        us_nodes = self._get_children(cls.node_id, "HAS_USER_STORY")
        if us_nodes:
            h.append(self._html_user_story_inline(us_nodes))

        # 코드 미리보기
        code = cls.prop("summarized_code", cls.prop("node_code"))
        if code:
            preview = code[:1200]
            if len(code) > 1200:
                preview += "\n... (생략)"
            h.append('<div class="sub-section"><h4>💻 코드 미리보기</h4>')
            h.append(f'<div class="code-block">{html_escape(preview)}</div></div>')

        h.append('</div></div>')  # card-body, card
        return "\n".join(h)

    # ------------------------------------------------------------------
    # User Story inline (클래스 내부)
    # ------------------------------------------------------------------

    def _html_user_story_inline(self, us_nodes: List[GraphNode]) -> str:
        h = ['<div class="sub-section"><h4>📖 User Story</h4>']
        for us in us_nodes:
            us_id = us.prop("id")
            role = us.prop("role")
            goal = us.prop("goal")
            benefit = us.prop("benefit")
            h.append(f'<div style="background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:15px;margin-bottom:15px">')
            h.append(f'<strong>{html_escape(us_id)}</strong>: '
                     f'<em>{html_escape(role)}</em>으로서 '
                     f'<strong>{html_escape(goal)}</strong>을(를) 원한다, '
                     f'그래서 <em>{html_escape(benefit)}</em>.')

            # AC
            ac_nodes = self._get_children(us.node_id, "HAS_AC")
            if ac_nodes:
                h.append('<div style="margin-top:12px">')
                for ac in ac_nodes:
                    ac_id = ac.prop("id")
                    title = ac.prop("title")
                    given = ac.prop("given")
                    when_c = ac.prop("when_clause", ac.prop("when"))
                    then_c = ac.prop("then_clause", ac.prop("then"))
                    h.append('<div class="ac-card">')
                    h.append(f'<h5>{html_escape(ac_id)}: {html_escape(title)}</h5>')
                    h.append('<div class="ac-row">')
                    h.append(f'<span class="ac-label">Given</span><span>{html_escape(given)}</span>')
                    h.append(f'<span class="ac-label">When</span><span>{html_escape(when_c)}</span>')
                    h.append(f'<span class="ac-label">Then</span><span>{html_escape(then_c)}</span>')
                    h.append('</div></div>')
                h.append('</div>')
            h.append('</div>')
        h.append('</div>')
        return "\n".join(h)

    # ------------------------------------------------------------------
    # Nontarget Files / Blocks
    # ------------------------------------------------------------------

    def _html_nontarget_section(self) -> str:
        if not self.nontarget_files and not self.nontarget_blocks:
            return ""
        h = ['<div class="section-group" id="sec-nontarget">',
             '<h2>📄 3. Query 파일 / 블록</h2>']

        if self.nontarget_files:
            for nf in self.nontarget_files:
                fp = nf.prop("file_path")
                fn = nf.prop("file_name")
                ext = nf.prop("ext")
                directory = nf.prop("directory")
                blocks = self._get_children(nf.node_id, "CONTAINS")
                pkg_nodes = self._get_children(nf.node_id, "BELONGS_TO_PACKAGE")
                pkg_name = pkg_nodes[0].prop("fqn") if pkg_nodes else ""

                h.append('<div class="card">')
                h.append(f'<div class="card-header bg-nontarget">')
                h.append(f'<h3>📄 {html_escape(fn)}</h3>')
                h.append(f'<div class="meta">📂 {html_escape(directory)} | '
                         f'확장자: {html_escape(ext)}'
                         f'{" | 📦 " + html_escape(pkg_name) if pkg_name else ""}'
                         f' | 블록 수: {len(blocks)}</div>')
                h.append('</div><div class="card-body">')

                if blocks:
                    blocks.sort(key=lambda b: b.prop("start_line", 0))
                    h.append('<table class="tbl"><thead><tr>')
                    h.append('<th>블록명</th><th>종류</th><th>라인</th><th>키워드</th><th>요약</th></tr></thead><tbody>')
                    for blk in blocks:
                        kind = blk.prop("kind")
                        keywords = blk.prop("keywords")
                        kw_str = ", ".join(keywords) if isinstance(keywords, list) else str(keywords)
                        bsum = blk.prop("summary")
                        bsum_text = html_escape(bsum[:150]) + '...' if bsum and len(bsum) > 150 else html_escape(bsum)
                        h.append(f'<tr><td><strong>{html_escape(blk.prop("name"))}</strong></td>'
                                 f'<td><span class="badge badge-nontarget">{html_escape(kind)}</span></td>'
                                 f'<td>{blk.prop("start_line")}-{blk.prop("end_line")}</td>'
                                 f'<td style="font-size:.82em">{html_escape(kw_str)}</td>'
                                 f'<td style="font-size:.85em">{bsum_text}</td></tr>')
                    h.append('</tbody></table>')

                    # 블록별 REFERS_TO 관계
                    block_refs = []
                    for blk in blocks:
                        for rel in self._get_outgoing_rels(blk.node_id, {"REFERS_TO"}):
                            target = self._get_node(rel.end_node_id)
                            if target:
                                block_refs.append((blk, rel, target))
                    if block_refs:
                        h.append('<div class="sub-section" style="margin-top:15px"><h4>🔗 테이블 참조 (REFERS_TO)</h4>')
                        h.append('<table class="tbl"><thead><tr><th>블록</th><th>테이블</th>'
                                 '<th>역할</th><th>증거</th><th>신뢰도</th></tr></thead><tbody>')
                        for blk, rel, tgt in block_refs:
                            role = rel.prop("role")
                            evidence = rel.prop("evidence")
                            conf = rel.prop("confidence")
                            h.append(f'<tr><td>{html_escape(blk.prop("name"))}</td>'
                                     f'<td>{html_escape(tgt.prop("name"))}</td>'
                                     f'<td>{html_escape(role)}</td>'
                                     f'<td style="font-size:.82em">{html_escape(str(evidence)[:100])}</td>'
                                     f'<td>{conf}</td></tr>')
                        h.append('</tbody></table></div>')

                h.append('</div></div>')  # card-body, card

        # 파일에 속하지 않는 블록
        orphan_blocks = [b for b in self.nontarget_blocks
                         if not self._get_parents(b.node_id, "CONTAINS")]
        if orphan_blocks:
            h.append('<div class="sub-section"><h4>기타 블록</h4>')
            h.append('<table class="tbl"><thead><tr><th>블록명</th><th>파일</th>'
                     '<th>종류</th><th>요약</th></tr></thead><tbody>')
            for blk in orphan_blocks:
                h.append(f'<tr><td>{html_escape(blk.prop("name"))}</td>'
                         f'<td>{html_escape(blk.prop("file_path"))}</td>'
                         f'<td>{html_escape(blk.prop("kind"))}</td>'
                         f'<td style="font-size:.85em">{html_escape(blk.prop("summary")[:100])}</td></tr>')
            h.append('</tbody></table></div>')

        h.append('</div>')
        return "\n".join(h)

    # ------------------------------------------------------------------
    # Tables / Columns
    # ------------------------------------------------------------------

    def _html_tables_section(self) -> str:
        if not self.tables:
            return ""
        h = ['<div class="section-group" id="sec-tables">',
             '<h2>🗄️ 4. 테이블 / 컬럼</h2>']

        for tbl in self.tables:
            t_name = tbl.prop("name")
            t_schema = tbl.prop("schema")
            t_desc = tbl.prop("description")
            t_entity = tbl.prop("entity_name")
            t_mapping = tbl.prop("mapping_type")
            t_db = tbl.prop("db")
            t_type = tbl.prop("table_type")
            full_name = f"{t_schema}.{t_name}" if t_schema else t_name

            cols = self._get_children(tbl.node_id, "HAS_COLUMN")
            cols.sort(key=lambda c: c.prop("name"))

            # FK 관계
            fk_out = self._get_outgoing_rels(tbl.node_id, {"FK_TO_TABLE"})
            fk_in = self._get_incoming_rels(tbl.node_id, {"FK_TO_TABLE"})
            # FROM/WRITES
            from_rels = self._get_incoming_rels(tbl.node_id, {"FROM"})
            writes_rels = self._get_incoming_rels(tbl.node_id, {"WRITES"})

            h.append('<div class="card">')
            h.append(f'<div class="card-header bg-table">')
            h.append(f'<h3>🗄️ {html_escape(full_name)}</h3>')
            meta_parts = []
            if t_entity:
                meta_parts.append(f'엔티티: {html_escape(t_entity)}')
            if t_mapping:
                meta_parts.append(f'매핑: {html_escape(t_mapping)}')
            if t_db:
                meta_parts.append(f'DB: {html_escape(t_db)}')
            if t_type:
                meta_parts.append(f'타입: {html_escape(t_type)}')
            meta_parts.append(f'컬럼: {len(cols)}개')
            h.append(f'<div class="meta">{" | ".join(meta_parts)}</div>')
            h.append('</div><div class="card-body">')

            # 설명
            if t_desc:
                h.append(f'<div class="sub-section"><h4>📝 설명</h4>'
                         f'<div class="summary-text">{html_escape(t_desc)}</div></div>')

            # 컬럼
            if cols:
                h.append('<div class="sub-section"><h4>📊 컬럼</h4>')
                h.append('<table class="tbl"><thead><tr><th>이름</th><th>데이터 타입</th>'
                         '<th>Nullable</th><th>PK</th><th>설명</th></tr></thead><tbody>')
                for col in cols:
                    nullable = col.prop("nullable")
                    nullable_str = "✓" if nullable else ("✗" if nullable is not None and not nullable else "-")
                    pk = col.prop("pk_constraint")
                    pk_str = "🔑" if pk else "-"
                    c_desc = col.prop("description")
                    h.append(f'<tr><td><strong>{html_escape(col.prop("name"))}</strong></td>'
                             f'<td>{html_escape(col.prop("dtype"))}</td>'
                             f'<td style="text-align:center">{nullable_str}</td>'
                             f'<td style="text-align:center">{pk_str}</td>'
                             f'<td style="font-size:.85em">{html_escape(c_desc[:100]) if c_desc else "-"}</td></tr>')
                h.append('</tbody></table></div>')

            # FK 관계
            if fk_out or fk_in:
                h.append('<div class="sub-section"><h4>🔗 FK 관계</h4>')
                h.append('<table class="tbl"><thead><tr><th>방향</th><th>관계</th>'
                         '<th>대상 테이블</th><th>FK 타입</th><th>신뢰도</th><th>증거</th>'
                         '</tr></thead><tbody>')
                for r in fk_out:
                    tgt_name = self._node_display_name(r.end_node_id)
                    h.append(f'<tr><td>→</td>'
                             f'<td><span class="badge badge-fk_to_table">FK_TO_TABLE</span></td>'
                             f'<td>{html_escape(tgt_name)}</td>'
                             f'<td>{html_escape(r.prop("fk_type"))}</td>'
                             f'<td>{r.prop("confidence")}</td>'
                             f'<td style="font-size:.82em">{html_escape(str(r.prop("evidence"))[:80])}</td></tr>')
                for r in fk_in:
                    src_name = self._node_display_name(r.start_node_id)
                    h.append(f'<tr><td>←</td>'
                             f'<td><span class="badge badge-fk_to_table">FK_TO_TABLE</span></td>'
                             f'<td>{html_escape(src_name)}</td>'
                             f'<td>{html_escape(r.prop("fk_type"))}</td>'
                             f'<td>{r.prop("confidence")}</td>'
                             f'<td style="font-size:.82em">{html_escape(str(r.prop("evidence"))[:80])}</td></tr>')
                h.append('</tbody></table></div>')

            # FROM / WRITES 관계
            if from_rels or writes_rels:
                h.append('<div class="sub-section"><h4>📥📤 FROM / WRITES 관계</h4>')
                h.append('<table class="tbl"><thead><tr><th>관계</th><th>소스</th>'
                         '<th>소스 블록</th><th>증거</th></tr></thead><tbody>')
                for r in from_rels:
                    src = self._node_display_name(r.start_node_id)
                    h.append(f'<tr><td><span class="badge badge-from">FROM</span></td>'
                             f'<td>{html_escape(src)}</td>'
                             f'<td>{html_escape(r.prop("source_block"))}</td>'
                             f'<td style="font-size:.82em">{html_escape(str(r.prop("evidence"))[:80])}</td></tr>')
                for r in writes_rels:
                    src = self._node_display_name(r.start_node_id)
                    h.append(f'<tr><td><span class="badge badge-writes">WRITES</span></td>'
                             f'<td>{html_escape(src)}</td>'
                             f'<td>{html_escape(r.prop("source_block"))}</td>'
                             f'<td style="font-size:.82em">{html_escape(str(r.prop("evidence"))[:80])}</td></tr>')
                h.append('</tbody></table></div>')

            h.append('</div></div>')  # card-body, card

        # Column-level FK
        col_fk_rels = [r for r in self.db_rels if r.rel_type == "FK_TO"]
        if col_fk_rels:
            h.append('<div class="sub-section"><h4>🔗 컬럼 레벨 FK (FK_TO)</h4>')
            h.append('<table class="tbl"><thead><tr><th>소스 컬럼</th><th>→</th>'
                     '<th>대상 컬럼</th><th>FK 타입</th><th>신뢰도</th></tr></thead><tbody>')
            for r in col_fk_rels:
                src = self._node_display_name(r.start_node_id)
                tgt = self._node_display_name(r.end_node_id)
                h.append(f'<tr><td>{html_escape(src)}</td>'
                         f'<td class="rel-arrow">→</td>'
                         f'<td>{html_escape(tgt)}</td>'
                         f'<td>{html_escape(r.prop("fk_type"))}</td>'
                         f'<td>{r.prop("confidence")}</td></tr>')
            h.append('</tbody></table></div>')

        h.append('</div>')
        return "\n".join(h)

    # ------------------------------------------------------------------
    # Procedures / Functions
    # ------------------------------------------------------------------

    def _html_procedures_section(self) -> str:
        if not self.procedures:
            return ""
        h = ['<div class="section-group" id="sec-procedures">',
             '<h2>⚙️ 5. 프로시저 / 함수</h2>']

        for proc in self.procedures:
            p_name = proc.prop("procedure_name", proc.prop("name"))
            p_label = proc.primary_label
            p_summary = proc.prop("summary")
            p_file = proc.prop("file_name")
            p_dir = proc.prop("directory")
            p_start = proc.prop("start_line", proc.prop("startLine", 0))
            p_end = proc.prop("end_line", proc.prop("endLine", 0))
            p_token = proc.prop("token", 0)

            h.append('<div class="card">')
            h.append(f'<div class="card-header bg-procedure">')
            h.append(f'<h3>{html_escape(p_name)}</h3>')
            h.append(f'<div class="meta"><span class="badge badge-procedure">{p_label}</span> | '
                     f'📁 {html_escape(p_file)} | 📏 {p_start}-{p_end} | 🔢 {p_token} tokens</div>')
            h.append('</div><div class="card-body">')

            if p_summary:
                h.append(f'<div class="sub-section"><h4>📝 요약</h4>'
                         f'<div class="summary-text">{html_escape(p_summary)}</div></div>')

            # DML 자식 노드
            dml_children = []
            for rel in self._outgoing.get(proc.node_id, []):
                child = self._get_node(rel.end_node_id)
                if child and child.primary_label in DML_LABELS:
                    dml_children.append(child)

            if dml_children:
                dml_children.sort(key=lambda d: d.prop("start_line", d.prop("startLine", 0)))
                h.append('<div class="sub-section"><h4>🔍 SQL 문장</h4>')
                h.append('<table class="tbl"><thead><tr><th>라인</th><th>타입</th>'
                         '<th>내용</th></tr></thead><tbody>')
                for dml in dml_children[:30]:
                    dml_type = dml.primary_label
                    dml_line = dml.prop("start_line", dml.prop("startLine", 0))
                    dml_code = dml.prop("node_code", "")
                    code_preview = dml_code[:120].replace('\n', ' ').strip()
                    if len(dml_code) > 120:
                        code_preview += '...'
                    badge_cls = f'badge-{dml_type.lower()}'
                    h.append(f'<tr><td>{dml_line}</td>'
                             f'<td><span class="badge {badge_cls}">{dml_type}</span></td>'
                             f'<td style="font-size:.85em">{html_escape(code_preview)}</td></tr>')
                if len(dml_children) > 30:
                    h.append(f'<tr><td colspan="3" style="text-align:center;color:#666">'
                             f'... 외 {len(dml_children) - 30}개</td></tr>')
                h.append('</tbody></table></div>')

            # CALL 관계
            call_rels = self._get_outgoing_rels(proc.node_id, {"CALL"})
            if call_rels:
                h.append('<div class="sub-section"><h4>📞 호출된 프로시저/함수</h4><ul class="list">')
                for r in call_rels:
                    called = self._node_display_name(r.end_node_id)
                    h.append(f'<li>{html_escape(called)}</li>')
                h.append('</ul></div>')

            # 테이블 관계
            table_rels_out = self._get_outgoing_rels(proc.node_id, {"FROM", "WRITES"})
            if table_rels_out:
                h.append('<div class="sub-section"><h4>🗄️ 사용된 테이블</h4>')
                h.append('<table class="tbl"><thead><tr><th>관계</th><th>테이블</th>'
                         '<th>증거</th></tr></thead><tbody>')
                for r in table_rels_out:
                    tgt = self._node_display_name(r.end_node_id)
                    badge_cls = f'badge-{r.rel_type.lower()}'
                    h.append(f'<tr><td><span class="badge {badge_cls}">{r.rel_type}</span></td>'
                             f'<td>{html_escape(tgt)}</td>'
                             f'<td style="font-size:.82em">{html_escape(str(r.prop("evidence"))[:80])}</td></tr>')
                h.append('</tbody></table></div>')

            # 코드 미리보기
            code = proc.prop("summarized_code", proc.prop("node_code"))
            if code:
                preview = code[:1000]
                if len(code) > 1000:
                    preview += "\n... (생략)"
                h.append('<div class="sub-section"><h4>💻 코드 미리보기</h4>')
                h.append(f'<div class="code-block">{html_escape(preview)}</div></div>')

            h.append('</div></div>')

        h.append('</div>')
        return "\n".join(h)

    # ------------------------------------------------------------------
    # User Stories 전체 섹션
    # ------------------------------------------------------------------

    def _html_user_stories_section(self) -> str:
        if not self.user_stories:
            return ""
        h = ['<div class="section-group" id="sec-userstories">',
             '<h2>📖 6. User Story / Acceptance Criteria</h2>']

        for us in self.user_stories:
            us_id = us.prop("id")
            role = us.prop("role")
            goal = us.prop("goal")
            benefit = us.prop("benefit")
            cls_name = us.prop("class_name")

            # 부모 클래스
            parent_classes = self._get_parents(us.node_id, "HAS_USER_STORY")
            parent_name = parent_classes[0].prop("class_name") if parent_classes else cls_name

            h.append('<div class="card">')
            h.append(f'<div class="card-header bg-userstory">')
            h.append(f'<h3>📖 {html_escape(us_id)}</h3>')
            h.append(f'<div class="meta">클래스: {html_escape(parent_name)}</div>')
            h.append('</div><div class="card-body">')

            h.append(f'<div class="summary-text">'
                     f'<strong>{html_escape(role)}</strong>로서 '
                     f'<strong>{html_escape(goal)}</strong>을(를) 원한다, '
                     f'그래서 <strong>{html_escape(benefit)}</strong>.</div>')

            # AC
            ac_nodes = self._get_children(us.node_id, "HAS_AC")
            if ac_nodes:
                h.append('<div class="sub-section" style="margin-top:15px"><h4>✅ Acceptance Criteria</h4>')
                for ac in ac_nodes:
                    ac_id = ac.prop("id")
                    title = ac.prop("title")
                    given = ac.prop("given")
                    when_c = ac.prop("when_clause", ac.prop("when"))
                    then_c = ac.prop("then_clause", ac.prop("then"))
                    h.append('<div class="ac-card">')
                    h.append(f'<h5>{html_escape(ac_id)}: {html_escape(title)}</h5>')
                    h.append('<div class="ac-row">')
                    h.append(f'<span class="ac-label">Given</span><span>{html_escape(given)}</span>')
                    h.append(f'<span class="ac-label">When</span><span>{html_escape(when_c)}</span>')
                    h.append(f'<span class="ac-label">Then</span><span>{html_escape(then_c)}</span>')
                    h.append('</div></div>')
                h.append('</div>')

            h.append('</div></div>')

        h.append('</div>')
        return "\n".join(h)

    # ------------------------------------------------------------------
    # UML Relationships 전체 섹션
    # ------------------------------------------------------------------

    def _html_uml_relationships_section(self) -> str:
        if not self.uml_rels:
            return ""
        h = ['<div class="section-group" id="sec-uml">',
             '<h2>🔗 7. UML 관계</h2>']

        # 관계 타입별 그룹핑
        groups: Dict[str, List[GraphRelationship]] = defaultdict(list)
        for r in self.uml_rels:
            groups[r.rel_type].append(r)

        for rel_type in ["EXTENDS", "IMPLEMENTS", "ASSOCIATION", "COMPOSITION", "AGGREGATION", "DEPENDENCY"]:
            rels = groups.get(rel_type, [])
            if not rels:
                continue

            badge_cls = f'badge-{rel_type.lower()}'
            h.append(f'<div class="sub-section"><h4><span class="badge {badge_cls}">{rel_type}</span> ({len(rels)}개)</h4>')
            h.append('<table class="tbl"><thead><tr><th>소스</th><th>소스 레이블</th>'
                     '<th>→</th><th>대상</th><th>대상 레이블</th><th>속성</th></tr></thead><tbody>')

            for r in rels:
                src_node = self._get_node(r.start_node_id)
                tgt_node = self._get_node(r.end_node_id)
                src_name = self._node_display_name(r.start_node_id)
                tgt_name = self._node_display_name(r.end_node_id)

                src_labels = ""
                if src_node:
                    scope = src_node.scope_label
                    if scope:
                        src_labels = f'<span class="badge badge-{scope.lower()}">{scope}</span>'

                tgt_labels = ""
                if tgt_node:
                    scope = tgt_node.scope_label
                    if scope:
                        tgt_labels = f'<span class="badge badge-{scope.lower()}">{scope}</span>'

                attrs = self._rel_attrs_str(r)
                h.append(f'<tr><td><strong>{html_escape(src_name)}</strong></td>'
                         f'<td>{src_labels}</td>'
                         f'<td class="rel-arrow">→</td>'
                         f'<td><strong>{html_escape(tgt_name)}</strong></td>'
                         f'<td>{tgt_labels}</td>'
                         f'<td style="font-size:.82em">{html_escape(attrs)}</td></tr>')

            h.append('</tbody></table></div>')

        h.append('</div>')
        return "\n".join(h)

    # ------------------------------------------------------------------
    # 전체 관계 요약
    # ------------------------------------------------------------------

    def _html_all_relationships_section(self) -> str:
        h = ['<div class="section-group" id="sec-allrels">',
             '<h2>📊 8. 전체 관계 요약</h2>']

        # 관계 타입별 카운트
        type_counts: Dict[str, int] = defaultdict(int)
        for r in self.relationships:
            type_counts[r.rel_type] += 1

        if type_counts:
            h.append('<div class="sub-section"><h4>관계 타입별 통계</h4>')
            h.append('<table class="tbl"><thead><tr><th>관계 타입</th><th>카테고리</th>'
                     '<th>수량</th></tr></thead><tbody>')
            for rt, cnt in sorted(type_counts.items(), key=lambda x: -x[1]):
                cat = self._rel_category(rt)
                badge_cls = f'badge-{rt.lower()}'
                h.append(f'<tr><td><span class="badge {badge_cls}">{rt}</span></td>'
                         f'<td>{html_escape(cat)}</td>'
                         f'<td><strong>{cnt}</strong></td></tr>')
            h.append('</tbody></table></div>')

        # 전체 관계 목록 (최대 200개)
        all_rels = self.relationships[:200]
        if all_rels:
            h.append(f'<div class="sub-section"><h4>관계 상세 (총 {len(self.relationships)}개'
                     f'{", 처음 200개 표시" if len(self.relationships) > 200 else ""})</h4>')
            h.append('<table class="tbl"><thead><tr><th>#</th><th>관계 타입</th>'
                     '<th>소스</th><th>→</th><th>대상</th><th>속성</th></tr></thead><tbody>')
            for idx, r in enumerate(all_rels, 1):
                src = self._node_display_name(r.start_node_id)
                tgt = self._node_display_name(r.end_node_id)
                attrs = self._rel_attrs_str(r)
                badge_cls = f'badge-{r.rel_type.lower()}'
                h.append(f'<tr><td>{idx}</td>'
                         f'<td><span class="badge {badge_cls}">{r.rel_type}</span></td>'
                         f'<td>{html_escape(src)}</td>'
                         f'<td class="rel-arrow">→</td>'
                         f'<td>{html_escape(tgt)}</td>'
                         f'<td style="font-size:.82em">{html_escape(attrs[:100])}</td></tr>')
            h.append('</tbody></table></div>')

        # 기타 노드 (분류되지 않은)
        if self.other_nodes:
            h.append(f'<div class="sub-section"><h4>기타 노드 ({len(self.other_nodes)}개)</h4>')
            h.append('<table class="tbl"><thead><tr><th>레이블</th><th>속성</th></tr></thead><tbody>')
            for node in self.other_nodes[:50]:
                labels_str = ", ".join(node.labels)
                props_str = ", ".join(f"{k}={v}" for k, v in list(node.properties.items())[:5])
                h.append(f'<tr><td>{html_escape(labels_str)}</td>'
                         f'<td style="font-size:.82em">{html_escape(props_str[:150])}</td></tr>')
            h.append('</tbody></table></div>')

        h.append('</div>')
        return "\n".join(h)

    # ------------------------------------------------------------------
    # Footer
    # ------------------------------------------------------------------

    def _html_footer(self) -> str:
        return """
<div class="footer">
    <p>Generated by Graph Analyzer | Neo4j Graph Data Analysis Tool</p>
                </div>
            </div>
</body>
</html>"""

    # ------------------------------------------------------------------
    # 관계 속성 문자열
    # ------------------------------------------------------------------

    def _rel_attrs_str(self, rel: GraphRelationship) -> str:
        """관계의 주요 속성을 읽기 쉬운 문자열로 변환"""
        skip_keys = {"updated_at", "created_at"}
        parts = []
        for k, v in rel.properties.items():
            if k in skip_keys or v is None or v == "":
                continue
            parts.append(f"{k}: {v}")
        return ", ".join(parts)

    def _rel_category(self, rel_type: str) -> str:
        if rel_type in UML_REL_TYPES:
            return "UML"
        if rel_type in STRUCTURAL_REL_TYPES:
            return "구조적"
        if rel_type in DEPENDENCY_REL_TYPES:
            return "의존성"
        if rel_type in USER_STORY_REL_TYPES:
            return "User Story"
        if rel_type in DB_REL_TYPES:
            return "데이터베이스"
        return "기타"


# ============================================================================
# 메인 + 테스트
# ============================================================================

def main():
    """메인 함수"""
    print("=" * 80)
    print("Neo4j 그래프 분석 프로그램")
    print("=" * 80)
    print()
    
    analyzer = GraphAnalyzer('test/data/neo4j_exports/records.json')
    
    print("[LOAD] 데이터 로딩 중...")
    analyzer.load_data()
    analyzer.analyze()
    print(f"[OK] 노드 {len(analyzer.nodes)}개, 관계 {len(analyzer.relationships)}개 로드 완료")
    print()
    
    # 통계
    print("[STAT] 노드 분류:")
    print(f"  - CLASS/INTERFACE/ENUM: {len(analyzer.classes)}개")
    print(f"  - METHOD: {len(analyzer.methods)}개")
    print(f"  - FIELD: {len(analyzer.fields)}개")
    print(f"  - PACKAGE: {len(analyzer.packages)}개")
    print(f"  - UserStory: {len(analyzer.user_stories)}개")
    print(f"  - AcceptanceCriteria: {len(analyzer.acceptance_criteria)}개")
    print(f"  - NONTARGET_FILE: {len(analyzer.nontarget_files)}개")
    print(f"  - NONTARGET_BLOCK: {len(analyzer.nontarget_blocks)}개")
    print(f"  - Table: {len(analyzer.tables)}개")
    print(f"  - Column: {len(analyzer.columns)}개")
    print(f"  - PROCEDURE/FUNCTION: {len(analyzer.procedures)}개")
    print(f"  - DML: {len(analyzer.dml_nodes)}개")
    print(f"  - Variable: {len(analyzer.variables)}개")
    print(f"  - 기타: {len(analyzer.other_nodes)}개")
    print()
    print("[STAT] 관계 분류:")
    print(f"  - 구조적: {len(analyzer.structural_rels)}개")
    print(f"  - UML: {len(analyzer.uml_rels)}개")
    print(f"  - 의존성: {len(analyzer.dependency_rels)}개")
    print(f"  - User Story: {len(analyzer.user_story_rels)}개")
    print(f"  - 데이터베이스: {len(analyzer.db_rels)}개")
    print(f"  - 기타: {len(analyzer.other_rels)}개")
    print()
    
    # 레포트 생성
    print("[GEN] HTML 레포트 생성 중...")
    output_path = 'test/data/graph_analysis_report.html'
    analyzer.generate_report(output_path)
    print()
    
    # 클래스별 상세 출력
    if analyzer.classes:
        print("=" * 80)
        print("클래스별 상세 분석 결과")
        print("=" * 80)
        print()
        for i, cls in enumerate(analyzer.classes, 1):
            name = cls.prop("class_name", cls.prop("name"))
            label = cls.primary_label
            scope = cls.scope_label
            st = cls.stereotype
            pkg = cls.prop("package_name")
            methods = analyzer._get_children(cls.node_id, "HAS_METHOD")
            fields = analyzer._get_children(cls.node_id, "HAS_FIELD")
            us_nodes = analyzer._get_children(cls.node_id, "HAS_USER_STORY")
            uml_count = len(analyzer._get_outgoing_rels(cls.node_id, UML_REL_TYPES))

            print(f"{i}. [{label}] {name}")
            if scope:
                print(f"   범위: {scope}")
            if st:
                print(f"   스테레오타입: <<{st}>>")
            if pkg:
                print(f"   패키지: {pkg}")
            print(f"   메서드: {len(methods)}개, 필드: {len(fields)}개")
            if uml_count:
                print(f"   UML 관계: {uml_count}개")
            if us_nodes:
                print(f"   User Story: {len(us_nodes)}개")

            summary = cls.prop("summary")
            if summary:
                print(f"   요약: {summary[:150]}{'...' if len(summary) > 150 else ''}")
        print()


def test_procedure_analyzer():
    """pytest용 테스트 함수"""
    analyzer = GraphAnalyzer('test/data/neo4j_exports/records.json')
    
    analyzer.load_data()
    analyzer.analyze()

    # 기본 검증: 노드가 있는지
    assert len(analyzer.nodes) > 0, "노드가 로드되지 않았습니다"
    
    # 레포트 생성 테스트
    import tempfile
    import os
    with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False) as f:
        temp_path = f.name
    
    try:
        analyzer.generate_report(temp_path)
        assert os.path.exists(temp_path), "HTML 레포트가 생성되지 않았습니다"
        assert os.path.getsize(temp_path) > 0, "HTML 레포트가 비어있습니다"
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
    
    print(f"[OK] 테스트 통과: 노드 {len(analyzer.nodes)}개, 관계 {len(analyzer.relationships)}개 분석 완료")


if __name__ == '__main__':
    main()
