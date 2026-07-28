# Research Artifacts — provenance 기록

## 보존 상태: **byte-exact 원본** (2026-07-23 교체 완료)

2026-07-22~23 사이 이 파일들은 **의미 보존 복원본**이었다. 사용자가 채팅에 첨부한 텍스트가
인코딩 손상(UTF-8 → Latin-1 표시) 상태로 도착해 byte-exact 보존이 불가능했기 때문이다.

2026-07-23 사용자가 원본 파일을 `D:\다운로드` 에 복원해 **전부 byte-exact 사본으로 교체했다.**
아래 파일은 모두 외부 Deep Research 산출물 원문 그대로다.

| 파일 | 원본 | 발행 |
|---|---|---|
| `deep-research-raw.md` | `compass_artifact_wf-6ebacb8d-3f72-543e-a374-5c970e80e0b6_text_markdown.md` | 2026-07-22 11:43 |
| `deep-research-1b-custom-access-pattern-discovery.md` | `compass_artifact_wf-5b34aacc-2735-5c3f-90a0-cd5e2a9dd4a5_text_markdown.md` | 2026-07-22 15:51 |
| `deep-research-2-structure-mining.md` | `compass_artifact_wf-98bbb1f1-b1c9-5f99-be55-5ad23cbf1766_text_markdown.md` | 2026-07-22 16:14 |
| `deep-research-3-indirect-reference-architecture.md` | `compass_artifact_wf-4b2d85c6-957d-52d8-9a52-4999dccee46e_text_markdown.md` | 2026-07-23 11:48 |
| `deep-research-4-db-procedure-file-references.md` | `compass_artifact_wf-314dadcc-2d11-5b65-9576-021194391d04_text_markdown.md` | 2026-07-23 12:32 |

5호·6호는 `../../081-unified-reference-resolution/research/` 에 있다(같은 날 발행, 같은 경위).

## 복원본이 원문에서 잃었던 것 (교체 시 실측 대조)

교체 전 복원본을 백업해 원문과 대조했다. **섹션 누락은 없었고**, 인용·URL·arXiv 번호 62개 중
**1개만 유실**돼 있었다.

- `deep-research-3` — `arXiv 2601.18844`
  > "state-of-the-art open-source SATs (e.g., CodeQL and Infer) exhibit over 95% false alarm
  > rates when detecting Null Pointer Dereference (NPD) bugs in large-scale projects such as
  > the Linux Kernel"

  맥락: 최신 SAST 도구는 FP0 이 아니라 soundness(FN0) 우선이라 오탐률이 매우 높고, 반대로
  OpenRewrite/Moderne LST("100% accurate method matching – No false positives")와
  IDE 프레임워크 지원(IntelliJ Spring facet, MyBatis 플러그인)이 정밀도 우선이라
  **우리 FP0 목표의 롤모델은 후자**라는 근거. 081 설계 방향과 일치하며 이 유실이 결론을
  바꾸지는 않았다.

그 밖에는 소제목 레벨(`##` vs `###`)과 문구 차이뿐이고 내용 구조는 동일했다.

## deep-research-1b 는 2026-07-23 에 뒤늦게 발견됐다

발행 시점(2026-07-22 15:51)에 저장소로 들어오지 못했고, 원본 복원 작업 중 `D:\다운로드` 에서
발견해 추가했다. **081 작업 기간 내내 이 문서는 참조되지 않았다.** 다만 내용은 081 이 별도로
도달한 설계를 독립적으로 뒷받침한다(sink-reachability 역전파, def-use 상수 전파를 단순 상수·
단순 연결까지만 하고 계산된 이름은 UNVERIFIED 로 포기, 리플렉션 콜사이트 중 49%만 문자열
상수 — Li/Tan/Xue TOSEM 2019).

## 인용 규칙

이 문서들의 claim 은 `../research.md` 에서 공식 출처·버전·라이선스·실제 repo spike 로
재검증한다. 리서치 문서 자체를 검증된 사실로 인용하지 않는다.
