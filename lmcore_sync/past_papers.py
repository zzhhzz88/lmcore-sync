"""Past exam papers from the XJTLU library's "Theses and Dissertations" site
(etd.xjtlu.edu.cn → Past Exam Papers), looked up ONCE per course, when its
folder is first set up (not on every sync).

* Search by module code; if the library has nothing under that code (modules
  get renamed: ADS305 used to be INT303, ISA305 was INT301), search by the
  module title from Learning Mall and pick the matching old code.
* Download only through the site's own "View Online" flow and only when the
  library grants it to this account. If the library refuses (no right / "请解
  锁后再使用"), nothing is forced: a list with links to each paper is written so
  the student can open them in the browser.
* Papers are saved as <CODE>_<year>_<Final|Resit>.pdf in <exam folder>/往年试题/.
  Copyright belongs to the university: personal study only.
"""
from __future__ import annotations

import asyncio
import base64
import html as htmlmod
import json
import re
from datetime import date
from pathlib import Path

ETD = "https://etd.xjtlu.edu.cn"
DB = {"DbId": 3, "DbCode": "EXAMXJTLU"}
SUB = "往年试题"
LIST = "往年试题清单.md"
STATE = "past_papers.json"
FINAL = ("done", "not_found")          # outcomes that are not retried automatically

# runs inside the ETD page, so the browser supplies the school's own session
_CALL = """async ([path, body]) => {
  const tok = localStorage.getItem('token');
  if (!tok) return {status: 401};
  const h = {'Authorization': 'bearer ' + tok};
  if (body) h['Content-Type'] = 'application/json';
  const r = await fetch(path, {method: body ? 'POST' : 'GET', headers: h, body: body ? JSON.stringify(body) : undefined});
  const ct = r.headers.get('content-type') || '';
  if (ct.includes('pdf') || ct.includes('octet')) {
    const b = new Uint8Array(await r.arrayBuffer()); let s = '';
    for (let i = 0; i < b.length; i += 0x8000) s += String.fromCharCode.apply(null, b.subarray(i, i + 0x8000));
    return {status: r.status, b64: btoa(s)};
  }
  const t = await r.text();
  try { return {status: r.status, json: JSON.parse(t)}; } catch (e) { return {status: r.status, text: t.slice(0, 300)}; }
}"""


def clean(v) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", htmlmod.unescape(str(v or "")))).strip()


def module_title(fullname: str, shortname: str) -> str:
    """'ADS305-2627-S1-Big Data Analytics' -> 'Big Data Analytics'."""
    t = fullname or ""
    if shortname and t.startswith(shortname):
        t = t[len(shortname):]
    return re.sub(r"^[\s\-–:]+", "", t).strip() or fullname


def pick_code(rows: list[dict], current: str) -> tuple[str | None, list[str]]:
    """Among title matches choose the code most like the current module:
    not a campus variant (…TC), same year level, most recent, most papers."""
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(clean(r.get("paperCode")).upper(), []).append(r)
    if not by:
        return None, []
    lvl = re.search(r"\d", current)

    def score(code):
        d = re.search(r"\d", code)
        return (not re.search(r"\d[A-Z]+$", code), bool(lvl and d and d.group() == lvl.group()),
                max(clean(r.get("year")) for r in by[code]), len(by[code]))

    best = max(by, key=score)
    return best, sorted(c for c in by if c != best)


def paper_name(code: str, year: str, typ: str) -> str:
    return f"{code}_{year}_{({'F': 'Final', 'R': 'Resit'}).get(typ, typ or 'Exam')}.pdf"


def detail_link(rid) -> str:
    return f"{ETD}/index.html#/PaperDetail/3/EXAMXJTLU/{rid}"


class Locked(Exception):
    """The library refused the file (no right / must be unlocked on the website)."""


class RateLocked(Exception):
    """The library temporarily blocked the account for 'bulk downloading'."""


