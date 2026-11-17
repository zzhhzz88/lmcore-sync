"""Collect assignment / quiz deadlines into one Markdown table."""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from pathlib import Path

from bs4 import BeautifulSoup

from .moodle import LoginExpired

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], 1)}


def parse_moodle_date(text: str, now: datetime) -> datetime | None:
    """'Sunday, 13 December, 23:59' or '13 December 2026, 23:59' -> datetime.
    When the year is missing, pick the year that puts the date closest to now."""
    m = re.search(r"(\d{1,2})\s+([A-Za-z]+)(?:\s+(\d{4}))?,?\s+(\d{1,2}):(\d{2})\s*(AM|PM)?", text or "", re.I)
    if not m or m.group(2).lower() not in MONTHS:
        return None
    day, mon = int(m.group(1)), MONTHS[m.group(2).lower()]
    hh, mm = int(m.group(4)), int(m.group(5))
    if m.group(6):
        hh = hh % 12 + (12 if m.group(6).upper() == "PM" else 0)
    years = [int(m.group(3))] if m.group(3) else [now.year - 1, now.year, now.year + 1]
    cands = []
    for year in years:
        try:
            cands.append(datetime(year, mon, day, hh, mm))
        except ValueError:
            continue
    return min(cands, key=lambda d: abs((d - local_naive(now)).total_seconds()), default=None)


def parse_handbook(html: str, now: datetime) -> list[dict]:
    """XJTLU module handbook (e-Bridge HTML): each Initial Assessment task is a
    bold title '<name> (NN% of the module mark)' followed by a table with
    'Assessment Task | ... | Weighting | Release Date | Due Date'."""
    soup = BeautifulSoup(html, "html.parser")
    resit = soup.find(string=re.compile(r"Resit Assessment", re.I))
    cut = (resit.parent.sourceline, resit.parent.sourcepos) if resit is not None and resit.parent else None
    out = []
    for table in soup.find_all("table"):
        head = [c.get_text(" ", strip=True).lower() for c in (table.find("tr") or []).find_all(["th", "td"])] \
            if table.find("tr") else []
        if not head or "weighting" not in " ".join(head) or "due date" not in " ".join(head):
            continue
        # stop at the resit section
        if cut and (table.sourceline, table.sourcepos) > cut:
            break
        trs = table.find_all("tr")
        cells = [c.get_text(" ", strip=True) for c in trs[1].find_all(["td", "th"])] if len(trs) > 1 else []
        if len(cells) < 5:
            continue
        task, weight, release, due_txt = cells[0], cells[2], cells[3], cells[4]
        title_el = table.find_previous(string=re.compile(r"of the module mark", re.I))
        title = re.sub(r"\s*\(.*$", "", title_el.strip()) if title_el else task
        desc = ""
        for tr in trs:
            t = tr.get_text(" ", strip=True)
            if t.lower().startswith("brief description"):
                nxt = tr.find_next_sibling("tr")
                desc = nxt.get_text(" ", strip=True) if nxt else ""
        m = re.search(r"(\d{1,2})/([A-Za-z]{3})/(\d{4})(?:\s+(\d{1,2}):(\d{2}))?", due_txt)
        due, date_only = None, False
        if m:
            mon = next(i for n, i in MONTHS.items() if n.startswith(m.group(2).lower()))
            if m.group(4):
                due = datetime(int(m.group(3)), mon, int(m.group(1)), int(m.group(4)), int(m.group(5)))
            else:
                due, date_only = datetime(int(m.group(3)), mon, int(m.group(1)), 23, 59), True
        exam = bool(re.search(r"exam", title + task, re.I)) and not m
        rel = re.search(r"(\d{1,2})/([A-Za-z]{3})/(\d{4})", release)
        if rel and m and rel.group(0) != m.group(0)[:len(rel.group(0))] and date_only:
            desc = f"{release} 至 {due_txt}。" + desc     # a window, e.g. in-lab test over two days
        out.append({"name": f"{title}（{weight}）", "weight": weight, "due": due, "date_only": date_only,
                    "desc": desc[:200], "exam": exam, "kind": "期末考试" if exam else "Handbook 考核"})
    return out


