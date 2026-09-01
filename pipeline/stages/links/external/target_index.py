"""참조 대상 사전 — 무엇이 참조당할 수 있는가를 문자열 하나로 조회한다 (spec 081).

조각 이름표·파일 이름·경로 접미를 한 사전에 모아 두면, 링킹은 언어나 프레임워크를
몰라도 **조회 한 번**으로 끝난다. 자바의 ``getBean("id")``, C 의 ``fopen("a/b.dat")``,
PL/SQL 의 ``UTL_FILE.FOPEN('DIR','recv.dat')`` 이 같은 경로를 탄다.

키 문자셋의 단일 진실은 ``span_common`` 이다 — 여기서 다시 정의하지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Literal

from pipeline.models.external_source import ExternalSourceFile, ExternalSourceRecord
from pipeline.stages.external.parsers.span_common import is_reference_key


# 키에 못 들어가는 문자 = 구분자. ``_KEY_PATTERN`` 의 허용 집합에서 기계적으로 유도한다.
_DELIMITER = re.compile(r"[^\w.\-\[\]가-힣]")


@dataclass(frozen=True)
class TargetHit:
    """조회 결과 — 확정된 대상 노드 하나. 확정하지 못하면 조회는 None 을 낸다."""
    target_id: str
    matched_key: str
    target_kind: Literal["external_source_record", "external_file"]


def suffix_candidates(value: str) -> list[str]:
    """값 자체 + 구분자 경계에서 시작하는 접미들 중 키 자격을 갖춘 것만, 긴 것부터.

    ``java:comp/env/ejb/LoanApplicationSession`` → ``LoanApplicationSession``.
    부분 문자열은 만들지 않는다 — 반드시 구분자 **바로 뒤**에서 시작해 끝까지다.

    공백이 있는 값은 분해하지 않는다. 식별자와 경로에는 공백이 없고, 공백이 있는 값은
    사람이 읽는 문장이다. 실측에서 ``" for order "`` 의 끝 단어가 엔티티 이름 ``order`` 와
    겹쳐 오연결 6건이 발생했다(ofbiz).
    """
    candidates: list[str] = []
    if is_reference_key(value):
        candidates.append(value)
    if any(char.isspace() for char in value):
        return candidates
    for delimiter in _DELIMITER.finditer(value):
        tail = value[delimiter.end():]
        if tail and tail not in candidates and is_reference_key(tail):
            candidates.append(tail)
    return candidates


def _normalized_path(value: str) -> str:
    path = value.replace("\\", "/").strip()
    while path.startswith("./"):
        path = path[2:]
    return path.strip("/")


class TargetIndex:
    """조각 이름표·파일 이름·경로 접미의 통합 조회 사전."""

    def __init__(
        self,
        records: Iterable[ExternalSourceRecord],
        files: Iterable[ExternalSourceFile],
    ) -> None:
        self._records_by_key: dict[str, list[ExternalSourceRecord]] = {}
        self._files_by_name: dict[str, list[ExternalSourceFile]] = {}
        self._files_by_path: dict[str, list[ExternalSourceFile]] = {}
        for record in records:
            if record.provenance != "source_span":
                continue                      # derived 청크는 대상이 아니다 (078 계약)
            # 조회 주소인 키만 대상이다. 스키마 필드명처럼 이름만 겹치는 키는 제외 (spec 081).
            for key in record.addressable_keys:
                self._records_by_key.setdefault(key, []).append(record)
        for file in files:
            path = _normalized_path(file.project_relative_path)
            if not path:
                continue
            segments = path.split("/")
            for index in range(len(segments)):
                self._files_by_path.setdefault("/".join(segments[index:]), []).append(file)
            name = segments[-1]
            # 확장자 없는 이름은 흔한 단어와 우연히 겹치기 쉬워 이름 조회 대상에서 뺀다.
            if "." in name:
                self._files_by_name.setdefault(name, []).append(file)

    def lookup(self, value: str, *, source_file: str = "") -> list[TargetHit]:
        """확정 문자열 하나를 조회한다. 대상이 없으면 빈 목록(미연결).

        조각이 여럿 걸리면 **전부** 돌려준다. 여기까지 온 키는 이미 주소성 게이트를
        통과했으므로 우연 일치가 아니라 같은 주소의 판본들(다국어 메시지, 프로파일별
        설정 등)이며, 어느 하나를 임의로 고르는 것보다 전부 잇는 것이 정직하다.
        """
        raw = value.strip()
        if not raw:
            return []
        if "/" in raw or "\\" in raw:
            hit = self._by_path(_normalized_path(raw), source_file)
            if hit is not None:
                return [hit]
        for candidate in suffix_candidates(raw):
            records = self._records_by_key.get(candidate)
            if records:
                # 후보가 하나라도 잡히면 여기서 끝낸다. 더 짧은 후보로 내려가면 우연히
                # 겹치는 흔한 토막을 집게 되어 오연결이 된다.
                return [
                    TargetHit(
                        record.source_record_id,
                        candidate,
                        "external_source_record",
                    )
                    for record in {
                        record.source_record_id: record for record in records
                    }.values()
                ]
            files = self._files_by_name.get(candidate)
            if files:
                hit = self._single_file(candidate, files, source_file)
                return [hit] if hit else []
        return []

    def _by_path(self, path: str, source_file: str) -> TargetHit | None:
        files = self._files_by_path.get(path)
        if not files:
            return None
        return self._single_file(path, files, source_file)

    @staticmethod
    def _single_file(
        matched_key: str,
        files: list[ExternalSourceFile],
        source_file: str,
    ) -> TargetHit | None:
        targets = {
            file.source_file_id
            for file in files
            if _normalized_path(file.project_relative_path) != source_file
        }
        if len(targets) != 1:
            return None
        return TargetHit(targets.pop(), matched_key, "external_file")