PAPER_GAP_S = 25          # pause between two PDF downloads (the library blocks bursts)
MAX_PER_RUN = 12          # PDFs per run at most; the rest is picked up on the next day
COOLDOWN_H = 2            # after a lock: stay away for this long (the school says 60 min)
_LOCK_RE = re.compile(r"temporarily locked|bulk download|批量下载|暂时锁定", re.I)


def cooldown_file(home: Path) -> Path:
    return home / "user_data" / "etd_cooldown.json"


def in_cooldown(home: Path) -> str | None:
    from datetime import datetime
    f = cooldown_file(home)
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        until = datetime.fromisoformat(d["until"])
        if datetime.now() < until:
            return f"图书馆试卷库因“批量下载”暂时锁定了账号，{until:%H:%M} 之后再试（{d.get('message', '')[:80]}）"
    except Exception:
        pass
    return None


def start_cooldown(home: Path, message: str):
    from datetime import datetime, timedelta
    f = cooldown_file(home)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"until": (datetime.now() + timedelta(hours=COOLDOWN_H)).isoformat(timespec="minutes"),
                             "message": message}, ensure_ascii=False), encoding="utf-8")


class EtdClient:
    """Uses a tab of the tool's browser; the school SSO signs it in silently."""

    def __init__(self, m, log=print):
        self.m, self.log, self.page = m, log, None
        self.downloaded = 0
        self.lock_message = ""
        self._last_pdf = 0.0

    async def _has_token(self) -> bool:
        try:
            return bool(await self.page.evaluate("() => !!localStorage.getItem('token')"))
        except Exception:
            return False

    async def open(self) -> bool:
        """The library swaps an SSO ?code= for its own token on the HOME page only.
        If the school SSO doesn't bounce us there by itself, press its Login button
        (it signs in silently with the existing XJTLU session)."""
        self.page = await self.m.ctx.new_page()

        async def watch(r):
            if r.url.startswith(ETD + "/token") and r.status >= 400:
                try:
                    txt = await r.text()
                except Exception:
                    txt = ""
                if _LOCK_RE.search(txt):
                    self.lock_message = txt[:300]
        self.page.on("response", lambda r: asyncio.ensure_future(watch(r)))
        await self.page.goto(ETD + "/index.html", wait_until="domcontentloaded")
        for attempt in range(2):
            for _ in range(25):
                if await self._has_token():
                    return True
                if self.lock_message:
                    raise RateLocked(self.lock_message)
                await asyncio.sleep(1)
            if attempt == 0:
                try:
                    btn = self.page.get_by_role("button", name=re.compile(r"^\s*Login\s*$", re.I))
                    if await btn.count():
                        await btn.first.click()
                    else:
                        await self.page.goto(ETD + "/index.html", wait_until="domcontentloaded")
                except Exception:
                    pass
        return False

    async def close(self):
        if self.page:
            await self.page.close()

    async def _call(self, path, body=None):
        await asyncio.sleep(getattr(self.m, "delay", 0.3))
        return await self.page.evaluate(_CALL, [path, body])

    async def search(self, field: str, text: str) -> list[dict]:
        res = await self._call("/api/v1/Search/Search", {
            **DB, "OrderField": "", "PageIndex": 1, "PageSize": 50, "CategoryExpression": "", "CatalogCode": "",
            "AdvancedSearchParams": [{"SearchType": "1", "SearchField": field, "SearchContent": text},
                                     {"SearchField": "FullText", "SearchContent": 0}]})
        j = res.get("json") or {}
        if res.get("status") != 200 or not j.get("isSuccess"):
            raise RuntimeError(f"试卷库检索失败（HTTP {res.get('status')}）")
        return j.get("result", {}).get("overviewResult") or []

    async def detail(self, rid) -> dict:
        res = await self._call(f"/api/v1/Detail?dbId=3&dbCode=EXAMXJTLU&recordId={rid}&userId=")
        d = (res.get("json") or {}).get("detailResult") or {}
        rec = ((d.get("result") or {}).get("detailResult") or [{}])[0]
        return {"type": clean(rec.get("typeABB")), "semester": clean(rec.get("semsterABB")),
                "flag": rec.get("flag") or 0, "can_view": bool(d.get("hasBrowserRight"))}

    async def pdf(self, rid, flag) -> bytes:
        import time
        if self.downloaded >= MAX_PER_RUN:
            raise Locked(f"本次已下载 {MAX_PER_RUN} 份，为避免被学校判定为批量下载，剩下的明天再取")
        wait = PAPER_GAP_S - (time.monotonic() - self._last_pdf)
        if self._last_pdf and wait > 0:
            await asyncio.sleep(wait)
        self._last_pdf = time.monotonic()
        urls = (await self._call(f"/api/v1/file/BrowserUrl/{flag}?dbId=3&dbCode=EXAMXJTLU&recordId={rid}&userId=")).get("json")
        if not isinstance(urls, list) or not urls:
            raise Locked("学校没有提供在线阅读地址")
        res = await self._call(urls[0])
        msg = json.dumps(res.get("json"), ensure_ascii=False) if res.get("json") is not None else (res.get("text") or "")
        if _LOCK_RE.search(msg):
            raise RateLocked(msg[:300])
        if res.get("status") != 200 or not res.get("b64"):
            raise Locked(clean((res.get("json") if isinstance(res.get("json"), str) else "") or res.get("text") or f"HTTP {res.get('status')}"))
        body = base64.b64decode(res["b64"])
        if not body.startswith(b"%PDF") or b"%%EOF" not in body[-4096:]:
            raise Locked("返回的不是完整 PDF")
        self.downloaded += 1
        return body


