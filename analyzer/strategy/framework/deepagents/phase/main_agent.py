"""
Main DeepAgent (공식 deepagents 라이브러리 사용)

런타임 해석 에이전트.
공식 create_deep_agent API + 커스텀 서브에이전트 + CompositeBackend.

안전장치:
- EvidenceManager: S7 선행조건, S8 다중파일
- MinimalLock: 동시 커밋 방지 (table_name/elementId 단위)
- 노드 속성 기반: pending_review 관리 (deepagents_status)
- RuntimePolicy: 도구 권한/정책 강제 (30KB limit, 10회 상한)

사용 예시:
    agent = create_runtime_resolution_agent(
        neo4j_driver=driver,
        docs_root="data/docs",  # 외부 설정 파일 (프로젝트 루트)
        ddl_root="data/ddl",     # DDL 파일 (프로젝트 루트)
        runs_root="data/runs",   # 작업 워크스페이스 (프로젝트 루트)
        # skills_root, memory_root는 기본값 사용 (framework/deepagents/resources/)
    )
    
    # 후보 처리
    result = await agent.invoke({"messages": [...]})
"""

from typing import Any, Dict, List, Optional
from dataclasses import dataclass
import os
import asyncio
from pathlib import Path

# 공식 deepagents 라이브러리
from deepagents import create_deep_agent, CompiledSubAgent
from deepagents.backends import CompositeBackend, StateBackend, FilesystemBackend, StoreBackend


class PathNormalizingFilesystemBackend:
    """경로 정규화 래퍼 - deepagents의 FilesystemBackend를 완전히 대체
    
    deepagents의 FilesystemBackend가 Windows 경로와 glob을 제대로 처리하지 못하므로,
    Python 기본 파일 시스템 함수로 모든 메서드를 직접 구현합니다.
    
    주요 기능:
    - 경로 정규화: 백슬래시→슬래시 자동 변환
    - ls_info/als_info: 디렉토리 목록 (deepagents 인터페이스 호환)
    - glob_info/aglob_info: 파일 패턴 검색 (deepagents 인터페이스 호환)
    - grep/grep_raw/agrep_raw: 텍스트 검색 (deepagents 인터페이스 호환)
    - read/aread: 파일 읽기 (전체 내용)
    - write/awrite: 파일 쓰기
    """
    
    def __init__(self, backend: FilesystemBackend, root_dir: str, virtual_prefix: str = "/docs/"):
        self._backend = backend
        self._root_dir = os.path.abspath(root_dir)
        self._virtual_prefix = virtual_prefix.rstrip("/") + "/"  # 항상 슬래시로 끝나도록
        import glob as glob_module
        import re
        self._glob_module = glob_module
        self._re_module = re
    
    def _normalize_path(self, path: str) -> str:
        """경로 정규화: 백슬래시→슬래시, 이중슬래시 제거"""
        if not path:
            return "/"
        # 백슬래시를 슬래시로 변환
        normalized = path.replace("\\", "/")
        # 이중 슬래시 정리 (프로토콜 제외)
        while "//" in normalized and not normalized.startswith("//"):
            normalized = normalized.replace("//", "/")
        return normalized
    
    def _virtual_to_real(self, virtual_path: str) -> str:
        """가상 경로를 실제 경로로 변환
        
        예: / → root_dir, /SAMPLE/file.xml → root_dir/SAMPLE/file.xml
        """
        normalized = self._normalize_path(virtual_path)
        
        # /docs/ 프리픽스가 있으면 제거 (CompositeBackend가 이미 제거했을 수 있음)
        if normalized.startswith(self._virtual_prefix):
            relative = normalized[len(self._virtual_prefix):]
        elif normalized.startswith("/"):
            relative = normalized[1:]  # leading slash 제거
        else:
            relative = normalized
        
        # 빈 경로면 root_dir 반환
        if not relative:
            return self._root_dir
        
        return os.path.join(self._root_dir, relative)
    
    def _real_to_virtual(self, real_path: str) -> str:
        """실제 경로를 가상 경로로 변환
        
        예: root_dir/SAMPLE/file.xml → /SAMPLE/file.xml
        """
        real_path = self._normalize_path(real_path)
        root_normalized = self._normalize_path(self._root_dir)
        
        if real_path.startswith(root_normalized):
            relative = real_path[len(root_normalized):]
            if not relative:
                return "/"
            if not relative.startswith("/"):
                relative = "/" + relative
            return relative
        return real_path
    
    def _get_file_info(self, real_path: str) -> dict:
        """파일 정보를 FileInfo 형식으로 반환"""
        virtual_path = self._real_to_virtual(real_path)
        is_dir = os.path.isdir(real_path)
        
        try:
            stat = os.stat(real_path)
            size = stat.st_size if not is_dir else 0
            modified_at = stat.st_mtime
        except Exception:
            size = 0
            modified_at = 0
        
        return {
            "path": virtual_path + "/" if is_dir else virtual_path,
            "is_dir": is_dir,
            "size": size,
            "modified_at": str(modified_at),
        }
    
    # =========================================================================
    # ls_info / als_info - deepagents 인터페이스
    # =========================================================================
    
    def ls_info(self, path: str = "/") -> list:
        """디렉토리 목록 반환 (sync) - deepagents 인터페이스"""
        real_path = self._virtual_to_real(path)
        
        if not os.path.isdir(real_path):
            return []
        
        try:
            entries = os.listdir(real_path)
            result = []
            for entry in sorted(entries):
                full_path = os.path.join(real_path, entry)
                result.append(self._get_file_info(full_path))
            return result
        except Exception as e:
            return []
    
    async def als_info(self, path: str = "/") -> list:
        """디렉토리 목록 반환 (async) - deepagents 인터페이스"""
        return self.ls_info(path)
    
    # =========================================================================
    # glob_info / aglob_info - deepagents 인터페이스
    # =========================================================================
    
    def glob_info(self, pattern: str, path: str = "/") -> list:
        """glob 패턴 검색 (sync) - deepagents 인터페이스"""
        # 패턴 결합
        if path and path != "/":
            real_base = self._virtual_to_real(path)
            # 패턴이 **로 시작하면 그대로, 아니면 path와 결합
            if pattern.startswith("**"):
                real_pattern = os.path.join(real_base, pattern)
            else:
                real_pattern = os.path.join(real_base, pattern.lstrip("/"))
        else:
            real_pattern = self._virtual_to_real(pattern)
        
        try:
            matches = self._glob_module.glob(real_pattern, recursive=True)
            result = []
            for match in sorted(matches):
                if os.path.isfile(match):  # 파일만 반환
                    result.append(self._get_file_info(match))
            return result
        except Exception as e:
            return []
    
    async def aglob_info(self, pattern: str, path: str = "/") -> list:
        """glob 패턴 검색 (async) - deepagents 인터페이스"""
        return self.glob_info(pattern, path)
    
    # =========================================================================
    # read / aread - deepagents 인터페이스
    # =========================================================================
    
    def read(self, file_path: str, offset: int = 0, limit: int = 10000) -> str:
        """파일 읽기 (sync) - deepagents 인터페이스
        
        Args:
            file_path: 파일 경로
            offset: 시작 라인 (0부터)
            limit: 읽을 라인 수 (기본 10000 - 전체 읽기)
        """
        real_path = self._virtual_to_real(file_path)
        
        try:
            # 다양한 인코딩 시도
            for encoding in ['utf-8', 'utf-8-sig', 'cp949', 'euc-kr', 'latin-1']:
                try:
                    with open(real_path, 'r', encoding=encoding) as f:
                        content = f.read()
                        if offset == 0 and limit >= 10000:
                            return content
                        lines = content.split('\n')
                        selected = lines[offset:offset + limit]
                        return '\n'.join(selected)
                except UnicodeDecodeError:
                    continue
            
            # 바이너리로 읽기 (최후의 수단)
            with open(real_path, 'rb') as f:
                return f.read().decode('utf-8', errors='replace')
        except FileNotFoundError:
            return f"Error: File not found: {file_path}"
        except Exception as e:
            return f"Error reading file: {str(e)}"
    
    async def aread(self, file_path: str, offset: int = 0, limit: int = 10000) -> str:
        """파일 읽기 (async) - deepagents 인터페이스"""
        return self.read(file_path, offset, limit)
    
    # =========================================================================
    # write / awrite - deepagents 인터페이스
    # =========================================================================
    
    def write(self, file_path: str, content: str) -> dict:
        """파일 쓰기 (sync) - deepagents 인터페이스"""
        real_path = self._virtual_to_real(file_path)
        
        try:
            os.makedirs(os.path.dirname(real_path), exist_ok=True)
            with open(real_path, 'w', encoding='utf-8') as f:
                f.write(content)
            return {"path": file_path, "size": len(content)}
        except Exception as e:
            return {"error": str(e)}
    
    async def awrite(self, file_path: str, content: str) -> dict:
        """파일 쓰기 (async) - deepagents 인터페이스"""
        return self.write(file_path, content)
    
    # =========================================================================
    # grep / grep_raw / agrep_raw - deepagents 인터페이스
    # =========================================================================
    
    def grep_raw(self, pattern: str, path: str | None = None, glob: str | None = None) -> list:
        """텍스트 검색 (sync) - deepagents raw 인터페이스
        
        Args:
            pattern: 검색할 정규식 패턴
            path: 검색할 디렉토리/파일 경로 (기본값: "/")
            glob: 파일 필터 패턴 (예: "*.xml", "*.java")
        """
        search_path = path if path is not None else "/"
        real_path = self._virtual_to_real(search_path)
        results = []
        
        try:
            # path가 파일인 경우
            if os.path.isfile(real_path):
                files_to_search = [real_path]
            # path가 디렉토리인 경우
            elif os.path.isdir(real_path):
                # glob 필터가 있으면 적용
                if glob:
                    # glob 패턴을 경로에 맞게 조합
                    glob_pattern = os.path.join(real_path, "**", glob)
                    files_to_search = self._glob_module.glob(glob_pattern, recursive=True)
                else:
                    files_to_search = self._glob_module.glob(os.path.join(real_path, "**", "*"), recursive=True)
                files_to_search = [f for f in files_to_search if os.path.isfile(f)]
            else:
                # glob 패턴으로 해석
                files_to_search = self._glob_module.glob(real_path, recursive=True)
                files_to_search = [f for f in files_to_search if os.path.isfile(f)]
            
            # 각 파일에서 패턴 검색
            for file_path in files_to_search:
                try:
                    with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                        lines = f.readlines()
                        for line_num, line in enumerate(lines, 1):
                            if self._re_module.search(pattern, line, self._re_module.IGNORECASE):
                                virtual_file = self._real_to_virtual(file_path)
                                results.append({
                                    "path": virtual_file,
                                    "line": line_num,  # deepagents 표준 필드명
                                    "text": line.strip()[:200],  # deepagents 표준 필드명
                                })
                except Exception:
                    continue
            
            return results
        except Exception as e:
            return []
    
    async def agrep_raw(self, pattern: str, path: str | None = None, glob: str | None = None) -> list:
        """텍스트 검색 (async) - deepagents raw 인터페이스"""
        return self.grep_raw(pattern, path, glob)
    
    def grep(self, pattern: str, path: str | None = None, glob: str | None = None) -> str:
        """텍스트 검색 (sync) - 포맷된 문자열 반환"""
        results = self.grep_raw(pattern, path, glob)
        if not results:
            search_path = path if path is not None else "/"
            return f"No matches found for pattern '{pattern}' in {search_path}"
        
        lines = []
        for r in results:
            lines.append(f"{r['path']}:{r['line']}: {r['text']}")
        return '\n'.join(lines)
    
    async def agrep(self, pattern: str, path: str | None = None, glob: str | None = None) -> str:
        """텍스트 검색 (async) - 포맷된 문자열 반환"""
        return self.grep(pattern, path, glob)
    
    # =========================================================================
    # exists - deepagents 인터페이스
    # =========================================================================
    
    def exists(self, path: str) -> bool:
        """파일 존재 확인 (sync)"""
        real_path = self._virtual_to_real(path)
        return os.path.exists(real_path)
    
    async def aexists(self, path: str) -> bool:
        """파일 존재 확인 (async)"""
        return self.exists(path)
    
    # =========================================================================
    # 기타 메서드 - 원래 백엔드로 위임
    # =========================================================================
    
    def __getattr__(self, name):
        """구현되지 않은 메서드는 원래 백엔드로 위임"""
        return getattr(self._backend, name)
from langgraph.checkpoint.memory import MemorySaver
from langgraph.store.memory import InMemoryStore
from langchain_core.tools import StructuredTool

# nest_asyncio: sync 컨텍스트에서 async 도구 실행용 (선택적)
try:
    import nest_asyncio
    NEST_ASYNCIO_AVAILABLE = True
except ImportError:
    NEST_ASYNCIO_AVAILABLE = False
    nest_asyncio = None

# 내부 도구들 (Neo4j 작업용)
from ..tools.neo4j_tools import Neo4jTools
from ..tools.neo4j_schema import SchemaManager
from ..parsers.mybatis_parser import MyBatisParser
from ..parsers.ofbiz_parser import OFBizParser
from ..parsers.jpa_parser import JPAParser
from ..parsers.yaml_parser import YAMLParser

# 안전장치 도구들
from ..data.evidence_manager import EvidenceManager, EvidenceData, create_evidence_tool
from ..support.minimal_lock import MinimalLock
from ..support.runtime_policy import RuntimePolicy, POLICY
from ..support.cache_layer import CacheLayer, hints_to_artifact_key
from ..tools.docs_tools import DocsTools, create_docs_tools



# =============================================================================
# 커스텀 도구 정의 (Neo4j 작업용)
# =============================================================================

import json
import uuid


def _run_async_in_sync(coro, json_result: bool = False):
    """비동기 코루틴을 동기 컨텍스트에서 실행하는 헬퍼
    
    Neo4j 비동기 드라이버는 특정 이벤트 루프에 바인딩되므로,
    항상 같은 루프에서 실행해야 합니다.
    
    Args:
        coro: 실행할 코루틴
        json_result: True면 결과를 JSON 문자열로 변환
    
    Returns:
        코루틴 실행 결과 (json_result=True면 JSON 문자열)
    """
    result = None
    
    try:
        # 이미 실행 중인 이벤트 루프가 있는지 확인
        loop = asyncio.get_running_loop()
        
        # 이미 루프가 있으면 nest_asyncio 적용 (필수)
        if NEST_ASYNCIO_AVAILABLE:
            nest_asyncio.apply()
            result = loop.run_until_complete(coro)
        else:
            # nest_asyncio 없으면 에러 - 필수 의존성
            raise RuntimeError(
                "nest_asyncio is required for sync tool execution in async context. "
                "Install with: pip install nest_asyncio"
            )
                
    except RuntimeError as e:
        # 루프가 없거나 루프가 실행 중이 아님
        error_msg = str(e).lower()
        if "no running event loop" in error_msg or "no current event loop" in error_msg:
            # 새 루프 생성 및 설정 후 실행
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            try:
                # nest_asyncio 적용 (새 루프에서도)
                if NEST_ASYNCIO_AVAILABLE:
                    nest_asyncio.apply()
                result = new_loop.run_until_complete(coro)
            finally:
                new_loop.close()
                # 이벤트 루프 정리 (Python 3.12+ 호환)
                try:
                    asyncio.set_event_loop(None)
                except Exception:
                    pass
        elif "nest_asyncio" in str(e):
            # nest_asyncio 관련 에러는 그대로 전파
            raise
        else:
            raise
    
    if json_result:
        if hasattr(result, 'records'):
            return json.dumps(result.records, default=str)
        return json.dumps(result, default=str)
    return result


