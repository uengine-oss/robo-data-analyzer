"""XLSX 추출 도구 — openpyxl 로 시트별 셀 배열 반환."""

import io

from langchain_core.tools import tool


def make_excel_extract(state):
    @tool
    def excel_extract(sheet: str = "", max_rows: int = 200, row_offset: int = 0) -> dict:
        """XLSX 에서 시트별 데이터를 2차원 배열로 추출.

        Args:
            sheet:      시트 이름 (빈 문자열 = 모든 시트)
            max_rows:   시트당 최대 행 수 (기본 200). 더 많으면 크게 지정.
            row_offset: 행 시작 위치 (기본 0). 큰 시트는 구간 나눠 호출.

        Returns:
            {"sheets": {"Sheet1": [[cell, ...], ...], ...}, "sheet_meta": {sheet: {total_rows, ...}}}
        """
        try:
            from openpyxl import load_workbook
        except ImportError:
            return {"error": "openpyxl 미설치."}

        try:
            wb = load_workbook(io.BytesIO(state.data), data_only=True)
        except Exception as e:
            return {"error": f"XLSX 읽기 실패: {type(e).__name__}: {e}"}

        result = {"sheets": {}, "sheet_meta": {}}
        sheet_names = [sheet] if sheet else wb.sheetnames

        for name in sheet_names:
            if name not in wb.sheetnames:
                continue
            ws = wb[name]
            rows = []
            total_in_sheet = 0
            start = max(int(row_offset), 0)
            end = start + max(int(max_rows), 1)
            for row_idx, row in enumerate(ws.iter_rows(values_only=True)):
                total_in_sheet += 1
                if row_idx < start:
                    continue
                if row_idx >= end:
                    continue
                rows.append(list(row))
            result["sheets"][name] = rows
            result["sheet_meta"][name] = {
                "total_rows": total_in_sheet,
                "returned_rows": len(rows),
                "row_offset": start,
            }

        state.last_extracted = result
        return result
    return excel_extract
