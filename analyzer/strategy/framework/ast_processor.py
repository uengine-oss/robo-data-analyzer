"""Framework 코드 분석기 - Java/Kotlin AST → Neo4j 그래프

클래스 다이어그램 생성에 필요한 정보를 추출합니다.

분석 파이프라인:
1. AST 수집 (StatementCollector)
2. 정적 그래프 생성 (CLASS, METHOD, FIELD 노드)
3. 상속/구현 관계 추출 (EXTENDS, IMPLEMENTS)
4. LLM 배치 분석 (요약, 메서드 콜 추출)
5. 클래스 요약 및 User Story 생성

리팩토링: BaseAstProcessor 상속으로 공통 로직 재사용
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Dict, List, Optional, Tuple, Set, Union

from config.settings import settings
from util.rule_loader import RuleLoader
# Exceptions: 모든 커스텀 예외는 RuntimeError로 대체됨
from util.text_utils import calculate_code_token, log_process

from analyzer.strategy.base.statement_node import StatementNode
from analyzer.strategy.base.batch import AnalysisBatch
from analyzer.strategy.base.processor import BaseAstProcessor


# ==================== 상수 정의 ====================
# 노드 타입 분류
NON_ANALYSIS_TYPES = frozenset(["FILE", "PACKAGE", "IMPORT"])
CLASS_TYPES = frozenset(["CLASS", "INTERFACE", "ENUM"])
FIELD_TYPES = frozenset(["FIELD"])
METHOD_TYPES = frozenset(["METHOD", "CONSTRUCTOR"])
VARIABLE_TYPES = frozenset(["VARIABLE"])

# 클래스 스켈레톤 생성에 포함할 노드 타입
SKELETON_KEEP_TYPES = frozenset(["FIELD", "CONSTRUCTOR", "METHOD", "NEW_INSTANCE", "METHOD_CALL"])

# ==================== 스테레오타입 정의 (표준화) ====================
# Neo4j 멀티 레이블로 사용되며, 역할 기반 조회 및 시각화에 활용됩니다.
#
# [클래스 스테레오타입]
#   Controller  - REST/MVC 컨트롤러 (HTTP 요청 처리)
#   Service     - 비즈니스 서비스 레이어
#   Repository  - 데이터 액세스 레이어 (DAO)
#   Entity      - JPA 엔티티 / 도메인 모델
#   DTO         - 데이터 전송 객체 (Request/Response 포함)
#   Config      - 설정 클래스
#   Component   - 범용 Spring 컴포넌트
#   Aspect      - AOP 관점
#   Filter      - 서블릿 필터 / 인터셉터
#   Listener    - 이벤트 리스너
#   Validator   - 검증 클래스
#   Mapper      - 객체 매핑 클래스
#   Util        - 유틸리티 / 헬퍼 클래스
#   ExceptionHandler - 예외 처리 클래스
#
# [메서드 스테레오타입]
#   Endpoint    - REST API 엔드포인트 (@XxxMapping)
#   Query       - 조회 메서드 (find/get/search/list/fetch)
#   Command     - 변경 메서드 (create/save/update/delete/remove)
#   Getter      - 단순 getter (getXxx)
#   Setter      - 단순 setter (setXxx)
#   Lifecycle   - 라이프사이클 메서드 (@PostConstruct, main)
#   Scheduled   - 스케줄 메서드 (@Scheduled)
#   EventHandler- 이벤트 핸들러 (@EventListener)
#   Init        - 초기화 메서드 (init/setup/configure)
#   Transactional - 트랜잭션 메서드 (@Transactional)

# 클래스 스테레오타입 감지 규칙: (어노테이션 패턴, 이름 패턴, 부모 타입 패턴) → 스테레오타입
CLASS_STEREOTYPE_RULES = [
    # 어노테이션 기반 (최우선)
    (r'@RestController|@Controller', None, None, "Controller"),
    (r'@Service', None, None, "Service"),
    (r'@Repository', None, None, "Repository"),
    (r'@Entity|@Table|@MappedSuperclass|@Embeddable', None, None, "Entity"),
    (r'@Configuration', None, None, "Config"),
    (r'@Component', None, None, "Component"),
    (r'@Aspect', None, None, "Aspect"),
    (r'@ControllerAdvice|@RestControllerAdvice', None, None, "ExceptionHandler"),
    # 상속/구현 기반
    (None, None, r'JpaRepository|CrudRepository|PagingAndSortingRepository|Repository', "Repository"),
    (None, None, r'Filter|OncePerRequestFilter|HandlerInterceptor', "Filter"),
    (None, None, r'ApplicationListener|EventListener', "Listener"),
    (None, None, r'Validator|ConstraintValidator', "Validator"),
    (None, None, r'Exception|RuntimeException|Error', "ExceptionHandler"),
    # 이름 패턴 기반 (fallback)
    (None, r'Controller$', None, "Controller"),
    (None, r'Service$|ServiceImpl$', None, "Service"),
    (None, r'Repository$|RepositoryImpl$|Dao$|DaoImpl$', None, "Repository"),
    (None, r'DTO$|Dto$|Request$|Response$|Command$|Event$', None, "DTO"),
    (None, r'Config$|Configuration$|Properties$', None, "Config"),
    (None, r'Mapper$|Converter$|Transformer$', None, "Mapper"),
    (None, r'Util$|Utils$|Helper$|Helpers$', None, "Util"),
    (None, r'Listener$|Handler$|EventHandler$', None, "Listener"),
    (None, r'Validator$', None, "Validator"),
    (None, r'Filter$|Interceptor$', None, "Filter"),
    (None, r'Aspect$', None, "Aspect"),
]

# 메서드 스테레오타입 감지 규칙: (어노테이션 패턴, 이름 패턴) → 스테레오타입
METHOD_STEREOTYPE_RULES = [
    # 어노테이션 기반 (최우선)
    (r'@GetMapping|@PostMapping|@PutMapping|@DeleteMapping|@PatchMapping|@RequestMapping', None, "Endpoint"),
    (r'@Scheduled', None, "Scheduled"),
    (r'@EventListener', None, "EventHandler"),
    (r'@PostConstruct|@PreDestroy', None, "Lifecycle"),
    (r'@Transactional', None, "Transactional"),
    # 이름 패턴 기반
    (None, r'^(find|search|list|fetch|count|exists|is|has|check)', "Query"),
    (None, r'^(create|save|insert|add|register|submit|post)', "Command"),
    (None, r'^(update|modify|change|patch|edit|put)', "Command"),
    (None, r'^(delete|remove|destroy|drop|cancel)', "Command"),
    (None, r'^get[A-Z]', "Getter"),
    (None, r'^set[A-Z]', "Setter"),
    (None, r'^(init|setup|configure|initialize|start|boot)', "Init"),
    (None, r'^(on[A-Z]|handle[A-Z])', "EventHandler"),
    (None, r'^main$', "Lifecycle"),
]

# 설정에서 가져오는 상수 (DBMS와 공통)
MAX_BATCH_TOKEN = settings.batch.max_batch_token
MAX_CONCURRENCY = settings.concurrency.max_concurrency
STATIC_QUERY_BATCH_SIZE = settings.batch.static_query_batch_size
MAX_SUMMARY_CHUNK_TOKEN = settings.batch.max_summary_chunk_token
MAX_CONTEXT_TOKEN = settings.batch.max_context_token
PARENT_EXPAND_THRESHOLD = settings.batch.parent_expand_threshold

# Java 표준 라이브러리 및 기본 타입 - 클래스 생성 제외 대상
JAVA_BUILTIN_TYPES = frozenset([
    # 기본 타입 및 래퍼
    "int", "long", "double", "float", "boolean", "char", "byte", "short", "void",
    "Integer", "Long", "Double", "Float", "Boolean", "Character", "Byte", "Short",
    # 기본 클래스
    "String", "Object", "Class", "Enum", "System", "Math", "Runtime",
    # 컬렉션
    "List", "ArrayList", "LinkedList", "Set", "HashSet", "TreeSet", "LinkedHashSet",
    "Map", "HashMap", "TreeMap", "LinkedHashMap", "ConcurrentHashMap",
    "Collection", "Collections", "Arrays", "Iterator", "Iterable",
    "Queue", "Deque", "Stack", "Vector", "PriorityQueue",
    # 유틸리티
    "Optional", "Stream", "Collectors", "Comparator", "Comparable",
    "Date", "Calendar", "LocalDate", "LocalTime", "LocalDateTime", "Instant",
    "UUID", "Random", "Scanner", "Pattern", "Matcher",
    # 예외
    "Exception", "RuntimeException", "Throwable", "Error",
    "IOException", "SQLException", "NullPointerException", "IllegalArgumentException",
    # I/O
    "File", "Path", "Files", "InputStream", "OutputStream", "Reader", "Writer",
    "BufferedReader", "BufferedWriter", "PrintWriter", "FileReader", "FileWriter",
    # 기타
    "StringBuilder", "StringBuffer", "BigDecimal", "BigInteger",
    "Logger", "Log", "LogFactory",
])

# 유틸리티/헬퍼 클래스 패턴 - USES 관계 생성 제외 대상
# 이 클래스들은 EXTERNAL_CLASS로 생성하지 않고, USES 관계에서도 제외
UTILITY_CLASS_PATTERNS = frozenset([
    # 로거
    "Debug", "Logger", "Log", "LogFactory", "LogManager",
    "IKesaLogger", "KesaLogHelper", "ILogger", "LogHelper",
    # 유틸리티/헬퍼
    "Utils", "Utility", "Utilities", "Helper", "Helpers",
    "StringUtils", "DateUtils", "NumberUtils", "CollectionUtils",
    "ObjectUtil", "StringUtil", "FileUtil", "IOUtil",
    # 설정/상수
    "Constants", "Config", "Configuration", "Settings", "Properties",
    # 검증/변환
    "Validator", "Validation", "Formatter", "Converter",
    "Assert", "Assertions", "Preconditions", "Check",
    # 팩토리/컨텍스트 (인프라)
    "ComponentFactory", "BeanFactory", "ObjectFactory",
    "ApplicationContext", "Context", "Environment",
    # 예외 처리
    "ExceptionThrower", "ErrorHandler", "ExceptionHandler",
    # SQL/DB 유틸
    "SqlMapper", "SqlSession", "JdbcTemplate",
    # 기타 인프라
    "TranCtxUtil", "TranDataConverterUtil", "MassDataHelper",
])


# ==================== 데이터 클래스 ====================
class ClassInfo:
    """클래스/인터페이스 정보"""
    __slots__ = ('key', 'name', 'kind', 'node_start', 'node_end', 'pending_nodes', 'finalized', 'package_name')
    
    def __init__(
        self,
        key: str,
        name: str,
        kind: str,
        node_start: int,
        node_end: int,
        pending_nodes: int = 0,
        finalized: bool = False,
        package_name: Optional[str] = None,
    ):
        self.key = key
        self.name = name
        self.kind = kind
        self.node_start = node_start
        self.node_end = node_end
        self.pending_nodes = pending_nodes
        self.finalized = finalized
        self.package_name = package_name  # 패키지명 (com.example.service)


# ==================== 헬퍼 함수 ====================
# 대소문자 무시 비교를 위한 유틸리티 패턴 소문자 셋
_UTILITY_PATTERNS_LOWER = frozenset(p.lower() for p in UTILITY_CLASS_PATTERNS)
_JAVA_BUILTIN_LOWER = frozenset(t.lower() for t in JAVA_BUILTIN_TYPES)

def _is_valid_class_name_for_calls(name: str) -> bool:
    """calls 관계 생성에 유효한 클래스명인지 검증.
    
    대소문자 무시 비교를 통해 'logger', 'Logger' 등을 모두 필터링합니다.
    """
    if not name:
        return False
    name_lower = name.lower()
    if name_lower in _JAVA_BUILTIN_LOWER:
        return False
    if name_lower in _UTILITY_PATTERNS_LOWER:
        return False
    if len(name) == 1:
        return False
    if name[0].islower() and len(name) <= 3:
        return False
    if name.islower() and len(name) <= 6:
        return False
    return True


# ==================== RuleLoader 헬퍼 ====================
def _rule_loader() -> RuleLoader:
    return RuleLoader(target_lang="framework")


def analyze_code(code: str, context: str, ranges: list, count: int, api_key: str, locale: str) -> Dict[str, Any]:
    """코드 범위별 분석"""
    inputs = {"code": code, "ranges": ranges, "count": count, "locale": locale}
    if context.strip():
        inputs["context"] = context
    return _rule_loader().execute(
        "analysis",
        inputs,
        api_key,
    )


def analyze_class_summary_only(summaries: dict, api_key: str, locale: str, previous_summary: str = "") -> Dict[str, Any]:
    """클래스 전체 요약 생성 (Summary만)."""
    return _rule_loader().execute(
        "class_summary_only",
        {"summaries": summaries, "locale": locale, "previous_summary": previous_summary},
        api_key,
    )


def analyze_class_user_story(summary: str, api_key: str, locale: str) -> Dict[str, Any]:
    """클래스 User Story + AC 생성."""
    return _rule_loader().execute(
        "class_user_story",
        {"summary": summary, "locale": locale},
        api_key,
    )


def analyze_node_context(
    skeleton_code: str, 
    ancestor_context: str, 
    sibling_code: str,
    api_key: str, 
    locale: str,
) -> Tuple[str, str]:
    """부모 스켈레톤 + 이전 형제 코드에서 컨텍스트를 추출합니다.
    
    Returns:
        (parent_context, sibling_context) 튜플
    """
    result = _rule_loader().execute(
        "node_context",
        {
            "skeleton_code": skeleton_code, 
            "ancestor_context": ancestor_context, 
            "sibling_code": sibling_code,
            "locale": locale,
        },
        api_key,
    )
    
    if not isinstance(result, dict):
        raise ValueError(f"node_context 규칙이 dict가 아닌 값을 반환했습니다: {type(result)}")
    
    parent_ctx = result.get("parent_context", "").strip()
    sibling_ctx = result.get("sibling_context", "").strip()
    
    if not parent_ctx:
        raise ValueError("node_context 규칙이 빈 parent_context를 반환했습니다")
    
    return parent_ctx, sibling_ctx


def analyze_class_relations(class_name: str, skeleton_code: str, api_key: str, locale: str) -> Dict[str, Any]:
    """클래스의 관계(Association, Composition, Dependency, Calls)를 분석합니다."""
    return _rule_loader().execute(
        "class_relations",
        {"class_name": class_name, "skeleton_code": skeleton_code, "locale": locale},
        api_key,
    )


def analyze_variable_summary(variables: List[Dict[str, Any]], api_key: str, locale: str) -> Dict[str, Any]:
    """변수 역할 목록을 하나의 요약 설명으로 통합합니다."""
    from util.rule_loader import RuleLoader
    # common 디렉토리의 프롬프트 사용
    loader = RuleLoader(target_lang="common")
    return loader.execute(
        "variable_summary",
        {"variables": variables, "locale": locale},
        api_key,
    )


# ==================== 노드 수집기 ====================
class StatementCollector:
    """AST를 후위순회하여 StatementNode와 클래스 정보를 수집합니다.
    
    코드 추출 우선순위:
    1. AST JSON의 code 속성 (PL/SQL 등 code가 포함된 경우)
    2. source_content에서 startLine/endLine으로 잘라서 추출 (Java 등)
    """

    def __init__(self, antlr_data: Dict[str, Any], directory: str, file_name: str,
                 source_content: str = ""):
        self.antlr_data = antlr_data
        self.directory = directory
        self.file_name = file_name
        self.nodes: List[StatementNode] = []
        self.classes: Dict[str, ClassInfo] = {}
        self._node_id = 0
        
        # 소스 파일 내용 및 라인 캐시
        self.source_content = source_content
        if source_content:
            self._source_lines = source_content.replace('\r\n', '\n').replace('\r', '\n').split('\n')
        else:
            self._source_lines = []

    def _extract_code_from_source(self, start_line: int, end_line: int) -> str:
        """소스 파일에서 startLine~endLine 범위의 코드를 추출합니다.
        
        Args:
            start_line: 시작 라인 번호 (1-based)
            end_line: 종료 라인 번호 (1-based, inclusive)
            
        Returns:
            '15: public class Foo {\\n16:   private int x;' 형태의 라인 번호 포함 코드
        """
        if not self._source_lines or start_line <= 0 or end_line <= 0:
            return ""
        
        # 범위 보정 (1-based → 0-based index)
        start_idx = max(0, start_line - 1)
        end_idx = min(len(self._source_lines), end_line)
        
        if start_idx >= end_idx:
            return ""
        
        parts = []
        for i in range(start_idx, end_idx):
            line_no = i + 1  # 1-based
            parts.append(f"{line_no}: {self._source_lines[i]}")
        
        return "\n".join(parts)

    def _parse_code_to_lines(self, code: str, start_line: int, end_line: int) -> List[Tuple[int, str]]:
        """JSON code 속성을 [(line_no, text), ...] 형태로 파싱합니다.
        
        AST JSON에 code가 없으면 소스 파일에서 추출합니다.
        
        Args:
            code: '1: public class...\n2: ...' 형태의 문자열 (빈 문자열 가능)
            start_line: 노드 시작 라인
            end_line: 노드 종료 라인
            
        Returns:
            [(line_no, text), ...] 형태의 튜플 리스트
        """
        # code가 없으면 소스 파일에서 추출 시도
        if not code and self._source_lines:
            code = self._extract_code_from_source(start_line, end_line)
        
        if not code:
            return []
        
        # \r\n 또는 \n으로 분리
        lines = code.replace('\r\n', '\n').split('\n')
        parsed_lines: List[Tuple[int, str]] = []
        
        for line in lines:
            if not line:
                continue
            # '123: text' 형태 파싱
            match = re.match(r'^(\d+):\s?(.*)', line)
            if match:
                line_no = int(match.group(1))
                text = match.group(2)
                parsed_lines.append((line_no, text))
            else:
                # 매칭 실패 시 전체 라인을 텍스트로 (fallback)
                if parsed_lines:
                    last_no = parsed_lines[-1][0]
                    parsed_lines.append((last_no + 1, line))
                else:
                    parsed_lines.append((start_line, line))
        
        return parsed_lines

    def collect(self) -> Tuple[List[StatementNode], Dict[str, ClassInfo]]:
        """AST 전역을 후위 순회하여 노드 목록과 클래스 정보를 생성합니다."""
        self._visit(self.antlr_data, None, None, None, None, None, False)
        return self.nodes, self.classes

    def _make_class_key(self, package_name: Optional[str], class_name: Optional[str], start_line: int) -> str:
        """클래스 고유키를 생성합니다 (패키지 + 클래스명으로 유일성 보장)."""
        base = class_name or f"anonymous_{start_line}"
        if package_name:
            # 패키지명 + 클래스명으로 고유키 생성
            return f"{package_name}.{base}"
        return f"{self.directory}:{self.file_name}:{base}:{start_line}"
    
    def _extract_package_name(self, node: Dict[str, Any]) -> Optional[str]:
        """PACKAGE 노드에서 패키지명을 추출합니다."""
        # 1. AST JSON에서 name 속성 사용
        if node.get('name'):
            return node['name']
        
        # 2. AST JSON에서 code 속성에서 추출
        code = node.get('code', '')
        if code:
            match = re.search(r'\bpackage\s+([\w.]+)', code)
            if match:
                return match.group(1)
        
        # 3. _source_lines에서 라인 번호로 추출 (fallback)
        start_line = node.get('startLine', 0)
        if self._source_lines and 0 < start_line <= len(self._source_lines):
            line_content = self._source_lines[start_line - 1]  # 0-indexed
            match = re.search(r'\bpackage\s+([\w.]+)', line_content)
            if match:
                return match.group(1)
        
        return None

    def _extract_class_name(self, code: str, node_type: str) -> Optional[str]:
        """코드에서 클래스/인터페이스 이름을 추출합니다."""
        patterns = {
            "CLASS": r"\bclass\s+(\w+)",
            "INTERFACE": r"\binterface\s+(\w+)",
            "ENUM": r"\benum\s+(\w+)",
            "RECORD": r"\brecord\s+(\w+)",
            "ANNOTATION_TYPE": r"@interface\s+(\w+)",
        }
        pattern = patterns.get(node_type)
        if pattern:
            match = re.search(pattern, code, re.IGNORECASE)
            if match:
                return match.group(1)
        return None

    def _collect_method_calls(
        self,
        children: List[Dict[str, Any]],
        class_key: Optional[str],
        class_name: Optional[str],
        class_kind: Optional[str],
        package_name: Optional[str],
        parent_method_line: Optional[int] = None,
    ) -> List[StatementNode]:
        """메서드 내부에서 호출 관계와 클래스 참조를 추출합니다.
        
        수집 대상:
        - METHOD_CALL: 직접적인 메서드 호출
        - VARIABLE: 변수 선언 (타입 정보에서 클래스 참조 추출)
        - RETURN: 반환문 (메서드 호출 포함 가능)
        - EXPRESSION: 표현식 (메서드 호출 포함 가능)
        """
        result: List[StatementNode] = []
        
        # 클래스 참조를 추출할 노드 타입들
        REFERENCE_TYPES = {'VARIABLE', 'RETURN', 'EXPRESSION', 'ASSIGNMENT', 'FIELD_ACCESS'}
        
        for child in children:
            child_type = child.get('type', '')
            
            # VARIABLE, RETURN 등에서 클래스 참조 추출
            if child_type in REFERENCE_TYPES:
                start_line = child.get('startLine', 0)
                end_line = child.get('endLine', start_line)
                
                # 소스에서 코드 추출
                if self._source_lines and 0 < start_line <= len(self._source_lines):
                    code_lines = []
                    for ln in range(start_line, min(end_line + 1, len(self._source_lines) + 1)):
                        if 0 < ln <= len(self._source_lines):
                            code_lines.append((ln, self._source_lines[ln - 1]))
                    code = "\n".join(f"{ln}: {txt}" for ln, txt in code_lines)
                    
                    # 클래스 참조 추출
                    class_refs = self._extract_class_references(code)
                    
                    if class_refs:
                        # 호출 대상도 추출 시도
                        caller_class, method_name = self._extract_call_target(code, start_line)
                        
                        self._node_id += 1
                        ref_node = StatementNode(
                            node_id=self._node_id,
                            start_line=start_line,
                            end_line=end_line,
                            node_type="CLASS_REFERENCE",  # 클래스 참조 노드
                            code=code,
                            token=calculate_code_token(code),
                            has_children=False,
                            analyzable=False,
                            unit_key=class_key,
                            unit_name=class_name,
                            unit_kind=class_kind,
                            package_name=package_name,
                            call_target_class=caller_class,
                            call_target_method=method_name,
                            class_references=class_refs,
                            lines=code_lines,
                        )
                        ref_node.parent_method_line = parent_method_line
                        ref_node.summary = f"References: {', '.join(class_refs)}"
                        ref_node.completion_event.set()
                        result.append(ref_node)
                        self.nodes.append(ref_node)
            
            if child_type == 'METHOD_CALL':
                # METHOD_CALL 노드 수집
                start_line = child.get('startLine', 0)
                end_line = child.get('endLine', start_line)
                
                # 소스에서 코드 추출
                code_lines = []
                if self._source_lines and 0 < start_line <= len(self._source_lines):
                    for ln in range(start_line, min(end_line + 1, len(self._source_lines) + 1)):
                        if 0 < ln <= len(self._source_lines):
                            code_lines.append((ln, self._source_lines[ln - 1]))
                code = "\n".join(f"{ln}: {txt}" for ln, txt in code_lines)
                
                # 호출 대상 추출 (예: logger.debug, SqlMapper.getSqlMapClient)
                caller_class, method_name = self._extract_call_target(code, start_line)
                
                # 코드에서 참조되는 모든 클래스명 추출
                class_refs = self._extract_class_references(code)
                
                self._node_id += 1
                call_node = StatementNode(
                    node_id=self._node_id,
                    start_line=start_line,
                    end_line=end_line,
                    node_type="METHOD_CALL",
                    code=code,
                    token=calculate_code_token(code),
                    has_children=False,
                    analyzable=False,  # METHOD_CALL 자체는 LLM 분석 불필요
                    unit_key=class_key,
                    unit_name=class_name,
                    unit_kind=class_kind,
                    package_name=package_name,
                    # 호출 대상 정보 저장
                    call_target_class=caller_class,
                    call_target_method=method_name,
                    class_references=class_refs if class_refs else None,
                    lines=code_lines,
                )
                # 소속 메서드 라인 정보 저장 (관계 생성 시 사용)
                call_node.parent_method_line = parent_method_line
                call_node.summary = f"Call: {caller_class or '?'}.{method_name or '?'}()"
                call_node.completion_event.set()
                result.append(call_node)
                self.nodes.append(call_node)
            
            # 재귀적으로 자식 탐색 (METHOD_CALL은 중첩된 구조에 있을 수 있음)
            nested_calls = self._collect_method_calls(
                child.get('children', []),
                class_key, class_name, class_kind, package_name,
                parent_method_line
            )
            result.extend(nested_calls)
        
        return result

    def _extract_call_target(self, code: str, line_no: int) -> Tuple[Optional[str], Optional[str]]:
        """코드에서 메서드 호출 대상을 추출합니다.
        
        예: 
        - logger.debug("msg") -> ("logger", "debug")
        - SqlMapper.getSqlMapClient() -> ("SqlMapper", "getSqlMapClient")
        - someMethod() -> (None, "someMethod")
        
        Returns:
            (호출대상클래스/변수, 메서드명) 튜플
        """
        # 라인 번호 제거
        clean_code = re.sub(r'^\d+:\s*', '', code.strip())
        
        # 패턴: variable.method(...) 또는 Class.method(...)
        match = re.search(r'(\b[A-Za-z_]\w*)\s*\.\s*([A-Za-z_]\w*)\s*\(', clean_code)
        if match:
            return (match.group(1), match.group(2))
        
        # 패턴: method(...) - 클래스 없이 직접 호출
        match = re.search(r'\b([A-Za-z_]\w*)\s*\(', clean_code)
        if match:
            method_name = match.group(1)
            # Java 키워드 제외
            if method_name not in ('if', 'while', 'for', 'switch', 'catch', 'return', 'throw', 'new'):
                return (None, method_name)
        
        return (None, None)
    
    def _extract_class_references(self, code: str) -> List[str]:
        """코드에서 참조되는 모든 클래스명을 추출합니다.
        
        예:
        - IPCReferenceInformation iPCRef = ... -> ["IPCReferenceInformation"]
        - ComponentFactory.getInstance(PCReferenceInformation.class) -> ["ComponentFactory", "PCReferenceInformation"]
        - (IPCReferenceInformation)... -> ["IPCReferenceInformation"]
        """
        # 라인 번호 제거
        clean_code = re.sub(r'^\d+:\s*', '', code.strip())
        
        classes: Set[str] = set()
        
        # 1. 타입 선언: TypeName variableName = ...
        for match in re.finditer(r'\b([A-Z][A-Za-z0-9_]*)\s+[a-z]\w*\s*[=;,)]', clean_code):
            classes.add(match.group(1))
        
        # 2. 캐스팅: (TypeName)
        for match in re.finditer(r'\(\s*([A-Z][A-Za-z0-9_]*)\s*\)', clean_code):
            classes.add(match.group(1))
        
        # 3. .class 참조: TypeName.class
        for match in re.finditer(r'\b([A-Z][A-Za-z0-9_]*)\.class\b', clean_code):
            classes.add(match.group(1))
        
        # 4. 정적 메서드 호출: TypeName.methodName(...)
        for match in re.finditer(r'\b([A-Z][A-Za-z0-9_]*)\s*\.\s*[a-z]\w*\s*\(', clean_code):
            classes.add(match.group(1))
        
        # 5. 제네릭 타입: List<TypeName>, Map<K, TypeName>
        for match in re.finditer(r'<\s*([A-Z][A-Za-z0-9_]*)\s*[,>]', clean_code):
            classes.add(match.group(1))
        
        # 6. new 연산자: new TypeName(...)
        for match in re.finditer(r'\bnew\s+([A-Z][A-Za-z0-9_]*)\s*[(<]', clean_code):
            classes.add(match.group(1))
        
        # 7. 상속/구현: extends/implements TypeName
        for match in re.finditer(r'\b(?:extends|implements)\s+([A-Z][A-Za-z0-9_]*)', clean_code):
            classes.add(match.group(1))
        
        # 기본 Java 타입 및 일반적인 타입 제외
        java_types = {
            'String', 'Integer', 'Long', 'Double', 'Float', 'Boolean', 'Byte', 'Short', 'Character',
            'Object', 'Class', 'Void', 'Number', 'Throwable', 'Exception', 'Error', 'RuntimeException',
            'List', 'ArrayList', 'LinkedList', 'Set', 'HashSet', 'TreeSet', 'Map', 'HashMap', 'TreeMap',
            'Collection', 'Iterator', 'Iterable', 'Comparable', 'Comparator', 'Optional',
            'Date', 'Calendar', 'Timestamp', 'BigDecimal', 'BigInteger',
            'StringBuilder', 'StringBuffer', 'Thread', 'Runnable', 'Callable', 'Future',
        }
        
        return [c for c in classes if c not in java_types]

    def _visit(
        self,
        node: Dict[str, Any],
        parent: Optional[StatementNode],
        current_class: Optional[str],
        current_class_name: Optional[str],
        current_class_kind: Optional[str],
        current_package: Optional[str],
        in_method: bool,
    ) -> Optional[StatementNode]:
        """재귀적으로 AST를 내려가며 StatementNode를 생성합니다.
        
        Args:
            in_method: 현재 노드가 메서드 내부에 있는지 여부 (Java: 메서드 내부 분석 제외용)
        """
        node_type = node["type"]
        start_line = node["startLine"]
        end_line = node["endLine"]
        children = node.get("children", []) or []

        # AST JSON의 code 속성에서 라인 정보 추출
        raw_code = node.get('code', '')
        line_entries = self._parse_code_to_lines(raw_code, start_line, end_line)
        
        # code가 없으면 _source_lines에서 라인 번호로 추출 (fallback)
        if not line_entries and self._source_lines and start_line > 0:
            for ln in range(start_line, min(end_line + 1, len(self._source_lines) + 1)):
                if 0 < ln <= len(self._source_lines):
                    line_entries.append((ln, self._source_lines[ln - 1]))
        
        code = "\n".join(f"{ln}: {txt}" for ln, txt in line_entries)

        class_key = current_class
        class_name = current_class_name
        class_kind = current_class_kind
        package_name = current_package

        # PACKAGE 노드에서 패키지명 추출
        if node_type == "PACKAGE":
            extracted_pkg = self._extract_package_name(node)
            if extracted_pkg:
                package_name = extracted_pkg
            log_process("ANALYZE", "COLLECT", f"📦 패키지 발견: {package_name}")

        # 클래스/인터페이스 노드 처리
        if node_type in CLASS_TYPES:
            # JSON에서 name 직접 추출 (정규식 추출보다 정확)
            name_from_json = node.get('name')
            if name_from_json:
                extracted_name = name_from_json
            else:
                # fallback: 기존 정규식 추출 (deprecated)
                extracted_name = self._extract_class_name(code, node_type)
            
            # EXTENDS/IMPLEMENTS 자식 노드에서 상속/구현 정보 추출
            extends_type = node.get('extendsType')  # AST 속성에서 먼저 시도
            implements_types = node.get('implementsTypes')  # AST 속성에서 먼저 시도
            
            # ANTLR 파서는 EXTENDS/IMPLEMENTS를 자식 노드로 생성할 수 있음
            for child in children:
                child_type = child.get('type')
                child_line = child.get('startLine', 0)
                
                if child_type == 'EXTENDS' and not extends_type:
                    # 소스에서 extends 대상 추출
                    if self._source_lines and 0 < child_line <= len(self._source_lines):
                        line_content = self._source_lines[child_line - 1]
                        match = re.search(r'\bextends\s+(\w+)', line_content)
                        if match:
                            extends_type = match.group(1)
                
                elif child_type == 'IMPLEMENTS' and not implements_types:
                    # 소스에서 implements 대상 추출
                    if self._source_lines and 0 < child_line <= len(self._source_lines):
                        line_content = self._source_lines[child_line - 1]
                        match = re.search(r'\bimplements\s+([\w,\s]+)', line_content)
                        if match:
                            implements_types = match.group(1).strip()
            
            # 패키지명 + 클래스명으로 고유키 생성 (클래스 유일성 보장)
            class_key = self._make_class_key(package_name, extracted_name, start_line)
            class_name = extracted_name
            class_kind = node_type
            if class_key not in self.classes:
                self.classes[class_key] = ClassInfo(
                    key=class_key,
                    name=extracted_name or class_key,
                    kind=node_type,
                    node_start=start_line,
                    node_end=end_line,
                    package_name=package_name,  # 패키지명 저장
                )
                log_process("ANALYZE", "COLLECT", f"📋 클래스 발견: {package_name}.{extracted_name} ({node_type}, 라인 {start_line}~{end_line})")
            
            # extends/implements 정보를 node에 임시 저장 (StatementNode 생성 시 사용)
            node['_extracted_extends'] = extends_type
            node['_extracted_implements'] = implements_types

        # 현재 노드가 METHOD인지 확인
        is_method_node = node_type in METHOD_TYPES
        
        # 자식 노드 수집 (package_name을 누적하며 전달)
        # ★ Java 메서드 내부 분석 최적화:
        #   - METHOD 본문의 일반 노드는 건너뛰되
        #   - METHOD_CALL은 수집하여 클래스 간 호출 관계 분석
        child_nodes: List[StatementNode] = []
        accumulated_package = package_name
        
        # 메서드 내부가 아닌 경우: 모든 자식 노드 순회
        # 메서드 내부인 경우 또는 현재 노드가 메서드인 경우: METHOD_CALL만 수집
        if not in_method and not is_method_node:
            # 일반 순회 (CLASS의 자식들: FIELD, METHOD 등)
            for ch in children:
                cn = self._visit(ch, None, class_key, class_name, class_kind, accumulated_package, False)
                if cn:
                    child_nodes.append(cn)
                    if cn.node_type == "PACKAGE" and cn.package_name:
                        accumulated_package = cn.package_name
        elif is_method_node or in_method:
            # 메서드 내부: METHOD_CALL 노드만 재귀적으로 수집
            # 현재 노드가 METHOD면 이 메서드의 line을 전달, 아니면 None
            method_line = start_line if is_method_node else None
            method_calls = self._collect_method_calls(children, class_key, class_name, class_kind, package_name, method_line)
            child_nodes.extend(method_calls)

        # 분석 가능 여부 판단 (Java: 메서드까지만 분석)
        # METHOD/CONSTRUCTOR 노드는 analyzable=True, 메서드 본문은 순회 자체를 건너뜀
        analyzable = (
            node_type not in NON_ANALYSIS_TYPES 
            and node_type not in CLASS_TYPES 
            and node_type not in FIELD_TYPES
        )
        token = calculate_code_token(code)

        self._node_id += 1
        st = StatementNode(
            node_id=self._node_id,
            start_line=start_line,
            end_line=end_line,
            node_type=node_type,
            code=code,
            token=token,
            has_children=bool(child_nodes),
            analyzable=analyzable,
            # 통합 필드
            unit_key=class_key,
            unit_name=class_name,
            unit_kind=class_kind,
            # Framework 전용 필드
            package_name=package_name,
            # AST JSON 메타데이터 (선택적)
            signature=node.get('signature'),
            modifiers=node.get('modifiers'),
            return_type=node.get('returnType'),
            parameters=node.get('parameters'),
            generic_type=node.get('genericType'),
            extends_type=node.get('_extracted_extends') or node.get('extendsType'),
            implements_types=node.get('_extracted_implements') or node.get('implementsTypes'),
            field_type=node.get('fieldType'),
            lines=line_entries,
        )
        for c in child_nodes:
            c.parent = st
        st.children.extend(child_nodes)

        # 분석 대상 노드 카운트
        # analyzable=True인 노드는 배치에 포함되므로, completion_event는 배치 완료 시에만 설정
        # analyzable=False인 노드는 배치에 포함되지 않으므로, 수집 시 처리
        if not analyzable and node_type not in CLASS_TYPES:
            # 배치에 포함되지 않는 노드는 수집 시 summary + completion_event 설정
            st.summary = st.get_raw_code()
            st.completion_event.set()
        elif analyzable and class_key and class_key in self.classes:
            # 클래스에 속한 분석 대상 노드
            self.classes[class_key].pending_nodes += 1
        # else: analyzable=True이지만 class_key 없음
        # → 배치에서 LLM 분석 후 completion_event 설정됨

        self.nodes.append(st)
        log_process(
            "ANALYZE",
            "COLLECT",
            f"✅ {node_type} 노드 수집 완료: 라인 {start_line}~{end_line}, 토큰 {token}, 자식 {len(child_nodes)}개",
        )
        return st


# ==================== AST 프로세서 본체 ====================
class FrameworkAstProcessor(BaseAstProcessor):
    """Framework AST 처리 및 LLM 분석 파이프라인
    
    BaseAstProcessor를 상속하여 공통 파이프라인 재사용.
    Framework 전용 로직만 구현.
    """

    def __init__(
        self,
        antlr_data: dict,
        directory: str,
        file_name: str,
        api_key: str,
        locale: str,
        last_line: int,
        source_content: str = "",
    ):
        """Framework Analyzer 초기화
        
        source_content: 원본 소스 파일 내용. AST JSON에 code 속성이 없을 때
        startLine/endLine으로 코드를 추출하는 fallback으로 사용.
        """
        super().__init__(
            antlr_data=antlr_data,
            directory=directory,
            file_name=file_name,
            api_key=api_key,
            locale=locale,
            last_line=last_line,
            source_content=source_content,
        )
        
        # 필드 타입 캐시
        self._field_type_cache: Optional[Dict[str, Dict[str, str]]] = None
        
        # 변수 역할 맵: {unit_key: {선언라인: [역할1, 역할2, ...]}}
        self._variable_roles: Dict[str, Dict[int, List[str]]] = {}

    # =========================================================================
    # BaseAstProcessor 추상 메서드 구현
    # =========================================================================
    
    def _collect_nodes(self) -> Tuple[List[StatementNode], Dict[str, ClassInfo]]:
        """AST 수집"""
        collector = StatementCollector(
            self.antlr_data, self.directory, self.file_name,
            source_content=self.source_content,
        )
        nodes, classes = collector.collect()
        
        # 필드 타입 캐시 초기화 및 AST 속성에서 직접 구축
        self._field_type_cache = {key: {} for key in classes} if classes else {}
        self._build_field_type_cache_from_ast(nodes)
        
        return nodes, classes
    
    def _build_field_type_cache_from_ast(self, nodes: List[StatementNode]) -> None:
        """AST의 fieldType 속성에서 필드 타입 캐시를 직접 구축합니다.
        
        LLM 호출 없이 AST 파싱 결과를 활용하여 객체명→클래스명 매핑을 생성합니다.
        이 매핑은 CALLS 관계 생성 시 객체명으로부터 실제 클래스를 찾는 데 사용됩니다.
        """
        if not self._field_type_cache:
            return
        
        for node in nodes:
            if node.node_type not in FIELD_TYPES:
                continue
            if not node.unit_key or node.unit_key not in self._field_type_cache:
                continue
            
            # AST에서 필드명과 타입 추출
            field_name = self._extract_field_name_from_code(node.code, node)
            field_type_raw = node.field_type  # AST의 fieldType 속성 사용
            
            if field_name and field_type_raw:
                # 제네릭 타입에서 기본 클래스명 추출 (List<Order> → Order)
                target_class = self._extract_class_from_type(field_type_raw)
                if target_class and _is_valid_class_name_for_calls(target_class):
                    self._field_type_cache[node.unit_key][field_name] = target_class
                    log_process("ANALYZE", "FIELD_CACHE", 
                               f"   📝 필드 캐시: {field_name} → {target_class} (클래스: {node.unit_name})")
    
    def _extract_field_name_from_code(self, code: str, node: StatementNode) -> Optional[str]:
        """코드에서 필드명을 추출합니다."""
        # 코드에서 라인 번호 제거
        lines = []
        for line in code.split('\n'):
            match = re.match(r'^\d+:\s?(.*)', line)
            if match:
                lines.append(match.group(1))
            else:
                lines.append(line)
        clean_code = ' '.join(lines)
        
        # 필드 선언 패턴: [modifiers] Type fieldName [= value];
        # private OrderService orderService;
        # private final List<Order> orders = new ArrayList<>();
        patterns = [
            r'(?:private|protected|public|static|final|\s)+\s+[\w<>,\s\[\]]+\s+(\w+)\s*[;=]',
            r'(\w+)\s*[;=]',  # fallback: 단순 변수명
        ]
        
        for pattern in patterns:
            match = re.search(pattern, clean_code)
            if match:
                return match.group(1)
        
        return None
    
    def _extract_class_from_type(self, type_str: str) -> Optional[str]:
        """타입 문자열에서 클래스명을 추출합니다.
        
        예시:
        - List<Order> → Order
        - Map<String, OrderItem> → OrderItem (마지막 타입 파라미터)
        - Order → Order
        - OrderService → OrderService
        """
        if not type_str:
            return None
        
        # 기본 타입 및 래퍼 제외
        if type_str in JAVA_BUILTIN_TYPES:
            return None
        
        # 제네릭 타입 파라미터 추출
        generic_match = re.search(r'<(.+)>', type_str)
        if generic_match:
            params = generic_match.group(1)
            # 마지막 타입 파라미터 추출 (Map<K, V>에서 V)
            parts = [p.strip() for p in params.split(',')]
            for part in reversed(parts):
                # 중첩 제네릭 처리 (List<List<Order>>)
                inner_match = re.search(r'<(.+)>', part)
                if inner_match:
                    return self._extract_class_from_type(inner_match.group(1))
                if part not in JAVA_BUILTIN_TYPES and part[0].isupper():
                    return part
        
        # 배열 타입 (Order[])
        if type_str.endswith('[]'):
            base_type = type_str[:-2]
            if base_type not in JAVA_BUILTIN_TYPES:
                return base_type
            return None
        
        # 단순 클래스명
        if type_str[0].isupper() and type_str not in JAVA_BUILTIN_TYPES:
            return type_str
        
        return None
    
    def _resolve_class_from_object_name(
        self, 
        object_or_class: str, 
        unit_key: Optional[str]
    ) -> str:
        """객체명 또는 클래스명에서 실제 클래스명을 해석합니다.
        
        1. 필드 타입 캐시에서 객체명→클래스명 매핑을 찾음
           (orderService → OrderService)
        2. 없으면 원래 이름 반환 (이미 클래스명인 경우)
        
        Args:
            object_or_class: 객체명 또는 클래스명 (orderService 또는 OrderService)
            unit_key: 현재 클래스 키 (필드 캐시 조회용)
            
        Returns:
            해석된 클래스명
        """
        if not object_or_class:
            return object_or_class
        
        # 이미 대문자로 시작하면 클래스명으로 간주
        if object_or_class[0].isupper():
            return object_or_class
        
        # 필드 타입 캐시에서 조회
        if unit_key and self._field_type_cache and unit_key in self._field_type_cache:
            field_cache = self._field_type_cache[unit_key]
            if object_or_class in field_cache:
                resolved = field_cache[object_or_class]
                log_process("ANALYZE", "CALLS", 
                           f"   🔗 객체→클래스 매핑: {object_or_class} → {resolved}")
                return resolved
        
        # 캐시에 없으면 관례에 따라 첫 글자를 대문자로 변환 시도
        # orderService → OrderService
        conventional_class = object_or_class[0].upper() + object_or_class[1:]
        return conventional_class

    def _get_excluded_context_types(self) -> Set[str]:
        """컨텍스트 생성에서 제외할 노드 타입"""
        return CLASS_TYPES

    async def _extract_node_context(
        self, 
        skeleton_code: str, 
        ancestor_context: str,
        sibling_code: str,
    ) -> Tuple[str, str]:
        """부모 + 이전 형제 컨텍스트 추출"""
        return await asyncio.to_thread(
            analyze_node_context,
            skeleton_code,
            ancestor_context,
            sibling_code,
            self.api_key,
            self.locale,
        )

    @staticmethod
    def _strip_annotations(raw_lines: List[str]) -> str:
        """어노테이션 라인을 제거하고 순수 코드만 합칩니다.
        
        @Override, @GetMapping(...), @Column(nullable=false) 등 제거.
        여러 줄에 걸친 어노테이션도 처리합니다.
        """
        clean_lines = []
        in_annotation = False
        paren_depth = 0
        
        for line in raw_lines:
            stripped = line.strip()
            
            # 어노테이션 시작
            if stripped.startswith('@') and not in_annotation:
                # 괄호가 열리면 닫힐 때까지 어노테이션으로 간주
                paren_depth += stripped.count('(') - stripped.count(')')
                if paren_depth > 0:
                    in_annotation = True
                # 괄호가 없거나 한 줄에서 닫히면 그냥 스킵
                continue
            
            if in_annotation:
                paren_depth += stripped.count('(') - stripped.count(')')
                if paren_depth <= 0:
                    in_annotation = False
                    paren_depth = 0
                continue
            
            clean_lines.append(line)
        
        return ' '.join(clean_lines)

    @staticmethod
    def _extract_method_name_from_signature(signature: str) -> Optional[str]:
        """메서드 시그니처에서 메서드 이름을 추출합니다.
        
        Args:
            signature: 메서드 시그니처 (예: "public void methodName(String param)")
        Returns:
            메서드 이름 (예: "methodName")
        """
        if not signature:
            return None
        
        # 메서드 이름 추출: 괄호 앞의 단어
        # 패턴: [modifiers] [returnType] methodName(params)
        match = re.search(r'(\w+)\s*\(', signature)
        if match:
            return match.group(1)
        return None

    @staticmethod
    def _extract_node_name_from_code(code: str, node_type: str) -> Optional[str]:
        """코드에서 노드의 실제 이름(메서드명, 필드명, 생성자명 등)을 추출합니다.
        
        Args:
            code: 라인번호 포함 소스 코드 (예: "23: public void createOrder(...)")
            node_type: AST 노드 타입 (METHOD, FIELD, CONSTRUCTOR 등)
        Returns:
            추출된 이름 또는 None
        """
        if not code:
            return None
        
        # 라인 번호 제거한 순수 코드 라인
        raw_lines = []
        for line in code.split('\n'):
            m = re.match(r'^\d+:\s?(.*)', line)
            raw_lines.append(m.group(1) if m else line)
        
        if node_type in ("METHOD", "METHOD_SIGNATURE"):
            # 어노테이션 라인 제거 후 메서드 시그니처에서 이름 추출
            clean = FrameworkAstProcessor._strip_annotations(raw_lines)
            # Java 메서드: 접근제어자/제네릭/리턴타입 뒤에 메서드명(
            # 패턴: 접근제어자? static? <T>? 리턴타입 메서드명(
            match = re.search(
                r'(?:public|private|protected|static|final|synchronized|abstract|native|default|\s|<[^>]*>)*'
                r'\b(\w+(?:<[^>]*>)?)\s+(\w+)\s*\(',
                clean
            )
            if match:
                method_name = match.group(2)
                keywords = {'if', 'for', 'while', 'switch', 'catch', 'return', 'new', 'class', 'interface', 'enum'}
                if method_name not in keywords:
                    return method_name
            # fallback: '(' 바로 앞 식별자 (어노테이션 제거 후)
            match = re.search(r'\b(\w+)\s*\(', clean)
            if match:
                name = match.group(1)
                keywords = {'if', 'for', 'while', 'switch', 'catch', 'return', 'new', 'class', 'interface', 'enum'}
                if name not in keywords:
                    return name
        
        elif node_type == "CONSTRUCTOR":
            clean = FrameworkAstProcessor._strip_annotations(raw_lines)
            # 생성자: 클래스명(
            match = re.search(r'\b([A-Z]\w*)\s*\(', clean)
            if match:
                return match.group(1)
        
        elif node_type == "FIELD":
            # 어노테이션 제거 후 필드 선언에서 이름 추출
            clean = FrameworkAstProcessor._strip_annotations(raw_lines)
            if not clean.strip():
                return None
            
            # Java 필드 패턴: (접근제어자) (static) (final) Type fieldName (= ...)?;
            # "private final CustomerRepository customerRepository;" → customerRepository
            match = re.search(
                r'(?:public|private|protected|static|final|volatile|transient|\s)*'
                r'\b(\w+(?:<[^>]*>)?(?:\[\])?)\s+(\w+)\s*[;=,)]',
                clean
            )
            if match:
                field_name = match.group(2)
                # Java 키워드 제외
                keywords = {'class', 'interface', 'enum', 'extends', 'implements', 'new', 'return'}
                if field_name not in keywords:
                    return field_name
        
        elif node_type == "VARIABLE":
            clean = FrameworkAstProcessor._strip_annotations(raw_lines)
            match = re.search(
                r'\b(\w+(?:<[^>]*>)?(?:\[\])?)\s+(\w+)\s*[;=)]',
                clean
            )
            if match:
                return match.group(2)
        
        elif node_type == "ENUM":
            raw = ' '.join(raw_lines)
            match = re.search(r'\benum\s+(\w+)', raw, re.IGNORECASE)
            if match:
                return match.group(1)
        
        return None

    # =========================================================================
    # 스테레오타입 감지
    # =========================================================================

    def _get_annotation_context(self, start_line: int) -> str:
        """노드 시작 위치 바로 위의 어노테이션 라인들을 가져옵니다.
        
        ANTLR이 CLASS 노드에 어노테이션을 포함하지 않을 수 있으므로,
        소스 파일에서 시작 라인 위의 어노테이션을 직접 읽습니다.
        """
        if not self.source_content:
            return ""
        lines = self.source_content.replace('\r\n', '\n').split('\n')
        annotation_lines = []
        idx = start_line - 2  # 0-based, 시작 라인 바로 위부터
        while idx >= 0:
            stripped = lines[idx].strip()
            if stripped.startswith('@') or stripped.startswith('//') or not stripped:
                annotation_lines.insert(0, stripped)
                idx -= 1
            elif stripped.endswith(')'):
                # 여러 줄 어노테이션의 중간일 수 있음
                annotation_lines.insert(0, stripped)
                idx -= 1
            else:
                break
        return '\n'.join(annotation_lines)

    def _detect_class_stereotype(self, node: StatementNode) -> List[str]:
        """클래스/인터페이스 노드의 스테레오타입을 감지합니다.
        
        Returns:
            감지된 스테레오타입 리스트 (예: ["Controller"], ["Entity", "DTO"])
        """
        stereotypes: List[str] = []
        seen: Set[str] = set()
        
        # 코드 + 어노테이션 컨텍스트
        code = node.code or ""
        annotation_ctx = self._get_annotation_context(node.start_line)
        full_context = annotation_ctx + "\n" + code
        
        class_name = node.unit_name or ""
        extends = node.extends_type or ""
        implements = node.implements_types or ""
        parent_types = extends + " " + implements
        
        for ann_pattern, name_pattern, parent_pattern, stereotype in CLASS_STEREOTYPE_RULES:
            if stereotype in seen:
                continue
            
            matched = False
            if ann_pattern and re.search(ann_pattern, full_context):
                matched = True
            elif name_pattern and class_name and re.search(name_pattern, class_name):
                matched = True
            elif parent_pattern and parent_types.strip() and re.search(parent_pattern, parent_types):
                matched = True
            
            if matched:
                stereotypes.append(stereotype)
                seen.add(stereotype)
        
        return stereotypes

    def _detect_method_stereotype(self, node: StatementNode, method_name: str) -> List[str]:
        """메서드 노드의 스테레오타입을 감지합니다.
        
        Returns:
            감지된 스테레오타입 리스트 (예: ["Endpoint", "Command"])
        """
        stereotypes: List[str] = []
        seen: Set[str] = set()
        
        # 코드 + 어노테이션 컨텍스트
        code = node.code or ""
        annotation_ctx = self._get_annotation_context(node.start_line)
        full_context = annotation_ctx + "\n" + code
        
        for ann_pattern, name_pattern, stereotype in METHOD_STEREOTYPE_RULES:
            if stereotype in seen:
                continue
            
            matched = False
            if ann_pattern and re.search(ann_pattern, full_context):
                matched = True
            elif name_pattern and method_name and re.search(name_pattern, method_name):
                matched = True
            
            if matched:
                stereotypes.append(stereotype)
                seen.add(stereotype)
        
        return stereotypes

    # =========================================================================
    # 정적 그래프 쿼리 생성
    # =========================================================================

    def _build_static_node_queries(self, node: StatementNode) -> List[Union[str, Dict[str, Any]]]:
        """정적 노드 생성 쿼리 리스트를 반환합니다 (파라미터화).
        
        스테레오타입 감지:
        - CLASS/INTERFACE 노드에 역할 레이블 추가 (Controller, Service, Entity 등)
        - METHOD 노드에 역할 레이블 추가 (Endpoint, Query, Command 등)
        """
        queries: List[Union[str, Dict[str, Any]]] = []
        label = node.node_type
        
        # PACKAGE 노드는 건너뛰기 (패키지 계층은 CLASS 노드 처리 시 _build_package_hierarchy_queries에서 생성)
        if label == "PACKAGE":
            return queries
        
        # name 속성 결정: 실제 식별자 이름 추출 우선
        if label == "FILE":
            node_name = self.file_name
        elif label in CLASS_TYPES and node.unit_name:
            node_name = node.unit_name
        elif label in METHOD_TYPES:
            # METHOD/CONSTRUCTOR: signature에서 메서드 이름 추출
            if node.signature:
                # "public void methodName(params)" -> "methodName"
                extracted = self._extract_method_name_from_signature(node.signature)
                node_name = extracted if extracted else f"{label}[{node.start_line}]"
            else:
                # 코드에서 추출 시도
                extracted = self._extract_node_name_from_code(node.code, label)
                node_name = extracted if extracted else f"{label}[{node.start_line}]"
        else:
            # 코드에서 실제 이름 추출 시도
            extracted = self._extract_node_name_from_code(node.code, label)
            if extracted:
                node_name = extracted
            else:
                node_name = f"{label}[{node.start_line}]"
        
        # 스테레오타입 감지
        stereotypes: List[str] = []
        if label in CLASS_TYPES:
            stereotypes = self._detect_class_stereotype(node)
        elif label in METHOD_TYPES:
            stereotypes = self._detect_method_stereotype(node, node_name)
        
        # 기본 파라미터
        params: Dict[str, Any] = {
            "start_line": node.start_line,
            "end_line": node.end_line,
            "directory": self.full_directory,
            "file_name": self.file_name,
            "node_name": node_name,
            "node_code": node.code,
            "token": node.token,
            "has_children": node.has_children,
        }

        # 동적 SET 절 구성
        set_parts = [
            "__cy_n__.endLine = $end_line",
            "__cy_n__.name = $node_name",
            "__cy_n__.node_code = $node_code",
            "__cy_n__.token = $token",
            "__cy_n__.has_children = $has_children",
        ]
        
        # 스테레오타입 레이블 추가 (Neo4j 멀티 레이블 + 속성)
        if stereotypes:
            set_parts.append("__cy_n__.stereotype = $stereotype")
            params["stereotype"] = stereotypes
            # 멀티 레이블: SET __cy_n__:Controller, __cy_n__:Service 등
            for st in stereotypes:
                set_parts.append(f"__cy_n__:{st}")
        
        # AST JSON 메타데이터 속성 추가 (있는 경우만)
        if node.signature:
            set_parts.append("__cy_n__.signature = $signature")
            params["signature"] = node.signature
        if node.modifiers:
            set_parts.append("__cy_n__.modifiers = $modifiers")
            params["modifiers"] = node.modifiers
        if node.return_type:
            set_parts.append("__cy_n__.returnType = $return_type")
            params["return_type"] = node.return_type
        if node.parameters:
            set_parts.append("__cy_n__.parameters = $parameters")
            params["parameters"] = node.parameters
        if node.generic_type:
            set_parts.append("__cy_n__.genericType = $generic_type")
            params["generic_type"] = node.generic_type
        if node.extends_type:
            set_parts.append("__cy_n__.extendsType = $extends_type")
            params["extends_type"] = node.extends_type
        if node.implements_types:
            set_parts.append("__cy_n__.implementsTypes = $implements_types")
            params["implements_types"] = node.implements_types
        if node.field_type:
            set_parts.append("__cy_n__.fieldType = $field_type")
            params["field_type"] = node.field_type
        
        # METHOD_CALL 노드: 호출 대상 정보 추가
        if label == "METHOD_CALL":
            if node.call_target_class:
                set_parts.append("__cy_n__.call_target_class = $call_target_class")
                params["call_target_class"] = node.call_target_class
            if node.call_target_method:
                set_parts.append("__cy_n__.call_target_method = $call_target_method")
                params["call_target_method"] = node.call_target_method
            if node.parent_method_line:
                set_parts.append("__cy_n__.parent_method_line = $parent_method_line")
                params["parent_method_line"] = node.parent_method_line
            if node.class_references:
                set_parts.append("__cy_n__.class_references = $class_references")
                params["class_references"] = node.class_references

        # CLASS/INTERFACE 등: class_name, package_name과 type 속성 추가
        if label in CLASS_TYPES and node.unit_name:
            set_parts.append("__cy_n__.class_name = $class_name")
            set_parts.append("__cy_n__.type = $class_type")
            params["class_name"] = node.unit_name
            params["class_type"] = label
            # 패키지명 추가
            if node.package_name:
                set_parts.append("__cy_n__.package_name = $package_name")
                params["package_name"] = node.package_name
                # FQN (Fully Qualified Name) 추가
                set_parts.append("__cy_n__.fqn = $fqn")
                params["fqn"] = f"{node.package_name}.{node.unit_name}"
        elif node.unit_name:
            set_parts.append("__cy_n__.class_name = $class_name")
            params["class_name"] = node.unit_name

        if node.has_children:
            # Framework용 preserve_types 설정 (메서드 시그니처 보존)
            preserve_types = METHOD_TYPES
            set_parts.append("__cy_n__.summarized_code = $summarized_code")
            params["summarized_code"] = node.get_placeholder_code(preserve_types)

        set_clause = ", ".join(set_parts)
        
        # CLASS/INTERFACE/ENUM 노드: package + class_name으로 MERGE (중복 방지)
        # 분석 범위 내 클래스는 :INTERNAL 레이블 추가
        if label in ("CLASS", "INTERFACE", "ENUM") and node.unit_name:
            if node.package_name:
                # 패키지명 + 클래스명으로 유일성 보장 + INTERNAL 레이블
                query = (
                    f"MERGE (__cy_n__:{label}:INTERNAL {{package_name: $package_name, class_name: $class_name}})\n"
                    f"SET __cy_n__.startLine = $start_line, __cy_n__.directory = $directory, __cy_n__.file_name = $file_name, {set_clause}\n"
                    f"RETURN __cy_n__"
                )
            else:
                # 패키지명이 없는 경우 기존 방식 (fallback) + INTERNAL 레이블
                query = (
                    f"MERGE (__cy_n__:{label}:INTERNAL {{class_name: $class_name}})\n"
                    f"SET __cy_n__.startLine = $start_line, __cy_n__.directory = $directory, __cy_n__.file_name = $file_name, {set_clause}\n"
                    f"RETURN __cy_n__"
                )
            queries.append({"query": query, "params": params})
            
            # 패키지 노드 계층 구조 생성 및 BELONGS_TO_PACKAGE 관계
            if node.package_name:
                package_queries = self._build_package_hierarchy_queries(node.package_name, node.unit_name, label)
                queries.extend(package_queries)
        else:
            query = (
                f"MERGE (__cy_n__:{label} {{startLine: $start_line, directory: $directory, file_name: $file_name}})\n"
                f"SET {set_clause}\n"
                f"RETURN __cy_n__"
            )
            queries.append({"query": query, "params": params})
        return queries
    
    def _build_package_hierarchy_queries(
        self, 
        package_name: str, 
        class_name: str, 
        class_type: str
    ) -> List[Dict[str, Any]]:
        """패키지 계층 구조 노드와 관계를 생성하는 쿼리를 반환합니다.
        
        예: com.kbstar.kee.dc 패키지의 경우
        - PACKAGE(name: "com")
        - PACKAGE(name: "kbstar", fqn: "com.kbstar") -[:CONTAINS_PACKAGE]-> PACKAGE(name: "com")
        - PACKAGE(name: "kee", fqn: "com.kbstar.kee") -[:CONTAINS_PACKAGE]-> ...
        - PACKAGE(name: "dc", fqn: "com.kbstar.kee.dc") -[:CONTAINS_PACKAGE]-> ...
        - CLASS -[:BELONGS_TO_PACKAGE]-> PACKAGE(fqn: "com.kbstar.kee.dc")
        """
        queries: List[Dict[str, Any]] = []
        parts = package_name.split(".")
        
        # 각 패키지 세그먼트에 대해 노드 생성
        for i in range(len(parts)):
            segment = parts[i]
            fqn = ".".join(parts[:i + 1])
            parent_fqn = ".".join(parts[:i]) if i > 0 else None
            
            # PACKAGE 노드 생성 (MERGE)
            queries.append({
                "query": (
                    "MERGE (__cy_pkg__:PACKAGE {fqn: $fqn})\n"
                    "SET __cy_pkg__.name = $segment\n"
                    "RETURN __cy_pkg__"
                ),
                "params": {
                    "fqn": fqn,
                    "segment": segment,
                }
            })
            
            # 부모 패키지와의 CONTAINS_PACKAGE 관계 생성
            if parent_fqn:
                queries.append({
                    "query": (
                        "MATCH (__cy_parent__:PACKAGE {fqn: $parent_fqn})\n"
                        "MATCH (__cy_child__:PACKAGE {fqn: $child_fqn})\n"
                        "MERGE (__cy_parent__)-[__cy_r__:CONTAINS_PACKAGE]->(__cy_child__)\n"
                        "RETURN __cy_r__"
                    ),
                    "params": {
                        "parent_fqn": parent_fqn,
                        "child_fqn": fqn,
                    }
                })
        
        # CLASS -[:BELONGS_TO_PACKAGE]-> PACKAGE 관계 생성
        queries.append({
            "query": (
                f"MATCH (__cy_class__:{class_type} {{package_name: $package_name, class_name: $class_name}})\n"
                "MATCH (__cy_pkg__:PACKAGE {fqn: $package_fqn})\n"
                "MERGE (__cy_class__)-[__cy_r__:BELONGS_TO_PACKAGE]->(__cy_pkg__)\n"
                "RETURN __cy_r__"
            ),
            "params": {
                "package_name": package_name,
                "class_name": class_name,
                "package_fqn": package_name,
            }
        })
        
        return queries

    def _build_relationship_queries(self) -> List[Union[str, Dict[str, Any]]]:
        """정적 관계 쿼리를 생성합니다 (파라미터화).
        
        생성 순서:
        1. 구조적 관계 (HAS_METHOD, HAS_FIELD, CONTAINS, PARENT_OF)
        2. 상속/구현 관계 (EXTENDS, IMPLEMENTS) - EXTERNAL_CLASS 포함
        3. 클래스 간 사용 관계 (CLASS → USES → CLASS)
        4. 메서드 레벨 사용 관계 (METHOD → USES → CLASS/INTERFACE)
        5. 후처리 (EXTERNAL_CLASS → 실제 CLASS/INTERFACE 연결)
        """
        queries: List[Union[str, Dict[str, Any]]] = []
        
        # 1. 구조적 관계 생성
        for node in self._nodes or []:
            if not node.parent:
                continue
            
            parent = node.parent
            
            # File → 최상위 타입만 CONTAINS
            if parent.node_type == "FILE" and node.node_type in CLASS_TYPES:
                queries.append(self._build_contains_query(parent, node))
            # Class → Method: HAS_METHOD
            elif node.node_type in METHOD_TYPES:
                queries.append(self._build_has_method_query(parent, node))
            # Class → Field: HAS_FIELD
            elif node.node_type in FIELD_TYPES:
                queries.append(self._build_has_field_query(parent, node))
            # 그 외: PARENT_OF
            else:
                queries.append(self._build_parent_of_query(parent, node))
        
        # 2. CLASS 노드의 상속/구현 관계 (AST 속성에서 직접, EXTERNAL_CLASS 포함)
        for node in self._nodes or []:
            if node.node_type in CLASS_TYPES:
                queries.extend(self._build_inheritance_from_ast(node))
        
        # 3. 클래스 간 호출 관계 생성 (METHOD_CALL 기반, CLASS → USES → CLASS)
        queries.extend(self._build_class_usage_queries())
        
        # 4. 메서드 레벨 사용 관계 생성 (METHOD → USES → CLASS/INTERFACE)
        queries.extend(self._build_method_uses_queries())
        
        # 5. 후처리: EXTERNAL 클래스와 분석 범위 내 INTERNAL CLASS/INTERFACE 매칭
        # EXTERNAL 레이블이 있는 노드를 실제 분석된 INTERNAL CLASS/INTERFACE로 연결
        # CLASS → USES → CLASS:EXTERNAL을 CLASS → USES → INTERNAL CLASS/INTERFACE로 변환
        queries.append({
            "query": (
                "MATCH (src:CLASS)-[r:USES]->(ext:EXTERNAL)\n"
                "MATCH (real:INTERNAL) WHERE (real:CLASS OR real:INTERFACE)\n"
                "  AND toLower(real.class_name) = toLower(ext.class_name)\n"
                "MERGE (src)-[:USES]->(real)\n"
                "DELETE r\n"
                "RETURN count(*) AS class_uses_resolved"
            ),
            "params": {}
        })
        
        # METHOD → USES → CLASS:EXTERNAL도 INTERNAL CLASS/INTERFACE로 변환
        queries.append({
            "query": (
                "MATCH (m:METHOD)-[r:USES]->(ext:EXTERNAL)\n"
                "MATCH (real:INTERNAL) WHERE (real:CLASS OR real:INTERFACE)\n"
                "  AND toLower(real.class_name) = toLower(ext.class_name)\n"
                "MERGE (m)-[:USES]->(real)\n"
                "DELETE r\n"
                "RETURN count(*) AS method_uses_resolved"
            ),
            "params": {}
        })
        
        # EXTENDS → CLASS:EXTERNAL도 INTERNAL CLASS로 변환 (있는 경우)
        queries.append({
            "query": (
                "MATCH (src)-[r:EXTENDS]->(ext:EXTERNAL)\n"
                "MATCH (real:CLASS:INTERNAL) WHERE toLower(real.class_name) = toLower(ext.class_name)\n"
                "MERGE (src)-[:EXTENDS]->(real)\n"
                "DELETE r\n"
                "RETURN count(*) AS extends_resolved"
            ),
            "params": {}
        })
        
        # IMPLEMENTS → INTERFACE:EXTERNAL도 INTERNAL INTERFACE로 변환 (있는 경우)
        queries.append({
            "query": (
                "MATCH (src)-[r:IMPLEMENTS]->(ext:EXTERNAL)\n"
                "MATCH (real:INTERFACE:INTERNAL) WHERE toLower(real.class_name) = toLower(ext.class_name)\n"
                "MERGE (src)-[:IMPLEMENTS]->(real)\n"
                "DELETE r\n"
                "RETURN count(*) AS implements_resolved"
            ),
            "params": {}
        })
        
        # 6. 분석 범위 내 클래스와 동일한 이름의 EXTERNAL 노드 정리
        # INTERNAL CLASS/INTERFACE가 존재하면 중복된 EXTERNAL 노드 삭제
        queries.append({
            "query": (
                "MATCH (ext:EXTERNAL)\n"
                "WHERE EXISTS { MATCH (real:INTERNAL) WHERE (real:CLASS OR real:INTERFACE)\n"
                "  AND toLower(real.class_name) = toLower(ext.class_name) }\n"
                "DETACH DELETE ext\n"
                "RETURN count(*) AS external_cleaned"
            ),
            "params": {}
        })
        
        return queries
    
    def _build_class_usage_queries(self) -> List[Dict[str, Any]]:
        """METHOD_CALL 노드를 기반으로 클래스 간 USES 관계를 생성합니다.
        
        생성되는 관계:
        1. METHOD -[:CALLS]-> METHOD_CALL (메서드가 어떤 호출을 하는지)
        2. CLASS -[:USES]-> CLASS (클래스 간 의존성, 상위 뷰)
        """
        queries: List[Dict[str, Any]] = []
        
        # 클래스별 호출 대상 집계 (중복 제거)
        class_usages: Dict[str, Set[str]] = {}  # {source_class: {target_class, ...}}
        
        for node in self._nodes or []:
            if node.node_type not in ("METHOD_CALL", "CLASS_REFERENCE"):
                continue
            
            call_target = node.call_target_class
            if not call_target:
                continue
            
            # 소속 METHOD 찾기 (저장된 parent_method_line 사용)
            parent_method_line = node.parent_method_line
            
            if parent_method_line:
                # METHOD → CALLS → METHOD_CALL 관계
                queries.append({
                    "query": (
                        f"MATCH (__cy_m__:METHOD {{startLine: $method_line, directory: $directory, file_name: $file_name}})\n"
                        f"MATCH (__cy_c__:METHOD_CALL {{startLine: $call_line, directory: $directory, file_name: $file_name}})\n"
                        f"MERGE (__cy_m__)-[__cy_r__:CALLS]->(__cy_c__)\n"
                        f"RETURN __cy_r__"
                    ),
                    "params": {
                        "method_line": parent_method_line,
                        "call_line": node.start_line,
                        "directory": self.full_directory,
                        "file_name": self.file_name,
                    }
                })
            
            # 소속 클래스 찾기
            source_class = node.unit_name
            if source_class:
                if source_class not in class_usages:
                    class_usages[source_class] = set()
                
                # call_target 추가
                if call_target:
                    class_usages[source_class].add(call_target)
                
                # class_references에서 추출된 클래스들도 추가
                if node.class_references:
                    for ref_class in node.class_references:
                        class_usages[source_class].add(ref_class)
        
        # CLASS → USES → CLASS 관계 (상위 뷰, 타깃 클래스가 분석 범위에 있는 경우만)
        # 타깃 클래스명이 분석 범위 내 클래스와 매칭되면 관계 생성
        analyzed_classes = {
            n.unit_name: n for n in (self._nodes or []) 
            if n.node_type in CLASS_TYPES and n.unit_name
        }
        
        for source_class, targets in class_usages.items():
            source_node = analyzed_classes.get(source_class)
            if not source_node:
                continue
            
            for target_class in targets:
                # 자기 호출 및 this 제외
                if target_class == source_class or target_class.lower() == 'this':
                    continue
                
                # 유틸리티/로거 클래스 제외
                if target_class in UTILITY_CLASS_PATTERNS or target_class in JAVA_BUILTIN_TYPES:
                    continue
                
                # 타깃이 분석 범위 내 클래스인지 확인 (대소문자 무시)
                target_node = None
                for cls_name, cls_node in analyzed_classes.items():
                    if cls_name and cls_name.lower() == target_class.lower():
                        target_node = cls_node
                        break
                
                if target_node:
                    # 분석 범위 내 클래스 간 관계
                    queries.append({
                        "query": (
                            f"MATCH (__cy_src__:{source_node.node_type} {{package_name: $src_pkg, class_name: $src_class}})\n"
                            f"MATCH (__cy_dst__:{target_node.node_type} {{package_name: $dst_pkg, class_name: $dst_class}})\n"
                            f"MERGE (__cy_src__)-[__cy_r__:USES]->(__cy_dst__)\n"
                            f"RETURN __cy_r__"
                        ),
                        "params": {
                            "src_pkg": source_node.package_name,
                            "src_class": source_node.unit_name,
                            "dst_pkg": target_node.package_name,
                            "dst_class": target_node.unit_name,
                        }
                    })
                else:
                    # 외부 라이브러리/클래스에 대한 호출 - CLASS:EXTERNAL 노드 생성 후 관계
                    queries.append({
                        "query": (
                            f"MERGE (__cy_ext__:CLASS:EXTERNAL {{class_name: $target_class}})\n"
                            f"SET __cy_ext__.name = $target_class\n"
                            f"RETURN __cy_ext__"
                        ),
                        "params": {"target_class": target_class}
                    })
                    queries.append({
                        "query": (
                            f"MATCH (__cy_src__:{source_node.node_type} {{package_name: $src_pkg, class_name: $src_class}})\n"
                            f"MATCH (__cy_ext__:CLASS:EXTERNAL {{class_name: $target_class}})\n"
                            f"MERGE (__cy_src__)-[__cy_r__:USES]->(__cy_ext__)\n"
                            f"RETURN __cy_r__"
                        ),
                        "params": {
                            "src_pkg": source_node.package_name,
                            "src_class": source_node.unit_name,
                            "target_class": target_class,
                        }
                    })
        
        return queries
    
    def _build_method_uses_queries(self) -> List[Dict[str, Any]]:
        """METHOD 노드에서 직접 USES 관계를 생성합니다.
        
        다이어그램 요구사항: doExecute:METHOD → USES → IPCReferenceInformation:INTERFACE
        
        생성되는 관계:
        - METHOD → USES → CLASS/INTERFACE (메서드가 직접 사용하는 클래스)
        - METHOD → USES → EXTERNAL_CLASS (분석 범위 외 클래스)
        
        유틸리티/로거 클래스는 제외됩니다.
        """
        queries: List[Dict[str, Any]] = []
        
        # 메서드별 클래스 참조 집계
        method_usages: Dict[int, Set[str]] = {}  # {method_start_line: {class_name, ...}}
        method_nodes: Dict[int, StatementNode] = {}  # {method_start_line: StatementNode}
        
        # METHOD 노드 수집
        for node in self._nodes or []:
            if node.node_type in METHOD_TYPES:
                method_nodes[node.start_line] = node
        
        # METHOD_CALL, CLASS_REFERENCE에서 클래스 참조 추출
        for node in self._nodes or []:
            if node.node_type not in ("METHOD_CALL", "CLASS_REFERENCE"):
                continue
            
            parent_method_line = node.parent_method_line
            if not parent_method_line:
                continue
            
            if parent_method_line not in method_usages:
                method_usages[parent_method_line] = set()
            
            # call_target_class 추가
            if node.call_target_class:
                target = node.call_target_class
                target_lower = target.lower()
                # 유틸리티/로거 제외, this 제외 (대소문자 무시)
                if target_lower != 'this' and target_lower not in _UTILITY_PATTERNS_LOWER:
                    if target_lower not in _JAVA_BUILTIN_LOWER:
                        method_usages[parent_method_line].add(target)
            
            # class_references 추가
            if node.class_references:
                for ref_class in node.class_references:
                    ref_lower = ref_class.lower()
                    # 유틸리티/로거 제외 (대소문자 무시)
                    if ref_lower not in _UTILITY_PATTERNS_LOWER and ref_lower not in _JAVA_BUILTIN_LOWER:
                        method_usages[parent_method_line].add(ref_class)
        
        # 분석 범위 내 CLASS/INTERFACE 수집
        analyzed_classes = {
            n.unit_name.lower(): n for n in (self._nodes or []) 
            if n.node_type in CLASS_TYPES and n.unit_name
        }
        
        # METHOD → USES → CLASS/INTERFACE/EXTERNAL_CLASS 관계 생성
        for method_line, target_classes in method_usages.items():
            method_node = method_nodes.get(method_line)
            if not method_node:
                continue
            
            for target_class in target_classes:
                # 자기 클래스 제외
                if method_node.unit_name and target_class.lower() == method_node.unit_name.lower():
                    continue
                
                # 분석 범위 내 클래스와 매칭
                target_node = analyzed_classes.get(target_class.lower())
                
                if target_node:
                    # 분석 범위 내 클래스/인터페이스에 대한 USES
                    queries.append({
                        "query": (
                            f"MATCH (__cy_m__:METHOD {{startLine: $method_line, directory: $directory, file_name: $file_name}})\n"
                            f"MATCH (__cy_dst__:{target_node.node_type} {{package_name: $dst_pkg, class_name: $dst_class}})\n"
                            f"MERGE (__cy_m__)-[__cy_r__:USES]->(__cy_dst__)\n"
                            f"RETURN __cy_r__"
                        ),
                        "params": {
                            "method_line": method_line,
                            "directory": self.full_directory,
                            "file_name": self.file_name,
                            "dst_pkg": target_node.package_name,
                            "dst_class": target_node.unit_name,
                        }
                    })
                    log_process("ANALYZE", "METHOD_USES", 
                               f"   🔗 METHOD USES: {method_node.unit_name}.method → {target_node.unit_name}")
                else:
                    # 외부 클래스에 대한 USES - CLASS:EXTERNAL 생성 후 연결
                    # CLASS:EXTERNAL 생성
                    queries.append({
                        "query": (
                            "MERGE (__cy_ext__:CLASS:EXTERNAL {class_name: $target_class})\n"
                            "SET __cy_ext__.name = $target_class\n"
                            "RETURN __cy_ext__"
                        ),
                        "params": {"target_class": target_class}
                    })
                    # METHOD → USES → CLASS:EXTERNAL
                    queries.append({
                        "query": (
                            f"MATCH (__cy_m__:METHOD {{startLine: $method_line, directory: $directory, file_name: $file_name}})\n"
                            f"MATCH (__cy_ext__:CLASS:EXTERNAL {{class_name: $target_class}})\n"
                            f"MERGE (__cy_m__)-[__cy_r__:USES]->(__cy_ext__)\n"
                            f"RETURN __cy_r__"
                        ),
                        "params": {
                            "method_line": method_line,
                            "directory": self.full_directory,
                            "file_name": self.file_name,
                            "target_class": target_class,
                        }
                    })
                    log_process("ANALYZE", "METHOD_USES", 
                               f"   🔗 METHOD USES (external): method → {target_class}")
        
        return queries
    
    def _build_has_method_query(self, parent: StatementNode, child: StatementNode) -> Dict[str, Any]:
        """HAS_METHOD 관계 쿼리 (파라미터화)"""
        # 부모가 패키지를 가진 클래스인 경우 package+class_name으로 매칭
        if parent.package_name and parent.unit_name:
            return {
                "query": (
                    f"MATCH (__cy_p__:{parent.node_type} {{package_name: $package_name, class_name: $class_name}})\n"
                    f"MATCH (__cy_c__:{child.node_type} {{startLine: $child_line, directory: $directory, file_name: $file_name}})\n"
                    f"MERGE (__cy_p__)-[__cy_r__:HAS_METHOD]->(__cy_c__)\n"
                    f"RETURN __cy_r__"
                ),
                "params": {
                    "package_name": parent.package_name,
                    "class_name": parent.unit_name,
                    "child_line": child.start_line,
                    "directory": self.full_directory,
                    "file_name": self.file_name,
                }
            }
        else:
            return {
                "query": (
                    f"MATCH (__cy_p__:{parent.node_type} {{startLine: $parent_line, directory: $directory, file_name: $file_name}})\n"
                    f"MATCH (__cy_c__:{child.node_type} {{startLine: $child_line, directory: $directory, file_name: $file_name}})\n"
                    f"MERGE (__cy_p__)-[__cy_r__:HAS_METHOD]->(__cy_c__)\n"
                    f"RETURN __cy_r__"
                ),
                "params": {
                    "parent_line": parent.start_line,
                    "child_line": child.start_line,
                    "directory": self.full_directory,
                    "file_name": self.file_name,
                }
            }
    
    def _build_has_field_query(self, parent: StatementNode, child: StatementNode) -> Dict[str, Any]:
        """HAS_FIELD 관계 쿼리 (파라미터화)"""
        # 부모가 패키지를 가진 클래스인 경우 package+class_name으로 매칭
        if parent.package_name and parent.unit_name:
            return {
                "query": (
                    f"MATCH (__cy_p__:{parent.node_type} {{package_name: $package_name, class_name: $class_name}})\n"
                    f"MATCH (__cy_c__:{child.node_type} {{startLine: $child_line, directory: $directory, file_name: $file_name}})\n"
                    f"MERGE (__cy_p__)-[__cy_r__:HAS_FIELD]->(__cy_c__)\n"
                    f"RETURN __cy_r__"
                ),
                "params": {
                    "package_name": parent.package_name,
                    "class_name": parent.unit_name,
                    "child_line": child.start_line,
                    "directory": self.full_directory,
                    "file_name": self.file_name,
                }
            }
        else:
            return {
                "query": (
                    f"MATCH (__cy_p__:{parent.node_type} {{startLine: $parent_line, directory: $directory, file_name: $file_name}})\n"
                    f"MATCH (__cy_c__:{child.node_type} {{startLine: $child_line, directory: $directory, file_name: $file_name}})\n"
                    f"MERGE (__cy_p__)-[__cy_r__:HAS_FIELD]->(__cy_c__)\n"
                    f"RETURN __cy_r__"
                ),
                "params": {
                    "parent_line": parent.start_line,
                    "child_line": child.start_line,
                    "directory": self.full_directory,
                    "file_name": self.file_name,
                }
            }
    
    async def _run_preprocessing(self) -> List[Union[str, Dict[str, Any]]]:
        """선행 처리: AST 속성에서 상속/구현 관계 생성 + LLM으로 클래스 관계 분석 (파라미터화)
        
        1. AST 속성에서 직접: EXTENDS, IMPLEMENTS
        2. LLM 분석 (class_relations): ASSOCIATION, COMPOSITION, DEPENDENCY, CALLS
        """
        queries: List[Union[str, Dict[str, Any]]] = []
        class_nodes: List[StatementNode] = []
        
        for node in self._nodes or []:
            # CLASS 노드의 상속/구현 관계 (AST 속성에서 직접)
            if node.node_type in CLASS_TYPES:
                queries.extend(self._build_inheritance_from_ast(node))
                class_nodes.append(node)
        
        # CLASS별 관계 분석 (LLM 호출)
        if class_nodes:
            relation_queries = await self._analyze_class_relations(class_nodes)
            queries.extend(relation_queries)
        
        log_process("ANALYZE", "PREPROCESS", 
                   f"✅ 선행 처리 완료: {len(queries)}개 관계 쿼리 생성")
        
        return queries
    
    async def _analyze_class_relations(self, class_nodes: List[StatementNode]) -> List[Union[str, Dict[str, Any]]]:
        """CLASS별 관계 분석 (class_relations.yaml LLM 호출)
        
        각 클래스의 스켈레톤을 생성하고 LLM으로 관계를 분석합니다.
        """
        queries: List[Union[str, Dict[str, Any]]] = []
        
        async def analyze_one(node: StatementNode) -> List[Union[str, Dict[str, Any]]]:
            if not node.unit_name:
                return []
            
            # 스켈레톤 생성
            skeleton = self._build_class_skeleton(node)
            if not skeleton.strip():
                return []
            
            log_process("ANALYZE", "CLASS_RELATIONS", 
                       f"🔍 클래스 관계 분석: {node.unit_name}")
            
            # LLM 호출
            result = await asyncio.to_thread(
                analyze_class_relations,
                node.unit_name,
                skeleton,
                self.api_key,
                self.locale,
            )
            
            if not isinstance(result, dict):
                return []
            
            return self._build_class_relations_queries(node, result)
        
        # 병렬 처리
        semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
        
        async def analyze_with_semaphore(node: StatementNode) -> List[Union[str, Dict[str, Any]]]:
            async with semaphore:
                return await analyze_one(node)
        
        results = await asyncio.gather(*[analyze_with_semaphore(n) for n in class_nodes])
        for r in results:
            queries.extend(r)
        
        return queries
    
    def _build_class_relations_queries(self, class_node: StatementNode, result: Dict[str, Any]) -> List[Union[str, Dict[str, Any]]]:
        """class_relations LLM 결과에서 관계 쿼리를 생성합니다 (파라미터화)."""
        queries: List[Union[str, Dict[str, Any]]] = []
        
        if not class_node.unit_name:
            return queries
        
        parent_kind = class_node.node_type
        parent_start = class_node.start_line
        
        # fieldRelations → ASSOCIATION/COMPOSITION 관계
        for rel in result.get("fieldRelations", []) or []:
            if not rel:
                continue
            field_name = rel.get("field", "")
            target_class = rel.get("targetClass", "")
            rel_type = rel.get("type", "ASSOCIATION")
            multiplicity = rel.get("multiplicity", "1")
            
            if not target_class or not _is_valid_class_name_for_calls(target_class):
                continue
            
            queries.append({
                "query": (
                    f"MATCH (__cy_src__:{parent_kind} {{startLine: $start_line, directory: $directory, file_name: $file_name}})\n"
                f"MATCH (__cy_dst__) WHERE (__cy_dst__:CLASS OR __cy_dst__:INTERFACE OR __cy_dst__:ENUM)\n"
                    f"  AND toLower(__cy_dst__.class_name) = toLower($target_class)\n"
                f"  AND __cy_src__ <> __cy_dst__\n"
                    f"MERGE (__cy_src__)-[__cy_r__:{rel_type} {{source_member: $field_name, multiplicity: $multiplicity}}]->(__cy_dst__)\n"
                f"RETURN __cy_r__"
                ),
                "params": {
                    "start_line": parent_start,
                    "directory": self.full_directory,
                    "file_name": self.file_name,
                    "target_class": target_class,
                    "field_name": field_name,
                    "multiplicity": multiplicity,
                }
            })
            
            log_process("ANALYZE", "FIELD_REL", 
                       f"   🔗 {rel_type}: {class_node.unit_name}.{field_name} → {target_class} [{multiplicity}]")
        
        # dependencies → DEPENDENCY 관계
        for dep in result.get("dependencies", []) or []:
            if not dep:
                continue
            target_class = dep.get("targetClass", "")
            source_member = dep.get("sourceMember", "unknown")
            usage = dep.get("usage", "local")
            
            if not target_class or not _is_valid_class_name_for_calls(target_class):
                continue
            
            # 이미 ASSOCIATION/COMPOSITION이 있으면 DEPENDENCY 생성 안 함
            queries.append({
                "query": (
                    f"MATCH (__cy_src__:{parent_kind} {{startLine: $start_line, directory: $directory, file_name: $file_name}})\n"
                f"MATCH (__cy_dst__) WHERE (__cy_dst__:CLASS OR __cy_dst__:INTERFACE OR __cy_dst__:ENUM)\n"
                    f"  AND toLower(__cy_dst__.class_name) = toLower($target_class)\n"
                f"  AND __cy_src__ <> __cy_dst__ AND NOT (__cy_src__)-[:ASSOCIATION|COMPOSITION]->(__cy_dst__)\n"
                    f"MERGE (__cy_src__)-[__cy_r__:DEPENDENCY {{usage: $usage, source_member: $source_member}}]->(__cy_dst__)\n"
                f"RETURN __cy_r__"
                ),
                "params": {
                    "start_line": parent_start,
                    "directory": self.full_directory,
                    "file_name": self.file_name,
                    "target_class": target_class,
                    "source_member": source_member,
                    "usage": usage,
                }
            })
        
        # calls → CALLS 관계
        for call in result.get("calls", []) or []:
            if not call:
                continue
            target = call.get("target", "")
            method = call.get("method", "")
            target_class = call.get("targetClass", "")
            
            # targetClass가 없으면 필드 캐시에서 조회 시도
            if not target_class and target:
                target_class = self._resolve_class_from_object_name(target, class_node.unit_key)
            
            if not target_class or not _is_valid_class_name_for_calls(target_class):
                continue
            
            queries.append({
                "query": (
                    f"MATCH (__cy_src__:{parent_kind} {{startLine: $start_line, directory: $directory, file_name: $file_name}})\n"
                f"MATCH (__cy_dst__) WHERE (__cy_dst__:CLASS OR __cy_dst__:INTERFACE OR __cy_dst__:ENUM)\n"
                    f"  AND toLower(__cy_dst__.class_name) = toLower($target_class)\n"
                    f"MERGE (__cy_src__)-[__cy_r__:CALLS {{method: $method}}]->(__cy_dst__)\n"
                f"RETURN __cy_r__"
                ),
                "params": {
                    "start_line": parent_start,
                    "directory": self.full_directory,
                    "file_name": self.file_name,
                    "target_class": target_class,
                    "method": method,
                }
            })
        
        return queries
    
    def _build_inheritance_from_ast(self, node: StatementNode) -> List[Union[str, Dict[str, Any]]]:
        """CLASS 노드의 AST 속성에서 상속/구현 관계 쿼리를 생성합니다 (파라미터화).
        
        AST JSON의 extendsType, implementsTypes 속성을 활용합니다.
        
        변경사항:
        - 분석 범위 내 클래스/인터페이스와 먼저 매칭 시도
        - 매칭 실패 시 EXTERNAL_CLASS 노드 생성 후 관계 연결
        - 유틸리티/로거 클래스는 제외
        """
        queries: List[Union[str, Dict[str, Any]]] = []
        
        if not node.unit_name:
            return queries
        
        # EXTENDS 관계 (단일 상속)
        if node.extends_type:
            target_class = node.extends_type.strip()
            if target_class and _is_valid_class_name_for_calls(target_class):
                # 유틸리티/로거 클래스 제외
                if target_class not in UTILITY_CLASS_PATTERNS:
                    # 1. 먼저 EXTERNAL:CLASS 노드 생성 (MERGE이므로 이미 존재하면 재사용)
                    # 다중 레이블: CLASS + EXTERNAL 스테레오타입
                    queries.append({
                        "query": (
                            "MERGE (__cy_ext__:CLASS:EXTERNAL {class_name: $target_class})\n"
                            "SET __cy_ext__.name = $target_class\n"
                            "RETURN __cy_ext__"
                        ),
                        "params": {"target_class": target_class}
                    })
                    
                    # 2. 소스 클래스에서 EXTERNAL CLASS로 EXTENDS 관계 생성
                    queries.append({
                        "query": (
                            f"MATCH (__cy_src__:{node.node_type} {{package_name: $package_name, class_name: $class_name}})\n"
                            f"MATCH (__cy_ext__:CLASS:EXTERNAL {{class_name: $target_class}})\n"
                            f"MERGE (__cy_src__)-[__cy_r__:EXTENDS]->(__cy_ext__)\n"
                            f"RETURN __cy_r__"
                        ),
                        "params": {
                            "package_name": node.package_name,
                            "class_name": node.unit_name,
                            "target_class": target_class,
                        }
                    })
                    log_process("ANALYZE", "INHERITANCE", 
                               f"   🔗 상속: {node.unit_name} extends {target_class} (EXTERNAL)")
        
        # IMPLEMENTS 관계 (다중 구현)
        if node.implements_types:
            # 콤마로 구분된 인터페이스 목록 파싱
            interfaces = [i.strip() for i in node.implements_types.split(',')]
            for iface in interfaces:
                if not iface or not _is_valid_class_name_for_calls(iface):
                    continue
                # 유틸리티/로거 클래스 제외
                if iface in UTILITY_CLASS_PATTERNS:
                    continue
                    
                # 분석 범위 내 INTERFACE와 직접 매칭 시도 (OPTIONAL MATCH 사용)
                # 매칭되면 해당 노드에 연결, 아니면 INTERFACE:EXTERNAL 생성
                queries.append({
                    "query": (
                        f"MATCH (__cy_src__:{node.node_type} {{package_name: $package_name, class_name: $class_name}})\n"
                        f"OPTIONAL MATCH (__cy_iface__:INTERFACE) WHERE toLower(__cy_iface__.class_name) = toLower($target_iface)\n"
                        f"WITH __cy_src__, __cy_iface__\n"
                        f"CALL {{\n"
                        f"  WITH __cy_src__, __cy_iface__\n"
                        f"  WITH __cy_src__, __cy_iface__ WHERE __cy_iface__ IS NOT NULL\n"
                        f"  MERGE (__cy_src__)-[:IMPLEMENTS]->(__cy_iface__)\n"
                        f"  RETURN 1 AS done\n"
                        f"  UNION ALL\n"
                        f"  WITH __cy_src__, __cy_iface__\n"
                        f"  WITH __cy_src__ WHERE __cy_iface__ IS NULL\n"
                        f"  MERGE (__cy_ext__:INTERFACE:EXTERNAL {{class_name: $target_iface}})\n"
                        f"  SET __cy_ext__.name = $target_iface\n"
                        f"  MERGE (__cy_src__)-[:IMPLEMENTS]->(__cy_ext__)\n"
                        f"  RETURN 1 AS done\n"
                        f"}}\n"
                        f"RETURN done"
                    ),
                    "params": {
                        "package_name": node.package_name,
                        "class_name": node.unit_name,
                        "target_iface": iface,
                    }
                })
                log_process("ANALYZE", "INHERITANCE", 
                           f"   🔗 구현: {node.unit_name} implements {iface}")
        
        return queries
    
    def _determine_multiplicity(self, type_str: str) -> str:
        """타입 문자열에서 다중성을 결정합니다.
        
        컬렉션 타입이면 '*', 아니면 '1'을 반환합니다.
        """
        collection_patterns = [
            r'List\s*<', r'Set\s*<', r'Collection\s*<', r'Map\s*<',
            r'ArrayList\s*<', r'HashSet\s*<', r'TreeSet\s*<',
            r'\[\]$',  # 배열
        ]
        
        for pattern in collection_patterns:
            if re.search(pattern, type_str):
                return '*'
        
        return '1'

    def _build_class_skeleton(self, class_node: StatementNode) -> str:
        """클래스 노드에서 관계 분석에 필요한 스켈레톤을 생성합니다.
        
        유지할 노드 타입:
        - FIELD: 필드 선언
        - CONSTRUCTOR: 생성자
        - METHOD: 시그니처만 (본문 제거)
        - NEW_INSTANCE: 객체 생성
        - METHOD_CALL: 메서드 호출
        
        제거할 것:
        - 메서드 본문 (IF, FOR, TRY 등)
        - 기타 불필요한 노드
        """
        skeleton_lines: List[str] = []
        
        def collect_skeleton(node: StatementNode, depth: int = 0) -> None:
            indent = "  " * depth
            node_type = node.node_type
            
            if node_type in SKELETON_KEEP_TYPES:
                if node_type == "METHOD":
                    # METHOD는 시그니처만 포함 (본문 제외)
                    if node.signature:
                        skeleton_lines.append(f"{indent}[METHOD] {node.signature}")
                    else:
                        # fallback: 첫 번째 라인만
                        first_line = node.code.split('\n')[0] if node.code else ""
                        skeleton_lines.append(f"{indent}[METHOD] {first_line}")
                elif node_type == "CONSTRUCTOR":
                    if node.signature:
                        skeleton_lines.append(f"{indent}[CONSTRUCTOR] {node.signature}")
                    else:
                        first_line = node.code.split('\n')[0] if node.code else ""
                        skeleton_lines.append(f"{indent}[CONSTRUCTOR] {first_line}")
                elif node_type == "FIELD":
                    # 필드: 전체 선언
                    clean_code = self._clean_code_lines(node.code)
                    skeleton_lines.append(f"{indent}[FIELD] {clean_code}")
                elif node_type == "NEW_INSTANCE":
                    clean_code = self._clean_code_lines(node.code)
                    skeleton_lines.append(f"{indent}[NEW_INSTANCE] {clean_code}")
                elif node_type == "METHOD_CALL":
                    clean_code = self._clean_code_lines(node.code)
                    skeleton_lines.append(f"{indent}[METHOD_CALL] {clean_code}")
            
            # 자식 노드 순회 (METHOD 본문은 제외하되 CONSTRUCTOR 내부는 포함)
            if node_type not in METHOD_TYPES or node_type == "CONSTRUCTOR":
                for child in node.children:
                    collect_skeleton(child, depth + 1 if node_type in SKELETON_KEEP_TYPES else depth)
        
        # 클래스 노드의 직접 자식들 순회
        for child in class_node.children:
            collect_skeleton(child, 0)
        
        return "\n".join(skeleton_lines)
    
    def _clean_code_lines(self, code: str) -> str:
        """코드에서 라인 번호를 제거하고 한 줄로 정리합니다."""
        if not code:
            return ""
        lines = []
        for line in code.split('\n'):
            match = re.match(r'^\d+:\s?(.*)', line)
            if match:
                lines.append(match.group(1).strip())
            else:
                lines.append(line.strip())
        return ' '.join(lines)

    async def _invoke_llm(self, batch: AnalysisBatch) -> Optional[Dict[str, Any]]:
        """LLM 호출 (일반 분석만)"""
        if not batch.ranges:
            raise RuntimeError(f"배치 #{batch.batch_id}에 분석할 범위가 없습니다")

        code, context = batch.build_payload()
        result = await asyncio.to_thread(
            analyze_code,
            code,
            context,
            batch.ranges,
            len(batch.ranges),
            self.api_key,
            self.locale,
        )
        return result

    def _build_analysis_queries(
        self, 
        batch: AnalysisBatch, 
        llm_result: Any,
        unit_summary_store: Optional[Dict[str, Dict[str, str]]],
    ) -> List[Union[str, Dict[str, Any]]]:
        """LLM 분석 결과를 쿼리로 변환 (파라미터화)
        
        Note: localDependencies, calls 관계는 class_relations.yaml에서 처리됨
        여기서는 summary와 variables만 처리
        
        응답 형식 (v3.0):
        - analysis[]: 각 범위별 summary, localDependencies, calls
        - variables[]: 최상위 레벨, 각 변수에 line 필드 포함 (중복 방지)
        """
        queries: List[Union[str, Dict[str, Any]]] = []
        
        # 타입 검증 (실패 시 예외 발생 → 전체 분석 중단)
        llm_result = self.validate_dict_result(llm_result, "llm_result", batch.batch_id)
        analysis_list = llm_result.get("analysis") or []
        
        # 1. 각 범위별 summary 처리
        for node, analysis in zip(batch.nodes, analysis_list):
            if not analysis:
                continue
            
            # 요약 업데이트
            summary = analysis.get("summary") or ""
            if summary:
                queries.append({
                    "query": (
                        f"MATCH (__cy_n__:{node.node_type} {{startLine: $start_line, directory: $directory, file_name: $file_name}})\n"
                        f"SET __cy_n__.summary = $summary\n"
                    f"RETURN __cy_n__"
                    ),
                    "params": {
                        "start_line": node.start_line,
                        "directory": self.full_directory,
                        "file_name": self.file_name,
                        "summary": str(summary),
                    }
                })
                
                # 클래스별 summary 저장
                if unit_summary_store is not None and node.unit_key:
                    if node.unit_key in unit_summary_store:
                        key = f"{node.node_type}_{node.start_line}_{node.end_line}"
                        unit_summary_store[node.unit_key][key] = summary
        
        # 2. 최상위 variables 처리 (라인 기반, 중복 없음)
        variables = llm_result.get("variables", []) or []
        if variables:
            # 라인 → 노드 매핑 구성
            line_to_node: Dict[int, StatementNode] = {}
            for node in batch.nodes:
                for line in range(node.start_line, node.end_line + 1):
                    # 더 작은 범위의 노드가 우선 (이미 있으면 덮어쓰지 않음)
                    if line not in line_to_node:
                        line_to_node[line] = node
            
            # 변수별 USES 관계 생성
            uses_queries = self._build_uses_queries_by_line(variables, line_to_node)
            queries.extend(uses_queries)
            
            # 변수 역할 누적 (기존 로직 호환)
            for var in variables:
                if isinstance(var, dict):
                    line = var.get("line")
                    if line and line in line_to_node:
                        node = line_to_node[line]
                        self._accumulate_variable_roles(node, [var])
        
        return queries
    
    def _build_uses_queries(
        self,
        node: StatementNode,
        variables: List[Any],
    ) -> List[Dict[str, Any]]:
        """SymbolRef 노드와 USES 관계 쿼리를 생성합니다.
        
        흐름 보존 목적: 정확한 타입 판별이 아닌, 이름 사용 사실만 기록
        
        Args:
            node: 분석된 코드 범위 노드 (METHOD 등)
            variables: [{"name": "v", "role": "write"}, ...] 형식의 리스트
            
        Returns:
            SymbolRef 노드 생성 + USES 관계 생성 쿼리 리스트
        """
        queries: List[Dict[str, Any]] = []
        
        # 소속 클래스 정보
        class_name = node.unit_name or "unknown"
        
        for var in variables:
            if isinstance(var, dict):
                var_name = var.get("name", "")
                var_role = var.get("role", "read")
            elif isinstance(var, str):
                # 호환성: 기존 형식 (문자열 배열) 지원
                var_name = var
                var_role = "read"
            else:
                continue
            
            if not var_name:
                continue
            
            # 1. SymbolRef 노드 생성 또는 재사용
            queries.append({
                "query": (
                    "MERGE (__cy_s__:SymbolRef {name: $sym_name, scope_class: $scope_class})\n"
                    "RETURN __cy_s__"
                ),
                "params": {
                    "sym_name": var_name,
                    "scope_class": class_name,
                }
            })
            
            # 2. METHOD → SymbolRef USES 관계 생성
            queries.append({
                "query": (
                    f"MATCH (__cy_n__:{node.node_type} {{startLine: $start_line, directory: $directory, file_name: $file_name}})\n"
                    "MATCH (__cy_s__:SymbolRef {name: $sym_name, scope_class: $scope_class})\n"
                    "MERGE (__cy_n__)-[__cy_r__:USES {role: $role}]->(__cy_s__)\n"
                    "RETURN __cy_r__"
                ),
                "params": {
                    "start_line": node.start_line,
                    "directory": self.full_directory,
                    "file_name": self.file_name,
                    "sym_name": var_name,
                    "scope_class": class_name,
                    "role": var_role,
                }
            })
            
            log_process("ANALYZE", "USES", 
                       f"   🔗 USES: {node.node_type}[{node.start_line}] -[{var_role}]-> {var_name}")
        
        return queries
    
    def _build_uses_queries_by_line(
        self,
        variables: List[Dict[str, Any]],
        line_to_node: Dict[int, "StatementNode"],
    ) -> List[Dict[str, Any]]:
        """라인 기반 variables를 USES 관계 쿼리로 변환합니다.
        
        v3.0 형식: 최상위 variables 배열, 각 변수에 line 필드 포함
        
        Args:
            variables: [{"line": 30, "name": "v", "role": "write"}, ...]
            line_to_node: 라인 번호 → 노드 매핑
            
        Returns:
            SymbolRef 노드 생성 + USES 관계 생성 쿼리 리스트
        """
        queries: List[Dict[str, Any]] = []
        
        for var in variables:
            if not isinstance(var, dict):
                continue
            
            line = var.get("line")
            var_name = var.get("name", "")
            var_role = var.get("role", "read")
            
            if not var_name:
                continue
            
            # 라인에 해당하는 노드 찾기
            node = line_to_node.get(line) if line else None
            
            if not node:
                # 노드를 찾지 못하면 스킵 (로그만 남김)
                log_process("ANALYZE", "USES", 
                           f"   ⚠️ USES 스킵: 라인 {line}에 해당하는 노드 없음 ({var_name})")
                continue
            
            class_name = node.unit_name or "unknown"
            
            # 1. SymbolRef 노드 생성 또는 재사용
            queries.append({
                "query": (
                    "MERGE (__cy_s__:SymbolRef {name: $sym_name, scope_class: $scope_class})\n"
                    "RETURN __cy_s__"
                ),
                "params": {
                    "sym_name": var_name,
                    "scope_class": class_name,
                }
            })
            
            # 2. 노드 → SymbolRef USES 관계 생성
            queries.append({
                "query": (
                    f"MATCH (__cy_n__:{node.node_type} {{startLine: $start_line, directory: $directory, file_name: $file_name}})\n"
                    "MATCH (__cy_s__:SymbolRef {name: $sym_name, scope_class: $scope_class})\n"
                    "MERGE (__cy_n__)-[__cy_r__:USES {role: $role}]->(__cy_s__)\n"
                    "RETURN __cy_r__"
                ),
                "params": {
                    "start_line": node.start_line,
                    "directory": self.full_directory,
                    "file_name": self.file_name,
                    "sym_name": var_name,
                    "scope_class": class_name,
                    "role": var_role,
                }
            })
            
            log_process("ANALYZE", "USES", 
                       f"   🔗 USES: {node.node_type}[{node.start_line}] -[{var_role}]-> {var_name} (line:{line})")
        
        return queries
    
    def _accumulate_variable_roles(
        self,
        context_node: StatementNode,
        variables: List[Any],
    ) -> None:
        """변수 역할을 메모리 맵에 누적합니다.
        
        Neo4j 저장은 _process_unit_summaries()에서 LLM 요약 후 수행됩니다.
        
        Args:
            context_node: 분석된 코드 범위 노드 (변수 사용 위치 기준)
            variables: 변수 정보 리스트 [{"name": "count", "role": "누적값 저장"}, ...]
        """
        unit_key = context_node.unit_key
        if not unit_key:
            return
        
        # 유닛별 맵 초기화
        if unit_key not in self._variable_roles:
            self._variable_roles[unit_key] = {}
        
        for var in variables:
            if isinstance(var, dict):
                var_name = var.get("name", "")
                var_role = var.get("role", "")
            elif isinstance(var, str):
                var_name = var
                var_role = ""
            else:
                continue
            
            if not var_name or not var_role:
                continue
            
            # 가장 가까운 VARIABLE 노드 찾기
            nearest_line = self._find_nearest_variable_declaration(
                var_name, 
                context_node.start_line,
                unit_key
            )
            
            if nearest_line is None:
                continue
            
            # 선언 라인별로 역할 누적
            if nearest_line not in self._variable_roles[unit_key]:
                self._variable_roles[unit_key][nearest_line] = []
            
            # 중복 역할 제거
            if var_role not in self._variable_roles[unit_key][nearest_line]:
                self._variable_roles[unit_key][nearest_line].append(var_role)
    
    def _find_nearest_variable_declaration(
        self,
        var_name: str,
        usage_line: int,
        unit_key: Optional[str],
    ) -> Optional[int]:
        """사용 위치에서 가장 가까운 변수 선언 라인을 찾습니다.
        
        Args:
            var_name: 변수명
            usage_line: 변수 사용 라인
            unit_key: 현재 클래스/유닛 키 (스코프 제한용)
        
        Returns:
            가장 가까운 선언 라인 또는 None
        """
        if not self._nodes:
            return None
        
        # 같은 유닛 내에서 VARIABLE 노드 중 변수명 일치하는 것 찾기
        candidates: List[int] = []
        
        for node in self._nodes:
            if node.node_type not in VARIABLE_TYPES:
                continue
            
            # 유닛 스코프 제한 (같은 클래스/메서드 내에서만)
            if unit_key and node.unit_key != unit_key:
                continue
            
            # 사용 라인 이전에 선언된 것만
            if node.start_line > usage_line:
                continue
            
            # 변수명 매칭 (코드에서 변수명 확인)
            if var_name in (node.code or ""):
                candidates.append(node.start_line)
        
        # 가장 가까운 것 (가장 큰 라인 번호)
        return max(candidates) if candidates else None

    async def _process_unit_summaries(
        self, 
        unit_summary_store: Dict[str, Dict[str, str]]
    ) -> List[Union[str, Dict[str, Any]]]:
        """클래스별 summary 처리 (파라미터화)"""
        queries: List[Union[str, Dict[str, Any]]] = []
        
        classes = self._unit_info
        if not classes:
            return queries
        
        for class_key, info in classes.items():
            summaries = unit_summary_store.get(class_key, {})
            if not summaries:
                continue
            
            # 클래스 노드 찾기
            class_node = next(
                (n for n in self._nodes if n.start_line == info.node_start and n.node_type == info.kind),
                None,
            )
            if not class_node:
                continue
            
            if not class_node.ok:
                log_process("ANALYZE", "SUMMARY", f"⚠️ {info.name}: 하위 분석 실패로 최종 summary 생성 스킵")
                continue
            
            all_user_stories: List[Dict[str, Any]] = []
            final_summary = ""
            
            # 청크 분할
            chunks = self._split_summaries_by_token(summaries, MAX_SUMMARY_CHUNK_TOKEN)
            if not chunks:
                continue
            
            log_process("ANALYZE", "SUMMARY", f"📦 {info.name}: summary 청크 분할 완료 ({len(chunks)}개 청크)")
            
            # 청크별 처리 (실패 시 예외 발생 → 전체 분석 중단)
            async def process_chunk(chunk_idx: int, chunk: dict) -> str:
                chunk_tokens = calculate_code_token(json.dumps(chunk, ensure_ascii=False))
                log_process("ANALYZE", "SUMMARY", f"  → 청크 {chunk_idx + 1}/{len(chunks)} 처리 시작 (토큰: {chunk_tokens})")
                
                summary_result = await asyncio.to_thread(
                    analyze_class_summary_only, chunk, self.api_key, self.locale, ""
                )
                validated = self.validate_dict_result(summary_result, "청크 분석")
                return validated.get('summary', '')
            
            chunk_results_raw = await asyncio.gather(
                *[process_chunk(idx, chunk) for idx, chunk in enumerate(chunks)]
            )
            
            chunk_results = [r for r in chunk_results_raw if r]
            
            if not chunk_results:
                raise RuntimeError(f"{info.name}: 청크 처리 결과가 모두 비어있음")
            
            # 청크 통합
            if len(chunk_results) == 1:
                final_summary = chunk_results[0]
            else:
                combined_summaries = {f"CHUNK_{idx + 1}": s for idx, s in enumerate(chunk_results)}
                result = await asyncio.to_thread(
                    analyze_class_summary_only, combined_summaries, self.api_key, self.locale, ""
                )
                validated = self.validate_dict_result(result, "청크 통합")
                final_summary = validated.get('summary') or "\n\n".join(chunk_results)
            
            log_process("ANALYZE", "SUMMARY", f"✅ {info.name}: summary 통합 완료")
            
            # User Story 생성 (실패 시 예외 발생)
            if final_summary:
                us_result = await asyncio.to_thread(
                    analyze_class_user_story, final_summary, self.api_key, self.locale
                )
                validated = self.validate_dict_result(us_result, "User Story")
                all_user_stories = validated.get('user_stories', []) or []
            
            if all_user_stories:
                log_process("ANALYZE", "SUMMARY", f"✅ {info.name}: User Story {len(all_user_stories)}개")
            else:
                log_process("ANALYZE", "SUMMARY", f"✅ {info.name}: User Story 없음")
            
            if not final_summary:
                continue
            
            # Neo4j 쿼리 생성 (파라미터화)
            queries.append({
                "query": (
                    f"MATCH (__cy_n__:{info.kind} {{startLine: $start_line, directory: $directory, file_name: $file_name}})\n"
                    f"SET __cy_n__.summary = $summary\n"
                f"RETURN __cy_n__"
                ),
                "params": {
                    "start_line": info.node_start,
                    "directory": self.full_directory,
                    "file_name": self.file_name,
                    "summary": str(final_summary),
                }
            })
            
            # User Story 노드 생성 (파라미터화)
            if all_user_stories:
                for us_idx, us in enumerate(all_user_stories, 1):
                    us_id = us.get('id', f"US-{us_idx}")
                    
                    queries.append({
                        "query": (
                            f"MATCH (__cy_c__:{info.kind} {{startLine: $start_line, directory: $directory, file_name: $file_name}})\n"
                            f"MERGE (__cy_us__:UserStory {{id: $us_id, class_name: $class_name, directory: $directory, file_name: $file_name}})\n"
                            f"SET __cy_us__.role = $role, __cy_us__.goal = $goal, __cy_us__.benefit = $benefit\n"
                        f"MERGE (__cy_c__)-[:HAS_USER_STORY]->(__cy_us__)\n"
                        f"RETURN __cy_us__"
                        ),
                        "params": {
                            "start_line": info.node_start,
                            "directory": self.full_directory,
                            "file_name": self.file_name,
                            "us_id": us_id,
                            "class_name": info.name,
                            "role": us.get('role', ''),
                            "goal": us.get('goal', ''),
                            "benefit": us.get('benefit', ''),
                        }
                    })
                    
                    for ac_idx, ac in enumerate(us.get('acceptance_criteria', []) or [], 1):
                        ac_id = ac.get('id', f"AC-{us_idx}-{ac_idx}")
                        
                        queries.append({
                            "query": (
                                f"MATCH (__cy_us__:UserStory {{id: $us_id, class_name: $class_name, directory: $directory, file_name: $file_name}})\n"
                                f"MERGE (__cy_ac__:AcceptanceCriteria {{id: $ac_id, user_story_id: $us_id, directory: $directory, file_name: $file_name}})\n"
                                f"SET __cy_ac__.title = $ac_title, __cy_ac__.given = $ac_given, __cy_ac__.when_clause = $ac_when, __cy_ac__.then_clause = $ac_then\n"
                            f"MERGE (__cy_us__)-[:HAS_AC]->(__cy_ac__)\n"
                            f"RETURN __cy_ac__"
                            ),
                            "params": {
                                "us_id": us_id,
                                "class_name": info.name,
                                "directory": self.full_directory,
                                "file_name": self.file_name,
                                "ac_id": ac_id,
                                "ac_title": ac.get('title', ''),
                                "ac_given": ac.get('given', ''),
                                "ac_when": ac.get('when', ''),
                                "ac_then": ac.get('then', ''),
                            }
                        })
            
            # 3. 변수 역할 요약 (신규)
            var_queries = await self._summarize_unit_variables(class_key)
            queries.extend(var_queries)
        
        return queries
    
    async def _summarize_unit_variables(self, unit_key: str) -> List[Union[str, Dict[str, Any]]]:
        """해당 유닛의 변수들 역할을 요약하여 Neo4j 쿼리를 생성합니다 (파라미터화).
        
        역할이 2개 이상인 변수는 LLM으로 요약, 1개인 변수는 그대로 저장.
        """
        queries: List[Union[str, Dict[str, Any]]] = []
        unit_vars = self._variable_roles.get(unit_key, {})
        
        if not unit_vars:
            return queries
        
        # 역할이 2개 이상인 변수들만 LLM 요약
        vars_to_summarize: List[Dict[str, Any]] = []
        single_role_vars: List[Tuple[int, str]] = []  # (선언라인, 역할)
        
        for var_line, roles in unit_vars.items():
            if len(roles) >= 2:
                vars_to_summarize.append({
                    "name": str(var_line),  # 선언 라인을 키로 사용
                    "roles": roles
                })
            elif len(roles) == 1:
                single_role_vars.append((var_line, roles[0]))
        
        # LLM 요약 호출
        if vars_to_summarize:
            try:
                result = await asyncio.to_thread(
                    analyze_variable_summary, vars_to_summarize, self.api_key, self.locale
                )
                validated = self.validate_dict_result(result, "변수 역할 요약", allow_none=True)
                
                if validated:
                    for var_sum in validated.get("summaries", []) or []:
                        var_line_str = var_sum.get("name", "")
                        summary = var_sum.get("summary", "")
                        
                        if var_line_str and summary:
                            try:
                                var_line = int(var_line_str)
                                queries.append({
                                    "query": (
                                        f"MATCH (__cy_v__:VARIABLE {{startLine: $var_line, directory: $directory, file_name: $file_name}})\n"
                                        f"SET __cy_v__.role = $role\n"
                                    f"RETURN __cy_v__"
                                    ),
                                    "params": {
                                        "var_line": var_line,
                                        "directory": self.full_directory,
                                        "file_name": self.file_name,
                                        "role": summary,
                                    }
                                })
                            except ValueError:
                                pass
                
                log_process("ANALYZE", "VARIABLE", f"✅ 변수 역할 요약 완료: {len(vars_to_summarize)}개 변수")
            except Exception as e:
                log_process("ANALYZE", "VARIABLE", f"⚠️ 변수 역할 요약 실패: {e}")
        
        # 역할이 1개인 변수는 그대로 저장
        for var_line, role in single_role_vars:
            queries.append({
                "query": (
                    f"MATCH (__cy_v__:VARIABLE {{startLine: $var_line, directory: $directory, file_name: $file_name}})\n"
                    f"SET __cy_v__.role = $role\n"
                f"RETURN __cy_v__"
                ),
                "params": {
                    "var_line": var_line,
                    "directory": self.full_directory,
                    "file_name": self.file_name,
                    "role": role,
                }
            })
        
        return queries