def create_neo4j_tools(neo4j_driver: Any) -> List:
    """Neo4j 관련 도구 생성
    
    각 도구는 sync/async 버전을 모두 제공하여 deepagents 라이브러리의
    다양한 실행 컨텍스트에서 올바르게 동작합니다.
    """
    tools = Neo4jTools(neo4j_driver)
    
    # run_id 상태 관리
    _current_run_id = {"value": None}
    
    # =========================================================================
    # set_run_id: 현재 run_id 설정 (sync only - 상태만 변경)
    # =========================================================================
    def set_run_id(run_id: str) -> str:
        """현재 run_id 설정
        
        Args:
            run_id: 실행 ID
        
        Returns:
            설정된 run_id
        """
        _current_run_id["value"] = run_id
        return f"run_id set to: {run_id}"
    
    # =========================================================================
    # query_neo4j: Neo4j 쿼리 실행 (async only - Neo4j 드라이버는 이벤트 루프에 바인딩됨)
    # =========================================================================
    async def _query_neo4j_async(cypher_query: str, params: Optional[str] = None) -> str:
        """Neo4j Cypher 쿼리 실행 (비동기 전용)"""
        params_dict = json.loads(params) if params else {}
        result = await tools.query(cypher_query, params_dict)
        return json.dumps(result.records, default=str)
    
    query_neo4j_tool = StructuredTool.from_function(
        name="query_neo4j",
        description="Neo4j Cypher 쿼리 실행 (읽기 전용). cypher_query: Cypher 쿼리, params: JSON 파라미터 (선택)",
        func=None,  # 비동기 전용 - Neo4j 드라이버가 이벤트 루프에 바인딩되므로
        coroutine=_query_neo4j_async,
    )
    
    # =========================================================================
    # search_nodes_by_keyword: 키워드로 노드 검색 (async only)
    # =========================================================================
    async def _search_nodes_by_keyword_async(
        keyword: str,
        labels: Optional[str] = None,
        limit: int = 50
    ) -> str:
        """키워드로 노드 검색 (대소문자 무시) (비동기 전용)"""
        labels_list = json.loads(labels) if labels else None
        result = await tools.search_nodes_by_keyword(keyword, labels_list, limit)
        return json.dumps(result.records, default=str)
    
    search_nodes_tool = StructuredTool.from_function(
        name="search_nodes_by_keyword",
        description="키워드로 노드 검색 (node_code, name 필드에서 대소문자 무시 검색). keyword: 검색어, labels: JSON 라벨 배열 (선택), limit: 최대 결과 수 (기본 50)",
        func=None,
        coroutine=_search_nodes_by_keyword_async,
    )
    
    # =========================================================================
    # get_graph_schema: 그래프 스키마 조회 (async only)
    # =========================================================================
    async def _get_graph_schema_async() -> str:
        """그래프 스키마 조회 (라벨별 속성 목록) (비동기 전용)"""
        result = await tools.get_graph_schema()
        return json.dumps(result, default=str)
    
    get_graph_schema_tool = StructuredTool.from_function(
        name="get_graph_schema",
        description="그래프 스키마 조회 - 라벨별 속성 목록과 관계 타입 반환",
        func=None,
        coroutine=_get_graph_schema_async,
    )
    
    # =========================================================================
    # find_imports_in_file: 파일의 IMPORT 노드 조회 (async only)
    # =========================================================================
    async def _find_imports_in_file_async(directory: str) -> str:
        """특정 파일의 IMPORT 노드 조회 (비동기 전용)"""
        result = await tools.find_imports_in_file(directory)
        return json.dumps(result.records, default=str)
    
    find_imports_tool = StructuredTool.from_function(
        name="find_imports_in_file",
        description="특정 파일의 IMPORT 노드 조회. directory: 파일 경로 (예: 'SAMPLE/OrderService.java')",
        func=None,
        coroutine=_find_imports_in_file_async,
    )
    
    # =========================================================================
    # find_nodes_by_directory: 경로 패턴으로 노드 검색 (async only)
    # =========================================================================
    async def _find_nodes_by_directory_async(
        path_pattern: str,
        labels: Optional[str] = None,
        limit: int = 100
    ) -> str:
        """경로 패턴으로 노드 검색 (비동기 전용)"""
        labels_list = json.loads(labels) if labels else None
        result = await tools.find_nodes_by_directory(path_pattern, labels_list, limit)
        return json.dumps(result.records, default=str)
    
    find_by_directory_tool = StructuredTool.from_function(
        name="find_nodes_by_directory",
        description="경로 패턴으로 노드 검색. path_pattern: 경로 패턴 (예: 'SAMPLE/', '*.java'), labels: JSON 라벨 배열 (선택), limit: 최대 결과 수 (기본 100)",
        func=None,
        coroutine=_find_nodes_by_directory_async,
    )
    
    # =========================================================================
    # get_node_children: 부모 노드의 자식 노드 조회 (async only)
    # =========================================================================
    async def _get_node_children_async(parent_element_id: str) -> str:
        """부모 노드의 자식 노드 조회 (startLine/endLine 기반) (비동기 전용)"""
        result = await tools.get_node_children(parent_element_id)
        return json.dumps(result.records, default=str)
    
    get_children_tool = StructuredTool.from_function(
        name="get_node_children",
        description="부모 노드의 자식 노드 조회 (startLine/endLine 범위 내 노드들). parent_element_id: 부모 노드의 elementId",
        func=None,
        coroutine=_get_node_children_async,
    )
    
    # =========================================================================
    # check_table_exists: Table 노드 존재 여부 확인 (async only)
    # =========================================================================
    async def _check_table_exists_async(table_name: str) -> str:
        """Table 노드 존재 여부 확인 (비동기 전용)"""
        exists = await tools.check_table_exists(table_name)
        return json.dumps({"table_name": table_name, "exists": exists})
    
    check_table_exists_tool = StructuredTool.from_function(
        name="check_table_exists",
        description="Table 노드 존재 여부 확인. table_name: 테이블명",
        func=None,
        coroutine=_check_table_exists_async,
    )
    
    # =========================================================================
    # enrich_table_schema: DDL 있을 때 테이블 정보 보강 (async only)
    # =========================================================================
    async def _enrich_table_schema_async(
        table_name: str,
        columns: Optional[str] = None,
        fk_relationships: Optional[str] = None,
        source_info: Optional[str] = None
    ) -> str:
        """DDL 있는 경우 테이블 정보 보강 (비동기 전용)"""
        columns_list = json.loads(columns) if columns else None
        fk_list = json.loads(fk_relationships) if fk_relationships else None
        source_dict = json.loads(source_info) if source_info else None
        result = await tools.enrich_table_schema(table_name, columns_list, fk_list, source_dict)
        return json.dumps(result, default=str)
    
    enrich_table_tool = StructuredTool.from_function(
        name="enrich_table_schema",
        description="DDL이 있는 경우 테이블 정보 보강 (컬럼 추가/업데이트, FK 관계 생성). table_name: 테이블명, columns: JSON 컬럼 배열 (선택), fk_relationships: JSON FK 배열 (선택), source_info: JSON 출처 정보 (선택)",
        func=None,
        coroutine=_enrich_table_schema_async,
    )
    
    # =========================================================================
    # create_table_schema: DDL 없을 때 테이블 전체 생성 (async only)
    # =========================================================================
    async def _create_table_schema_async(
        table_name: str,
        columns: str,
        fk_relationships: Optional[str] = None,
        source_info: Optional[str] = None
    ) -> str:
        """DDL 없는 경우 테이블 스키마 전체 생성 (비동기 전용)"""
        try:
            columns_list = json.loads(columns)
        except json.JSONDecodeError as e:
            return json.dumps({"error": f"columns JSON 파싱 실패: {e}. 전달된 값: {columns[:200]}..."})
        
        try:
            fk_list = json.loads(fk_relationships) if fk_relationships else None
        except json.JSONDecodeError as e:
            return json.dumps({"error": f"fk_relationships JSON 파싱 실패: {e}. 전달된 값: {fk_relationships[:200] if fk_relationships else None}..."})
        
        try:
            source_dict = json.loads(source_info) if source_info else None
        except json.JSONDecodeError as e:
            return json.dumps({"error": f"source_info JSON 파싱 실패: {e}"})
        
        result = await tools.create_table_schema(table_name, columns_list, fk_list, source_dict)
        return json.dumps(result, default=str)
    
    create_table_tool = StructuredTool.from_function(
        name="create_table_schema",
        description="DDL이 없는 경우 테이블 스키마 전체 생성 (Table 노드, Column 노드, FK 관계). table_name: 테이블명, columns: JSON 컬럼 배열 (필수), fk_relationships: JSON FK 배열 (선택), source_info: JSON 출처 정보 (선택)",
        func=None,
        coroutine=_create_table_schema_async,
    )
    
    # =========================================================================
    # add_fk_relationship: FK 관계 추가 (async only)
    # =========================================================================
    async def _add_fk_relationship_async(
        from_table: str,
        to_table: str,
        fk_column: str,
        pk_column: str,
        source_info: Optional[str] = None
    ) -> str:
        """FK 관계 추가 (비동기 전용)"""
        source_dict = json.loads(source_info) if source_info else None
        result = await tools.add_fk_relationship(from_table, to_table, fk_column, pk_column, source_dict)
        return json.dumps(result, default=str)
    
    add_fk_tool = StructuredTool.from_function(
        name="add_fk_relationship",
        description="FK 관계 추가. from_table: 소스 테이블명, to_table: 타겟 테이블명, fk_column: FK 컬럼명, pk_column: PK 컬럼명, source_info: JSON 출처 정보 (선택)",
        func=None,
        coroutine=_add_fk_relationship_async,
    )
    
    # =========================================================================
    # set_status 함수들 (플랜 기반)
    # =========================================================================
    async def _set_status_success_async(element_id: str, evidence_path: str) -> str:
        """성공 상태로 업데이트"""
        result = await tools.set_status_success(element_id, evidence_path)
        return json.dumps(result, default=str)
    
    set_status_success_tool = StructuredTool.from_function(
        name="set_status_success",
        description="노드를 성공 상태로 업데이트. element_id: 노드 ID, evidence_path: 증거 파일 경로",
        func=None,
        coroutine=_set_status_success_async,
    )
    
    async def _set_status_review_async(element_id: str, reason: str, evidence_path: str) -> str:
        """리뷰 상태로 업데이트"""
        result = await tools.set_status_review(element_id, reason, evidence_path)
        return json.dumps(result, default=str)
    
    set_status_review_tool = StructuredTool.from_function(
        name="set_status_review",
        description="노드를 리뷰 상태로 업데이트. element_id: 노드 ID, reason: 사유, evidence_path: 증거 파일 경로",
        func=None,
        coroutine=_set_status_review_async,
    )
    
    async def _set_status_failed_async(element_id: str, reason: str) -> str:
        """실패 상태로 업데이트"""
        result = await tools.set_status_failed(element_id, reason)
        return json.dumps(result, default=str)
    
    set_status_failed_tool = StructuredTool.from_function(
        name="set_status_failed",
        description="노드를 실패 상태로 업데이트. element_id: 노드 ID, reason: 사유",
        func=None,
        coroutine=_set_status_failed_async,
    )
    
    # =========================================================================
    # apply_graph_mutations (플랜 기반 추가)
    # =========================================================================
    async def _apply_graph_mutations_async(mutations: str) -> str:
        """트랜잭션 배치로 그래프 변경 적용"""
        mutations_list = json.loads(mutations)
        result = await tools.apply_graph_mutations(mutations_list)
        return json.dumps(result, default=str)
    
    apply_mutations_tool = StructuredTool.from_function(
        name="apply_graph_mutations",
        description="트랜잭션 배치로 그래프 변경. mutations: JSON 배열 [{type, ...}]",
        func=None,
        coroutine=_apply_graph_mutations_async,
    )
    
    # =========================================================================
    # get_resolution_candidates (플랜 기반 추가)
    # =========================================================================
    async def _get_candidates_async(limit: int = 500) -> str:
        """후보 수집"""
        result = await tools.get_resolution_candidates(limit)
        return json.dumps(result.records, default=str)
    
    get_candidates_tool = StructuredTool.from_function(
        name="get_resolution_candidates",
        description="후보 수집. limit: 최대 결과 수",
        func=None,
        coroutine=_get_candidates_async,
    )
    
    # =========================================================================
    # 청크 단위 FROM/WRITES 연결 도구 (신규)
    # =========================================================================
    async def _link_candidates_to_tables_async(
        candidate_element_ids: str,
        table_names: str,
        operation: str = "read"
    ) -> str:
        """청크 내 후보노드들 → 테이블들 일괄 MERGE 연결
        
        Args:
            candidate_element_ids: JSON 배열 - 후보노드 element_id 목록
            table_names: JSON 배열 - 테이블명 목록
            operation: "read" → FROM, "write" → WRITES
        
        Returns:
            {"success": bool, "from_count": int, "writes_count": int}
        """
        try:
            eids = json.loads(candidate_element_ids)
        except json.JSONDecodeError as e:
            return json.dumps({"error": f"candidate_element_ids JSON 파싱 실패: {e}"})
        
        try:
            tables = json.loads(table_names)
        except json.JSONDecodeError as e:
            return json.dumps({"error": f"table_names JSON 파싱 실패: {e}"})
        
        result = await tools.link_candidates_to_tables(eids, tables, operation)
        return json.dumps(result, default=str)
    
    link_candidates_tool = StructuredTool.from_function(
        name="link_candidates_to_tables",
        description="청크 후보노드들과 테이블들 일괄 MERGE 연결. candidate_element_ids: JSON 배열, table_names: JSON 배열, operation: read/write",
        func=None,
        coroutine=_link_candidates_to_tables_async,
    )
    
    # =========================================================================
    # Description 업데이트 도구 (LLM 통합 후)
    # =========================================================================
    async def _update_table_description_async(
        table_name: str,
        description: str,
        description_source: str = "runtime_resolution"
    ) -> str:
        """테이블 description 덮어쓰기 (LLM 통합 후 호출)"""
        result = await tools.update_table_description(table_name, description, description_source)
        return json.dumps(result, default=str)
    
    update_table_desc_tool = StructuredTool.from_function(
        name="update_table_description",
        description="테이블 description 덮어쓰기. table_name, description, description_source",
        func=None,
        coroutine=_update_table_description_async,
    )
    
    async def _update_column_description_async(
        table_name: str,
        column_name: str,
        description: str,
        description_source: str = "runtime_resolution"
    ) -> str:
        """컬럼 description 덮어쓰기 (LLM 통합 후 호출)"""
        result = await tools.update_column_description(table_name, column_name, description, description_source)
        return json.dumps(result, default=str)
    
    update_column_desc_tool = StructuredTool.from_function(
        name="update_column_description",
        description="컬럼 description 덮어쓰기. table_name, column_name, description, description_source",
        func=None,
        coroutine=_update_column_description_async,
    )
    
    async def _get_table_description_async(table_name: str) -> str:
        """테이블 기존 description 조회"""
        desc = await tools.get_table_description(table_name)
        return json.dumps({"table_name": table_name, "description": desc or ""})
    
    get_table_desc_tool = StructuredTool.from_function(
        name="get_table_description",
        description="테이블 기존 description 조회. table_name",
        func=None,
        coroutine=_get_table_description_async,
    )
    
    # =========================================================================
    # LLM 기반 Description 통합 도구
    # =========================================================================
    async def _merge_table_description_async(
        table_name: str,
        new_findings: str,
        source: str = "runtime_resolution"
    ) -> str:
        """테이블 description LLM 통합
        
        기존 description + 새 발견 내용 → LLM으로 통합된 새 description 생성
        
        Args:
            table_name: 테이블명
            new_findings: 새로 발견된 정보 (JSON 배열 또는 문자열)
            source: 출처 정보
        
        Returns:
            {"success": bool, "description": str, "merged": bool}
        """
        from langchain_openai import ChatOpenAI
        
        # 기존 description 조회
        existing = await tools.get_table_description(table_name) or ""
        
        # new_findings 파싱
        try:
            findings_list = json.loads(new_findings) if new_findings.startswith('[') else [new_findings]
        except:
            findings_list = [new_findings]
        
        # 새 발견 내용이 없으면 기존 유지
        if not findings_list or all(not f.strip() for f in findings_list):
            return json.dumps({
                "success": True,
                "table_name": table_name,
                "description": existing,
                "merged": False,
                "reason": "no_new_findings"
            })
        
        # LLM으로 통합
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
        
        prompt = f"""당신은 데이터베이스 테이블 설명 전문가입니다.
아래 정보를 바탕으로 '{table_name}' 테이블이 무엇인지 **한 문단**으로 정리하세요.

규칙:
1. 중복 내용 제거
2. 핵심 기능/역할만 포함
3. 관련 엔티티/서비스 언급 가능
4. 최대 3문장

기존 설명: {existing if existing else "(없음)"}

새로 발견된 정보:
{chr(10).join(f"- {f}" for f in findings_list)}

통합된 설명:"""
        
        try:
            result = await llm.ainvoke(prompt)
            merged_desc = result.content.strip()
            
            # Neo4j 업데이트
            await tools.update_table_description(table_name, merged_desc, source)
            
            return json.dumps({
                "success": True,
                "table_name": table_name,
                "description": merged_desc,
                "merged": True,
                "previous": existing[:100] + "..." if len(existing) > 100 else existing
            })
        except Exception as e:
            return json.dumps({
                "success": False,
                "error": str(e),
                "table_name": table_name
            })
    
    async def _merge_column_description_async(
        table_name: str,
        column_name: str,
        new_findings: str,
        source: str = "runtime_resolution"
    ) -> str:
        """컬럼 description LLM 통합
        
        Args:
            table_name: 테이블명
            column_name: 컬럼명
            new_findings: 새로 발견된 정보
            source: 출처 정보
        """
        from langchain_openai import ChatOpenAI
        
        # 기존 description 조회
        existing = await tools.get_column_description(table_name, column_name) or ""
        
        # new_findings 파싱
        try:
            findings_list = json.loads(new_findings) if new_findings.startswith('[') else [new_findings]
        except:
            findings_list = [new_findings]
        
        if not findings_list or all(not f.strip() for f in findings_list):
            return json.dumps({
                "success": True,
                "column": f"{table_name}.{column_name}",
                "description": existing,
                "merged": False
            })
        
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
        
        prompt = f"""'{table_name}.{column_name}' 컬럼이 무엇인지 **한 문장**으로 정리하세요.

기존: {existing if existing else "(없음)"}
새 정보: {', '.join(findings_list)}

설명:"""
        
        try:
            result = await llm.ainvoke(prompt)
            merged_desc = result.content.strip()
            
            await tools.update_column_description(table_name, column_name, merged_desc, source)
            
            return json.dumps({
                "success": True,
                "column": f"{table_name}.{column_name}",
                "description": merged_desc,
                "merged": True
            })
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)})
    
    MERGE_TABLE_DESC = """🔄 테이블 description LLM 통합 (중요!)

═══════════════════════════════════════════════════════════════════════════════
📌 용도: 기존 description + 새 발견 내용 → 통합된 설명 생성
═══════════════════════════════════════════════════════════════════════════════

테이블 정보 발견 후 반드시 호출! 단순 누적이 아닌 LLM 통합.

📥 입력:
- table_name: 테이블명 (예: "ORDER_HEADER")
- new_findings: 새 발견 내용 (JSON 배열 또는 문자열)
  예: '["OFBiz OrderHeader 엔티티", "주문 정보 저장"]'
- source: 출처 (기본: runtime_resolution)

📤 반환:
{"success": true, "description": "통합된 설명", "merged": true}

예시:
기존: "주문 테이블"
새 발견: ["OFBiz OrderHeader 엔티티와 매핑", "주문 ID, 상태 관리"]
→ "주문 정보를 관리하는 핵심 테이블. OFBiz OrderHeader 엔티티와 매핑되며, 주문 ID와 상태를 저장한다."
"""
    
    MERGE_COLUMN_DESC = """🔄 컬럼 description LLM 통합

table_name, column_name, new_findings를 전달하면 LLM이 기존+신규 통합."""
    
    merge_table_desc_tool = StructuredTool.from_function(
        name="merge_table_description",
        description=MERGE_TABLE_DESC,
        func=None,
        coroutine=_merge_table_description_async,
    )
    
    merge_column_desc_tool = StructuredTool.from_function(
        name="merge_column_description",
        description=MERGE_COLUMN_DESC,
        func=None,
        coroutine=_merge_column_description_async,
    )
    
    return [
        set_run_id, 
        query_neo4j_tool, 
        # 그래프 검색 도구
        search_nodes_tool,
        get_graph_schema_tool,
        find_imports_tool,
        find_by_directory_tool,
        get_children_tool,
        # DDL/스키마 관리 도구
        check_table_exists_tool,
        enrich_table_tool,
        create_table_tool,
        add_fk_tool,
        # 상태 관리 도구 (플랜 기반)
        set_status_success_tool,
        set_status_review_tool,
        set_status_failed_tool,
        # 배치 도구 (플랜 기반)
        apply_mutations_tool,
        get_candidates_tool,
        # 청크 단위 FROM/WRITES 연결 (신규)
        link_candidates_tool,
        # Description 업데이트 (신규)
        update_table_desc_tool,
        update_column_desc_tool,
        get_table_desc_tool,
        # Description LLM 통합 (핵심!)
        merge_table_desc_tool,
        merge_column_desc_tool,
    ]


