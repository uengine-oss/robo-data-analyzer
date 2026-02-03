"""
YAML Parser (결정론적)

Spring Boot application.yml 등 설정 파일 파싱.

사용 예시:
    parser = YAMLParser()
    result = parser.parse("application.yml", content)
    value = result.get("spring.datasource.url")
"""

from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
import yaml
import re


@dataclass
class YAMLParseResult:
    """YAML 파싱 결과"""
    file_path: str
    data: Dict[str, Any]
    flat_keys: Dict[str, Any]  # 점 표기법 키
    errors: List[str] = field(default_factory=list)
    
    def get(self, key: str, default: Any = None) -> Any:
        """점 표기법으로 값 조회"""
        return self.flat_keys.get(key, default)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_path": self.file_path,
            "data": self.data,
            "flat_keys": self.flat_keys,
            "errors": self.errors,
        }


class YAMLParser:
    """YAML 파서 (결정론적)"""
    
    def parse(self, file_path: str, content: str) -> YAMLParseResult:
        """
        YAML 파싱
        
        Args:
            file_path: 파일 경로
            content: YAML 내용
        
        Returns:
            YAMLParseResult
        """
        errors = []
        data = {}
        flat_keys = {}
        
        try:
            # 멀티 문서 지원
            docs = list(yaml.safe_load_all(content))
            
            if docs:
                # 첫 번째 문서 사용 (또는 병합)
                for doc in docs:
                    if doc:
                        data = self._merge_dicts(data, doc)
                
                # 점 표기법 키로 평탄화
                flat_keys = self._flatten(data)
        
        except yaml.YAMLError as e:
            errors.append(f"YAML parse error: {e}")
            # Regex fallback
            flat_keys = self._regex_fallback(content)
        
        return YAMLParseResult(
            file_path=file_path,
            data=data,
            flat_keys=flat_keys,
            errors=errors,
        )
    
    def _flatten(
        self, 
        data: Dict[str, Any], 
        prefix: str = ""
    ) -> Dict[str, Any]:
        """딕셔너리를 점 표기법으로 평탄화"""
        result = {}
        
        for key, value in data.items():
            full_key = f"{prefix}.{key}" if prefix else key
            
            if isinstance(value, dict):
                result.update(self._flatten(value, full_key))
            elif isinstance(value, list):
                result[full_key] = value
                # 리스트 항목도 인덱스로 접근 가능하게
                for i, item in enumerate(value):
                    if isinstance(item, dict):
                        result.update(self._flatten(item, f"{full_key}[{i}]"))
                    else:
                        result[f"{full_key}[{i}]"] = item
            else:
                result[full_key] = value
        
        return result
    
    def _merge_dicts(
        self, 
        base: Dict[str, Any], 
        override: Dict[str, Any]
    ) -> Dict[str, Any]:
        """딕셔너리 병합 (override가 우선)"""
        result = base.copy()
        
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._merge_dicts(result[key], value)
            else:
                result[key] = value
        
        return result
    
    def _regex_fallback(self, content: str) -> Dict[str, Any]:
        """YAML 파싱 실패 시 regex fallback"""
        flat_keys = {}
        
        # 간단한 key: value 패턴
        pattern = r'^(\s*)([a-zA-Z0-9_-]+)\s*:\s*(.*)$'
        
        key_stack = []
        prev_indent = -1
        
        for line in content.split('\n'):
            match = re.match(pattern, line)
            if not match:
                continue
            
            indent = len(match.group(1))
            key = match.group(2)
            value = match.group(3).strip()
            
            # 들여쓰기에 따라 키 스택 조정
            if indent > prev_indent:
                pass  # 하위 레벨
            else:
                # 상위 레벨로 이동
                levels_up = (prev_indent - indent) // 2 + 1
                key_stack = key_stack[:-levels_up] if levels_up <= len(key_stack) else []
            
            key_stack.append(key)
            prev_indent = indent
            
            # 값이 있으면 저장
            if value and not value.startswith('#'):
                # 따옴표 제거
                if value.startswith('"') and value.endswith('"'):
                    value = value[1:-1]
                elif value.startswith("'") and value.endswith("'"):
                    value = value[1:-1]
                
                full_key = '.'.join(key_stack)
                flat_keys[full_key] = value
        
        return flat_keys
    
    def extract_datasource_config(
        self, 
        result: YAMLParseResult
    ) -> Dict[str, Any]:
        """Spring DataSource 설정 추출"""
        config = {}
        
        # 일반적인 키 패턴
        patterns = [
            "spring.datasource.url",
            "spring.datasource.username",
            "spring.datasource.password",
            "spring.datasource.driver-class-name",
            "spring.jpa.database-platform",
            "spring.jpa.hibernate.ddl-auto",
        ]
        
        for pattern in patterns:
            value = result.get(pattern)
            if value:
                config[pattern] = value
        
        return config
    
    def extract_mybatis_config(
        self, 
        result: YAMLParseResult
    ) -> Dict[str, Any]:
        """MyBatis 설정 추출"""
        config = {}
        
        patterns = [
            "mybatis.mapper-locations",
            "mybatis.type-aliases-package",
            "mybatis.configuration.map-underscore-to-camel-case",
        ]
        
        for pattern in patterns:
            value = result.get(pattern)
            if value:
                config[pattern] = value
        
        return config

