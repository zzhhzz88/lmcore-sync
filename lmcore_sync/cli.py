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
from datetime import datetime, timedelta
from pathlib import Path

from . import deadlines
from .calendar_ics import build_ics
from .courses import auto_entries, course_code, courses_root, current_real_courses, resolve_layout, scaffold
from .convert import Converter, CONVERTIBLE
from .moodle import Moodle, LoginExpired
from .organize import CATEGORIES, MANIFEST, STATE_DIR
from .organize import sha256_bytes
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
        return json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    # zero-config default: discover this semester's XJTLU modules and create
    # one folder per course on the desktop
    return {"base_url": "https://core.xjtlu.edu.cn", "browser": "msedge", "courses": [], "auto_courses": True}


def save_config(cfg: dict):
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def moodle(cfg, headless=True) -> Moodle:
    return Moodle(cfg["base_url"], PROFILE, cfg.get("browser", "msedge"), headless=headless, log=log,
                  delay=float(cfg.get("request_delay", 0.3)), retries=int(cfg.get("retries", 3)))


async def ensure_login(cfg, non_interactive=False) -> None:
    async with moodle(cfg, headless=True) as m:
        if await m.is_logged_in():
            return
    if non_interactive:
        raise LoginExpired("登录已过期，请运行 python -m lmcore_sync login 后重新同步。")
    log(">> 需要登录：已打开浏览器窗口，请用学校账号登录（登录完成后会自动继续，最多等 15 分钟）")
    async with moodle(cfg, headless=False) as m:
        if not await m.wait_for_login():
            raise SystemExit("登录超时。")
    log(">> 登录成功，登录状态已保存在 user_data/（只属于你本人，不要拷给别人）")


def match_course(courses: list[dict], key: str) -> dict | None:
    k = norm(key)
    for c in courses:
        if k == norm(c["shortname"]):
            return c
    matches = [c for c in courses if k == norm(course_code(c["shortname"]))]
    if matches:
        now = datetime.now().timestamp()
        return max(matches, key=lambda c: (c.get("startdate", 0) <= now and
                   (not c.get("enddate") or c["enddate"] >= now), c.get("startdate", 0)))
    for c in courses:
        if k == norm(c["shortname"]) or k in norm(c["shortname"]) or k in norm(c["fullname"]):
            return c
    return None


def ddl_path(cfg):
    return Path(cfg.get("ddl_file") or deadlines.default_path()).expanduser()


def login_notice(cfg, message):
    """Keep the last successful report and mark it stale instead of erasing it."""
    path = ddl_path(cfg)
    old = path.read_text(encoding="utf-8") if path.exists() else "# 作业 DDL 汇总\n"
    old = re.sub(r"<!-- lmcore-notice -->.*?<!-- /lmcore-notice -->\n*", "", old, flags=re.S)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"<!-- lmcore-notice -->\n> ⚠ {message} 以下保留上次结果，尚未刷新。\n"
                    f"<!-- /lmcore-notice -->\n\n{old}", encoding="utf-8")


def recent_materials(cfg):
    cutoff = (datetime.now() - timedelta(days=7)).date().isoformat()
    recent, seen = [], set()
    for entry in cfg.get("courses", []):
        folder = Path(entry["folder"]).expanduser()
        sf = folder / STATE_DIR / "state.json"
        if not sf.exists():
            continue
        state = json.loads(sf.read_text(encoding="utf-8"))
        for item in state.get("items", {}).values():
            path = folder / item.get("path", "")
            key = str(path.resolve())
            if (item.get("date", "") >= cutoff and item.get("status") in ("新下载", "已更新")
                    and path.is_file() and key not in seen):
                recent.append({**item, "course": entry["match"], "path": path})
                seen.add(key)
    return recent


