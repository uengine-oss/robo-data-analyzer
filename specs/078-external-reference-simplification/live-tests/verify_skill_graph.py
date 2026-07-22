"""078 S7: skill DB 실그래프 검증 — source→chunk 전수 대조 + REFER_TO 증거 덤프.

실행 전제: Neo4j skill DB 에 E2E 분석 결과가 있어야 한다 (E2E_PROJECT env 로
프로젝트 지정, 기본 loan-ejb-app). 데이터를 쓰지 않는 읽기 전용 검증이다.
"""
import asyncio
import hashlib
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))
from dotenv import load_dotenv

load_dotenv(REPO_ROOT / ".env")
os.environ["ROBO_NEO4J_DATABASE"] = "skill"

from graph.neo4j_client import Neo4jClient  # noqa: E402

SOURCE_ROOT = REPO_ROOT.parent / "data" / "source"


def decode(path: Path) -> str:
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp949")


async def main() -> None:
    client = Neo4jClient()

    labels = await client.run_read_query(
        "MATCH (n) UNWIND labels(n) AS l RETURN l AS label, count(*) AS cnt ORDER BY cnt DESC")
    print("labels:", [(r["label"], r["cnt"]) for r in labels])
    rels = await client.run_read_query(
        "MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS cnt ORDER BY cnt DESC")
    print("rels:", [(r["t"], r["cnt"]) for r in rels])

    # 폐기 라벨/관계가 하나라도 있으면 실패
    banned_labels = {"SQL_STATEMENT", "CONFIG_SECTION", "ARTIFACT_CHUNK", "CHUNK",
                     "DOCUMENT", "DOC_SECTION", "DOC_CHUNK", "UNRESOLVED_REF", "CANDIDATE_LINK"}
    banned_rels = {"CALLS_SQL", "USES_CONFIG", "MENTIONS", "HAS_PART", "HAS_SECTION"}
    live_labels = {r["label"] for r in labels}
    live_rels = {r["t"] for r in rels}
    assert not (banned_labels & live_labels), f"banned labels present: {banned_labels & live_labels}"
    assert not (banned_rels & live_rels), f"banned rels present: {banned_rels & live_rels}"
    print("[PASS] banned labels/rels: 0")

    # source→chunk 전수 slice/hash 대조
    chunks = await client.run_read_query(
        "MATCH (c:EXTERNAL_CHUNK) RETURN c.id AS id, c.file_path AS fp, c.char_start AS s, "
        "c.char_end AS e, c.text AS text, c.content_hash AS ch, c.provenance AS prov, "
        "c.keys AS keys ORDER BY c.file_path, c.char_start")
    bad = 0
    project = os.environ.get("E2E_PROJECT", "loan-ejb-app")
    for chunk in chunks:
        if chunk["prov"] != "source_span":
            continue
        source = decode(SOURCE_ROOT / project / chunk["fp"])
        piece = source[chunk["s"]:chunk["e"]]
        if piece != chunk["text"]:
            bad += 1
            print(f"[FAIL] slice mismatch: {chunk['id']}")
        if hashlib.sha256(chunk["text"].encode("utf-8")).hexdigest() != chunk["ch"]:
            bad += 1
            print(f"[FAIL] hash mismatch: {chunk['id']}")
    print(f"chunks total={len(chunks)}, source_span slice/hash mismatches={bad}")
    assert bad == 0

    refers = await client.run_read_query(
        "MATCH (m)-[r:REFER_TO]->(c:EXTERNAL_CHUNK) "
        "RETURN m.id AS method, labels(m) AS ml, r.matched_key AS key, r.via_symbol AS via, "
        "c.file_path AS fp, c.name AS chunk ORDER BY method")
    print(f"REFER_TO(method->chunk) = {len(refers)}")
    for r in refers:
        print(f"  {r['method']} --[{r['key']}{' via ' + r['via'] if r['via'] else ''}]--> "
              f"{r['fp']}#{r['chunk']}")

    # 미연결 청크 = 정상 데이터 확인
    unlinked = await client.run_read_query(
        "MATCH (c:EXTERNAL_CHUNK) WHERE NOT ()-[:REFER_TO]->(c) RETURN count(c) AS cnt")
    print(f"unlinked chunks (정상): {unlinked[0]['cnt']}")

    files = await client.run_read_query(
        "MATCH (f:PROJECT_FILE) RETURN f.status AS status, count(*) AS cnt ORDER BY cnt DESC")
    print("project_file status:", [(r["status"], r["cnt"]) for r in files])
    await client.close()
    print("[VERIFY DONE]")


asyncio.run(main())
