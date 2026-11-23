"""Local diagnostics and an opt-in Windows daily sync task.

The scheduler uses the current user's interactive token: no password is stored,
and the task runs only while that Windows user is logged in. XML follows:
https://learn.microsoft.com/en-us/windows/win32/taskschd/daily-trigger-example--xml-
"""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"
ET.register_namespace("", NS)


def validate_time(at: str) -> str:
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", at):
        raise ValueError("时间必须为 24 小时制 HH:MM，例如 09:00 或 18:30。")
    return at


def task_name(home: Path) -> str:
    identity = os.path.normcase(str(Path(home).resolve()))
    return "LMCoreSync-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]


def _owner(home: Path) -> str:
    return "lmcore-sync managed task; project=" + str(Path(home).resolve())


def python_executable(home: Path, windowless: bool = False) -> Path:
    venv = Path(home) / ".venv" / "Scripts" / "python.exe"
    exe = venv if venv.is_file() else Path(sys.executable)
    if windowless:
        exe = exe.with_name("pythonw.exe")
        if not exe.is_file():
            raise RuntimeError("未找到 pythonw.exe，无法创建无窗口任务；请先运行 install.cmd 安装完整的 Python 环境。")
    return exe


def build_task_xml(home: Path, at: str, user: str, executable: Path | None = None,
                   start_date: date | None = None) -> str:
    """Build XML without invoking Task Scheduler; paths are XML-escaped, not shell code."""
    home = Path(home).resolve()
    at = validate_time(at)
    exe = executable or python_executable(home, windowless=True)
    task = ET.Element(f"{{{NS}}}Task", version="1.2")

    def child(parent, name, value=None, **attrs):
        node = ET.SubElement(parent, f"{{{NS}}}{name}", attrs)
        if value is not None:
            node.text = str(value)
        return node

    reg = child(task, "RegistrationInfo")
    child(reg, "Description", _owner(home))
    triggers = child(task, "Triggers")
    daily = child(triggers, "CalendarTrigger")
    child(daily, "StartBoundary", f"{start_date or date.today():%Y-%m-%d}T{at}:00")
    child(daily, "Enabled", "true")
    child(child(daily, "ScheduleByDay"), "DaysInterval", "1")
    principal = child(child(task, "Principals"), "Principal", id="CurrentUser")
    child(principal, "UserId", user)
    child(principal, "LogonType", "InteractiveToken")
    child(principal, "RunLevel", "LeastPrivilege")
    settings = child(task, "Settings")
    for key, value in {
        "MultipleInstancesPolicy": "IgnoreNew",
        "DisallowStartIfOnBatteries": "false",
        "StopIfGoingOnBatteries": "false",
        "AllowHardTerminate": "false",
        "StartWhenAvailable": "true",
        "RunOnlyIfNetworkAvailable": "true",
        "Enabled": "true",
        "Hidden": "false",
        "WakeToRun": "false",
        "ExecutionTimeLimit": "PT0S",
    }.items():
        child(settings, key, value)
    action = child(child(task, "Actions", Context="CurrentUser"), "Exec")
    child(action, "Command", str(exe))
    child(action, "Arguments", "-m lmcore_sync sync --non-interactive --login-popup")
    child(action, "WorkingDirectory", str(home))
    return '<?xml version="1.0" encoding="UTF-16"?>\n' + ET.tostring(task, encoding="unicode")


def _run(*args: str) -> str:
    proc = subprocess.run(args, capture_output=True, text=True, errors="replace",
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=45)
    if proc.returncode:
        raise RuntimeError((proc.stderr or proc.stdout or f"命令失败 ({proc.returncode})").strip())
    return proc.stdout


def _existing_task(name: str) -> str | None:
    # An exact-name /Query failure is ambiguous (absent vs access denied).
    # Enumerate first, so a failing lookup is never mistaken for permission to replace.
    listing = _run("schtasks.exe", "/Query", "/FO", "CSV", "/NH")
    exists = any(row and row[0].lstrip("\\").casefold() == name.casefold()
                 for row in csv.reader(io.StringIO(listing)))
    if not exists:
        return None
    return _run("schtasks.exe", "/Query", "/TN", name, "/XML")


def _check_owner(xml: str, home: Path) -> ET.Element:
    root = ET.fromstring(xml.lstrip("\ufeff"))
    if root.findtext(f"{{{NS}}}RegistrationInfo/{{{NS}}}Description") != _owner(home):
        raise RuntimeError("存在同名但不属于此项目的任务，已停止操作。请在任务计划程序中检查。")
    return root


