import copy
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from lmcore_sync import deadlines
from lmcore_sync.calendar_ics import build_ics
from lmcore_sync.moodle import LoginExpired


def row(**changes):
    value = {"course": "ABC123-9900-S1", "name": "Assignment 1", "type": "作业",
             "due": datetime(2099, 11, 24, 12, 0), "due_text": "", "status": "", "url": ""}
    value.update(changes)
    return value


class ReportsTest(unittest.TestCase):
    def test_pending_statuses_are_never_hidden(self):
        for status in ("Not submitted", "not yet submitted", "No submission", "Unsubmitted",
                       "Not graded", "Unfinished", "Draft", "未提交", "草稿"):
            with self.subTest(status=status):
                self.assertFalse(deadlines.is_done(status))
        for status in ("Submitted for grading", "Submitted for grading, not graded", "已提交", "Finished"):
            self.assertTrue(deadlines.is_done(status))

    def test_render_does_not_change_source_or_accumulate_manual_rows(self):
        rows = [row(weight="20%")]
        original = copy.deepcopy(rows)
        extra = [{"course": "ABC123", "name": "Exam", "due": "2099-12-01"}]
        first = deadlines.render(rows, extra)
        second = deadlines.render(rows, extra)
        self.assertEqual(rows, original)
        self.assertEqual(first, second)
        self.assertIn("20%", first)
        self.assertEqual(rows[0]["name"], "Assignment 1")

    def test_merge_is_idempotent_and_keeps_unrelated_same_day_item(self):
        rows = [row(), row(name="Different test", from_handbook=True)]
        extra = [{"course": "ABC123", "name": "Assignment 1", "due": "2099-11-24T14:00+08:00"}]
        deadlines.merge_extra(rows, extra)
        once = copy.deepcopy(rows)
        deadlines.merge_extra(rows, extra)
        self.assertEqual(rows, once)
        self.assertEqual(len(rows), 2)
        changed = next(r for r in rows if r["name"] == "Assignment 1")
        self.assertEqual(changed["due"], datetime.fromisoformat(extra[0]["due"]).astimezone().replace(tzinfo=None))

    def test_render_accepts_mixed_aware_dates_and_cross_drive_links(self):
        rows = [row(due=datetime(2099, 11, 24, tzinfo=timezone.utc)), row(name="Other")]
        with patch("os.path.relpath", side_effect=ValueError("different drives")):
            text = deadlines.render(rows, recent=[{"course": "ABC123", "date": "2099-11-24",
                                      "path": Path.cwd() / "lecture.pdf", "status": "新下载"}], base_dir=Path.cwd())
        self.assertIn("file:", text)
        self.assertIn("lecture.pdf", text)

    def test_invalid_manual_date_stays_unknown(self):
        values = deadlines.merge_extra([], [{"name": "Unscheduled", "due": "n/a"}])
        self.assertTrue(values[0]["unknown"])
        self.assertNotIn("BEGIN:VEVENT", build_ics(values, deadlines.is_done))

    def test_ics_uses_actual_deadline_and_no_invented_exam_duration(self):
        due = datetime(2099, 11, 24, 12, tzinfo=timezone(timedelta(hours=8)))
        text = build_ics([row(due=due, type="考试")], deadlines.is_done)
        self.assertIn("DTSTART:20991124T040000Z", text)
        self.assertNotIn("DTEND:", text)
        text = build_ics([row(due=due)], deadlines.is_done)
        self.assertIn("DTSTART:20991124T040000Z", text)
        self.assertIn("TRIGGER:-PT2H", text)

    def test_calendar_identity_survives_display_weight_date_and_session_change(self):
        a = row(url="https://example.edu/mod/assign/view.php?id=17&sesskey=old", weight="20%")
        b = row(url="https://example.edu/mod/assign/view.php?id=17&sesskey=new", name="Assignment 1（占总评 30%）",
                due=datetime(2099, 12, 1))
        uid = lambda text: next(line for line in text.splitlines() if line.startswith("UID:"))
        self.assertEqual(uid(build_ics([a], deadlines.is_done)), uid(build_ics([b], deadlines.is_done)))
        a["url"] = b["url"] = ""
        self.assertEqual(uid(build_ics([a], deadlines.is_done)), uid(build_ics([b], deadlines.is_done)))

    def test_calendar_date_only_unknown_time_and_utf8_folding(self):
        text = build_ics([row(name="长标题" * 50 + "\r\nHidden:yes", date_only=True)], deadlines.is_done)
        self.assertIn("DTSTART;VALUE=DATE:20991124", text)
        self.assertIn("DTEND;VALUE=DATE:20991125", text)
        self.assertNotIn("TRIGGER:-PT2H", text)
        self.assertNotIn("\r\nHidden:yes", text)
        self.assertIn("具体时间待确认", text)
        self.assertTrue(all(len(line.encode("utf-8")) <= 75 for line in text.split("\r\n")))
        self.assertTrue(text.endswith("\r\n"))

    def test_invalid_moodle_date_does_not_abort_report(self):
        self.assertIsNone(deadlines.parse_moodle_date("31 February 2026, 14:00", datetime(2026, 1, 1)))
        self.assertEqual(deadlines.parse_moodle_date("29 February, 14:00", datetime(2025, 1, 1)),
                         datetime(2024, 2, 29, 14))


