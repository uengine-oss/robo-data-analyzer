"""
DDL 정적 파서 테스트

data/ddl 폴더의 DDL 파일들을 정적 파서로 분석하여
생성된 테이블과 컬럼 정보를 출력합니다.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from analyzer.common.ddl_parser import parse_ddl
from config.settings import settings

# ==================== 프로젝트 경로 설정 ====================
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# ==================== 환경변수 설정 ====================
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def read_text_file_sync(file_path: str) -> str:
    """동기 방식으로 텍스트 파일 읽기 (인코딩 fallback 지원)"""
    encodings = ("utf-8", "utf-8-sig", "cp949", "euc-kr")
    
    for encoding in encodings:
        try:
            with open(file_path, "r", encoding=encoding) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    
    raise RuntimeError(f"파일 디코딩 실패: {file_path} (tried: {', '.join(encodings)})")


def get_ddl_files() -> list[Path]:
    """data/ddl 폴더의 DDL 파일 목록 반환"""
    ddl_dir = Path(settings.path.data_dir) / "ddl"
    
    if not ddl_dir.exists():
        print(f"\n⚠️  DDL 디렉토리가 존재하지 않습니다: {ddl_dir}")
        print(f"   data_dir: {settings.path.data_dir}")
        print(f"   base_dir: {settings.path.base_dir}")
        return []
    
    if not ddl_dir.is_dir():
        print(f"\n⚠️  DDL 경로가 디렉토리가 아닙니다: {ddl_dir}")
        return []
    
    ddl_files = sorted([f for f in ddl_dir.iterdir() if f.is_file()])
    
    if not ddl_files:
        print(f"\n⚠️  DDL 디렉토리에 파일이 없습니다: {ddl_dir}")
        return []
    
    return ddl_files


def test_ddl_static_parser():
    """DDL 정적 파서 테스트
    
    data/ddl 폴더의 모든 DDL 파일을 읽어서 정적 파서로 분석하고
    생성된 테이블과 컬럼 정보를 출력합니다.
    """
    ddl_files = get_ddl_files()
    
    print(f"\n{'='*80}")
    print(f"DDL 정적 파서 테스트 시작")
    print(f"DDL 디렉토리: {Path(settings.path.data_dir) / 'ddl'}")
    print(f"발견된 DDL 파일 수: {len(ddl_files)}")
    print(f"{'='*80}\n")
    
    if not ddl_files:
        print("❌ DDL 파일이 없어 테스트를 진행할 수 없습니다.")
        print(f"   DDL 디렉토리를 확인하세요: {Path(settings.path.data_dir) / 'ddl'}")
        pytest.fail("DDL 파일이 없습니다.")
    
    total_tables = 0
    total_columns = 0
    
    for ddl_file in ddl_files:
        print(f"\n[파일] {ddl_file.name}")
        print(f"{'-'*80}")
        
        try:
            # DDL 파일 읽기
            ddl_content = read_text_file_sync(str(ddl_file))
            
            if not ddl_content.strip():
                print(f"  ⚠️  파일이 비어있습니다.")
                continue
            
            # 정적 파서로 파싱
            result = parse_ddl(ddl_content)
            
            if "analysis" not in result or not result["analysis"]:
                print(f"  ⚠️  파싱된 테이블이 없습니다.")
                continue
            
            # 테이블 정보 출력
            for table_info in result["analysis"]:
                table = table_info.get("table", {})
                columns = table_info.get("columns", [])
                primary_keys = table_info.get("primaryKeys", [])
                foreign_keys = table_info.get("foreignKeys", [])
                
                schema = table.get("schema", "")
                table_name = table.get("name", "")
                table_comment = table.get("comment", "")
                
                full_table_name = f"{schema}.{table_name}" if schema else table_name
                
                print(f"\n  📊 테이블: {full_table_name}")
                if table_comment:
                    print(f"     설명: {table_comment}")
                
                # 컬럼 정보 출력
                if columns:
                    print(f"     컬럼 ({len(columns)}개):")
                    for col in columns:
                        col_name = col.get("name", "")
                        col_type = col.get("dtype", "")
                        nullable = col.get("nullable", True)
                        col_comment = col.get("comment", "")
                        is_pk = col.get("primary_key", False)
                        
                        nullable_str = "NULL" if nullable else "NOT NULL"
                        pk_str = " [PK]" if is_pk else ""
                        comment_str = f" -- {col_comment}" if col_comment else ""
                        
                        print(f"       - {col_name}: {col_type} {nullable_str}{pk_str}{comment_str}")
                else:
                    print(f"     컬럼: 없음")
                
                # Primary Key 정보
                if primary_keys:
                    pk_cols = ", ".join(primary_keys)
                    print(f"     Primary Key: {pk_cols}")
                
                # Foreign Key 정보
                if foreign_keys:
                    print(f"     Foreign Keys ({len(foreign_keys)}개):")
                    for fk in foreign_keys:
                        fk_col = fk.get("column", "")
                        fk_ref = fk.get("ref", "")
                        print(f"       - {fk_col} -> {fk_ref}")
                
                total_tables += 1
                total_columns += len(columns)
        
        except Exception as e:
            print(f"  ❌ 오류 발생: {e}")
            import traceback
            traceback.print_exc()
            continue
    
    # 전체 요약
    print(f"\n{'='*80}")
    print(f"테스트 완료")
    print(f"  - 처리된 파일 수: {len(ddl_files)}")
    print(f"  - 총 테이블 수: {total_tables}")
    print(f"  - 총 컬럼 수: {total_columns}")
    print(f"{'='*80}\n")
    
    # 최소한 하나의 테이블은 파싱되어야 함
    assert total_tables > 0, "파싱된 테이블이 없습니다."

