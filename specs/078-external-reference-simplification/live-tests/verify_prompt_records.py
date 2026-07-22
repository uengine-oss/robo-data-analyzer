"""078 T704: 실제 LLM prompt record 검증.

- 연결 청크 원문이 해당 routine의 user_content에 100% 포함
- 중간 절단 0 (원문 전문 일치)
- 타 routine evidence leak 0
- system_prompt 병렬 byte-identical
"""
import asyncio
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))
from dotenv import load_dotenv

load_dotenv(REPO_ROOT / ".env")
os.environ["ROBO_NEO4J_DATABASE"] = "skill"

from graph.neo4j_client import Neo4jClient  # noqa: E402
from shared.config.settings import SETTINGS  # noqa: E402


async def main() -> None:
    client = Neo4jClient()
    refers = await client.run_read_query(
        "MATCH (m)-[r:REFER_TO]->(c:EXTERNAL_CHUNK) "
        "RETURN m.id AS method, r.matched_key AS key, c.text AS text ORDER BY method")
    await client.close()
    linked: dict[str, list[dict]] = {}
    for row in refers:
        linked.setdefault(row["method"], []).append(row)
    print(f"linked routines: {len(linked)}")

    prompts_dir = SETTINGS.run_output.dir / "llm-prompts" / "latest"
    rule_file = prompts_dir / "code_summary.json"
    records = json.loads(rule_file.read_text(encoding="utf-8"))["records"]
    print(f"code_summary records: {len(records)}")

    systems = {record["system_prompt"] for record in records}
    print(f"distinct system prompts: {len(systems)}")
    assert len(systems) == 1, "system prompt must be byte-identical across parallel calls"

    by_target = {record.get("target_id"): record for record in records}
    include_fail = leak_fail = 0
    for method, rows in linked.items():
        record = by_target.get(method)
        if record is None:
            print(f"[WARN] no prompt record for linked routine {method}")
            continue
        user = record["user_content"]
        for row in rows:
            encoded = json.dumps(row["text"], ensure_ascii=False)[1:-1]
            if encoded not in user and row["text"] not in user:
                include_fail += 1
                print(f"[FAIL] chunk text missing/truncated in prompt: {method} key={row['key']}")
    owners_by_text: dict[str, set[str]] = {}
    for row in refers:
        owners_by_text.setdefault(row["text"], set()).add(row["method"])
    for target, record in by_target.items():
        user = record.get("user_content", "")
        for text, owners in owners_by_text.items():
            if target not in owners and len(text) > 40:
                encoded = json.dumps(text, ensure_ascii=False)[1:-1]
                if encoded in user or text in user:
                    leak_fail += 1
                    print(f"[FAIL] evidence leak: chunk of {sorted(owners)} in {target}'s prompt")
    print(f"inclusion failures={include_fail}, leaks={leak_fail}")
    assert include_fail == 0 and leak_fail == 0
    print("[PROMPT RECORD VERIFY DONE]")


asyncio.run(main())