async def collect(m, courses: list[dict]) -> list[dict]:
    """Return available rows; expose failed sources in m.deadline_warnings.

    Callers must check warnings before replacing a previous complete report.
    Authentication failures still propagate immediately.
    """
    now = datetime.now()
    rows: dict[tuple, dict] = {}
    m.deadline_warnings = []

    def warn(source: str, error: Exception):
        # Browser exception strings can contain authenticated request URLs.
        message = f"{source}读取失败（{type(error).__name__}），DDL 数据可能不完整"
        m.deadline_warnings.append(message)
        logger = getattr(m, "log", None)
        if logger:
            logger(f"   !! {message}")
    # 1) per-course activity index pages (complete list incl. already submitted)
    for c in courses:
        for mod, label in (("assign", "作业"), ("quiz", "测验")):
            try:
                html, url = await m.html(f"{m.base}/mod/{mod}/index.php?id={c['id']}")
            except LoginExpired:
                raise
            except Exception as error:
                warn(f"{c['shortname']} {label}列表", error)
                continue
            soup = BeautifulSoup(html, "html.parser")
            table = soup.select_one("#region-main table")
            if not table:
                continue
            heads = [th.get_text(" ", strip=True).lower() for th in table.select("thead th")] or \
                    [td.get_text(" ", strip=True).lower() for td in table.select("tr")[0].select("th,td")]
            def col(*names):
                for i, h in enumerate(heads):
                    if any(n in h for n in names):
                        return i
                return None
            i_name, i_due = col("name"), col("due date", "quiz closes", "close")
            i_sub = col("submission status", "submitted", "attempt")
            if i_name is None or i_due is None:
                continue
            for tr in table.select("tbody tr"):
                cells = tr.select("td")
                if len(cells) <= max(i_name, i_due):
                    continue
                a = cells[i_name].select_one("a")
                name = (a.get_text(" ", strip=True) if a else cells[i_name].get_text(" ", strip=True))
                due_txt = cells[i_due].get_text(" ", strip=True)
                due = parse_moodle_date(due_txt, now)
                status = cells[i_sub].get_text(" ", strip=True) if i_sub is not None and i_sub < len(cells) else ""
                link = a["href"] if a and a.has_attr("href") else url
                rows[(c["shortname"], name)] = {"course": c["shortname"], "name": name, "type": label,
                                                "due": due, "due_text": due_txt, "status": status, "url": link}
    # 1b) the module handbook's assessment tables: the official list of every
    #     assessed task with weighting and dates (catches in-lab tests etc.)
    handbook_rows: dict[str, list[dict]] = {}
    for c in courses:
        try:
            cms = await m.course_structure(c["id"])
            hb = next((cm for cm in cms if cm["modname"] == "resource" and re.search(r"handbook", cm["name"], re.I)
                       and re.search(r"module|mhb|" + re.escape(c["shortname"][:6]), cm["name"], re.I)), None)
            if not hb:
                continue
            body, _, _ = await m.fetch(f"{m.base}/mod/resource/view.php?id={hb['id']}&redirect=1")
            handbook_rows[c["shortname"]] = parse_handbook(body.decode("utf-8", "replace"), now)
        except LoginExpired:
            raise
        except Exception as error:
            warn(f"{c['shortname']} Module Handbook", error)
            continue
    for cs, items in handbook_rows.items():
        for h in items:
            # same task already known from Moodle (same day)? -> just add the weighting
            # deadlines often get extended, so accept the nearest Moodle item within 10 days
            cands = [r for r in rows.values() if r["course"] == cs and r["due"] and h["due"]
                     and abs((r["due"] - h["due"]).days) <= 10 and not r.get("weight")]
            twin = min(cands, key=lambda r: abs((r["due"] - h["due"]).total_seconds()), default=None)
            if twin:
                twin["weight"] = h["weight"]
                twin["handbook"] = h["name"]
                continue
            rows[(cs, "handbook:" + h["name"])] = {
                "course": cs, "name": h["name"], "type": h["kind"], "due": h["due"], "date_only": h["date_only"],
                "due_text": h["desc"], "status": "", "url": "", "weight": h["weight"], "exam": h["exam"],
                "from_handbook": True}
    # 2) assessments that exist on the course page but have NO assignment/quiz
    #    entry (e.g. a "Coursework 2" folder for an in-lab test, a "Take-home"
    #    PDF). They have no Moodle due date, so flag them instead of missing them.
    ASSESS = re.compile(r"coursework|\bcw ?\d|assessment|assignment|in-?class[- ]test|\btest\b|\bexam\b|quiz|project brief|作业|考试|测验", re.I)
    NOT_ASSESS = re.compile(r"past paper|sample|mock|往年|cover sheet|integrity|recording|handbook|solution|answer|feedback", re.I)
    for c in courses:
        try:
            cms = await m.course_structure(c["id"])
        except LoginExpired:
            raise
        except Exception as error:
            warn(f"{c['shortname']} 课程考核目录", error)
            continue
        have = [k[1].lower() for k in rows if k[0] == c["shortname"]]
        for cm in cms:
            if cm["modname"] in ("assign", "quiz", "label", "forum", "url") or not ASSESS.search(cm["name"]) \
                    or NOT_ASSESS.search(cm["name"]):
                continue
            key = re.split(r"[:：]", cm["name"])[0].strip().lower()
            if any(key in h or h.split(":")[0].strip() in cm["name"].lower() for h in have):
                continue
            rows[(c["shortname"], cm["name"])] = {
                "course": c["shortname"], "name": cm["name"], "type": "无提交入口", "due": None, "due_text": "",
                "status": "Moodle 上没有截止时间，看要求文件/公告确认", "url": cm.get("url", ""), "unknown": True,
                "section": cm.get("section", "")}
    # 3) timeline: pending actions in every course (catches courses not configured)
    try:
        data = await m.ajax("core_calendar_get_action_events_by_timesort",
                            {"timesortfrom": int(time.time()) - 86400 * 30, "limitnum": 50})
        for e in data.get("events", []):
            name = re.sub(r"\s+is due$", "", e["name"])
            key = (e["course"]["shortname"], name)
            if any(k[0] == key[0] and (name in k[1] or k[1] in name) for k in rows):
                continue
            rows[key] = {"course": e["course"]["shortname"], "name": e["name"], "type": e.get("modulename", ""),
                         "due": datetime.fromtimestamp(e["timesort"]), "due_text": "",
                         "status": "待完成" + ("（已过期）" if e.get("overdue") else ""), "url": e.get("url", "")}
    except LoginExpired:
        raise
    except Exception as error:
        warn("Moodle 时间线", error)
    return sorted(rows.values(), key=lambda r: r["due"] or datetime.max)


