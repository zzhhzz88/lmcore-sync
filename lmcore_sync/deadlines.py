"""Collect assignment / quiz deadlines into one Markdown table."""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from pathlib import Path

from bs4 import BeautifulSoup

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
    if m.group(3):
        return datetime(int(m.group(3)), mon, day, hh, mm)
    cands = [datetime(y, mon, day, hh, mm) for y in (now.year - 1, now.year, now.year + 1)]
    return min(cands, key=lambda d: abs((d - now).total_seconds()))


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
    now = datetime.now()
    rows: dict[tuple, dict] = {}
    # 1) per-course activity index pages (complete list incl. already submitted)
    for c in courses:
        for mod, label in (("assign", "作业"), ("quiz", "测验")):
            try:
                html, url = await m.html(f"{m.base}/mod/{mod}/index.php?id={c['id']}")
            except Exception:
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
        except Exception:
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
        except Exception:
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
    except Exception:
        pass
    return sorted(rows.values(), key=lambda r: r["due"] or datetime.max)


def is_done(status: str) -> bool:
    s = status.lower()
    return any(k in s for k in ("submitted", "已提交", "finished", "graded")) and "no submission" not in s


def render(rows: list[dict], extra: list[dict] | None = None) -> str:
    now = datetime.now()
    for x in extra or []:  # manual entries from config.json ("extra_deadlines")
        # a manual entry replaces an auto-detected "no due date" row of the same item
        reps = x.get("replaces") or []
        reps = [reps] if isinstance(reps, str) else reps
        try:
            xday = datetime.fromisoformat(x["due"]).date()
        except Exception:
            xday = None
        same_course = lambda r: r["course"][:6].lower() == x.get("course", "")[:6].lower()
        rows[:] = [r for r in rows if not (
            (r.get("unknown") or r.get("from_handbook")) and same_course(r) and (
                any(k.lower() in r["name"].lower() for k in reps)
                or (xday and r["due"] and r["due"].date() == xday)))]
        try:
            due = datetime.fromisoformat(x["due"])
            if len(x["due"]) == 10:          # date only -> sort at end of that day
                due = due.replace(hour=23, minute=59)
        except Exception:
            due = None
        rows.append({"date_only": len(x.get("due", "")) == 10, "course": x.get("course", ""), "name": x.get("name", ""), "type": x.get("type", "手动添加"),
                     "due": due, "due_text": x.get("note", ""), "status": x.get("status", ""), "url": x.get("url", ""),
                     "weight": x.get("weight", "")})
    rows.sort(key=lambda r: r["due"] or datetime.max)
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
            return f"{d:%Y-%m-%d %a} 时间待定" if full else f"{d:%m月%d日 %a}（时间待定）"
        return f"{d:%Y-%m-%d %a %H:%M}" if full else f"{d:%m月%d日 %a %H:%M}"

    unknown = [r for r in rows if r.get("unknown")]
    exams = [r for r in rows if r.get("exam") and not r["due"]]
    upcoming = [r for r in rows if r["due"] and r["due"] >= now - timedelta(hours=1)]
    past = [r for r in rows if r not in upcoming and r not in unknown and r not in exams]
    for r in rows:
        if r.get("weight") and r["weight"] not in r["name"]:
            r["name"] = f"{r['name']}（占总评 {r['weight']}）"
    out = [f"# 作业 DDL 汇总", "", f"更新时间：{now:%Y-%m-%d %H:%M}（来源：Learning Mall Core，每次同步自动刷新）", ""]
    todo = [r for r in upcoming if not is_done(r["status"])]
    if todo:
        out += ["## ⚠ 待办（还没交的作业 / 还没考的考试）", ""]
        out += [f"- **{when(r, False)}** · {r['course'].split('-')[0]} · {r['name']}（剩 {left(r['due'])}）"
                + (f" — {r['due_text']}" if r.get("due_text") and r.get("type") != "作业" else "") for r in todo]
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
