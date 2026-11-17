import tempfile
import unittest
from pathlib import Path

from lmcore_sync.courses import (auto_entries, courses_root, current_real_courses, desktop_dir,
                                 resolve_layout, scaffold)


XJTLU_ENROLMENTS = [
    {"shortname": "EEE339-2627-S1", "fullname": "EEE339-2627-S1-Digital System Design with HDL"},
    {"shortname": "ADS305-2627-S1", "fullname": "ADS305-2627-S1-Big Data Analytics"},
    {"shortname": "Academy of AI and Advanced Technology Communication Area", "fullname": "x"},
    {"shortname": "Announcements", "fullname": "Announcements"},
    {"shortname": "Career Centre", "fullname": "Career Centre"},
]


class DesktopFolderTests(unittest.TestCase):
    def test_only_real_modules_are_offered(self):
        codes = [c["shortname"] for c in current_real_courses(XJTLU_ENROLMENTS, {})]
        self.assertEqual(codes, ["EEE339-2627-S1", "ADS305-2627-S1"])

    def test_default_root_is_the_real_desktop(self):
        self.assertEqual(courses_root({}), desktop_dir())
        self.assertEqual(courses_root({"courses_root": "~/Desktop"}), desktop_dir())
        self.assertEqual(courses_root({"courses_root": "desktop"}), desktop_dir())
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(courses_root({"courses_root": tmp}), Path(tmp))

    def test_zero_config_creates_one_folder_per_course(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {"courses_root": tmp, "courses": [], "auto_courses": True}
            entries = auto_entries(cfg, current_real_courses(XJTLU_ENROLMENTS, cfg))
            self.assertEqual(sorted(Path(e["folder"]).name for e in entries), ["ads305", "eee339"])
            for e in entries:
                scaffold(Path(e["folder"]), resolve_layout(e, cfg), {"shortname": e["match"]})
            self.assertTrue((Path(tmp) / "ads305" / "02_课件").is_dir())
            self.assertTrue((Path(tmp) / "eee339" / "02_Lectures").is_dir())   # Quartus-safe ASCII layout
            self.assertTrue((Path(tmp) / "ads305" / "00_从这里开始.md").is_file())

    def test_unpicked_courses_stay_out_in_auto_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {"courses_root": tmp, "courses": [], "exclude": ["ADS305"]}
            entries = auto_entries(cfg, current_real_courses(XJTLU_ENROLMENTS, cfg))
            self.assertEqual([e["match"] for e in entries], ["EEE339-2627-S1"])


if __name__ == "__main__":
    unittest.main()