def write_ai_index(folder, ai_dir):
    root = folder / ai_dir
    root.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in root.rglob("*.md") if p.name != "00_AI目录.md")
    lines = ["# AI 阅读目录", "", "先按主题选择文件；公式、图表和排版以原始文档为准。", ""]
    lines += [f"- [{p.relative_to(root).as_posix()}](<{p.relative_to(root).as_posix()}>)" for p in files]
    (root / "00_AI目录.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- commands
async def cmd_login(cfg, args):
    log(">> 打开浏览器，请登录……")
    async with moodle(cfg, headless=False) as m:
        ok = await m.wait_for_login()
    log("登录成功" if ok else "登录超时")
    if not ok:
        raise SystemExit(2)


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
        current = current_real_courses(await m.courses("inprogress"), cfg)
        everything = await m.courses()
    if not current:  # e.g. between semesters
        current = current_real_courses(everything, cfg)
    log("\n本学期的课程：")
    for i, c in enumerate(current, 1):
        log(f"  {i:>2}. {course_code(c['shortname']):<8} {c['fullname']}")
    raw = input("\n要同步哪些？输入编号或课程代码（空格分隔），直接回车 = 全部：").split()
    if raw:
        picked = []
        for p in raw:
            c = current[int(p) - 1] if p.isdigit() and 0 < int(p) <= len(current) else match_course(everything, p)
            if c:
                picked.append(c)
            else:
                log(f"  !! 找不到 {p}")
    else:
        picked = list(current)
    root_default = courses_root(cfg)
    root = input(f"课程文件夹建在哪里？直接回车 = 桌面（{root_default}）：").strip()
    cfg["courses_root"] = root or "desktop"
    cfg["auto_courses"] = input("以后新学期的新课程也自动建文件夹？[Y/n]：").strip().lower() != "n"
    picked_codes = {course_code(c["shortname"]) for c in picked}
    # courses you did not pick stay out, also in auto mode
    cfg["exclude"] = sorted((set(cfg.get("exclude", [])) | {course_code(c["shortname"]) for c in current}) - picked_codes)
    cfg["courses"] = [e for e in cfg.get("courses", []) if course_code(e["match"]) in picked_codes]
    cfg["courses"] += auto_entries(cfg, picked)
    log("")
    for e in cfg["courses"]:
        c = match_course(everything, e["match"]) or {"shortname": e["match"]}
        made = scaffold(Path(e["folder"]).expanduser(), resolve_layout(e, cfg), c, ddl_path(cfg))
        log(f"  📁 {course_code(e['match']):<8} → {e['folder']}" + ("（新建）" if made else "（已存在，沿用）"))
    save_config(cfg)
    log(f"\n已保存设置：{CONFIG}")
    if getattr(args, "no_first_sync", False):
        return
    if input("现在就开始第一次同步，把课件下载进这些文件夹吗？[Y/n]：").strip().lower() != "n":
        await cmd_sync(cfg, argparse.Namespace(only=None, refresh=False, videos=False, all_forums=False,
                                               no_convert=False, non_interactive=False))


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
            xdir = ai_dir / f"{src.stem}_{sha256_bytes(src.read_bytes())[:12]}_解压"
            if not xdir.exists():
                try:
                    with zipfile.ZipFile(src) as z:
                        for info in z.infolist():
                            target = (xdir / info.filename).resolve()
                            if not target.is_relative_to(xdir.resolve()) or info.is_dir():
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
            x = ai_root / src.parent.relative_to(folder) / f"{src.stem}_{sha256_bytes(src.read_bytes())[:12]}_解压"
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
    if not cfg.get("courses") and not cfg.get("auto_courses"):
        raise SystemExit("还没有配置课程，请先运行 setup。")
    await ensure_login(cfg, args.non_interactive)
    conv = Converter(cfg.get("filee_path"), log=log)
    log(f">> AI 可读版转换引擎：{conv.engine}")
    only = {norm(x) for x in (args.only or [])}
    async with moodle(cfg) as m:
        if not await m.is_logged_in():
            raise LoginExpired("登录状态失效，请运行 login。")
        courses = await m.courses()
        if cfg.get("auto_courses"):
            new_entries = auto_entries(cfg, await m.courses("inprogress"))
            if new_entries:
                cfg.setdefault("courses", []).extend(new_entries)
                save_config(cfg)
                log(f">> 发现本学期 {len(new_entries)} 门新课程，自动建文件夹：")
                for e in new_entries:
                    log(f"   📁 {course_code(e['match'])} → {e['folder']}")
        failures = 0
        for entry in cfg["courses"]:
            if only and not any(o in norm(entry["match"]) for o in only):
                continue
            c = match_course(courses, entry["match"])
            if not c:
                log(f"!! 没找到课程 {entry['match']}")
                failures += 1
                continue
            folder = Path(entry["folder"]).expanduser()
            folder.mkdir(parents=True, exist_ok=True)
            layout = resolve_layout(entry, cfg)
            made = scaffold(folder, layout, c, ddl_path(cfg))
            if made:
                log(f"   📁 新建：{', '.join(made)}")
            log(f"\n======== {c['shortname']} → {folder}")
            cs = CourseSync(m, c, folder, USER_DATA, {"refresh": args.refresh, "videos": args.videos,
                                                       "all_forums": args.all_forums,
                                                       "layout": layout}, log=log)
            await cs.run()
            ai = {} if args.no_convert else build_ai_copies(cfg, folder, cs.rows, conv, layout["ai"])
            if not args.no_convert:
                write_ai_index(folder, layout["ai"])
            write_manifest(folder, c, cs, cs.rows, ai, conv.engine)
            new = sum(1 for r in cs.rows if r["status"] == "新下载")
            have = sum(1 for r in cs.rows if r["status"].startswith("已有"))
            fail = sum(1 for r in cs.rows if r["status"].startswith("失败"))
            failures += fail
            log(f"   完成：新下载 {new}，已有跳过 {have}，失败 {fail}，AI 可读版 {len(ai)}")
            log(f"   清单：{folder / MANIFEST}")
        await write_ddl(cfg, m, courses)
        if failures:
            raise SystemExit(f"同步结束，但有 {failures} 项失败，请查看资料清单后重试。")


async def write_ddl(cfg, m, courses):
    picked = [c for c in (match_course(courses, e["match"]) for e in cfg["courses"]) if c]
    rows = await deadlines.collect(m, picked)
    if getattr(m, "deadline_warnings", []):
        message = "部分 DDL 来源读取失败，请重试；未覆盖上次完整结果。"
        login_notice(cfg, message)
        raise RuntimeError(message)
    rows = deadlines.merge_extra(rows, cfg.get("extra_deadlines"))
    path = ddl_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(deadlines.render(rows, recent=recent_materials(cfg), base_dir=path.parent), encoding="utf-8")
    ics = Path(cfg.get("ics_file") or path.with_suffix(".ics")).expanduser()
    ics.parent.mkdir(parents=True, exist_ok=True)
    ics.write_bytes(build_ics(rows, deadlines.is_done).encode("utf-8"))
    log(f"\n>> 作业 DDL 已更新：{path}")
    log(f">> 日历文件已更新：{ics}（可导入日历；重新导入是否更新取决于日历软件）")
    for r in rows:
        if r["due"] and r["due"] >= datetime.now() and not deadlines.is_done(r["status"]):
            log(f"   未交 {r['due']:%m-%d %H:%M}  {r['course'].split('-')[0]}  {r['name']}")


async def cmd_ddl(cfg, args):
    await ensure_login(cfg, args.non_interactive)
    async with moodle(cfg) as m:
        if not await m.is_logged_in():
            raise LoginExpired("登录状态失效，请运行 login。")
        await write_ddl(cfg, m, await m.courses())


async def cmd_convert(cfg, args):
    conv = Converter(cfg.get("filee_path"), log=log)
    log(f">> 引擎：{conv.engine}")
    for entry in cfg["courses"]:
        folder = Path(entry["folder"]).expanduser()
        if not folder.exists():
            continue
        layout = resolve_layout(entry, cfg)
        ai = build_ai_copies(cfg, folder, None, conv, layout["ai"])
        write_ai_index(folder, layout["ai"])
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
    for name in ("login", "courses", "convert"):
        sub.add_parser(name)
    st = sub.add_parser("setup", help="首次设置：登录、选课、在桌面建课程文件夹，然后同步")
    st.add_argument("--no-first-sync", action="store_true", help="设置完不立即同步")
    d = sub.add_parser("ddl")
    d.add_argument("--non-interactive", action="store_true")
    s = sub.add_parser("sync")
    s.add_argument("--only", nargs="*", help="只同步这些课程代码，如 --only EEE339 COS301")
    s.add_argument("--refresh", action="store_true", help="忽略同步记录，重新检查所有文件")
    s.add_argument("--videos", action="store_true", help="也下载视频/音频")
    s.add_argument("--all-forums", action="store_true", help="所有讨论区都存档（默认只存公告）")
    s.add_argument("--no-convert", action="store_true", help="不生成 AI 可读版")
    s.add_argument("--non-interactive", action="store_true", help="登录失效时退出并保留上次结果，不弹登录窗口")
    sub.add_parser("doctor", help="检查本地运行环境")
    sch = sub.add_parser("schedule", help="Windows 每日定时同步")
    sch.add_argument("action", choices=["install", "remove", "status"])
    sch.add_argument("--time", "--at", default="09:00", help="每日运行时间 HH:MM")
    args = ap.parse_args(argv)
    cfg = load_config()
    if args.cmd in ("doctor", "schedule"):
        from . import maintenance
        if args.cmd == "doctor":
            raise SystemExit(maintenance.doctor(cfg, HOME, PROFILE))
        raise SystemExit(maintenance.schedule(args.action, HOME, args.time))
    fn = {"setup": cmd_setup, "login": cmd_login, "courses": cmd_courses, "sync": cmd_sync, "convert": cmd_convert, "ddl": cmd_ddl}[args.cmd]
    def run():
        try:
            asyncio.run(fn(cfg, args))
        except LoginExpired as exc:
            login_notice(cfg, str(exc))
            log(f"!! {exc}")
            raise SystemExit(2)
    if getattr(args, "non_interactive", False):
        from contextlib import redirect_stdout, redirect_stderr
        USER_DATA.mkdir(parents=True, exist_ok=True)
        with (USER_DATA / "last_sync.log").open("w", encoding="utf-8", buffering=1) as stream:
            with redirect_stdout(stream), redirect_stderr(stream):
                log(f"同步开始：{datetime.now().isoformat(timespec='seconds')}")
                try:
                    run()
                except BaseException as exc:
                    log(f"同步退出：{exc}")
                    raise
    else:
        run()


if __name__ == "__main__":
    main()
