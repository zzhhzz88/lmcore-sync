"""Export deadlines as an iCalendar (.ics) file that Outlook, Apple/Google
Calendar and phone calendars can import. Subscription requires hosting the file."""
from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit, parse_qsl, urlencode, urlunsplit
from datetime import datetime, timedelta, timezone


def _esc(s: str) -> str:
    return (s or "").replace("\r\n", "\n").replace("\r", "\n").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    """RFC 5545: lines longer than 75 octets are folded."""
    out, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > 74:
            out.append(cur.decode("utf-8"))
            cur = b" " + b
        else:
            cur += b
    out.append(cur.decode("utf-8"))
    return "\r\n".join(out)


def _utc(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _identity(row: dict) -> str:
    """Keep identity stable when dates or display weighting change."""
    if row.get("uid"):
        return str(row["uid"])
    url = row.get("url", "")
    if url:
        parsed = urlsplit(url)
        query = urlencode([(k, v) for k, v in parse_qsl(parsed.query)
                           if k not in {"sesskey", "token", "redirect"}])
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))
    name = re.sub(r"[（(](?:占总评\s*)?\d+(?:\.\d+)?%[）)]", "", row.get("name", "")).strip()
    return f"{row.get('course', '')}|{name}"


def build_ics(rows: list[dict], is_done) -> str:
    """Export known dates, interpreting naive times in the computer's timezone.

    A timed item marks the supplied deadline / test time exactly; no exam
    duration or advance work session is invented. Date-only rows are all-day
    events clearly marked as having an unknown time.
    """
    now = datetime.now(timezone.utc)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//lmcore-sync//Deadlines//ZH", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", "X-WR-CALNAME:作业与考试 DDL"]
    seen = set()
    for r in rows:
        d = r.get("due")
        if not isinstance(d, datetime) or is_done(r.get("status", "")):
            continue
        if d.astimezone(timezone.utc) < now - timedelta(days=1):
            continue
        exam = r.get("exam") or bool(re.search(r"考试|测|exam|test|quiz", r.get("type", ""), re.I))
        code = (r.get("course") or "").split("-")[0]
        uid = hashlib.sha256(_identity(r).encode("utf-8")).hexdigest()[:32] + "@lmcore-sync"
        if uid in seen:
            continue
        seen.add(uid)
        desc = " / ".join(str(x) for x in (r.get("due_text"), r.get("status"), r.get("url")) if x)
        ev = ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{_utc(now)}",
              f"SUMMARY:{_esc(f'[{code}] ' + r.get('name', ''))}"]
        if r.get("date_only"):
            day = d.date()
            ev += [f"DTSTART;VALUE=DATE:{day:%Y%m%d}", f"DTEND;VALUE=DATE:{day + timedelta(days=1):%Y%m%d}"]
            desc = "具体时间待确认；全天事件仅标记日期。" + desc
        else:
            ev.append(f"DTSTART:{_utc(d)}")
        if desc:
            ev.append(f"DESCRIPTION:{_esc(desc)}")
        if r.get("url"):
            # RFC URI fields must not introduce new content lines.
            ev.append("URL:" + str(r["url"]).replace("\r", "").replace("\n", ""))
        # Relative triggers are anchored to DTSTART, i.e. the actual deadline.
        # Date-only rows have no known hour; use whole-day reminders only.
        triggers = ("-P3D", "-P1D") if exam else (("-P1D",) if r.get("date_only") else ("-P1D", "-PT2H"))
        for trig in triggers:
            ev += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_esc(r.get('name', ''))}", f"TRIGGER:{trig}", "END:VALARM"]
        ev.append("END:VEVENT")
        lines += ev
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
