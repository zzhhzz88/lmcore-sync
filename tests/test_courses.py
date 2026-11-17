import tempfile
import unittest
from pathlib import Path

from lmcore_sync.courses import auto_entries, resolve_layout, scaffold


class CoursesTests(unittest.TestCase):
    def test_invalid_default_preset_falls_back_without_crash(self):
        self.assertEqual(resolve_layout({"match": "ABC123", "layout": "en"}, {"layout_preset": "unknown"})["lecture"], "02_Lectures")
        self.assertEqual(resolve_layout({"match": "ABC123", "layout": "unknown"}, {"layout_preset": "unknown"})["lecture"], "02_课件")

    def test_eee_uses_ascii_layout(self):
        self.assertEqual(resolve_layout({"match": "EEE339-2627-S1"}, {})["ai"], "06_AI_readable")

    def test_auto_entries_use_full_semester_name_and_existing_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "ABC123"
            folder.mkdir()
            courses = [{"shortname": "ABC123-2627-S1"}, {"shortname": "ABC123-2627-S1"}, {"shortname": "Support"}]
            entries = auto_entries({"courses_root": tmp}, courses)
            self.assertEqual(entries, [{"match": "ABC123-2627-S1", "folder": str(folder), "auto": True}])

    def test_exclusions_and_configured_courses_are_not_added(self):
        entries = auto_entries({"courses": [{"match": "ABC123", "folder": "somewhere"}], "exclude": ["DEF456"]},
                               [{"shortname": "ABC123-2627-S1"}, {"shortname": "DEF456-2627-S1"}])
        self.assertEqual(entries, [])

    def test_scaffold_preserves_existing_guide(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            guide = folder / "README.md"
            guide.write_text("personal guide", encoding="utf-8")
            made = scaffold(folder, resolve_layout({"match": "ABC123"}, {}), {"shortname": "ABC123"})
            self.assertEqual(guide.read_text(encoding="utf-8"), "personal guide")
            self.assertNotIn("00_从这里开始.md", made)
            self.assertTrue((folder / "02_课件").is_dir())
