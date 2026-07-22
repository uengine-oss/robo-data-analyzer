# 078 Context·Prompt Contract

## Step6 — external evidence 선택

- 입력: state의 `external_chunks`(EXTERNAL_CHUNK)·`external_edges`(ReferTo/HasChunk).
- routine별 evidence = 그 routine의 **outgoing ReferTo** target 청크만.
  청크→코드·문서 mention 등 다른 방향/종류의 유입 없음.
- 항목 순서: (file_path, char_start, id) 결정론 정렬.
- **통째-or-skip**: 남은 budget에 청크 전문+메타가 들어가면 전문 포함, 아니면 그 청크를
  통째로 생략하고 관측 로그(`skipped_oversize`)에 남긴다. `truncated` 필드·부분 절단 폐기.
- 각 evidence: `id, file_path, matched_key, semantic_kind, provenance, text(전문),
  start_line, end_line, content_hash`.
- 조립 전 hash 재검증 — 불일치는 fail-closed 예외 (silent drop 금지).

## Step7 — prompt

- 공통 system prefix는 한 실행의 병렬 요청 간 **byte-identical** (KV cache 계약).
  외부 evidence는 user suffix JSON에만 들어간다.
- `_common.yaml`의 외부 evidence 서술은 새 스키마(단일 REFER_TO·원문 slice·provenance)로
  갱신한다 — 계약 변경은 이 작업의 의도된 diff이며 테스트가 새 문구를 고정한다.
- 다른 routine의 evidence가 한 요청에 섞이지 않는다 (fanout 단위 검증).

## 검증

- unit: 선택·정렬·통째-or-skip·hash 실패 경로.
- 실물: 실제 LLM 호출의 SystemMessage/HumanMessage record에서
  (a) 연결 청크 원문 100% 포함, (b) 중간 절단 0, (c) 타 routine 원문 0,
  (d) 병렬 요청 system prefix byte 동일성.
