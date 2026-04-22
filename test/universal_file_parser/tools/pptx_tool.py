"""PPTX 추출 도구 — python-pptx 로 슬라이드별 제목/본문/표 반환."""

import io

from langchain_core.tools import tool


def make_pptx_extract(state):
    @tool
    def pptx_extract(slide_range: str = "all") -> dict:
        """PPTX 에서 슬라이드별 제목/본문 텍스트/표를 추출.

        Args:
            slide_range: "all" | "1-5" | "1,3,5"

        Returns:
            {
                "total_slides": int,
                "slides": [
                    {"slide": 1, "title": "...", "texts": [...], "tables": [[...]]},
                    ...
                ]
            }
        """
        try:
            from pptx import Presentation
        except ImportError:
            return {"error": "python-pptx 미설치. `pip install python-pptx` 필요."}

        try:
            prs = Presentation(io.BytesIO(state.data))
        except Exception as e:
            return {"error": f"PPTX 읽기 실패: {type(e).__name__}: {e}"}

        slide_indices = _parse_range(slide_range)
        total = len(prs.slides)
        result = {"total_slides": total, "slides": []}

        for idx in slide_indices:
            if idx >= total:
                continue
            slide = prs.slides[idx]
            title = ""
            texts = []
            tables = []

            for shape in slide.shapes:
                try:
                    if shape.has_text_frame:
                        text = shape.text_frame.text
                        if shape == slide.shapes.title:
                            title = text
                        elif text.strip():
                            texts.append(text)
                    if shape.has_table:
                        table_data = []
                        for row in shape.table.rows:
                            table_data.append([cell.text for cell in row.cells])
                        tables.append(table_data)
                except Exception:
                    continue

            result["slides"].append({
                "slide": idx + 1,
                "title": title,
                "texts": texts,
                "tables": tables,
            })

        state.last_extracted = result
        return result
    return pptx_extract


def _parse_range(spec: str) -> list[int]:
    spec = spec.strip()
    if spec == "all":
        return list(range(1000))
    if "-" in spec:
        start, end = spec.split("-", 1)
        return list(range(int(start) - 1, int(end)))
    if "," in spec:
        return [int(x.strip()) - 1 for x in spec.split(",")]
    return [int(spec) - 1]