def schedule(action: str, home: Path, at: str = "09:00", log=print) -> int:
    """Install/update, remove or inspect this checkout's task; failures return 1."""
    try:
        if sys.platform != "win32":
            raise RuntimeError("定时同步命令目前仅支持 Windows 任务计划程序。")
        if action not in {"install", "remove", "status"}:
            raise ValueError("schedule 操作必须是 install、remove 或 status。")
        if action == "install":
            validate_time(at)
        home = Path(home).resolve()
        name = task_name(home)
        existing = _existing_task(name)
        tree = _check_owner(existing, home) if existing else None
        if action == "status":
            if tree is None:
                log(f"尚未启用定时同步（{name}）。")
            else:
                start = tree.findtext(f"{{{NS}}}Triggers/{{{NS}}}CalendarTrigger/{{{NS}}}StartBoundary", "未知")
                enabled = tree.findtext(f"{{{NS}}}Settings/{{{NS}}}Enabled", "true")
                log(f"任务：{name}\n每天：{start[11:16] if 'T' in start else start}（系统本地时间）\n启用：{enabled}\n项目：{home}")
                log("仅在当前 Windows 用户已登录时运行；学校登录过期时会弹出登录窗口。")
            return 0
        if action == "remove":
            if existing:
                _run("schtasks.exe", "/Delete", "/TN", name, "/F")
                log(f"已移除定时同步：{name}")
            else:
                log("未发现此项目的定时任务，无需移除。")
            return 0

        user = _run("whoami.exe").strip()
        if not user:
            raise RuntimeError("无法确定当前 Windows 用户，未注册任务。")
        xml = build_task_xml(home, at, user)
        # A temporary file is needed by schtasks; it contains no login credentials.
        with tempfile.TemporaryDirectory(prefix="lmcore-schedule-") as tmp:
            path = Path(tmp) / "task.xml"
            path.write_text(xml, encoding="utf-16")
            args = ["schtasks.exe", "/Create", "/TN", name, "/XML", str(path)]
            if existing:  # Only replace a task whose ownership was verified above.
                args.append("/F")
            _run(*args)
        log(f"已{'更新' if existing else '启用'}定时同步：每天 {at}（系统本地时间），任务 {name}")
        log("仅在当前 Windows 用户已登录时运行，错过时间后可补跑；不会唤醒电脑。")
        log("学校登录过期时会弹出登录窗口，等你 15 分钟；没登录就在作业DDL.md 顶部提示。运行记录见 user_data/last_sync.log。")
        return 0
    except (OSError, ValueError, RuntimeError, ET.ParseError, subprocess.SubprocessError) as exc:
        log(f"定时同步失败：{exc}")
        return 1


def _browser_path(kind: str) -> Path | None:
    choices = {"msedge": Path("Microsoft/Edge/Application/msedge.exe"),
               "chrome": Path("Google/Chrome/Application/chrome.exe")}
    order = [kind] + [k for k in choices if k != kind] if kind in choices else []
    for key in order:
        for env in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base and (Path(base) / choices[key]).is_file():
                return Path(base) / choices[key]
    for name in ("msedge", "google-chrome", "chromium", "chrome"):
        found = shutil.which(name)
        if found:
            return Path(found)
    # Ask the installed Playwright version for its exact bundled browser path;
    # finding an arbitrary stale chromium cache would give a false positive.
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            bundled = Path(pw.chromium.executable_path)
            return bundled if bundled.is_file() else None
    except Exception:
        return None


def doctor(cfg: dict, home: Path, profile: Path, log=print) -> int:
    """Read-only local checks. Cookie presence is not proof of valid authentication."""
    failures = 0

    def report(ok, detail):
        nonlocal failures
        log(f"{'[OK]' if ok else '[需处理]'} {detail}")
        if not ok:
            failures += 1

    home, profile = Path(home), Path(profile)
    report(sys.version_info >= (3, 10), f"Python {sys.version.split()[0]}：{sys.executable}（需要 3.10+）")
    for module, package in (("playwright", "playwright"), ("bs4", "beautifulsoup4"), ("markdownify", "markdownify")):
        try:
            found = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            found = False
        report(found, f"依赖 {package}" + (" 已安装" if found else " 缺失，请运行 install.cmd"))
    browser = _browser_path(cfg.get("browser", "msedge"))
    report(browser is not None, f"浏览器：{browser}" if browser else "未找到可用浏览器，请安装 Edge 或运行 python -m playwright install chromium")
    report((home / "config.json").is_file(), "配置 config.json" + (" 已存在" if (home / "config.json").is_file() else " 不存在，请运行 setup"))
    entries = cfg.get("courses") or []
    report(bool(entries) or cfg.get("auto_courses", False), f"已配置课程 {len(entries)} 门" + ("；自动发现已启用" if cfg.get("auto_courses") else ""))
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("folder"), str) or not entry["folder"].strip():
            report(False, "课程条目缺少有效的 folder，请运行 setup 重新配置")
            continue
        folder = Path(entry["folder"]).expanduser()
        if folder.exists():
            report(folder.is_dir() and os.access(folder, os.W_OK), f"课程目录：{folder}")
        else:
            parent = folder.parent
            while not parent.exists() and parent != parent.parent:
                parent = parent.parent
            report(parent.is_dir() and os.access(parent, os.W_OK), f"课程目录尚未创建，同步时自动建立：{folder}")
    cookies = profile.parent / "cookies.json"
    report(cookies.is_file(), "登录状态文件已存在（是否过期未验证）" if cookies.is_file() else "没有登录状态文件，请运行 login")
    log("自检没有访问学校网站，也没有修改课程或登录文件；登录有效性须通过实际同步验证。")
    return 1 if failures else 0
