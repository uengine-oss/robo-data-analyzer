"""
Docs Tools - 문서 검색 및 슬라이싱 도구

규칙:
- docs_search: scope 제한 필수 (폴더/확장자 기준)
- docs_slice: 바이트 offset 기준, 30KB limit
- 전체 docs 스캔 금지

🚨 소스 파일 탐색 금지:
- .java, .kt, .py 등 소스 코드 파일은 docs에서 접근 금지
- 코드 분석은 Neo4j 그래프(Cypher)로만 수행
- 허용: XML, YAML, properties, SQL 등 외부 문서만
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import os
import glob as glob_module
import re


@dataclass
class SearchResult:
    """검색 결과"""
    file_path: str
    byte_offset: int
    line_number: int
    text: str
    context: str = ""


# 🚨 소스 코드 파일 확장자 (탐색 금지)
BLOCKED_EXTENSIONS = {'.java', '.kt', '.py', '.cs', '.ts', '.js', '.go', '.rs', '.cpp', '.c', '.h', '.scala'}

# 검색 scope를 폴더/확장자 기준으로 제한
# ⚠️ jpa scope 제거 (Java 파일 탐색 금지)
SEARCH_SCOPES = {
    "mybatis": "**/mapper/*.xml",
    "ofbiz_entity": "**/entitydef/*.xml",
    "ofbiz_service": "**/servicedef/*.xml",
    "config": "**/*.yml",
    "config_yaml": "**/*.yaml",
    "spring": "**/application*.yml",
    "sql": "**/*.sql",
    "properties": "**/*.properties",
}


class DocsTools:
    """문서 검색/슬라이싱 도구 (소스 코드 파일 탐색 금지)
    
    🚨 중복 호출 방지: 세션 내 캐싱 지원
    - docs_slice: (file_path, offset, limit) 기준 캐싱
    - parse_*: (xml_content hash) 기준 캐싱
    """
    
    MAX_SLICE_LIMIT = 30000  # 30KB
    
    def __init__(self, docs_root: str):
        """
        Args:
            docs_root: docs 폴더 루트 경로 (절대 경로)
        """
        self.docs_root = os.path.abspath(docs_root)
        
        # 세션 내 중복 호출 방지 캐시
        self._slice_cache: Dict[str, Dict[str, Any]] = {}  # (file_path, offset, limit) → result
        self._parse_cache: Dict[str, Dict[str, Any]] = {}  # content_hash → result
    
    def _normalize_path(self, path: str) -> str:
        """경로 정규화: 백슬래시 → 슬래시"""
        return path.replace("\\", "/")
    
    def _is_blocked_file(self, file_path: str) -> bool:
        """소스 코드 파일인지 확인 (차단 대상)"""
        _, ext = os.path.splitext(file_path.lower())
        return ext in BLOCKED_EXTENSIONS
    
    def _to_virtual_path(self, real_path: str) -> str:
        """실제 경로 → 가상 경로 (/docs/...)"""
        rel = os.path.relpath(real_path, self.docs_root)
        return "/docs/" + self._normalize_path(rel)
    
    def _to_real_path(self, virtual_path: str) -> str:
        """가상 경로 → 실제 경로"""
        # /docs/ 제거
        if virtual_path.startswith("/docs/"):
            rel = virtual_path[6:]
        elif virtual_path.startswith("docs/"):
            rel = virtual_path[5:]
        else:
            rel = virtual_path.lstrip("/")
        return os.path.join(self.docs_root, rel)
    
    def docs_search(
        self,
        keyword: str,
        hint_type: Optional[str] = None,
        scope: Optional[str] = None,
        max_results: int = 50
    ) -> List[SearchResult]:
        """
        키워드로 문서 검색 (scope 제한 필수)
        
        Args:
            keyword: 검색 키워드
            hint_type: 힌트 유형 (mybatis, ofbiz_entity, ofbiz_service, config, jpa)
            scope: 커스텀 glob 패턴 (hint_type보다 우선)
            max_results: 최대 결과 수
        
        Returns:
            SearchResult 목록
        """
        # scope 결정
        if scope:
            glob_pattern = scope
        elif hint_type and hint_type in SEARCH_SCOPES:
            glob_pattern = SEARCH_SCOPES[hint_type]
        else:
            # 기본: XML, YAML만
            glob_pattern = "**/*.{xml,yml,yaml}"
        
        # glob 패턴으로 파일 목록
        full_pattern = os.path.join(self.docs_root, glob_pattern)
        
        # Windows 호환: glob.glob에서 recursive=True
        try:
            # {xml,yml,yaml} 같은 패턴 처리
            if "{" in glob_pattern:
                # 패턴 분리
                base, ext_part = glob_pattern.rsplit(".", 1)
                extensions = ext_part.strip("{}").split(",")
                files = []
                for ext in extensions:
                    pattern = os.path.join(self.docs_root, f"{base}.{ext}")
                    files.extend(glob_module.glob(pattern, recursive=True))
            else:
                files = glob_module.glob(full_pattern, recursive=True)
        except Exception:
            files = []
        
        results = []
        keyword_lower = keyword.lower()
        
        for file_path in files:
            if not os.path.isfile(file_path):
                continue
            
            # 🚨 소스 코드 파일 필터링
            if self._is_blocked_file(file_path):
                continue
            
            try:
                # 파일 읽기 (바이트 offset 계산을 위해 binary로)
                with open(file_path, 'rb') as f:
                    content_bytes = f.read()
                
                # UTF-8 디코딩 (오류 무시)
                content = content_bytes.decode('utf-8', errors='replace')
                lines = content.split('\n')
                
                byte_offset = 0
                for line_num, line in enumerate(lines, 1):
                    if keyword_lower in line.lower():
                        # 컨텍스트 (앞뒤 2줄)
                        start_ctx = max(0, line_num - 3)
                        end_ctx = min(len(lines), line_num + 2)
                        context_lines = lines[start_ctx:end_ctx]
                        
                        results.append(SearchResult(
                            file_path=self._to_virtual_path(file_path),
                            byte_offset=byte_offset,
                            line_number=line_num,
                            text=line.strip()[:200],
                            context='\n'.join(context_lines)[:500]
                        ))
                        
                        if len(results) >= max_results:
                            return results
                    
                    # 바이트 offset 업데이트 (줄바꿈 포함)
                    line_bytes = (line + '\n').encode('utf-8', errors='replace')
                    byte_offset += len(line_bytes)
                    
            except Exception:
                continue
        
        return results
    
    def docs_slice(
        self,
        file_path: str,
        byte_offset: int = 0,
        limit: int = 30000
    ) -> Dict[str, Any]:
        """
        파일 부분 읽기 (바이트 offset 기준)
        
        🚨 소스 코드 파일(.java, .kt 등)은 차단됨!
        코드 분석은 Neo4j 그래프로만 수행하세요.
        
        Args:
            file_path: 파일 경로 (가상 경로 또는 실제 경로)
            byte_offset: 시작 바이트 offset
            limit: 읽을 바이트 수 (최대 30KB)
        
        Returns:
            {
                "content": str,
                "file_path": str,
                "byte_offset": int,
                "bytes_read": int,
                "mtime": float,
                "size": int
            }
        """
        # 🚨 소스 코드 파일 차단
        if self._is_blocked_file(file_path):
            _, ext = os.path.splitext(file_path)
            return {
                "error": f"BLOCKED: 소스 코드 파일({ext})은 docs에서 접근 금지. Neo4j 그래프(search_nodes_by_keyword, node_code)를 사용하세요.",
                "file_path": file_path,
                "content": "",
                "blocked": True
            }
        
        # limit 강제
        limit = min(limit, self.MAX_SLICE_LIMIT)
        
        # 🚨 중복 호출 방지: 캐시 확인
        cache_key = f"{file_path}:{byte_offset}:{limit}"
        if cache_key in self._slice_cache:
            cached = self._slice_cache[cache_key]
            cached["from_cache"] = True
            return cached
        
        # 경로 변환
        real_path = self._to_real_path(file_path)
        
        if not os.path.exists(real_path):
            return {
                "error": f"File not found: {file_path}",
                "file_path": file_path,
                "content": ""
            }
        
        try:
            stat = os.stat(real_path)
            
            with open(real_path, 'rb') as f:
                f.seek(byte_offset)
                content_bytes = f.read(limit)
            
            # UTF-8 디코딩
            content = content_bytes.decode('utf-8', errors='replace')
            
            result = {
                "content": content,
                "file_path": self._to_virtual_path(real_path),
                "byte_offset": byte_offset,
                "bytes_read": len(content_bytes),
                "mtime": stat.st_mtime,
                "size": stat.st_size
            }
            
            # 캐시에 저장
            self._slice_cache[cache_key] = result
            return result
            
        except Exception as e:
            return {
                "error": str(e),
                "file_path": file_path,
                "content": ""
            }
    
    def docs_slice_around_match(
        self,
        file_path: str,
        keyword: str,
        context_bytes: int = 5000
    ) -> Dict[str, Any]:
        """
        키워드 주변 슬라이스 (키워드 위치 기준 앞뒤로 추출)
        
        🚨 소스 코드 파일(.java, .kt 등)은 차단됨!
        
        Args:
            file_path: 파일 경로
            keyword: 검색 키워드
            context_bytes: 앞뒤로 읽을 바이트 수 (기본 5KB씩)
        
        Returns:
            docs_slice와 동일한 형식 + match_offset
        """
        # 🚨 소스 코드 파일 차단
        if self._is_blocked_file(file_path):
            _, ext = os.path.splitext(file_path)
            return {
                "error": f"BLOCKED: 소스 코드 파일({ext})은 docs에서 접근 금지. Neo4j 그래프를 사용하세요.",
                "file_path": file_path,
                "content": "",
                "blocked": True
            }
        
        real_path = self._to_real_path(file_path)
        
        if not os.path.exists(real_path):
            return {
                "error": f"File not found: {file_path}",
                "file_path": file_path,
                "content": ""
            }
        
        try:
            with open(real_path, 'rb') as f:
                content_bytes = f.read()
            
            content = content_bytes.decode('utf-8', errors='replace')
            
            # 키워드 위치 찾기 (대소문자 무시)
            match_pos = content.lower().find(keyword.lower())
            
            if match_pos == -1:
                return {
                    "error": f"Keyword '{keyword}' not found in file",
                    "file_path": file_path,
                    "content": ""
                }
            
            # 바이트 offset 계산 (UTF-8)
            byte_offset = len(content[:match_pos].encode('utf-8', errors='replace'))
            
            # 앞뒤로 context_bytes만큼 슬라이스
            start_offset = max(0, byte_offset - context_bytes)
            total_limit = min(context_bytes * 2, self.MAX_SLICE_LIMIT)
            
            result = self.docs_slice(file_path, start_offset, total_limit)
            result["match_offset"] = byte_offset - start_offset
            
            return result
            
        except Exception as e:
            return {
                "error": str(e),
                "file_path": file_path,
                "content": ""
            }
    
    def get_file_info(self, file_path: str) -> Dict[str, Any]:
        """
        파일 정보 조회 (캐시 무효화용)
        
        Args:
            file_path: 파일 경로
        
        Returns:
            {"mtime": float, "size": int, "exists": bool}
        """
        real_path = self._to_real_path(file_path)
        
        if not os.path.exists(real_path):
            return {"exists": False, "mtime": 0, "size": 0}
        
        try:
            stat = os.stat(real_path)
            return {
                "exists": True,
                "mtime": stat.st_mtime,
                "size": stat.st_size
            }
        except Exception:
            return {"exists": False, "mtime": 0, "size": 0}

    def extract_enclosing_element(
        self,
        file_path: str,
        keyword: str,
        max_depth: int = 3,
        max_size: int = 50000
    ) -> Dict[str, Any]:
        """
        키워드를 포함하는 가장 가까운 XML 요소 전체 추출 (범용)
        
        어떤 태그든 상관없이 동작:
        - <entity entity-name="OrderHeader">...</entity>
        - <select id="selectOrderById">...</select>
        - <bean id="orderService">...</bean>
        - <service name="createOrder">...</service>
        
        Args:
            file_path: 파일 경로
            keyword: 찾을 키워드 (예: "OrderHeader", "createOrder")
            max_depth: 최대 탐색 깊이 (너무 큰 부모 태그 방지)
            max_size: 최대 추출 크기 (바이트)
        
        Returns:
            {"tag_name": str, "content": str, "start": int, "end": int}
        """
        import re
        
        # 소스 코드 파일 차단
        if self._is_blocked_file(file_path):
            _, ext = os.path.splitext(file_path)
            return {
                "error": f"BLOCKED: 소스 코드 파일({ext})은 docs에서 접근 금지",
                "content": ""
            }
        
        real_path = self._to_real_path(file_path)
        
        if not os.path.exists(real_path):
            return {"error": f"File not found: {file_path}", "content": ""}
        
        try:
            with open(real_path, 'r', encoding='utf-8', errors='replace') as f:
                xml_content = f.read()
            
            # 1. 키워드 위치 찾기
            keyword_lower = keyword.lower()
            content_lower = xml_content.lower()
            keyword_pos = content_lower.find(keyword_lower)
            
            if keyword_pos == -1:
                return {"error": f"Keyword '{keyword}' not found", "content": ""}
            
            # 2. 키워드 이전의 가장 가까운 여는 태그 찾기
            before_keyword = xml_content[:keyword_pos]
            
            # 모든 여는 태그 찾기: <tagName ... > (self-closing 제외)
            open_tags = list(re.finditer(r'<([a-zA-Z][\w:-]*)[^>]*(?<!/)>', before_keyword))
            
            if not open_tags:
                return {"error": "No enclosing tag found", "content": ""}
            
            # 3. 가장 가까운 여는 태그부터 시도 (max_depth 제한)
            candidates = list(reversed(open_tags[-max_depth:]))
            
            for match in candidates:
                tag_name = match.group(1)
                start_pos = match.start()
                
                # 4. 해당 태그의 닫는 태그 찾기 (중첩 처리)
                after_start = xml_content[start_pos:]
                depth = 0
                end_pos = None
                
                for m in re.finditer(rf'</?{re.escape(tag_name)}(?:\s[^>]*)?>|<{re.escape(tag_name)}(?:\s[^>]*)?/>', 
                                     after_start, re.IGNORECASE):
                    tag_text = m.group()
                    if tag_text.startswith('</'):
                        depth -= 1
                        if depth == 0:
                            end_pos = start_pos + m.end()
                            break
                    elif tag_text.endswith('/>'):
                        # self-closing, depth 변화 없음
                        pass
                    else:
                        depth += 1
                
                if end_pos:
                    element = xml_content[start_pos:end_pos]
                    # 크기 제한 체크
                    if len(element) <= max_size:
                        return {
                            "tag_name": tag_name,
                            "content": element,
                            "start": start_pos,
                            "end": end_pos,
                            "size": len(element)
                        }
            
            return {"error": "Could not find suitable enclosing element (too large or unclosed)", "content": ""}
            
        except Exception as e:
            return {"error": str(e), "content": ""}

    def extract_yaml_block(
        self,
        file_path: str,
        key_path: str,
        include_parent: bool = True
    ) -> Dict[str, Any]:
        """
        YAML 중첩 키의 블록 추출
        
        예: key_path="order.max-items-per-order"
        → "order:" 블록 전체 또는 "max-items-per-order:" 라인 추출
        
        Args:
            file_path: 파일 경로
            key_path: 점(.)으로 구분된 키 경로 (예: "order.max-items-per-order")
            include_parent: True면 상위 블록 포함, False면 해당 키만
        
        Returns:
            {"key": str, "content": str, "start_line": int, "end_line": int}
        """
        real_path = self._to_real_path(file_path)
        
        if not os.path.exists(real_path):
            return {"error": f"File not found: {file_path}", "content": ""}
        
        try:
            with open(real_path, 'r', encoding='utf-8', errors='replace') as f:
                lines = f.readlines()
            
            keys = key_path.split('.')
            
            # 각 키의 시작/끝 라인 추적
            key_positions = {}  # {key_level: {"start": int, "indent": int}}
            current_indent = -1
            
            for i, line in enumerate(lines):
                stripped = line.lstrip()
                if not stripped or stripped.startswith('#'):
                    continue
                
                indent = len(line) - len(stripped)
                
                # 키 찾기
                for level, key in enumerate(keys):
                    key_pattern = f"{key}:"
                    if stripped.startswith(key_pattern):
                        # 상위 레벨 indent보다 크거나 같아야 함
                        if level == 0 or (level - 1 in key_positions and 
                                         indent > key_positions[level - 1]["indent"]):
                            key_positions[level] = {"start": i, "indent": indent}
            
            if not key_positions:
                return {"error": f"Key path '{key_path}' not found", "content": ""}
            
            # 추출할 레벨 결정
            if include_parent:
                target_level = 0  # 최상위 키부터
            else:
                target_level = max(key_positions.keys())  # 가장 깊은 키
            
            if target_level not in key_positions:
                return {"error": f"Target level not found", "content": ""}
            
            start_line = key_positions[target_level]["start"]
            start_indent = key_positions[target_level]["indent"]
            
            # 해당 블록 끝 찾기 (같은 또는 더 작은 indent 만날 때까지)
            end_line = start_line + 1
            for i in range(start_line + 1, len(lines)):
                stripped = lines[i].lstrip()
                if not stripped or stripped.startswith('#'):
                    continue
                indent = len(lines[i]) - len(stripped)
                if indent <= start_indent:
                    break
                end_line = i + 1
            
            content = ''.join(lines[start_line:end_line])
            
            return {
                "key": keys[target_level] if include_parent else key_path,
                "content": content,
                "start_line": start_line + 1,  # 1-based
                "end_line": end_line,
                "file_path": file_path
            }
            
        except Exception as e:
            return {"error": str(e), "content": ""}


def create_docs_tools(docs_root: str) -> List:
    """DocsTools 기반 LangChain 도구 생성"""
    from langchain_core.tools import StructuredTool
    import json
    
    tools = DocsTools(docs_root)
    
    def docs_search_tool(
        keyword: str,
        hint_type: str = "",
        scope: str = "",
        max_results: int = 50
    ) -> str:
        """
        문서 검색 (scope 제한 적용)
        
        Args:
            keyword: 검색 키워드
            hint_type: 힌트 유형 (mybatis, ofbiz_entity, ofbiz_service, config, jpa)
            scope: 커스텀 glob 패턴 (선택)
            max_results: 최대 결과 수
        
        Returns:
            검색 결과 JSON
        """
        results = tools.docs_search(
            keyword=keyword,
            hint_type=hint_type if hint_type else None,
            scope=scope if scope else None,
            max_results=max_results
        )
        
        return json.dumps([{
            "file_path": r.file_path,
            "byte_offset": r.byte_offset,
            "line_number": r.line_number,
            "text": r.text,
            "context": r.context
        } for r in results], ensure_ascii=False, indent=2)
    
    def docs_slice_tool(
        file_path: str,
        byte_offset: int = 0,
        limit: int = 30000
    ) -> str:
        """
        파일 부분 읽기 (바이트 offset 기준, 최대 30KB)
        
        Args:
            file_path: 파일 경로 (/docs/로 시작)
            byte_offset: 시작 바이트 offset
            limit: 읽을 바이트 수 (최대 30000)
        
        Returns:
            슬라이스 결과 JSON
        """
        result = tools.docs_slice(file_path, byte_offset, limit)
        return json.dumps(result, ensure_ascii=False, indent=2)
    
    def docs_slice_around_match_tool(
        file_path: str,
        keyword: str,
        context_bytes: int = 5000
    ) -> str:
        """
        키워드 주변 슬라이스 (키워드 위치 기준 앞뒤로 추출)
        
        Args:
            file_path: 파일 경로
            keyword: 검색 키워드
            context_bytes: 앞뒤로 읽을 바이트 수 (기본 5000)
        
        Returns:
            슬라이스 결과 JSON
        """
        result = tools.docs_slice_around_match(file_path, keyword, context_bytes)
        return json.dumps(result, ensure_ascii=False, indent=2)
    
    def extract_enclosing_element_tool(
        file_path: str,
        keyword: str,
        max_depth: int = 3
    ) -> str:
        """
        키워드를 포함하는 XML 요소 전체 추출 (범용)
        
        어떤 태그든 동작: entity, select, bean, service 등
        예: keyword="OrderHeader" → <entity entity-name="OrderHeader">...</entity> 전체
        
        Args:
            file_path: XML 파일 경로
            keyword: 찾을 키워드 (예: "OrderHeader", "createOrder", "selectById")
            max_depth: 최대 탐색 깊이 (기본 3, 너무 큰 부모 태그 방지)
        
        Returns:
            {"tag_name": str, "content": str, "start": int, "end": int, "size": int}
        """
        result = tools.extract_enclosing_element(file_path, keyword, max_depth)
        return json.dumps(result, ensure_ascii=False, indent=2)
    
    def extract_yaml_block_tool(
        file_path: str,
        key_path: str,
        include_parent: bool = True
    ) -> str:
        """
        YAML 중첩 키의 블록 추출
        
        예: key_path="order.max-items-per-order", include_parent=True
        → "order:" 블록 전체 반환 (order.default-currency 등 형제 키 포함)
        
        예: key_path="order.max-items-per-order", include_parent=False
        → "max-items-per-order: 100" 해당 라인만 반환
        
        Args:
            file_path: YAML 파일 경로
            key_path: 점(.)으로 구분된 키 경로 (예: "order.max-items-per-order")
            include_parent: True면 상위 블록 포함, False면 해당 키만
        
        Returns:
            {"key": str, "content": str, "start_line": int, "end_line": int}
        """
        result = tools.extract_yaml_block(file_path, key_path, include_parent)
        return json.dumps(result, ensure_ascii=False, indent=2)
    
    return [
        StructuredTool.from_function(
            name="docs_search",
            description="문서 검색 (scope 제한 적용). keyword: 검색어, hint_type: mybatis/ofbiz_entity/ofbiz_service/config/jpa, scope: 커스텀 glob (선택)",
            func=docs_search_tool
        ),
        StructuredTool.from_function(
            name="docs_slice",
            description="파일 부분 읽기 (바이트 offset 기준, 최대 30KB). file_path: /docs/로 시작하는 경로, byte_offset: 시작 위치, limit: 바이트 수",
            func=docs_slice_tool
        ),
        StructuredTool.from_function(
            name="docs_slice_around_match",
            description="(🚨 XML/YAML은 extract_enclosing_element/extract_yaml_block 권장!) 키워드 주변 바이트 슬라이스. 단순 텍스트 파일용.",
            func=docs_slice_around_match_tool
        ),
        StructuredTool.from_function(
            name="extract_enclosing_element",
            description="🔥 XML 필수! 키워드를 포함하는 XML 태그 전체 추출. keyword: 찾을 키워드 (예: OrderHeader, createOrder), max_depth: 탐색 깊이",
            func=extract_enclosing_element_tool
        ),
        StructuredTool.from_function(
            name="extract_yaml_block",
            description="🔥 YAML 필수! 중첩 키의 상위 블록 추출. key_path: 점 구분 키 (예: order.max-items), include_parent: True면 상위 블록 포함",
            func=extract_yaml_block_tool
        ),
    ]

