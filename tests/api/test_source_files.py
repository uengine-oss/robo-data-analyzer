"""「코드」 화면의 파일 목록을 서버에서 되살린다 — 다른 브라우저에서도 보여야 한다.

목록이 브라우저 localStorage 에만 있어서, 같은 프로젝트를 다른 PC 에서 열면 코드
그래프는 있는데 「코드」 화면이 비어 있었다(2026-11-18 golden-1117).
"""
from __future__ import annotations

import contextvars
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from starlette.requests import Request

import api.analyze_router as analyze_router
import api.source_files as source_files
import shared.config.workspace as workspace


def _request(database: str | None) -> Request:
    headers = [(b"x-neo4j-database", database.encode())] if database else []
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers})


class SourceFilesTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        # 작업 폴더 뿌리만 임시 폴더로 바꾼다(SETTINGS 는 frozen 이라 모듈 이름을 바꿔 낀다).
        patcher = mock.patch.object(
            workspace, "SETTINGS", SimpleNamespace(analysis_data=SimpleNamespace(dir=self.root))
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def _put(self, workspace: str, rel: str, data: bytes) -> None:
        path = self.root / "projects" / workspace / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    async def _call(self, database: str | None) -> dict:
        ctx = contextvars.copy_context()
        return await ctx.run(lambda: analyze_router.source_files(_request(database)))

    async def test_returns_upload_shape_for_this_project_only(self) -> None:
        self._put("prj_a", "source/sample/src/A.java", "class A {}".encode())
        self._put("prj_a", "ddl/schema.sql", b"CREATE TABLE T (ID INT);")
        self._put("prj_b", "source/B.java", b"class B {}")

        got = await self._call("prj_a")

        self.assertEqual(
            got["files"], [{"fileName": "sample/src/A.java", "fileContent": "class A {}"}]
        )
        self.assertEqual(
            got["ddlFiles"],
            [{"fileName": "schema.sql", "fileContent": "CREATE TABLE T (ID INT);"}],
        )
        self.assertFalse(got["truncated"])

    async def test_nothing_uploaded_is_empty_not_error(self) -> None:
        got = await self._call("prj_none")
        self.assertEqual(got, {"files": [], "ddlFiles": [], "truncated": False})

    async def test_unsafe_workspace_never_leaves_the_data_dir(self) -> None:
        # sanitize 를 못 넘는 값은 범위 없음(루트) — ../ 로 밖을 읽지 못한다.
        outside = self.root.parent / "secret.txt"
        got = await self._call("../" + outside.name)
        names = [f["fileName"] for f in got["files"]]
        self.assertNotIn("secret.txt", names)

    async def test_korean_legacy_encoding_is_decoded_like_the_parser(self) -> None:
        self._put("prj_k", "source/K.java", "// 연차".encode("euc-kr"))
        got = await self._call("prj_k")
        self.assertEqual(got["files"][0]["fileContent"], "// 연차")

    async def test_oversized_file_keeps_name_but_not_content(self) -> None:
        self._put("prj_big", "source/Big.java", b"x" * 10)
        with mock.patch.object(source_files, "MAX_FILE_BYTES", 5):
            got = await self._call("prj_big")
        self.assertEqual(got["files"][0]["fileName"], "Big.java")
        self.assertEqual(got["files"][0]["fileContent"], source_files.TOO_LARGE_MARK)
        self.assertTrue(got["truncated"])

    @unittest.skipIf(os.name == "nt", "심볼릭 링크는 윈도에서 권한이 필요하다")
    async def test_symlink_out_of_the_folder_is_skipped(self) -> None:
        secret = self.root / "outside.txt"
        secret.write_text("secret")
        link = self.root / "projects" / "prj_l" / "source" / "link.txt"
        link.parent.mkdir(parents=True)
        link.symlink_to(secret)
        got = await self._call("prj_l")
        self.assertEqual(got["files"], [])


if __name__ == "__main__":
    unittest.main()
