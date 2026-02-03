"""용어집 관리 서비스

비즈니스 용어집 CRUD 기능을 제공합니다.

주요 기능:
- 용어집(Glossary) CRUD
- 용어(Term) CRUD
- 도메인/소유자/태그 관리
"""

import logging
from typing import Optional, List
from datetime import datetime

from analyzer.neo4j_client import Neo4jClient
from util.text_utils import escape_for_cypher


logger = logging.getLogger(__name__)


def get_current_timestamp() -> str:
    """현재 시간을 ISO 형식으로 반환"""
    return datetime.utcnow().isoformat() + "Z"


# =============================================================================
# 용어집(Glossary) CRUD
# =============================================================================

async def fetch_all_glossaries() -> dict:
    """모든 용어집 목록 조회
    
    Returns:
        {"glossaries": [...]}
    """
    query = """
        MATCH (__cy_g__:Glossary)
        OPTIONAL MATCH (__cy_g__)-[:HAS_TERM]->(__cy_t__:Term)
        WITH __cy_g__, count(__cy_t__) as termCount
        RETURN 
            elementId(__cy_g__) as id,
            __cy_g__.name as name,
            __cy_g__.description as description,
            __cy_g__.type as type,
            __cy_g__.created_at as createdAt,
            __cy_g__.updated_at as updatedAt,
            termCount
        ORDER BY __cy_g__.name
    """
    
    client = Neo4jClient()
    try:
        result = await client.execute_queries([query])
        glossaries = []
        for record in result[0] if result else []:
            glossaries.append({
                "id": record["id"],
                "name": record["name"],
                "description": record.get("description", ""),
                "type": record.get("type", "Business"),
                "termCount": record.get("termCount", 0),
                "createdAt": record.get("createdAt"),
                "updatedAt": record.get("updatedAt"),
            })
        return {"glossaries": glossaries}
    finally:
        await client.close()


async def create_new_glossary(name: str, description: str, type_: str) -> dict:
    """용어집 생성
    
    Returns:
        생성된 용어집 정보
    """
    timestamp = get_current_timestamp()
    
    query = f"""
        CREATE (__cy_g__:Glossary {{
            name: '{escape_for_cypher(name)}',
            description: '{escape_for_cypher(description)}',
            type: '{escape_for_cypher(type_)}',
            created_at: '{timestamp}',
            updated_at: '{timestamp}'
        }})
        RETURN elementId(__cy_g__) as id, __cy_g__.name as name
    """
    
    client = Neo4jClient()
    try:
        result = await client.execute_queries([query])
        if result and result[0]:
            record = result[0][0]
            return {
                "id": record["id"],
                "name": record["name"],
                "message": "용어집이 생성되었습니다."
            }
        raise RuntimeError("용어집 생성 실패")
    finally:
        await client.close()


async def fetch_glossary_by_id(glossary_id: str) -> dict:
    """용어집 상세 조회
    
    Returns:
        용어집 정보
    """
    query = f"""
        MATCH (__cy_g__:Glossary)
        WHERE elementId(__cy_g__) = '{escape_for_cypher(glossary_id)}'
        OPTIONAL MATCH (__cy_g__)-[:HAS_TERM]->(__cy_t__:Term)
        WITH __cy_g__, count(__cy_t__) as termCount
        RETURN 
            elementId(__cy_g__) as id,
            __cy_g__.name as name,
            __cy_g__.description as description,
            __cy_g__.type as type,
            __cy_g__.created_at as createdAt,
            __cy_g__.updated_at as updatedAt,
            termCount
    """
    
    client = Neo4jClient()
    try:
        result = await client.execute_queries([query])
        if result and result[0]:
            record = result[0][0]
            return {
                "id": record["id"],
                "name": record["name"],
                "description": record.get("description", ""),
                "type": record.get("type", "Business"),
                "termCount": record.get("termCount", 0),
                "createdAt": record.get("createdAt"),
                "updatedAt": record.get("updatedAt"),
            }
        return None
    finally:
        await client.close()


