import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from unittest.mock import patch

from lmcore_sync import maintenance as m


class MaintenanceTests(unittest.TestCase):
    def test_time_requires_valid_hhmm(self):
        for value in ("00:00", "09:00", "23:59"):
            self.assertEqual(m.validate_time(value), value)
        for value in ("24:00", "12:60", "9:00", "09:00 & calc", "12:30:00"):
            with self.assertRaises(ValueError):
                m.validate_time(value)

    def test_xml_preserves_chinese_spaces_and_shell_characters(self):
        home = Path(tempfile.gettempdir()) / "课程 同步 & tools"
        exe = home / ".venv/Scripts/pythonw.exe"
        xml = m.build_task_xml(home, "09:23", r"PC\学生", exe, date(2026, 11, 17))
        tree = ET.fromstring(xml)
        ns = {"s": m.NS}
        self.assertEqual(tree.findtext("s:Actions/s:Exec/s:Command", namespaces=ns), str(exe))
        self.assertEqual(tree.findtext("s:Actions/s:Exec/s:WorkingDirectory", namespaces=ns), str(home.resolve()))
        self.assertEqual(tree.findtext("s:Actions/s:Exec/s:Arguments", namespaces=ns), "-m lmcore_sync sync --non-interactive")
        self.assertEqual(tree.findtext("s:Principals/s:Principal/s:LogonType", namespaces=ns), "InteractiveToken")
        self.assertEqual(tree.findtext("s:Triggers/s:CalendarTrigger/s:StartBoundary", namespaces=ns), "2026-11-17T09:23:00")
        self.assertEqual(tree.findtext("s:Settings/s:MultipleInstancesPolicy", namespaces=ns), "IgnoreNew")
        self.assertEqual(tree.findtext("s:Settings/s:WakeToRun", namespaces=ns), "false")

    def test_venv_python_and_windowless_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            scripts = home / ".venv/Scripts"
            scripts.mkdir(parents=True)
            (scripts / "python.exe").touch()
            (scripts / "pythonw.exe").touch()
            self.assertEqual(m.python_executable(home), scripts / "python.exe")
            self.assertEqual(m.python_executable(home, True), scripts / "pythonw.exe")

    def test_refuses_replacing_or_deleting_foreign_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            foreign = m.build_task_xml(home / "other", "09:00", "user", Path("pythonw.exe"))
            for action in ("install", "remove"):
                with patch.object(m.sys, "platform", "win32"), patch.object(m, "_existing_task", return_value=foreign), patch.object(m, "_run") as run:
                    self.assertEqual(m.schedule(action, home, log=lambda _: None), 1)
                    run.assert_not_called()

    def test_install_creates_owned_xml_without_force_for_new_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            calls = []
            def fake_run(*args):
                calls.append(args)
                if args[0] == "whoami.exe":
                    return "pc\\user\n"
                xml = Path(args[args.index("/XML") + 1]).read_text(encoding="utf-16")
                m._check_owner(xml, home)
                self.assertNotIn("/F", args)
                return "SUCCESS"
            with patch.object(m.sys, "platform", "win32"), patch.object(m, "_existing_task", return_value=None), patch.object(m, "python_executable", return_value=home / "pythonw.exe"), patch.object(m, "_run", side_effect=fake_run):
                self.assertEqual(m.schedule("install", home, "13:45", log=lambda _: None), 0)
            self.assertEqual(len(calls), 2)

    def test_query_failure_does_not_attempt_create(self):
        with patch.object(m.sys, "platform", "win32"), patch.object(m, "_run", side_effect=RuntimeError("access denied")) as run:
            self.assertEqual(m.schedule("install", Path.cwd(), log=lambda _: None), 1)
            self.assertEqual(run.call_count, 1)

    def test_query_matches_exact_task_name(self):
        name = "LMCoreSync-abc"
        with patch.object(m, "_run", return_value='"\\LMCoreSync-abcdef","N/A","Ready"\n') as run:
            self.assertIsNone(m._existing_task(name))
            self.assertEqual(run.call_count, 1)

    def test_process_failure_is_reported_as_failure(self):
        proc = subprocess.CompletedProcess([], 5, "", "access denied")
        with patch.object(m.subprocess, "run", return_value=proc):
            with self.assertRaisesRegex(RuntimeError, "access denied"):
                m._run("schtasks.exe", "/Query")

    def test_doctor_is_read_only_and_does_not_claim_login_validity(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            profile = home / "user_data/browser_profile"
            profile.parent.mkdir()
            (profile.parent / "cookies.json").write_text("[]")
            (home / "config.json").write_text("{}")
            folder = home / "new course"
            messages = []
            with patch.object(m, "_browser_path", return_value=Path("browser.exe")), patch.object(m.importlib.util, "find_spec", return_value=object()):
                code = m.doctor({"courses": [{"folder": str(folder)}]}, home, profile, messages.append)
            self.assertEqual(code, 0)
            self.assertFalse(folder.exists())
            self.assertIn("是否过期未验证", "\n".join(messages))


if __name__ == "__main__":
    unittest.main()
