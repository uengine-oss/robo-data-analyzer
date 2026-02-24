"""Persistence adapters - Table/Column extraction from various sources."""

from analyzer.strategy.framework.persistence.adapters.base import BasePersistenceAdapter
from analyzer.strategy.framework.persistence.adapters.sqlmap_adapter import SqlMapAdapter
from analyzer.strategy.framework.persistence.adapters.jpa_adapter import JpaAdapter

__all__ = ["BasePersistenceAdapter", "SqlMapAdapter", "JpaAdapter"]