async def update_glossary_info(
    glossary_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    type_: Optional[str] = None
) -> dict:
    """용어집 수정
    
    Returns:
        수정 결과
    """
    set_clauses = [f"__cy_g__.updated_at = '{get_current_timestamp()}'"]
    
    if name is not None:
        set_clauses.append(f"__cy_g__.name = '{escape_for_cypher(name)}'")
    if description is not None:
        set_clauses.append(f"__cy_g__.description = '{escape_for_cypher(description)}'")
    if type_ is not None:
        set_clauses.append(f"__cy_g__.type = '{escape_for_cypher(type_)}'")
    
    query = f"""
        MATCH (__cy_g__:Glossary)
        WHERE elementId(__cy_g__) = '{escape_for_cypher(glossary_id)}'
        SET {', '.join(set_clauses)}
        RETURN elementId(__cy_g__) as id
    """
    
    client = Neo4jClient()
    try:
        result = await client.execute_queries([query])
        if result and result[0]:
            return {"message": "용어집이 수정되었습니다.", "updated": True}
        return {"message": "용어집을 찾을 수 없습니다.", "updated": False}
    finally:
        await client.close()


async def delete_glossary_by_id(glossary_id: str) -> dict:
    """용어집 삭제
    
    Returns:
        삭제 결과
    """
    query = f"""
        MATCH (__cy_g__:Glossary)
        WHERE elementId(__cy_g__) = '{escape_for_cypher(glossary_id)}'
        OPTIONAL MATCH (__cy_g__)-[:HAS_TERM]->(__cy_t__:Term)
        DETACH DELETE __cy_g__, __cy_t__
        RETURN count(*) as deleted
    """
    
    client = Neo4jClient()
    try:
        result = await client.execute_queries([query])
        return {"message": "용어집이 삭제되었습니다.", "deleted": True}
    finally:
        await client.close()


# =============================================================================
# 용어(Term) CRUD
# =============================================================================

async def fetch_glossary_terms(
    glossary_id: str,
    search: Optional[str] = None,
    limit: int = 100
) -> dict:
    """용어 목록 조회
    
    Returns:
        {"terms": [...]}
    """
    where_clause = f"elementId(__cy_g__) = '{escape_for_cypher(glossary_id)}'"
    
    if search:
        where_clause += f" AND (toLower(__cy_t__.name) CONTAINS toLower('{escape_for_cypher(search)}'))"
    
    query = f"""
        MATCH (__cy_g__:Glossary)-[:HAS_TERM]->(__cy_t__:Term)
        WHERE {where_clause}
        OPTIONAL MATCH (__cy_t__)-[:BELONGS_TO_DOMAIN]->(__cy_d__:Domain)
        OPTIONAL MATCH (__cy_t__)-[:HAS_TAG]->(__cy_tag__:Tag)
        RETURN 
            elementId(__cy_t__) as id,
            __cy_t__.name as name,
            __cy_t__.description as description,
            __cy_t__.status as status,
            collect(DISTINCT __cy_d__.name) as domains,
            collect(DISTINCT __cy_tag__.name) as tags
        ORDER BY __cy_t__.name
        LIMIT {limit}
    """
    
    client = Neo4jClient()
    try:
        result = await client.execute_queries([query])
        terms = []
        for record in result[0] if result else []:
            terms.append({
                "id": record["id"],
                "name": record["name"],
                "description": record.get("description", ""),
                "status": record.get("status", "Draft"),
                "domains": record.get("domains", []),
                "tags": record.get("tags", []),
            })
        return {"terms": terms}
    finally:
        await client.close()


async def create_new_term(glossary_id: str, term_data: dict) -> dict:
    """용어 생성
    
    Returns:
        생성된 용어 정보
    """
    timestamp = get_current_timestamp()
    name = term_data.get("name", "")
    description = term_data.get("description", "")
    status = term_data.get("status", "Draft")
    
    query = f"""
        MATCH (__cy_g__:Glossary)
        WHERE elementId(__cy_g__) = '{escape_for_cypher(glossary_id)}'
        CREATE (__cy_g__)-[:HAS_TERM]->(__cy_t__:Term {{
            name: '{escape_for_cypher(name)}',
            description: '{escape_for_cypher(description)}',
            status: '{escape_for_cypher(status)}',
            created_at: '{timestamp}',
            updated_at: '{timestamp}'
        }})
        RETURN elementId(__cy_t__) as id, __cy_t__.name as name
    """
    
    client = Neo4jClient()
    try:
        result = await client.execute_queries([query])
        if result and result[0]:
            record = result[0][0]
            return {
                "id": record["id"],
                "name": record["name"],
                "message": "용어가 생성되었습니다."
            }
        raise RuntimeError("용어 생성 실패")
    finally:
        await client.close()


