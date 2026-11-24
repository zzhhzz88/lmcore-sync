import asyncio
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from lmcore_sync import past_papers as pp


def row(code, year, rid, title="Big Data Analytics"):
    return {"paperCode": f'<span style="color:red">{code}</span>', "year": year, "syS_FLD_SYSID": rid,
            "paperTitle": title.replace(" ", "&nbsp;")}


class FakeClient:
    """Library with ADS305 renamed from INT303; IOT307TC is a same-title campus variant."""

    def __init__(self, locked=False):
        self.locked, self.downloads = locked, 0
        self.rows = [row("INT303", "2023-2024", 1), row("INT303", "2024-2025", 2), row("INT303", "2024-2025", 3),
                     row("INT303", "2025-2026", 4), row("IOT307TC", "2025-2026", 9)]

    async def search(self, field, text):
        if field == "PaperCode":
            return [r for r in self.rows if pp.clean(r["paperCode"]) == text]
        return [r for r in self.rows if pp.clean(r["paperTitle"]) == text]

    async def detail(self, rid):
        return {"type": "R" if rid == 3 else "F", "semester": "S1", "flag": 0, "can_view": True}

    async def pdf(self, rid, flag):
        if self.locked:
            raise pp.Locked("请解锁后再使用")
        self.downloads += 1
        return b"%PDF-1.4 fake\n%%EOF"


COURSE = {"shortname": "ADS305-2627-S1", "fullname": "ADS305-2627-S1-Big Data Analytics"}


class PastPaperTests(unittest.TestCase):
    def test_title_strips_moodle_prefix(self):
        self.assertEqual(pp.module_title(COURSE["fullname"], COURSE["shortname"]), "Big Data Analytics")

    def test_renamed_module_found_by_title_and_campus_variant_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient()
            st = asyncio.run(pp.run_course(client, COURSE, Path(tmp), "05_考试复习", {}, log=lambda *_: None))
            self.assertEqual(st["searched_code"], "INT303")
            self.assertEqual(st["other_codes"], ["IOT307TC"])
            names = sorted(p.name for p in (Path(tmp) / "05_考试复习" / "往年试题").glob("*.pdf"))
            self.assertEqual(names, ["INT303_2023-2024_Final.pdf", "INT303_2024-2025_Final.pdf",
                                     "INT303_2024-2025_Resit.pdf", "INT303_2025-2026_Final.pdf"])
            self.assertEqual(st["status"], "done")

    def test_locked_library_writes_links_instead_of_forcing(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = asyncio.run(pp.run_course(FakeClient(locked=True), COURSE, Path(tmp), "05_考试复习", {}, log=lambda *_: None))
            self.assertEqual(st["status"], "partial")
            listing = (Path(tmp) / "05_考试复习" / "往年试题" / pp.LIST).read_text(encoding="utf-8")
            self.assertIn("PaperDetail/3/EXAMXJTLU/4", listing)
            self.assertFalse(list((Path(tmp) / "05_考试复习" / "往年试题").glob("*.pdf")))

    def test_existing_papers_are_not_downloaded_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "05_考试复习" / "往年试题"
            d.mkdir(parents=True)
            (d / "INT303_2025-2026_Final.pdf").write_bytes(b"%PDF mine")
            client = FakeClient()
            asyncio.run(pp.run_course(client, COURSE, Path(tmp), "05_考试复习", {}, log=lambda *_: None))
            self.assertEqual(client.downloads, 3)
            self.assertEqual((d / "INT303_2025-2026_Final.pdf").read_bytes(), b"%PDF mine")

    def test_runs_once_per_course(self):
        with tempfile.TemporaryDirectory() as tmp:
            sf = Path(tmp) / ".lmcore" / pp.STATE
            sf.parent.mkdir()
            sf.write_text(json.dumps({"status": "done"}), encoding="utf-8")
            self.assertIsNone(asyncio.run(pp.fetch_for_course(None, COURSE, Path(tmp), "05_考试复习", {})))
            sf.write_text(json.dumps({"status": "partial", "last_try": date.today().isoformat()}), encoding="utf-8")
            self.assertIsNone(asyncio.run(pp.fetch_for_course(None, COURSE, Path(tmp), "05_考试复习", {})))


if __name__ == "__main__":
    unittest.main()


class RateLockClient(FakeClient):
    async def pdf(self, rid, flag):
        if self.downloads >= 1:
            raise pp.RateLocked("Your account has been temporarily locked because bulk downloading activity was detected.")
        self.downloads += 1
        return b"%PDF-1.4 fake\n%%EOF"


class BulkDownloadProtectionTests(unittest.TestCase):
    def test_rate_lock_stops_at_once_and_keeps_what_was_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = RateLockClient()
            with self.assertRaises(pp.RateLocked):
                asyncio.run(pp.run_course(client, COURSE, Path(tmp), "05_考试复习", {}, log=lambda *_: None))
            d = Path(tmp) / "05_考试复习" / "往年试题"
            self.assertEqual(len(list(d.glob("*.pdf"))), 1)
            self.assertIn("锁定", (d / pp.LIST).read_text(encoding="utf-8"))

    def test_cooldown_blocks_until_it_expires(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.assertIsNone(pp.in_cooldown(home))
            pp.start_cooldown(home, "temporarily locked")
            self.assertIn("锁定", pp.in_cooldown(home))
            data = json.loads(pp.cooldown_file(home).read_text(encoding="utf-8"))
            data["until"] = "2000-01-01T00:00"
            pp.cooldown_file(home).write_text(json.dumps(data), encoding="utf-8")
            self.assertIsNone(pp.in_cooldown(home))

    def test_lock_message_is_recognised(self):
        self.assertTrue(pp._LOCK_RE.search('{"error_description":"Your account has been temporarily locked because bulk downloading activity was detected."}'))

    def test_downloads_are_spaced_and_capped(self):
        self.assertGreaterEqual(pp.PAPER_GAP_S, 20)
        self.assertLessEqual(pp.MAX_PER_RUN, 12)