def create_parser_tools() -> List:
    """파서 도구 생성 (XML, YAML 파싱)
    
    🚨 중복 호출 방지: content hash 기반 캐싱
    """
    mybatis_parser = MyBatisParser()
    ofbiz_parser = OFBizParser()
    jpa_parser = JPAParser()
    yaml_parser = YAMLParser()
    
    # 세션 내 중복 호출 방지 캐시
    _parse_cache: Dict[str, str] = {}
    
    def _content_hash(content: str, extra: str = "") -> str:
        """content + extra의 해시 생성"""
        import hashlib
        return hashlib.md5((content + extra).encode()).hexdigest()[:16]
    
    def parse_mybatis_mapper(xml_content: str) -> str:
        """MyBatis Mapper XML 파싱하여 SQL 및 테이블 추출
        
        Args:
            xml_content: MyBatis Mapper XML 내용
        
        Returns:
            파싱 결과 (namespace, queries, tables) - JSON 문자열
        """
        cache_key = f"mybatis:{_content_hash(xml_content)}"
        if cache_key in _parse_cache:
            return _parse_cache[cache_key]
        
        result = mybatis_parser.parse_mapper("unknown", xml_content)
        output = json.dumps(result.to_dict(), ensure_ascii=False)
        _parse_cache[cache_key] = output
        return output
    
    def parse_ofbiz_entity(xml_content: str) -> str:
        """OFBiz Entity XML 파싱하여 엔티티-테이블 매핑 추출
        
        Args:
            xml_content: OFBiz entitydef XML 내용
        
        Returns:
            파싱 결과 (entity-name, table-name, fields) - JSON 문자열
        """
        cache_key = f"ofbiz_entity:{_content_hash(xml_content)}"
        if cache_key in _parse_cache:
            return _parse_cache[cache_key]
        
        result = ofbiz_parser.parse_entitydef("unknown", xml_content)
        output = json.dumps(result.to_dict(), ensure_ascii=False)
        _parse_cache[cache_key] = output
        return output
    
    def parse_ofbiz_service(xml_content: str) -> str:
        """OFBiz Service XML 파싱하여 서비스 정의 추출
        
        Args:
            xml_content: OFBiz servicedef XML 내용
        
        Returns:
            파싱 결과 (service-name, engine, entity references) - JSON 문자열
        """
        cache_key = f"ofbiz_service:{_content_hash(xml_content)}"
        if cache_key in _parse_cache:
            return _parse_cache[cache_key]
        
        result = ofbiz_parser.parse_servicedef("unknown", xml_content)
        output = json.dumps(result.to_dict(), ensure_ascii=False)
        _parse_cache[cache_key] = output
        return output
    
    def parse_yaml_config(yaml_content: str, key_path: str) -> str:
        """YAML 설정 파일 파싱
        
        Args:
            yaml_content: YAML 파일 내용
            key_path: 추출할 키 경로 (예: "spring.datasource.url")
        
        Returns:
            파싱 결과 - key_path가 있으면 해당 값, 없으면 전체 결과 JSON 문자열
        """
        cache_key = f"yaml:{_content_hash(yaml_content, key_path)}"
        if cache_key in _parse_cache:
            return _parse_cache[cache_key]
        
        result = yaml_parser.parse("unknown", yaml_content)
        if key_path:
            value = result.get(key_path)
            output = json.dumps({"key": key_path, "value": value}, ensure_ascii=False)
        else:
            output = json.dumps(result.to_dict(), ensure_ascii=False)
        
        _parse_cache[cache_key] = output
        return output
    
    def normalize_table_name(table_name: str) -> str:
        """테이블명 정규화
        
        Args:
            table_name: 원본 테이블명
        
        Returns:
            정규화된 테이블명 (대문자, 따옴표 제거, 스키마 분리)
        """
        # 따옴표 제거
        name = table_name.strip().strip('"').strip("'").strip('`')
        
        # 대문자 변환
        name = name.upper()
        
        # 스키마.테이블 형태면 테이블만 반환
        if '.' in name:
            parts = name.split('.')
            return parts[-1]
        
        return name
    
    return [
        parse_mybatis_mapper, 
        parse_ofbiz_entity, 
        parse_ofbiz_service, 
        parse_yaml_config,
        normalize_table_name
    ]


