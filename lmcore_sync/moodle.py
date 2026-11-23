"""Browser-session access to a Moodle site (works with SSO and with the mobile
web-service API switched off). Idea and download hardening adapted from
thntx/moodle-browser-scraper and elonuziel/moodle_scraper (both MIT)."""
from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse, parse_qs, urlencode

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, BrowserContext, Page

import os
import sys
import shutil
import socket
import subprocess


def _kill_profile_browsers(profile_dir: Path) -> int:
    """End browser processes that use *our* private profile (never the user's
    normal Edge/Chrome, which runs with a different --user-data-dir)."""
    if sys.platform != "win32":
        return 0
    marker = str(Path(profile_dir).resolve()).lower()
    # UTF-8 output, otherwise non-ASCII profile paths come back in the console code page
    script = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
              "Get-CimInstance Win32_Process -Filter \"name='msedge.exe' or name='chrome.exe' or name='chromium.exe'\" | "
              "ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True,
                             timeout=30, encoding="utf-8", errors="replace").stdout
    except Exception:
        return 0
    killed = 0
    for line in out.splitlines():
        pid, _, cmd = line.partition("\t")
        if marker in cmd.lower().replace('"', "") and pid.strip().isdigit():
            subprocess.run(["taskkill", "/PID", pid.strip(), "/T", "/F"], capture_output=True)
            killed += 1
    return killed


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
DL_TIMEOUT = 180_000  # longer downloads switch to a direct streamed download (see fetch)


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


class LoginExpired(RuntimeError):
    """The saved school session must be renewed interactively."""


class _HTTPError(RuntimeError):
    def __init__(self, status: int, retry_after: str = ""):
        super().__init__(f"HTTP {status}")
        self.status = status
        self.retry_after = retry_after