async def run_course(client, course: dict, folder: Path, exam_dir: str, cfg: dict, log=print, want: int = 6) -> dict:
    """Find and fetch up to `want` most recent papers. Returns the state dict."""
    from .courses import course_code
    code = course_code(course["shortname"])
    target = folder / exam_dir / SUB
    title = module_title(course.get("fullname", ""), course.get("shortname", ""))
    override = (cfg.get("past_paper_codes") or {}).get(code)
    used, others = override or code, []
    rows = [r for r in await client.search("PaperCode", used) if clean(r.get("paperCode")).upper() == used]
    if not rows and not override and title:
        hits = await client.search("PaperTitle", title)
        used, others = pick_code(hits, code)
        rows = [r for r in hits if clean(r.get("paperCode")).upper() == used] if used else []
    st = {"code": code, "searched_code": used, "other_codes": others, "title": title,
          "last_try": date.today().isoformat(), "papers": []}
    if not rows:
        st["status"] = "not_found"
        log(f"   往年试题：试卷库里没有 {code}，也没有同名课程「{title}」")
        return st
    rows.sort(key=lambda r: clean(r.get("year")), reverse=True)
    locked_reason = None
    for r in rows[:want]:
        rid, year = r.get("syS_FLD_SYSID"), clean(r.get("year"))
        d = await client.detail(rid)
        name = paper_name(used, year, d["type"])
        item = {"record_id": rid, "year": year, "type": d["type"], "file": name, "link": detail_link(rid)}
        if (target / name).exists():
            item["status"] = "已有"
        elif locked_reason or not d["can_view"]:
            item["status"] = "需在网页打开" if d["can_view"] else "无阅读权限"
        else:
            try:
                body = await client.pdf(rid, d["flag"])
                target.mkdir(parents=True, exist_ok=True)
                (target / name).write_bytes(body)
                item["status"] = "已下载"
                log(f"      + {exam_dir}/{SUB}/{name}")
            except RateLocked:
                st["papers"].append({**item, "status": "需在网页打开"})
                st["status"], st["locked_reason"] = "partial", "图书馆暂时锁定了账号（批量下载保护）"
                write_list(target, course, st)
                raise
            except Locked as e:
                locked_reason = str(e) or "学校拒绝了下载"
                item["status"] = "需在网页打开"
        st["papers"].append(item)
    st["status"] = "done" if all(p["status"] in ("已有", "已下载") for p in st["papers"]) else "partial"
    st["locked_reason"] = locked_reason
    write_list(target, course, st)
    got = sum(p["status"] in ("已有", "已下载") for p in st["papers"])
    renamed = f"（往年代码 {used}）" if used != code else ""
    log(f"   往年试题{renamed}：{got}/{len(st['papers'])} 份在本地" + (f"；其余需要你在网页打开（{locked_reason}）" if locked_reason else ""))
    return st