def is_done(status: str) -> bool:
    """Treat explicit negative / draft states as pending, including 'not graded'."""
    s = (status or "").lower()
    if re.search(r"not\s+(?:yet\s+)?(?:submitted|finished|graded)|no\s+submission|unsubmitted|unfinished|ungraded|draft|未提交|尚未提交|未完成|未评分|草稿", s):
        # A submitted item can still be awaiting grading.
        return bool(re.search(r"\bsubmitted\s+for\s+grading\b|已提交", s)) and not re.search(
            r"not\s+(?:yet\s+)?submitted|no\s+submission|unsubmitted|未提交|草稿|draft", s)
    return bool(re.search(r"\b(?:submitted|finished|graded)\b|已提交|已完成|已评分", s))


def local_naive(value: datetime | None) -> datetime | None:
    """All report comparisons use the computer's local time, as Moodle text does.

    Explicit ISO offsets are converted to local time before removing tzinfo.
    A date with no offset already represents local wall time.
    """
    if value is not None and value.tzinfo is not None:
        return value.astimezone().replace(tzinfo=None)
    return value


def merge_extra(rows: list[dict], extra: list[dict] | None) -> list[dict]:
    """Apply manual corrections once; repeated calls are idempotent.

    Keeps the historical in-place list API, but never mutates caller row dicts.
    Explicit names / 'replaces' determine replacement, not merely a shared date.
    """
    rows[:] = [{**r, "due": local_naive(r.get("due"))} for r in rows]
    course_key = lambda value: re.split(r"[-_\s]", value or "")[0].casefold()
    for x in extra or []:
        reps = x.get("replaces") or []
        reps = [reps] if isinstance(reps, str) else reps
        raw_due = x.get("due", "")
        try:
            due = local_naive(datetime.fromisoformat(raw_due.replace("Z", "+00:00")))
            if len(raw_due) == 10:
                due = due.replace(hour=23, minute=59)
        except (ValueError, TypeError, AttributeError):
            due = None
        name = x.get("name", "")
        rows[:] = [r for r in rows if not (
            course_key(r.get("course")) == course_key(x.get("course")) and (
                r.get("name", "").casefold() == name.casefold()
                or any(str(k).casefold() in r.get("name", "").casefold() for k in reps if k)))]
        rows.append({"date_only": isinstance(raw_due, str) and len(raw_due) == 10,
                     "course": x.get("course", ""), "name": name, "type": x.get("type", "手动添加"),
                     "due": due, "due_text": x.get("note", ""), "status": x.get("status", ""),
                     "url": x.get("url", ""), "weight": x.get("weight", ""),
                     "unknown": due is None, "exam": x.get("exam", False)})
    rows.sort(key=lambda r: r["due"] or datetime.max)
    return rows


