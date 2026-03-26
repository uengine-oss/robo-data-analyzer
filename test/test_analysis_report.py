"""분석 보고서 생성 단독 테스트

Neo4j에 이미 적재된 그래프 데이터로 분석 보고서만 생성합니다.
전체 파이프라인을 다시 실행하지 않고 보고서 스텝만 테스트할 때 사용.

실행:
    python -m test.test_analysis_report
    또는
    pytest test/test_analysis_report.py -v -s
"""

from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path

# Windows cp949 인코딩 문제 방지
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# 프로젝트 루트를 sys.path에 추가
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from client.neo4j_client import Neo4jClient
from config.settings import settings
from analyzer.report import generate_analysis_report_from_graph


def _filter_props(props: dict) -> dict:
    return {k: v for k, v in (props or {}).items()
            if not (isinstance(v, list) and len(v) > 64)}


async def run_report_test():
    """Neo4j에서 그래프를 조회하고 분석 보고서를 생성합니다."""
    client = Neo4jClient()
    try:
        count_result = await client.execute_queries([
            "MATCH (__cy_n__) RETURN count(__cy_n__) AS cnt"
        ])
        node_count = count_result[0][0]["cnt"] if count_result and count_result[0] else 0
        print(f"\n{'='*60}")
        print(f"  Neo4j 노드 수: {node_count}")
        print(f"{'='*60}")

        if node_count == 0:
            print("  ❌ Neo4j에 데이터가 없습니다. 파이프라인을 먼저 실행하세요.")
            return

        node_query = """
            MATCH (__cy_n__)
            RETURN elementId(__cy_n__) AS nodeId,
                   labels(__cy_n__) AS labels,
                   properties(__cy_n__) AS props
        """
        rel_query = """
            MATCH (__cy_a__)-[__cy_r__]->(__cy_b__)
            RETURN elementId(__cy_r__) AS relId,
                   elementId(__cy_a__) AS startId,
                   elementId(__cy_b__) AS endId,
                   type(__cy_r__) AS relType,
                   properties(__cy_r__) AS props
        """

        print("  Neo4j 그래프 조회 중...")
        results = await client.execute_queries([node_query, rel_query])
        node_result = results[0] if results else []
        rel_result = results[1] if len(results) > 1 else []

        # 4. dict 변환
        nodes = []
        for rec in node_result:
            nodes.append({
                "Node ID": rec["nodeId"],
                "Labels": rec["labels"],
                "Properties": _filter_props(rec.get("props")),
            })

        relationships = []
        for rec in rel_result:
            relationships.append({
                "Relationship ID": rec["relId"],
                "Start Node ID": rec["startId"],
                "End Node ID": rec["endId"],
                "Type": rec["relType"],
                "Properties": _filter_props(rec.get("props")),
            })

        graph_data = {"Nodes": nodes, "Relationships": relationships}
        print(f"  조회 완료: 노드 {len(nodes)}개, 관계 {len(relationships)}개")

        # 5. 레이블별 통계
        from collections import Counter
        label_counts = Counter()
        for n in nodes:
            for lbl in n["Labels"]:
                label_counts[lbl] += 1
        print(f"\n  레이블별 노드 수:")
        for lbl, cnt in label_counts.most_common():
            print(f"    {lbl}: {cnt}")

        rel_type_counts = Counter(r["Type"] for r in relationships)
        print(f"\n  관계 타입별 수:")
        for rt, cnt in rel_type_counts.most_common():
            print(f"    {rt}: {cnt}")

        # 6. 보고서 생성
        print(f"\n{'='*60}")
        print("  보고서 생성 중...")
        report = generate_analysis_report_from_graph(graph_data)
        print(f"  보고서 생성 완료: {len(report):,} bytes")

        # 7. 저장
        run_dir = Path(settings.path.audit_dir) / "analysis.run" / "latest"
        run_dir.mkdir(parents=True, exist_ok=True)
        report_path = run_dir / "analysis_report.md"
        report_path.write_text(report, encoding="utf-8")
        print(f"  저장 위치: {report_path}")
        print(f"{'='*60}")

        # 8. 보고서 앞부분 미리보기
        lines = report.split("\n")
        print(f"\n  보고서 미리보기 (처음 50줄):")
        print("  " + "-" * 56)
        for line in lines[:50]:
            print(f"  {line}")
        if len(lines) > 50:
            print(f"  ... (총 {len(lines)}줄)")

    finally:
        await client.close()


# pytest 호환
def test_analysis_report():
    """pytest에서 실행 가능한 테스트 함수."""
    asyncio.run(run_report_test())


if __name__ == "__main__":
    asyncio.run(run_report_test())