def write_list(target: Path, course: dict, st: dict):
    target.mkdir(parents=True, exist_ok=True)
    rows = "\n".join(f"| {p['year']} | {({'F': 'Final 期末', 'R': 'Resit 补考'}).get(p['type'], p['type'] or '-')} | {p['status']} | "
                     f"{'`' + p['file'] + '`' if p['status'] in ('已有', '已下载') else ''} | [打开]({p['link']}) |" for p in st["papers"])
    note = ""
    if st["searched_code"] != st["code"]:
        note += (f"\n> 试卷库里没有 {st['code']} 的试卷，按课程名「{st['title']}」找到往年代码 **{st['searched_code']}**。"
                 + (f"同名的还有 {', '.join(st['other_codes'])}（多半是别的校区或专业），没有下载。" if st["other_codes"] else "")
                 + "请对照课程手册确认内容一致。\n")
    if st.get("locked_reason"):
        note += (f"\n> 学校这次没有允许下载（{st['locked_reason']}）。点表格里的「打开」，登录图书馆网站后在线阅读；"
                 "解锁后再运行 `python -m lmcore_sync past-papers --force` 即可补齐。\n")
    (target / LIST).write_text(
        f"# {course.get('fullname', st['code'])} — 往年试题\n\n来源：西浦图书馆 [Past Exam Papers]({ETD}/index.html#/SearchDetail/EXAMXJTLU)"
        f"，按最近年份取至多 6 份。版权归学校，**只供本人复习，不要上传或转发**。\n{note}\n"
        f"| 学年 | 类型 | 状态 | 文件 | 网页 |\n|---|---|---|---|---|\n{rows}\n", encoding="utf-8")


async def fetch_for_course(m, course: dict, folder: Path, exam_dir: str, cfg: dict, log=print, force=False,
                           client: EtdClient | None = None) -> dict | None:
    """Once per course: skipped when an earlier run finished (or found nothing),
    and tried at most once a day while papers are still missing."""
    sf = folder / ".lmcore" / STATE
    old = json.loads(sf.read_text(encoding="utf-8")) if sf.exists() else {}
    if not force and (old.get("status") in FINAL or old.get("last_try") == date.today().isoformat()):
        return None
    from .cli import HOME
    cool = in_cooldown(HOME)
    if cool:
        log(f"   往年试题：{cool}")
        return None
    own = client is None
    client = client or EtdClient(m, log)
    try:
        if own and not await client.open():
            log("   往年试题：图书馆试卷库没有登录成功，下次同步再试")
            st = {**old, "status": "need_login", "last_try": date.today().isoformat()}
        else:
            st = await run_course(client, course, folder, exam_dir, cfg, log)
    except RateLocked as e:
        start_cooldown(HOME, str(e))
        log(f"   往年试题：图书馆因“批量下载”暂时锁定了账号，{COOLDOWN_H} 小时内不再访问；之后的同步会自动补齐")
        st = {**old, "status": "rate_locked", "last_try": "", "error": str(e)[:200]}
    except Exception as e:
        log(f"   往年试题：查询失败，下次同步再试（{e}）")
        st = {**old, "status": "error", "error": str(e)[:200], "last_try": date.today().isoformat()}
    finally:
        if own:
            await client.close()
    sf.parent.mkdir(parents=True, exist_ok=True)
    sf.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    return st
