"""Classification of downloaded items into clearly named sub-folders, and
de-duplication against files the user already has in the course folder."""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

# Top-level folders inside each course folder (can be overridden per course
# with "layout" in config.json). Manual files and Moodle downloads share them.
CATEGORIES = {
    "handbook": "01_课程信息",
    "announcement": "01_课程信息",
    "lecture": "02_课件",
    "lab": "03_实验",
    "assessment": "04_作业",
    "submission": "04_作业",      # goes to 04_作业/<作业名>/我的提交与反馈/
    "exam": "05_考试复习",
    "other": "07_参考资料",
}
AI_DIR = "06_AI可读版"
STATE_DIR = ".lmcore"
MANIFEST = "00_资料清单.md"


def assignment_folder(name: str) -> str:
    """'Assignment 1 Submission' -> 'Assignment 1'; 'Coursework 1: A ...' -> 'Coursework 1'."""
    n = re.split(r"[:：]", name or "", maxsplit=1)[0]
    n = re.sub(r"\s*(submission|提交)\s*$", "", n, flags=re.I).strip()
    return safe_name(n, 60) if n else "作业"


# Order matters: first match wins.
_RULES = [
    ("submission", r"assignsubmission|assignfeedback|my submission|feedback file"),
    ("exam", r"past ?paper|final exam|resit|mock|sample (paper|test|exam)|revision|exam paper|in-?class test|往年|复习"),
    ("handbook", r"handbook|module (guide|outline|specification|information)|syllabus|course (outline|information)|teaching plan|\bmhb\b|课程手册"),
    ("assessment", r"assign|coursework|\bcw\d?\b|assessment|project brief|report|rubric|marking|submission|homework|deadline|作业|报告"),
    ("lab", r"\blab\b|\blabs\b|lab\d|tutorial|practical|workshop|seminar|exercise|worksheet|notebook|\.ipynb$|实验"),
    ("lecture", r"lecture|slides?|\blec\b|lec\d|chapter|\bch\d|week ?\d|topic ?\d|课件|讲义"),
]


def classify(*texts: str, modname: str = "") -> str:
    blob = " ".join(t for t in texts if t).lower()
    if modname == "forum":
        return "announcement"
    for cat, pattern in _RULES:
        if re.search(pattern, blob):
            return cat
    if modname == "assign":
        return "assessment"
    return "other"


INVALID = re.compile(r'[\\/:*?"<>|\r\n\t]')


def safe_name(name: str, maxlen: int = 120) -> str:
    name = INVALID.sub("_", name or "").strip().strip(".")
    name = re.sub(r"\s+", " ", name)
    if len(name) > maxlen:
        stem, dot, ext = name.rpartition(".")
        if dot and len(ext) <= 6:
            name = stem[: maxlen - len(ext) - 1] + "." + ext
        else:
            name = name[:maxlen]
    return name or "untitled"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# Directories that never contain the user's course material.
SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "site-packages", "__pycache__",
    "python_packages", "runtime", ".ipynb_checkpoints", "db", "incremental_db",
    "06_AI可读版", "06_AI_readable", ".lmcore", "third_party", "tmp",
}
DOC_EXT = {
    ".pdf", ".ppt", ".pptx", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".zip",
    ".ipynb", ".m", ".txt", ".md", ".py", ".mat", ".rar", ".7z", ".png", ".jpg",
    ".jpeg", ".mp4", ".html", ".tex", ".json", ".v", ".vhd", ".sv",
}


# every file type is indexed (data files like .bz2/.pickle are course material too),
# except obvious build/cache artefacts
SKIP_EXT = {".pyc", ".tmp", ".lock", ".log", ".ddb", ".rdb", ".cdb", ".hdb", ".qmsg", ".dpi"}


class ExistingIndex:
    """Hashes every document-like file already present in a course folder so
    that a freshly downloaded file can be recognised as 'already imported'
    even if the user renamed or moved it."""

    def __init__(self, root: Path, cache: dict | None = None):
        self.root = root
        self.by_hash: dict[str, Path] = {}
        self.by_name_size: dict[tuple[str, int], Path] = {}
        self._cache = cache if cache is not None else {}
        self._scan()

    def _scan(self):
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            for fn in filenames:
                p = Path(dirpath) / fn
                if p.suffix.lower() in SKIP_EXT:
                    continue
                try:
                    st = p.stat()
                except OSError:
                    continue
                if st.st_size == 0 or st.st_size > 600 * 1024 * 1024:
                    continue
                key = f"{p}|{st.st_size}|{int(st.st_mtime)}"
                h = self._cache.get(key)
                if h is None:
                    try:
                        h = sha256_file(p)
                    except OSError:
                        continue
                    self._cache[key] = h
                self.add(p, h, st.st_size)

    def add(self, path: Path, digest: str, size: int):
        self.by_hash.setdefault(digest, path)
        self.by_name_size.setdefault((path.name.lower(), size), path)

    def find(self, digest: str, name: str, size: int) -> Path | None:
        return self.by_hash.get(digest) or self.by_name_size.get((name.lower(), size))

    @property
    def cache(self) -> dict:
        return self._cache
