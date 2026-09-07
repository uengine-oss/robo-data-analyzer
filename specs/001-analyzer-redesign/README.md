# Spec 001 - Analyzer 전면 재설계

**상태**: 구현·검증 완료
**기준일**: 2026-09-08

이 폴더는 Analyzer의 현재 목표와 계약만 설명하는 단일 SDD입니다.

| 문서 | 내용 |
|---|---|
| `spec.md` | 제품 요구사항과 완료 조건 |
| `plan.md` | 구현·검증 순서 |
| `tasks.md` | 현재 체크리스트 |
| `contracts/parser-contract.md` | Parser와 Analyzer의 책임 경계 |
| `contracts/graph-contract.md` | 공개 그래프의 노드·속성·관계 |
| `contracts/service-contract.md` | 관련 서비스의 소비 계약 |
| `CURRENT_STATUS.md` | 실제 진척과 남은 작업 |

구현과 문서가 다르면 구현을 몰래 정당화하지 않는다. 계약을 먼저 다시 판단하고 같은 변경에서 코드·테스트·문서를 맞춘다.
