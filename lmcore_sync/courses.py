"""Which courses to sync, where their folders live, and what the folders look like.

* Layout presets: "zh" (Chinese folder names, default) and "en" (ASCII-only
  names – needed for tools such as Quartus 13 that break on non-ASCII / space
  paths). A course entry may use a preset name or a dict that overrides keys.
* Auto mode: every course of the current semester whose short name looks like
  a real module code (e.g. ``EEE339-2627-S1``) gets a folder ``<root>/<code>``
  automatically – reusing an existing folder with the same name in any case.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

LAYOUTS: dict[str, dict] = {
    "zh": {
        "handbook": "01_课程信息", "announcement": "01_课程信息", "lecture": "02_课件", "lab": "03_实验",
        "assessment": "04_作业", "submission": "04_作业", "exam": "05_考试复习", "other": "07_参考资料",
        "ai": "06_AI可读版", "assignment_nospace": False,
    },
    "en": {
        "handbook": "01_CourseInfo", "announcement": "01_CourseInfo", "lecture": "02_Lectures", "lab": "03_Labs",
        "assessment": "04_Assignments", "submission": "04_Assignments", "exam": "05_ExamPrep",
        "other": "07_References", "ai": "06_AI_readable", "assignment_nospace": True,
    },
}

FOLDER_HELP = {
    "handbook": ("课程信息", "课程手册（Module Handbook）、公告、外部链接"),
    "lecture": ("课件", "每周的 Lecture 课件"),
    "lab": ("实验", "Lab / Tutorial / Workshop 材料"),
    "assessment": ("作业", "每个作业一个子文件夹：题目、Moodle 作业页说明、你的提交与反馈"),
    "exam": ("考试复习", "往年试题、模拟题、复习资料"),
    "ai": ("AI 可读版", "上面所有文档的 Markdown 版，目录结构一一对应，直接丢给 AI 读"),
    "other": ("参考资料", "其他阅读材料"),
}

DEFAULT_COURSE_PATTERN = r"^[A-Z]{2,4}\d{3}"     # XJTLU module codes: EEE339, ADS305, ...
INDEX_NAMES = ("00_从这里开始.md", "README.md", "先看这里.md", "00_START_HERE.md")


def desktop_dir() -> Path:
    """The user's real desktop. On Windows ask the shell (handles OneDrive
    desktop redirection and relocated desktops); elsewhere ~/Desktop or ~/桌面."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            from uuid import UUID

            class GUID(ctypes.Structure):
                _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD), ("d3", wintypes.WORD), ("d4", ctypes.c_ubyte * 8)]

            u = UUID("B4BFCC3A-DB2C-424C-B029-7FE99A87C641")  # FOLDERID_Desktop
            g = GUID(u.fields[0], u.fields[1], u.fields[2], (ctypes.c_ubyte * 8)(*u.bytes[8:]))
            out = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(out)) == 0:
                path = Path(out.value)
                ctypes.windll.ole32.CoTaskMemFree(out)
                if path.exists():
                    return path
        except Exception:
            pass
    for cand in (Path.home() / "Desktop", Path.home() / "桌面"):
        if cand.exists():
            return cand
    return Path.home() / "Desktop"


def courses_root(cfg: dict) -> Path:
    """Where course folders go. Unset / "desktop" / "~/Desktop" = the real desktop."""
    raw = (cfg.get("courses_root") or "").strip()
    if not raw or raw.lower() in ("desktop", "~/desktop", "~\desktop", "桌面"):
        return desktop_dir()
    return Path(os.path.expandvars(raw)).expanduser()


def current_real_courses(current: list[dict], cfg: dict) -> list[dict]:
    """This semester's real modules (drops 'Communication Area', 'Career Centre' ...)."""
    out, seen = [], set()
    for c in current:
        code = course_code(c["shortname"])
        if code in seen or not re.match(cfg.get("course_pattern", DEFAULT_COURSE_PATTERN), c["shortname"] or "", re.I):
            continue
        seen.add(code)
        out.append(c)
    return out


def course_code(shortname: str) -> str:
    m = re.match(r"[A-Za-z]{2,4}\s?\d{3}", shortname or "")
    return (m.group(0).replace(" ", "") if m else re.split(r"[\s_\-]", shortname or "course")[0]).upper()


def resolve_layout(entry: dict, cfg: dict) -> dict:
    """entry['layout'] may be a preset name, a dict of overrides, or absent."""
    code = course_code(entry.get("match", ""))
    default = cfg.get("layout_preset", "zh")
    if any(code.startswith(p.upper()) for p in cfg.get("ascii_prefixes", ["EEE"])):
        default = "en"
    lay = entry.get("layout")
    if isinstance(lay, str):
        return dict(LAYOUTS.get(lay, LAYOUTS.get(default, LAYOUTS["zh"])))
    base = dict(LAYOUTS.get(default, LAYOUTS["zh"]))
    if isinstance(lay, dict):
        base.update({k: v for k, v in lay.items() if not k.startswith("_")})
    return base


def is_real_course(c: dict, cfg: dict) -> bool:
    pat = cfg.get("course_pattern", DEFAULT_COURSE_PATTERN)
    code = course_code(c["shortname"])
    if code in {x.upper() for x in cfg.get("exclude", [])}:
        return False
    return bool(re.match(pat, c["shortname"] or "", re.I))


def find_existing_folder(root: Path, code: str) -> Path | None:
    if not root.exists():
        return None
    for d in root.iterdir():
        if d.is_dir() and d.name.lower() == code.lower():
            return d
    return None


def auto_entries(cfg: dict, current: list[dict]) -> list[dict]:
    """New config entries for current-semester courses that are not configured yet."""
    root = courses_root(cfg)
    have = {course_code(e["match"]) for e in cfg.get("courses", [])}
    new = []
    for c in current:
        code = course_code(c["shortname"])
        if code in have or not is_real_course(c, cfg):
            continue
        folder = find_existing_folder(root, code) or (root / code.lower())
        new.append({"match": c["shortname"], "folder": str(folder), "auto": True})
        have.add(code)
    return new


def scaffold(folder: Path, layout: dict, course: dict, ddl_file: Path | None = None) -> list[str]:
    """Create the numbered sub-folders and, if the course has no guide yet, a
    short '00_从这里开始.md'. Never touches existing files. Returns created paths."""
    made = []
    folder.mkdir(parents=True, exist_ok=True)
    for key in ("handbook", "lecture", "lab", "assessment", "exam", "ai", "other"):
        d = folder / layout[key]
        if not d.exists():
            d.mkdir(parents=True)
            made.append(layout[key])
    if not any((folder / n).exists() for n in INDEX_NAMES):
        rows = "\n".join(f"| `{layout[k]}/` | {FOLDER_HELP[k][1]} |" for k in FOLDER_HELP)
        ddl = f"\n全部课程的作业截止时间和考试安排汇总在 `{ddl_file}`。\n" if ddl_file else ""
        text = f"""# {course.get('fullname', course.get('shortname', ''))}

- Learning Mall 课程页：{course.get('url', '')}
- 这个文件夹由 lmcore-sync 自动建立；以后运行同步，新资料会自动放进对应的文件夹，你自己放进来的文件不会被覆盖。
- `00_资料清单.md` 列出 Learning Mall 上每份资料在本地的位置。
{ddl}
| 文件夹 | 放什么 |
|---|---|
{rows}
"""
        (folder / "00_从这里开始.md").write_text(text, encoding="utf-8")
        made.append("00_从这里开始.md")
    return made
