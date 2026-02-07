"""그래프 질의 응답 서비스

DeepAgents를 활용한 그래프 기반 질의 응답을 수행합니다.
Linker용 에이전트를 재사용하여 Neo4j 그래프를 탐색합니다.
"""

from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage

from analyzer.strategy.framework.nontarget.utils.nontarget_logger import (
    log_nontarget,
    log_agent_action,
)
from analyzer.strategy.framework.nontarget.agents.deepagents_client import (
    DeepAgentsClient,
    extract_agent_output,
)
from analyzer.strategy.framework.nontarget.workflow import RuntimeCtx


def create_query_agent(ctx: RuntimeCtx) -> Any:
    """질의 응답용 에이전트 생성 (Linker 에이전트 재사용)"""
    log_agent_action("query_agent", "create", {})
    client = DeepAgentsClient()
    return client.create_linker_agent(ctx)


async def query_graph(
    question: str,
    ctx: RuntimeCtx,
) -> Dict[str, Any]:
    """그래프 기반 질의 응답"""
    log_agent_action("query_agent", "query_graph", {"question": question[:100]})

    agent = create_query_agent(ctx)

    try:
        result = await agent.ainvoke({
            "messages": [HumanMessage(content=question)]
        })
        output = extract_agent_output(result)

        log_nontarget("QUERY", f"질의 응답 완료: {len(output)} chars")
        return {"success": True, "answer": output, "question": question}

    except Exception as e:
        log_nontarget("QUERY", f"질의 응답 실패: {e}")
        return {"success": False, "error": str(e), "question": question}


class QueryService:
    """질의 응답 서비스 클래스"""

    def __init__(self, ctx: RuntimeCtx):
        self.ctx = ctx
        self._agent = None

    @property
    def agent(self) -> Any:
        if self._agent is None:
            self._agent = create_query_agent(self.ctx)
        return self._agent

    async def ask(self, question: str) -> Dict[str, Any]:
        return await query_graph(question, self.ctx)
