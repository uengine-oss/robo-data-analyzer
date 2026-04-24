"""PDF 추출 도구 — pdfplumber 로 페이지별 텍스트 + 표 반환."""

import io

from langchain_core.tools import tool


def make_pdf_extract(state):
    @tool
    def pdf_extract(pages: str = "1-5") -> dict:
        """PDF 에서 지정된 페이지들의 텍스트와 표를 추출.

        Args:
            pages: "1-5" (범위), "all" (전체), "1,3,5" (복수), "6-10" 등.
                   큰 PDF 는 페이지 구간 나눠서 여러 번 호출 권장.

        Returns:
            {"total_pages": int, "pages": [{"page": N, "text": "...", "tables": [[...]]}, ...]}
        """
        try:
            import pdfplumber
        except ImportError:
            return {"error": "pdfplumber 미설치."}

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
                    result["pages"].append({
                        "page": idx + 1,
                        "text": page.extract_text() or "",
                        "tables": page.extract_tables() or [],
                    })
        except Exception as e:
            return {"error": f"PDF 읽기 실패: {type(e).__name__}: {e}"}

        state.last_extracted = result
        return result
    return pdf_extract


def _parse_page_spec(spec: str) -> list[int]:
    """'1-5' → [0..4], 'all' → [0..], '1,3,5' → [0,2,4]."""
    spec = spec.strip().lower()
    if spec == "all":
        return list(range(10000))
    if "-" in spec:
        start, end = spec.split("-", 1)
        return list(range(int(start) - 1, int(end)))
    if "," in spec:
        return [int(x.strip()) - 1 for x in spec.split(",")]
    return [int(spec) - 1]
