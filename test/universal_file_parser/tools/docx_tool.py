"""DOCX 추출 도구 — python-docx 로 문단(스타일 포함) + 표 반환."""

import io

from langchain_core.tools import tool


def make_docx_extract(state):
    @tool
    def docx_extract() -> dict:
        """DOCX 에서 문단(스타일명 포함)과 표를 추출.

        Returns:
            {
                "paragraphs": [
                    {"style": "Heading 1", "text": "..."},
                    {"style": "Normal",    "text": "..."},
                    ...
                ],
                "tables": [[[...]], ...]
            }

        style 을 보고 ToC·제목·본문 구분 가능. LLM 이 직접 판단해서 parse() 작성.
        """
        try:
            from docx import Document
        except ImportError:
            return {"error": "python-docx 미설치. `pip install python-docx` 필요."}

        try:
            doc = Document(io.BytesIO(state.data))
        except Exception as e:
            return {"error": f"DOCX 읽기 실패: {type(e).__name__}: {e}"}

        paragraphs = []
        for para in doc.paragraphs:
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

        result = {"paragraphs": paragraphs, "tables": tables}
        state.last_extracted = result
        return result
    return docx_extract