def render(rows: list[dict], extra: list[dict] | None = None, recent: list[dict] | None = None,
           notice: str = "", base_dir: Path | None = None) -> str:
    """recent: [{course, date, status, path(Path), title}] – files added/updated lately."""
    now = datetime.now()
    rows = merge_extra([dict(r) for r in rows], extra)
    link = lambda r: f"[{r['name']}]({r['url']})" if r.get("url") else r["name"]

    def left(d):
        if not d:
            return "?"
        delta = d - now
        if delta.total_seconds() < 0:
            return "已截止"
        days = delta.days
        return f"{days} 天 {delta.seconds // 3600} 小时" if days < 3 else f"{days} 天"

    def when(r, full=True):
        d = r["due"]
        if r.get("date_only"):
            return f"{d:%Y-%m-%d %a} 时间待定" if full else f"{d.month:02d}月{d.day:02d}日 {d:%a}（时间待定）"
        return f"{d:%Y-%m-%d %a %H:%M}" if full else f"{d.month:02d}月{d.day:02d}日 {d:%a %H:%M}"

    unknown = [r for r in rows if r.get("unknown")]
    exams = [r for r in rows if r.get("exam") and not r["due"]]
    upcoming = [r for r in rows if r["due"] and r["due"] >= now - timedelta(hours=1)]
    past = [r for r in rows if r not in upcoming and r not in unknown and r not in exams]
    for r in rows:
        if r.get("weight") and r["weight"] not in r["name"]:
            r["name"] = f"{r['name']}（占总评 {r['weight']}）"
    out = [f"# 作业 DDL 汇总", "", f"更新时间：{now:%Y-%m-%d %H:%M}（来源：Learning Mall Core，每次同步自动刷新）", ""]
    if notice:
        out += [f"> ⚠ **{notice}**", ""]
    todo = [r for r in upcoming if not is_done(r["status"])]
    if todo:
        out += ["## ⚠ 待办（还没交的作业 / 还没考的考试）", ""]
        out += [f"- **{when(r, False)}** · {r['course'].split('-')[0]} · {r['name']}（剩 {left(r['due'])}）"
                + (f" — {r['due_text']}" if r.get("due_text") and r.get("type") != "作业" else "") for r in todo]
        out.append("")
    if recent:
        import os
        out += [f"## 📥 最近 7 天新增 / 更新的资料（{len(recent)} 个）", ""]
        by_course: dict[str, list[dict]] = {}
        for x in sorted(recent, key=lambda x: (x["course"], x.get("date", "")), reverse=True):
            by_course.setdefault(x["course"].split("-")[0], []).append(x)
        for code, items in sorted(by_course.items()):
            out.append(f"**{code}**")
            for x in items[:15]:
                try:
                    rel = os.path.relpath(x["path"], base_dir).replace("\\", "/") if base_dir else str(x["path"])
                except ValueError:  # different Windows drives cannot use relative links
                    rel = Path(x["path"]).resolve().as_uri()
                tag = "🆕" if x["status"] == "新下载" else "↻ 更新"
                out.append(f"- {tag} {x.get('date', '')} [{Path(x['path']).name}](<{rel}>)")
            if len(items) > 15:
                out.append(f"- …还有 {len(items) - 15} 个，见该课程的 `00_资料清单.md`")
            out.append("")
    if unknown:
        out += ["## ❓ 有考核但 Moodle 上没有截止时间（务必自己确认）", "",
                "这些在课程页面上像是作业/考试，但没有 Moodle 作业提交入口（常见于机房上机测试、线下考试）。"
                "确认日期后写进 config.json 的 `extra_deadlines`（加 `\"replaces\": \"名称关键词\"`），这里就会换成准确日期。", ""]
        out += [f"- {r['course'].split('-')[0]} · {link(r)}（版块：{r.get('section', '')}）" for r in unknown]
        out.append("")
    if exams:
        out += ["## 📝 期末考试（Handbook 列出，日期等考试周安排公布）", ""]
        out += [f"- {r['course'].split('-')[0]} · {r['name']}" + (f" — {r['due_text']}" if r.get("due_text") else "") for r in exams]
        out.append("")
    out += ["## 即将截止（全部）", "", "| 截止时间 | 剩余 | 课程 | 名称 | 类型 | 提交状态 |", "|---|---|---|---|---|---|"]
    for r in upcoming:
        out.append(f"| {when(r)} | {left(r['due'])} | {r['course']} | {link(r)} | {r['type']} | {r['status'] or '-'} |")
    if past:
        out += ["", "## 已截止", "", "| 截止时间 | 课程 | 名称 | 提交状态 |", "|---|---|---|---|"]
        for r in reversed(past):
            d = f"{r['due']:%Y-%m-%d %H:%M}" if r["due"] else r["due_text"] or "?"
            out.append(f"| {d} | {r['course']} | {link(r)} | {r['status'] or '-'} |")
    out += ["", "> 来源：各课 Moodle 作业/测验列表 + Module Handbook 的考核表（Initial Assessment）+ 课程页上像考核但没有提交入口的资料。",
            "> 老师后来在公告/邮件里改了安排的，以公告为准，写进 config.json 的 `extra_deadlines`（`replaces` 填要替换的名称关键词）。"]
    return "\n".join(out) + "\n"


def default_path() -> Path:
    return Path.home() / "Desktop" / "作业DDL.md"
