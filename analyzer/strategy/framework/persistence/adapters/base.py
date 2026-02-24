"""Base adapter for persistence layer."""

from abc import ABC, abstractmethod
from typing import Any

from analyzer.strategy.framework.persistence.models import PersistenceResult


class BasePersistenceAdapter(ABC):
    """각 소스(SQL, JPA, Ofbiz 등)에서 Table/Column 추출."""

    @abstractmethod
    def extract(
        self,
        chunks: list[Any],
        ast_context: list[Any],
        db: str = "",
    ) -> PersistenceResult:
        """소스에서 테이블/컬럼 추출. 해당 소스 없으면 빈 결과 반환."""
        pass