def create_safety_tools(
    neo4j_driver: Any,
    runs_root: str,
    ddl_root: str,
    docs_root: str = "",
) -> tuple:
    """안전장치 도구 생성 (플랜 기반 업데이트)
    
    플랜 규칙:
    - ReviewQueue 노드 생성 제거 (속성으로 관리)
    - MinimalLock으로 대체
    - RuntimePolicy 적용
    - docs_tools 추가
    - todo는 deepagents built-in write_todos 사용
    
    Args:
        neo4j_driver: Neo4j 드라이버
        runs_root: 작업 워크스페이스 루트
        ddl_root: DDL 파일 루트
        docs_root: docs 폴더 루트
    
    Returns:
        (evidence_tools, lock_tools, ddl_tools, docs_tools_list, cache_tools, safety_instances)
    """
    # 인스턴스 생성 (run_id는 나중에 설정)
    evidence_mgr = EvidenceManager(runs_root, run_id="")
    minimal_lock = MinimalLock()
    # 싱글턴 패턴: 동일 경로면 같은 인스턴스 (메모리 캐시 공유!)
    cache_layer = CacheLayer.get_instance(os.path.join(runs_root, "cache"))
    schema_mgr = SchemaManager(neo4j_driver, ddl_root)
    
    # Evidence 도구
    evidence_tools = create_evidence_tool(evidence_mgr)
    
    # MinimalLock 도구 (간소화)
    def acquire_lock(key: str, owner_id: str = "") -> str:
        """락 획득 (동기 버전 - 실제로는 비동기 필요)"""
        return f"Lock requested for {key}"
    
    def release_lock(key: str) -> str:
        """락 해제"""
        minimal_lock.release(key)
        return f"Lock released for {key}"
    
    lock_tools = [acquire_lock, release_lock]
    
    # DDL 체크 도구 (async only - Neo4j 드라이버 사용)
    async def _check_ddl_exists_async(table_name: str) -> str:
        """DDL에 테이블이 존재하는지 확인 (비동기 전용)"""
        exists = await schema_mgr.check_table_exists(table_name)
        
        if exists:
            return f"Table {table_name} exists in DDL"
        else:
            return f"Table {table_name} NOT found in DDL"
    
    check_ddl_exists_tool = StructuredTool.from_function(
        name="check_ddl_exists",
        description="DDL에 테이블이 존재하는지 확인. table_name: 테이블명",
        func=None,  # 비동기 전용
        coroutine=_check_ddl_exists_async,
    )
    
    ddl_tools = [check_ddl_exists_tool]
    
    # Docs 도구 (scope 제한 + 바이트 offset)
    docs_tools_list = []
    if docs_root:
        docs_tools_list = create_docs_tools(docs_root)
    
    # =========================================================================
    # 캐시 도구 (2단계 키 시스템: exact → fuzzy)
    # =========================================================================
    
    # hints_to_artifact_key 함수 import
    from ..support.cache_layer import hints_to_artifact_key
    
    def cache_get(hints_json: str, missing_bindings_json: str = "[]", search_tokens_json: str = "[]") -> str:
        """
        🔍 캐시에서 아티팩트 조회 (유사도 기반 매칭)
        
        ═══════════════════════════════════════════════════════════════════════
        📌 핵심: 단어가 "포함"되거나 "유사"하면 매칭됩니다!
        ═══════════════════════════════════════════════════════════════════════
        
        매칭 방식:
        1. 정확 키 매칭 (exact)
        2. 유사도 기반 매칭 (fuzzy) - 단어 포함/유사 시 후보로 반환
        
        예: search_tokens=["order", "header"] 검색 시
            → "ORDER_HEADER", "orderheader", "OrderHeader" 모두 매칭!
        
        ✅ 올바른 사용법:
        ────────────────────
        cache_get('["entity=OrderHeader"]', '["OrderHeader"]', '["order", "header"]')
        cache_get('["service=createOrder"]', '[]', '["order", "create"]')
        
        ═══════════════════════════════════════════════════════════════════════
        📥 입력 파라미터
        ═══════════════════════════════════════════════════════════════════════
        
        hints_json: (필수) 외부 힌트 리스트 JSON 문자열
        ───────────────────────────────────────────────────────────────────
        │ 힌트 타입      │ 형식                      │ 예시                              │
        ├────────────────┼───────────────────────────┼───────────────────────────────────┤
        │ entity         │ entity=X                  │ ["entity=OrderHeader"]            │
        ├────────────────┼───────────────────────────┼───────────────────────────────────┤
        │ service        │ service=X                 │ ["service=createOrderHeader"]     │
        ├────────────────┼───────────────────────────┼───────────────────────────────────┤
        │ queryId        │ queryId=X                 │ ["queryId=selectOrderById"]       │
        ├────────────────┼───────────────────────────┼───────────────────────────────────┤
        │ configKey      │ configKey=X               │ ["configKey=spring.datasource.url"]│
        ├────────────────┼───────────────────────────┼───────────────────────────────────┤
        │ symbol         │ symbol=X                  │ ["symbol=orderService"]           │
        └────────────────┴───────────────────────────┴───────────────────────────────────┘
        
        missing_bindings_json: (선택) 미해결 바인딩 리스트 JSON 문자열
        ───────────────────────────────────────────────────────────────────
        유사도 매칭 시 추가 검색어로 사용
        예: '["OrderHeader", "orderId"]'
        
        search_tokens_json: (선택) 비즈니스 단어 토큰 리스트 JSON 문자열
        ───────────────────────────────────────────────────────────────────
        Analysis 단계에서 추출된 의미있는 단어들
        유사도 매칭의 핵심 검색어로 사용!
        예: '["order", "header", "status"]'
        
        ═══════════════════════════════════════════════════════════════════════
        📤 반환값 (JSON 문자열)
        ═══════════════════════════════════════════════════════════════════════
        
        EXACT HIT (정확 일치):
        ───────────────────────────────────────────────────────────────────
        {
            "hit": true,
            "match": "exact:memory",     // 또는 "exact:file"
            "score": 100.0,
            "artifact_key": "mybatis:ordermapper#createorder",
            "data": {
                "tables": [{"name": "ORDER_HEADER", "op": "write", "confidence": 0.9}],
                "columns": [...],
                "confidence": 0.9
            }
        }
        
        FUZZY HIT (유사 일치):
        ───────────────────────────────────────────────────────────────────
        {
            "hit": true,
            "match": "fuzzy:matched_tags=[token:orderheader, table:order_header]",
            "score": 75.0,              // 점수 (높을수록 신뢰도 높음)
            "artifact_key": "mybatis:ordermapper#createorder",
            "data": {...}
        }
        
        MISS (없음):
        ───────────────────────────────────────────────────────────────────
        {
            "hit": false,
            "match": "miss",
            "score": 0
        }
        
        ═══════════════════════════════════════════════════════════════════════
        📊 Fuzzy 점수 해석 가이드
        ═══════════════════════════════════════════════════════════════════════
        
        │ score │ 신뢰도 │ 권장 행동                              │
        ├───────┼────────┼────────────────────────────────────────┤
        │ ≥ 80  │ 높음   │ 캐시 data 그대로 사용                  │
        │ 50~79 │ 중간   │ 캐시 data 참고, 추가 검증 권장         │
        │ < 50  │ 낮음   │ docs 탐색 진행                         │
        └───────┴────────┴────────────────────────────────────────┘
        
        Args:
            hints_json: 외부 힌트 리스트 JSON 문자열
            missing_bindings_json: 미해결 바인딩 리스트 JSON 문자열 (선택)
            search_tokens_json: 비즈니스 단어 토큰 리스트 JSON 문자열 (선택)
        
        Returns:
            조회 결과 JSON 문자열
        """
        import json
        import logging
        logger = logging.getLogger("deepagents.cache")
        
        try:
            hints = json.loads(hints_json) if hints_json else []
            missing_bindings = json.loads(missing_bindings_json) if missing_bindings_json else []
            search_tokens = json.loads(search_tokens_json) if search_tokens_json else []
        except json.JSONDecodeError as e:
            logger.error(f"⚠️ CACHE_GET JSON 파싱 오류: {str(e)}")
            return json.dumps({"hit": False, "match": "error:json_parse", "score": 0, "error": str(e)})
        
        if not hints and not search_tokens:
            logger.warning("⚠️ CACHE_GET: hints와 search_tokens 모두 비어있음")
            return json.dumps({"hit": False, "match": "error:empty_hints", "score": 0})
        
        # 유사도 기반 조회: exact → fuzzy (similarity-based)
        entry, match_type, score = cache_layer.get_with_fallback(
            hints=hints,
            missing_bindings=missing_bindings,
            search_tokens=search_tokens
        )
        
        if entry:
            tables = entry.data.get("tables", [])
            table_names = [t.get("name", "?") for t in tables if isinstance(t, dict)][:3]
            logger.info(f"✅ CACHE_GET HIT ({match_type}, score={score:.1f}): {entry.artifact_key} → tables={table_names}")
            
            return json.dumps({
                "hit": True,
                "match": match_type,
                "score": score,
                "artifact_key": entry.artifact_key,
                "is_provisional": entry.is_provisional,
                "data": entry.data
            }, ensure_ascii=False)
        
        # 캐시 미스
        artifact_key = hints_to_artifact_key(hints)
        logger.info(f"❌ CACHE_GET MISS: hints={hints[:3]} → generated_key={artifact_key}")
        return json.dumps({"hit": False, "match": "miss", "score": 0})
    
    def cache_set(hints_json: str, data_json: str) -> str:
        """
        💾 캐시에 아티팩트 저장 (태그 인덱스 자동 생성)
        
        ═══════════════════════════════════════════════════════════════════════
        📌 핵심 원칙: hints를 그대로 전달하세요. 키 생성은 시스템이 처리합니다!
        ═══════════════════════════════════════════════════════════════════════
        
        🚨 절대 하지 말 것:
        ────────────────────
        ❌ cache_set('mybatis:ordermapper#createorder', data)  ← 키를 직접 생성
        
        ✅ 올바른 사용법:
        ────────────────────
        cache_set(
            '["namespace=OrderMapper", "queryId=createOrder"]',
            '{"tables": [{"name": "ORDER_HEADER", "op": "write", "confidence": 0.9}], "confidence": 0.9}'
        )
        
        ═══════════════════════════════════════════════════════════════════════
        📥 입력 파라미터
        ═══════════════════════════════════════════════════════════════════════
        
        hints_json: (필수) 외부 힌트 리스트 JSON 문자열
        ───────────────────────────────────────────────────────────────────
        cache_get과 동일한 형식
        
        data_json: (필수) 저장할 데이터 JSON 문자열
        ───────────────────────────────────────────────────────────────────
        
        필수 필드:
        {
            "tables": [
                {
                    "name": "ORDER_HEADER",      // 테이블명 (필수, 대문자 권장)
                    "op": "write",               // "read" | "write" (필수)
                    "confidence": 0.9,           // 0.0~1.0 (필수)
                    "columns": ["ORDER_ID", "CUSTOMER_ID"]  // 선택
                }
            ],
            "confidence": 0.9                    // 전체 신뢰도 (필수)
        }
        
        선택 필드:
        {
            "columns": [...],                    // 테이블 외부 컬럼 정보
            "fks": [...],                        // FK 관계
            "artifact_meta": {                   // 메타데이터
                "entity_name": "OrderHeader",
                "service_name": "OrderService"
            }
        }
        
        ═══════════════════════════════════════════════════════════════════════
        📤 반환값
        ═══════════════════════════════════════════════════════════════════════
        
        성공:
        ───────────────────────────────────────────────────────────────────
        "Cached: mybatis:ordermapper#createorder (tags=12, tables=[ORDER_HEADER])"
        
        실패:
        ───────────────────────────────────────────────────────────────────
        "Cache error: JSON 파싱 오류"
        
        ═══════════════════════════════════════════════════════════════════════
        📊 자동 처리 기능
        ═══════════════════════════════════════════════════════════════════════
        
        1. 키 자동 생성: hints에서 정규화된 artifact_key 생성
        2. 태그 자동 생성: 테이블명, 컬럼명, 힌트에서 검색용 태그 추출
        3. 임시키 판별: mybatis:unknown#... 형태는 is_provisional=true
        4. 점수 비교: 기존 캐시보다 품질이 낮으면 덮어쓰기 안 함
        
        Args:
            hints_json: 외부 힌트 리스트 JSON 문자열
            data_json: 저장할 데이터 JSON 문자열
        
        Returns:
            저장 결과 문자열
        """
        import json
        import logging
        logger = logging.getLogger("deepagents.cache")
        
        try:
            hints = json.loads(hints_json) if hints_json else []
            data = json.loads(data_json) if data_json else {}
        except json.JSONDecodeError as e:
            logger.error(f"⚠️ CACHE_SET JSON 파싱 오류: {str(e)}")
            return f"Cache error: JSON 파싱 오류 - {str(e)}"
        
        if not hints:
            logger.warning("⚠️ CACHE_SET: hints가 비어있음")
            return "Cache error: hints가 비어있음"
        
        # 키 생성
        artifact_key = hints_to_artifact_key(hints)
        
        try:
            cache_layer.set(artifact_key, data, hints=hints)
            
            # 로깅
            tables = data.get("tables", [])
            table_names = [t.get("name", "?") for t in tables if isinstance(t, dict)][:3]
            confidence = data.get("confidence", "?")
            
            # 생성된 태그 수 확인
            entry = cache_layer.memory_get(artifact_key)
            tag_count = len(entry.tags) if entry else 0
            
            logger.info(f"💾 CACHE_SET: {artifact_key} → tables={table_names}, confidence={confidence}, tags={tag_count}")
            
            return f"Cached: {artifact_key} (tags={tag_count}, tables={table_names})"
        except Exception as e:
            logger.error(f"⚠️ CACHE_SET ERROR: {artifact_key} → {str(e)}")
            return f"Cache error: {str(e)}"
    
    def cache_promote(provisional_key: str, confirmed_namespace: str) -> str:
        """
        🔄 임시키를 확정키로 승격 (namespace 확정 시)
        
        MyBatis에서 queryId만 있어서 임시키로 저장했다가,
        나중에 namespace가 확정되면 이 함수로 승격합니다.
        
        ═══════════════════════════════════════════════════════════════════════
        📥 입력 파라미터
        ═══════════════════════════════════════════════════════════════════════
        
        provisional_key: 임시키 (기존)
            예: "mybatis:unknown#createorder"
        
        confirmed_namespace: 확정된 namespace
            예: "ordermapper"
        
        ═══════════════════════════════════════════════════════════════════════
        📤 반환값
        ═══════════════════════════════════════════════════════════════════════
        
        성공: "Promoted: mybatis:unknown#createorder → mybatis:ordermapper#createorder"
        실패: "Promote failed: 임시키를 찾을 수 없음"
        
        Args:
            provisional_key: 임시키
            confirmed_namespace: 확정된 namespace
        
        Returns:
            승격 결과 문자열
        """
        import logging
        logger = logging.getLogger("deepagents.cache")
        
        # 확정키 생성
        if "#" in provisional_key:
            prefix, query_id = provisional_key.rsplit("#", 1)
            type_prefix = prefix.split(":")[0] if ":" in prefix else "mybatis"
            confirmed_key = f"{type_prefix}:{confirmed_namespace.lower()}#{query_id}"
        else:
            logger.warning(f"⚠️ CACHE_PROMOTE: 잘못된 임시키 형식: {provisional_key}")
            return f"Promote failed: 잘못된 임시키 형식"
        
        success = cache_layer.promote_key(provisional_key, confirmed_key)
        
        if success:
            logger.info(f"🔄 CACHE_PROMOTE: {provisional_key} → {confirmed_key}")
            return f"Promoted: {provisional_key} → {confirmed_key}"
        else:
            logger.warning(f"⚠️ CACHE_PROMOTE FAILED: {provisional_key}")
            return f"Promote failed: 임시키를 찾을 수 없음"
    
    # 도구 등록 (3배 상세한 description)
    CACHE_GET_DESCRIPTION = """🔍 캐시에서 아티팩트 조회 (2단계: exact → fuzzy)

═══════════════════════════════════════════════════════════════════════════════
📌 핵심 원칙
═══════════════════════════════════════════════════════════════════════════════
hints를 그대로 전달하세요. 키 생성은 시스템이 자동으로 처리합니다.
절대로 artifact_key를 직접 생성하지 마세요!

═══════════════════════════════════════════════════════════════════════════════
📥 파라미터
═══════════════════════════════════════════════════════════════════════════════
hints_json: (필수) 외부 힌트 리스트 JSON

  MyBatis:     '["namespace=OrderMapper", "queryId=createOrder"]'
  OFBiz entity: '["entity=OrderHeader"]'
  OFBiz service: '["service=createOrderHeader"]'
  Config:       '["configKey=spring.datasource.url"]'
  JPA:          '["jpa_entity=Order"]'

missing_bindings_json: (선택) 미해결 바인딩 리스트 JSON
  예: '["ORDER_HEADER", "order_id"]'
  fuzzy 조회 시 추가 검색 토큰으로 사용

═══════════════════════════════════════════════════════════════════════════════
📤 반환값 (JSON)
═══════════════════════════════════════════════════════════════════════════════
EXACT HIT:  {"hit": true, "match": "exact:memory", "score": 100, "data": {...}}
FUZZY HIT:  {"hit": true, "match": "fuzzy:...", "score": 75, "data": {...}}
MISS:       {"hit": false, "match": "miss", "score": 0}

═══════════════════════════════════════════════════════════════════════════════
📊 Fuzzy 점수 가이드
═══════════════════════════════════════════════════════════════════════════════
≥80: 높은 신뢰도 → 캐시 data 사용
50~79: 중간 → 참고하되 검증 권장
<50: 낮음 → docs 탐색 진행

═══════════════════════════════════════════════════════════════════════════════
⚠️ 금지 사항
═══════════════════════════════════════════════════════════════════════════════
❌ cache_get('mybatis:ordermapper#createorder')  ← 키 직접 전달 금지
✅ cache_get('["namespace=OrderMapper", "queryId=createOrder"]')  ← hints 전달"""
    
    CACHE_SET_DESCRIPTION = """💾 캐시에 아티팩트 저장 (태그 인덱스 자동 생성)

═══════════════════════════════════════════════════════════════════════════════
📌 핵심 원칙
═══════════════════════════════════════════════════════════════════════════════
hints를 그대로 전달하세요. 키 생성은 시스템이 자동으로 처리합니다.
docs에서 테이블/컬럼 추출 후 즉시 호출하세요!

═══════════════════════════════════════════════════════════════════════════════
📥 파라미터
═══════════════════════════════════════════════════════════════════════════════
hints_json: (필수) 외부 힌트 리스트 JSON (cache_get과 동일)

data_json: (필수) 저장할 데이터 JSON
{
  "tables": [
    {"name": "ORDER_HEADER", "op": "write", "confidence": 0.9, "columns": ["ORDER_ID"]}
  ],
  "confidence": 0.9
}

필수 필드: tables, confidence
선택 필드: columns, fks, artifact_meta

═══════════════════════════════════════════════════════════════════════════════
📤 반환값
═══════════════════════════════════════════════════════════════════════════════
성공: "Cached: mybatis:ordermapper#createorder (tags=12, tables=[ORDER_HEADER])"
실패: "Cache error: ..."

═══════════════════════════════════════════════════════════════════════════════
🔧 자동 처리
═══════════════════════════════════════════════════════════════════════════════
• 키 자동 생성 (hints → artifact_key)
• 태그 자동 생성 (테이블명, 컬럼명 → 검색용 태그)
• 임시키 판별 (unknown# → is_provisional=true)
• 품질 비교 (기존보다 낮으면 덮어쓰기 안 함)

═══════════════════════════════════════════════════════════════════════════════
⚠️ 금지 사항
═══════════════════════════════════════════════════════════════════════════════
❌ cache_set('mybatis:ordermapper#createorder', data)  ← 키 직접 전달 금지
✅ cache_set('["namespace=OrderMapper", "queryId=createOrder"]', data)"""
    
    CACHE_PROMOTE_DESCRIPTION = """🔄 임시키를 확정키로 승격 (namespace 확정 시)

═══════════════════════════════════════════════════════════════════════════════
📌 사용 시점
═══════════════════════════════════════════════════════════════════════════════
MyBatis에서 queryId만 있어서 임시키(mybatis:unknown#...)로 저장했다가,
나중에 namespace가 확정되면 이 함수로 승격합니다.

═══════════════════════════════════════════════════════════════════════════════
📥 파라미터
═══════════════════════════════════════════════════════════════════════════════
provisional_key: 임시키 (기존)
  예: "mybatis:unknown#createorder"

confirmed_namespace: 확정된 namespace
  예: "ordermapper"

═══════════════════════════════════════════════════════════════════════════════
📤 반환값
═══════════════════════════════════════════════════════════════════════════════
성공: "Promoted: mybatis:unknown#createorder → mybatis:ordermapper#createorder"
실패: "Promote failed: 임시키를 찾을 수 없음"

═══════════════════════════════════════════════════════════════════════════════
🔧 자동 처리
═══════════════════════════════════════════════════════════════════════════════
• 태그 인덱스 갱신 (임시키 제거, 확정키 추가)
• 임시키 캐시 파일 삭제
• promoted_from 필드에 원본 임시키 기록"""
    
    cache_get_tool = StructuredTool.from_function(
        name="cache_get",
        description=CACHE_GET_DESCRIPTION,
        func=cache_get
    )
    
    cache_set_tool = StructuredTool.from_function(
        name="cache_set",
        description=CACHE_SET_DESCRIPTION,
        func=cache_set
    )
    
    cache_promote_tool = StructuredTool.from_function(
        name="cache_promote",
        description=CACHE_PROMOTE_DESCRIPTION,
        func=cache_promote
    )
    
    cache_tools = [cache_get_tool, cache_set_tool, cache_promote_tool]
    
    # =========================================================================
    # 안전장치 인스턴스 반환
    # =========================================================================
    # Note: todo는 deepagents built-in write_todos 사용 (커스텀 도구 삭제됨)
    
    safety_instances = {
        "evidence_mgr": evidence_mgr,
        "minimal_lock": minimal_lock,
        "cache_layer": cache_layer,
        "schema_mgr": schema_mgr,
        "runtime_policy": POLICY,
    }
    
    return evidence_tools, lock_tools, ddl_tools, docs_tools_list, cache_tools, safety_instances


