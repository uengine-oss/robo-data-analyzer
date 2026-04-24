"""DOCX 추출 도구 — python-docx 로 문단(스타일 포함) + 표 반환."""

import io

from langchain_core.tools import tool


def make_docx_extract(state):
    @tool
    def docx_extract(max_paragraphs: int = 200, para_offset: int = 0) -> dict:
        """DOCX 에서 문단(스타일명 포함)과 표를 추출.

        Args:
            max_paragraphs: 반환할 문단 수 (기본 200)
            para_offset:    문단 시작 위치 (기본 0). 큰 문서는 구간 나눠 호출.

        Returns:
            {"paragraphs": [{"style": ..., "text": ...}, ...],
             "tables": [[[...]]],
             "meta": {"total_paragraphs", "returned", "offset"}}
        """
        try:
            from docx import Document
        except ImportError:
            return {"error": "python-docx 미설치."}

        try:
            doc = Document(io.BytesIO(state.data))
        except Exception as e:
            return {"error": f"DOCX 읽기 실패: {type(e).__name__}: {e}"}

        all_paragraphs = list(doc.paragraphs)
        total = len(all_paragraphs)
        start = max(int(para_offset), 0)
        end = start + max(int(max_paragraphs), 1)

        paragraphs = []
        for i, para in enumerate(all_paragraphs):
            if i < start:
                continue
            if i >= end:
                break
            if para.text.strip():
                paragraphs.append({
                    "style": para.style.name if para.style else "Normal",
                    "text": para.text,
                })

        tables = []
        for table in doc.tables:
            table_data = []
            for row in table.rows:
                table_data.append([cell.text for cell in row.cells])
            tables.append(table_data)

        result = {
            "paragraphs": paragraphs,
            "tables": tables,
            "meta": {
                "total_paragraphs": total,
                "returned": len(paragraphs),
                "offset": start,
            },
        }
        state.last_extracted = result
        return result
    return docx_extract
