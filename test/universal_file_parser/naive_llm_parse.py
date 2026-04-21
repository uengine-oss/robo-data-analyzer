"""❌ 나쁜 방법 — LLM 에게 파일 통째로 던지기 (비교용)

비교 메트릭 수집 목적. 실제로는 쓰면 안 되는 패턴.
"""

import json
import re
import time

from client.llm_client import get_llm
from config.settings import settings


MAX_INPUT_CHARS = 8000  # 큰 파일은 앞부분만 (실제 운영도 비슷한 한계)


# 범용 shallow-anchor 추출 규칙 (stage2 universal_parse.yaml 과 동일한 규칙 — 문구만 줄임)
_NAIVE_SYSTEM_PROMPT = """당신은 구조화된 파일(XML/JSON/YAML/TOML/INI/미지 포맷)에서
**코드에서 참조되는 식별자 anchor 만** 추출하는 전문가입니다.

## 핵심 규칙 (범용)

파일을 위에서 아래로 순회하며 각 노드(태그/키)에 대해:

> "이 노드의 식별값이 **다른 코드/설정에서 참조되는 이름**인가?"

**YES → anchor 로 채택. 그 자식으로는 파고들지 않음.** 해당 노드의 식별값 + 같은 레벨의 다른 속성만 기록.
**NO → 단순 컨테이너. 건너뛰고 자식으로 내려가 계속 탐색.**

## 판단 예시 (참조 가능성 기준)
| 노드 | anchor? | 이유 |
|---|---|---|
| `<insert id="createOrder">` | ✅ id | `sqlMap.insert("createOrder", ...)` 호출 키 |
| `<select id="getUserById">` | ✅ id | 코드에서 이 id 로 호출 |
| `<entity entity-name="Customer">` | ✅ entity-name | `delegator.findByPrimaryKey("Customer", ...)` |
| `<service name="X" location="Y" invoke="Z"/>` | ✅ name+location+invoke 모두 | `dispatcher.runSync("X")` → Y.Z() |
| `<bean id="foo" class="Bar">` | ✅ id+class | `getBean("foo")` → Bar |
| `<entity><ejb-name>LoanEntity</ejb-name>...` | ✅ child ejb-name | J2EE EJB 패턴 |
| `<sqlMap namespace="X">` | ✅ 루트 namespace | 외부 참조 키 |
| `<services>`, `<beans>`, `<entitymodel>` | ❌ 컨테이너 | wrapper 일 뿐 |
| `<insert>` 내부의 SQL 텍스트 | ❌ | 구현 디테일, 참조 키 아님 |
| `<entity>` 내부의 `<field>` | ❌ | anchor 에서 멈춤, 자식 드롭 |
| YAML `database.url = jdbc:...` | ✅ leaf value | `@Value("${database.url}")` 참조 |
| YAML `services:` (컨테이너) | ❌ | 하위 항목이 anchor |

## 공통 원칙
1. **anchor 를 찾으면 자식으로 들어가지 않는다.** 그 레벨 속성만 수집.
2. 식별자는 attribute (id/name/alias) 일 수도, child element (ejb-name/servlet-name) 일 수도 — **의미 판단**.
3. 실제 코드가 이 이름을 API 호출 / 설정 참조 / 스키마 조회로 쓸 만한가? 가 기준.
4. anchor 의 "긴 설명 텍스트" (SQL 본문, 주석, body) 는 drop. 짧은 속성값만 유지.

## 출력 포맷
```json
{
  "root": "sqlMap",
  "namespace": "ReferenceInformationDC_IDCReferenceInformation",
  "anchors": [
    {"kind": "insert", "id": "createCmbsDesk", "parameterClass": "cmbsDeskDTO"},
    {"kind": "select", "id": "getNextCmbsDeskNo", "resultClass": "String"},
    ...
  ]
}
```

YAML/JSON 설정 파일처럼 anchor 가 leaf key-value 인 경우엔 단순 flat dict 가능:
```json
{"database.url": "jdbc:...", "database.user": "app_user", ...}
```

반드시 유효한 JSON 만 출력. 설명이나 마크다운 코드블록 금지.
"""


def naive_llm_parse(path: str) -> dict:
    """파일을 통째로 LLM 에게 던져서 JSON 추출을 요청.

    Returns:
        {
          "result": dict | list | {"_parse_error": "..."},
          "elapsed_sec": float,
          "tokens": int,
          "strategy": "naive_llm",
        }
    """
    start = time.time()

    with open(path, 'rb') as f:
        data = f.read()
    text = data.decode('utf-8', errors='replace')

    if len(text) > MAX_INPUT_CHARS:
        text = text[:MAX_INPUT_CHARS] + "\n...[TRUNCATED]"

    llm = get_llm(api_key=settings.llm.api_key, model=settings.llm.model)

    system_msg = _NAIVE_SYSTEM_PROMPT

    tokens = 0
    try:
        response = llm.invoke([
            {"role": "system", "content": system_msg},
            {"role": "user", "content": text}
        ])

        content = _extract_content(response)
        tokens = _extract_tokens(response)
    except Exception as e:
        return {
            "result": {"_llm_error": f"{type(e).__name__}: {str(e)[:200]}"},
            "elapsed_sec": round(time.time() - start, 3),
            "tokens": 0,
            "strategy": "naive_llm",
        }

    # 코드블록 제거
    m = re.search(r'```(?:json)?\s*(.*?)\s*```', content, re.DOTALL)
    if m:
        content = m.group(1)

    try:
        result = json.loads(content)
    except json.JSONDecodeError:
        result = {"_parse_error": content[:500]}

    return {
        "result": result,
        "elapsed_sec": round(time.time() - start, 3),
        "tokens": tokens,
        "strategy": "naive_llm",
    }


def _extract_content(response) -> str:
    if isinstance(response, str):
        return response
    content = getattr(response, 'content', None)
    if content is None:
        return str(response)
    if isinstance(content, list):
        return "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        )
    return str(content)


def _extract_tokens(response) -> int:
    usage = getattr(response, 'usage_metadata', None)
    if usage:
        return usage.get('total_tokens', 0)
    meta = getattr(response, 'response_metadata', {}) or {}
    token_usage = meta.get('token_usage') or meta.get('usage') or {}
    return token_usage.get('total_tokens', 0)
