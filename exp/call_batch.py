"""Phase5 CALLS 3단: CallBatch(ChunkBundle)→Call LLM Extract→Deterministic Link. Method→Method CALLS 생성."""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from exp.config import (
    get_chunk_bundle_dir,
    get_chunk_bundle_dir_from_run_dir,
    get_llm_parallel_workers,
    estimate_tokens,
    MAX_LLM_BATCH_TOKEN,
)
from exp.models import CallsEdge, UsesEdge
from exp.chunk_bundle import load_chunk_bundle
from exp.type_map import (
    TypeMap3Layer,
    build_type_map_3layer,
    build_class_map_with_scope,
    method_to_class_map,
    resolve_obj_to_type,
)
from util.rule_loader import RuleLoader

logger = logging.getLogger(__name__)

# call_code에서 receiver.methodName( 휴리스틱 추출
OBJ_METHOD_PATTERN = re.compile(r"(\w+)\s*\.\s*(\w+)\s*\(")

# Java 표준/기본 라이브러리 클래스 (LLM 지침 보완용 방어: 이 클래스로의 호출은 CALLS/USES에서 제외)
# java.lang + wrapper, String, Number, Enum, Thread, Throwable 계층, Reflect 등
_JAVA_LANG = (
    "Object", "Class", "String", "StringBuilder", "StringBuffer", "CharSequence",
    "Integer", "Long", "Double", "Float", "Boolean", "Byte", "Short", "Character", "Void",
    "Number", "Math", "System", "Runtime", "Thread", "Runnable", "ThreadLocal",
    "Throwable", "Exception", "RuntimeException", "Error", "AssertionError",
    "Enum", "Comparable", "Iterable", "AutoCloseable", "Cloneable", "Serializable",
    "Package", "Process", "ProcessBuilder", "SecurityManager", "StackTraceElement",
    "ClassLoader", "Module", "ModuleLayer", "ReflectiveOperationException",
    "IllegalArgumentException", "IllegalStateException", "NullPointerException", "IndexOutOfBoundsException",
    "UnsupportedOperationException", "NumberFormatException", "ClassCastException", "ArrayIndexOutOfBoundsException",
    "StringIndexOutOfBoundsException", "NegativeArraySizeException", "ArithmeticException", "ConcurrentModificationException",
    "NoSuchElementException", "Optional", "Objects",
)
# java.util: 컬렉션, 맵, 날짜, 유틸
_JAVA_UTIL = (
    "List", "ArrayList", "LinkedList", "Vector", "Stack", "CopyOnWriteArrayList",
    "Map", "HashMap", "LinkedHashMap", "TreeMap", "Hashtable", "ConcurrentHashMap", "WeakHashMap", "IdentityHashMap",
    "Set", "HashSet", "LinkedHashSet", "TreeSet", "CopyOnWriteArraySet", "EnumSet",
    "Queue", "Deque", "ArrayDeque", "LinkedList", "PriorityQueue", "BlockingQueue", "LinkedBlockingQueue", "ArrayBlockingQueue",
    "Collections", "Arrays", "Iterator", "ListIterator", "Enumeration", "Spliterator",
    "Comparator", "Comparable", "Optional", "OptionalInt", "OptionalLong", "OptionalDouble",
    "Date", "Calendar", "GregorianCalendar", "TimeZone", "SimpleTimeZone",
    "Random", "UUID", "Properties", "Hashtable", "StringTokenizer", "Formatter", "Scanner",
    "Locale", "Currency", "ResourceBundle", "PropertyResourceBundle", "ListResourceBundle",
    "Timer", "TimerTask", "Observable", "Observer", "EventListener", "EventObject",
    "Base64", "BitSet", "Currency", "Dictionary", "AbstractMap", "AbstractList", "AbstractSet", "AbstractCollection",
)
# java.util.stream
_JAVA_STREAM = (
    "Stream", "IntStream", "LongStream", "DoubleStream", "StreamSupport",
    "Collectors", "Collector",
)
# java.util.concurrent
_JAVA_CONCURRENT = (
    "Executor", "ExecutorService", "ScheduledExecutorService", "ThreadPoolExecutor", "Executors",
    "Future", "FutureTask", "CompletableFuture", "CompletionStage",
    "Callable", "Runnable", "ForkJoinPool", "ForkJoinTask", "RecursiveTask", "RecursiveAction",
    "AtomicInteger", "AtomicLong", "AtomicBoolean", "AtomicReference", "AtomicReferenceArray",
    "ConcurrentHashMap", "ConcurrentLinkedQueue", "ConcurrentLinkedDeque", "CopyOnWriteArrayList", "CopyOnWriteArraySet",
    "BlockingQueue", "LinkedBlockingQueue", "ArrayBlockingQueue", "SynchronousQueue", "DelayQueue", "PriorityBlockingQueue",
    "BlockingDeque", "LinkedBlockingDeque", "CountDownLatch", "CyclicBarrier", "Semaphore", "Phaser",
    "ReentrantLock", "ReentrantReadWriteLock", "Lock", "ReadWriteLock", "Condition",
    "Exchanger", "LockSupport", "TimeUnit",
)
# java.util.function
_JAVA_FUNCTION = (
    "Function", "Predicate", "Consumer", "Supplier", "BiFunction", "BiConsumer", "BiPredicate",
    "UnaryOperator", "BinaryOperator", "IntFunction", "LongFunction", "DoubleFunction",
    "ToIntFunction", "ToLongFunction", "ToDoubleFunction", "IntConsumer", "LongConsumer", "DoubleConsumer",
)
# java.io
_JAVA_IO = (
    "File", "FileInputStream", "FileOutputStream", "FileReader", "FileWriter",
    "InputStream", "OutputStream", "Reader", "Writer", "InputStreamReader", "OutputStreamWriter",
    "BufferedReader", "BufferedWriter", "BufferedInputStream", "BufferedOutputStream",
    "PrintStream", "PrintWriter", "ByteArrayInputStream", "ByteArrayOutputStream", "CharArrayReader", "CharArrayWriter",
    "DataInputStream", "DataOutputStream", "ObjectInputStream", "ObjectOutputStream",
    "FilterInputStream", "FilterOutputStream", "PipedInputStream", "PipedOutputStream",
    "StringReader", "StringWriter", "SequenceInputStream", "PushbackInputStream", "LineNumberReader",
    "FileFilter", "FilenameFilter", "Serializable", "Externalizable", "Closeable", "Flushable",
)
# java.nio
_JAVA_NIO = (
    "Path", "Paths", "Files", "FileSystem", "FileSystems",
    "Buffer", "ByteBuffer", "CharBuffer", "ShortBuffer", "IntBuffer", "LongBuffer", "FloatBuffer", "DoubleBuffer",
    "Charset", "Charsets", "StandardCharsets", "CharsetDecoder", "CharsetEncoder",
    "Channel", "ReadableByteChannel", "WritableByteChannel", "AsynchronousFileChannel", "FileChannel",
    "Selector", "SelectionKey", "ServerSocketChannel", "SocketChannel", "DatagramChannel",
    "DirectoryStream", "DirectoryStream.Filter", "LinkOption", "OpenOption", "StandardOpenOption",
    "CopyOption", "StandardCopyOption", "FileVisitOption", "FileVisitor", "SimpleFileVisitor",
)
# java.time
_JAVA_TIME = (
    "LocalDate", "LocalTime", "LocalDateTime", "ZonedDateTime", "OffsetDateTime", "OffsetTime",
    "Instant", "Duration", "Period", "Year", "YearMonth", "MonthDay", "DayOfWeek", "Month",
    "ZoneId", "ZoneOffset", "Clock", "Temporal", "TemporalAmount", "ChronoUnit", "ChronoField",
    "DateTimeFormatter", "DateTimeFormatterBuilder", "DateTimeParseException", "DateTimeException",
)
# java.math
_JAVA_MATH = (
    "BigDecimal", "BigInteger", "MathContext", "RoundingMode",
)
# java.text
_JAVA_TEXT = (
    "DateFormat", "SimpleDateFormat", "NumberFormat", "DecimalFormat", "ChoiceFormat", "MessageFormat",
    "Collator", "RuleBasedCollator", "BreakIterator", "CharacterIterator", "StringCharacterIterator",
    "Normalizer", "Bidi",
)
# java.util.regex
_JAVA_REGEX = (
    "Pattern", "Matcher", "PatternSyntaxException",
)
# java.lang.reflect
_JAVA_REFLECT = (
    "Method", "Field", "Constructor", "Array", "Modifier", "Proxy", "InvocationHandler",
    "AccessibleObject", "Parameter", "Type", "GenericDeclaration", "AnnotatedElement",
)
# java.net
_JAVA_NET = (
    "URL", "URI", "URLEncoder", "URLDecoder", "InetAddress", "Inet4Address", "Inet6Address",
    "Socket", "ServerSocket", "DatagramSocket", "DatagramPacket",
    "HttpURLConnection", "URLConnection", "SocketAddress", "InetSocketAddress",
    "Proxy", "ProxySelector", "CookieHandler", "Authenticator", "PasswordAuthentication",
    "NetworkInterface", "InterfaceAddress", "MulticastSocket",
)
# 로깅 (java.util.logging + slf4j API 등 표준적 로거 인터페이스명)
_JAVA_LOGGING = (
    "Logger", "Log", "LogFactory", "LogManager", "Level", "Handler", "Formatter", "Filter",
    "ConsoleHandler", "FileHandler", "StreamHandler", "SocketHandler", "MemoryHandler",
)
# javax.* (JDK 포함)
_JAVAX_COMMON = (
    "Servlet", "ServletRequest", "ServletResponse", "ServletContext", "ServletConfig",
    "HttpServlet", "HttpServletRequest", "HttpServletResponse", "HttpSession", "Filter", "FilterChain",
    "Validation", "Validator", "ConstraintValidator", "NotNull", "NotEmpty",
    "Annotation", "Resource", "Inject", "EJB", "PersistenceContext",
    "SecurityContext", "Principal", "X509Certificate",
)
# 기타 JDK
_JAVA_OTHER = (
    "ServiceLoader", "StringJoiner",
    "WeakReference", "SoftReference", "PhantomReference", "Reference", "ReferenceQueue",
)

