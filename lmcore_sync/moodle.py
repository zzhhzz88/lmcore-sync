"""Browser-session access to a Moodle site (works with SSO and with the mobile
web-service API switched off). Idea and download hardening adapted from
thntx/moodle-browser-scraper and elonuziel/moodle_scraper (both MIT)."""
from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse, parse_qs

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, BrowserContext, Page

import os
import shutil
import socket
import subprocess


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def find_browser(kind: str = "msedge") -> str | None:
    pf = [os.environ.get(k) for k in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
    rel = {"msedge": [r"Microsoft\Edge\Application\msedge.exe"],
           "chrome": [r"Google\Chrome\Application\chrome.exe"]}
    order = [kind] + [k for k in rel if k != kind] if kind in rel else []
    for k in order:
        for base in filter(None, pf):
            for r in rel[k]:
                p = os.path.join(base, r)
                if os.path.exists(p):
                    return p
    for name in ("msedge", "google-chrome", "chromium", "chrome"):
        w = shutil.which(name)
        if w:
            return w
    return None


PAGE_TIMEOUT = 45_000
DL_TIMEOUT = 180_000


@dataclass
class Item:
    """One thing found on a course page."""
    kind: str                 # file | html | link
    title: str                # activity name
    section: str              # Moodle section title
    modname: str              # resource, folder, assign, page, url, forum, book, label ...
    url: str                  # file url (kind=file) / page url
    filename: str = ""        # for files
    html: str = ""            # for kind=html (already converted to markdown)
    extra: dict = field(default_factory=dict)


class Moodle:
    def __init__(self, base: str, profile_dir: Path, browser: str = "msedge", headless: bool = True, log=print):
        self.base = base.rstrip("/")
        self.profile_dir = profile_dir
        self.browser = browser
        self.headless = headless
        self.log = log
        self._pw = None
        self.ctx: BrowserContext | None = None
        self.page: Page | None = None
        self.sesskey = ""

    # ------------------------------------------------------------------ session
    async def __aenter__(self):
        """Start a *normal* browser process (no automation flags, so SSO pages
        that detect webdriver still render) and attach to it over CDP."""
        self._pw = await async_playwright().start()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        exe = find_browser(self.browser) or self._pw.chromium.executable_path
        port = _free_port()
        args = [exe, f"--remote-debugging-port={port}", f"--user-data-dir={self.profile_dir}",
                "--no-first-run", "--no-default-browser-check", "--disable-sync",
                "--window-size=1200,900"]
        if self.headless:
            args.append("--headless=new")
        args.append("about:blank")
        self._proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        last = None
        for _ in range(60):
            try:
                self._browser = await self._pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
                break
            except Exception as e:
                last = e
                await asyncio.sleep(0.5)
        else:
            raise RuntimeError(f"无法连接浏览器: {last}")
        self.ctx = self._browser.contexts[0]
        await self.load_cookies()
        self.page = self.ctx.pages[0] if self.ctx.pages else await self.ctx.new_page()
        self.page.set_default_timeout(PAGE_TIMEOUT)
        return self

    @property
    def cookie_file(self) -> Path:
        return self.profile_dir.parent / "cookies.json"

    async def save_cookies(self):
        """Moodle/SSO use session cookies that vanish when the browser closes,
        so keep a copy (personal login data - never share user_data/)."""
        cookies = await self.ctx.cookies()
        self.cookie_file.write_text(json.dumps(cookies), encoding="utf-8")

    async def load_cookies(self):
        if self.cookie_file.exists():
            try:
                await self.ctx.add_cookies(json.loads(self.cookie_file.read_text(encoding="utf-8")))
            except Exception as e:
                self.log(f"   (读取已保存的登录状态失败: {e})")

    async def __aexit__(self, *exc):
        try:
            try:
                await self._browser.close()
            except Exception:
                pass
            try:
                self._proc.terminate()
                self._proc.wait(timeout=10)
            except Exception:
                pass
        finally:
            await self._pw.stop()

    async def _read_sesskey(self) -> str:
        try:
            return await self.page.evaluate("() => (window.M && M.cfg && M.cfg.sesskey) || ''")
        except Exception:
            return ""

    async def is_logged_in(self) -> bool:
        await self.page.goto(f"{self.base}/my/courses.php", wait_until="domcontentloaded")
        url = self.page.url
        if not url.startswith(self.base) or "/login" in url:
            return False
        self.sesskey = await self._read_sesskey()
        if self.sesskey:
            await self.save_cookies()  # keep refreshed cookies
        return bool(self.sesskey)

    async def wait_for_login(self, timeout_s: int = 900) -> bool:
        """Leave the (visible) browser on the login page until the user is in."""
        await self.page.goto(f"{self.base}/my/courses.php")
        t0 = time.time()
        while time.time() - t0 < timeout_s:
            url = self.page.url
            if url.startswith(self.base) and "/login" not in url and "saml" not in url:
                sk = await self._read_sesskey()
                if sk:
                    self.sesskey = sk
                    await self.save_cookies()
                    return True
            await asyncio.sleep(2)
        return False

    async def ajax(self, method: str, args: dict):
        res = await self.page.evaluate(
            """async ([sk, method, args]) => {
                const r = await fetch('/lib/ajax/service.php?sesskey=' + sk + '&info=' + method, {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify([{index: 0, methodname: method, args: args}])});
                return await r.json();
            }""",
            [self.sesskey, method, args],
        )
        if isinstance(res, dict) and res.get("error"):
            raise RuntimeError(f"{method}: {res}")
        r0 = res[0]
        if r0.get("error"):
            raise RuntimeError(f"{method}: {r0.get('exception')}")
        return r0["data"]

    # ------------------------------------------------------------------ courses
    async def courses(self) -> list[dict]:
        data = await self.ajax(
            "core_course_get_enrolled_courses_by_timeline_classification",
            {"offset": 0, "limit": 0, "classification": "all", "sort": "fullname"},
        )
        return [
            {"id": c["id"], "shortname": c.get("shortname", ""), "fullname": c.get("fullname", ""),
             "url": c.get("viewurl", f"{self.base}/course/view.php?id={c['id']}")}
            for c in data["courses"]
        ]

    async def html(self, url: str) -> tuple[str, str]:
        resp = await self.page.goto(url, wait_until="domcontentloaded")
        return await self.page.content(), self.page.url

    async def course_structure(self, course_id: int) -> list[dict]:
        """Returns a list of activities: {id, name, modname, url, section}."""
        try:
            raw = await self.ajax("core_courseformat_get_state", {"courseid": course_id})
            state = json.loads(raw)
            sections = {s["id"]: s for s in state.get("section", [])}
            out = []
            for sec in state.get("section", []):
                for cmid in sec.get("cmlist", []):
                    cm = next((c for c in state.get("cm", []) if str(c["id"]) == str(cmid)), None)
                    if not cm or not cm.get("uservisible", True):
                        continue
                    out.append({
                        "id": int(cm["id"]), "name": cm.get("name", ""), "modname": cm.get("module", ""),
                        "url": cm.get("url") or "", "section": sec.get("title") or f"Section {sec.get('number')}",
                    })
            if out:
                return out
        except Exception as e:
            self.log(f"   (getstate 不可用，改用页面解析: {e})")
        return await self._structure_from_html(course_id)

    async def _structure_from_html(self, course_id: int) -> list[dict]:
        html, _ = await self.html(f"{self.base}/course/view.php?id={course_id}")
        soup = BeautifulSoup(html, "html.parser")
        out = []
        for sec in soup.select("li.section, .course-section"):
            h = sec.select_one(".sectionname, h3")
            title = h.get_text(" ", strip=True) if h else ""
            for act in sec.select("li.activity"):
                m = re.search(r"modtype_(\w+)", " ".join(act.get("class", [])))
                a = act.select_one("a[href*='/mod/']")
                cmid = re.sub(r"\D", "", act.get("id", "")) or "0"
                name_el = act.select_one(".instancename") or a
                out.append({"id": int(cmid), "name": name_el.get_text(" ", strip=True) if name_el else "",
                            "modname": m.group(1) if m else "", "url": a["href"] if a else "", "section": title})
        return out

    # ------------------------------------------------------------------ files
    async def head(self, url: str) -> tuple[str, dict]:
        r = await self.ctx.request.fetch(url, method="HEAD", max_redirects=10, timeout=PAGE_TIMEOUT)
        return r.url, dict(r.headers)

    async def fetch(self, url: str) -> tuple[bytes, str, dict]:
        r = await self.ctx.request.get(url, max_redirects=10, timeout=DL_TIMEOUT)
        if not r.ok:
            raise RuntimeError(f"HTTP {r.status}")
        body = await r.body()
        headers = dict(r.headers)
        final = r.url
        await r.dispose()
        return body, final, headers


def filename_from(url: str, headers: dict, hint: str = "") -> str:
    cd = headers.get("content-disposition", "")
    m = re.search(r"filename\*=(?:UTF-8'')?([^;]+)", cd, re.I) or re.search(r'filename="?([^";]+)"?', cd, re.I)
    if m:
        return unquote(m.group(1).strip().strip('"'))
    path = urlparse(url).path
    name = unquote(path.rsplit("/", 1)[-1])
    if name and name not in ("view.php", "pluginfile.php", "index.php"):
        return name
    return hint or "file"


def looks_like_html(body: bytes) -> bool:
    head = body[:600].lstrip().lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html")


def pluginfile_links(html: str, base_url: str, region_selector: str | None = None) -> list[tuple[str, str]]:
    """(url, link text) for every pluginfile.php link in the page (or region)."""
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one(region_selector) if region_selector else soup
    root = root or soup
    seen, out = set(), []
    for el in root.select("a[href*='pluginfile.php'], iframe[src*='pluginfile.php'], object[data*='pluginfile.php'], embed[src*='pluginfile.php']"):
        u = el.get("href") or el.get("src") or el.get("data")
        if not u:
            continue
        from urllib.parse import urljoin
        u = urljoin(base_url, u)
        key = u.split("?")[0]
        # skip theme/user pictures and tiny icons
        if re.search(r"/(user/icon|core_admin|theme|course/overviewfiles|user/draft)/", u):
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append((u, el.get_text(" ", strip=True)))
    return out
