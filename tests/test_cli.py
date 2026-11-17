"""CLI integration checks against fake Moodle sessions and temporary course data."""
import json
import tempfile
import unittest
import zipfile
from contextlib import ExitStack
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from lmcore_sync import cli


class FakeMoodle:
    def __init__(self, logged_in=True, courses=None):
        self.logged_in = logged_in
        self.course_rows = courses or []
        self.course_requests = []
        self.deadline_warnings = []
        self.wait_for_login = AsyncMock(return_value=False)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def is_logged_in(self):
        return self.logged_in

    async def courses(self, classification="all"):
        self.course_requests.append(classification)
        return self.course_rows


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.ddl = self.home / "reports/作业DDL.md"
        self.ics = self.home / "reports/作业DDL.ics"
        self.cfg = {
            "base_url": "https://moodle.example.test",
            "ddl_file": str(self.ddl),
            "ics_file": str(self.ics),
            "courses": [{"match": "EEE339", "folder": str(self.home / "eee339")}],
        }
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name, value in (("HOME", self.home), ("CONFIG", self.home / "config.json"),
                            ("USER_DATA", self.home / "user_data"),
                            ("PROFILE", self.home / "user_data/browser_profile")):
            self.stack.enter_context(patch.object(cli, name, value))
        self.stack.enter_context(patch.object(cli, "load_config", return_value=self.cfg))

    def seed_reports(self):
        self.ddl.parent.mkdir(parents=True)
        self.ddl.write_text("# 上次完整报告\n\n- 作业 A：2030-01-01\n", encoding="utf-8")
        self.ics.write_bytes(b"BEGIN:VCALENDAR\r\nOLD COMPLETE CALENDAR\r\nEND:VCALENDAR\r\n")
        return self.ddl.read_text(encoding="utf-8"), self.ics.read_bytes()

    def test_noninteractive_expiry_exit2_preserves_reports_without_visible_browser(self):
        old_report, old_calendar = self.seed_reports()
        fake = FakeMoodle(logged_in=False)
        with patch.object(cli, "moodle", return_value=fake) as factory:
            # A second failure must replace, rather than duplicate, the stale banner.
            for _ in range(2):
                with self.assertRaises(SystemExit) as raised:
                    cli.main(["sync", "--non-interactive"])
                self.assertEqual(raised.exception.code, 2)
            self.assertTrue(all(call.kwargs.get("headless", True) for call in factory.call_args_list))
        fake.wait_for_login.assert_not_awaited()
        report = self.ddl.read_text(encoding="utf-8")
        self.assertIn(old_report, report)
        self.assertIn("登录已过期", report)
        self.assertEqual(report.count("<!-- lmcore-notice -->"), 1)
        self.assertEqual(self.ics.read_bytes(), old_calendar)
        self.assertIn("登录已过期", (self.home / "user_data/last_sync.log").read_text(encoding="utf-8"))

    def test_partial_deadline_failure_preserves_previous_complete_report(self):
        old_report, old_calendar = self.seed_reports()
        fake = FakeMoodle()
        fake.deadline_warnings = ["assignment source timed out"]
        with patch.object(cli.deadlines, "collect", AsyncMock(return_value=[])), patch.object(cli, "build_ics") as build:
            with self.assertRaisesRegex(RuntimeError, "部分 DDL 来源读取失败"):
                cli.asyncio.run(cli.write_ddl(self.cfg, fake, []))
            build.assert_not_called()
        self.assertIn(old_report, self.ddl.read_text(encoding="utf-8"))
        self.assertEqual(self.ics.read_bytes(), old_calendar)

    def test_login_timeout_reports_nonzero_exit(self):
        fake = FakeMoodle(logged_in=False)
        with patch.object(cli, "moodle", return_value=fake) as factory, patch.object(cli, "log"):
            with self.assertRaises(SystemExit) as raised:
                cli.main(["login"])
        self.assertEqual(raised.exception.code, 2)
        fake.wait_for_login.assert_awaited_once()
        factory.assert_called_once_with(self.cfg, headless=False)

    def test_updated_zip_converts_new_content_and_updates_manifest_link(self):
        folder = self.home / "course"
        lecture = folder / "02_Lectures"
        lecture.mkdir(parents=True)
        source = lecture / "week.zip"

        class RecordingConverter:
            def __init__(self):
                self.seen = []

            def convert_many(self, jobs):
                result = {}
                for src, dst in jobs:
                    body = src.read_bytes()
                    self.seen.append(body)
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    dst.write_text(body.decode("utf-8"), encoding="utf-8")
                    result[src] = "ok"
                return result

        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("notes.pdf", b"OLD VERSION")
        conv = RecordingConverter()
        first = cli.build_ai_copies({}, folder, None, conv, "06_AI_readable")
        old_dir = folder / first["02_Lectures/week.zip"]
        self.assertEqual(conv.seen, [b"OLD VERSION"])
        # Local edits to the previous extraction must survive a new remote ZIP.
        (old_dir / "notes.pdf").write_bytes(b"USER ANNOTATED OLD")
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("notes.pdf", b"NEW VERSION")
        conv.seen.clear()
        second = cli.build_ai_copies({}, folder, None, conv, "06_AI_readable")
        new_dir = folder / second["02_Lectures/week.zip"]
        self.assertEqual(conv.seen, [b"NEW VERSION"])
        self.assertNotEqual(first["02_Lectures/week.zip"], second["02_Lectures/week.zip"])
        self.assertEqual((new_dir / "notes.md").read_text(encoding="utf-8"), "NEW VERSION")
        self.assertEqual((old_dir / "notes.pdf").read_bytes(), b"USER ANNOTATED OLD")

    def test_auto_courses_and_string_layout_are_connected_to_sync_and_conversion(self):
        self.cfg.update({"auto_courses": True, "courses_root": str(self.home / "courses"), "layout_preset": "en"})
        self.cfg["courses"][0]["layout"] = "en"
        courses = [
            {"id": 1, "shortname": "EEE339-2627-S1", "fullname": "Digital Design", "url": "https://moodle.example.test/course/1"},
            {"id": 2, "shortname": "ADS305-2627-S1", "fullname": "Big Data", "url": "https://moodle.example.test/course/2"},
        ]
        fake = FakeMoodle(courses=courses)
        sessions = []

        class FakeCourseSync:
            def __init__(self, moodle, course, folder, state_dir, opts, log=None):
                self.layout = opts["layout"]
                self.rows, self.links, self.skipped_mods = [], [], []
                self.course, self.folder = course, folder
                sessions.append(self)

            async def run(self):
                return None

        with patch.object(cli, "moodle", return_value=fake), patch.object(cli, "CourseSync", FakeCourseSync), \
                patch.object(cli, "Converter", return_value=SimpleNamespace(engine="test converter")), \
                patch.object(cli, "build_ai_copies", return_value={}) as convert, \
                patch.object(cli.deadlines, "collect", AsyncMock(return_value=[])):
            cli.main(["sync", "--non-interactive"])
        self.assertEqual(len(sessions), 2)
        self.assertIn("inprogress", fake.course_requests)
        self.assertEqual(len(self.cfg["courses"]), 2)
        saved = json.loads((self.home / "config.json").read_text(encoding="utf-8"))
        self.assertTrue(saved["auto_courses"])
        self.assertEqual(len(saved["courses"]), 2)
        for session in sessions:
            self.assertIsInstance(session.layout, dict)
            self.assertEqual(session.layout["lecture"], "02_Lectures")
            self.assertTrue((session.folder / "02_Lectures").is_dir())
            self.assertTrue((session.folder / "06_AI_readable/00_AI目录.md").is_file())
            self.assertTrue((session.folder / "00_资料清单.md").is_file())
        self.assertTrue(all(call.args[-1] == "06_AI_readable" for call in convert.call_args_list))
        self.assertTrue(self.ddl.is_file())
        self.assertIn(b"BEGIN:VCALENDAR", self.ics.read_bytes())

    def test_recent_materials_excludes_old_unchanged_missing_and_duplicate_files(self):
        folder = Path(self.cfg["courses"][0]["folder"])
        (folder / ".lmcore").mkdir(parents=True)
        now = datetime.now()
        items = {}
        for key, days, status in (("new", 0, "新下载"), ("updated", 3, "已更新"),
                                  ("old", 8, "新下载"), ("unchanged", 0, "已有(未重复下载)")):
            (folder / (key + ".pdf")).write_bytes(b"test")
            items[key] = {"path": key + ".pdf", "date": (now - timedelta(days=days)).date().isoformat(), "status": status}
        items["missing"] = {"path": "missing.pdf", "date": now.date().isoformat(), "status": "新下载"}
        items["duplicate"] = dict(items["new"])
        (folder / ".lmcore/state.json").write_text(json.dumps({"items": items}), encoding="utf-8")
        recent = cli.recent_materials(self.cfg)
        self.assertEqual({r["path"].name for r in recent}, {"new.pdf", "updated.pdf"})
        self.assertEqual(len(recent), 2)

    def test_exact_semester_match_wins_and_bare_code_prefers_current(self):
        now = datetime.now().timestamp()
        courses = [
            {"id": 1, "shortname": "EEE339-2526-S1", "fullname": "Digital Design", "startdate": now - 5000, "enddate": now - 4000},
            {"id": 2, "shortname": "EEE339-2728-S1", "fullname": "Digital Design", "startdate": now + 2000, "enddate": now + 4000},
            {"id": 3, "shortname": "EEE339-2627-S1", "fullname": "Digital Design", "startdate": now - 1000, "enddate": now + 1000},
        ]
        self.assertEqual(cli.match_course(courses, "EEE339-2526-S1")["id"], 1)
        self.assertEqual(cli.match_course(courses, "EEE339-2627-S1")["id"], 3)
        self.assertEqual(cli.match_course(courses, "eee339")["id"], 3)

    def test_ai_index_is_rebuildable_and_uses_portable_relative_links(self):
        folder = self.home / "course"
        root = folder / "06_AI_readable"
        nested = root / "02_Lectures/Week 1"
        nested.mkdir(parents=True)
        (nested / "课件.md").write_text("# lecture", encoding="utf-8")
        (root / "00_AI目录.md").write_text("OLD INDEX", encoding="utf-8")
        cli.write_ai_index(folder, "06_AI_readable")
        first = (root / "00_AI目录.md").read_text(encoding="utf-8")
        self.assertIn("[02_Lectures/Week 1/课件.md](<02_Lectures/Week 1/课件.md>)", first)
        self.assertNotIn("00_AI目录.md", first)
        self.assertNotIn("OLD INDEX", first)
        cli.write_ai_index(folder, "06_AI_readable")
        self.assertEqual((root / "00_AI目录.md").read_text(encoding="utf-8"), first)


if __name__ == "__main__":
    unittest.main()
