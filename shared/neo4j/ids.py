"""노드 식별자(FQN) 단일진실 — 빈 구성요소 생략 (spec 024).

DB 노드 id = ``db.schema.name[.column]``. db 등 구성요소가 빈값이면 그 자리를 점으로
남기지 않는다(선두/중복 점 0). 생성(graph_nodes·ddl 파서)과 후처리(enrich)가 **같은
함수**를 써야 식별자가 어긋나지 않는다. 전략무관·결정론.
"""
from __future__ import annotations


def fqn(*parts: object) -> str:
    """비어있지 않은 조각만 ``.`` 으로 이어 식별자 생성. 빈 구성요소(빈 db 등)는 생략.

    fqn("", "RWIS", "RDD01DD_TB")             -> "RWIS.RDD01DD_TB"
    fqn("prod", "PUBLIC", "USERS")            -> "prod.PUBLIC.USERS"
    fqn("", "RWIS", "RDD01DD_TB", "LOG_TIME") -> "RWIS.RDD01DD_TB.LOG_TIME"
    """
    segs = [str(p).strip() for p in parts if p is not None]
    return ".".join(s for s in segs if s)


if __name__ == "__main__":
    cases = [
        (("", "RWIS", "RDD01DD_TB"), "RWIS.RDD01DD_TB"),
        (("prod", "PUBLIC", "USERS"), "prod.PUBLIC.USERS"),
        (("", "RWIS", "RDD01DD_TB", "LOG_TIME"), "RWIS.RDD01DD_TB.LOG_TIME"),
        ((None, "RWIS", "T"), "RWIS.T"),
        (("", "", "T"), "T"),
    ]
    ok = True
    for args, want in cases:
        got = fqn(*args)
        flag = "OK" if got == want else "FAIL"
        if got != want:
            ok = False
        print(f"  [{flag}] fqn{args} = {got!r} (want {want!r})")
    print("ALL PASS" if ok else "SOME FAILED")
