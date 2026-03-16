"""DDL 정규식 파서

하위호환 re-export — 실제 구현은 analyzer.common.ddl_parser로 이동.
"""

from analyzer.common.ddl_parser import DDLParser, parse_ddl, get_ddl_parser  # noqa: F401
