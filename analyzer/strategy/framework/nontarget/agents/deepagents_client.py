"""DeepAgents 클라이언트 - 4개 독립 에이전트 팩토리

4개 독립 DeepAgents를 생성합니다:
- ChunkerAgent: Pass 1 패턴 학습
- SummarizerAgent: 블록 배치 요약
- LinkerAgent: 관계 후보 탐색
- DescriptionAgent: 엔티티 description 합성

모든 에이전트는 `from deepagents import create_deep_agent`로 생성합니다.
메인 에이전트/서브에이전트 구조 없음 — 시스템(Python)이 오케스트레이션합니다.
"""

import json
import re
from typing import Any, Dict, List, Optional

from deepagents import create_deep_agent
from langchain_core.messages import HumanMessage

from analyzer.strategy.framework.nontarget.utils.nontarget_logger import (
    log_nontarget,
    log_agent_action,
)
from config.settings import settings
from util.rule_loader import RuleLoader


class DeepAgentsClient:
    """4개 독립 DeepAgents 팩토리 (싱글톤)

    RuleLoader로 rules/deepagents/에서 프롬프트를 로드합니다.
    모든 에이전트는 읽기 전용 도구만 사용합니다.
    """

    _instance: Optional["DeepAgentsClient"] = None
    _rule_loader: Optional[RuleLoader] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if self._rule_loader is None:
            try:
                self._rule_loader = RuleLoader(target_lang="deepagents")
                log_nontarget("DEEPAGENTS", "RuleLoader 초기화 완료 (rules/deepagents/)")
            except FileNotFoundError as e:
                log_nontarget("DEEPAGENTS", f"RuleLoader 초기화 실패: {e}")
                self._rule_loader = None

    def _get_model_str(self, model: Optional[str] = None) -> str:
        """모델 문자열 생성"""
        if model is None:
            model = settings.llm.model
        return f"openai:{model}" if not model.startswith("openai:") else model

    def _get_system_prompt(self, agent_name: str) -> str:
        """YAML에서 시스템 프롬프트 렌더링"""
        if self._rule_loader is None:
            raise RuntimeError("RuleLoader가 초기화되지 않았습니다")
        return self._rule_loader.render_prompt(agent_name, {})

    def get_rendered_task(self, agent_name: str, inputs: Dict[str, Any]) -> str:
        """YAML task 필드 렌더링 (워커에서 사용)"""
        if self._rule_loader is None:
            raise RuntimeError("RuleLoader가 초기화되지 않았습니다")
        return self._rule_loader.render_task(agent_name, inputs)

    # ================================================================
    # 4개 독립 에이전트 생성
    # ================================================================

    def create_chunker_agent(
        self,
        ctx: Optional[Any] = None,
        model: Optional[str] = None,
    ) -> Any:
        """ChunkerAgent 생성 - Pass 1 패턴 학습

        읽기 전용 도구만 제공합니다.
        블록을 확정하지 않고 규칙/경계후보/열린블록 JSON만 반환합니다.
        """
        model_str = self._get_model_str(model)
        prompt = self._get_system_prompt("agents/chunker")

        from analyzer.strategy.framework.nontarget.tools import get_analysis_tools
        tools = get_analysis_tools(ctx) if ctx else []

        agent = create_deep_agent(
            system_prompt=prompt,
            tools=tools,
            model=model_str,
        )
        log_agent_action("chunker", "create", {"model": model_str})
        return agent

    def create_summarizer_agent(
        self,
        ctx: Optional[Any] = None,
        model: Optional[str] = None,
    ) -> Any:
        """SummarizerAgent 생성 - 블록 배치 요약

        읽기 전용 도구만 제공합니다.
        summary/kind/keywords/tables JSON을 반환합니다.
        """
        model_str = self._get_model_str(model)
        prompt = self._get_system_prompt("agents/summarizer")

        from analyzer.strategy.framework.nontarget.tools import get_analysis_tools
        tools = get_analysis_tools(ctx) if ctx else []

        agent = create_deep_agent(
            system_prompt=prompt,
            tools=tools,
            model=model_str,
        )
        log_agent_action("summarizer", "create", {"model": model_str})
        return agent

    def create_linker_agent(
        self,
        ctx: Optional[Any] = None,
        model: Optional[str] = None,
    ) -> Any:
        """LinkerAgent 생성 - 관계 후보 탐색

        읽기 전용 도구 + 검색 도구를 제공합니다.
        관계 후보 JSON만 반환합니다 (description/summary 업데이트 금지).
        """
        model_str = self._get_model_str(model)
        prompt = self._get_system_prompt("agents/linker")

        from analyzer.strategy.framework.nontarget.tools import get_analysis_tools
        tools = get_analysis_tools(ctx) if ctx else []

        agent = create_deep_agent(
            system_prompt=prompt,
            tools=tools,
            model=model_str,
        )
        log_agent_action("linker", "create", {"model": model_str})
        return agent

    def create_description_agent(
        self,
        ctx: Optional[Any] = None,
        model: Optional[str] = None,
    ) -> Any:
        """DescriptionAgent 생성 - 엔티티 description 합성

        읽기 전용 도구만 제공합니다.
        그래프 증거를 종합하여 description JSON을 반환합니다.
        """
        model_str = self._get_model_str(model)
        prompt = self._get_system_prompt("agents/description")

        from analyzer.strategy.framework.nontarget.tools import get_analysis_tools
        tools = get_analysis_tools(ctx) if ctx else []

        agent = create_deep_agent(
            system_prompt=prompt,
            tools=tools,
            model=model_str,
        )
        log_agent_action("description", "create", {"model": model_str})
        return agent


# ============================================================================
# 에이전트 응답 파싱 헬퍼
# ============================================================================

def extract_agent_output(result: Dict[str, Any]) -> str:
    """에이전트 응답에서 출력 텍스트 추출"""
    if "messages" in result and result["messages"]:
        last_msg = result["messages"][-1]
        if hasattr(last_msg, "content"):
            return last_msg.content
        elif isinstance(last_msg, dict) and "content" in last_msg:
            return last_msg["content"]
    if "output" in result:
        return result["output"]
    return str(result)


def parse_json_from_output(output: str) -> Dict[str, Any]:
    """출력에서 JSON 파싱 (코드펜스 지원)"""
    # 코드펜스 내 JSON 찾기
    json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', output)
    if json_match:
        json_str = json_match.group(1).strip()
    else:
        json_str = output.strip()
        obj_match = re.search(r'\{[\s\S]*\}', json_str)
        if obj_match:
            json_str = obj_match.group(0)

    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        log_nontarget("DEEPAGENTS", f"JSON 파싱 실패: {e}\n출력: {output[:500]}")
        raise RuntimeError(f"JSON 파싱 실패") from e
