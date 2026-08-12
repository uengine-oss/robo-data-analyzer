# Implementation Plan: DBMS 원격 CALL 신원 보존

1. CallLinker의 raw callee 해석과 external stub 필드를 고정한다.
2. 따옴표 밖 DBLINK 식별을 단일 helper로 만든다.
3. builtin/로컬 package 귀속보다 원격 신원을 우선한다.
4. 국소·전체 테스트와 물 코퍼스 preflight로 검증한다.

