"""XLSX 추출 도구 — openpyxl 로 시트별 셀 배열 반환."""

import io

from langchain_core.tools import tool


def make_excel_extract(state):
    @tool
    def excel_extract(sheet: str = "", max_rows: int = 100) -> dict:
        """XLSX 에서 시트별 데이터를 2차원 배열로 추출.

        Args:
            sheet: 시트 이름 (빈 문자열이면 모든 시트)
            max_rows: 시트당 최대 행 수 (기본 100)

        Returns:
            {
                "sheets": {
                    "Sheet1": [[cell, cell, ...], ...],
                    ...
                }
            }

        헤더 위치·계층 구조는 LLM 이 직접 추론해야 함. 빈 행/병합셀 등 주의.
        """
        try:
            from openpyxl import load_workbook
        except ImportError:
            return {"error": "openpyxl 미설치. `pip install openpyxl` 필요."}

        try:
            wb = load_workbook(io.BytesIO(state.data), data_only=True)
        except Exception as e:
            return {"error": f"XLSX 읽기 실패: {type(e).__name__}: {e}"}

        result = {"sheets": {}}
        sheet_names = [sheet] if sheet else wb.sheetnames

        for name in sheet_names:
            if name not in wb.sheetnames:
                continue
            ws = wb[name]
            rows = []
            for row_idx, row in enumerate(ws.iter_rows(values_only=True)):
                if row_idx >= max_rows:
                    break
                rows.append(list(row))
            result["sheets"][name] = rows

        state.last_extracted = result
        return result
    return excel_extract