# =============================================================================
# 서브에이전트 정의
# =============================================================================

def create_subagents(
    neo4j_tools: List, 
    parser_tools: List,
    safety_tools: Optional[tuple] = None
) -> List[Dict]:
    """서브에이전트 정의
    
    Args:
        neo4j_tools: Neo4j 관련 도구 (query, apply_graph_mutations, set_status 등)
        parser_tools: 파서 도구 (mybatis, ofbiz, yaml 파서)
        safety_tools: 안전장치 도구 (evidence, ddl)
    
    서브에이전트 도구 구성 전략:
    ─────────────────────────────────────────────────────────────────────────
    
    1. Locator (tools: [])
       - 역할: 외부 파일 검색 (grep, glob, ls)
       - 도구: 빈 리스트 → FilesystemMiddleware가 자동으로 ls, read_file, 
               grep, glob, write_file, edit_file 제공
       - 이유: 파일 시스템 도구만 필요, 커스텀 도구 불필요
    
    2. Slicer (tools: [])
       - 역할: 파일 슬라이싱 (read_file로 특정 영역 추출)
       - 도구: 빈 리스트 → FilesystemMiddleware 자동 제공
       - 이유: 파일 읽기/쓰기만 필요
    
    3. Confirmer (tools: parser_tools)
       - 역할: 파서로 구조화된 정보 추출
       - 도구: 파서 도구 명시 (parse_mybatis_mapper, parse_ofbiz_entity 등)
               + FilesystemMiddleware 자동 제공
       - 이유: 결정론적 파서 우선, 파서 실패시 LLM fallback 허용
    
    4. Verifier (tools: neo4j_tools + safety_tools)
       - 역할: 검증 및 Neo4j 패치
       - 도구: Neo4j 도구 + Evidence/Lock/Review/DDL 도구 명시
               + FilesystemMiddleware 자동 제공
       - 이유: 그래프 조작 및 안전장치 사용 필요
    
    참고: deepagents의 FilesystemMiddleware는 모든 서브에이전트에
          기본 파일 시스템 도구를 자동 주입합니다.
    ─────────────────────────────────────────────────────────────────────────
    """
    
    # =========================================================================
    # Locator: 파일 시스템 검색 + 그래프 탐색 전문
    # tools: 그래프 검색 도구 + FilesystemMiddleware가 ls, read_file, grep, glob 자동 제공
    # =========================================================================
    locator_subagent = {
        "name": "locator",
        "description": "소스 코드 노드 검색(그래프)과 외부 설정 파일 검색(파일시스템)을 담당합니다.",
        "system_prompt": """당신은 코드 탐색 전문가입니다.

## ⚠️ 중요: 경로 규칙

**모든 파일시스템 경로는 슬래시(/)만 사용하고, /docs/로 시작해야 합니다!**
- ✅ 올바름: /docs/SAMPLE/entitydef/order.xml
- ❌ 잘못됨: \\SAMPLE\\entitydef\\order.xml (백슬래시 금지!)
- ❌ 잘못됨: /SAMPLE/entitydef/order.xml (/docs/ 누락!)

## 📂 파일 탐색 전략

**⚠️ ls 대신 glob 사용하세요! (ls가 빈 결과를 반환할 수 있음)**

### 파일 목록 확인:
```
❌ ls({'path': '/docs/SAMPLE'})                  → 빈 결과 가능!
✅ glob({'pattern': '/docs/SAMPLE/*'})           → 1레벨 내용
✅ glob({'pattern': '/docs/SAMPLE/**/*.xml'})    → 모든 XML
✅ glob({'pattern': '/docs/**/entitydef/*.xml'}) → entitydef 폴더의 XML
✅ glob({'pattern': '/docs/**/servicedef/*.xml'})→ servicedef 폴더의 XML
✅ glob({'pattern': '/docs/**/mapper/*.xml'})    → mapper 폴더의 XML
```

### 파일 읽기:
```
✅ read_file({'file_path': '/docs/SAMPLE/entitydef/order-entitymodel.xml'})
```

### 텍스트 검색:
```
✅ grep({'pattern': 'entity-name="OrderHeader"', 'path': '/docs/**/*.xml'})
✅ grep({'pattern': 'namespace="com.example"', 'path': '/docs/**/mapper/*.xml'})
```

## 🔍 소스 코드 분석 (그래프 탐색만!)

소스 코드(.java, .kt)는 **파일시스템 접근 금지!** Neo4j 그래프 사용:
- search_nodes_by_keyword({'keyword': 'OrderService'})
- find_imports_in_file({'directory': 'SAMPLE/OrderService.java'})  ← /docs/ 없이!
- find_nodes_by_directory({'path_pattern': 'SAMPLE/'})

## 📋 외부 참조 검색 가이드

| external_hint 유형 | 검색 방법 |
|------------------|---------|
| entity=EntityName | glob('/docs/**/entitydef/*.xml') → grep('entity-name="EntityName"') |
| service=ServiceName | glob('/docs/**/servicedef/*.xml') → grep('name="ServiceName"') |
| queryId=ns.id | glob('/docs/**/mapper/*.xml') → grep('namespace="ns"') |
| configKey | glob('/docs/**/*.yaml') → grep('key') |

LLM 호출 금지 - 도구만 사용하세요.""",
        # tools: 그래프 검색 도구 + FilesystemMiddleware 자동 제공
        "tools": [
            neo4j_tools[5],   # search_nodes_by_keyword
            neo4j_tools[7],   # find_imports_in_file
            neo4j_tools[8],   # find_nodes_by_directory
            neo4j_tools[9],   # get_node_children
        ],
    }
    
    # =========================================================================
    # Slicer: 파일 슬라이싱 전문
    # tools: [] → FilesystemMiddleware가 read_file, write_file 자동 제공
    # =========================================================================
    slicer_subagent = {
        "name": "slicer",
        "description": "Locator가 찾은 파일에서 필요한 부분만 추출합니다. XML 태그 블록이나 YAML 섹션을 정확히 잘라냅니다.",
        "system_prompt": """당신은 파일 슬라이싱 전문가입니다.

## ⚠️ 경로 규칙 (필수!)

파일 읽기/쓰기 시 반드시 올바른 경로 사용:
- 읽기: /docs/로 시작 (예: /docs/SAMPLE/mapper/order.xml)
- 쓰기: /slices/로 시작 (예: /slices/OrderMapper_select.xml)

```
❌ read_file({'file_path': '/SAMPLE/order.xml'})      → 오류!
✅ read_file({'file_path': '/docs/SAMPLE/order.xml'}) → 정상
✅ write_file({'file_path': '/slices/extract.xml', ...}) → 정상
```

## 당신의 역할

1. Locator가 찾은 파일 경로와 라인 범위를 받음
2. 해당 영역의 완전한 블록을 추출 (XML 태그가 열리고 닫히는 완전한 범위)
3. 추출된 내용을 /slices/에 저장

## 슬라이싱 규칙

- XML: 태그가 완전히 열리고 닫히는 범위로 확장
- YAML: 들여쓰기 레벨이 같은 블록 전체
- 최대 500줄까지만 (넘으면 분할)

LLM 호출 금지 - 파일 시스템 도구만 사용하세요.""",
        # tools: [] → FilesystemMiddleware가 read_file, write_file 자동 제공
        "tools": [],
    }
    
    # =========================================================================
    # Confirmer: 파싱 및 매핑 확정
    # tools: parser_tools (파서 도구) + FilesystemMiddleware 자동 제공
    # LLM fallback 허용 (파서 실패시)
    # =========================================================================
    confirmer_subagent = {
        "name": "confirmer",
        "description": "Slicer가 추출한 내용을 파싱하여 테이블/엔티티 매핑을 확정합니다. 파서 도구 우선, 필요시 LLM 사용.",
        "system_prompt": """당신은 코드 분석 전문가입니다.

당신의 역할:
1. 추출된 XML/YAML 내용을 받음
2. 파서 도구로 구조화된 정보 추출 (우선)
3. 파서로 해결 안 되면 직접 분석 (LLM)
4. 테이블명, 엔티티명, 매핑 정보 확정
5. normalize_table_name 도구로 테이블명 정규화

파싱 우선순위:
1. parse_mybatis_mapper - MyBatis XML
2. parse_ofbiz_entity - OFBiz entitydef
3. parse_ofbiz_service - OFBiz servicedef
4. parse_yaml_config - YAML 설정

출력 형식:
{
  "confirmed": true/false,
  "artifact_type": "mybatis_query" | "ofbiz_entity" | "ofbiz_service" | "yaml_config",
  "artifact_key": "namespace.id" 또는 "entity-name",
  "tables_read": ["TABLE1", "TABLE2"],
  "tables_write": ["TABLE3"],
  "confidence": 0.0-1.0,
  "evidence": "근거 요약"
}""",
        # tools: parser_tools (파서 도구) + FilesystemMiddleware 자동 제공
        "tools": parser_tools,
    }
    
    # =========================================================================
    # Verifier: FSM 기반 검증 및 Neo4j 커밋
    # 플랜 기반: Artifact/RESOLVES_TO 금지, apply_graph_mutations 사용
    # =========================================================================
    # Verifier 도구 구성 (Neo4j + Evidence + Cache)
    verifier_tools = list(neo4j_tools)  # 복사
    
    if safety_tools:
        evidence_tools, _, ddl_tools, _, cache_tools, _ = safety_tools
        verifier_tools.extend(evidence_tools)  # create_evidence, verify_evidence
        verifier_tools.extend(ddl_tools)       # check_ddl_exists
        verifier_tools.extend(cache_tools)     # cache_get, cache_set, cache_promote
    
    verifier_subagent = {
        "name": "verifier",
        "description": "DBMS 표준 스키마로 Neo4j 커밋. apply_graph_mutations로 배치 처리.",
        "system_prompt": """당신은 DBMS 표준 스키마 커밋 전문가입니다.

## 🚨 최우선 규칙

1. **DDL 없어도 무조건 커밋** - TABLE/COLUMN/FK 생성
2. **FROM/WRITES 무조건 생성** - 테이블 발견되면 반드시 관계 연결
3. **set_status_success 우선** - review 남용 금지

## 🔄 캐시 사용 (hints 기반!)

캐시 도구에는 hints를 그대로 전달하세요. 키 생성은 시스템이 처리합니다.

❌ 금지: cache_get('mybatis:ordermapper#createorder')
✅ 올바름: cache_get('["namespace=OrderMapper", "queryId=createOrder"]')

## DBMS 표준 관계명 (6개만!)

- `FROM`: 읽기 (SELECT)
- `WRITES`: 쓰기 (INSERT/UPDATE/DELETE)
- `HAS_COLUMN`: 테이블-컬럼
- `FK_TO_TABLE`: 테이블 → 테이블 (sourceColumn, targetColumn 속성)
- `FK_TO`: 컬럼 → 컬럼

### ❌ 금지
- FK_TO_COLUMNS, FK_TO_TABLES (레거시)
- REFERENCES (안티패턴)

## Neo4j 반영 (apply_graph_mutations)

**mutations 배열 (DBMS 표준):**
```json
[
  {"type": "MERGE_TABLE", "table": "ORDER_HEADER", "db": "default", "schema": "PUBLIC"},
  {"type": "MERGE_HAS_COLUMN", "table_name": "ORDER_HEADER", "column_name": "ID", "dtype": "VARCHAR", "db": "default", "schema": "PUBLIC"},
  {"type": "MERGE_FROM", "from_element_id": "4:xxx", "to_table": "ORDER_HEADER"},
  {"type": "MERGE_WRITES", "from_element_id": "4:xxx", "to_table": "ORDER_ITEM"},
  {"type": "MERGE_FK_TO_TABLE", "from_table": "ORDER_ITEM", "to_table": "ORDER_HEADER", "source_column": "ORDER_ID", "target_column": "ID"}
]
```

## 상태 업데이트

### 성공 시 (기본!)
```
set_status_success(element_id="4:xxx", evidence_path="evidence/xxx.json")
```

### 리뷰 (제한적 사용)
- write_conflict: write 대상 primary table 2개 이상
- 그 외는 **성공** 처리 (DDL 없음, confidence 낮음 → 리뷰 아님!)

## 처리 순서

1. mutations 배열 생성 (MERGE_TABLE → MERGE_HAS_COLUMN → MERGE_FK_TO_TABLE → FROM/WRITES)
2. apply_graph_mutations() 호출
3. set_status_success() 호출

LLM 호출 금지 - 도구와 규칙만 사용하세요.""",
        "tools": verifier_tools,
    }
    
    return [locator_subagent, slicer_subagent, confirmer_subagent, verifier_subagent]


# =============================================================================
# 메인 에이전트 생성
# =============================================================================