class Moodle:
    def __init__(self, base: str, profile_dir: Path, browser: str = "msedge", headless: bool = True, log=print,
                 delay: float = 0.3, retries: int = 3):
        self.base = base.rstrip("/")
        self.delay = max(0.0, float(delay))
        self.retries = max(0, int(retries))
        self._structure_cache: dict[int, list[dict]] = {}
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
        # Proxy tools (Clash, v2rayN ...) often set HTTP(S)_PROXY. Playwright would
        # then send the local DevTools handshake through the proxy (-> HTTP 502).
        bypass = {"127.0.0.1", "localhost", "::1"}
        for var in ("NO_PROXY", "no_proxy"):
            have = {x.strip() for x in os.environ.get(var, "").split(",") if x.strip()}
            os.environ[var] = ",".join(sorted(have | bypass))
        self._pw = await async_playwright().start()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        exe = find_browser(self.browser) or self._pw.chromium.executable_path
        port = _free_port()
        # A browser left over from an interrupted run keeps the profile locked;
        # a new launch would just hand off to it and never open our port.
        _kill_profile_browsers(self.profile_dir)
        args = [exe, f"--remote-debugging-port={port}", f"--user-data-dir={self.profile_dir}",
                "--no-first-run", "--no-default-browser-check", "--disable-sync",
                "--window-size=1200,900",
                # Edge otherwise relaunches itself, so the PID we hold would not be the browser
                "--edge-skip-compat-layer-relaunch"]
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
            self._stop_browser()
            await self._pw.stop()
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
            self._stop_browser()
        finally:
            await self._pw.stop()

    def _stop_browser(self):
        try:
            self._proc.terminate()
            self._proc.wait(timeout=10)
        except Exception:
            pass
        _kill_profile_browsers(self.profile_dir)

    async def _read_sesskey(self) -> str:
        try:
            return await self.page.evaluate("() => (window.M && M.cfg && M.cfg.sesskey) || ''")
        except Exception:
            return ""

    async def is_logged_in(self, settle_s: int = 25) -> bool:
        """True if Moodle accepts the saved session. An expired Moodle session
        is often renewed silently by the school SSO (redirect to the identity
        provider and back via an auto-submitted SAML form), so follow the
        redirects for a while instead of judging the first page."""
        await self.page.goto(f"{self.base}/my/courses.php", wait_until="domcontentloaded")
        t0 = time.time()
        while True:
            url = self.page.url
            if url.startswith(self.base) and "/login" not in url and "saml" not in url.lower():
                self.sesskey = await self._read_sesskey()
                if self.sesskey:
                    await self.save_cookies()  # keep refreshed cookies
                    return True
            try:  # a visible password box = the SSO really wants a password
                if await self.page.locator("input[type=password]").count():
                    return False
            except Exception:
                pass
            if time.time() - t0 > settle_s:
                return False
            await asyncio.sleep(1)

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

    def _check_auth(self, url: str, html: str = ""):
        parsed = urlparse(url)
        path = parsed.path.lower()
        if re.search(r"/(login(?:\.php|/|$)|saml(?:2)?(?:/|$)|adfs/ls|oauth2?/authorize|cas/login)", path) or re.search(
                r"<input[^>]+type\s*=\s*['\"]password['\"]", html, re.I):
            raise LoginExpired("登录状态失效，请运行 python -m lmcore_sync login。")

    def _check_response(self, response):
        self._check_auth(response.url)
        if response.status == 401:
            raise LoginExpired("登录状态失效，请重新登录。")
        if response.status >= 400:
            raise _HTTPError(response.status, response.headers.get("retry-after", ""))

    async def ajax(self, method: str, args: dict):
        async def go():
            endpoint = self.base + "/lib/ajax/service.php?" + urlencode({"sesskey": self.sesskey, "info": method})
            # APIRequestContext shares the browser's cookies and is independent
            # of whichever external page the browser last visited.
            response = await self.ctx.request.post(endpoint,
                data=json.dumps([{"index": 0, "methodname": method, "args": args}]),
                headers={"Content-Type": "application/json"}, timeout=PAGE_TIMEOUT)
            try:
                self._check_response(response)
                body = await response.text()
                self._check_auth(response.url, body)
                res = json.loads(body)
                r0 = res if isinstance(res, dict) else res[0]
                if r0.get("error"):
                    exc = r0.get("exception") or r0
                    if re.search(r"invalidsesskey|requirelogin|requireslogin|notloggedin|sessionexpired", str(exc), re.I):
                        raise LoginExpired("登录状态失效，请重新登录。")
                    raise RuntimeError(f"{method}: {exc}")
                return r0["data"]
            finally:
                await response.dispose()
        return await self._retry(method, go)

    # ------------------------------------------------------------------ courses
    async def courses(self, classification: str = "all") -> list[dict]:
        """classification: all | inprogress (this semester) | past | future"""
        data = await self.ajax(
            "core_course_get_enrolled_courses_by_timeline_classification",
            {"offset": 0, "limit": 0, "classification": classification, "sort": "fullname"},
        )
        return [
            {"id": c["id"], "shortname": c.get("shortname", ""), "fullname": c.get("fullname", ""),
             "url": c.get("viewurl", f"{self.base}/course/view.php?id={c['id']}"),
             "startdate": c.get("startdate", 0), "enddate": c.get("enddate", 0)}
            for c in data["courses"]
        ]

    async def _retry(self, what: str, fn):
        for attempt in range(self.retries + 1):
            try:
                if self.delay:
                    await asyncio.sleep(self.delay)
                return await fn()
            except LoginExpired:
                raise
            except Exception as e:
                retryable = (isinstance(e, _HTTPError) and (e.status in (408, 429) or 500 <= e.status <= 599)) or bool(
                    re.search(r"ENOTFOUND|ECONNRESET|ETIMEDOUT|Timeout|ERR_NETWORK|ERR_CONNECTION|ERR_NAME|socket|ECONNREFUSED", str(e), re.I))
                if attempt == self.retries or not retryable:
                    raise
                wait = 2 ** attempt * 2
                retry_after = getattr(e, "retry_after", "")
                if retry_after:
                    try:
                        wait = float(retry_after)
                    except ValueError:
                        try:
                            when = parsedate_to_datetime(retry_after)
                            wait = (when - datetime.now(timezone.utc)).total_seconds()
                        except (TypeError, ValueError):
                            pass
                wait = max(0, min(wait, 60))
                self.log(f"      (网络问题，{wait:g}s 后重试 {attempt + 1}/{self.retries}: {str(e)[:80]})")
                await asyncio.sleep(wait)

    async def html(self, url: str) -> tuple[str, str]:
        async def go():
            response = await self.page.goto(url, wait_until="domcontentloaded")
            if response is not None:
                self._check_response(response)
            content = await self.page.content()
            self._check_auth(self.page.url, content)
            return content, self.page.url
        return await self._retry(url, go)

    async def course_structure(self, course_id: int) -> list[dict]:
        """Returns a list of activities: {id, name, modname, url, section}."""
        if course_id not in self._structure_cache:
            self._structure_cache[course_id] = await self._course_structure(course_id)
        return self._structure_cache[course_id]

    async def _course_structure(self, course_id: int) -> list[dict]:
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
        except LoginExpired:
            raise
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
        async def go():
            r = await self.ctx.request.fetch(url, method="HEAD", max_redirects=10, timeout=PAGE_TIMEOUT)
            try:
                self._check_response(r)
                return r.url, dict(r.headers)
            finally:
                await r.dispose()
        return await self._retry(url, go)

    async def fetch(self, url: str) -> tuple[bytes, str, dict]:
        async def go():
            r = await self.ctx.request.get(url, max_redirects=10, timeout=DL_TIMEOUT)
            try:
                self._check_response(r)
                body = await r.body()
                if looks_like_html(body):
                    self._check_auth(r.url, body.decode("utf-8", "replace"))
                return body, r.url, dict(r.headers)
            finally:
                await r.dispose()
        async def go_or_direct():
            try:
                return await go()
            except LoginExpired:
                raise
            except Exception as e:
                # Big files (e.g. a 440 MB lab pack) stall inside the browser's
                # network stack, especially behind a local proxy. Retrying the
                # browser just burns the timeout again, so switch to a plain
                # streamed download with the same session cookies at once.
                if not re.search(r"Timeout|ECONNRESET|aborted|string longer than", str(e)):  # Node cannot hold >~400 MB bodies
                    raise
                self.log("      (浏览器下载太慢或被中断，改用直接下载……)")
                return await self._fetch_direct(url)
        return await self._retry(url, go_or_direct)

    async def _fetch_direct(self, url: str) -> tuple[bytes, str, dict]:
        import urllib.request
        host = urlparse(url).hostname or ""
        jar = [c for c in await self.ctx.cookies() if host.endswith(c["domain"].lstrip("."))]
        header = "; ".join(f"{c['name']}={c['value']}" for c in jar)

        def run(proxied: bool):
            handlers = [] if proxied else [urllib.request.ProxyHandler({})]
            opener = urllib.request.build_opener(*handlers)
            req = urllib.request.Request(url, headers={"Cookie": header, "User-Agent": "lmcore-sync"})
            with opener.open(req, timeout=120) as resp:   # timeout per read, not for the whole file
                chunks = []
                while True:
                    block = resp.read(1 << 20)
                    if not block:
                        break
                    chunks.append(block)
                return b"".join(chunks), resp.geturl(), {k.lower(): v for k, v in resp.headers.items()}

        last = None
        for proxied in (False, True):     # direct first; then through the system proxy
            try:
                body, final, headers = await asyncio.to_thread(run, proxied)
                self._check_auth(final, body[:4000].decode("utf-8", "replace") if looks_like_html(body) else "")
                return body, final, headers
            except LoginExpired:
                raise
            except Exception as e:
                last = e
        raise RuntimeError(f"直接下载也失败：{last}")


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
