"""lmcore-sync command line.

  python -m lmcore_sync setup     # first-time wizard: log in, pick courses, map folders
  python -m lmcore_sync login     # (re)log in in a visible browser window
  python -m lmcore_sync courses   # list enrolled courses
  python -m lmcore_sync sync      # download + de-duplicate + sort + convert
  python -m lmcore_sync convert   # only (re)build the AI-readable Markdown copies
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import zipfile
import sys
from datetime import datetime
from pathlib import Path

from . import deadlines
from .convert import Converter, CONVERTIBLE
from .moodle import Moodle
from .organize import CATEGORIES, MANIFEST, STATE_DIR
from .sync import AI_DIRNAME, CourseSync, norm

HOME = Path(__file__).resolve().parent.parent
CONFIG = HOME / "config.json"
USER_DATA = HOME / "user_data"
PROFILE = USER_DATA / "browser_profile"

try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass


def log(msg: str):
    print(msg, flush=True)


def load_config() -> dict:
    if CONFIG.exists():
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    return {"base_url": "https://core.xjtlu.edu.cn", "browser": "msedge", "courses": []}


def save_config(cfg: dict):
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def moodle(cfg, headless=True) -> Moodle:
    return Moodle(cfg["base_url"], PROFILE, cfg.get("browser", "msedge"), headless=headless, log=log)


async def ensure_login(cfg) -> None:
    async with moodle(cfg, headless=True) as m:
        if await m.is_logged_in():
            return
    log(">> 需要登录：已打开浏览器窗口，请用学校账号登录（登录完成后会自动继续，最多等 15 分钟）")
    async with moodle(cfg, headless=False) as m:
        if not await m.wait_for_login():
            raise SystemExit("登录超时。")
    log(">> 登录成功，登录状态已保存在 user_data/（只属于你本人，不要拷给别人）")


def match_course(courses: list[dict], key: str) -> dict | None:
    k = norm(key)
    for c in courses:
        if k == norm(c["shortname"]) or k in norm(c["shortname"]) or k in norm(c["fullname"]):
            return c
    return None


# ---------------------------------------------------------------- commands
async def cmd_login(cfg, args):
    log(">> 打开浏览器，请登录……")
    async with moodle(cfg, headless=False) as m:
        ok = await m.wait_for_login()
    log("登录成功" if ok else "登录超时")


async def cmd_courses(cfg, args):
    await ensure_login(cfg)
    async with moodle(cfg) as m:
        await m.is_logged_in()
        for c in await m.courses():
            log(f"{c['id']:>7} | {c['shortname']} | {c['fullname']}")


async def cmd_setup(cfg, args):
    await ensure_login(cfg)
    async with moodle(cfg) as m:
        await m.is_logged_in()
        courses = await m.courses()
    log("\n你已选的课程：")
    for i, c in enumerate(courses, 1):
        log(f"  {i:>2}. {c['shortname']}  —  {c['fullname']}")
    picked = input("\n输入要同步的课程编号或课程代码，用空格分隔（如 1 3 EEE339）：").split()
    default_root = Path.home() / "Desktop"
    root = input(f"课程文件夹放在哪个目录下？直接回车 = {default_root}：").strip() or str(default_root)
    cfg["courses"] = []
    for p in picked:
        c = courses[int(p) - 1] if p.isdigit() and 0 < int(p) <= len(courses) else match_course(courses, p)
        if not c:
            log(f"  !! 找不到 {p}")
            continue
        code = c["shortname"].split()[0].split("_")[0]
        folder = input(f"  {c['shortname']} 的文件夹（回车 = {Path(root) / code.lower()}）：").strip() or str(Path(root) / code.lower())
        cfg["courses"].append({"match": c["shortname"], "folder": folder})
    save_config(cfg)
    log(f"\n已保存 {CONFIG}。以后直接运行 sync 即可。")


# folders inside the course whose files are build artefacts, not course material
AI_SKIP_DIRS = {"build", "quartus", "rtl", "tb", "db", "incremental_db", "results", "figures", "fig",
                "runnable", "scripts", ".ipynb_checkpoints", "__pycache__", ".venv", "runtime", "tmp",
                "study_support", "9_internal", ".lmcore", ".git", "node_modules", "work", ".runtime"}


def build_ai_copies(cfg, folder: Path, rows: list[dict] | None, conv: Converter, ai_dir: str = AI_DIRNAME) -> dict[str, str]:
    """Mirror every readable document of the numbered course folders into
    06_AI可读版/<same path>.md (zip packs are unpacked first)."""
    ai_root = folder / ai_dir
    readable = CONVERTIBLE | {".ipynb", ".html", ".htm"}
    jobs, used = [], set()

    def add_job(src: Path, ai_dir: Path):
        name = src.stem + ".md"
        if (ai_dir / name) in used:      # e.g. 1.1_RDD.html and 1.1_RDD.ipynb
            name = f"{src.stem}.{src.suffix.lstrip('.').lower()}.md"
        dst = ai_dir / name
        used.add(dst)
        jobs.append((src, dst))

    sources = []
    for top in sorted(folder.iterdir()):
        if not top.is_dir() or not re.match(r"0[0-57-9]_", top.name):
            continue
        for dp, dn, fn in os.walk(top):
            dn[:] = sorted(d for d in dn if d not in AI_SKIP_DIRS and not d.startswith("."))
            sources += [Path(dp) / f for f in sorted(fn)]
    for src in sources:
        ext = src.suffix.lower()
        ai_dir = ai_root / src.parent.relative_to(folder)
        if ext in readable:
            add_job(src, ai_dir)
        elif ext == ".zip" and not src.name.startswith("_") and src.stat().st_size < 300 * 1024 * 1024:
            # ("_xxx.zip" = personal process archives, not course material)
            # unpack lab packs etc. so the manuals inside become readable too
            xdir = ai_dir / f"{src.stem}_解压"
            if not xdir.exists():
                try:
                    with zipfile.ZipFile(src) as z:
                        for info in z.infolist():
                            target = (xdir / info.filename).resolve()
                            if not str(target).startswith(str(xdir.resolve())) or info.is_dir():
                                continue  # zip-slip guard
                            if "__MACOSX" in info.filename:
                                continue
                            target.parent.mkdir(parents=True, exist_ok=True)
                            with z.open(info) as fsrc, open(target, "wb") as fdst:
                                shutil.copyfileobj(fsrc, fdst)
                except Exception as e:
                    log(f"      解压失败 {src.name}: {e}")
                    continue
            for inner in sorted(xdir.rglob("*")):
                if inner.is_file() and inner.suffix.lower() in readable:
                    add_job(inner, inner.parent)
    res = conv.convert_many(jobs)
    done = {}
    for (src, dst) in jobs:
        try:
            rel = src.relative_to(folder).as_posix()
        except ValueError:
            continue
        if res.get(src) == "ok":
            done.setdefault(rel, dst.relative_to(folder).as_posix())
        elif res.get(src) != "unsupported":
            log(f"      转换失败 {src.name}: {res.get(src)}")
    for src in sources:  # zip packs: point the manifest at the unpacked folder
        if src.suffix.lower() == ".zip":
            x = ai_root / src.parent.relative_to(folder) / f"{src.stem}_解压"
            if x.exists():
                done[src.relative_to(folder).as_posix()] = x.relative_to(folder).as_posix() + "/"
    return done


def write_manifest(folder: Path, course: dict, cs: CourseSync | None, rows: list[dict], ai: dict[str, str], engine: str):
    out = folder
    lines = [f"# {course['fullname']}（{course['shortname']}）— Learning Mall Core 资料清单",
             "", f"- 最近同步：{datetime.now():%Y-%m-%d %H:%M}",
             f"- 原课程页：{course['url']}",
             f"- AI 可读版转换引擎：{engine}",
             "- 说明：“已有(未重复下载)”表示你文件夹里早就有内容完全相同的文件，路径指向你原来的那份。",
             "- 给 AI 阅读时优先打开 AI 可读版文件夹（06_）里同路径的 .md；需要看公式/图时再看原 PDF。", ""]
    seen = set()
    titles = {"handbook": "课程手册与信息", "announcement": "公告", "lecture": "课件", "lab": "实验",
              "assessment": "作业与考核要求", "submission": "我的提交与反馈", "exam": "考试复习", "other": "参考资料"}
    for cat, cname in titles.items():
        rs = [r for r in rows if r.get("cat", "other") == cat and (r.get("path") or r.get("status", "").startswith(("失败", "跳过")))]
        uniq = []
        for r in rs:
            k = (r.get("path") or r.get("url"), r["status"])
            if k not in seen:
                seen.add(k)
                uniq.append(r)
        rs = uniq
        if not rs:
            continue
        lines += [f"## {cname}", "", "| 名称 | Moodle 版块 | 状态 | 文件 | AI 可读版 |", "|---|---|---|---|---|"]
        for r in rs:
            p = r.get("path", "")
            a = ai.get(p, "")
            cell = lambda s: (s or "").replace("|", "/").replace("\n", " ")
            lines.append(f"| {cell(r['title'])} | {cell(r['section'])} | {r['status']} | {f'`{p}`' if p else ''} | {f'`{a}`' if a else ''} |")
        lines.append("")
    if cs and cs.links:
        lines += ["## 外部链接（课程里的网址类资源）", ""]
        lines += [f"- [{n}]({u}) — {s}" for s, n, u in cs.links]
        lines.append("")
    if cs and cs.skipped_mods:
        lines += ["## 没有下载的活动（测验、讨论区等需要在网页上操作的内容）", ""]
        lines += [f"- {n}（{m}）— {s}" for s, n, m in cs.skipped_mods]
        lines.append("")
    (out / MANIFEST).write_text("\n".join(lines), encoding="utf-8")
    if cs and cs.links:
        info = cs.layout["handbook"]
        (out / info).mkdir(parents=True, exist_ok=True)
        (out / info / "外部链接.md").write_text(
            "# 外部链接\n\n" + "\n".join(f"- [{n}]({u}) — {s}" for s, n, u in cs.links) + "\n", encoding="utf-8")


async def cmd_sync(cfg, args):
    if not cfg.get("courses"):
        raise SystemExit("还没有配置课程，请先运行 setup。")
    await ensure_login(cfg)
    conv = Converter(cfg.get("filee_path"), log=log)
    log(f">> AI 可读版转换引擎：{conv.engine}")
    only = {norm(x) for x in (args.only or [])}
    async with moodle(cfg) as m:
        if not await m.is_logged_in():
            raise SystemExit("登录状态失效，请运行 login。")
        courses = await m.courses()
        for entry in cfg["courses"]:
            if only and not any(o in norm(entry["match"]) for o in only):
                continue
            c = match_course(courses, entry["match"])
            if not c:
                log(f"!! 没找到课程 {entry['match']}")
                continue
            folder = Path(entry["folder"]).expanduser()
            folder.mkdir(parents=True, exist_ok=True)
            log(f"\n======== {c['shortname']} → {folder}")
            cs = CourseSync(m, c, folder, USER_DATA, {"refresh": args.refresh, "videos": args.videos,
                                                       "all_forums": args.all_forums,
                                                       "layout": entry.get("layout")}, log=log)
            await cs.run()
            ai = {} if args.no_convert else build_ai_copies(cfg, folder, cs.rows, conv, (entry.get("layout") or {}).get("ai", AI_DIRNAME))
            write_manifest(folder, c, cs, cs.rows, ai, conv.engine)
            new = sum(1 for r in cs.rows if r["status"] == "新下载")
            have = sum(1 for r in cs.rows if r["status"].startswith("已有"))
            fail = sum(1 for r in cs.rows if r["status"].startswith("失败"))
            log(f"   完成：新下载 {new}，已有跳过 {have}，失败 {fail}，AI 可读版 {len(ai)}")
            log(f"   清单：{folder / MANIFEST}")
        await write_ddl(cfg, m, courses)


async def write_ddl(cfg, m, courses):
    picked = [c for c in (match_course(courses, e["match"]) for e in cfg["courses"]) if c]
    rows = await deadlines.collect(m, picked)
    path = Path(cfg.get("ddl_file") or deadlines.default_path()).expanduser()
    path.write_text(deadlines.render(rows, cfg.get("extra_deadlines")), encoding="utf-8")
    log(f"\n>> 作业 DDL 已更新：{path}")
    for r in rows:
        if r["due"] and r["due"] >= datetime.now() and not deadlines.is_done(r["status"]):
            log(f"   未交 {r['due']:%m-%d %H:%M}  {r['course'].split('-')[0]}  {r['name']}")


async def cmd_ddl(cfg, args):
    await ensure_login(cfg)
    async with moodle(cfg) as m:
        await m.is_logged_in()
        await write_ddl(cfg, m, await m.courses())


async def cmd_convert(cfg, args):
    conv = Converter(cfg.get("filee_path"), log=log)
    log(f">> 引擎：{conv.engine}")
    for entry in cfg["courses"]:
        folder = Path(entry["folder"]).expanduser()
        if not folder.exists():
            continue
        ai = build_ai_copies(cfg, folder, None, conv, (entry.get("layout") or {}).get("ai", AI_DIRNAME))
        sf = folder / STATE_DIR / "state.json"
        if sf.exists():
            st = json.loads(sf.read_text(encoding="utf-8"))
            rows = list(st["items"].values())
            for r in rows:
                r.setdefault("section", "")
            write_manifest(folder, st["course"], None, rows, ai, conv.engine)
        log(f"{entry['match']}: {len(ai)} 个 AI 可读文件")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="lmcore-sync", description="Learning Mall Core 课件同步工具")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("setup", "login", "courses", "convert", "ddl"):
        sub.add_parser(name)
    s = sub.add_parser("sync")
    s.add_argument("--only", nargs="*", help="只同步这些课程代码，如 --only EEE339 COS301")
    s.add_argument("--refresh", action="store_true", help="忽略同步记录，重新检查所有文件")
    s.add_argument("--videos", action="store_true", help="也下载视频/音频")
    s.add_argument("--all-forums", action="store_true", help="所有讨论区都存档（默认只存公告）")
    s.add_argument("--no-convert", action="store_true", help="不生成 AI 可读版")
    args = ap.parse_args(argv)
    cfg = load_config()
    fn = {"setup": cmd_setup, "login": cmd_login, "courses": cmd_courses, "sync": cmd_sync, "convert": cmd_convert, "ddl": cmd_ddl}[args.cmd]
    asyncio.run(fn(cfg, args))


if __name__ == "__main__":
    main()