async def fetch_term_by_id(glossary_id: str, term_id: str) -> dict:
    """용어 상세 조회
    
    Returns:
        용어 정보
    """
    query = f"""
        MATCH (__cy_g__:Glossary)-[:HAS_TERM]->(__cy_t__:Term)
        WHERE elementId(__cy_g__) = '{escape_for_cypher(glossary_id)}'
          AND elementId(__cy_t__) = '{escape_for_cypher(term_id)}'
        OPTIONAL MATCH (__cy_t__)-[:BELONGS_TO_DOMAIN]->(__cy_d__:Domain)
        OPTIONAL MATCH (__cy_t__)-[:HAS_TAG]->(__cy_tag__:Tag)
        OPTIONAL MATCH (__cy_t__)-[:OWNED_BY]->(__cy_o__:Owner)
        RETURN 
            elementId(__cy_t__) as id,
            __cy_t__.name as name,
            __cy_t__.description as description,
            __cy_t__.status as status,
            __cy_t__.synonyms as synonyms,
            collect(DISTINCT __cy_d__.name) as domains,
            collect(DISTINCT __cy_tag__.name) as tags,
            collect(DISTINCT __cy_o__.name) as owners
    """
    
    client = Neo4jClient()
    try:
        result = await client.execute_queries([query])
        if result and result[0]:
            record = result[0][0]
            return {
                "id": record["id"],
                "name": record["name"],
                "description": record.get("description", ""),
                "status": record.get("status", "Draft"),
                "synonyms": record.get("synonyms", []),
                "domains": record.get("domains", []),
                "tags": record.get("tags", []),
                "owners": record.get("owners", []),
            }
        return None
    finally:
        await client.close()


async def update_term_info(glossary_id: str, term_id: str, term_data: dict) -> dict:
    """용어 수정
    
    Returns:
        수정 결과
    """
    set_clauses = [f"__cy_t__.updated_at = '{get_current_timestamp()}'"]
    
    if term_data.get("name") is not None:
        set_clauses.append(f"__cy_t__.name = '{escape_for_cypher(term_data['name'])}'")
    if term_data.get("description") is not None:
        set_clauses.append(f"__cy_t__.description = '{escape_for_cypher(term_data['description'])}'")
    if term_data.get("status") is not None:
        set_clauses.append(f"__cy_t__.status = '{escape_for_cypher(term_data['status'])}'")
    
    query = f"""
        MATCH (__cy_g__:Glossary)-[:HAS_TERM]->(__cy_t__:Term)
        WHERE elementId(__cy_g__) = '{escape_for_cypher(glossary_id)}'
          AND elementId(__cy_t__) = '{escape_for_cypher(term_id)}'
        SET {', '.join(set_clauses)}
        RETURN elementId(__cy_t__) as id
    """
    
    client = Neo4jClient()
    try:
        result = await client.execute_queries([query])
        if result and result[0]:
            return {"message": "용어가 수정되었습니다.", "updated": True}
        return {"message": "용어를 찾을 수 없습니다.", "updated": False}
    finally:
        await client.close()


async def delete_term_by_id(glossary_id: str, term_id: str) -> dict:
    """용어 삭제
    
    Returns:
        삭제 결과
    """
    query = f"""
        MATCH (__cy_g__:Glossary)-[:HAS_TERM]->(__cy_t__:Term)
        WHERE elementId(__cy_g__) = '{escape_for_cypher(glossary_id)}'
          AND elementId(__cy_t__) = '{escape_for_cypher(term_id)}'
        DETACH DELETE __cy_t__
        RETURN count(*) as deleted
    """
    
    client = Neo4jClient()
    try:
        result = await client.execute_queries([query])
        return {"message": "용어가 삭제되었습니다.", "deleted": True}
    finally:
        await client.close()