SYSTEM_PROMPT = """당신은 코드 분석 Phase3 실행 에이전트입니다.

## 역할 정의

═══════════════════════════════════════════════════════════════════════════════
### 🚨🚨🚨 핵심: 청크 단위 일괄 처리! (노드별 개별 처리 아님!) 🚨🚨🚨
═══════════════════════════════════════════════════════════════════════════════

**여러 후보 노드가 한 번에 전달됩니다!**
- 청크 내 모든 후보를 **한 번에** 처리해야 합니다
- cache_get/docs_search는 **청크당 1회** (후보별 반복 금지!)
- link_candidates_to_tables로 **모든 후보↔테이블 일괄 연결**
- set_status_success는 **각 후보별로** 호출

**✅ 올바른 흐름 (청크 5개 후보):**
```
cache_get (1회) → docs_search (1회) → TABLE MERGE (1회)
→ link_candidates_to_tables (element_ids=[5개], tables=[...])
→ set_status_success × 5
```

**❌ 잘못된 흐름 (노드별 반복):**
```
후보1: cache_get → docs → TABLE → link → status
후보2: cache_get → docs → TABLE → link → status  ← 반복 금지!
...
```

docs에서 DB 테이블/컬럼/FK를 추출하고 Neo4j 그래프에 DBMS 표준 스키마로 반영합니다.

## 🚨 최우선 규칙 3개

1. **DDL 없어도 무조건 생성** - DDL 체크는 '옵션'이지 '커밋 조건'이 아님
2. **테이블 발견되면 FROM/WRITES 무조건 생성** - evidence만 만들고 커밋 안 하는 것 금지
3. **소스 파일(.java) 탐색 절대 금지** - 코드 확인은 Neo4j 그래프(Cypher)만

## DBMS 표준 스키마 (필수!)

### TABLE 노드
- PK: {db, schema, name} (기본값: default, PUBLIC, 테이블명)
- 속성: description, description_source, table_type

### COLUMN 노드
- PK: {fqn} (db.schema.table.column)
- 속성: name, dtype, description, description_source, nullable, pk_constraint

### 허용되는 관계 (6개만)
- (CodeNode)-[:FROM]->(Table): 읽기 (SELECT)
- (CodeNode)-[:WRITES]->(Table): 쓰기 (INSERT/UPDATE/DELETE)
- (Table)-[:HAS_COLUMN]->(Column)
- (Table)-[:FK_TO_TABLE]->(Table): sourceColumn, targetColumn 속성 포함
- (Column)-[:FK_TO]->(Column)
- (Table)-[:BELONGS_TO]->(Schema)

### ❌ 금지되는 관계
- REFERENCES, USES_TABLE, MAPS_TO_TABLE, RESOLVES_TO, FK_TO_COLUMNS, FK_TO_TABLES

### 관계 속성 규칙
- FROM/WRITES: source 속성만 (다른 속성 넣지 말 것)
- FK_TO_TABLE: sourceColumn, targetColumn, type, source

## DDL 정책 (핵심 변경!)

❌ 이전 정책 (잘못됨):
- DDL 없으면 review로 보내고 커밋 중단
- confidence < 0.7이면 review

✅ 새 정책:
- DDL 없음 = DeepAgents가 docs 기반으로 스키마를 **만들어야 하는** 상황
- 테이블/컬럼 발견되면 무조건 생성 (DDL 여부 무관)
- FROM/WRITES 관계도 무조건 생성
- set_status_success 사용 (review 남용 금지)

## FROM/WRITES 관계 생성 (🚨 핵심!)

### 원칙
- 청크 내 후보노드들 → 식별된 테이블들 연결 (단순!)
- MERGE라서 중복 체크 불필요
- 하나의 노드에서 여러 FROM 가능 (JOIN, 서브쿼리 등)

### 판정 규칙

| SQL/힌트 | 관계 |
|----------|------|
| SELECT | FROM |
| INSERT/UPDATE/DELETE | WRITES |
| ofbiz service: create/update | WRITES |
| ofbiz service: find/get/list | FROM |
| 불확실 | FROM (안전하게) |

### 사용 도구
- `link_candidates_to_tables`: 청크 후보들과 테이블들 일괄 연결
  - candidate_element_ids: JSON 배열 (청크 내 후보노드 ID들)
  - table_names: JSON 배열 (식별된 테이블명들)
  - operation: "read" → FROM, "write" → WRITES

## 🔄 비-테이블 결과 처리 (중요!)

═══════════════════════════════════════════════════════════════════════════════
### 🚨 핵심: 외부 파일 참조가 항상 테이블은 아님!
═══════════════════════════════════════════════════════════════════════════════

외부 파일 참조 유형:
| 유형 | 예시 | 처리 |
|------|------|------|
| **Entity/SQL** | `findByAnd("OrderHeader")`, MyBatis query | TABLE 생성 + FROM/WRITES |
| **Bean/Service** | `@Autowired OrderService`, `runSync("createOrder")` | **노드 summary 업데이트** |
| **Config** | `@Value("${order.max}")`, YAML 설정 | **노드 summary 업데이트** |

### 테이블이 아닌 경우 → 노드 summary 업데이트!

외부 파일에서 Bean, Service, Config 정보를 발견한 경우:

```
apply_graph_mutations(mutations_json='[
  {
    "type": "UPDATE_SUMMARY",
    "element_id": "4:abc:123",
    "summary": "OrderService Bean을 주입받아 createOrder 서비스 호출. 주문 생성 및 검증 수행."
  }
]')
```

### 판정 기준

| 발견 내용 | 처리 방법 |
|-----------|-----------|
| entity 정의 (테이블명 있음) | TABLE/COLUMN 생성 + FROM/WRITES |
| service 정의 (entity 참조 있음) | TABLE 생성 + FROM/WRITES |
| service 정의 (entity 참조 없음) | **UPDATE_SUMMARY** ("서비스X 호출") |
| config 값 (설정값만) | **UPDATE_SUMMARY** ("설정값X=Y 사용") |
| Bean 정의 | **UPDATE_SUMMARY** ("BeanX 주입") |

### UPDATE_SUMMARY 작성 규칙

summary에 반드시 포함:
1. **무엇을** 참조하는지 (Bean명, Service명, Config키)
2. **어디서** 왔는지 (파일 경로)
3. **역할이** 무엇인지 (간략 설명)

예시:
- "OrderService Bean(@Autowired) 주입. 주문 생성/조회 서비스 제공."
- "order.max-items-per-order=100 설정값 사용. 주문당 최대 상품 수 제한."
- "createOrderHeader 서비스(OFBiz) 호출. 주문 헤더 생성 처리."

## 이름 정규화 정책

- TABLE_NAME_POLICY env 변수 따름 (ORIGINAL | UPPER | LOWER)
- 기본값: ORIGINAL (원본 그대로 유지)
- 테이블/컬럼명 변경하지 말고 식별된 그대로 사용

## dtype 정책

- SQL 타입으로 변환하지 말 것!
- 자바 타입 그대로 저장 (id-ne, String, Long, BigDecimal 등)

## description 정책 (🚨 중요! LLM 통합!)

═══════════════════════════════════════════════════════════════════════════════
### ❌ 금지: 단순 누적 (예: "설명1 | 설명2 | 설명3")
### ✅ 필수: LLM 통합 (예: "통합된 한 문단 설명")
═══════════════════════════════════════════════════════════════════════════════

**테이블 정보 발견 시 반드시 `merge_table_description` 호출!**

```
merge_table_description(
    table_name='ORDER_HEADER',
    new_findings='["OFBiz OrderHeader 엔티티", "주문 정보 저장"]'
)
```

**결과 예시:**
- 기존: "주문 테이블"
- 새 발견: ["OFBiz OrderHeader 엔티티", "주문 ID/상태 관리"]
- → **통합**: "주문 정보를 관리하는 핵심 테이블. OFBiz OrderHeader 엔티티와 매핑되며, 주문 ID와 상태를 저장한다."

**규칙:**
1. 테이블/컬럼 생성 후 description 발견 시 반드시 merge 호출
2. 한 청크에서 같은 테이블 여러 번 발견 → 한 번만 merge (발견 내용 모아서)
3. description이 없으면 새 발견 내용 그대로 저장

## 소스 파일 탐색 금지 (절대!)

❌ 금지:
- docs_slice로 .java/.kt 파일 열기
- /docs/SAMPLE/OrderService.java 탐색 시도

✅ 허용:
- docs에서 XML/YAML/properties만 탐색
- 코드는 Neo4j 그래프로만 조회 (search_nodes_by_keyword, node_code 속성)

## 🔄 캐시 시스템 (유사도 기반 매칭)

═══════════════════════════════════════════════════════════════════════════════
### 🚨🚨🚨 핵심: 단어가 포함/유사하면 매칭됩니다! 🚨🚨🚨
═══════════════════════════════════════════════════════════════════════════════

캐시 매칭은 **정확 일치가 아닌 유사도 기반**입니다.
- "order" 검색 → "ORDER_HEADER", "orderheader", "OrderHeader" 모두 매칭!
- "header" 검색 → "ORDER_HEADER", "PRODUCT_HEADER" 등 포함된 것 모두 매칭!

┌─────────────────────────────────────────────────────────────────────────────┐
│ ✅ search_tokens 활용 (핵심!)                                               │
├─────────────────────────────────────────────────────────────────────────────┤
│ 후보의 search_tokens(비즈니스 단어)를 cache_get에 전달하면                   │
│ 유사도 매칭 성공률이 높아집니다!                                             │
│                                                                              │
│ 예: search_tokens=["order", "header", "status"] 전달 시                      │
│     → ORDER_HEADER, ORDER_STATUS 등과 매칭                                  │
└─────────────────────────────────────────────────────────────────────────────┘

═══════════════════════════════════════════════════════════════════════════════
### cache_get 사용법 (docs 탐색 전에 반드시!)
═══════════════════════════════════════════════════════════════════════════════

**입력:**
```json
hints_json: '["entity=OrderHeader"]'
missing_bindings_json: '["OrderHeader", "orderId"]'  // 선택
search_tokens_json: '["order", "header"]'  // 🚨 핵심! 비즈니스 단어
```

**hints 형식 가이드 (범용):**
┌────────────────┬──────────────────────────────────────────────────────────┐
│ 힌트 타입      │ 형식 및 예시                                             │
├────────────────┼──────────────────────────────────────────────────────────┤
│ entity         │ ["entity=OrderHeader"]                                   │
│ service        │ ["service=createOrder"]                                  │
│ queryId        │ ["queryId=selectOrderById"]                              │
│ namespace      │ ["namespace=OrderMapper", "queryId=createOrder"]         │
│ configKey      │ ["configKey=spring.datasource.url"]                      │
│ symbol         │ ["symbol=orderService"]                                  │
└────────────────┴──────────────────────────────────────────────────────────┘

**반환값 해석:**
┌────────────────┬────────────────┬──────────────────────────────────────────┐
│ match 값       │ score          │ 다음 행동                                │
├────────────────┼────────────────┼──────────────────────────────────────────┤
│ exact:memory   │ 100            │ ✅ data 사용, docs 탐색 생략             │
│ exact:file     │ 100            │ ✅ data 사용, docs 탐색 생략             │
│ fuzzy:sim=0.8  │ 60~99          │ ⚠️ 유사도 매칭 (score 확인 후 결정)      │
│ miss           │ 0              │ ❌ docs 탐색 진행                        │
└────────────────┴────────────────┴──────────────────────────────────────────┘

**유사도 점수 해석:**
- **score ≥ 70**: 높은 유사도 → 캐시 data 사용
- **score 50~69**: 중간 유사도 → 캐시 data 참고, 추가 검증 권장
- **score < 50**: 낮은 유사도 → docs 탐색 진행

═══════════════════════════════════════════════════════════════════════════════
### cache_set 사용법 (docs 파싱 후 즉시!)
═══════════════════════════════════════════════════════════════════════════════

**docs에서 테이블/컬럼 추출한 후 즉시 호출!**
저장 안 하면 다음 후보에서 또 docs 파싱해야 함 (비효율!)

**입력:**
```json
hints_json: '["namespace=OrderMapper", "queryId=createOrder"]'
data_json: '{
  "tables": [
    {"name": "ORDER_HEADER", "op": "write", "confidence": 0.9, "columns": ["ORDER_ID", "CUSTOMER_ID"]}
  ],
  "confidence": 0.9
}'
```

**data_json 필수 필드:**
┌────────────────┬────────────────────────────────────────────────────────────┐
│ 필드           │ 설명                                                       │
├────────────────┼────────────────────────────────────────────────────────────┤
│ tables         │ 테이블 배열 [{name, op, confidence, columns?}, ...]        │
│ tables[].name  │ 테이블명 (대문자 권장): "ORDER_HEADER"                      │
│ tables[].op    │ "read" 또는 "write"                                        │
│ tables[].confidence │ 0.0~1.0                                               │
│ confidence     │ 전체 신뢰도: 0.0~1.0                                       │
└────────────────┴────────────────────────────────────────────────────────────┘

**자동 처리 기능:**
1. 키 자동 생성: hints에서 정규화된 artifact_key 생성
2. 태그 자동 생성: 테이블명, 컬럼명, 힌트에서 검색용 태그 추출
3. 임시키 판별: mybatis:unknown#... 형태는 is_provisional=true
4. 품질 비교: 기존 캐시보다 품질이 낮으면 덮어쓰기 안 함

═══════════════════════════════════════════════════════════════════════════════
### 처리 흐름 (TODO 체크리스트)
═══════════════════════════════════════════════════════════════════════════════

각 후보 처리 시 아래 순서로 진행:

```
[ ] 1. cache_get 호출
[ ] 2. 캐시 분기 판단 (🚨 아래 규칙 필수!)
[ ] 3. (miss/재탐색) docs 탐색 + cache_set
[ ] 4. TABLE/COLUMN MERGE (테이블 있을 때)
[ ] 5. FROM/WRITES 관계 생성 (테이블 있을 때)
[ ] 6. 🚨 evidence + set_status_success (항상 필수!)
```

═══════════════════════════════════════════════════════════════════════════════
### 🚨🚨🚨 캐시 히트 분기 규칙 (핵심!) 🚨🚨🚨
═══════════════════════════════════════════════════════════════════════════════

**✅ cache_get 반환값 해석 (시스템이 artifact_type별로 자동 검증!)**

| cache_get 결과 | 처리 |
|----------------|------|
| `hit=True` | ✅ 유효한 캐시! data 사용 |
| `hit=False` (miss) | 🔍 **docs 탐색 필수!** |

**🔑 캐시 유효성 검증 (시스템 자동 처리, 프레임워크 무관):**

| 정보 유형 | 유효 조건 |
|-----------|-----------|
| 테이블 정보 | `tables` 배열에 1개 이상 |
| 설정값 정보 | `config.key` 또는 `config.value` 있음 |
| 서비스 정보 | `services` 배열에 1개 이상 |
| 메타 정보 | `artifact_meta`에 file/key/name 등 |

→ 어떤 형태로든 유의미한 정보가 있으면 hit
→ 빈 캐시 체크 로직 직접 구현 불필요

### 히트 시 처리 (타입별 분기)
```
cache_get → hit=True
→ artifact_type 확인
→ 테이블 타입: TABLE/COLUMN MERGE + FROM/WRITES
→ config 타입: 노드 summary 업데이트 (UPDATE_SUMMARY)
→ service 타입: 참조 entity 있으면 TABLE MERGE
```

### 미스 시 처리
```
cache_get → hit=False
→ docs 탐색 (extract_enclosing_element 등)
→ parse_mybatis_mapper / parse_ofbiz_entity / parse_yaml_config
→ 결과에 따라 tables/config 추출
→ cache_set (다음을 위해!)
```

═══════════════════════════════════════════════════════════════════════════════
### 🚨 6번 (evidence + status)은 항상 실행! (비-테이블도!)
═══════════════════════════════════════════════════════════════════════════════

**테이블 있는 경우:**
```
create_evidence(...) → set_status_success(element_id, evidence_path)
```

**비-테이블 (config 등):**
```
// 테이블은 없지만 status는 반드시 업데이트!
set_status_success(
  element_id="4:...:123",
  evidence_path="/evidence/config_order_max_items.json"
)
```

완료된 항목은 [x]로 표시.

═══════════════════════════════════════════════════════════════════════════════
### 🔥 docs 탐색 도구 선택 가이드 (필수!)
═══════════════════════════════════════════════════════════════════════════════

| 파일 유형 | 권장 도구 | 예시 |
|-----------|-----------|------|
| **XML** (entity, mapper, service, bean) | `extract_enclosing_element` | keyword="OrderHeader" → 전체 태그 |
| **YAML** (config, application.yml) | `extract_yaml_block` | key_path="order.max-items" → 블록 |
| **기타** (properties, txt) | `docs_slice_around_match` | 단순 텍스트 슬라이스 |

### ❌ 흔한 실수
```
docs_slice_around_match(file_path="...xml", keyword="OrderHeader")
→ 키워드 위치만 잘라서 태그가 깨질 수 있음!
```

### ✅ 올바른 사용
```
# XML 파일에서 OrderHeader 엔티티 전체 추출
extract_enclosing_element(
  file_path="/docs/SAMPLE/entitydef/order.xml",
  keyword="OrderHeader"
)
→ <entity entity-name="OrderHeader">...</entity> 전체 반환!

# YAML에서 order 설정 블록 추출  
extract_yaml_block(
  file_path="/docs/SAMPLE/config/application.yaml",
  key_path="order.max-items-per-order",
  include_parent=True  # order: 블록 전체
)
→ order:
    default-currency: USD
    max-items-per-order: 100
```

### 도구 반환 형식
```json
// extract_enclosing_element
{"tag_name": "entity", "content": "<entity>...</entity>", "start": 1234, "end": 5678, "size": 4444}

// extract_yaml_block  
{"key": "order", "content": "order:\n  ...", "start_line": 10, "end_line": 15}
```

═══════════════════════════════════════════════════════════════════════════════
### 캐시 효율화 규칙 (툴 호출 난사 방지!)
═══════════════════════════════════════════════════════════════════════════════

1. **cache_get 먼저**: docs 탐색 전에 반드시 캐시 조회
2. **Exact hit + tables 있음**: docs 생략 가능
3. **Fuzzy hit 또는 tables=[]**: docs 확인 필요 (빈 캐시 검증)
4. **cache_set 즉시**: 파싱 완료 후 즉시 저장 (다음 후보를 위해)
5. **중복 금지**: 같은 hints로 cache_get을 2번 호출하지 않음

═══════════════════════════════════════════════════════════════════════════════
### 🚨 write_todos 호출 규칙 (중복 방지!)
═══════════════════════════════════════════════════════════════════════════════

write_todos는 **상태 변경 시에만** 호출:

✅ 허용:
- 처리 시작 시: 전체 TODO 초기화 (1회)
- 단계 완료 시: 해당 단계만 completed로 변경 (1회씩)

❌ 금지:
- 같은 상태로 2번 이상 호출
- 매 LLM 응답마다 전체 TODO 재전송
- 이미 completed인 항목 다시 completed로 변경

**효율적 호출 예시:**
```
// 시작 시 1회
write_todos([{id:1, status:"in_progress"}, {id:2, status:"pending"}, ...])

// 1단계 완료 후 1회
write_todos([{id:1, status:"completed"}, {id:2, status:"in_progress"}])

// 2단계 완료 후 1회 (변경된 것만!)
write_todos([{id:2, status:"completed"}, {id:3, status:"in_progress"}])
```

═══════════════════════════════════════════════════════════════════════════════
### ⚠️ 흔한 실수와 해결책
═══════════════════════════════════════════════════════════════════════════════

| 실수 | 해결책 |
|------|--------|
| 키를 직접 생성 | hints 배열을 그대로 전달 |
| cache_set 안 함 | 파싱 후 반드시 cache_set 호출 |
| fuzzy hit 무시 | score≥80이면 data 신뢰 가능 |
| 중복 cache_get | 한 후보당 1회만 호출 |

## 📋 TODO 워크플로우 (built-in write_todos 사용)

═══════════════════════════════════════════════════════════════════════════════
### 🚨🚨🚨 필수: 청크 처리 시 아래 체크리스트 사용! 🚨🚨🚨
═══════════════════════════════════════════════════════════════════════════════

**청크 처리 시작 시 write_todos 호출:**

```json
[
  {"id": "1", "content": "cache_get 호출 (hints + search_tokens)", "status": "in_progress"},
  {"id": "2", "content": "캐시 hit→3번, miss→docs 탐색", "status": "pending"},
  {"id": "3", "content": "결과 분석 + cache_set (테이블 vs 비테이블 분기)", "status": "pending"},
  {"id": "4", "content": "🅰️TABLE MERGE 또는 🅱️UPDATE_SUMMARY", "status": "pending"},
  {"id": "5", "content": "🅰️merge_description + FROM/WRITES (테이블인 경우만)", "status": "pending"},
  {"id": "6", "content": "create_evidence + set_status_success", "status": "pending"}
]
```

**분기 규칙:**
- **테이블 발견** (entity/SQL) → 4→5→6 (TABLE 생성 + 관계)
- **비-테이블** (Bean/Config/Service) → 4(UPDATE_SUMMARY)→6 (5 생략)

═══════════════════════════════════════════════════════════════════════════════
### 단계별 상세 흐름
═══════════════════════════════════════════════════════════════════════════════

**1단계: cache_get 호출**
```
cache_get(
  hints_json='["entity=OrderHeader"]',
  missing_bindings_json='["OrderHeader"]',
  search_tokens_json='["order", "header"]'
)
```
→ 완료 후: write_todos([{"id":"1","status":"completed"},{"id":"2","status":"in_progress"}])

**2단계: 분기 처리 (🚨 캐시 규칙 필수!)**

| 캐시 결과 | tables | 처리 |
|-----------|--------|------|
| Exact hit (100) | **있음** | 3단계 생략, 캐시 data 사용 |
| Exact hit (100) | **없음** | ⚠️ **빈 캐시! 재탐색 필요** |
| Fuzzy hit (70~90) | 상관없음 | docs 확인 권장 (검증) |
| Miss (<70) | - | docs 탐색 필수 |

→ 완료 후: write_todos([{"id":"2","status":"completed"},{"id":"3","status":"in_progress"}])

**3단계: 결과 분석 + cache_set**

⚠️ 여기서 **분기** 발생:
- **테이블 발견** → 4A 경로 (TABLE 생성)
- **비-테이블** (Bean/Config/Service) → 4B 경로 (summary 업데이트)

```
cache_set(
  hints_json='["entity=OrderHeader"]',
  data_json='{"tables":[...],"artifact_type":"ofbiz_entity","confidence":0.9}'
)
```
→ 완료 후: write_todos([{"id":"3","status":"completed"},{"id":"4","status":"in_progress"}])

═══════════════════════════════════════════════════════════════════════════════
### 🅰️ 테이블 경로 (entity/SQL 발견 시)
═══════════════════════════════════════════════════════════════════════════════

**4A단계: TABLE/COLUMN 노드 MERGE**
```
apply_graph_mutations(mutations_json='[
  {"type":"MERGE_TABLE","table_name":"ORDER_HEADER","columns":[...]},
  {"type":"MERGE_HAS_COLUMN","table_name":"ORDER_HEADER","columns":["ORDER_ID","STATUS"]}
]')
```

**5A단계: Description LLM 통합**
```
merge_table_description(
  table_name='ORDER_HEADER',
  new_findings='["OFBiz OrderHeader 엔티티", "주문 정보 저장"]'
)
```

**6A단계: FROM/WRITES 관계 생성**
```
link_candidates_to_tables(
  candidate_element_ids='["4:abc:123"]',
  table_names='["ORDER_HEADER"]',
  operation='read'
)
```

═══════════════════════════════════════════════════════════════════════════════
### 🅱️ 비-테이블 경로 (Bean/Config/Service 발견 시)
═══════════════════════════════════════════════════════════════════════════════

**4B단계: 노드 summary 업데이트 (🚨 테이블 없음!)**
```
apply_graph_mutations(mutations_json='[
  {
    "type": "UPDATE_SUMMARY",
    "element_id": "4:abc:123",
    "summary": "OrderService Bean 주입. createOrder 서비스 호출하여 주문 생성 처리."
  }
]')
```

예시 summary:
- Bean: "OrderService Bean(@Autowired) 주입. 주문 생성/조회 서비스."
- Config: "order.max-items=100 설정값 사용. 주문당 최대 상품 수 제한."
- Service: "createOrderHeader 서비스(OFBiz) 호출. 주문 헤더 생성."

**🚨 비-테이블도 6단계 (evidence + status) 필수!**
```
create_evidence(
  patch_id="run_cfg_order_max",
  source_file="/docs/SAMPLE/config/application.yaml",
  line_start=46, line_end=51,
  matched_content="order.max-items-per-order=100"
)
set_status_success(element_id='4:abc:123', evidence_path='/evidence/run_cfg_order_max.json')
```

═══════════════════════════════════════════════════════════════════════════════
### 🚨🚨🚨 6단계는 테이블 유무와 관계없이 항상 실행! 🚨🚨🚨
═══════════════════════════════════════════════════════════════════════════════

**테이블 경로:**
```
create_evidence(...) → set_status_success(...)
```

**비-테이블 경로 (config, bean 등):**
```
// 테이블 없어도 반드시 status 업데이트!
set_status_success(element_id, evidence_path)
```

→ 완료 후: write_todos([{"id":"6","status":"completed"}])

═══════════════════════════════════════════════════════════════════════════════
### ⚠️ TODO 규칙
═══════════════════════════════════════════════════════════════════════════════

1. **청크 시작 시**: 반드시 write_todos로 체크리스트 생성
2. **단계 완료 시**: 즉시 해당 항목 "completed"로 업데이트
3. **실패 시**: "failed"로 업데이트 + 원인 기록
4. **다음 청크**: 새 체크리스트로 교체 (merge=false)

═══════════════════════════════════════════════════════════════════════════════
### 🚨🚨🚨 청크 일괄 처리 (필수!) 🚨🚨🚨
═══════════════════════════════════════════════════════════════════════════════

**청크에 여러 후보가 전달됩니다. 반드시 일괄 처리하세요!**

**처리 순서:**
1. **cache_get 1회**: 청크 전체 힌트로 조회
2. **docs 탐색 1회**: 캐시 miss인 경우만
3. **TABLE/COLUMN MERGE**: 청크 내 모든 테이블 일괄
4. **link_candidates_to_tables**: 모든 후보 ID 배열 + 테이블 배열
5. **set_status_success × N**: 각 후보별로 완료 처리

**예시 (청크 5개 후보):**
```
// 1. cache_get (힌트 통합)
cache_get(hints_json='["entity=OrderHeader", "queryId=createOrder"]')

// 2. TABLE MERGE (테이블 일괄)
apply_graph_mutations(...)

// 3. 관계 일괄 생성
link_candidates_to_tables(
  candidate_element_ids='["4:a:1", "4:a:2", "4:a:3", "4:a:4", "4:a:5"]',
  table_names='["ORDER_HEADER"]',
  operation='write'
)

// 4. 각 후보 완료
set_status_success(element_id='4:a:1', evidence_path='...')
set_status_success(element_id='4:a:2', evidence_path='...')
set_status_success(element_id='4:a:3', evidence_path='...')
set_status_success(element_id='4:a:4', evidence_path='...')
set_status_success(element_id='4:a:5', evidence_path='...')
```

**❌ 금지: 후보별 반복 처리**
```
// 금지! 후보마다 cache_get 호출
for each candidate:
    cache_get(...)  ← 반복 금지!
    docs_search(...)  ← 반복 금지!
```

## 툴 호출 규칙 (필수!)

## 🚨 도구 호출 규칙 (필수!)

### 병렬 호출 허용 (성능 최적화)

- 읽기 도구(get_table_description, search_nodes 등)는 **병렬 호출 가능**
- 서로 다른 테이블/키워드 조회 시 동시 호출하면 빠름

### 🚫 중복 호출 금지 (핵심!)

- **동일한 도구 + 동일한 인자로 2번 호출 절대 금지**
- 한 번 호출한 결과는 재사용

### ❌ 잘못된 예 (중복 호출)
```
get_table_description(ORDER_HEADER)  // 1번째
get_table_description(ORDER_HEADER)  // 2번째 - 금지! 같은 인자
```

### ✅ 올바른 예 (병렬 + 중복 없음)
```
// 서로 다른 테이블이므로 병렬 OK
get_table_description(ORDER_HEADER)
get_table_description(ORDER_ITEM)
```

### 쓰기 도구는 순차 권장
- create_table_schema, link_candidates_to_tables 등은 순차 처리
- 트랜잭션 안정성을 위해

## 🚨 JSON 생성 규칙 (필수!)

도구에 JSON 인자를 전달할 때 **반드시 유효한 JSON**이어야 합니다.

### JSON 필수 규칙

1. **문자열은 반드시 큰따옴표** (`"`, 작은따옴표 `'` 금지)
2. **속성명도 반드시 큰따옴표로 감싸기**
3. **마지막 쉼표 금지** (trailing comma 금지)
4. **null, true, false는 소문자**
5. **숫자에 따옴표 금지** (0.9, 100 등)

### ✅ 올바른 JSON 예시

```json
[
  {"name": "order_id", "dtype": "id-ne", "nullable": true, "pk_constraint": true},
  {"name": "order_date", "dtype": "date-time", "nullable": false}
]
```

### ❌ 잘못된 JSON 예시

```
[
  {'name': 'order_id'},           // 작은따옴표 사용 금지!
  {name: "order_id"},             // 속성명 따옴표 누락!
  {"name": "order_id",},          // 마지막 쉼표!
  {"nullable": True},             // True/False 대문자 금지!
]
```

## 처리 흐름

1. **cache_get으로 캐시 조회** (hint → artifact_key 변환 후)
2. **miss면 docs에서 테이블 정보 추출** (외부 문서만!)
3. **cache_set으로 추출 결과 저장** (다음 후보를 위해!)
4. **TABLE 노드 MERGE** (DBMS 표준 PK)
5. **COLUMN 노드 MERGE** (fqn 기준)
6. **FK 관계 생성** (FK_TO_TABLE + FK_TO)
7. **FROM/WRITES 관계 생성** (무조건!)
8. **set_status_success 호출**
"""


