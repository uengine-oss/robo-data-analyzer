# Feature Specification: DBMS 원격 CALL 신원 보존

**Status**: Draft  
**Created**: 2026-08-11  
**Upstream**: antlr-code-parser spec 019

## 1. 목적

parser가 보낸 `routine@dblink` CALL을 로컬 스키마 소속으로 창작하지 않고,
DBLINK가 포함된 외부 호출 신원을 CALLS 끝점에 보존한다.

## 2. 첫 오좌표

`KWMS_DATE_CHANGE@DAON`은 기존 CallLinker에서 로컬 정의와 일치하지 않아 external이 되지만,
점(`.`) 수식어가 없다는 이유로 호출자 폴더 `NBEAVER`를 callee schema로 붙인다. 실제 원격
메타데이터가 없는 상태에서 이는 거짓 소속이다.

## 3. 요구사항

- FR-001: 따옴표 밖 `@dblink`가 있는 CALL은 external로 유지한다.
- FR-002: 외부 callee id/name은 `routine@dblink`를 그대로 보존한다.
- FR-003: 원격 CALL에는 호출자 로컬 schema/package를 붙이지 않는다.
- FR-004: 원격 package 이름이 로컬 builtin 이름과 같아도 호출 사실을 드롭하지 않는다.
- FR-005: 따옴표 식별자 내부 `@`는 DBLINK로 오인하지 않는다.

## 4. 수락 기준

- 원격 bare/package CALL, 로컬 builtin, quoted-identifier 반대 사례가 통과한다.
- 물 코퍼스 원격 CALL 9건이 모두 external이며 링크 포함 신원이 유지된다.