JAVA_STDLIB_CLASSES = frozenset(
    _JAVA_LANG + _JAVA_UTIL + _JAVA_STREAM + _JAVA_CONCURRENT + _JAVA_FUNCTION
    + _JAVA_IO + _JAVA_NIO + _JAVA_TIME + _JAVA_MATH + _JAVA_TEXT + _JAVA_REGEX
    + _JAVA_REFLECT + _JAVA_NET + _JAVA_LOGGING + _JAVAX_COMMON + _JAVA_OTHER
)


def load_call_records_from_chunk_bundles(
    data_dir: Optional[str] = None,
    chunk_bundle_dir: Optional[str] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """ChunkBundle에서 method_id별 call_records [{call_id, call_code}] 로드.
    chunk_bundle_dir 있으면 exp/exp_run/chunk_bundles 사용, 없으면 data_dir/chunk_bundles."""
    base = Path(chunk_bundle_dir) if chunk_bundle_dir else Path(get_chunk_bundle_dir(data_dir or ""))
    if not base.is_dir():
        logger.info("[EXP:CALL_BATCH] ChunkBundle 로드 스킵 | reason=chunk_bundle_dir_not_found path=%s", base)
        return {}
    call_records_by_method: Dict[str, List[Dict[str, Any]]] = {}
    for path in base.glob("*.json"):
        try:
            bundle = load_chunk_bundle(path)
            for c in bundle.method_calls:
                call_records_by_method.setdefault(c.parent_method_id, []).append({
                    "call_id": c.call_id,
                    "call_code": c.call_code,
                })
            for v in bundle.variables:
                if not v.flags or not v.flags.get("initializerContainsMethodCall"):
                    continue
                method_id = v.parent_method_id
                recs = call_records_by_method.setdefault(method_id, [])
                seq = len(recs)
                call_id = f"C:{v.start_line}:{v.end_line}:{seq}"
                recs.append({
                    "call_id": call_id,
                    "call_code": (v.initializer_code or "").strip(),
                })
        except Exception as e:
            logger.debug("ChunkBundle 로드 스킵 %s: %s", path, e)
    total_records = sum(len(recs) for recs in call_records_by_method.values())
    logger.info(
        "[EXP:CALL_BATCH] ChunkBundle 로드 완료 | methods_with_calls=%d total_records=%d",
        len(call_records_by_method),
        total_records,
    )
    return call_records_by_method


def extract_obj_method_from_call_code(call_code: str) -> Tuple[Optional[str], Optional[str]]:
    """call_code에서 objName, methodName 휴리스틱 추출. LLM 없을 때 fallback."""
    if not call_code:
        return None, None
    m = OBJ_METHOD_PATTERN.search(call_code)
    if m:
        return m.group(1), m.group(2)
    return None, None


def _call_item_tokens(method_id: str, calls: List[Dict[str, Any]]) -> int:
    """한 (method_id, calls) 항목의 토큰 수 근사 (배치 묶기용)."""
    t = estimate_tokens(method_id or "")
    for c in calls or []:
        t += estimate_tokens(str(c.get("call_code") or ""))
    return max(1, t)


def _build_call_batches(
    call_records_by_method: Dict[str, List[Dict[str, Any]]],
    max_tokens: int,
) -> List[List[Tuple[str, List[Dict[str, Any]]]]]:
    """method_id별 call 목록을 max_tokens 이하로 묶어 배치 리스트 반환. 각 배치는 [(method_id, calls), ...]."""
    batches: List[List[Tuple[str, List[Dict[str, Any]]]]] = []
    current: List[Tuple[str, List[Dict[str, Any]]]] = []
    current_tokens = 0
    for method_id, recs in call_records_by_method.items():
        calls_input = [{"call_id": r.get("call_id", ""), "call_code": r.get("call_code", "")} for r in recs]
        t = _call_item_tokens(method_id, calls_input)
        if t > max_tokens:
            t = max_tokens
        if current and current_tokens + t > max_tokens:
            batches.append(current)
            current = []
            current_tokens = 0
        current.append((method_id, calls_input))
        current_tokens += t
    if current:
        batches.append(current)
    return batches


def _extract_calls_batch_via_llm(
    batch: List[Tuple[str, List[Dict[str, Any]]]],
    api_key: str,
) -> Dict[str, List[Dict[str, Any]]]:
    """한 배치(여러 method_id + calls)를 한 번에 LLM에 보내 obj_name/method_name 추출. method_id -> results 반환."""
    if not batch or not api_key:
        return {}
    items = [{"method_id": mid, "calls": calls} for mid, calls in batch]
    try:
        loader = RuleLoader(target_lang="exp")
        result = loader.execute("call_extract", {"items": items}, api_key=api_key)
    except Exception as e:
        logger.warning("[EXP:CALL_BATCH] LLM call_extract 오류: %s", e)
        return {mid: [] for mid, _ in batch}
    out: Dict[str, List[Dict[str, Any]]] = {}
    results_list = result.get("results") or []
    for i, (method_id, _) in enumerate(batch):
        if i < len(results_list) and isinstance(results_list[i], dict):
            res = results_list[i]
            out[method_id] = list(res.get("results") or [])
        else:
            out[method_id] = []
    for mid, _ in batch:
        if mid not in out:
            out[mid] = []
    logger.info(
        "[EXP:CALL_BATCH] LLM call_extract 완료 | 배치 메서드 수=%d",
        len(batch),
    )
    return out


def _is_stdlib_class(name: Optional[str]) -> bool:
    """Java 표준/기본 라이브러리 클래스면 True. LLM 방어 로직용."""
    if not name or not isinstance(name, str):
        return False
    n = name.strip()
    if not n:
        return False
    return n in JAVA_STDLIB_CLASSES or n.split(".")[-1] in JAVA_STDLIB_CLASSES


def _candidates_for_class_and_method(
    method_index_by_class: Dict[str, Dict[str, List[str]]],
    class_name: str,
    method_name: str,
) -> List[str]:
    """FQN 또는 short name으로 후보 조회. method_index_by_class 키가 FQN이므로 short name이면 끝이 일치하는 키 모두 검사."""
    direct = (method_index_by_class.get(class_name) or {}).get(method_name, [])
    if direct:
        return direct
    # short name: 키가 ".ClassName"으로 끝나는 경우 수집
    out: List[str] = []
    for fqn, by_method in method_index_by_class.items():
        if fqn == class_name or fqn.endswith("." + class_name):
            out.extend(by_method.get(method_name, []))
    return out


def resolve_calls_deterministic(
    call_records_with_obj_method: List[Dict[str, Any]],  # [{call_id, objName, methodName, parent_method_id}]
    type_map: TypeMap3Layer,
    method_index_by_class: Dict[str, Dict[str, List[str]]],
    method_to_class: Dict[str, str],
) -> Tuple[List[CallsEdge], List[Dict[str, Any]]]:
    """objName→className (type_map), method_index_by_class[className][methodName] → callee. 1개면 CALLS, 아니면 unresolved."""
    edges: List[CallsEdge] = []
    unresolved: List[Dict[str, Any]] = []
    for r in call_records_with_obj_method:
        caller_id = r.get("parent_method_id")
        obj_name = r.get("objName")
        method_name = r.get("methodName")
        if not caller_id or not method_name:
            logger.debug("[EXP:CALL_BATCH] resolve 스킵 | reason=missing_caller_or_method call_id=%s", r.get("call_id"))
            continue
        className = resolve_obj_to_type(caller_id, obj_name or "", type_map, method_to_class) if obj_name else None
        if not className:
            unresolved.append(dict(r, className=None))  # external 시 obj_name으로 표시
            logger.debug("[EXP:CALL_BATCH] resolve 스킵 | reason=obj_not_resolved obj_name=%s caller_id=%s", obj_name, caller_id)
            continue
        candidates = _candidates_for_class_and_method(method_index_by_class, className, method_name)
        if len(candidates) == 1:
            edges.append(CallsEdge(caller_method_id=caller_id, callee_method_id=candidates[0], call_code=r.get("call_code", ""), scope="internal"))
        else:
            r_with_class = dict(r)
            r_with_class["className"] = className  # external 표시용
            unresolved.append(r_with_class)
            logger.debug(
                "[EXP:CALL_BATCH] resolve 스킵 | reason=ambiguous_or_none candidates=%d className=%s method_name=%s",
                len(candidates),
                className,
                method_name,
            )
    logger.info("[EXP:CALL_BATCH] deterministic 완료 | edges=%d unresolved=%d", len(edges), len(unresolved))
    return edges, unresolved


def build_external_calls_edges(
    unresolved: List[Dict[str, Any]],
    class_map: Dict[Tuple[str, str], Tuple[str, str]],
) -> List[CallsEdge]:
    """클래스맵에 있고 scope=external인 경우만 external CALLS 엣지 생성.
    클래스맵에 존재하지 않는 (객체명, 타입) → 라이브러리/표준 메서드로 간주하여 CALLS 생성하지 않음."""
    external: List[CallsEdge] = []
    for r in unresolved or []:
        caller_id = r.get("parent_method_id")
        obj_name = r.get("objName") or ""
        method_name = r.get("methodName") or "?"
        if not caller_id or not obj_name:
            continue
        key = (caller_id, obj_name)
        if key not in class_map:
            # 클래스맵에 없음 = 라이브러리/표준 → CALLS 생성 안 함
            continue
        class_name, scope = class_map[key]
        if scope != "external":
            continue
        if _is_stdlib_class(class_name):
            continue
        callee_id = f"external:{class_name}#{method_name}"
        external.append(CallsEdge(
            caller_method_id=caller_id,
            callee_method_id=callee_id,
            call_code=r.get("call_code", ""),
            scope="external",
        ))
    if external:
        logger.info("[EXP:CALL_BATCH] external CALLS 엣지 생성 | count=%d", len(external))
    return external


def _external_callee_class(callee_method_id: str) -> Optional[str]:
    """external:ClassName#methodName → ClassName. 아니면 None."""
    s = (callee_method_id or "").strip()
    if not s.startswith("external:"):
        return None
    rest = s[len("external:"):]
    if "#" in rest:
        return rest.split("#", 1)[0].strip() or None
    return rest.strip() or None


def derive_uses_from_calls(
    calls_edges: List[CallsEdge],
    method_facts_list: List[Any],
) -> List[UsesEdge]:
    """CallsEdge 리스트에서 (caller_class_fqn, callee_class_fqn) 추출 → UsesEdge. internal·external 모두 포함(framework와 동일)."""
    method_id_to_class_fqn: Dict[str, str] = {}
    for m in method_facts_list or []:
        mid = getattr(m, "method_id", None)
        if not mid:
            continue
        fqn = getattr(m, "fqn", None) or mid
        class_fqn = fqn.rsplit(".", 1)[0] if "." in fqn else fqn
        method_id_to_class_fqn[mid] = class_fqn
    seen: set[Tuple[str, str, str]] = set()  # (from, to, scope)
    out: List[UsesEdge] = []
    for e in calls_edges or []:
        caller_cfqn = method_id_to_class_fqn.get(e.caller_method_id) or (
            e.caller_method_id.rsplit(".", 1)[0] if "." in e.caller_method_id else ""
        )
        if not caller_cfqn:
            continue
        is_external = getattr(e, "scope", "internal") == "external" or (getattr(e, "callee_method_id", "") or "").startswith("external:")
        if is_external:
            callee_cfqn = _external_callee_class(e.callee_method_id)
            scope = "external"
        else:
            callee_cfqn = method_id_to_class_fqn.get(e.callee_method_id) or (
                e.callee_method_id.rsplit(".", 1)[0] if "." in e.callee_method_id else ""
            )
            scope = "internal"
        if not callee_cfqn or caller_cfqn == callee_cfqn:
            continue
        key = (caller_cfqn, callee_cfqn, scope)
        if key in seen:
            continue
        seen.add(key)
        out.append(UsesEdge(from_fqn=caller_cfqn, to_fqn=callee_cfqn, scope=scope))
    logger.info("[EXP:CALL_BATCH] derive_uses_from_calls | calls=%d -> uses=%d", len(calls_edges or []), len(out))
    return out


def run_call_batch_pipeline(
    data_dir: str,
    method_facts_list: List[Any],
    field_facts_list: List[Any],
    symbol_type_index: Dict[str, Dict[str, str]],
    method_index_by_class: Dict[str, Dict[str, List[str]]],
    use_llm: bool = True,
    api_key: Optional[str] = None,
    run_dir: Optional[str] = None,
) -> Tuple[List[CallsEdge], List[Dict[str, Any]]]:
    """CallBatch 로드 → objName/methodName 추출(LLM 필수 시 RuleLoader, 없으면 휴리스틱) → Deterministic Link.
    run_dir 있으면 ChunkBundle을 exp/exp_run/chunk_bundles에서 로드."""
    chunk_dir = get_chunk_bundle_dir_from_run_dir(run_dir) if run_dir else None
    call_records_by_method = load_call_records_from_chunk_bundles(
        data_dir=data_dir,
        chunk_bundle_dir=chunk_dir,
    )
    if not call_records_by_method:
        logger.info("[EXP:CALL_BATCH] 파이프라인 스킵 | reason=no_call_records chunk_dir=%s", chunk_dir or data_dir)
        return [], []

    logger.info(
        "[EXP:CALL_BATCH] 파이프라인 진입 | methods_with_calls=%d use_llm=%s api_key_set=%s",
        len(call_records_by_method),
        use_llm,
        bool(api_key),
    )
    type_map = build_type_map_3layer(symbol_type_index, field_facts_list, method_facts_list)
    m2c = method_to_class_map(method_facts_list)

    flat: List[Dict[str, Any]] = []
    key = api_key or ""
    if use_llm and key:
        batches = _build_call_batches(call_records_by_method, MAX_LLM_BATCH_TOKEN)
        logger.info(
            "[EXP:CALL_BATCH] 청크 배치 | methods=%d → 배치=%d (max_tokens=%d)",
            len(call_records_by_method),
            len(batches),
            MAX_LLM_BATCH_TOKEN,
        )
        batch_results: Dict[str, List[Dict[str, Any]]] = {}
        workers = get_llm_parallel_workers()
        if workers <= 1:
            for batch in batches:
                one = _extract_calls_batch_via_llm(batch, key)
                for mid, results_list in one.items():
                    batch_results.setdefault(mid, []).extend(results_list)
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(_extract_calls_batch_via_llm, batch, key): batch for batch in batches}
                for future in as_completed(futures):
                    try:
                        one = future.result()
                        for mid, results_list in one.items():
                            batch_results.setdefault(mid, []).extend(results_list)
                    except Exception as ex:
                        logger.warning("[EXP:CALL_BATCH] 배치 LLM 처리 예외: %s", ex)
        for method_id, recs in call_records_by_method.items():
            by_call_id = {r.get("call_id"): r for r in (batch_results.get(method_id) or []) if r.get("call_id")}
            for r in recs:
                cid = r.get("call_id", "")
                info = by_call_id.get(cid, {})
                obj_name = info.get("obj_name") or None
                method_name = info.get("method_name") or None
                # 방어: LLM이 표준 라이브러리도 반환한 경우 제외 (framework analysis와 동일)
                if obj_name and _is_stdlib_class(obj_name):
                    obj_name, method_name = "", ""
                flat.append({
                    "parent_method_id": method_id,
                    "call_id": cid,
                    "call_code": r.get("call_code", ""),
                    "objName": obj_name,
                    "methodName": method_name,
                })
    else:
        for method_id, recs in call_records_by_method.items():
            for r in recs:
                call_code = r.get("call_code", "")
                obj_name, method_name = extract_obj_method_from_call_code(call_code)
                if obj_name and _is_stdlib_class(obj_name):
                    obj_name, method_name = None, None
                flat.append({
                    "parent_method_id": method_id,
                    "call_id": r.get("call_id", ""),
                    "call_code": call_code,
                    "objName": obj_name,
                    "methodName": method_name,
                })

    logger.info(
        "[EXP:CALL_BATCH] 추출 방식 | use_llm=%s api_key_set=%s flat_records=%d",
        use_llm,
        bool(key),
        len(flat),
    )
    edges, unresolved = resolve_calls_deterministic(flat, type_map, method_index_by_class, m2c)
    internal_class_fqns = set(method_index_by_class.keys())
    is_domain_class = lambda cn: bool(
        cn and str(cn).strip() and str(cn).strip()[0].isupper() and not _is_stdlib_class(str(cn).strip())
    )
    class_map = build_class_map_with_scope(type_map, m2c, internal_class_fqns, is_domain_class)
    external_edges = build_external_calls_edges(unresolved, class_map)
    all_edges = edges + external_edges
    if unresolved and not key:
        logger.info("[CALL_BATCH] api_key 없음: unresolved %d건 관계 생성 스킵 (로그만)", len(unresolved))
    logger.info("[EXP:CALL_BATCH] 파이프라인 완료 | internal=%d external=%d unresolved=%d", len(edges), len(external_edges), len(unresolved))
    return all_edges, unresolved