def create_runtime_resolution_agent(
    neo4j_driver: Any,
    docs_root: str,
    ddl_root: str,
    runs_root: str = "data/runs",
    skills_root: Optional[str] = None,
    memory_root: Optional[str] = None,
    min_confidence: float = 0.7,
    enable_hitl: bool = True,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> tuple[Any, Dict[str, Any], Any]:
    """런타임 해석 에이전트 생성 (공식 deepagents API 사용)
    
    Args:
        neo4j_driver: Neo4j 드라이버
        docs_root: 외부 설정 파일 루트 (읽기 전용)
        ddl_root: DDL 파일 루트 (읽기 전용)
        runs_root: 작업 워크스페이스 루트 (읽기/쓰기)
        skills_root: 스킬 파일 루트 (기본: framework/deepagents/resources/skills)
        memory_root: 메모리 파일 루트 (기본: framework/deepagents/resources/memory)
        min_confidence: 최소 신뢰도
        enable_hitl: Human-in-the-Loop 활성화 여부
        api_key: LLM API 키 (기본: settings.llm.api_key)
        model: 모델명 (기본: settings.llm.model)
            - OpenAI: "gpt-5.2", "gpt-4.1" 등
            - Anthropic: "claude-sonnet-4-5-20250929" 등
    
    Returns:
        (agent, safety_instances, store) 튜플
        - agent: DeepAgent 인스턴스
        - safety_instances: 안전장치 인스턴스들 (evidence_mgr, commit_lock, review_queue, schema_mgr)
        - store: InMemoryStore 인스턴스 (장기 기억용)
    """
    # 경로 설정
    if skills_root is None:
        current_file = Path(__file__).resolve()
        skills_root = str(current_file.parent / "resources" / "skills")
    
    if memory_root is None:
        current_file = Path(__file__).resolve()
        memory_root = str(current_file.parent / "resources" / "memory")
    
    # 경로 절대화
    docs_root = os.path.abspath(docs_root)
    ddl_root = os.path.abspath(ddl_root)
    runs_root = os.path.abspath(runs_root)
    skills_root = os.path.abspath(skills_root)
    memory_root = os.path.abspath(memory_root)
    
    os.makedirs(runs_root, exist_ok=True)
    
    # 모델 설정 (API 키 환경변수 설정 포함)
    if api_key:
        os.environ["OPENAI_API_KEY"] = api_key
    model_str = model or os.getenv("DEFAULT_MODEL", "gpt-4o")
    
    # 커스텀 도구 생성
    neo4j_tools = create_neo4j_tools(neo4j_driver)
    parser_tools = create_parser_tools()
    
    # 안전장치 도구 생성 (플랜 기반 업데이트)
    safety_tools = create_safety_tools(neo4j_driver, runs_root, ddl_root, docs_root)
    evidence_tools, lock_tools, ddl_tools, docs_tools_list, cache_tools, safety_instances = safety_tools
    
    # 서브에이전트 생성 (안전장치 도구 포함)
    subagents = create_subagents(neo4j_tools, parser_tools, safety_tools)
    
    # 장기 기억용 Store (InMemoryStore - 프로덕션에서는 PostgresStore 사용)
    store = InMemoryStore()
    
    # CompositeBackend 설정
    # - /docs/ → FilesystemBackend (읽기 전용, docs 폴더)
    # - /ddl/ → FilesystemBackend (읽기 전용, ddl 폴더)
    # - /skills/ → FilesystemBackend (읽기 전용, skills 폴더)
    # - /memory/ → StateBackend (현재 미사용 - 모든 지침은 SYSTEM_PROMPT에 통합됨)
    # - /memories/ → StoreBackend (장기 기억, 영구 저장)
    # - 기본 → StateBackend (작업용)
    def create_backend(runtime):
        # FilesystemBackend를 PathNormalizingFilesystemBackend로 래핑
        # - 경로 정규화: 백슬래시→슬래시 자동 변환
        # - glob 버그 우회: Python 기본 glob.glob() 직접 사용
        # - list 버그 우회: Python 기본 os.listdir() 직접 사용
        docs_backend = PathNormalizingFilesystemBackend(
            FilesystemBackend(root_dir=docs_root, virtual_mode=False),
            root_dir=docs_root,
            virtual_prefix="/docs/"
        )
        ddl_backend = PathNormalizingFilesystemBackend(
            FilesystemBackend(root_dir=ddl_root, virtual_mode=False),
            root_dir=ddl_root,
            virtual_prefix="/ddl/"
        )
        skills_backend = PathNormalizingFilesystemBackend(
            FilesystemBackend(root_dir=skills_root, virtual_mode=False),
            root_dir=skills_root,
            virtual_prefix="/skills/"
        )
        
        return CompositeBackend(
            default=StateBackend(runtime),
            routes={
                "/docs/": docs_backend,
                "/ddl/": ddl_backend,
                "/skills/": skills_backend,
                "/memory/": StateBackend(runtime),  # 읽기+쓰기 (세션 중 업데이트 가능)
                "/memories/": StoreBackend(runtime),  # 장기 기억 (영구 저장)
            }
        )
    
    # 체크포인터 (상태 저장용)
    checkpointer = MemorySaver()
    
    # Human-in-the-Loop 설정 (플랜 기반)
    # - apply_graph_mutations: 그래프 변경 전 human 확인
    # - 읽기/조회 도구는 허용
    interrupt_on = None
    if enable_hitl:
        interrupt_on = {
            "apply_graph_mutations": True,   # 그래프 변경 전 human 확인
            "set_status_success": False,     # 상태 업데이트는 허용
            "set_status_review": False,      # 리뷰 상태 업데이트는 허용
            "set_status_failed": False,      # 실패 상태 업데이트는 허용
            "query_neo4j": False,            # 읽기는 허용
        }
    
    # 모든 도구 합치기 (플랜 기반: review_tools 제거, docs_tools 추가)
    # Note: todo는 deepagents built-in write_todos 사용 (TodoListMiddleware 자동 주입)
    all_tools = (
        neo4j_tools + 
        parser_tools + 
        evidence_tools + 
        lock_tools + 
        ddl_tools +
        docs_tools_list +
        cache_tools   # cache_get, cache_set 도구 추가
    )
    
    # 에이전트 생성
    # memory 삭제: SYSTEM_PROMPT에 모든 지침 통합됨
    agent = create_deep_agent(
        model=model_str,
        system_prompt=SYSTEM_PROMPT,
        tools=all_tools,
        subagents=subagents,
        backend=create_backend,
        checkpointer=checkpointer,
        store=store,
        skills=["/skills/"],
        memory=[],  # 레거시 AGENTS.md 제거 - SYSTEM_PROMPT에 통합
        name="runtime-resolution-agent",
        interrupt_on=interrupt_on,
    )
    
    return agent, safety_instances, store


# =============================================================================
# 편의 함수
# =============================================================================

def scan_docs_structure(docs_root: str, max_depth: int = 3) -> str:
    """docs 폴더 구조를 스캔해서 플랫한 파일 목록으로 반환
    
    Args:
        docs_root: docs 폴더 경로 (상대/절대)
        max_depth: 최대 탐색 깊이
    
    Returns:
        사용 가능한 파일 경로 목록 (LLM이 바로 사용할 수 있는 형식)
    """
    import os
    
    # 절대 경로로 변환
    docs_root = os.path.abspath(docs_root)
    
    if not os.path.exists(docs_root):
        return f"(docs 폴더가 존재하지 않습니다: {docs_root})"
    
    # 폴더와 파일 목록
    folders = []
    files = []
    
    for root, dirs, filenames in os.walk(docs_root):
        # 상대 경로 계산
        rel_path = os.path.relpath(root, docs_root)
        depth = 0 if rel_path == "." else rel_path.count(os.sep) + 1
        
        # 최대 깊이 제한
        if depth > max_depth:
            dirs.clear()
            continue
        
        # 현재 폴더의 POSIX 경로
        current_path = "" if rel_path == "." else rel_path.replace("\\", "/")
        
        # 폴더 경로 저장
        if current_path:
            folders.append(f"/docs/{current_path}/")
        
        # 파일 경로 저장 (XML, YAML만)
        for file_name in filenames:
            if file_name.endswith(('.xml', '.XML', '.yaml', '.yml')):
                file_path = f"{current_path}/{file_name}" if current_path else file_name
                files.append(f"/docs/{file_path}")
    
    if not folders and not files:
        return "(docs 폴더가 비어 있습니다)"
    
    # 출력 구성
    lines = ["사용 가능한 경로:"]
    lines.append("")
    lines.append("폴더:")
    for folder in sorted(folders):
        lines.append(f"  {folder}")
    lines.append("")
    lines.append("파일:")
    for f in sorted(files):
        lines.append(f"  {f}")
    
    return "\n".join(lines)


def normalize_file_path(file_path: str, prefix: str = "/docs/") -> str:
    """파일 경로를 FilesystemBackend 기준으로 정규화
    
    Args:
        file_path: 원본 파일 경로 (상대 경로 또는 절대 경로)
        prefix: 경로 프리픽스 (기본값: "/docs/")
    
    Returns:
        정규화된 경로 (예: "/docs/SAMPLE/OrderService.kt")
    
    Examples:
        >>> normalize_file_path("SAMPLE/OrderService.kt")
        '/docs/SAMPLE/OrderService.kt'
        >>> normalize_file_path("/SAMPLE/OrderService.kt")
        '/docs/SAMPLE/OrderService.kt'
        >>> normalize_file_path("docs/SAMPLE/OrderService.kt")
        '/docs/SAMPLE/OrderService.kt'
        >>> normalize_file_path("/docs/SAMPLE/OrderService.kt")
        '/docs/SAMPLE/OrderService.kt'
    """
    if not file_path:
        return file_path
    
    # Windows 경로 구분자 정규화
    normalized = file_path.replace("\\", "/")
    
    # 선행 슬래시 제거
    normalized = normalized.lstrip("/")
    
    # prefix 제거 (이미 있는 경우)
    if normalized.startswith("docs/"):
        normalized = normalized[5:]  # "docs/" 제거
    
    # prefix 추가
    if normalized:
        return f"{prefix.rstrip('/')}/{normalized}"
    else:
        return prefix.rstrip("/")


async def process_chunk(
    agent,
    chunk: List[Dict[str, Any]],
    thread_id: str,
    chunk_id: int,
    docs_root: Optional[str] = None,
    safety_instances: Optional[Dict[str, Any]] = None,
    callbacks: Optional[List[Any]] = None,
    cached_artifacts: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """청크 단위 처리 (여러 후보를 한 번에 LLM에게 전달)
    
    🚨 핵심 변경: 노드별 개별 호출 → 청크 단위 일괄 호출
    - LLM 호출 횟수 감소 (청크당 1회)
    - 같은 파일의 노드들이 컨텍스트 공유
    - 비용 절감 + 일관성 향상
    
    Args:
        agent: DeepAgent 인스턴스
        chunk: 후보 목록 (청크 내 모든 후보)
        thread_id: 스레드 ID (run_id)
        chunk_id: 청크 ID (로깅용)
        docs_root: docs 폴더 절대 경로 (구조 스캔용)
        safety_instances: 안전장치 인스턴스들 (옵션)
        callbacks: 콜백 핸들러 목록 (옵션, ReportCollector 등)
        cached_artifacts: 청크 공통 탐색 결과 (캐시 공유용)
    
    Returns:
        처리 결과 (청크 전체에 대한 결과)
    """
    import json
    
    # run_id로 EvidenceManager 업데이트 (안전장치가 있는 경우)
    if safety_instances and "evidence_mgr" in safety_instances:
        evidence_mgr = safety_instances["evidence_mgr"]
        evidence_mgr.run_id = thread_id
        # 작업 디렉토리 재설정
        from pathlib import Path
        evidence_mgr.base_path = Path(evidence_mgr.runs_root) / thread_id / "evidence"
        evidence_mgr.base_path.mkdir(parents=True, exist_ok=True)
    
    # docs 폴더 구조 스캔 (미리 제공)
    docs_structure = "(docs_root 미설정)"
    if docs_root:
        docs_structure = scan_docs_structure(docs_root)
    
    # 캐시 정보 준비 (청크 공통 탐색 결과)
    cache_info = ""
    if cached_artifacts:
        cache_entries = []
        for artifact_key, data in cached_artifacts.items():
            tables = data.get("data", {}).get("tables", [])
            if tables:
                table_names = [t.get("name", "?") for t in tables if isinstance(t, dict)]
                cache_entries.append(f"  - {artifact_key}: {', '.join(table_names)}")
        if cache_entries:
            cache_info = "\n## 🔄 청크 공통 탐색 결과 (캐시됨 - 재탐색 불필요!)\n\n" + "\n".join(cache_entries)
    
    # 청크 내 후보 정보 구성
    candidates_info = []
    all_hints = set()
    all_search_tokens = set()
    
    for i, candidate in enumerate(chunk, 1):
        original_file_path = candidate.get('file_path', '')
        normalized_file_path = normalize_file_path(original_file_path) if original_file_path else ''
        
        hints = candidate.get('external_hints', [])
        search_tokens = candidate.get('search_tokens', [])
        all_hints.update(hints)
        all_search_tokens.update(search_tokens)
        
        element_id = candidate.get('element_id')
        candidates_info.append(f"""
### 🔹 후보 #{i} (set_status_success 시 이 ID 사용!)
- **🆔 Element ID**: `{element_id}` ← set_status_success에 이 값 사용!
- **File**: {normalized_file_path}:{candidate.get('line_start')}-{candidate.get('line_end')}
- **Hints**: {hints}
- **Missing Bindings**: {candidate.get('missing_bindings', [])}
- **Search Tokens**: {search_tokens}
- **Artifact Type**: {candidate.get('artifact_type', 'unknown')}
- **Confidence**: {candidate.get('confidence', 0.5)}
""")
    
    # 청크 공통 힌트/토큰 요약
    common_info = f"""
## 📊 청크 공통 정보
- **청크 ID**: {chunk_id}
- **후보 수**: {len(chunk)}개
- **공통 Hints**: {list(all_hints)[:10]}
- **공통 Search Tokens**: {list(all_search_tokens)[:15]}
"""
    
    # 메시지 구성 (청크 전체)
    message = f"""## 🚀 청크 #{chunk_id} 처리 요청 ({len(chunk)}개 후보)

아래 **{len(chunk)}개 후보를 한 번에** 처리하세요!
같은 파일/힌트를 공유하는 후보들은 docs 탐색을 공유하여 효율적으로 처리합니다.

{common_info}

## 📋 후보 목록 (총 {len(chunk)}개)
{''.join(candidates_info)}

## 📁 docs 폴더 구조 (실제 파일 목록)

```
{docs_structure}
```

⚠️ **중요**: 위 목록에 있는 경로만 접근 가능합니다!

## 📋 청크 처리 흐름 (🚨 일괄 처리!)

### 1️⃣ cache_get (청크 전체 힌트로 한 번 조회)
- hints: {list(all_hints)[:5]}
- search_tokens: {list(all_search_tokens)[:10]}

### 2️⃣ docs 탐색 (캐시 miss인 경우만)
- 같은 파일을 여러 후보가 참조하면 **한 번만 탐색**

### 3️⃣ 테이블/컬럼 추출 + cache_set

### 4️⃣ TABLE/COLUMN MERGE (청크 내 모든 테이블 일괄)

### 5️⃣ FROM/WRITES 관계 생성
- `link_candidates_to_tables` 사용
- candidate_element_ids: 청크 내 모든 element_id 배열
- table_names: 발견된 모든 테이블명 배열

### 6️⃣ Evidence 생성 + set_status_success (🚨 각 후보별 고유 ID!)

## 🚨🚨🚨 핵심 규칙 (필수!) 🚨🚨🚨

1. **일괄 처리**: cache_get/docs_search는 청크당 1회
2. **관계 일괄 생성**: link_candidates_to_tables로 모든 후보↔테이블 연결
3. **개별 완료 (중요!)**: set_status_success는 **각 후보의 고유 element_id**로 호출

## ❌ 중복 호출 절대 금지!

```
// 금지! 같은 element_id로 여러 번 호출
set_status_success(element_id='4:abc:123', ...)  // 1번
set_status_success(element_id='4:abc:123', ...)  // 2번 - 금지!

// 올바른 예: 각 후보의 고유 element_id 사용
set_status_success(element_id='4:abc:1', ...)   // 후보 #1
set_status_success(element_id='4:abc:2', ...)   // 후보 #2
set_status_success(element_id='4:abc:3', ...)   // 후보 #3
```
{cache_info}

## ⚠️ 경로 규칙
모든 파일시스템 도구는 /docs/로 시작, 슬래시(/)만 사용"""
    
    # 에이전트 실행 설정
    config = {"configurable": {"thread_id": thread_id}}
    if callbacks:
        config["callbacks"] = callbacks
    
    # 에이전트 실행
    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": message}]},
        config=config
    )
    
    return result


# 레거시 호환용 (deprecated - 향후 제거 예정)
async def process_candidate(
    agent,
    candidate: Dict[str, Any],
    thread_id: str,
    docs_root: Optional[str] = None,
    safety_instances: Optional[Dict[str, Any]] = None,
    callbacks: Optional[List[Any]] = None,
    cached_artifacts: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """단일 후보 처리 (deprecated - process_chunk 사용 권장)
    
    ⚠️ 이 함수는 레거시 호환용입니다.
    청크 단위 처리를 위해 process_chunk를 사용하세요.
    """
    import warnings
    warnings.warn(
        "process_candidate is deprecated. Use process_chunk for batch processing.",
        DeprecationWarning,
        stacklevel=2
    )
    
    # process_chunk로 위임 (단일 후보를 청크로 감싸서)
    return await process_chunk(
        agent=agent,
        chunk=[candidate],
        thread_id=thread_id,
        chunk_id=0,
        docs_root=docs_root,
        safety_instances=safety_instances,
        callbacks=callbacks,
        cached_artifacts=cached_artifacts,
    )


async def resume_after_hitl(
    agent,
    thread_id: str,
    approved: bool = True,
) -> Dict[str, Any]:
    """HITL 대기 후 재개
    
    Args:
        agent: DeepAgent 인스턴스
        thread_id: 스레드 ID
        approved: Human 승인 여부
    
    Returns:
        처리 결과
    """
    if approved:
        # 승인 → 계속 진행
        result = await agent.ainvoke(
            None,  # 이전 상태에서 재개
            config={"configurable": {"thread_id": thread_id}}
        )
    else:
        # 거부 → 중단 메시지
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": "Human이 커밋을 거부했습니다. 작업을 중단하세요."}]},
            config={"configurable": {"thread_id": thread_id}}
        )
    
    return result
