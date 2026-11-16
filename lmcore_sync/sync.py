"""Walk the chosen courses, download everything useful, skip what the user
already has, sort into category folders and write a manifest."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from markdownify import markdownify

from .moodle import Moodle, filename_from, looks_like_html, pluginfile_links
from .organize import (AI_DIR, CATEGORIES, MANIFEST, STATE_DIR, ExistingIndex, assignment_folder,
                       classify, safe_name, sha256_bytes)

AI_DIRNAME = AI_DIR
SKIP_MODS = {"label", "quiz", "lti", "choice", "feedback", "attendance", "zoom", "questionnaire",
             "scheduler", "chat", "glossary", "workshop", "h5pactivity", "subsection", "qbank", "bigbluebuttonbn"}
MAX_MB = 400


def norm(s: str) -> str:
    return re.sub(r"[\s_\-]+", "", s or "").lower()


def html_to_md(fragment: str, base_url: str) -> str:
    soup = BeautifulSoup(fragment, "html.parser")
    for t in soup.select("script, style, template, .sr-only, .accesshide, nav, .modified, .comment-ctrl, .comment-area, [id^=comment-ctrl], .mdl-hidden"):
        t.decompose()
    for a in soup.select("a[href]"):
        a["href"] = urljoin(base_url, a["href"])
    md = markdownify(str(soup), heading_style="ATX")
    return re.sub(r"\n{3,}", "\n\n", md).strip()


class CourseSync:
    def __init__(self, m: Moodle, course: dict, folder: Path, state_dir: Path, opts: dict, log=print):
        self.m, self.course, self.folder, self.log, self.opts = m, course, folder, log, opts
        self.out = folder
        self.layout = {**CATEGORIES, **(opts.get("layout") or {})}
        self.state_file = folder / STATE_DIR / "state.json"
        self.state = json.loads(self.state_file.read_text(encoding="utf-8")) if self.state_file.exists() else {"items": {}}
        self.hash_cache_file = state_dir / f"hashcache_{course['id']}.json"
        cache = json.loads(self.hash_cache_file.read_text(encoding="utf-8")) if self.hash_cache_file.exists() else {}
        self.log(f"   扫描已有文件做去重: {folder}")
        self.existing = ExistingIndex(folder, cache)
        self.rows: list[dict] = []        # manifest rows of this run
        self.links: list[tuple[str, str, str]] = []
        self.skipped_mods: list[tuple[str, str, str]] = []
        self.touched: set[str] = set()
        self.written: set[Path] = set()

    # --------------------------------------------------------------- helpers
    def assign_dir(self, name: str) -> str:
        d = assignment_folder(name)
        # ASCII layouts (e.g. for Quartus, which breaks on spaces/Chinese in paths)
        return d.replace(" ", "") if self.layout.get("assignment_nospace") else d

    def _target_path(self, cat: str, sub: str, filename: str, digest: str) -> Path:
        d = self.out / self.layout[cat]
        if sub:
            d = d / safe_name(sub, 80)
        if cat == "submission":
            d = d / "我的提交与反馈"
        d.mkdir(parents=True, exist_ok=True)
        name = safe_name(filename)
        p = d / name
        n = 2
        while p.exists():
            if sha256_bytes(p.read_bytes()) == digest:
                return p
            stem, ext = os.path.splitext(name)
            p = d / f"{stem} ({n}){ext}"
            n += 1
        return p

    def _rel(self, p: Path) -> str:
        try:
            return p.relative_to(self.folder).as_posix()
        except ValueError:
            return str(p)

    async def save_file(self, url: str, title: str, section: str, modname: str, sub: str = "", cat: str | None = None):
        if "/mod/resource/view.php" in url:
            # resolve to the real pluginfile URL; it contains the file revision,
            # so a teacher's updated upload gets a new key and is fetched again
            try:
                final, hdrs = await self.m.head(url)
                if "pluginfile.php" in final:
                    url = final
            except Exception:
                pass
        key = url.split("?")[0]
        self.touched.add(key)
        prev = self.state["items"].get(key)
        if prev and not self.opts.get("refresh"):
            p = self.folder / prev["path"]
            if p.exists():
                self.rows.append({**prev, "status": prev["status"], "section": section, "title": title})
                return
        try:
            body, final, headers = await self.m.fetch(url)
        except Exception as e:
            self.rows.append({"title": title, "section": section, "status": f"失败: {e}", "path": "", "url": url, "cat": cat or "other"})
            return
        ctype = headers.get("content-type", "").lower()
        fname = filename_from(final, headers, title)
        if ctype.startswith(("video/", "audio/")) and not self.opts.get("videos"):
            self.rows.append({"title": title, "section": section, "status": "跳过(视频/音频)", "path": "", "url": url, "cat": cat or "other"})
            return
        if len(body) > MAX_MB * 1024 * 1024:
            self.rows.append({"title": title, "section": section, "status": "跳过(过大)", "path": "", "url": url, "cat": cat or "other"})
            return
        if looks_like_html(body) and not fname.lower().endswith((".html", ".htm")):
            return "html"
        digest = sha256_bytes(body)
        cat = cat or classify(title, fname, section, modname=modname)
        hit = self.existing.find(digest, fname, len(body))
        if hit is not None and hit not in self.written:
            rec = {"title": title, "section": section, "status": "已有(未重复下载)", "path": self._rel(hit), "url": url, "cat": cat, "sha": digest}
        else:
            target = hit if hit is not None else self._target_path(cat, sub, fname, digest)
            if not target.exists():
                target.write_bytes(body)
                self.written.add(target)
                self.existing.add(target, digest, len(body))
                self.log(f"      + {self._rel(target)}")
            rec = {"title": title, "section": section, "status": "新下载", "path": self._rel(target), "url": url, "cat": cat, "sha": digest}
        self.state["items"][key] = {k: rec[k] for k in ("title", "status", "path", "url", "cat", "sha")}
        self.rows.append(rec)

    def write_note(self, cat: str, name: str, md: str, section: str, title: str, url: str, sub: str = ""):
        d = self.out / self.layout[cat]
        if sub:
            d = d / safe_name(sub, 80)
        d.mkdir(parents=True, exist_ok=True)
        p = d / (safe_name(name, 100) + ".md")
        p.write_text(md, encoding="utf-8")
        self.rows.append({"title": title, "section": section, "status": "网页内容→md", "path": self._rel(p), "url": url, "cat": cat})

    # --------------------------------------------------------------- per module
    async def do_resource(self, cm):
        url = f"{self.m.base}/mod/resource/view.php?id={cm['id']}&redirect=1"
        r = await self.save_file(url, cm["name"], cm["section"], "resource")
        if r == "html":  # embedded / workaround page: look for the real file link
            html, final = await self.m.html(f"{self.m.base}/mod/resource/view.php?id={cm['id']}")
            for u, _ in pluginfile_links(html, final, "#region-main") or []:
                await self.save_file(u, cm["name"], cm["section"], "resource")

    async def do_folder(self, cm):
        html, final = await self.m.html(f"{self.m.base}/mod/folder/view.php?id={cm['id']}")
        links = pluginfile_links(html, final, "#region-main")
        # Keep a folder together; pick its category by majority vote of the
        # files inside (a "Week 8" folder full of Lab*.pptx / *.ipynb is a lab folder).
        from collections import Counter
        votes = Counter(c for c in (classify(t or u.rsplit("/", 1)[-1]) for u, t in links) if c != "other")
        cat = votes.most_common(1)[0][0] if votes else classify(cm["name"], cm["section"], modname="folder")
        name_cat = classify(cm["name"], modname="folder")
        if name_cat in ("assessment", "handbook", "exam"):
            cat = name_cat  # explicit folder names like "Coursework 2" win
        sub = self.assign_dir(cm["name"]) if cat == "assessment" else cm["name"]
        for u, text in links:
            await self.save_file(u, text or cm["name"], cm["section"], "folder", sub=sub, cat=cat)

    async def do_assign(self, cm):
        html, final = await self.m.html(f"{self.m.base}/mod/assign/view.php?id={cm['id']}")
        soup = BeautifulSoup(html, "html.parser")
        main = soup.select_one("#region-main") or soup
        parts = [f"# {cm['name']}", f"- 课程版块：{cm['section']}", f"- 原网页：{final}", ""]
        dates = soup.select_one("[data-region='activity-dates'], .activity-dates")
        if dates:
            parts += ["## 时间", html_to_md(str(dates), final), ""]
        intro = main.select_one(".activity-description, #intro, .box.generalbox")
        if intro:
            parts += ["## 作业说明", html_to_md(str(intro), final), ""]
        status = main.select_one(".submissionstatustable")
        if status:
            parts += ["## 我的提交状态", html_to_md(str(status), final), ""]
        feedback = main.select_one(".feedback")
        if feedback:
            parts += ["## 反馈", html_to_md(str(feedback), final), ""]
        folder = self.assign_dir(cm["name"])
        self.write_note("assessment", "作业说明_Moodle页面", "\n".join(parts), cm["section"], cm["name"], final, sub=folder)
        for u, text in pluginfile_links(html, final, "#region-main"):
            if "assignsubmission" in u or "assignfeedback" in u:
                await self.save_file(u, text, cm["section"], "assign", sub=folder, cat="submission")
            else:
                await self.save_file(u, text or cm["name"], cm["section"], "assign", sub=folder, cat="assessment")

    async def do_page(self, cm, mod="page"):
        url = (f"{self.m.base}/mod/book/tool/print/index.php?id={cm['id']}" if mod == "book"
               else f"{self.m.base}/mod/page/view.php?id={cm['id']}")
        html, final = await self.m.html(url)
        soup = BeautifulSoup(html, "html.parser")
        main = soup.select_one("#region-main, .book_info, body") or soup
        cat = classify(cm["name"], cm["section"], modname=mod)
        md = f"# {cm['name']}\n\n- 课程版块：{cm['section']}\n- 原网页：{final}\n\n" + html_to_md(str(main), final)
        self.write_note(cat, cm["name"], md, cm["section"], cm["name"], final)
        for u, text in pluginfile_links(html, final, "#region-main"):
            await self.save_file(u, text or cm["name"], cm["section"], mod, sub=cm["name"], cat=cat)

    async def do_url(self, cm):
        try:
            html, final = await self.m.html(f"{self.m.base}/mod/url/view.php?id={cm['id']}")
            link = final
            if final.startswith(self.m.base):
                a = BeautifulSoup(html, "html.parser").select_one(".urlworkaround a, #region-main a[href]")
                link = a["href"] if a else final
        except Exception:
            link = cm.get("url", "")
        if "pluginfile.php" in link and link.startswith(self.m.base):
            await self.save_file(link, cm["name"], cm["section"], "url")
        else:
            self.links.append((cm["section"], cm["name"], link))

    async def do_forum(self, cm):
        if not re.search(r"announce|news|公告|notice", cm["name"], re.I) and not self.opts.get("all_forums"):
            self.skipped_mods.append((cm["section"], cm["name"], "forum"))
            return
        html, final = await self.m.html(f"{self.m.base}/mod/forum/view.php?id={cm['id']}")
        soup = BeautifulSoup(html, "html.parser")
        discs = []
        for a in soup.select("a[href*='discuss.php?d=']"):
            mm = re.search(r"discuss\.php\?d=(\d+)", a["href"])
            if not mm:
                continue
            u = f"{self.m.base}/mod/forum/discuss.php?d={mm.group(1)}"
            t = a.get_text(" ", strip=True)
            if re.fullmatch(r"[\d\s:/\-A-Za-z,]{0,25}", t or "") and re.search(r"\d", t or ""):
                continue  # date-only 'last post' links
            if t and u not in [d[0] for d in discs]:
                discs.append((u, t))
        posts = [f"# {cm['name']}（{self.course['shortname']}）", ""]
        for u, t in discs[: self.opts.get("max_posts", 60)]:
            h, f = await self.m.html(u)
            s = BeautifulSoup(h, "html.parser")
            posts += [f"## {t}", f"<{f}>", ""]
            arts = s.select("article[data-region='post'], article.forum-post-container")
            for art in arts:
                head = art.select_one("header")
                meta = head.get_text(" ", strip=True) if head else ""
                meta = re.sub(r"\s*Number of replies:.*$", "", meta)
                body = art.select_one(".post-content-container") or art
                posts += [f"**{meta}**", "", html_to_md(str(body), f), ""]
            if not arts:
                body = s.select_one("#region-main") or s
                posts += [html_to_md(str(body), f), ""]
            posts += ["---", ""]
            for fu, ft in pluginfile_links(h, f, "#region-main"):
                if "/mod_forum/attachment/" in fu or "/mod_forum/post/" in fu:
                    await self.save_file(fu, ft or t, cm["section"], "forum", cat=classify(t, ft, modname="") if classify(t, ft) != "other" else "announcement")
        self.write_note("announcement", "公告" if re.search(r"announce|news", cm["name"], re.I) else cm["name"],"\n".join(posts), cm["section"], cm["name"], final)

    # --------------------------------------------------------------- run
    async def run(self):
        cms = await self.m.course_structure(self.course["id"])
        self.log(f"   课程里共有 {len(cms)} 个活动")
        for i, cm in enumerate(cms, 1):
            mod = cm["modname"]
            self.log(f"   [{i}/{len(cms)}] ({mod}) {cm['name'][:70]}")
            try:
                if mod == "resource":
                    await self.do_resource(cm)
                elif mod == "folder":
                    await self.do_folder(cm)
                elif mod == "assign":
                    await self.do_assign(cm)
                elif mod in ("page", "book"):
                    await self.do_page(cm, mod)
                elif mod == "url":
                    await self.do_url(cm)
                elif mod == "forum":
                    await self.do_forum(cm)
                else:
                    self.skipped_mods.append((cm["section"], cm["name"], mod))
            except Exception as e:
                self.log(f"      !! {type(e).__name__}: {e}")
                self.rows.append({"title": cm["name"], "section": cm["section"], "status": f"失败: {e}", "path": "", "url": cm.get("url", ""), "cat": "other"})
        self.save_state()

    def save_state(self):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        if self.touched:  # forget entries that no longer exist on Moodle / old revisions
            self.state["items"] = {k: v for k, v in self.state["items"].items() if k in self.touched}
        self.state["last_sync"] = datetime.now().isoformat(timespec="seconds")
        self.state["course"] = self.course
        self.state_file.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), encoding="utf-8")
        self.hash_cache_file.parent.mkdir(parents=True, exist_ok=True)
        self.hash_cache_file.write_text(json.dumps(self.existing.cache), encoding="utf-8")
