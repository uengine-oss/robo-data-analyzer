"""DeepAgents 클라이언트 — Chunker 전용

청킹(Step 1)만 DeepAgent를 사용합니다.
Step 2/3/4는 RuleLoader LLM chain으로 교체되었습니다.
"""

import json
import re
from typing import Any, Dict, Optional

from deepagents import create_deep_agent

from analyzer.strategy.framework.nontarget.utils.nontarget_logger import (
    log_nontarget,
    log_agent_action,
)
from config.settings import settings
from util.rule_loader import RuleLoader


class DeepAgentsClient:
    """Chunker 전용 DeepAgents 팩토리 (싱글톤)"""

    _instance: Optional["DeepAgentsClient"] = None
    _rule_loader: Optional[RuleLoader] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if self._rule_loader is None:
            try:
                # nontarget 전용 프롬프트(chunker)를 사용
                self._rule_loader = RuleLoader(target_lang="nontarget")
                log_nontarget("DEEPAGENTS", "RuleLoader 초기화 완료 (rules/nontarget/)")
            except FileNotFoundError as e:
                log_nontarget("DEEPAGENTS", f"RuleLoader 초기화 실패: {e}")
                self._rule_loader = None

    def _get_model_str(self, model: Optional[str] = None) -> str:
        if model is None:
            model = settings.llm.model
        return f"openai:{model}" if not model.startswith("openai:") else model

    def _get_system_prompt(self, agent_name: str) -> str:
        if self._rule_loader is None:
            raise RuntimeError("RuleLoader가 초기화되지 않았습니다")
        return self._rule_loader.render_prompt(agent_name, {})

    # ================================================================
    # Chunker Agent (유일한 DeepAgent)
    # ================================================================

    def create_chunker_agent(
        self,
        ctx: Optional[Any] = None,
        model: Optional[str] = None,
    ) -> Any:
        """ChunkerAgent 생성 — Pass 1 패턴 학습

        도구 없이 입력 데이터만으로 분석합니다.
        규칙/경계후보/열린블록 JSON만 반환합니다.
        """
        model_str = self._get_model_str(model)
        # nontarget/chunker.yaml 사용 (RuleLoader target_lang=\"nontarget\")
        prompt = self._get_system_prompt("chunker")

        agent = create_deep_agent(
            system_prompt=prompt,
            tools=[],
            model=model_str,
        )
        log_agent_action("chunker", "create", {"model": model_str})
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