class AuthenticationPropagationTest(unittest.IsolatedAsyncioTestCase):
    async def test_network_failure_warns_but_preserves_other_source_rows(self):
        class PartialMoodle:
            base = "https://example.edu"
            broken = True
            def __init__(self):
                self.messages = []
                self.log = self.messages.append
            async def html(self, url):
                if "/assign/" in url and self.broken:
                    raise TimeoutError("secret request URL should not be logged")
                return ("<div id='region-main'><table><thead><tr><th>Name</th><th>Due date</th>"
                        "</tr></thead><tbody><tr><td>Quiz</td><td>24 November 2099, 12:00</td>"
                        "</tr></tbody></table></div>", url)
            async def course_structure(self, course_id):
                return []
            async def ajax(self, method, args):
                return {"events": []}
        moodle = PartialMoodle()
        values = await deadlines.collect(moodle, [{"id": 1, "shortname": "ABC123"}])
        self.assertEqual(len(values), 1)
        self.assertEqual(values[0]["name"], "Quiz")
        self.assertEqual(len(moodle.deadline_warnings), 1)
        self.assertIn("ABC123", moodle.deadline_warnings[0])
        self.assertIn("TimeoutError", moodle.deadline_warnings[0])
        self.assertTrue(moodle.messages)
        self.assertNotIn("secret", moodle.messages[0])
        moodle.broken = False
        await deadlines.collect(moodle, [{"id": 1, "shortname": "ABC123"}])
        self.assertEqual(moodle.deadline_warnings, [])

    async def test_login_expiry_is_propagated_at_every_collection_stage(self):
        class FakeMoodle:
            base = "https://example.edu"
            def __init__(self, stage):
                self.stage = stage
                self.structures = 0
            async def html(self, url):
                if self.stage == "html":
                    raise LoginExpired("expired")
                return "<html></html>", url
            async def course_structure(self, course_id):
                self.structures += 1
                if self.stage == "structure":
                    raise LoginExpired("expired")
                if self.stage == "structure_second" and self.structures == 2:
                    raise LoginExpired("expired")
                if self.stage == "fetch":
                    return [{"id": 2, "modname": "resource", "name": "Module handbook"}]
                return []
            async def fetch(self, url):
                raise LoginExpired("expired")
            async def ajax(self, method, args):
                raise LoginExpired("expired")
        for stage in ("html", "structure", "structure_second", "fetch", "ajax"):
            with self.subTest(stage=stage):
                with self.assertRaises(LoginExpired):
                    await deadlines.collect(FakeMoodle(stage), [{"id": 1, "shortname": "ABC123"}])


if __name__ == "__main__":
    unittest.main()
