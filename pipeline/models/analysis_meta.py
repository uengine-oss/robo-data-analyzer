"""분석 입력과 실행 메타데이터.

분석 1회의 입력 단위. 디렉토리 경로 + strategy (+ 옵션 datasource).

★ 가공 식별자(``name``) 폐기. 진실은 ``root`` 경로.
catalog **접속 설정**(``base_url`` / ``enabled``)은 운영 영역 (``shared.config.settings`` 의 ``ROBO_CATALOG_*``) —
분석기는 URL 호출·응답 처리만 책임진다. 단 ``datasource`` ("이번 분석에서 어느 DB 를 샘플링")는
분석 1회의 **입력값** 이라 여기 둔다 ("접속 설정"=SETTINGS / "어느 DB"=입력 으로 구분).
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Literal, Optional
from uuid import uuid4


ANALYSIS_SCOPE_ID_SCHEME = "analysis-scope/v2"
_SHA256_HEX = frozenset("0123456789abcdef")


def _canonical_source_root(root: Path) -> str:
    """Return the host-canonical source-root identity used by graph generations.

    A scope must survive repeated runs of the same source population, so it
    cannot contain a run ID or a source-content hash.  Resolving aliases and
    applying the host filesystem's case rules makes the root identity
    deterministic without inventing a second project name.
    """
    return os.path.normcase(str(root.expanduser().resolve())).replace("\\", "/")


def analysis_scope_id_for(
    *,
    root: Path,
    strategy: str,
    selected_source_ids: tuple[str, ...] = (),
) -> str:
    """Build the stable graph scope ID for one exact source population."""
    selected = normalize_selected_source_ids(selected_source_ids)
    source_population: dict[str, object] = (
        {"mode": "selected", "source_ids": list(selected)}
        if selected
        else {"mode": "complete"}
    )
    payload = json.dumps(
        {
            "scheme": ANALYSIS_SCOPE_ID_SCHEME,
            "source_root": _canonical_source_root(root),
            "source_population": source_population,
            "strategy": strategy,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{ANALYSIS_SCOPE_ID_SCHEME}:{hashlib.sha256(payload).hexdigest()}"


def normalize_selected_source_ids(values: object) -> tuple[str, ...]:
    """Validate an explicit partial-upload population without touching source.

    Source IDs use the Parser's POSIX-relative ``filePath`` identity.  Sorting
    makes the selection itself deterministic; it does not change source order
    inside any selected file.
    """
    if values is None:
        return ()
    if isinstance(values, str) or not isinstance(values, (list, tuple, set)):
        raise ValueError("selected_source_ids must be a list of relative source IDs")
    normalized: list[str] = []
    for raw_value in values:
        if not isinstance(raw_value, str) or not raw_value.strip():
            raise ValueError("selected_source_ids entries must be non-blank strings")
        value = raw_value.strip().replace("\\", "/")
        path = PurePosixPath(value)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError(
                f"selected source ID must be a clean relative path: {raw_value!r}"
            )
        normalized.append(path.as_posix())
    if len(normalized) != len(set(normalized)):
        raise ValueError("selected_source_ids must not contain duplicates")
    return tuple(sorted(normalized))


def _validate_optional_content_reference(
    *,
    ref: Optional[Path],
    sha256: Optional[str],
    field_name: str,
) -> None:
    if (ref is None) != (sha256 is None):
        raise ValueError(f"{field_name}_ref and {field_name}_sha256 must be supplied together")
    if sha256 is not None:
        normalized = sha256.strip().lower()
        if len(normalized) != 64 or any(char not in _SHA256_HEX for char in normalized):
            raise ValueError(f"{field_name}_sha256 must be a SHA-256 hex digest")


@dataclass(frozen=True)
class Codebase:
    """분석 대상 codebase.

    Fields:
        root: 실제로 분석할 소스 디렉토리.
        corpus_root: 그 소스와 Parser 산출물을 함께 담은 corpus 디렉토리.
            둘을 명시해야 다른 실행의 source/AST가 섞이지 않는다.
        strategy: ``"framework"`` 또는 ``"dbms"`` — ``pipeline/strategies.py`` 의
            ``FRAMEWORK_PIPELINE`` / ``DBMS_PIPELINE`` 과 매칭.
        ddl_dir: 별도 DDL 디렉토리 (None 이면 root 안에서 자동 탐색).
        locale: LLM 응답 언어 (``"ko"`` / ``"en"``).
        datasource: 메타 보강(샘플데이터·설명) 시 샘플링할 DB(MindsDB datasource).
            분석 1회의 **입력값** (운영=AnalyzeRequest / CLI 테스트=SETTINGS.catalog_api.test_datasource).
            None = 미지정 → 샘플 보강 skip. (catalog 접속 base_url/enabled 는 운영 SETTINGS.)
    """
    root: Path
    corpus_root: Path
    strategy: Literal["framework", "dbms"]
    ddl_dir: Optional[Path] = None
    locale: str = "ko"
    datasource: Optional[str] = None
    # Empty means the complete source population.  Non-empty is an explicit
    # partial upload with its own stable analysis scope.
    selected_source_ids: tuple[str, ...] = ()
    # Validation-only immutable references. Product runs may omit them; a sealed
    # direct-meaning/baseline run supplies each path together with its expected bytes hash.
    validation_baseline_ref: Optional[Path] = None
    validation_baseline_sha256: Optional[str] = None
    validation_answer_key_ref: Optional[Path] = None
    validation_answer_key_sha256: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "selected_source_ids",
            normalize_selected_source_ids(self.selected_source_ids),
        )
        _validate_optional_content_reference(
            ref=self.validation_baseline_ref,
            sha256=self.validation_baseline_sha256,
            field_name="validation_baseline",
        )
        _validate_optional_content_reference(
            ref=self.validation_answer_key_ref,
            sha256=self.validation_answer_key_sha256,
            field_name="validation_answer_key",
        )

    @property
    def analysis_scope_id(self) -> str:
        """Stable scope shared by runs of this exact source population."""
        return analysis_scope_id_for(
            root=self.root,
            strategy=self.strategy,
            selected_source_ids=self.selected_source_ids,
        )


@dataclass(frozen=True)
class AnalysisRun:
    """한 번의 분석 실행을 식별하고 산출물 위치를 고정한다."""

    run_id: str
    codebase: Codebase
    started_at: datetime
    run_dir: Path

    @property
    def analysis_scope_id(self) -> str:
        """Stable codebase scope; unlike ``run_id`` this does not change per run."""
        return self.codebase.analysis_scope_id

    @classmethod
    def now(cls, codebase: Codebase, run_output_root: Path) -> "AnalysisRun":
        """현재 UTC 시각으로 새 실행 메타를 만든다.

        산출물은 루트 바로 아래가 아니라 ``root/<run_id>/`` 에 격리된다. run 끼리
        서로의 증거를 지우지 않아야 전후 비교가 백업 없이 성립한다.
        """
        now = datetime.now(timezone.utc)
        run_id = f"{now.strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:12]}"
        return cls(
            run_id=run_id,
            codebase=codebase,
            started_at=now,
            run_dir=run_output_root / run_id,
        )
