import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from lmcore_sync.moodle import LoginExpired
from lmcore_sync.organize import sha256_bytes
from lmcore_sync.sync import CourseSync, stable_key, file_key


class SyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.folder = self.root / "course"
        self.folder.mkdir()
        self.moodle = AsyncMock()
        self.cs = CourseSync(self.moodle, {"id": 1, "shortname": "ABC123"}, self.folder,
                             self.root / "data", {}, log=lambda _: None)

    async def save(self, revision, body, url=None):
        url = url or f"https://school/pluginfile.php/1/mod_resource/content/{revision}/lecture.pdf"
        self.moodle.fetch.return_value = (body, url, {"content-type": "application/pdf"})
        await self.cs.save_file(url, "lecture", "Week 1", "resource")
        return url.split("?")[0]

    async def test_three_same_day_revisions_preserve_every_version(self):
        await self.save(1, b"version one")
        await self.save(2, b"version two")
        key = await self.save(3, b"version three")
        self.assertEqual(len(self.cs.state["items"]), 1)
        current = self.folder / self.cs.state["items"][key]["path"]
        self.assertEqual(current.read_bytes(), b"version three")
        self.assertEqual({p.read_bytes() for p in (current.parent / "_旧版本").iterdir()},
                         {b"version one", b"version two"})

    async def test_user_edits_are_preserved(self):
        key = await self.save(1, b"original")
        path = self.folder / self.cs.state["items"][key]["path"]
        path.write_bytes(b"my annotations")
        new = await self.save(2, b"teacher update")
        self.assertEqual(path.read_bytes(), b"my annotations")
        self.assertNotEqual(self.cs.state["items"][new]["path"], str(path.relative_to(self.folder)))

    async def test_existing_user_file_is_not_replaced(self):
        personal = self.folder / "my lecture.pdf"
        personal.write_bytes(b"original")
        self.cs = CourseSync(self.moodle, {"id": 1}, self.folder, self.root / "data", {}, log=lambda _: None)
        first = await self.save(1, b"original")
        self.assertFalse(self.cs.state["items"][first]["managed"])
        await self.save(2, b"new upload")
        self.assertEqual(personal.read_bytes(), b"original")

    async def test_refresh_same_url_archives_previous_version(self):
        key = await self.save(1, b"first")
        self.cs.opts["refresh"] = True
        await self.save(1, b"second")
        path = self.folder / self.cs.state["items"][key]["path"]
        self.assertEqual(path.read_bytes(), b"second")
        self.assertEqual([p.read_bytes() for p in (path.parent / "_旧版本").iterdir()], [b"first"])

    async def test_same_filename_and_size_is_not_duplicate(self):
        (self.folder / "lecture.pdf").write_bytes(b"AAAA")
        self.cs = CourseSync(self.moodle, {"id": 1}, self.folder, self.root / "data", {}, log=lambda _: None)
        key = await self.save(1, b"BBBB")
        self.assertEqual((self.folder / self.cs.state["items"][key]["path"]).read_bytes(), b"BBBB")

    async def test_failed_scan_preserves_untouched_state(self):
        self.cs.state["items"]["old-unvisited"] = {"path": "old.pdf"}
        self.moodle.course_structure.return_value = [{"id": 2, "modname": "resource", "name": "x", "section": ""}]
        self.moodle.head.side_effect = RuntimeError("unreachable")
        self.moodle.fetch.side_effect = RuntimeError("network failure")
        self.moodle.base = "https://school"
        await self.cs.run()
        state = json.loads(self.cs.state_file.read_text(encoding="utf-8"))
        self.assertIn("old-unvisited", state["items"])

    async def test_login_expiry_is_not_swallowed(self):
        self.moodle.fetch.side_effect = LoginExpired("expired")
        with self.assertRaises(LoginExpired):
            await self.cs.save_file("https://school/pluginfile.php/1/a/b/1/x", "x", "", "resource")

    async def test_cached_item_does_not_report_new_download(self):
        await self.save(1, b"same")
        self.moodle.fetch.reset_mock()
        await self.save(1, b"same")
        self.moodle.fetch.assert_not_called()
        self.assertEqual(self.cs.rows[-1]["status"], "已有(未变化)")

    def test_stable_key_only_normalizes_resource_revisions(self):
        self.assertEqual(stable_key("https://s/pluginfile.php/1/mod_resource/content/2/a.pdf"),
                         stable_key("https://s/pluginfile.php/1/mod_resource/content/3/a.pdf"))
        self.assertNotEqual(stable_key("https://s/pluginfile.php/1/mod_forum/attachment/2/a.pdf"),
                            stable_key("https://s/pluginfile.php/1/mod_forum/attachment/3/a.pdf"))

    async def test_head_failure_does_not_merge_resource_ids(self):
        self.moodle.head.side_effect = RuntimeError("HEAD unsupported")
        for item, content in ((101, b"file one"), (102, b"file two")):
            url = f"https://school/mod/resource/view.php?id={item}&redirect=1"
            self.moodle.fetch.return_value = (content, url, {"content-type": "application/pdf", "content-disposition": f'attachment; filename="{item}.pdf"'})
            await self.cs.save_file(url, str(item), "", "resource")
        self.assertEqual(len(self.cs.state["items"]), 2)
        self.assertEqual({(self.folder / v["path"]).read_bytes() for v in self.cs.state["items"].values()}, {b"file one", b"file two"})

    async def test_get_redirect_records_real_resource_revision(self):
        self.moodle.head.side_effect = RuntimeError("HEAD unsupported")
        final = "https://school/pluginfile.php/1/mod_resource/content/9/lecture.pdf"
        self.moodle.fetch.return_value = (b"document", final, {"content-type": "application/pdf"})
        await self.cs.save_file("https://school/mod/resource/view.php?id=101", "lecture", "", "resource")
        self.assertIn(final, self.cs.state["items"])

    def test_file_key_keeps_resource_identity_query(self):
        self.assertEqual(file_key("https://s/mod/resource/view.php?id=1&redirect=1"), "https://s/mod/resource/view.php?id=1")
        self.assertNotEqual(file_key("https://s/mod/resource/view.php?id=1"), file_key("https://s/mod/resource/view.php?id=2"))
