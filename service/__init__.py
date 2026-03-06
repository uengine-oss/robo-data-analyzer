"""서비스 레이어 모듈

비즈니스 로직을 담당하는 서비스들을 제공합니다.
- source_analyze_service: 소스 코드 분석
- graph_query_service: 그래프 데이터 조회/삭제
- schema_manage_service: 스키마 CRUD
- data_lineage_service: 데이터 리니지
- pipeline_control_service: 파이프라인 제어
- dw_schema_service: DW 스타스키마
- glossary_manage_service: 용어집 관리
- metadata_enrichment_service: 메타데이터 보강
- user_story_document: User Story 문서 생성
"""

__all__ = [
    "run_source_analysis",
    "discover_analyzable_files",
    "fetch_graph_data",
    "delete_graph_data",
    "check_graph_data_exists",
    "MetadataEnrichmentService",
    "generate_user_story_document",
    "aggregate_user_stories_from_results",
    "get_pipeline_status",
    "control_pipeline_action",
]


def __getattr__(name: str):
    """Lazy import로 순환 참조 방지"""
    if name in ("run_source_analysis", "discover_analyzable_files"):
        from .source_analyze_service import run_source_analysis, discover_analyzable_files
        return {"run_source_analysis": run_source_analysis, "discover_analyzable_files": discover_analyzable_files}[name]
    if name in ("fetch_graph_data", "delete_graph_data", "check_graph_data_exists"):
        from . import graph_query_service as _gqs
        return getattr(_gqs, name)
    if name == "MetadataEnrichmentService":
        from .metadata_enrichment_service import MetadataEnrichmentService
        return MetadataEnrichmentService
    if name in ("generate_user_story_document", "aggregate_user_stories_from_results"):
        from . import user_story_document as _usd
        return getattr(_usd, name)
    if name in ("get_pipeline_status", "control_pipeline_action"):
        from . import pipeline_control_service as _pcs
        return getattr(_pcs, name)
    raise AttributeError(f"module 'service' has no attribute '{name}'")
