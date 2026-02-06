"""YAML 기반 프롬프트 로더

LLM 프롬프트를 YAML 파일로 관리하고 Jinja2로 렌더링합니다.

사용법:
    loader = RuleLoader(target_lang="dbms")
    result = loader.execute("analysis", {"code": "..."}, api_key="...")
    
디렉토리 구조:
    rules/
    ├── dbms/
    │   ├── analysis.yaml
    │   └── ddl.yaml
    └── framework/
        ├── analysis.yaml
        └── field.yaml
"""

import json
import logging
import os
from functools import lru_cache
from typing import Any, Dict

import yaml
from jinja2 import Template, TemplateError
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import RunnablePassthrough

from util.llm_audit import invoke_with_audit
from client.llm_client import get_llm

logger = logging.getLogger(__name__)


def _safe_copy(value: Any) -> Any:
    """JSON 직렬화 가능한 형태로 깊은 복사"""
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError):
        return value


class RuleLoader:
    """YAML 기반 프롬프트/템플릿 로더
    
    Args:
        target_lang: 타겟 언어 (dbms, framework)
    """
    
    __slots__ = ("target_lang", "rule_dir")
    
    def __init__(self, target_lang: str = "framework"):
        self.target_lang = target_lang
        
        base_dir = os.path.dirname(os.path.dirname(__file__))
        self.rule_dir = os.path.join(base_dir, "rules", target_lang)
        
        if not os.path.exists(self.rule_dir):
            raise FileNotFoundError(f"Rule 디렉토리가 존재하지 않습니다: {self.rule_dir}")
    
    @lru_cache(maxsize=32)
    def _load_rule(self, rule_name: str) -> Dict[str, Any]:
        """Rule YAML 파일 로드 (캐싱)"""
        rule_path = os.path.join(self.rule_dir, f"{rule_name}.yaml")
        
        if not os.path.exists(rule_path):
            raise FileNotFoundError(f"Rule 파일이 존재하지 않습니다: {rule_path}")
        
        try:
            with open(rule_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ValueError(f"YAML 파싱 오류 ({rule_path}): {e}")
    
    def validate_inputs(self, rule: Dict[str, Any], inputs: Dict[str, Any]) -> Dict[str, Any]:
        """입력값 검증 및 기본값 설정"""
        validated = inputs.copy()
        
        # 필수 필드 검증
        required = rule.get("input_schema", {}).get("required", [])
        missing = [f for f in required if f not in validated]
        if missing:
            raise ValueError(f"필수 필드 누락: {', '.join(missing)}")
        
        # 선택 필드 기본값 설정 (딕셔너리 형식인 경우만)
        optional = rule.get("input_schema", {}).get("optional", {})
        if isinstance(optional, dict):
            for field, spec in optional.items():
                if field not in validated and isinstance(spec, dict) and "default" in spec:
                    validated[field] = spec["default"]
        
        return validated
    
    def render_template(self, rule_name: str, inputs: Dict[str, Any]) -> str:
        """템플릿 렌더링 (LLM 미호출)"""
        rule = self._load_rule(rule_name)
        validated = self.validate_inputs(rule, inputs)
        
        template_content = rule.get("template", "")
        if not template_content:
            raise ValueError(f"템플릿이 정의되지 않았습니다: {rule_name}")
        
        try:
            return Template(template_content).render(**validated)
        except TemplateError as e:
            raise ValueError(f"템플릿 렌더링 오류 ({rule_name}): {e}")
    
    def render_prompt(self, rule_name: str, inputs: Dict[str, Any]) -> str:
        """프롬프트 렌더링 (LLM 미호출)"""
        rule = self._load_rule(rule_name)
        validated = self.validate_inputs(rule, inputs)
        
        try:
            return Template(rule["prompt"]).render(**validated)
        except TemplateError as e:
            raise ValueError(f"프롬프트 렌더링 오류 ({rule_name}): {e}")
    
    def render_task(self, rule_name: str, inputs: Dict[str, Any]) -> str:
        """task 필드 렌더링 (user_input으로 사용)
        
        Args:
            rule_name: 규칙 이름 (YAML 파일명, 확장자 제외)
            inputs: 프롬프트 입력값
            
        Returns:
            렌더링된 task 문자열. task 필드가 없으면 기본 메시지 반환.
        """
        rule = self._load_rule(rule_name)
        validated = self.validate_inputs(rule, inputs)
        
        task_template = rule.get("task", "")
        if not task_template:
            return "위 입력 데이터를 분석하여 결과를 JSON 형식으로 출력하세요."
        
        try:
            return Template(task_template).render(**validated)
        except TemplateError as e:
            raise ValueError(f"task 렌더링 오류 ({rule_name}): {e}")
    
    def execute(
        self, 
        rule_name: str, 
        inputs: Dict[str, Any], 
        api_key: str,
        model: str | None = None,
    ) -> Dict[str, Any]:
        """프롬프트 실행 (LLM 호출)
        
        Args:
            rule_name: 규칙 이름 (YAML 파일명, 확장자 제외)
            inputs: 프롬프트 입력값
            api_key: LLM API 키
            model: 사용할 LLM 모델 (None이면 기본 모델 사용)
            
        Returns:
            LLM 응답 (JSON 파싱됨)
        """
        try:
            _ = self._load_rule(rule_name)
            prompt_text = self.render_prompt(rule_name, inputs)
            rule_path = os.path.join(self.rule_dir, f"{rule_name}.yaml")

            llm = get_llm(api_key=api_key, model=model)
            chain = (
                RunnablePassthrough()
                | PromptTemplate.from_template("{prompt}")
                | llm
                | JsonOutputParser()
            )

            result = invoke_with_audit(
                chain,
                {"prompt": prompt_text},
                prompt_name=f"rules/{self.target_lang}/{rule_name}",
                input_payload={
                    "inputs": _safe_copy(inputs),
                    "renderedPrompt": prompt_text,
                },
                metadata={
                    "rule": rule_name,
                    "targetLang": self.target_lang,
                    "rulePath": rule_path,
                },
            )
            return result
            
        except Exception as e:
            logger.error("[RULE] 프롬프트 실행 실패 | rule=%s | error=%s", rule_name, e)
            raise RuntimeError(f"{rule_name} 프롬프트 실행 중 오류: {e}")
    
    def clear_cache(self):
        """캐시 초기화"""
        self._load_rule.cache_clear()