# =============================================================================
# 도메인/소유자/태그 관리
# =============================================================================

async def fetch_all_domains() -> dict:
    """도메인 목록 조회"""
    query = """
        MATCH (__cy_d__:Domain)
        RETURN elementId(__cy_d__) as id, __cy_d__.name as name, __cy_d__.description as description
        ORDER BY __cy_d__.name
    """
    
    client = Neo4jClient()
    try:
        result = await client.execute_queries([query])
        domains = [{"id": r["id"], "name": r["name"], "description": r.get("description", "")} 
                   for r in (result[0] if result else [])]
        return {"domains": domains}
    finally:
        await client.close()


async def fetch_all_owners() -> dict:
    """소유자 목록 조회"""
    query = """
        MATCH (__cy_o__:Owner)
        RETURN elementId(__cy_o__) as id, __cy_o__.name as name, __cy_o__.email as email, __cy_o__.role as role
        ORDER BY __cy_o__.name
    """
    
    client = Neo4jClient()
    try:
        result = await client.execute_queries([query])
        owners = [{"id": r["id"], "name": r["name"], "email": r.get("email", ""), "role": r.get("role", "Owner")} 
                  for r in (result[0] if result else [])]
        return {"owners": owners}
    finally:
        await client.close()


async def fetch_all_tags() -> dict:
    """태그 목록 조회"""
    query = """
        MATCH (__cy_tag__:Tag)
        RETURN elementId(__cy_tag__) as id, __cy_tag__.name as name, __cy_tag__.color as color
        ORDER BY __cy_tag__.name
    """
    
    client = Neo4jClient()
    try:
        result = await client.execute_queries([query])
        tags = [{"id": r["id"], "name": r["name"], "color": r.get("color", "#3498db")} 
                for r in (result[0] if result else [])]
        return {"tags": tags}
    finally:
        await client.close()


async def create_new_domain(name: str, description: str = "") -> dict:
    """도메인 생성"""
    query = f"""
        CREATE (__cy_d__:Domain {{name: '{escape_for_cypher(name)}', description: '{escape_for_cypher(description)}'}})
        RETURN elementId(__cy_d__) as id, __cy_d__.name as name
    """
    
    client = Neo4jClient()
    try:
        result = await client.execute_queries([query])
        if result and result[0]:
            return {"id": result[0][0]["id"], "name": result[0][0]["name"], "message": "도메인이 생성되었습니다."}
        raise RuntimeError("도메인 생성 실패")
    finally:
        await client.close()


async def create_new_owner(name: str, email: str, role: str) -> dict:
    """소유자 생성"""
    query = f"""
        CREATE (__cy_o__:Owner {{name: '{escape_for_cypher(name)}', email: '{escape_for_cypher(email)}', role: '{escape_for_cypher(role)}'}})
        RETURN elementId(__cy_o__) as id, __cy_o__.name as name
    """
    
    client = Neo4jClient()
    try:
        result = await client.execute_queries([query])
        if result and result[0]:
            return {"id": result[0][0]["id"], "name": result[0][0]["name"], "message": "소유자가 생성되었습니다."}
        raise RuntimeError("소유자 생성 실패")
    finally:
        await client.close()


async def create_new_tag(name: str, color: str) -> dict:
    """태그 생성"""
    query = f"""
        CREATE (__cy_tag__:Tag {{name: '{escape_for_cypher(name)}', color: '{escape_for_cypher(color)}'}})
        RETURN elementId(__cy_tag__) as id, __cy_tag__.name as name
    """
    
    client = Neo4jClient()
    try:
        result = await client.execute_queries([query])
        if result and result[0]:
            return {"id": result[0][0]["id"], "name": result[0][0]["name"], "message": "태그가 생성되었습니다."}
        raise RuntimeError("태그 생성 실패")
    finally:
        await client.close()

