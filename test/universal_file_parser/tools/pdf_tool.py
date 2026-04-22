"""PDF 추출 도구 — pdfplumber 로 페이지별 텍스트 + 표 반환."""

import io

from langchain_core.tools import tool


def make_pdf_extract(state):
    @tool
    def pdf_extract(pages: str = "1-5") -> dict:
        """PDF 에서 텍스트와 표를 페이지별로 추출.

        Args:
            pages: 페이지 지정. "1-5" (범위), "all" (전체), "1,3,5" (복수 선택)

        Returns:
            {
                "total_pages": int,
                "pages": [
                    {"page": 1, "text": "...", "tables": [[...]]},
                    ...
                ]
            }

        반환된 dict 를 보고 구조를 파악한 뒤, parse(extracted: dict) -> list 함수를 작성하세요.
        """
        try:
            import pdfplumber
        except ImportError:
            return {"error": "pdfplumber 미설치. `pip install pdfplumber` 필요."}

        page_indices = _parse_page_spec(pages)
        result = {"total_pages": 0, "pages": []}

        try:
            with pdfplumber.open(io.BytesIO(state.data)) as pdf:
                total = len(pdf.pages)
                result["total_pages"] = total

                for idx in page_indices:
                    if idx >= total:
                        continue
                    page = pdf.pages[idx]
                    text = page.extract_text() or ""
                    tables = page.extract_tables() or []

                    result["pages"].append({
                        "page": idx + 1,
                        "text": text[:3000],
                        "tables": tables[:5],
                    })
        except Exception as e:
            return {"error": f"PDF 읽기 실패: {type(e).__name__}: {e}"}

        state.last_extracted = result
        return result
    return pdf_extract


def _parse_page_spec(spec: str) -> list[int]:
    """'1-5' → [0,1,2,3,4], 'all' → [0..999], '1,3,5' → [0,2,4]."""
    spec = spec.strip()
    if spec == "all":
        return list(range(1000))
    if "-" in spec:
        start, end = spec.split("-", 1)
        return list(range(int(start) - 1, int(end)))
    if "," in spec:
        return [int(x.strip()) - 1 for x in spec.split(",")]
    return [int(spec) - 1]
